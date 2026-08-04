"""History and deletion-ledger table-family facade."""

from . import _legacy as _implementation

_EXPORTED = frozenset({
    "load_history", "history_snapshot_id", "query_history_page",
    "count_history_query", "iter_history", "search_history", "history_summary",
    "history_analytics", "save_history_entry", "save_completed_recording",
    "update_completed_recording", "record_upgrade_decision",
    "update_upgrade_decision", "list_upgrade_decisions", "latest_upgrade_decision",
    "adopt_history_records", "update_history_entry", "relocate_history_recording",
    "delete_history_entries", "delete_history_for_paths", "clear_history",
    "history_count", "find_history_by_url", "find_history_by_identity",
    "find_latest_history", "record_tombstone", "list_tombstones",
    "find_tombstone", "find_tombstone_for_item", "is_tombstoned",
    "is_tombstoned_for_item", "clear_tombstone", "build_rebuilt_library_database",
})

__all__ = sorted(_EXPORTED)


def __getattr__(name):
    if name not in _EXPORTED:
        raise AttributeError(name)
    return getattr(_implementation, name)
