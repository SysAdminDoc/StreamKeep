"""Event hook system — run user-configured shell commands on lifecycle events.

Commands execute as fire-and-forget subprocesses with context passed as
environment variables (SK_EVENT, SK_TITLE, SK_CHANNEL, SK_PLATFORM,
SK_PATH, SK_URL, SK_QUALITY).  Hooks are configured via
``config["hooks"]``  — a dict of ``event_name -> command_string``.
"""

import os
import subprocess
import threading

from .paths import _CREATE_NO_WINDOW

HOOK_TIMEOUT = 30
MAX_HOOK_CONTEXT_CHARS = 4096

HOOK_EVENTS = [
    "download_complete",
    "download_error",
    "channel_live",
    "auto_record_start",
    "auto_record_end",
    "transcode_complete",
]

HOOK_CONTEXT_KEYS = frozenset({
    "title", "channel", "platform", "path", "url", "quality", "error",
})


def fire_hook(event, context, hooks_config, log_fn=None):
    """Fire the configured hook for *event*, if any.

    *context* is a dict whose keys become ``SK_<KEY>`` env vars.
    Runs in a daemon thread so the UI never blocks.
    """
    if event not in HOOK_EVENTS or not isinstance(hooks_config, dict):
        return
    raw_command = hooks_config.get(event, "")
    if not isinstance(raw_command, str):
        return
    cmd = raw_command.strip()
    if not cmd or len(cmd) > 2048 or "\x00" in cmd:
        return

    env = dict(os.environ)
    env["SK_EVENT"] = str(event)
    for key, value in (context or {}).items():
        normalized_key = str(key or "").strip().lower()
        if normalized_key not in HOOK_CONTEXT_KEYS:
            continue
        # Remote metadata is data-only: pass it through bounded environment
        # variables and never format, concatenate, or parse it into ``cmd``.
        normalized_value = str(value or "").replace("\x00", "")
        env[f"SK_{normalized_key.upper()}"] = normalized_value[
            :MAX_HOOK_CONTEXT_CHARS
        ]

    def _run():
        try:
            proc = subprocess.run(
                cmd, shell=True, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=HOOK_TIMEOUT,
                creationflags=_CREATE_NO_WINDOW,
            )
            if proc.returncode != 0 and log_fn:
                stderr = (proc.stderr or b"").decode(
                    "utf-8", errors="replace").strip()
                from .diagnostics import redact_text
                log_fn(
                    f"[HOOK] {event} exited {proc.returncode}: "
                    f"{redact_text(stderr)[:200]}")
        except subprocess.TimeoutExpired:
            if log_fn:
                log_fn(f"[HOOK] {event} timed out after {HOOK_TIMEOUT}s")
        except Exception as e:
            if log_fn:
                log_fn(f"[HOOK] {event} error: {e}")

    threading.Thread(target=_run, daemon=True).start()
