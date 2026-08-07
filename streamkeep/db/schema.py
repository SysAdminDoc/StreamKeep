"""Schema definition and migration for the StreamKeep database (V163).

This module owns both halves of schema work: the ordered migration policy and
the migrations themselves. It used to own only the ordering and call into the
legacy module for every step, so reviewing the schema still meant walking a
6,700-line file.

Every function here takes its connection as a parameter and never acquires
one, which is why nothing in this module imports the connection-owning module
at import time and the package stays acyclic.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

from .history_actions import _append_history_action_in_connection
from .primitives import _sqlite_table_exists, _utc_now_iso


def migrate_database(connection, version: int, target_version: int) -> None:
    """Apply every migration between ``version`` and ``target_version``.

    The migration helpers remain in the compatibility implementation for this
    first structural move; keeping their calls here makes ordering explicit
    without changing any SQL or transaction behavior.
    """
    version = int(version or 0)
    target_version = int(target_version)
    if version >= target_version:
        return

    if version >= 1 and version < 4:
        _migrate_queue_v4(connection)
    if version >= 1 and version < 5:
        _migrate_queue_v5(connection)
    if version >= 1 and version < 6:
        _migrate_monitor_v6(connection)
    if 0 < version < 8:
        _migrate_execution_v8(connection)
    if 0 < version < 9:
        _migrate_identity_v9(connection)
    if 0 < version < 10:
        _migrate_retry_v10(connection)
    if 0 < version < 11:
        _migrate_auth_profiles_v11(connection)
    if 0 < version < 12:
        _migrate_media_layout_v12(connection)
    if 0 < version < 13:
        _migrate_publishing_v13(connection)
    if 0 < version < 14:
        _migrate_upload_v14(connection)
    if 0 < version < 15:
        _migrate_intelligence_v15(connection)
    if version < 16:
        _migrate_identity_v16(connection)
    if version < 17:
        _migrate_tombstones_v17(connection)
    if version < 18:
        _migrate_upgrade_v18(connection)
    if version < 19:
        _migrate_integrity_v19(connection)
    if version < 20:
        _migrate_history_actions_v20(connection)
    if version < 21:
        _migrate_comments_v21(connection)
    if version < 22:
        _migrate_failure_codes_v22(connection)
    if version < 23:
        _migrate_circuit_engine_v23(connection)

    _apply_schema(connection)
    if version == 0:
        _migrate_execution_v8(connection)
    try:
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_queue_status "
            "ON download_queue(status)"
        )
    except Exception:
        # A query-speed index only. The unique index and the user_version
        # bump below carry the correctness of the migration and are
        # deliberately left unguarded, so a genuinely broken or locked
        # database still fails loudly on the next statement.
        pass
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_job_id "
        "ON download_queue(job_id) WHERE job_id <> ''"
    )
    connection.execute(f"PRAGMA user_version = {target_version}")


def _apply_schema(db):
    script = """
        CREATE TABLE IF NOT EXISTS history (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            date                TEXT NOT NULL DEFAULT '',
            platform            TEXT NOT NULL DEFAULT '',
            source_id           TEXT NOT NULL DEFAULT '',
            webpage_url         TEXT NOT NULL DEFAULT '',
            title               TEXT NOT NULL DEFAULT '',
            channel             TEXT NOT NULL DEFAULT '',
            quality             TEXT NOT NULL DEFAULT '',
            size                TEXT NOT NULL DEFAULT '',
            path                TEXT NOT NULL DEFAULT '',
            url                 TEXT NOT NULL DEFAULT '',
            favorite            INTEGER NOT NULL DEFAULT 0,
            watched             INTEGER NOT NULL DEFAULT 0,
            watch_position_secs REAL    NOT NULL DEFAULT 0.0,
            bookmarks           TEXT    NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_history_platform ON history(platform);
        CREATE INDEX IF NOT EXISTS idx_history_identity
            ON history(platform COLLATE NOCASE, source_id);
        CREATE INDEX IF NOT EXISTS idx_history_channel  ON history(channel);
        CREATE INDEX IF NOT EXISTS idx_history_date     ON history(date);
        CREATE INDEX IF NOT EXISTS idx_history_url      ON history(url);
        CREATE INDEX IF NOT EXISTS idx_history_webpage_url
            ON history(webpage_url);
        CREATE INDEX IF NOT EXISTS idx_history_path     ON history(path);
        CREATE INDEX IF NOT EXISTS idx_history_id_date  ON history(id DESC, date);
        CREATE INDEX IF NOT EXISTS idx_history_day
            ON history(substr(date, 1, 10));
        CREATE INDEX IF NOT EXISTS idx_history_title_nocase
            ON history(title COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_history_platform_nocase
            ON history(platform COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_history_channel_nocase
            ON history(channel COLLATE NOCASE);

        CREATE TABLE IF NOT EXISTS monitor_channels (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            url                         TEXT NOT NULL UNIQUE,
            platform                    TEXT NOT NULL DEFAULT '',
            channel_id                  TEXT NOT NULL DEFAULT '',
            interval_secs               INTEGER NOT NULL DEFAULT 120,
            auto_record                 INTEGER NOT NULL DEFAULT 0,
            subscribe_vods              INTEGER NOT NULL DEFAULT 0,
            capture_comments            INTEGER NOT NULL DEFAULT 0,
            archive_ids                 TEXT    NOT NULL DEFAULT '[]',
            override_output_dir         TEXT    NOT NULL DEFAULT '',
            override_quality_pref       TEXT    NOT NULL DEFAULT '',
            override_filename_template  TEXT    NOT NULL DEFAULT '',
            schedule_start_hhmm         TEXT    NOT NULL DEFAULT '',
            schedule_end_hhmm           TEXT    NOT NULL DEFAULT '',
            schedule_days_mask          INTEGER NOT NULL DEFAULT 0,
            retention_keep_last         INTEGER NOT NULL DEFAULT 0,
            filter_keywords             TEXT    NOT NULL DEFAULT '',
            override_pp_preset          TEXT    NOT NULL DEFAULT '',
            ytdlp_template_name         TEXT    NOT NULL DEFAULT '',
            auto_upgrade                INTEGER NOT NULL DEFAULT 0,
            min_upgrade_quality         TEXT    NOT NULL DEFAULT '',
            upgrade_profile_json        TEXT    NOT NULL DEFAULT '{}',
            auth_profile_id             TEXT    NOT NULL DEFAULT '',
            media_server_layout         TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS upgrade_decisions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT '',
            job_id              TEXT    NOT NULL DEFAULT '',
            history_id          INTEGER NOT NULL DEFAULT 0,
            platform            TEXT    NOT NULL DEFAULT '',
            source_id           TEXT    NOT NULL DEFAULT '',
            title               TEXT    NOT NULL DEFAULT '',
            channel             TEXT    NOT NULL DEFAULT '',
            current_quality     TEXT    NOT NULL DEFAULT '',
            candidate_quality   TEXT    NOT NULL DEFAULT '',
            decision            TEXT    NOT NULL DEFAULT 'rejected',
            reason_code         TEXT    NOT NULL DEFAULT '',
            reason              TEXT    NOT NULL DEFAULT '',
            score               REAL    NOT NULL DEFAULT 0.0,
            profile_json        TEXT    NOT NULL DEFAULT '{}',
            execution_status    TEXT    NOT NULL DEFAULT 'not_started',
            activation_path     TEXT    NOT NULL DEFAULT '',
            previous_path       TEXT    NOT NULL DEFAULT '',
            execution_error     TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_upgrade_decisions_history
            ON upgrade_decisions(history_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_upgrade_decisions_identity
            ON upgrade_decisions(platform COLLATE NOCASE, source_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_upgrade_decisions_created
            ON upgrade_decisions(created_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS download_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      TEXT    NOT NULL DEFAULT '',
            position    INTEGER NOT NULL DEFAULT 0,
            url         TEXT    NOT NULL DEFAULT '',
            title       TEXT    NOT NULL DEFAULT '',
            platform    TEXT    NOT NULL DEFAULT '',
            quality     TEXT    NOT NULL DEFAULT '',
            status      TEXT    NOT NULL DEFAULT 'queued',
            recurrence  TEXT    NOT NULL DEFAULT '',
            failure_id  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT '',
            updated_at  TEXT    NOT NULL DEFAULT '',
            data        TEXT    NOT NULL DEFAULT '{}',
            execution_owner TEXT NOT NULL DEFAULT '',
            revision    INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_queue_pos ON download_queue(position);
        CREATE INDEX IF NOT EXISTS idx_queue_execution_owner
            ON download_queue(execution_owner);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_job_id
            ON download_queue(job_id) WHERE job_id <> '';

        CREATE TABLE IF NOT EXISTS executor_leases (
            profile_id   TEXT PRIMARY KEY,
            owner_id     TEXT    NOT NULL DEFAULT '',
            owner_kind   TEXT    NOT NULL DEFAULT '',
            acquired_at  REAL    NOT NULL DEFAULT 0,
            heartbeat_at REAL    NOT NULL DEFAULT 0,
            expires_at   REAL    NOT NULL DEFAULT 0,
            generation   INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS backup_runs (
            profile_id       TEXT PRIMARY KEY,
            running_owner    TEXT    NOT NULL DEFAULT '',
            running_since    REAL    NOT NULL DEFAULT 0,
            next_run_at      REAL    NOT NULL DEFAULT 0,
            cadence_seconds  INTEGER NOT NULL DEFAULT 0,
            last_started_at  TEXT    NOT NULL DEFAULT '',
            last_success_at  TEXT    NOT NULL DEFAULT '',
            last_failure_at  TEXT    NOT NULL DEFAULT '',
            last_path        TEXT    NOT NULL DEFAULT '',
            last_size        INTEGER NOT NULL DEFAULT 0,
            last_error       TEXT    NOT NULL DEFAULT '',
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            updated_at       TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS archive_manifests (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            history_id         INTEGER NOT NULL UNIQUE,
            recording_path     TEXT    NOT NULL DEFAULT '',
            manifest_json      TEXT    NOT NULL DEFAULT '{}',
            created_at         TEXT    NOT NULL DEFAULT '',
            updated_at         TEXT    NOT NULL DEFAULT '',
            status             TEXT    NOT NULL DEFAULT '',
            last_check_at      TEXT    NOT NULL DEFAULT '',
            last_check_details TEXT    NOT NULL DEFAULT '',
            FOREIGN KEY(history_id) REFERENCES history(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_archive_manifest_history
            ON archive_manifests(history_id);
        CREATE INDEX IF NOT EXISTS idx_archive_manifest_path
            ON archive_manifests(recording_path);

        CREATE TABLE IF NOT EXISTS failed_jobs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            url            TEXT    NOT NULL DEFAULT '',
            platform       TEXT    NOT NULL DEFAULT '',
            title          TEXT    NOT NULL DEFAULT '',
            stage          TEXT    NOT NULL DEFAULT '',
            error          TEXT    NOT NULL DEFAULT '',
            output_dir     TEXT    NOT NULL DEFAULT '',
            resume_sidecar TEXT    NOT NULL DEFAULT '',
            retry_count    INTEGER NOT NULL DEFAULT 0,
            status         TEXT    NOT NULL DEFAULT 'retryable',
            queue_data     TEXT    NOT NULL DEFAULT '{}',
            context_json   TEXT    NOT NULL DEFAULT '{}',
            created_at     TEXT    NOT NULL DEFAULT '',
            updated_at     TEXT    NOT NULL DEFAULT '',
            last_retry_at  TEXT    NOT NULL DEFAULT '',
            category       TEXT    NOT NULL DEFAULT 'unknown',
            retryable      INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT   NOT NULL DEFAULT '',
            retry_after_seconds INTEGER NOT NULL DEFAULT 0,
            last_reason    TEXT    NOT NULL DEFAULT '',
            source_key     TEXT    NOT NULL DEFAULT '',
            source_label   TEXT    NOT NULL DEFAULT '',
            auto_retry     INTEGER NOT NULL DEFAULT 0,
            reason_code    TEXT    NOT NULL DEFAULT 'unknown',
            terminal       INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_failed_jobs_status
            ON failed_jobs(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_failed_jobs_url
            ON failed_jobs(url);
        CREATE INDEX IF NOT EXISTS idx_failed_jobs_due
            ON failed_jobs(status, auto_retry, next_attempt_at);
        CREATE INDEX IF NOT EXISTS idx_failed_jobs_source
            ON failed_jobs(source_key, status);

        CREATE TABLE IF NOT EXISTS retry_circuits (
            source_key       TEXT PRIMARY KEY,
            source_label     TEXT    NOT NULL DEFAULT '',
            engine           TEXT    NOT NULL DEFAULT '',
            failure_count    INTEGER NOT NULL DEFAULT 0,
            window_started_at REAL   NOT NULL DEFAULT 0,
            opened_until     REAL    NOT NULL DEFAULT 0,
            last_category    TEXT    NOT NULL DEFAULT '',
            last_reason      TEXT    NOT NULL DEFAULT '',
            updated_at       TEXT    NOT NULL DEFAULT ''
        );

    """
    statement = []
    for line in script.splitlines(keepends=True):
        statement.append(line)
        sql = "".join(statement)
        if sqlite3.complete_statement(sql):
            db.execute(sql)
            statement = []
    if statement and "".join(statement).strip():
        db.execute("".join(statement))
    _apply_publishing_schema(db)
    _apply_upload_schema(db)
    _apply_intelligence_schema(db)
    _apply_tombstone_schema(db)
    _apply_integrity_scrub_schema(db)
    _apply_history_action_schema(db)


def _apply_history_action_schema(db):
    """Create the append-only history projection log and its indexes."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS history_actions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            history_id    INTEGER NOT NULL DEFAULT 0,
            identity_key  TEXT    NOT NULL DEFAULT '',
            action        TEXT    NOT NULL DEFAULT 'snapshot',
            value_json    TEXT    NOT NULL DEFAULT '{}',
            created_at    TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_history_actions_identity
            ON history_actions(identity_key, id DESC);
        CREATE INDEX IF NOT EXISTS idx_history_actions_history
            ON history_actions(history_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_history_actions_created
            ON history_actions(created_at DESC, id DESC);
    """)


def _apply_tombstone_schema(db):
    """Create the durable deletion ledger and its canonical identity indexes."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS media_tombstones (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            platform    TEXT NOT NULL DEFAULT '',
            source_id   TEXT NOT NULL DEFAULT '',
            webpage_url TEXT NOT NULL DEFAULT '',
            deleted_at  TEXT NOT NULL DEFAULT '',
            reason      TEXT NOT NULL DEFAULT 'user',
            path        TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            channel     TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_tombstones_identity
            ON media_tombstones(platform COLLATE NOCASE, source_id);
        CREATE INDEX IF NOT EXISTS idx_tombstones_webpage_url
            ON media_tombstones(webpage_url);
        CREATE INDEX IF NOT EXISTS idx_tombstones_deleted_at
            ON media_tombstones(deleted_at DESC, id DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tombstones_source_identity
            ON media_tombstones(platform COLLATE NOCASE, source_id)
            WHERE source_id <> '';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tombstones_page_identity
            ON media_tombstones(webpage_url)
            WHERE webpage_url <> '';
    """)


def _apply_publishing_schema(db):
    """Create the durable gallery/feed publication registry."""
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS published_recordings (
            share_id    TEXT PRIMARY KEY,
            history_id  INTEGER NOT NULL UNIQUE,
            created_at  TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(history_id) REFERENCES history(id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_published_recordings_history "
        "ON published_recordings(history_id)",
        """
        CREATE TABLE IF NOT EXISTS published_feeds (
            feed_id     TEXT PRIMARY KEY,
            channel     TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_published_feeds_channel "
        "ON published_feeds(channel COLLATE NOCASE)",
    ):
        db.execute(statement)


def _apply_upload_schema(db):
    """Create the credential-free upload profile/job ledger."""
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS upload_profiles (
            profile_id  TEXT PRIMARY KEY,
            label       TEXT NOT NULL DEFAULT '',
            adapter     TEXT NOT NULL DEFAULT '',
            config_json TEXT NOT NULL DEFAULT '{}',
            secret_ref  TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT '',
            updated_at  TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS upload_jobs (
            upload_id       TEXT PRIMARY KEY,
            profile_id      TEXT NOT NULL,
            adapter         TEXT NOT NULL DEFAULT '',
            source_path     TEXT NOT NULL DEFAULT '',
            metadata_json   TEXT NOT NULL DEFAULT '{}',
            status          TEXT NOT NULL DEFAULT 'queued',
            bytes_sent      INTEGER NOT NULL DEFAULT 0,
            total_bytes     INTEGER NOT NULL DEFAULT 0,
            attempts        INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            last_error      TEXT NOT NULL DEFAULT '',
            remote_uri      TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT '',
            updated_at      TEXT NOT NULL DEFAULT '',
            completed_at    TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(profile_id) REFERENCES upload_profiles(profile_id)
                ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_upload_jobs_status "
        "ON upload_jobs(status, next_attempt_at, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_upload_jobs_profile "
        "ON upload_jobs(profile_id, updated_at)",
    ):
        db.execute(statement)


def _migrate_upload_v14(db):
    """Install the durable upload profile and transfer ledger."""
    _apply_upload_schema(db)


def _apply_intelligence_schema(db):
    """Create redacted intelligence profiles and durable analysis jobs."""
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS intelligence_profiles (
            profile_id  TEXT PRIMARY KEY,
            label       TEXT NOT NULL DEFAULT '',
            provider    TEXT NOT NULL DEFAULT 'ollama',
            model       TEXT NOT NULL DEFAULT '',
            api_url     TEXT NOT NULL DEFAULT '',
            config_json TEXT NOT NULL DEFAULT '{}',
            secret_ref  TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT '',
            updated_at  TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS intelligence_jobs (
            job_id              TEXT PRIMARY KEY,
            kind                TEXT NOT NULL DEFAULT 'summary',
            history_id          INTEGER NOT NULL DEFAULT 0,
            source_path         TEXT NOT NULL DEFAULT '',
            profile_id          TEXT NOT NULL DEFAULT '',
            provider            TEXT NOT NULL DEFAULT 'local',
            model               TEXT NOT NULL DEFAULT '',
            provider_version    TEXT NOT NULL DEFAULT '',
            status              TEXT NOT NULL DEFAULT 'queued',
            progress            REAL NOT NULL DEFAULT 0.0,
            payload_sha256      TEXT NOT NULL DEFAULT '',
            payload_chars       INTEGER NOT NULL DEFAULT 0,
            redaction_applied   INTEGER NOT NULL DEFAULT 0,
            result_path         TEXT NOT NULL DEFAULT '',
            result_json         TEXT NOT NULL DEFAULT '{}',
            error               TEXT NOT NULL DEFAULT '',
            cancel_requested    INTEGER NOT NULL DEFAULT 0,
            edited              INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT NOT NULL DEFAULT '',
            updated_at          TEXT NOT NULL DEFAULT '',
            completed_at        TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_intelligence_jobs_status "
        "ON intelligence_jobs(status, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_intelligence_jobs_source "
        "ON intelligence_jobs(source_path, kind, updated_at)",
    ):
        db.execute(statement)
    columns = {
        row[1] for row in db.execute(
            "PRAGMA table_info(intelligence_jobs)"
        ).fetchall()
    }
    if "profile_id" not in columns:
        db.execute(
            "ALTER TABLE intelligence_jobs ADD COLUMN profile_id TEXT NOT NULL DEFAULT ''"
        )


def _migrate_intelligence_v15(db):
    """Install the durable intelligence profile and job ledger."""
    _apply_intelligence_schema(db)


def _migrate_queue_v4(db):
    """Migrate download_queue from JSON-only blobs to typed columns.

    Adds columns if they don't exist, then backfills from the JSON data field.
    """
    existing_cols = {
        row[1] for row in db.execute("PRAGMA table_info(download_queue)").fetchall()
    }
    new_cols = [
        ("url", "TEXT NOT NULL DEFAULT ''"),
        ("title", "TEXT NOT NULL DEFAULT ''"),
        ("platform", "TEXT NOT NULL DEFAULT ''"),
        ("quality", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT 'queued'"),
        ("recurrence", "TEXT NOT NULL DEFAULT ''"),
        ("failure_id", "INTEGER NOT NULL DEFAULT 0"),
        ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ("updated_at", "TEXT NOT NULL DEFAULT ''"),
    ]
    for col_name, col_def in new_cols:
        if col_name not in existing_cols:
            db.execute(f"ALTER TABLE download_queue ADD COLUMN {col_name} {col_def}")

    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_queue_status ON download_queue(status)"
        )
    except Exception:
        # A query-speed index on a table this migration has just finished
        # altering. The ALTERs above are unguarded and would already have
        # failed loudly on a broken or locked database, so the only thing
        # reachable here is an index that cannot be built - which costs
        # lookup speed, not correctness.
        pass

    rows = db.execute("SELECT id, data FROM download_queue").fetchall()
    for row in rows:
        try:
            d = json.loads(row[1]) if row[1] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        db.execute("""
            UPDATE download_queue SET
                url = ?, title = ?, platform = ?, quality = ?,
                status = ?, recurrence = ?, failure_id = ?
            WHERE id = ?
        """, (
            str(d.get("url", "")),
            str(d.get("title", "")),
            str(d.get("platform", "")),
            str(d.get("quality", "")),
            str(d.get("status", "queued")),
            str(d.get("recurrence", "")),
            int(d.get("failure_id", 0) or 0),
            row[0],
        ))


def _migrate_queue_v5(db):
    """Give every persisted queue item a stable, externally visible job ID."""
    existing_cols = {
        row[1] for row in db.execute("PRAGMA table_info(download_queue)").fetchall()
    }
    if "job_id" not in existing_cols:
        db.execute(
            "ALTER TABLE download_queue ADD COLUMN "
            "job_id TEXT NOT NULL DEFAULT ''"
        )

    seen: set[str] = set()
    rows = db.execute("SELECT id, job_id, data FROM download_queue").fetchall()
    for row in rows:
        try:
            data = json.loads(row[2]) if row[2] else {}
        except (json.JSONDecodeError, TypeError):
            data = {}
        job_id = str(row[1] or data.get("job_id", "")).strip()
        if not job_id or job_id in seen:
            job_id = uuid.uuid4().hex
        seen.add(job_id)
        data["job_id"] = job_id
        db.execute(
            "UPDATE download_queue SET job_id = ?, data = ? WHERE id = ?",
            (job_id, json.dumps(data, ensure_ascii=False), row[0]),
        )


def _migrate_execution_v8(db):
    """Add durable queue ownership and reset only legacy unowned work."""
    existing_cols = {
        row[1] for row in db.execute("PRAGMA table_info(download_queue)").fetchall()
    }
    if not existing_cols:
        # Partial legacy fixtures/profiles may not have created the queue table;
        # _apply_schema() will create it with the v8 columns immediately after.
        return
    if "execution_owner" not in existing_cols:
        db.execute(
            "ALTER TABLE download_queue ADD COLUMN "
            "execution_owner TEXT NOT NULL DEFAULT ''"
        )
    if "revision" not in existing_cols:
        db.execute(
            "ALTER TABLE download_queue ADD COLUMN "
            "revision INTEGER NOT NULL DEFAULT 0"
        )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_queue_execution_owner "
        "ON download_queue(execution_owner)"
    )
    # Schema v7 had no owner identity, so any in-progress row necessarily came
    # from a process that no longer has a valid v8 lease.
    db.execute(
        "UPDATE download_queue "
        "SET status = 'queued', execution_owner = '', revision = revision + 1, "
        "updated_at = ? "
        "WHERE status IN "
        "('fetching', 'downloading', 'finalizing', 'running', 'cancelling')",
        (_utc_now_iso(),),
    )


def _migrate_monitor_v6(db):
    """Add the named yt-dlp argument-template attachment to monitor jobs."""
    existing_cols = {
        row[1] for row in db.execute(
            "PRAGMA table_info(monitor_channels)"
        ).fetchall()
    }
    if not existing_cols:
        return
    if "ytdlp_template_name" not in existing_cols:
        db.execute(
            "ALTER TABLE monitor_channels ADD COLUMN "
            "ytdlp_template_name TEXT NOT NULL DEFAULT ''"
        )


def _migrate_auth_profiles_v11(db):
    """Give monitors an opaque site-bound authentication profile reference."""
    existing_cols = {
        row[1] for row in db.execute(
            "PRAGMA table_info(monitor_channels)"
        ).fetchall()
    }
    if not existing_cols:
        return
    if "auth_profile_id" not in existing_cols:
        db.execute(
            "ALTER TABLE monitor_channels ADD COLUMN "
            "auth_profile_id TEXT NOT NULL DEFAULT ''"
        )


def _migrate_identity_v9(db):
    """Add stable, platform-scoped source identity to completed history."""
    existing_cols = {
        row[1] for row in db.execute("PRAGMA table_info(history)").fetchall()
    }
    if not existing_cols:
        return
    if "source_id" not in existing_cols:
        db.execute(
            "ALTER TABLE history ADD COLUMN "
            "source_id TEXT NOT NULL DEFAULT ''"
        )
    # Only derive content identities from legacy public URLs. Channel-level
    # identities are deliberately excluded: they identify a creator, not one
    # recording, and must never make unrelated VODs upgrade-eligible.
    from ..metadata import build_archival_provenance
    from ..models import StreamInfo
    rows = db.execute(
        "SELECT id, platform, channel, url FROM history WHERE source_id=''"
    ).fetchall()
    for row in rows:
        info = StreamInfo(
            platform=str(row[1] or ""),
            channel=str(row[2] or ""),
            url=str(row[3] or ""),
        )
        identity = build_archival_provenance(
            info, source_url=str(row[3] or "")
        ).source_id
        if identity and not identity.lower().startswith("channel:"):
            db.execute(
                "UPDATE history SET source_id=? WHERE id=?",
                (identity, int(row[0])),
            )


def _migrate_identity_v16(db):
    """Persist canonical page URLs and repair legacy identity rows."""
    existing_cols = {
        row[1] for row in db.execute("PRAGMA table_info(history)").fetchall()
    }
    if not existing_cols:
        return
    if "source_id" not in existing_cols:
        db.execute(
            "ALTER TABLE history ADD COLUMN "
            "source_id TEXT NOT NULL DEFAULT ''"
        )
    if "webpage_url" not in existing_cols:
        db.execute(
            "ALTER TABLE history ADD COLUMN "
            "webpage_url TEXT NOT NULL DEFAULT ''"
        )

    from ..metadata import build_archival_provenance
    from ..models import StreamInfo

    rows = db.execute(
        "SELECT id, platform, source_id, channel, url, webpage_url "
        "FROM history"
    ).fetchall()
    for row in rows:
        raw_url = str(row[5] or row[4] or "")
        info = StreamInfo(
            platform=str(row[1] or ""),
            channel=str(row[3] or ""),
            source_id=str(row[2] or ""),
            webpage_url=raw_url,
        )
        provenance = build_archival_provenance(
            info, source_url=raw_url,
        )
        canonical_url = provenance.webpage_url
        if not canonical_url and raw_url:
            # Keep the migration conservative: an unrecognized source is
            # explicitly unknown rather than being assigned a guessed ID.
            canonical_url = ""
        db.execute(
            "UPDATE history SET source_id=?, webpage_url=?, url=? WHERE id=?",
            (
                provenance.source_id,
                canonical_url,
                canonical_url or str(row[4] or ""),
                int(row[0]),
            ),
        )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_webpage_url "
        "ON history(webpage_url)"
    )


def _migrate_tombstones_v17(db):
    """Install the deletion ledger used to suppress deliberate re-fetches."""
    _apply_tombstone_schema(db)


def _migrate_upgrade_v18(db):
    """Add explicit monitor upgrade profiles and durable decisions."""
    existing_cols = {
        row[1] for row in db.execute(
            "PRAGMA table_info(monitor_channels)"
        ).fetchall()
    }
    if existing_cols and "upgrade_profile_json" not in existing_cols:
        db.execute(
            "ALTER TABLE monitor_channels ADD COLUMN "
            "upgrade_profile_json TEXT NOT NULL DEFAULT '{}'"
        )
    _apply_upgrade_decision_schema(db)


def _migrate_integrity_v19(db):
    """Install rolling archive-integrity scrub state."""
    _apply_integrity_scrub_schema(db)


def _migrate_history_actions_v20(db):
    """Install and seed the append-only history projection log."""
    _apply_history_action_schema(db)
    if not _sqlite_table_exists(db, "history"):
        return
    rows = db.execute("SELECT * FROM history ORDER BY id ASC").fetchall()
    for row in rows:
        if db.execute(
            "SELECT 1 FROM history_actions WHERE history_id=? LIMIT 1",
            (int(row[0]),),
        ).fetchone():
            continue
        _append_history_action_in_connection(
            db, int(row[0]), "snapshot", dict(row),
        )


def _migrate_comments_v21(db):
    """Add the per-monitor opt-in for bounded VOD comment archives."""
    existing_cols = {
        row[1] for row in db.execute(
            "PRAGMA table_info(monitor_channels)"
        ).fetchall()
    }
    if existing_cols and "capture_comments" not in existing_cols:
        db.execute(
            "ALTER TABLE monitor_channels ADD COLUMN "
            "capture_comments INTEGER NOT NULL DEFAULT 0"
        )


def _apply_integrity_scrub_schema(db):
    """Create per-recording and global rolling-scrub checkpoints."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS integrity_scrub_state (
            history_id          INTEGER PRIMARY KEY,
            recording_path      TEXT NOT NULL DEFAULT '',
            last_cheap_at       TEXT NOT NULL DEFAULT '',
            last_full_at        TEXT NOT NULL DEFAULT '',
            status              TEXT NOT NULL DEFAULT '',
            details             TEXT NOT NULL DEFAULT '',
            last_full_bytes     INTEGER NOT NULL DEFAULT 0,
            last_duration_ms    INTEGER NOT NULL DEFAULT 0,
            run_started_at      TEXT NOT NULL DEFAULT '',
            run_finished_at     TEXT NOT NULL DEFAULT '',
            run_status          TEXT NOT NULL DEFAULT '',
            run_details         TEXT NOT NULL DEFAULT '',
            run_checked         INTEGER NOT NULL DEFAULT 0,
            run_mismatches      INTEGER NOT NULL DEFAULT 0,
            run_skipped         INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_integrity_scrub_full
            ON integrity_scrub_state(last_full_at, history_id);
        CREATE INDEX IF NOT EXISTS idx_integrity_scrub_status
            ON integrity_scrub_state(status, history_id);
    """)


def _apply_upgrade_decision_schema(db):
    """Create the upgrade audit table for new and rebuilt databases."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS upgrade_decisions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT '',
            job_id              TEXT    NOT NULL DEFAULT '',
            history_id          INTEGER NOT NULL DEFAULT 0,
            platform            TEXT    NOT NULL DEFAULT '',
            source_id           TEXT    NOT NULL DEFAULT '',
            title               TEXT    NOT NULL DEFAULT '',
            channel             TEXT    NOT NULL DEFAULT '',
            current_quality     TEXT    NOT NULL DEFAULT '',
            candidate_quality   TEXT    NOT NULL DEFAULT '',
            decision            TEXT    NOT NULL DEFAULT 'rejected',
            reason_code         TEXT    NOT NULL DEFAULT '',
            reason              TEXT    NOT NULL DEFAULT '',
            score               REAL    NOT NULL DEFAULT 0.0,
            profile_json        TEXT    NOT NULL DEFAULT '{}',
            execution_status    TEXT    NOT NULL DEFAULT 'not_started',
            activation_path     TEXT    NOT NULL DEFAULT '',
            previous_path       TEXT    NOT NULL DEFAULT '',
            execution_error     TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_upgrade_decisions_history
            ON upgrade_decisions(history_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_upgrade_decisions_identity
            ON upgrade_decisions(platform COLLATE NOCASE, source_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_upgrade_decisions_created
            ON upgrade_decisions(created_at DESC, id DESC);
    """)


def _migrate_media_layout_v12(db):
    """Add the per-monitor media-server layout override."""
    existing_cols = {
        row[1] for row in db.execute(
            "PRAGMA table_info(monitor_channels)"
        ).fetchall()
    }
    if existing_cols and "media_server_layout" not in existing_cols:
        db.execute(
            "ALTER TABLE monitor_channels ADD COLUMN "
            "media_server_layout TEXT NOT NULL DEFAULT ''"
        )


def _migrate_publishing_v13(db):
    """Create durable, revocable gallery and feed publication state."""
    _apply_publishing_schema(db)


def _migrate_circuit_engine_v23(db):
    """Record which engine a source's failures came from (V165).

    A standing "repeated extractor failures" condition that does not name the
    engine leaves the operator guessing which switch recovers the platform,
    which is why a broken extractor reads as a broken app.
    """
    existing_cols = {
        row[1] for row in db.execute("PRAGMA table_info(retry_circuits)").fetchall()
    }
    if not existing_cols:
        return
    if "engine" not in existing_cols:
        db.execute(
            "ALTER TABLE retry_circuits ADD COLUMN engine "
            "TEXT NOT NULL DEFAULT ''"
        )


def _migrate_failure_codes_v22(db):
    """Add the machine-readable failure taxonomy (V154).

    The pre-existing ``category`` stays as the coarse bucket the remediation
    table is keyed by. ``reason_code`` names the specific condition and
    ``terminal`` marks the ones no retry or operator action can fix, so a
    permanently-gone item stops being offered for retry forever.
    """
    existing_cols = {
        row[1] for row in db.execute("PRAGMA table_info(failed_jobs)").fetchall()
    }
    if not existing_cols:
        return
    for col_name, col_def in (
        ("reason_code", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("terminal", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if col_name not in existing_cols:
            db.execute(
                f"ALTER TABLE failed_jobs ADD COLUMN {col_name} {col_def}"
            )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_failed_jobs_reason_code "
        "ON failed_jobs(reason_code)"
    )


def _migrate_retry_v10(db):
    """Add persistent error policy, due times, and per-source circuits."""
    existing_cols = {
        row[1] for row in db.execute("PRAGMA table_info(failed_jobs)").fetchall()
    }
    if not existing_cols:
        return
    new_cols = [
        ("category", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("retryable", "INTEGER NOT NULL DEFAULT 0"),
        ("next_attempt_at", "TEXT NOT NULL DEFAULT ''"),
        ("retry_after_seconds", "INTEGER NOT NULL DEFAULT 0"),
        ("last_reason", "TEXT NOT NULL DEFAULT ''"),
        ("source_key", "TEXT NOT NULL DEFAULT ''"),
        ("source_label", "TEXT NOT NULL DEFAULT ''"),
        ("auto_retry", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col_name, col_def in new_cols:
        if col_name not in existing_cols:
            db.execute(
                f"ALTER TABLE failed_jobs ADD COLUMN {col_name} {col_def}"
            )
    db.execute("""
        CREATE TABLE IF NOT EXISTS retry_circuits (
            source_key       TEXT PRIMARY KEY,
            source_label     TEXT    NOT NULL DEFAULT '',
            failure_count    INTEGER NOT NULL DEFAULT 0,
            window_started_at REAL   NOT NULL DEFAULT 0,
            opened_until     REAL    NOT NULL DEFAULT 0,
            last_category    TEXT    NOT NULL DEFAULT '',
            last_reason      TEXT    NOT NULL DEFAULT '',
            updated_at       TEXT    NOT NULL DEFAULT ''
        )
    """)

    # The backfill below writes the full classification, including the
    # taxonomy columns a later migration owns. Adding them here first keeps a
    # v9 database from failing on a column the chain has not reached yet;
    # both migrations are idempotent, so v22 running twice is a no-op.
    _migrate_failure_codes_v22(db)
    _migrate_circuit_engine_v23(db)

    from ..retry import (
        classify_failure,
        retry_delay_seconds,
        retry_source,
        utc_iso,
    )
    current_time = time.time()
    rows = db.execute(
        "SELECT id, url, platform, error, retry_count, status, queue_data "
        "FROM failed_jobs"
    ).fetchall()
    for row in rows:
        try:
            queue_data = json.loads(row[6]) if row[6] else {}
        except (json.JSONDecodeError, TypeError):
            queue_data = {}
        decision = classify_failure(row[3], now=current_time)
        source_key, source_label = retry_source(
            row[1],
            row[2],
            queue_data.get("source_id", "") if isinstance(queue_data, dict) else "",
        )
        status = str(row[5] or "")
        auto_retry = int(decision.retryable and status == "retryable")
        next_attempt_at = ""
        if auto_retry:
            delay = retry_delay_seconds(
                int(row[4] or 0) + 1,
                source_key,
                retry_after_seconds=decision.retry_after_seconds,
            )
            next_attempt_at = utc_iso(current_time + delay)
        elif decision.terminal and status != "resolved":
            # Permanently gone: distinct from "intervention", which means an
            # operator could still make it work.
            status = "terminal"
        elif status == "retryable":
            status = "intervention"
        db.execute("""
            UPDATE failed_jobs
               SET category=?, retryable=?, next_attempt_at=?,
                   retry_after_seconds=?, last_reason=?, source_key=?,
                   source_label=?, auto_retry=?, status=?, error=?,
                   reason_code=?, terminal=?
             WHERE id=?
        """, (
            decision.category,
            int(decision.retryable),
            next_attempt_at,
            decision.retry_after_seconds,
            decision.reason,
            source_key,
            source_label,
            auto_retry,
            status,
            decision.reason,
            decision.code,
            int(decision.terminal),
            int(row[0]),
        ))
