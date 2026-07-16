import os
import sys
from unittest import mock

from streamkeep import hooks


class _ImmediateThread:
    def __init__(self, *, target, args=(), daemon=False):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        self.target(*self.args)


def _structured(executable, args, enabled=True):
    return {"executable": executable, "args": list(args), "enabled": enabled}


def test_structured_hook_runs_without_shell_and_builds_argv():
    recorded = {}

    def fake_run(event, argv, env, log_fn):
        recorded["event"] = event
        recorded["argv"] = argv
        recorded["env"] = env

    with mock.patch.object(hooks.threading, "Thread", _ImmediateThread), \
         mock.patch.object(hooks, "_run_structured_hook", fake_run):
        hooks.fire_hook(
            "download_complete",
            {"title": "My Video", "unknown=environment": "must-not-be-added"},
            {"download_complete": _structured(
                "/usr/bin/notify", ["--title", "SK", "--path"]
            )},
        )

    assert recorded["argv"] == ["/usr/bin/notify", "--title", "SK", "--path"]
    assert recorded["env"]["SK_TITLE"] == "My Video"
    assert recorded["env"]["SK_EVENT"] == "download_complete"
    assert "SK_UNKNOWN=ENVIRONMENT" not in recorded["env"]


def test_remote_metadata_is_environment_only_and_never_in_argv():
    recorded = {}

    def fake_run(event, argv, env, log_fn):
        recorded["argv"] = argv
        recorded["env"] = env

    remote_title = '$(malicious) & calc.exe " {title}'
    with mock.patch.object(hooks.threading, "Thread", _ImmediateThread), \
         mock.patch.object(hooks, "_run_structured_hook", fake_run):
        hooks.fire_hook(
            "download_complete",
            {"title": remote_title},
            {"download_complete": _structured("/bin/echo", ["fixed"])},
        )

    assert recorded["argv"] == ["/bin/echo", "fixed"]
    assert remote_title not in recorded["argv"]
    assert recorded["env"]["SK_TITLE"] == remote_title


def test_hook_environment_is_minimal_allowlist_plus_context():
    recorded = {}

    def fake_run(event, argv, env, log_fn):
        recorded["env"] = env

    with mock.patch.dict(
        os.environ,
        {"SECRET_TOKEN": "leak-me", "PATH": os.environ.get("PATH", "x")},
        clear=False,
    ), \
         mock.patch.object(hooks.threading, "Thread", _ImmediateThread), \
         mock.patch.object(hooks, "_run_structured_hook", fake_run):
        hooks.fire_hook(
            "download_complete",
            {"title": "t"},
            {"download_complete": _structured("/bin/true", [])},
        )

    env = recorded["env"]
    assert "SECRET_TOKEN" not in env
    assert "PATH" in env


def test_hook_context_is_bounded_and_nul_removed():
    recorded = {}

    def fake_run(event, argv, env, log_fn):
        recorded["env"] = env

    with mock.patch.object(hooks.threading, "Thread", _ImmediateThread), \
         mock.patch.object(hooks, "_run_structured_hook", fake_run):
        hooks.fire_hook(
            "download_complete",
            {"title": "a\x00" + "b" * (hooks.MAX_HOOK_CONTEXT_CHARS + 100)},
            {"download_complete": _structured("/bin/true", [])},
        )

    title = recorded["env"]["SK_TITLE"]
    assert "\x00" not in title
    assert len(title) == hooks.MAX_HOOK_CONTEXT_CHARS


def test_legacy_shell_string_hook_is_disabled_and_not_executed():
    with mock.patch.object(hooks.threading, "Thread") as thread:
        logs = []
        hooks.fire_hook(
            "download_complete",
            {},
            {"download_complete": "echo trusted-command"},
            log_fn=logs.append,
        )
    thread.assert_not_called()
    assert any("legacy" in line.lower() for line in logs)


def test_disabled_structured_hook_is_not_executed():
    with mock.patch.object(hooks.threading, "Thread") as thread:
        hooks.fire_hook(
            "download_complete",
            {},
            {"download_complete": _structured("/bin/true", [], enabled=False)},
        )
    thread.assert_not_called()


def test_invalid_and_unknown_hooks_are_not_executed():
    with mock.patch.object(hooks.threading, "Thread") as thread:
        hooks.fire_hook("unknown", {}, {"unknown": _structured("/bin/x", [])})
        hooks.fire_hook(
            "download_complete", {},
            {"download_complete": {"executable": "", "args": ["orphan"]}},
        )
        hooks.fire_hook(
            "download_complete", {},
            {"download_complete": {"executable": "/bin/x", "args": [1, 2]}},
        )
    thread.assert_not_called()


def test_normalize_hook_classification():
    assert hooks.normalize_hook(None)[0] == "empty"
    assert hooks.normalize_hook("")[0] == "empty"
    assert hooks.normalize_hook("do stuff")[0] == "legacy"
    kind, data = hooks.normalize_hook(_structured("/bin/x", ["-a"]))
    assert kind == "structured"
    assert data == {"executable": "/bin/x", "args": ["-a"], "enabled": True}
    assert hooks.normalize_hook({"executable": "/x", "args": "nope"})[0] == "invalid"


def test_parse_hook_args_text_ignores_blank_lines():
    assert hooks.parse_hook_args_text("--a\n\n  --b  \n\n") == ["--a", "--b"]


def test_structured_hook_execution_captures_bounded_stderr(tmp_path):
    # End-to-end: a real child process runs with no shell, its stderr is
    # captured and bounded, and it exits cleanly.
    script = tmp_path / "hook.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('E' * 100000)\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    logs = []
    hooks._run_structured_hook(
        "download_complete",
        [sys.executable, str(script)],
        hooks._hook_env("download_complete", {}),
        logs.append,
    )
    assert any("exited 3" in line for line in logs)


def test_structured_hook_timeout_is_terminated(tmp_path, monkeypatch):
    script = tmp_path / "sleep.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(hooks, "HOOK_TIMEOUT", 1)
    logs = []
    hooks._run_structured_hook(
        "download_complete",
        [sys.executable, str(script)],
        hooks._hook_env("download_complete", {}),
        logs.append,
    )
    assert any("timed out" in line for line in logs)
