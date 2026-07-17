"""Non-blocking GUI instance guard.

The desktop application owns one shared config/database directory.  Starting
two GUI processes at the same time can make both of them race while applying
SQLite's journal policy, so reject duplicate GUI launches before either one
opens persistent state.  CLI commands remain independent and do not use this
guard.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QLockFile


LOCK_FILENAME = "streamkeep-instance.lock"


def acquire_gui_instance_lock(config_dir) -> QLockFile | None:
    """Return the held GUI lock, or ``None`` when another GUI owns it.

    ``QLockFile`` records the owning PID and application name, removes locks
    left by dead processes, and performs a non-blocking acquisition here so a
    duplicate launch exits immediately instead of waiting or surfacing a
    database-lock crash dialog.
    """
    config_path = Path(config_dir)
    config_path.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config_path / LOCK_FILENAME))
    if not lock.tryLock(0):
        return None
    return lock
