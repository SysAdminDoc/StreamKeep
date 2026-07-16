"""Queue-complete power actions (V24).

When the download queue drains, StreamKeep can optionally notify, lock,
sleep, hibernate, shut down, or run a user hook. The action is chosen by
the user and defaults to ``none``. Destructive OS actions are issued with a
native cancellable delay (Windows ``shutdown /t`` — cancel with
``shutdown /a``) so an unattended run can still be aborted.

Command construction is separated from execution so the mapping can be
unit-tested without ever suspending or powering off the test machine.
"""

from __future__ import annotations

import os
import subprocess


# Ordered for display; "none" is the safe default.
POWER_ACTIONS = (
    "none",
    "notify",
    "run-hook",
    "lock",
    "sleep",
    "hibernate",
    "shutdown",
)

# Actions that only touch StreamKeep-internal surfaces (no OS command).
_SOFT_ACTIONS = frozenset({"none", "notify", "run-hook"})

# Default grace period before a destructive OS action, giving the user a
# window to cancel (Windows: `shutdown /a`).
DEFAULT_SHUTDOWN_DELAY_SECS = 60


def normalize_power_action(action):
    """Return a known power-action name, defaulting unknown/empty to ``none``."""
    name = str(action or "").strip().lower()
    return name if name in POWER_ACTIONS else "none"


def is_destructive(action):
    """True when the action suspends or powers off the machine."""
    return normalize_power_action(action) in {"sleep", "hibernate", "shutdown"}


def build_power_command(action, *, windows=None, delay_secs=DEFAULT_SHUTDOWN_DELAY_SECS):
    """Return the OS command argv for a power action, or ``[]`` for soft ones.

    ``windows`` selects the platform command set (defaults to the host).
    ``delay_secs`` sets the cancellable grace period for ``shutdown``.
    """
    action = normalize_power_action(action)
    if action in _SOFT_ACTIONS:
        return []
    if windows is None:
        windows = os.name == "nt"
    try:
        delay = max(0, int(delay_secs))
    except (TypeError, ValueError):
        delay = DEFAULT_SHUTDOWN_DELAY_SECS

    if windows:
        if action == "lock":
            return ["rundll32.exe", "user32.dll,LockWorkStation"]
        if action == "sleep":
            # SetSuspendState hibernate-flag 0 → sleep (honours system policy).
            return ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
        if action == "hibernate":
            return ["shutdown", "/h"]
        if action == "shutdown":
            return ["shutdown", "/s", "/t", str(delay)]
    else:
        if action == "lock":
            return ["loginctl", "lock-session"]
        if action == "sleep":
            return ["systemctl", "suspend"]
        if action == "hibernate":
            return ["systemctl", "hibernate"]
        if action == "shutdown":
            return ["shutdown", "-h", f"+{max(1, delay // 60)}"]
    return []


def run_queue_complete_action(
    action,
    *,
    notify_fn=None,
    hook_fn=None,
    execute=True,
    windows=None,
    delay_secs=DEFAULT_SHUTDOWN_DELAY_SECS,
    log_fn=None,
):
    """Dispatch a queue-complete power action.

    Soft actions call the provided ``notify_fn``/``hook_fn`` callbacks. OS
    actions build a command and run it (``shell=False``) only when
    ``execute`` is true; tests pass ``execute=False`` to verify the plan
    without powering off. Returns ``{"action", "command", "executed",
    "error"}``.
    """
    action = normalize_power_action(action)
    result = {"action": action, "command": [], "executed": False, "error": ""}

    def _log(message):
        if log_fn:
            try:
                log_fn(message)
            except Exception:
                pass

    if action == "none":
        return result
    if action == "notify":
        if notify_fn:
            try:
                notify_fn()
                result["executed"] = True
            except Exception as error:  # notification must never crash the queue
                result["error"] = str(error)
        return result
    if action == "run-hook":
        if hook_fn:
            try:
                hook_fn()
                result["executed"] = True
            except Exception as error:
                result["error"] = str(error)
        return result

    command = build_power_command(action, windows=windows, delay_secs=delay_secs)
    result["command"] = command
    if not command:
        result["error"] = "no command for action on this platform"
        return result
    if is_destructive(action):
        _log(
            f"[POWER] Queue complete — {action} scheduled: "
            f"{subprocess.list2cmdline(command)}"
        )
    if not execute:
        return result
    try:
        subprocess.Popen(  # noqa: S603 - fixed argv, shell=False
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result["executed"] = True
    except OSError as error:
        result["error"] = str(error)
        _log(f"[POWER] Could not run {action}: {error}")
    return result
