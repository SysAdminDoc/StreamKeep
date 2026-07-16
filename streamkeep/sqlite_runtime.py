"""Central SQLite runtime policy and connection factory.

SQLite's WAL-reset corruption fix was released in 3.51.3 and backported to
3.50.7 and 3.44.6.  Source checkouts can remain usable on older Python builds
by using rollback journals; frozen releases must bundle a fixed runtime.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


FIXED_SQLITE_RELEASE = (3, 51, 3)
FIXED_SQLITE_BACKPORTS = {
    (3, 44): (3, 44, 6),
    (3, 50): (3, 50, 7),
}
FIXED_SQLITE_DESCRIPTION = "3.51.3 or patched 3.50.7/3.44.6"


class UnsafeSQLiteRuntimeError(RuntimeError):
    """Raised when a frozen application is linked to an unsafe SQLite."""


def _version_tuple(value=None):
    value = sqlite3.sqlite_version_info if value is None else value
    if isinstance(value, str):
        value = value.split(".")
    try:
        parts = tuple(int(part) for part in value)
    except (TypeError, ValueError):
        return ()
    return (parts + (0, 0, 0))[:3]


def wal_reset_is_fixed(version=None):
    """Return whether *version* includes SQLite's WAL-reset fix."""
    current = _version_tuple(version)
    if not current:
        return False
    if current >= FIXED_SQLITE_RELEASE:
        return True
    backport = FIXED_SQLITE_BACKPORTS.get(current[:2])
    return bool(backport and current >= backport)


def runtime_status(*, version=None, frozen=None):
    """Describe the safe journal policy for this Python SQLite runtime."""
    current = _version_tuple(version)
    version_text = ".".join(str(part) for part in current) if current else "unknown"
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    fixed = wal_reset_is_fixed(current)
    supported = fixed or not is_frozen
    degraded = not fixed and not is_frozen
    if fixed:
        detail = f"SQLite {version_text} includes the WAL-reset fix; WAL is enabled."
    elif is_frozen:
        detail = (
            f"SQLite {version_text} does not include the WAL-reset fix. Frozen "
            f"releases require SQLite {FIXED_SQLITE_DESCRIPTION}."
        )
    else:
        detail = (
            f"SQLite {version_text} does not include the WAL-reset fix; "
            "rollback journaling is enforced. Use a Python runtime linked to "
            f"SQLite {FIXED_SQLITE_DESCRIPTION} to enable WAL."
        )
    return {
        "version": version_text,
        "version_info": list(current),
        "wal_reset_fixed": fixed,
        "frozen": is_frozen,
        "supported": supported,
        "degraded": degraded,
        "journal_mode": "wal" if fixed else "delete",
        "minimum": FIXED_SQLITE_DESCRIPTION,
        "detail": detail,
    }


def require_safe_runtime():
    status = runtime_status()
    if not status["supported"]:
        raise UnsafeSQLiteRuntimeError(status["detail"])
    return status


def connect(
    database,
    *,
    timeout=10,
    check_same_thread=True,
    uri=False,
    readonly=False,
    configure_journal=True,
    row_factory=None,
):
    """Open a configured SQLite connection under the central safety policy."""
    status = require_safe_runtime()
    if not uri and not readonly and str(database) != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(database),
        timeout=timeout,
        check_same_thread=check_same_thread,
        uri=uri,
    )
    try:
        connection.execute(f"PRAGMA busy_timeout={max(0, int(timeout * 1000))}")
        connection.execute("PRAGMA foreign_keys=ON")
        if configure_journal and not readonly:
            desired = status["journal_mode"]
            row = connection.execute("PRAGMA journal_mode").fetchone()
            actual = str(row[0] if row else "").lower()
            if actual != desired:
                try:
                    row = connection.execute(
                        f"PRAGMA journal_mode={desired}"
                    ).fetchone()
                except sqlite3.OperationalError as error:
                    raise sqlite3.OperationalError(
                        f"Unable to enforce SQLite {desired} journal policy; "
                        "close other StreamKeep processes and retry"
                    ) from error
                actual = str(row[0] if row else "").lower()
            if actual != desired:
                raise sqlite3.OperationalError(
                    f"SQLite journal policy requires {desired}, got {actual or 'unknown'}"
                )
        if row_factory is not None:
            connection.row_factory = row_factory
        return connection
    except Exception:
        connection.close()
        raise
