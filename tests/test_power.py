from types import SimpleNamespace

import pytest

from streamkeep.power import (
    DEFAULT_SHUTDOWN_DELAY_SECS,
    build_power_command,
    is_destructive,
    normalize_power_action,
    run_queue_complete_action,
)


@pytest.mark.parametrize("value,expected", [
    ("shutdown", "shutdown"),
    ("  Sleep ", "sleep"),
    ("NOTIFY", "notify"),
    ("", "none"),
    (None, "none"),
    ("explode", "none"),
])
def test_normalize_power_action(value, expected):
    assert normalize_power_action(value) == expected


@pytest.mark.parametrize("action,destructive", [
    ("none", False),
    ("notify", False),
    ("run-hook", False),
    ("lock", False),
    ("sleep", True),
    ("hibernate", True),
    ("shutdown", True),
])
def test_is_destructive(action, destructive):
    assert is_destructive(action) is destructive


def test_soft_actions_have_no_os_command():
    for action in ("none", "notify", "run-hook"):
        assert build_power_command(action, windows=True) == []
        assert build_power_command(action, windows=False) == []


def test_windows_power_commands():
    assert build_power_command("lock", windows=True) == [
        "rundll32.exe", "user32.dll,LockWorkStation"
    ]
    # Windows sleep has no argv on purpose: rundll32's SetSuspendState entry
    # ignores its command line, so "0,1,0" never reached the hibernate flag
    # and a hibernation-enabled machine hibernated on a sleep request. It is
    # dispatched in-process through the documented Win32 call instead.
    assert build_power_command("sleep", windows=True) == []
    assert build_power_command("hibernate", windows=True) == ["shutdown", "/h"]
    assert build_power_command("shutdown", windows=True) == [
        "shutdown", "/s", "/t", str(DEFAULT_SHUTDOWN_DELAY_SECS)
    ]


def test_shutdown_delay_is_configurable_and_cancellable_window():
    assert build_power_command("shutdown", windows=True, delay_secs=120) == [
        "shutdown", "/s", "/t", "120"
    ]
    # A bad delay falls back to the default grace period, never zero-surprise.
    assert build_power_command("shutdown", windows=True, delay_secs="oops") == [
        "shutdown", "/s", "/t", str(DEFAULT_SHUTDOWN_DELAY_SECS)
    ]


def test_posix_power_commands():
    assert build_power_command("lock", windows=False) == [
        "loginctl", "lock-session"
    ]
    assert build_power_command("sleep", windows=False) == ["systemctl", "suspend"]
    assert build_power_command("hibernate", windows=False) == [
        "systemctl", "hibernate"
    ]
    assert build_power_command("shutdown", windows=False, delay_secs=120) == [
        "shutdown", "-h", "+2"
    ]


def test_run_none_is_noop():
    result = run_queue_complete_action("none", execute=True)
    assert result == {
        "action": "none", "command": [], "executed": False, "error": ""
    }


def test_run_notify_invokes_callback():
    calls = []
    result = run_queue_complete_action(
        "notify", notify_fn=lambda: calls.append("n"), execute=True,
    )
    assert calls == ["n"]
    assert result["executed"] is True


def test_run_notify_survives_callback_error():
    def _boom():
        raise RuntimeError("toast backend missing")

    result = run_queue_complete_action("notify", notify_fn=_boom, execute=True)
    assert result["executed"] is False
    assert "toast backend" in result["error"]


def test_run_hook_invokes_callback():
    calls = []
    result = run_queue_complete_action(
        "run-hook", hook_fn=lambda: calls.append("h"), execute=True,
    )
    assert calls == ["h"]
    assert result["executed"] is True


def test_run_destructive_builds_command_without_executing_when_dry():
    # execute=False must NEVER power off the test machine, only plan.
    logged = []
    result = run_queue_complete_action(
        "shutdown", execute=False, windows=True, log_fn=logged.append,
    )
    assert result["command"] == [
        "shutdown", "/s", "/t", str(DEFAULT_SHUTDOWN_DELAY_SECS)
    ]
    assert result["executed"] is False
    # The scheduled command is logged so an unattended user sees the plan.
    assert any("shutdown" in line for line in logged)


def test_run_missing_callback_is_safe():
    assert run_queue_complete_action("notify", execute=True)["executed"] is False
    assert run_queue_complete_action("run-hook", execute=True)["executed"] is False


# ── V159: sleep must sleep, not hibernate ────────────────────────────

def test_windows_sleep_is_dispatched_in_process_not_as_argv():
    from streamkeep.power import uses_in_process_suspend

    assert uses_in_process_suspend("sleep", windows=True) is True
    # Every other action, and every other platform, still builds argv.
    for action in ("lock", "hibernate", "shutdown", "notify", "none"):
        assert uses_in_process_suspend(action, windows=True) is False
    assert uses_in_process_suspend("sleep", windows=False) is False


