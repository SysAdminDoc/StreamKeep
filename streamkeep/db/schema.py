"""Schema migration sequence for the StreamKeep database.

Table-family CRUD lives behind the stable :mod:`streamkeep.db` facade.  This
module owns the ordered migration policy so schema review no longer requires
walking the connection, history, queue, publishing, and transfer code.
"""

from __future__ import annotations


def migrate_database(connection, version: int, target_version: int) -> None:
    """Apply every migration between ``version`` and ``target_version``.

    The migration helpers remain in the compatibility implementation for this
    first structural move; keeping their calls here makes ordering explicit
    without changing any SQL or transaction behavior.
    """
    from . import _legacy as implementation

    version = int(version or 0)
    target_version = int(target_version)
    if version >= target_version:
        return

    if version >= 1 and version < 4:
        implementation._migrate_queue_v4(connection)
    if version >= 1 and version < 5:
        implementation._migrate_queue_v5(connection)
    if version >= 1 and version < 6:
        implementation._migrate_monitor_v6(connection)
    if 0 < version < 8:
        implementation._migrate_execution_v8(connection)
    if 0 < version < 9:
        implementation._migrate_identity_v9(connection)
    if 0 < version < 10:
        implementation._migrate_retry_v10(connection)
    if 0 < version < 11:
        implementation._migrate_auth_profiles_v11(connection)
    if 0 < version < 12:
        implementation._migrate_media_layout_v12(connection)
    if 0 < version < 13:
        implementation._migrate_publishing_v13(connection)
    if 0 < version < 14:
        implementation._migrate_upload_v14(connection)
    if 0 < version < 15:
        implementation._migrate_intelligence_v15(connection)
    if version < 16:
        implementation._migrate_identity_v16(connection)
    if version < 17:
        implementation._migrate_tombstones_v17(connection)
    if version < 18:
        implementation._migrate_upgrade_v18(connection)
    if version < 19:
        implementation._migrate_integrity_v19(connection)
    if version < 20:
        implementation._migrate_history_actions_v20(connection)
    if version < 21:
        implementation._migrate_comments_v21(connection)

    implementation._apply_schema(connection)
    if version == 0:
        implementation._migrate_execution_v8(connection)
    try:
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_queue_status "
            "ON download_queue(status)"
        )
    except Exception:
        pass  # safe: best-effort fallback; preserve the primary operation
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_job_id "
        "ON download_queue(job_id) WHERE job_id <> ''"
    )
    connection.execute(f"PRAGMA user_version = {target_version}")
