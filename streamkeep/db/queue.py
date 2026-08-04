"""Download queue and executor-lease table-family facade."""

from . import _legacy as _implementation

_EXPORTED = frozenset({
    "load_queue", "save_queue", "load_queue_by_status", "load_queue_job",
    "skip_tombstoned_queue_jobs", "enqueue_queue_job", "sync_queue_items",
    "delete_queue_jobs", "delete_queue_job", "update_queue_job",
    "cancel_queue_job", "get_executor_lease", "acquire_executor_lease",
    "heartbeat_executor_lease", "release_executor_lease", "claim_queue_job",
    "transition_owned_queue_job", "recover_interrupted_queue_jobs",
})

__all__ = sorted(_EXPORTED)


def __getattr__(name):
    if name not in _EXPORTED:
        raise AttributeError(name)
    return getattr(_implementation, name)
