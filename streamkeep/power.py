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

import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass


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


@dataclass(frozen=True)
class PowerState:
    """Best-effort effective power state for queue policy decisions."""

    available: bool
    on_battery: bool
    energy_saver: bool
    effective_mode: str = "unknown"


_POWER_MODE_NAMES = {
    0: "battery-saver",
    1: "better-battery",
    2: "balanced",
    3: "best-performance",
    4: "maximum-performance",
    5: "performance",
    6: "balanced",
}


def _read_system_power_status():
    """Return the Win32 ``SYSTEM_POWER_STATUS`` fields, or ``None``."""
    if sys.platform != "win32":
        return None
    try:
        class _SystemPowerStatus(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_uint32),
                ("BatteryFullLifeTime", ctypes.c_uint32),
            ]

        status = _SystemPowerStatus()
        get_status = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemPowerStatus
        get_status.argtypes = [ctypes.POINTER(_SystemPowerStatus)]
        get_status.restype = ctypes.c_int
        if not get_status(ctypes.byref(status)):
            return None
        return {
            "ac_line_status": int(status.ACLineStatus),
            "battery_flag": int(status.BatteryFlag),
            "battery_percent": int(status.BatteryLifePercent),
            "system_status_flag": int(status.SystemStatusFlag),
        }
    except Exception:
        return None


def _read_effective_power_mode():
    """Read ``PowerGetEffectivePowerMode`` when the OS exports it."""
    if sys.platform != "win32":
        return None
    try:
        powrprof = ctypes.WinDLL("PowrProf", use_last_error=True)
        get_mode = getattr(powrprof, "PowerGetEffectivePowerMode", None)
        if get_mode is None:
            return None
        mode = ctypes.c_uint32()
        get_mode.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
        get_mode.restype = ctypes.c_uint32
        if int(get_mode(ctypes.byref(mode))) != 0:
            return None
        return int(mode.value)
    except Exception:
        return None


def read_windows_power_state():
    """Return current battery/Energy Saver state without changing power.

    ``GetSystemPowerStatus`` is the fallback source for battery state.  The
    effective mode API is optional on older Windows versions; if it is absent,
    only the battery signal is used and the queue policy remains conservative.
    Non-Windows callers receive an unavailable state.
    """
    if sys.platform != "win32":
        return PowerState(False, False, False, "unsupported")
    status = _read_system_power_status()
    if status is None:
        return PowerState(False, False, False, "unavailable")
    mode_value = _read_effective_power_mode()
    mode = _POWER_MODE_NAMES.get(mode_value, "unknown")
    # Windows' first effective mode is the Battery Saver/Energy Saver mode.
    energy_saver = mode_value == 0
    return PowerState(
        True,
        int(status.get("ac_line_status", 255)) == 0,
        energy_saver,
        mode,
    )


def should_pause_for_power(state):
    """Return whether the opt-in queue policy should hold new work."""
    if not state or not bool(getattr(state, "available", False)):
        return False
    return bool(
        getattr(state, "on_battery", False)
        or getattr(state, "energy_saver", False)
    )


def power_pause_reason(state):
    """Return a concise log label for a queue pause decision."""
    if getattr(state, "on_battery", False):
        return "battery"
    if getattr(state, "energy_saver", False):
        mode = getattr(state, "effective_mode", "") or "Energy Saver"
        return f"Energy Saver ({mode})"
    return ""


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
                pass  # safe: best-effort fallback; preserve the primary operation

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
