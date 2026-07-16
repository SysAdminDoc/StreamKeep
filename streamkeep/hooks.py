"""Event hook system — run user-configured programs on lifecycle events.

Hooks execute as fire-and-forget subprocesses with **no shell**. Each hook is a
structured action: an executable plus an explicit argument array. Lifecycle
context is passed only as bounded ``SK_*`` environment variables
(SK_EVENT, SK_TITLE, SK_CHANNEL, SK_PLATFORM, SK_PATH, SK_URL, SK_QUALITY,
SK_ERROR) — remote metadata is data-only and is never concatenated, formatted,
or parsed into the command line.

Hooks are configured via ``config["hooks"]`` — a dict of
``event_name -> hook``. A hook is either:

* a structured object ``{"executable": str, "args": [str, ...],
  "enabled": bool}`` (executed), or
* a legacy shell-command string (**disabled** — never executed; it must be
  re-created as a structured action before it will run again).

Subprocesses run with a minimal allowlisted environment, discarded stdout,
bounded stderr capture, a wall-clock timeout, and process-tree termination on
timeout so a runaway hook cannot leave orphaned descendants.
"""

import os
import subprocess
import threading

from .paths import _CREATE_NO_WINDOW

HOOK_TIMEOUT = 30
MAX_HOOK_CONTEXT_CHARS = 4096
MAX_HOOK_ARGS = 64
MAX_HOOK_ARG_CHARS = 4096
MAX_HOOK_EXECUTABLE_CHARS = 4096
MAX_HOOK_OUTPUT_BYTES = 8192
MAX_LEGACY_HOOK_CHARS = 2048

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

# Only these environment variables are forwarded to a hook process. Everything
# else (tokens, session vars, unrelated app state) is withheld.
_ENV_ALLOWLIST = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "HOME", "LANG", "LC_ALL", "USERPROFILE",
    "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
})


def structured_hook(executable, args=None, enabled=True):
    """Build a canonical structured-hook dict from parts."""
    return {
        "executable": str(executable or "").strip(),
        "args": [str(a) for a in (args or [])],
        "enabled": bool(enabled),
    }


def parse_hook_args_text(text):
    """Parse a UI editor block into an argument array (one argv element/line).

    Blank lines are ignored so trailing newlines do not create empty args.
    """
    args = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped:
            args.append(stripped)
    return args


def normalize_hook(value):
    """Classify a raw hook config value.

    Returns ``(kind, data)`` where *kind* is one of:

    * ``"empty"``      — nothing configured (``data`` is ``None``)
    * ``"legacy"``     — a non-empty shell string (``data`` is the string);
      legacy hooks are disabled and never executed
    * ``"structured"`` — a valid structured hook (``data`` is the canonical
      ``{"executable", "args", "enabled"}`` dict)
    * ``"invalid"``    — a malformed structured hook (``data`` is a reason)
    """
    if value is None:
        return ("empty", None)
    if isinstance(value, str):
        text = value.strip()
        return ("legacy", text) if text else ("empty", None)
    if isinstance(value, dict):
        if not value:
            return ("empty", None)
        executable = str(value.get("executable", "") or "").strip()
        raw_args = value.get("args", [])
        enabled = bool(value.get("enabled", True))
        if not isinstance(raw_args, list) or not all(
            isinstance(arg, str) for arg in raw_args
        ):
            return ("invalid", "args must be a list of strings")
        if not executable:
            if raw_args:
                return ("invalid", "missing executable")
            return ("empty", None)
        if "\x00" in executable or len(executable) > MAX_HOOK_EXECUTABLE_CHARS:
            return ("invalid", "executable is malformed")
        if len(raw_args) > MAX_HOOK_ARGS:
            return ("invalid", f"more than {MAX_HOOK_ARGS} arguments")
        clean_args = []
        for arg in raw_args:
            if "\x00" in arg:
                return ("invalid", "argument contains a NUL byte")
            clean_args.append(arg[:MAX_HOOK_ARG_CHARS])
        return ("structured", {
            "executable": executable,
            "args": clean_args,
            "enabled": enabled,
        })
    return ("invalid", "unsupported hook type")


def fire_hook(event, context, hooks_config, log_fn=None):
    """Fire the configured hook for *event*, if any.

    Only structured, enabled hooks execute. Legacy shell strings are refused
    with an actionable log message. Runs in a daemon thread so the UI never
    blocks.
    """
    if event not in HOOK_EVENTS or not isinstance(hooks_config, dict):
        return
    kind, data = normalize_hook(hooks_config.get(event))
    if kind == "empty":
        return
    if kind == "legacy":
        if log_fn:
            log_fn(
                f"[HOOK] {event} is a legacy shell command and is disabled; "
                "re-create it as a structured action (executable + arguments) "
                "in Settings to enable it."
            )
        return
    if kind == "invalid":
        if log_fn:
            log_fn(f"[HOOK] {event} is misconfigured ({data}); skipped.")
        return
    if not data["enabled"]:
        return

    env = _hook_env(event, context)
    argv = [data["executable"], *data["args"]]
    threading.Thread(
        target=_run_structured_hook,
        args=(event, argv, env, log_fn),
        daemon=True,
    ).start()


def _hook_env(event, context):
    """Assemble the minimal allowlisted environment for a hook process."""
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() in _ENV_ALLOWLIST
    }
    env["SK_EVENT"] = str(event)
    for key, value in (context or {}).items():
        normalized_key = str(key or "").strip().lower()
        if normalized_key not in HOOK_CONTEXT_KEYS:
            continue
        # Remote metadata is data-only: bounded environment variables, never
        # formatted, concatenated, or parsed into the command line.
        normalized_value = str(value or "").replace("\x00", "")
        env[f"SK_{normalized_key.upper()}"] = normalized_value[
            :MAX_HOOK_CONTEXT_CHARS
        ]
    return env


def _run_structured_hook(event, argv, env, log_fn):
    creationflags = _CREATE_NO_WINDOW
    popen_kwargs = {}
    if os.name == "nt":
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            argv,
            shell=False,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            **popen_kwargs,
        )
    except (OSError, ValueError) as e:
        if log_fn:
            log_fn(f"[HOOK] {event} could not start: {e}")
        return

    captured = bytearray()
    reader = threading.Thread(
        target=_drain_capped,
        args=(proc.stderr, MAX_HOOK_OUTPUT_BYTES, captured),
        daemon=True,
    )
    reader.start()
    try:
        proc.wait(timeout=HOOK_TIMEOUT)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        reader.join(timeout=1)
        if log_fn:
            log_fn(f"[HOOK] {event} timed out after {HOOK_TIMEOUT}s and was stopped")
        return
    reader.join(timeout=1)

    if proc.returncode not in (0, None) and log_fn:
        stderr = bytes(captured).decode("utf-8", errors="replace").strip()
        from .diagnostics import redact_text
        log_fn(
            f"[HOOK] {event} exited {proc.returncode}: "
            f"{redact_text(stderr)[:200]}"
        )


def _drain_capped(pipe, limit, sink):
    """Read *pipe* to EOF, retaining at most *limit* bytes in *sink*.

    The pipe is always fully drained so the child never blocks on a full
    buffer, but only the first *limit* bytes are kept for diagnostics.
    """
    if pipe is None:
        return
    remaining = limit
    try:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                break
            if remaining > 0:
                take = chunk[:remaining]
                sink.extend(take)
                remaining -= len(take)
    except (OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _terminate_process_tree(proc):
    """Kill *proc* and every descendant it spawned."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
                timeout=10,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.kill()
        except OSError:
            pass
    else:
        import signal
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
