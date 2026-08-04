"""Diagnostics and maintenance table-family facade."""

from . import _legacy as _implementation

_EXPORTED = frozenset({
    "check_integrity", "run_optimize", "rebuild_history_indexes",
    "checkpoint_wal", "vacuum_after_backup", "db_diagnostics",
    "migrate_from_config",
})

__all__ = sorted(_EXPORTED)


def __getattr__(name):
    if name not in _EXPORTED:
        raise AttributeError(name)
    return getattr(_implementation, name)
