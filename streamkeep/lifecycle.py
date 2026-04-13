"""Auto-cleanup lifecycle policies — time-based, size-based, and favorites-exempt
retention rules for recordings.

Lifecycle evaluation always uses ``send2trash`` (recycle bin) — never
permanent delete.  A cleanup preview must be shown before execution.

Config keys (under ``config["lifecycle"]``):
    enabled, max_days (0=disabled), max_total_gb (0=disabled),
    delete_watched (bool), favorites_exempt (bool)
"""

import os
import time

DEFAULT_POLICY = {
    "enabled": False,
    "max_days": 0,
    "max_total_gb": 0,
    "delete_watched": False,
    "favorites_exempt": True,
}


def _dir_size_bytes(path):
    """Total size in bytes of all files in *path* (non-recursive top-level)."""
    total = 0
    try:
        for f in os.scandir(path):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def evaluate_cleanup(history, policy, output_dirs=None):
    """Return a list of ``(HistoryEntry, reason)`` tuples that should be
    cleaned up according to *policy*.

    Does NOT perform any deletion — call ``execute_cleanup()`` for that.
    ``output_dirs`` is an optional set/list of directories to consider;
    if None, all history entries are evaluated.
    """
    if not policy or not policy.get("enabled"):
        return []

    now = time.time()
    max_days = int(policy.get("max_days", 0) or 0)
    max_gb = float(policy.get("max_total_gb", 0) or 0)
    delete_watched = bool(policy.get("delete_watched"))
    fav_exempt = bool(policy.get("favorites_exempt", True))

    candidates = []
    total_bytes = 0

    for h in history:
        path = getattr(h, "path", "") or ""
        if output_dirs and path not in output_dirs:
            continue
        if fav_exempt and getattr(h, "favorite", False):
            continue
        entry_bytes = 0
        if path and os.path.isdir(path):
            entry_bytes = _dir_size_bytes(path)
        total_bytes += entry_bytes
        candidates.append((h, entry_bytes))

    to_remove = []

    # Rule 1: max age
    if max_days > 0:
        cutoff = now - max_days * 86400
        for h, _ in candidates:
            path = getattr(h, "path", "") or ""
            if not path or not os.path.isdir(path):
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime < cutoff:
                to_remove.append((h, f"older than {max_days} days"))

    # Rule 2: delete watched
    if delete_watched:
        for h, _ in candidates:
            if getattr(h, "watched", False):
                already = any(r[0] is h for r in to_remove)
                if not already:
                    to_remove.append((h, "watched"))

    # Rule 3: max total size — remove oldest watched first, then oldest
    if max_gb > 0:
        max_bytes = max_gb * 1024 ** 3
        if total_bytes > max_bytes:
            excess = total_bytes - max_bytes
            # Sort candidates by oldest mtime first
            sized = []
            for h, sz in candidates:
                if any(r[0] is h for r in to_remove):
                    continue  # already slated
                path = getattr(h, "path", "") or ""
                try:
                    mtime = os.path.getmtime(path) if path else 0
                except OSError:
                    mtime = 0
                sized.append((mtime, h, sz))
            sized.sort()  # oldest first
            freed = 0
            for _mt, h, sz in sized:
                if freed >= excess:
                    break
                to_remove.append((h, f"storage exceeds {max_gb:.0f} GB"))
                freed += sz

    return to_remove


def execute_cleanup(removals, log_fn=None):
    """Send each recording directory in *removals* to the recycle bin.

    *removals* is the list returned by ``evaluate_cleanup()``.
    Returns the count of successfully recycled recordings.
    """
    try:
        from send2trash import send2trash as _send2trash
    except ImportError:
        if log_fn:
            log_fn(
                "[LIFECYCLE] send2trash not installed — refusing to delete. "
                "Install with: pip install send2trash"
            )
        return 0

    removed = 0
    for h, reason in removals:
        path = getattr(h, "path", "") or ""
        if not path or not os.path.isdir(path):
            continue
        try:
            _send2trash(path)
            removed += 1
            if log_fn:
                log_fn(f"[LIFECYCLE] Recycled: {os.path.basename(path)} ({reason})")
        except Exception as e:
            if log_fn:
                log_fn(f"[LIFECYCLE] Failed to recycle {path}: {e}")
    return removed
