"""Archive-manifest and rolling-integrity table-family facade."""

from . import _legacy as _implementation

_EXPORTED = frozenset({
    "save_archive_manifest", "load_archive_manifest",
    "update_archive_manifest_check", "archive_manifest_count",
    "list_archive_manifest_records", "get_integrity_scrub_state",
    "list_integrity_scrub_states", "record_integrity_scrub",
    "record_integrity_scrub_run", "integrity_scrub_is_due",
})

__all__ = sorted(_EXPORTED)


def __getattr__(name):
    if name not in _EXPORTED:
        raise AttributeError(name)
    return getattr(_implementation, name)
