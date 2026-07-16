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
    assert build_power_command("sleep", windows=True) == [
        "rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"
    ]
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
