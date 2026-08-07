"""SQLite connection lifecycle for the library database (V163).

Everything that decides *which* database file is open, keeps a per-thread
pooled handle for the active profile, and refuses a database written by a
newer build. Split out of ``_legacy`` so the 4,900 lines that merely acquire a
connection stop sharing a module with the code that creates one.

``DB_PATH`` lives here because this is the only code that reads it to open a
file. It is patched heavily in tests through the ``streamkeep.db`` facade,
which propagates a write to every module holding the name -- see
``_holders`` there. Nothing in this module imports a db sibling, so it can be
imported from anywhere in the package without a cycle.
"""

from __future__ import annotations

import sqlite3
import threading
import weakref
from pathlib import Path

from ..paths import CONFIG_DIR
from ..sqlite_runtime import connect as sqlite_connect


DB_PATH = CONFIG_DIR / "library.db"
SCHEMA_VERSION = 23

_connection_pool_lock = threading.RLock()
_connection_pools: weakref.WeakSet = weakref.WeakSet()
_active_profile_path: str | None = None


class _ConnectionState:
    """One physical connection and its per-thread logical leases."""

    def __init__(self, connection, key):
        self.connection = connection
        self.key = key
        self.leases = 0
        self.closed = False

    def acquire(self):
        if self.closed:
            raise sqlite3.ProgrammingError("SQLite connection is closed")
        self.leases += 1

    def release(self):
        if self.leases > 0:
            self.leases -= 1
        # Existing callers treated close() as the end of a short operation.
        # Preserve that rollback-on-close behavior while retaining the handle.
        try:
            if self.connection.in_transaction:
                self.connection.rollback()
        except sqlite3.Error:
            self.closed = True

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.connection.in_transaction:
                self.connection.rollback()
        except sqlite3.Error:
            pass
        try:
            self.connection.close()
        except sqlite3.Error:
            pass


class _ConnectionPool:
    """Thread-owned connection cache; destruction closes its physical handles."""

    def __init__(self):
        self.states: dict[tuple[str, bool], _ConnectionState] = {}
        with _connection_pool_lock:
            _connection_pools.add(self)

    def close(self):
        for state in list(self.states.values()):
            state.close()
        self.states.clear()

    def __del__(self):
        self.close()


class _PooledConnection:
    """Small lease facade retaining sqlite3.Connection compatibility."""

    def __init__(self, state: _ConnectionState):
        self._state = state
        self._connection = state.connection
        self._released = False
        state.acquire()

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        result = self._connection.__exit__(exc_type, exc_value, traceback)
        self.close()
        return result

    def close(self):
        if self._released:
            return
        self._released = True
        self._state.release()


_connection_local = threading.local()


def _connection_pool() -> _ConnectionPool:
    pool = getattr(_connection_local, "pool", None)
    if pool is None:
        pool = _ConnectionPool()
        _connection_local.pool = pool
    return pool


def _profile_database_path() -> Path:
    return Path(DB_PATH).expanduser().resolve(strict=False)


def _profile_cache_key(readonly: bool) -> tuple[str, bool] | None:
    """Return a cache key for the active long-lived profile database only."""
    database_path = _profile_database_path()
    config_path = Path(CONFIG_DIR).expanduser().resolve(strict=False)
    if database_path != config_path / "library.db":
        return None
    return str(database_path), bool(readonly)


def close_connections():
    """Close cached profile connections in every live StreamKeep thread."""
    with _connection_pool_lock:
        pools = list(_connection_pools)
    for pool in pools:
        pool.close()
    pool = getattr(_connection_local, "pool", None)
    if pool is not None:
        pool.close()


def _close_stale_profile_connections(current_path: str):
    with _connection_pool_lock:
        pools = list(_connection_pools)
    for pool in pools:
        for key, state in list(pool.states.items()):
            if key[0] != current_path:
                state.close()
                pool.states.pop(key, None)


class DatabaseSchemaError(RuntimeError):
    """Raised when a database was written by a newer StreamKeep build."""

    def __init__(self, database_version: int, supported_version: int):
        self.database_version = int(database_version)
        self.supported_version = int(supported_version)
        super().__init__(
            f"Database schema version {self.database_version} is newer than "
            f"this build supports ({self.supported_version}). Run a newer "
            "StreamKeep build to open this library."
        )


# ── Connection management ───────────────────────────────────────────


def _check_schema_version(path=None):
    """Read the schema version without opening a writable database handle."""
    database_path = Path(path or DB_PATH).expanduser().resolve(strict=False)
    if not database_path.is_file():
        return 0
    probe = sqlite_connect(
        f"{database_path.as_uri()}?mode=ro",
        uri=True,
        readonly=True,
        configure_journal=False,
    )
    try:
        version = int(probe.execute("PRAGMA user_version").fetchone()[0] or 0)
    finally:
        probe.close()
    if version > SCHEMA_VERSION:
        raise DatabaseSchemaError(version, SCHEMA_VERSION)
    return version


def _connect(readonly=False):
    """Return a per-thread cached profile connection lease.

    Temporary/staged databases intentionally retain the previous one-shot
    lifecycle because they are often renamed or deleted immediately after an
    operation.  The configured profile database is the hot path and is reused
    until explicit shutdown or a profile switch.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = _profile_cache_key(readonly)
    if cache_key is None:
        global _active_profile_path
        current_path = str(_profile_database_path())
        if _active_profile_path and _active_profile_path != current_path:
            _close_stale_profile_connections(current_path)
            _active_profile_path = None
        return sqlite_connect(
            str(DB_PATH),
            check_same_thread=False,
            timeout=10,
            readonly=readonly,
            row_factory=sqlite3.Row,
        )
    current_path = cache_key[0]
    if _active_profile_path and _active_profile_path != current_path:
        _close_stale_profile_connections(current_path)
    _active_profile_path = current_path
    pool = _connection_pool()
    state = pool.states.get(cache_key)
    if state is None or state.closed:
        state = _ConnectionState(
            sqlite_connect(
                str(DB_PATH),
                check_same_thread=False,
                timeout=10,
                readonly=readonly,
                row_factory=sqlite3.Row,
            ),
            cache_key,
        )
        pool.states[cache_key] = state
    return _PooledConnection(state)
