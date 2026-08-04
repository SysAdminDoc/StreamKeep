"""Upload, intelligence, failure, retry, and backup table-family facade."""

from . import _legacy as _implementation

_EXPORTED = frozenset({
    "save_upload_profile", "load_upload_profile", "load_upload_profiles",
    "delete_upload_profile", "create_upload_job", "load_upload_job",
    "load_upload_jobs", "load_due_upload_jobs", "start_upload_job",
    "update_upload_progress", "finish_upload_job", "recover_upload_jobs",
    "retry_upload_job", "cancel_upload_job", "save_intelligence_profile",
    "load_intelligence_profile", "load_intelligence_profiles",
    "delete_intelligence_profile", "create_intelligence_job",
    "load_intelligence_job", "load_intelligence_jobs", "update_intelligence_job",
    "request_intelligence_cancel", "recover_intelligence_jobs", "save_failed_job",
    "load_failed_jobs", "load_failed_job", "mark_failed_job_retrying",
    "mark_failed_job_discarded", "mark_failed_job_resolved",
    "mark_failed_jobs_resolved_for_url", "load_due_failed_jobs",
    "promote_failed_job_retry", "promote_due_failed_jobs",
    "cancel_failed_job_retry", "load_retry_circuits", "load_backup_state",
    "claim_due_backup", "finish_backup_run", "request_backup_now",
    "release_backup_claim", "backup_state_public_view", "failed_job_public_view",
})

__all__ = sorted(_EXPORTED)


def __getattr__(name):
    if name not in _EXPORTED:
        raise AttributeError(name)
    return getattr(_implementation, name)