def test_a_sleep_plan_never_spawns_a_process(monkeypatch):
    """Guards the machine running the suite as much as the contract: a plan
    must not reach Popen, and must not reach SetSuspendState either."""
    import streamkeep.power as power

    def explode(*args, **kwargs):
        raise AssertionError("a planned sleep must not execute anything")

    monkeypatch.setattr(power.subprocess, "Popen", explode)
    monkeypatch.setattr(power, "suspend_windows", explode)

    result = power.run_queue_complete_action(
        "sleep", execute=False, windows=True,
    )
    assert result["executed"] is False
    assert result["command"] == []
    assert result["error"] == ""


def test_executing_sleep_calls_setsuspendstate_with_hibernate_false():
    """The whole point of the fix: the hibernate flag is actually passed.
    ``suspend_windows`` is stubbed — the real call would suspend the machine
    running this suite."""
    import streamkeep.power as power

    calls = []
    original = power.suspend_windows
    power.suspend_windows = lambda **kwargs: calls.append(kwargs) or True
    try:
        result = power.run_queue_complete_action(
            "sleep", execute=True, windows=True,
        )
    finally:
        power.suspend_windows = original

    assert calls == [{}], calls
    assert result["executed"] is True
    assert result["error"] == ""


def test_a_refused_suspend_is_reported_rather_than_swallowed():
    import streamkeep.power as power

    def refuse(**kwargs):
        raise OSError("SetSuspendState was refused")

    original = power.suspend_windows
    power.suspend_windows = refuse
    try:
        result = power.run_queue_complete_action(
            "sleep", execute=True, windows=True,
        )
    finally:
        power.suspend_windows = original

    assert result["executed"] is False
    assert "refused" in result["error"]


def test_suspend_windows_defaults_to_sleep_and_can_be_asked_to_hibernate():
    """Signature check only — calling through would suspend this machine.
    ``bHibernate`` is the argument rundll32's entry point ignored, which is
    the entire defect."""
    import inspect

    from streamkeep.power import suspend_windows

    signature = inspect.signature(suspend_windows)
    assert signature.parameters["hibernate"].default is False
    assert signature.parameters["hibernate"].kind is inspect.Parameter.KEYWORD_ONLY


def test_hibernate_remains_a_plain_command():
    from streamkeep.power import build_power_command

    assert build_power_command("hibernate", windows=True) == ["shutdown", "/h"]


class _FakePowerBackend:
    available = True

    def __init__(self):
        self.calls = []

    def create(self, reason):
        self.calls.append(("create", reason))
        return object()

    def set(self, _handle, request_type):
        self.calls.append(("set", request_type))

    def clear(self, _handle, request_type):
        self.calls.append(("clear", request_type))

    def close(self, _handle):
        self.calls.append(("close",))

    def block_shutdown(self, hwnd, reason):
        self.calls.append(("block", hwnd, reason))

    def unblock_shutdown(self, hwnd):
        self.calls.append(("unblock", hwnd))


def test_power_request_lease_publishes_and_releases_every_request_type():
    from streamkeep.power import (
        POWER_REQUEST_EXECUTION_REQUIRED,
        POWER_REQUEST_SYSTEM_REQUIRED,
        PowerRequestLease,
    )

    backend = _FakePowerBackend()
    lease = PowerRequestLease(backend=backend)
    assert lease.set_reasons(["Capture: Studio", "Capture: Studio"], hwnd=42)
    assert lease.active is True
    assert lease.reason == "StreamKeep active work: Capture: Studio"
    # Idempotent refreshes do not churn the OS request.
    assert lease.set_reasons(["Capture: Studio"], hwnd=42) is True
    assert [call[0] for call in backend.calls] == ["create", "set", "set", "block"]
    lease.release()
    assert lease.active is False
    assert backend.calls[-4:] == [
        ("unblock", 42),
        ("clear", POWER_REQUEST_EXECUTION_REQUIRED),
        ("clear", POWER_REQUEST_SYSTEM_REQUIRED),
        ("close",),
    ]


def test_power_request_reason_is_bounded_and_control_free():
    from streamkeep.power import power_request_reason

    reason = power_request_reason([" A\nB ", "a b", "x" * 500])
    assert "A B" in reason
    assert "a b" not in reason
    assert len(reason) < 512


def test_main_window_power_reasons_cover_foreground_queue_and_capture():
    from streamkeep.ui.main_window import StreamKeep

    class Running:
        def isRunning(self):
            return True

    queue_item = {"job_id": "job-1", "title": "Queued capture", "status": "queued"}
    window = SimpleNamespace(
        download_worker=Running(),
        _active_stream_info=SimpleNamespace(title="Foreground download"),
        _active_quality_name="",
        _download_queue=[queue_item],
        _queue_workers={id(queue_item): Running()},
        _queue_fetch_workers={},
        _queue_active_item=None,
        _autorecord_workers={"studio": Running()},
        _autorecord_contexts={"studio": {"title": "Studio live"}},
    )
    reasons = StreamKeep._active_power_reasons(window)
    assert reasons == [
        "Download: Foreground download",
        "Queue: Queued capture",
        "Capture: Studio live",
    ]
