from types import SimpleNamespace
from unittest import mock

from streamkeep import hooks


class _ImmediateThread:
    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


def test_remote_metadata_is_environment_only_and_never_interpolated():
    command = "echo trusted-command"
    remote_title = '$(malicious) & calc.exe " {title}'
    completed = SimpleNamespace(returncode=0, stderr=b"")

    with mock.patch.object(hooks.threading, "Thread", _ImmediateThread), \
         mock.patch.object(hooks.subprocess, "run", return_value=completed) as run:
        hooks.fire_hook(
            "download_complete",
            {
                "title": remote_title,
                "unknown=environment": "must-not-be-added",
            },
            {"download_complete": command},
        )

    assert run.call_args.args[0] == command
    env = run.call_args.kwargs["env"]
    assert env["SK_TITLE"] == remote_title
    assert "SK_UNKNOWN=ENVIRONMENT" not in env


def test_hook_context_is_bounded_and_nul_removed():
    completed = SimpleNamespace(returncode=0, stderr=b"")
    with mock.patch.object(hooks.threading, "Thread", _ImmediateThread), \
         mock.patch.object(hooks.subprocess, "run", return_value=completed) as run:
        hooks.fire_hook(
            "download_complete",
            {"title": "a\x00" + "b" * (hooks.MAX_HOOK_CONTEXT_CHARS + 100)},
            {"download_complete": "echo ok"},
        )

    title = run.call_args.kwargs["env"]["SK_TITLE"]
    assert "\x00" not in title
    assert len(title) == hooks.MAX_HOOK_CONTEXT_CHARS


def test_unknown_event_or_non_string_command_is_not_executed():
    with mock.patch.object(hooks.threading, "Thread") as thread:
        hooks.fire_hook("unknown", {}, {"unknown": "echo no"})
        hooks.fire_hook("download_complete", {}, {"download_complete": ["echo", "no"]})

    thread.assert_not_called()
