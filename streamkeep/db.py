"""SQLite library database — history, monitor channels, download queue.

Replaces the list-of-dicts sections of config.json with properly indexed
SQLite tables.  Config.json retains only user preferences and UI state.

Database lives at ``%APPDATA%/StreamKeep/library.db`` (or ``data/library.db``
in portable mode).  The central SQLite policy enables WAL only on runtimes
with the WAL-reset fix and otherwise uses rollback journaling.  All writes go
through module-level functions that serialise behind a lock.

Schema version is stored in ``PRAGMA user_version`` and bumped on each
migration so future schema changes are orderly.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .paths import CONFIG_DIR
from .sqlite_runtime import connect as sqlite_connect
from .sqlite_runtime import runtime_status

DB_PATH = CONFIG_DIR / "library.db"
SCHEMA_VERSION = 10

_write_lock = threading.Lock()


# ── Connection management ───────────────────────────────────────────

def _connect(readonly=False):
    """Return a connection.  Caller is responsible for closing."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return sqlite_connect(
        str(DB_PATH),
        check_same_thread=False,
        timeout=10,
        readonly=readonly,
        row_factory=sqlite3.Row,
    )


def init_db() -> None:
    """Create tables if they don't exist.  Idempotent."""
    # Repair a config directory left mixed by a restore that died mid-swap
    # before opening the database. Lazy import avoids a backup<->db cycle.
    try:
        from .backup import finalize_interrupted_restore
        finalize_interrupted_restore()
    except Exception:
        pass
    db = _connect()
    try:
        v = db.execute("PRAGMA user_version").fetchone()[0]
        if v < SCHEMA_VERSION:
            if v >= 1 and v < 4:
                _migrate_queue_v4(db)
            if v >= 1 and v < 5:
                _migrate_queue_v5(db)
            if v >= 1 and v < 6:
                _migrate_monitor_v6(db)
            if 0 < v < 8:
                _migrate_execution_v8(db)
            if 0 < v < 9:
                _migrate_identity_v9(db)
            if 0 < v < 10:
                _migrate_retry_v10(db)
            _apply_schema(db)
            if v == 0:
                _migrate_execution_v8(db)
            if v < 7:
                db.execute("INSERT INTO history_fts(history_fts) VALUES('rebuild')")
            try:
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_queue_status "
                    "ON download_queue(status)"
                )
            except Exception:
                pass
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_job_id "
                "ON download_queue(job_id) WHERE job_id <> ''"
            )
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            db.commit()
    finally:
        db.close()


def _apply_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS history (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            date                TEXT NOT NULL DEFAULT '',
            platform            TEXT NOT NULL DEFAULT '',
            source_id           TEXT NOT NULL DEFAULT '',
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

        CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
            title, platform, channel, path, url,
            content='history', content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS history_fts_insert AFTER INSERT ON history BEGIN
            INSERT INTO history_fts(rowid, title, platform, channel, path, url)
            VALUES (new.id, new.title, new.platform, new.channel, new.path, new.url);
        END;
        CREATE TRIGGER IF NOT EXISTS history_fts_delete AFTER DELETE ON history BEGIN
            INSERT INTO history_fts(history_fts, rowid, title, platform, channel, path, url)
            VALUES ('delete', old.id, old.title, old.platform, old.channel, old.path, old.url);
        END;
        CREATE TRIGGER IF NOT EXISTS history_fts_update
        AFTER UPDATE OF title, platform, channel, path, url ON history BEGIN
            INSERT INTO history_fts(history_fts, rowid, title, platform, channel, path, url)
            VALUES ('delete', old.id, old.title, old.platform, old.channel, old.path, old.url);
            INSERT INTO history_fts(rowid, title, platform, channel, path, url)
            VALUES (new.id, new.title, new.platform, new.channel, new.path, new.url);
        END;

        CREATE TABLE IF NOT EXISTS monitor_channels (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            url                         TEXT NOT NULL UNIQUE,
            platform                    TEXT NOT NULL DEFAULT '',
            channel_id                  TEXT NOT NULL DEFAULT '',
            interval_secs               INTEGER NOT NULL DEFAULT 120,
            auto_record                 INTEGER NOT NULL DEFAULT 0,
            subscribe_vods              INTEGER NOT NULL DEFAULT 0,
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
            min_upgrade_quality         TEXT    NOT NULL DEFAULT ''
        );

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
            auto_retry     INTEGER NOT NULL DEFAULT 0
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
            failure_count    INTEGER NOT NULL DEFAULT 0,
            window_started_at REAL   NOT NULL DEFAULT 0,
            opened_until     REAL    NOT NULL DEFAULT 0,
            last_category    TEXT    NOT NULL DEFAULT '',
            last_reason      TEXT    NOT NULL DEFAULT '',
            updated_at       TEXT    NOT NULL DEFAULT ''
        );
    """)


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
    from .metadata import build_archival_provenance
    from .models import StreamInfo
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

    from .retry import (
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
        elif status == "retryable":
            status = "intervention"
        db.execute("""
            UPDATE failed_jobs
               SET category=?, retryable=?, next_attempt_at=?,
                   retry_after_seconds=?, last_reason=?, source_key=?,
                   source_label=?, auto_retry=?, status=?, error=?
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
            int(row[0]),
        ))


# ── History CRUD ────────────────────────────────────────────────────

def load_history() -> list[dict[str, Any]]:
    """Return all history entries as a list of dicts, oldest-first."""
    db = _connect(readonly=True)
    try:
        rows = db.execute(
            "SELECT * FROM history ORDER BY id ASC"
        ).fetchall()
        return [_row_to_history_dict(r) for r in rows]
    finally:
        db.close()


def history_snapshot_id() -> int:
    """Return the newest history id for a stable paged-query snapshot."""
    db = _connect(readonly=True)
    try:
        row = db.execute("SELECT COALESCE(MAX(id), 0) FROM history").fetchone()
        return int(row[0] or 0)
    finally:
        db.close()


def _history_fts_query(query: str) -> str:
    """Build a literal prefix-token FTS query from untrusted user text."""
    tokens = re.findall(r"\w+", str(query or "").lower(), flags=re.UNICODE)
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)


def query_history_page(
    *,
    query: str = "",
    limit: int = 100,
    before_id: int | None = None,
    snapshot_id: int | None = None,
    recording_paths: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Return one newest-first history page using stable keyset pagination."""
    limit = max(1, min(1000, int(limit or 100)))
    snapshot_id = int(snapshot_id if snapshot_id is not None else history_snapshot_id())
    before_id = int(before_id if before_id is not None else snapshot_id + 1)
    paths_filter = recording_paths is not None
    paths = [str(path) for path in (recording_paths or []) if path]
    if paths_filter and not paths:
        return []
    fts_query = _history_fts_query(query)
    where = ["h.id <= ?", "h.id < ?"]
    params: list[Any] = [snapshot_id, before_id]
    join = ""
    if fts_query:
        join = "JOIN history_fts ON history_fts.rowid = h.id"
        where.append("history_fts MATCH ?")
        params.append(fts_query)
    if paths:
        placeholders = ",".join("?" for _ in paths)
        where.append(f"h.path IN ({placeholders})")
        params.extend(paths)
    params.append(limit)
    db = _connect(readonly=True)
    try:
        rows = db.execute(
            f"SELECT h.* FROM history h {join} "
            f"WHERE {' AND '.join(where)} ORDER BY h.id DESC LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_history_dict(row) for row in rows]
    finally:
        db.close()


def count_history_query(
    *,
    query: str = "",
    snapshot_id: int | None = None,
    recording_paths: list[str] | tuple[str, ...] | None = None,
) -> int:
    """Count a paged history query against the same snapshot boundary."""
    snapshot_id = int(snapshot_id if snapshot_id is not None else history_snapshot_id())
    paths_filter = recording_paths is not None
    paths = [str(path) for path in (recording_paths or []) if path]
    if paths_filter and not paths:
        return 0
    fts_query = _history_fts_query(query)
    where = ["h.id <= ?"]
    params: list[Any] = [snapshot_id]
    join = ""
    if fts_query:
        join = "JOIN history_fts ON history_fts.rowid = h.id"
        where.append("history_fts MATCH ?")
        params.append(fts_query)
    if paths:
        placeholders = ",".join("?" for _ in paths)
        where.append(f"h.path IN ({placeholders})")
        params.extend(paths)
    db = _connect(readonly=True)
    try:
        row = db.execute(
            f"SELECT COUNT(*) FROM history h {join} WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
        return int(row[0] or 0)
    finally:
        db.close()


def iter_history(*, newest_first=False, page_size=500):
    """Yield history rows in bounded pages without materializing the archive."""
    snapshot = history_snapshot_id()
    before_id = snapshot + 1
    if newest_first:
        while True:
            page = query_history_page(
                limit=page_size,
                before_id=before_id,
                snapshot_id=snapshot,
            )
            if not page:
                return
            yield from page
            before_id = int(page[-1]["id"])
    else:
        after_id = 0
        page_size = max(1, min(1000, int(page_size or 500)))
        while True:
            db = _connect(readonly=True)
            try:
                rows = db.execute(
                    "SELECT * FROM history WHERE id > ? AND id <= ? "
                    "ORDER BY id ASC LIMIT ?",
                    (after_id, snapshot, page_size),
                ).fetchall()
            finally:
                db.close()
            if not rows:
                return
            for row in rows:
                yield _row_to_history_dict(row)
            after_id = int(rows[-1]["id"])


def search_history(query: str, *, limit=15) -> list[dict[str, Any]]:
    """Return a bounded newest-first metadata search for global search."""
    return query_history_page(query=query, limit=limit)


def history_summary() -> dict[str, Any]:
    """Return indexed/aggregate values used by the shell and History hero."""
    db = _connect(readonly=True)
    try:
        total = int(db.execute("SELECT COUNT(*) FROM history").fetchone()[0] or 0)
        latest_row = db.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        platform_row = db.execute(
            "SELECT platform, COUNT(*) AS n FROM history WHERE platform <> '' "
            "GROUP BY platform ORDER BY n DESC, platform ASC LIMIT 1"
        ).fetchone()
        channel_row = db.execute(
            "SELECT channel, COUNT(*) AS n FROM history WHERE channel <> '' "
            "GROUP BY channel ORDER BY n DESC, channel ASC LIMIT 1"
        ).fetchone()
        return {
            "total": total,
            "latest": _row_to_history_dict(latest_row) if latest_row else None,
            "top_platform": tuple(platform_row) if platform_row else ("", 0),
            "top_channel": tuple(channel_row) if channel_row else ("", 0),
        }
    finally:
        db.close()


def history_analytics(cutoff_date: str = "") -> dict[str, Any]:
    """Return aggregate analytics without loading individual history rows."""
    where = "WHERE substr(date, 1, 10) >= ?" if cutoff_date else ""
    params = (cutoff_date,) if cutoff_date else ()
    size_gb = """
        SUM(CASE
            WHEN upper(trim(size)) LIKE '% TB' THEN CAST(size AS REAL) * 1024.0
            WHEN upper(trim(size)) LIKE '% GB' THEN CAST(size AS REAL)
            WHEN upper(trim(size)) LIKE '% MB' THEN CAST(size AS REAL) / 1024.0
            WHEN upper(trim(size)) LIKE '% KB' THEN CAST(size AS REAL) / 1048576.0
            ELSE 0 END)
    """
    db = _connect(readonly=True)
    try:
        totals = db.execute(
            f"SELECT COUNT(*), COALESCE({size_gb}, 0) FROM history {where}",
            params,
        ).fetchone()
        platforms = db.execute(
            f"SELECT platform, COUNT(*) AS n FROM history {where} "
            f"{'AND' if where else 'WHERE'} platform <> '' "
            "GROUP BY platform ORDER BY n DESC, platform ASC LIMIT 8",
            params,
        ).fetchall()
        channels = db.execute(
            f"SELECT channel, COUNT(*) AS n FROM history {where} "
            f"{'AND' if where else 'WHERE'} channel <> '' "
            "GROUP BY channel ORDER BY n DESC, channel ASC LIMIT 8",
            params,
        ).fetchall()
        daily_desc = db.execute(
            f"SELECT substr(date, 1, 10) AS day, COUNT(*) AS n FROM history {where} "
            f"{'AND' if where else 'WHERE'} length(date) >= 10 "
            "GROUP BY day ORDER BY day DESC LIMIT 30",
            params,
        ).fetchall()
        return {
            "total": int(totals[0] or 0),
            "size_gb": float(totals[1] or 0),
            "platforms": [tuple(row) for row in platforms],
            "channels": [tuple(row) for row in channels],
            "daily": list(reversed([tuple(row) for row in daily_desc])),
        }
    finally:
        db.close()


def save_history_entry(entry_dict: dict[str, Any]) -> int | None:
    """Insert a single history entry. Returns the new row id."""
    return save_completed_recording(entry_dict)


def save_completed_recording(
    entry_dict: dict[str, Any],
    manifest: dict[str, Any] | None = None,
) -> int | None:
    """Atomically persist completed history and its optional manifest."""
    with _write_lock:
        db = _connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("""
                INSERT INTO history
                    (date, platform, source_id, title, channel, quality, size,
                     path, url, favorite, watched, watch_position_secs, bookmarks)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(entry_dict.get("date", "")),
                str(entry_dict.get("platform", "")),
                str(entry_dict.get("source_id", "")),
                str(entry_dict.get("title", "")),
                str(entry_dict.get("channel", "")),
                str(entry_dict.get("quality", "")),
                str(entry_dict.get("size", "")),
                str(entry_dict.get("path", "")),
                str(entry_dict.get("url", "")),
                int(bool(entry_dict.get("favorite", False))),
                int(bool(entry_dict.get("watched", False))),
                float(entry_dict.get("watch_position_secs", 0) or 0),
                json.dumps(entry_dict.get("bookmarks", []) or []),
            ))
            history_id = int(cur.lastrowid)
            if manifest is not None:
                if not isinstance(manifest, dict):
                    raise TypeError("archive manifest must be a dictionary")
                now = _utc_now_iso()
                db.execute("""
                    INSERT INTO archive_manifests
                        (history_id, recording_path, manifest_json, created_at,
                         updated_at, status, last_check_at, last_check_details)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    history_id,
                    str(entry_dict.get("path", "")),
                    json.dumps(
                        manifest, ensure_ascii=False, sort_keys=True,
                    ),
                    str(manifest.get("created_at", now) or now),
                    now,
                    "created",
                    now,
                    (
                        f"Captured {len(manifest.get('files', []) or [])} "
                        "file(s)"
                    ),
                ))
            db.commit()
            return history_id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def update_history_entry(entry_id: int, fields: dict[str, Any]) -> None:
    """Update specific fields on a history row by id.

    *fields* is a dict of column_name -> value.  Only known columns
    are applied (unknown keys are silently ignored).
    """
    allowed = {
        "date", "platform", "source_id", "title", "channel", "quality", "size",
        "path", "url", "favorite", "watched", "watch_position_secs",
        "bookmarks",
    }
    parts = []
    vals = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "bookmarks":
            v = json.dumps(v if isinstance(v, list) else [])
        elif k in ("favorite", "watched"):
            v = int(bool(v))
        elif k == "watch_position_secs":
            v = float(v or 0)
        else:
            v = str(v)
        parts.append(f"{k}=?")
        vals.append(v)
    if not parts:
        return
    vals.append(int(entry_id))
    with _write_lock:
        db = _connect()
        try:
            db.execute(
                f"UPDATE history SET {', '.join(parts)} WHERE id=?",
                vals,
            )
            db.commit()
        finally:
            db.close()


def delete_history_entries(entry_ids: list[int]) -> None:
    """Delete history rows by id list."""
    if not entry_ids:
        return
    with _write_lock:
        db = _connect()
        try:
            placeholders = ",".join("?" for _ in entry_ids)
            ids = [int(i) for i in entry_ids]
            db.execute(
                f"DELETE FROM archive_manifests WHERE history_id IN ({placeholders})",
                ids,
            )
            db.execute(
                f"DELETE FROM history WHERE id IN ({placeholders})",
                ids,
            )
            db.commit()
        finally:
            db.close()


def clear_history() -> None:
    """Delete all history entries."""
    with _write_lock:
        db = _connect()
        try:
            db.execute("DELETE FROM archive_manifests")
            db.execute("DELETE FROM history")
            db.commit()
        finally:
            db.close()


def history_count() -> int:
    """Return total number of history entries."""
    db = _connect(readonly=True)
    try:
        return db.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    finally:
        db.close()


def find_history_by_url(url: str) -> dict[str, Any] | None:
    """Return the most recent history entry matching *url*, or None."""
    if not url:
        return None
    db = _connect(readonly=True)
    try:
        row = db.execute(
            "SELECT * FROM history WHERE url=? ORDER BY id DESC LIMIT 1",
            (str(url),),
        ).fetchone()
        return _row_to_history_dict(row) if row else None
    finally:
        db.close()


def find_history_by_identity(platform: str, source_id: str) -> dict[str, Any] | None:
    """Return the newest recording with an exact platform/source identity."""
    platform = str(platform or "").strip()
    source_id = str(source_id or "").strip()
    if not platform or not source_id:
        return None
    db = _connect(readonly=True)
    try:
        row = db.execute(
            "SELECT * FROM history "
            "WHERE platform=? COLLATE NOCASE AND source_id=? "
            "ORDER BY id DESC LIMIT 1",
            (platform, source_id),
        ).fetchone()
        return _row_to_history_dict(row) if row else None
    finally:
        db.close()


def find_latest_history(*, channel="", title="", platform="") -> dict[str, Any] | None:
    """Return the newest row matching indexed exact metadata fields."""
    where = []
    params = []
    if channel:
        where.append("channel = ? COLLATE NOCASE")
        params.append(str(channel))
    if title:
        where.append("title = ? COLLATE NOCASE")
        params.append(str(title))
    if platform:
        where.append("platform = ? COLLATE NOCASE")
        params.append(str(platform))
    if not where:
        return None
    db = _connect(readonly=True)
    try:
        row = db.execute(
            f"SELECT * FROM history WHERE {' AND '.join(where)} "
            "ORDER BY id DESC LIMIT 1",
            params,
        ).fetchone()
        return _row_to_history_dict(row) if row else None
    finally:
        db.close()


# ── Monitor channels CRUD ──────────────────────────────────────────

def load_monitor_channels() -> list[dict[str, Any]]:
    """Return all monitor channels as a list of dicts."""
    db = _connect(readonly=True)
    try:
        rows = db.execute(
            "SELECT * FROM monitor_channels ORDER BY id ASC"
        ).fetchall()
        return [_row_to_monitor_dict(r) for r in rows]
    finally:
        db.close()


def save_monitor_channel(entry_dict: dict[str, Any]) -> int | None:
    """Insert or replace a monitor channel (keyed by url). Returns row id."""
    with _write_lock:
        db = _connect()
        try:
            cur = db.execute("""
                INSERT OR REPLACE INTO monitor_channels
                    (url, platform, channel_id, interval_secs, auto_record,
                     subscribe_vods, archive_ids,
                     override_output_dir, override_quality_pref,
                     override_filename_template,
                     schedule_start_hhmm, schedule_end_hhmm, schedule_days_mask,
                     retention_keep_last, filter_keywords, override_pp_preset,
                     ytdlp_template_name, auto_upgrade, min_upgrade_quality)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(entry_dict.get("url", "")),
                str(entry_dict.get("platform", "")),
                str(entry_dict.get("channel_id", "")),
                int(entry_dict.get("interval_secs", 120) or 120),
                int(bool(entry_dict.get("auto_record", False))),
                int(bool(entry_dict.get("subscribe_vods", False))),
                json.dumps(entry_dict.get("archive_ids", []) or []),
                str(entry_dict.get("override_output_dir", "") or ""),
                str(entry_dict.get("override_quality_pref", "") or ""),
                str(entry_dict.get("override_filename_template", "") or ""),
                str(entry_dict.get("schedule_start_hhmm", "") or ""),
                str(entry_dict.get("schedule_end_hhmm", "") or ""),
                int(entry_dict.get("schedule_days_mask", 0) or 0),
                int(entry_dict.get("retention_keep_last", 0) or 0),
                str(entry_dict.get("filter_keywords", "") or ""),
                str(entry_dict.get("override_pp_preset", "") or ""),
                str(entry_dict.get("ytdlp_template_name", "") or ""),
                int(bool(entry_dict.get("auto_upgrade", False))),
                str(entry_dict.get("min_upgrade_quality", "") or ""),
            ))
            db.commit()
            return cur.lastrowid
        finally:
            db.close()


def save_all_monitor_channels(entries_dicts: list[dict[str, Any]]) -> None:
    """Replace all monitor channels atomically."""
    with _write_lock:
        db = _connect()
        try:
            db.execute("DELETE FROM monitor_channels")
            for d in entries_dicts:
                db.execute("""
                    INSERT INTO monitor_channels
                        (url, platform, channel_id, interval_secs, auto_record,
                         subscribe_vods, archive_ids,
                         override_output_dir, override_quality_pref,
                         override_filename_template,
                         schedule_start_hhmm, schedule_end_hhmm,
                         schedule_days_mask, retention_keep_last,
                         filter_keywords, override_pp_preset,
                         ytdlp_template_name, auto_upgrade, min_upgrade_quality)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    str(d.get("url", "")),
                    str(d.get("platform", "")),
                    str(d.get("channel_id", "")),
                    int(d.get("interval_secs", 120) or 120),
                    int(bool(d.get("auto_record", False))),
                    int(bool(d.get("subscribe_vods", False))),
                    json.dumps(d.get("archive_ids", []) or []),
                    str(d.get("override_output_dir", "") or ""),
                    str(d.get("override_quality_pref", "") or ""),
                    str(d.get("override_filename_template", "") or ""),
                    str(d.get("schedule_start_hhmm", "") or ""),
                    str(d.get("schedule_end_hhmm", "") or ""),
                    int(d.get("schedule_days_mask", 0) or 0),
                    int(d.get("retention_keep_last", 0) or 0),
                    str(d.get("filter_keywords", "") or ""),
                    str(d.get("override_pp_preset", "") or ""),
                    str(d.get("ytdlp_template_name", "") or ""),
                    int(bool(d.get("auto_upgrade", False))),
                    str(d.get("min_upgrade_quality", "") or ""),
                ))
            db.commit()
        finally:
            db.close()


def delete_monitor_channel(url: str) -> None:
    """Remove a monitor channel by URL."""
    with _write_lock:
        db = _connect()
        try:
            db.execute("DELETE FROM monitor_channels WHERE url=?", (url,))
            db.commit()
        finally:
            db.close()


# ── Download queue CRUD ─────────────────────────────────────────────

def load_queue() -> list[dict[str, Any]]:
    """Return all queue items as a list of dicts, ordered by position."""
    db = _connect(readonly=True)
    try:
        rows = db.execute(
            "SELECT job_id, url, title, platform, quality, status, recurrence, "
            "failure_id, created_at, updated_at, data, revision, execution_owner "
            "FROM download_queue ORDER BY position ASC"
        ).fetchall()
        return [_queue_row_to_dict(r) for r in rows]
    finally:
        db.close()


def save_queue(items: list[dict[str, Any]]) -> None:
    """Replace the queue for one-time migration and isolated fixtures only.

    Live callers must use :func:`sync_queue_items` plus explicit row deletes;
    replacing a stale process-local snapshot can otherwise erase work added by
    another process.
    """
    now = _utc_now_iso()
    with _write_lock:
        db = _connect()
        try:
            db.execute("DELETE FROM download_queue")
            for i, item in enumerate(items):
                job_id = str(item.get("job_id", "")).strip() or uuid.uuid4().hex
                item["job_id"] = job_id
                db.execute(
                    "INSERT INTO download_queue "
                    "(job_id, position, url, title, platform, quality, status, "
                    " recurrence, failure_id, created_at, updated_at, data) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        i,
                        str(item.get("url", "")),
                        str(item.get("title", "")),
                        str(item.get("platform", "")),
                        str(item.get("quality", "")),
                        str(item.get("status", "queued")),
                        str(item.get("recurrence", "")),
                        int(item.get("failure_id", 0) or 0),
                        str(item.get("created_at", "") or now),
                        now,
                        json.dumps(item, ensure_ascii=False),
                    ),
                )
            db.commit()
        finally:
            db.close()


def load_queue_by_status(status: str) -> list[dict[str, Any]]:
    """Return queue items filtered by status column."""
    db = _connect(readonly=True)
    try:
        rows = db.execute(
            "SELECT job_id, url, title, platform, quality, status, recurrence, "
            "failure_id, created_at, updated_at, data, revision, execution_owner "
            "FROM download_queue WHERE status = ? ORDER BY position ASC",
            (status,),
        ).fetchall()
        return [_queue_row_to_dict(r) for r in rows]
    finally:
        db.close()


def load_queue_job(job_id: str) -> dict[str, Any] | None:
    """Return a queue item by its durable public job ID."""
    db = _connect(readonly=True)
    try:
        row = db.execute(
            "SELECT job_id, url, title, platform, quality, status, recurrence, "
            "failure_id, created_at, updated_at, data, revision, execution_owner "
            "FROM download_queue WHERE job_id = ?",
            (str(job_id),),
        ).fetchone()
        return _queue_row_to_dict(row) if row else None
    finally:
        db.close()


def enqueue_queue_job(item: dict[str, Any]) -> dict[str, Any]:
    """Append one durable queue job without rewriting unrelated queue rows."""
    now = _utc_now_iso()
    data = dict(item)
    job_id = str(data.get("job_id", "")).strip() or uuid.uuid4().hex
    data["job_id"] = job_id
    data["status"] = str(data.get("status", "queued") or "queued")
    with _write_lock:
        db = _connect()
        try:
            position = int(db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM download_queue"
            ).fetchone()[0])
            db.execute(
                "INSERT INTO download_queue "
                "(job_id, position, url, title, platform, quality, status, "
                " recurrence, failure_id, created_at, updated_at, data) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, position, str(data.get("url", "")),
                    str(data.get("title", "")), str(data.get("platform", "")),
                    str(data.get("quality", "")), data["status"],
                    str(data.get("recurrence", "")),
                    int(data.get("failure_id", 0) or 0),
                    str(data.get("created_at", "") or now), now,
                    json.dumps(data, ensure_ascii=False),
                ),
            )
            db.commit()
        finally:
            db.close()
    result = load_queue_job(job_id)
    if result is None:  # pragma: no cover - protects against external DB deletion
        raise RuntimeError("Queue job disappeared after insertion")
    return result


def sync_queue_items(
    items: list[dict[str, Any]],
    *,
    owner_id: str = "",
) -> list[dict[str, Any]]:
    """Merge a process-local queue view without deleting unseen durable rows.

    Rows actively owned by another executor are never overwritten. Explicit
    removals must go through :func:`delete_queue_job`; this distinction makes a
    delayed GUI config save safe when a headless process has enqueued or
    advanced work since the GUI last loaded its view.
    """
    now = _utc_now_iso()
    owner_id = str(owner_id or "")
    ordered_ids: list[str] = []
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_rows = conn.execute(
                "SELECT job_id, url, title, platform, quality, status, recurrence, "
                "failure_id, created_at, updated_at, data, revision, execution_owner "
                "FROM download_queue ORDER BY position ASC"
            ).fetchall()
            existing = {str(row[0]): row for row in existing_rows}
            for item in items:
                if not isinstance(item, dict):
                    continue
                data = dict(item)
                job_id = str(data.get("job_id", "")).strip() or uuid.uuid4().hex
                data["job_id"] = job_id
                item["job_id"] = job_id
                current = existing.get(job_id)
                current_owner = str(current[12] or "") if current else ""
                try:
                    snapshot_revision = int(data.get("revision", 0) or 0)
                except (TypeError, ValueError):
                    snapshot_revision = 0
                if (
                    current is not None
                    and (
                        (current_owner and current_owner != owner_id)
                        or int(current[11] or 0) != snapshot_revision
                    )
                ):
                    ordered_ids.append(job_id)
                    continue
                data.pop("revision", None)
                data.pop("execution_owner", None)
                status = str(data.get("status", "queued") or "queued")
                if current is None:
                    conn.execute(
                        "INSERT INTO download_queue "
                        "(job_id, position, url, title, platform, quality, status, "
                        " recurrence, failure_id, created_at, updated_at, data) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            job_id, len(ordered_ids), str(data.get("url", "")),
                            str(data.get("title", "")),
                            str(data.get("platform", "")),
                            str(data.get("quality", "")), status,
                            str(data.get("recurrence", "")),
                            int(data.get("failure_id", 0) or 0),
                            str(data.get("created_at", "") or now), now,
                            json.dumps(data, ensure_ascii=False),
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE download_queue SET "
                        "url=?, title=?, platform=?, quality=?, status=?, "
                        "recurrence=?, failure_id=?, updated_at=?, data=?, "
                        "revision=revision+1 WHERE job_id=?",
                        (
                            str(data.get("url", "")),
                            str(data.get("title", "")),
                            str(data.get("platform", "")),
                            str(data.get("quality", "")), status,
                            str(data.get("recurrence", "")),
                            int(data.get("failure_id", 0) or 0), now,
                            json.dumps(data, ensure_ascii=False), job_id,
                        ),
                    )
                ordered_ids.append(job_id)

            # Preserve every row that was not present in this process-local
            # snapshot. They are appended in their existing relative order.
            for row in existing_rows:
                job_id = str(row[0])
                if job_id not in ordered_ids:
                    ordered_ids.append(job_id)
            for position, job_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE download_queue SET position=? WHERE job_id=?",
                    (position, job_id),
                )
            conn.commit()
        finally:
            conn.close()
    return load_queue()


def delete_queue_jobs(
    job_ids: list[str] | tuple[str, ...] | set[str],
    *,
    requester_owner: str = "",
) -> set[str]:
    """Delete non-running rows explicitly and return the IDs actually removed."""
    wanted = {str(job_id) for job_id in job_ids if str(job_id)}
    if not wanted:
        return set()
    requester_owner = str(requester_owner or "")
    placeholders = ",".join("?" for _ in wanted)
    params: list[Any] = [*sorted(wanted), requester_owner]
    with _write_lock:
        conn = _connect()
        try:
            rows = conn.execute(
                f"SELECT job_id FROM download_queue WHERE job_id IN ({placeholders}) "
                "AND status NOT IN "
                "('fetching','downloading','finalizing','running','cancelling') "
                "AND (execution_owner='' OR execution_owner=?)",
                params,
            ).fetchall()
            removed = {str(row[0]) for row in rows}
            if removed:
                delete_marks = ",".join("?" for _ in removed)
                conn.execute(
                    f"DELETE FROM download_queue WHERE job_id IN ({delete_marks})",
                    sorted(removed),
                )
                remaining = conn.execute(
                    "SELECT job_id FROM download_queue ORDER BY position ASC"
                ).fetchall()
                for position, row in enumerate(remaining):
                    conn.execute(
                        "UPDATE download_queue SET position=? WHERE job_id=?",
                        (position, str(row[0])),
                    )
            conn.commit()
            return removed
        finally:
            conn.close()


def delete_queue_job(job_id: str, *, requester_owner: str = "") -> bool:
    return str(job_id) in delete_queue_jobs(
        [str(job_id)], requester_owner=requester_owner,
    )


def update_queue_job(
    job_id: str,
    *,
    expected_revision: int | None = None,
    **changes: Any,
) -> dict[str, Any] | None:
    """Atomically merge fields into one durable queue job.

    ``expected_revision`` provides optimistic compare-and-swap semantics for
    non-executor edits. Executor state changes use
    :func:`transition_owned_queue_job`, which additionally verifies ownership.
    """
    job_id = str(job_id)
    now = _utc_now_iso()
    typed = {
        "url", "title", "platform", "quality", "status", "recurrence",
        "failure_id",
    }
    with _write_lock:
        db = _connect()
        try:
            row = db.execute(
                "SELECT data, revision FROM download_queue WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            try:
                data = json.loads(row[0]) if row[0] else {}
            except (json.JSONDecodeError, TypeError):
                data = {}
            data.update(changes)
            data["job_id"] = job_id
            assignments = [
                "updated_at = ?", "data = ?", "revision = revision + 1",
            ]
            values: list[Any] = [now, json.dumps(data, ensure_ascii=False)]
            for name in sorted(typed.intersection(changes)):
                assignments.append(f"{name} = ?")
                value = changes[name]
                if name == "failure_id":
                    value = int(value or 0)
                else:
                    value = str(value or "")
                values.append(value)
            where = "job_id = ?"
            values.append(job_id)
            if expected_revision is not None:
                where += " AND revision = ?"
                values.append(int(expected_revision))
            result = db.execute(
                f"UPDATE download_queue SET {', '.join(assignments)} "
                f"WHERE {where}",
                values,
            )
            if result.rowcount != 1:
                db.rollback()
                return None
            db.commit()
        finally:
            db.close()
    return load_queue_job(job_id)


def cancel_queue_job(job_id: str) -> dict[str, Any] | None:
    """Persist cancellation unless a job is already terminal."""
    job_id = str(job_id)
    now = _utc_now_iso()
    with _write_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT status, data FROM download_queue WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            if str(row[0]) in {"done", "failed", "cancelled"}:
                return load_queue_job(job_id)
            try:
                data = json.loads(row[1]) if row[1] else {}
            except (json.JSONDecodeError, TypeError):
                data = {}
            data.update({
                "job_id": job_id,
                "status": "cancelled",
                "cancelled_at": now,
            })
            conn.execute(
                "UPDATE download_queue SET status='cancelled', "
                "execution_owner='', updated_at=?, data=?, revision=revision+1 "
                "WHERE job_id=?",
                (now, json.dumps(data, ensure_ascii=False), job_id),
            )
            conn.commit()
        finally:
            conn.close()
    return load_queue_job(job_id)


def get_executor_lease(profile_id: str = "default") -> dict[str, Any] | None:
    """Return the current profile executor lease, including expired leases."""
    conn = _connect(readonly=True)
    try:
        row = conn.execute(
            "SELECT profile_id, owner_id, owner_kind, acquired_at, "
            "heartbeat_at, expires_at, generation "
            "FROM executor_leases WHERE profile_id=?",
            (str(profile_id or "default"),),
        ).fetchone()
        if row is None:
            return None
        return {
            "profile_id": str(row[0]),
            "owner_id": str(row[1]),
            "owner_kind": str(row[2]),
            "acquired_at": float(row[3] or 0),
            "heartbeat_at": float(row[4] or 0),
            "expires_at": float(row[5] or 0),
            "generation": int(row[6] or 0),
        }
    finally:
        conn.close()


def acquire_executor_lease(
    owner_id: str,
    *,
    owner_kind: str,
    profile_id: str = "default",
    lease_seconds: float = 30.0,
    now: float | None = None,
) -> dict[str, Any]:
    """Atomically acquire one execution lease for this SQLite profile."""
    owner_id = str(owner_id or "").strip()
    if not owner_id:
        raise ValueError("owner_id is required")
    owner_kind = str(owner_kind or "executor").strip()[:64]
    profile_id = str(profile_id or "default")
    current_time = float(time.time() if now is None else now)
    ttl = max(5.0, min(300.0, float(lease_seconds)))
    expires_at = current_time + ttl
    recovered = 0
    previous_owner = ""
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_id, owner_kind, acquired_at, expires_at, generation "
                "FROM executor_leases WHERE profile_id=?",
                (profile_id,),
            ).fetchone()
            if (
                row is not None
                and str(row[0]) != owner_id
                and float(row[3] or 0) > current_time
            ):
                conn.rollback()
                remaining = max(0.0, float(row[3]) - current_time)
                return {
                    "acquired": False,
                    "profile_id": profile_id,
                    "owner_id": owner_id,
                    "owner_kind": owner_kind,
                    "current_owner": str(row[0]),
                    "current_owner_kind": str(row[1]),
                    "expires_at": float(row[3]),
                    "retry_after_seconds": remaining,
                    "recovered": 0,
                    "message": (
                        f"Queue execution is already owned by {str(row[1]) or 'another process'} "
                        f"({str(row[0])}). Close it or wait {remaining:.0f}s for its lease to expire."
                    ),
                }

            if row is None:
                generation = 1
                acquired_at = current_time
                conn.execute(
                    "INSERT INTO executor_leases "
                    "(profile_id, owner_id, owner_kind, acquired_at, heartbeat_at, "
                    "expires_at, generation) VALUES (?,?,?,?,?,?,?)",
                    (
                        profile_id, owner_id, owner_kind, acquired_at,
                        current_time, expires_at, generation,
                    ),
                )
            else:
                previous_owner = str(row[0] or "")
                same_owner = previous_owner == owner_id
                generation = int(row[4] or 0) + (0 if same_owner else 1)
                acquired_at = float(row[2] or current_time) if same_owner else current_time
                if previous_owner and not same_owner:
                    result = conn.execute(
                        "UPDATE download_queue SET status='queued', "
                        "execution_owner='', updated_at=?, revision=revision+1 "
                        "WHERE execution_owner=? AND status IN "
                        "('fetching','downloading','finalizing','running','cancelling')",
                        (_utc_now_iso(), previous_owner),
                    )
                    recovered = int(result.rowcount)
                    conn.execute(
                        "UPDATE download_queue SET execution_owner='', "
                        "revision=revision+1 WHERE execution_owner=?",
                        (previous_owner,),
                    )
                conn.execute(
                    "UPDATE executor_leases SET owner_id=?, owner_kind=?, "
                    "acquired_at=?, heartbeat_at=?, expires_at=?, generation=? "
                    "WHERE profile_id=?",
                    (
                        owner_id, owner_kind, acquired_at, current_time,
                        expires_at, generation, profile_id,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    return {
        "acquired": True,
        "profile_id": profile_id,
        "owner_id": owner_id,
        "owner_kind": owner_kind,
        "previous_owner": previous_owner,
        "expires_at": expires_at,
        "retry_after_seconds": 0.0,
        "generation": generation,
        "recovered": recovered,
        "message": "",
    }


def heartbeat_executor_lease(
    owner_id: str,
    *,
    profile_id: str = "default",
    lease_seconds: float = 30.0,
    now: float | None = None,
) -> bool:
    """Renew a lease only while the caller remains its owner."""
    current_time = float(time.time() if now is None else now)
    ttl = max(5.0, min(300.0, float(lease_seconds)))
    with _write_lock:
        conn = _connect()
        try:
            result = conn.execute(
                "UPDATE executor_leases SET heartbeat_at=?, expires_at=? "
                "WHERE profile_id=? AND owner_id=?",
                (
                    current_time, current_time + ttl,
                    str(profile_id or "default"), str(owner_id),
                ),
            )
            conn.commit()
            return result.rowcount == 1
        finally:
            conn.close()


def release_executor_lease(
    owner_id: str,
    *,
    profile_id: str = "default",
) -> int:
    """Release the caller's lease and requeue only its unfinished rows."""
    owner_id = str(owner_id)
    profile_id = str(profile_id or "default")
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                "SELECT owner_id FROM executor_leases WHERE profile_id=?",
                (profile_id,),
            ).fetchone()
            if lease is None or str(lease[0]) != owner_id:
                conn.rollback()
                return 0
            result = conn.execute(
                "UPDATE download_queue SET status='queued', execution_owner='', "
                "updated_at=?, revision=revision+1 WHERE execution_owner=? "
                "AND status IN "
                "('fetching','downloading','finalizing','running','cancelling')",
                (_utc_now_iso(), owner_id),
            )
            recovered = int(result.rowcount)
            conn.execute(
                "UPDATE download_queue SET execution_owner='', revision=revision+1 "
                "WHERE execution_owner=?",
                (owner_id,),
            )
            conn.execute(
                "DELETE FROM executor_leases WHERE profile_id=? AND owner_id=?",
                (profile_id, owner_id),
            )
            conn.commit()
            return recovered
        finally:
            conn.close()


def claim_queue_job(
    job_id: str,
    owner_id: str,
    *,
    status: str = "fetching",
    profile_id: str = "default",
    now: float | None = None,
    **changes: Any,
) -> dict[str, Any] | None:
    """Claim one queued row if the caller holds the unexpired profile lease."""
    job_id = str(job_id)
    owner_id = str(owner_id)
    current_time = float(time.time() if now is None else now)
    changed = dict(changes)
    changed["status"] = str(status or "fetching")
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                "SELECT owner_id, expires_at FROM executor_leases WHERE profile_id=?",
                (str(profile_id or "default"),),
            ).fetchone()
            if (
                lease is None
                or str(lease[0]) != owner_id
                or float(lease[1] or 0) <= current_time
            ):
                conn.rollback()
                return None
            row = conn.execute(
                "SELECT data, revision FROM download_queue "
                "WHERE job_id=? AND status='queued' "
                "AND (execution_owner='' OR execution_owner=?)",
                (job_id, owner_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            try:
                data = json.loads(row[0]) if row[0] else {}
            except (json.JSONDecodeError, TypeError):
                data = {}
            data.update(changed)
            data["job_id"] = job_id
            result = conn.execute(
                "UPDATE download_queue SET status=?, execution_owner=?, "
                "updated_at=?, data=?, revision=revision+1 "
                "WHERE job_id=? AND status='queued' "
                "AND (execution_owner='' OR execution_owner=?) AND revision=?",
                (
                    changed["status"], owner_id, _utc_now_iso(),
                    json.dumps(data, ensure_ascii=False), job_id, owner_id,
                    int(row[1] or 0),
                ),
            )
            if result.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
        finally:
            conn.close()
    return load_queue_job(job_id)


def transition_owned_queue_job(
    job_id: str,
    owner_id: str,
    *,
    expected_statuses: str | tuple[str, ...] | list[str] | set[str],
    status: str | None = None,
    **changes: Any,
) -> dict[str, Any] | None:
    """Compare-and-swap one row while it remains owned by ``owner_id``."""
    if isinstance(expected_statuses, str):
        expected = (expected_statuses,)
    else:
        expected = tuple(str(value) for value in expected_statuses)
    if not expected:
        raise ValueError("expected_statuses must not be empty")
    job_id = str(job_id)
    owner_id = str(owner_id)
    placeholders = ",".join("?" for _ in expected)
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT status, data, revision FROM download_queue WHERE job_id=? "
                f"AND execution_owner=? AND status IN ({placeholders})",
                (job_id, owner_id, *expected),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            current_status = str(row[0])
            target_status = str(status or current_status)
            try:
                data = json.loads(row[1]) if row[1] else {}
            except (json.JSONDecodeError, TypeError):
                data = {}
            changed = dict(changes)
            changed["status"] = target_status
            data.update(changed)
            data["job_id"] = job_id
            typed = {
                "url", "title", "platform", "quality", "recurrence", "failure_id",
            }
            assignments = [
                "status=?", "updated_at=?", "data=?", "revision=revision+1",
            ]
            values: list[Any] = [
                target_status, _utc_now_iso(),
                json.dumps(data, ensure_ascii=False),
            ]
            for name in sorted(typed.intersection(changes)):
                assignments.append(f"{name}=?")
                value = changes[name]
                values.append(int(value or 0) if name == "failure_id" else str(value or ""))
            active = {
                "fetching", "downloading", "finalizing", "running", "cancelling",
            }
            if target_status not in active:
                assignments.append("execution_owner=''")
            values.extend([job_id, owner_id, *expected])
            values.append(int(row[2] or 0))
            result = conn.execute(
                f"UPDATE download_queue SET {', '.join(assignments)} "
                f"WHERE job_id=? AND execution_owner=? "
                f"AND status IN ({placeholders}) AND revision=?",
                values,
            )
            if result.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
        finally:
            conn.close()
    return load_queue_job(job_id)


def recover_interrupted_queue_jobs() -> int:
    """Recover invalid legacy rows that have no execution owner.

    Live owner recovery is performed only by lease takeover or clean release.
    """
    with _write_lock:
        db = _connect()
        try:
            result = db.execute(
                "UPDATE download_queue SET status='queued', updated_at=?, "
                "revision=revision+1 WHERE execution_owner='' AND status IN "
                "('fetching', 'downloading', 'finalizing', 'running', 'cancelling')",
                (_utc_now_iso(),),
            )
            db.commit()
            return int(result.rowcount)
        finally:
            db.close()


def _queue_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    try:
        extras = json.loads(row[10]) if row[10] else {}
    except (json.JSONDecodeError, TypeError):
        extras = {}
    item = dict(extras)
    item.update({
        "job_id": row[0],
        "url": row[1] or extras.get("url", ""),
        "title": row[2] or extras.get("title", ""),
        "platform": row[3] or extras.get("platform", ""),
        "quality": row[4] or extras.get("quality", ""),
        "status": row[5] or extras.get("status", "queued"),
        "recurrence": row[6] or extras.get("recurrence", ""),
        "failure_id": row[7] or extras.get("failure_id", 0),
        "created_at": row[8] or extras.get("created_at", ""),
        "updated_at": row[9] or extras.get("updated_at", ""),
        "revision": int(row[11] or 0),
        "execution_owner": row[12] or "",
    })
    return item


# ── Row conversion helpers ──────────────────────────────────────────

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def save_archive_manifest(
    history_id: int,
    recording_path: str,
    manifest: dict[str, Any],
    *,
    status: str = "created",
    details: str = "",
) -> None:
    """Insert or replace the archive integrity manifest for a history row."""
    if not history_id or not isinstance(manifest, dict):
        return
    now = _utc_now_iso()
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    with _write_lock:
        db = _connect()
        try:
            db.execute("""
                INSERT INTO archive_manifests
                    (history_id, recording_path, manifest_json, created_at,
                     updated_at, status, last_check_at, last_check_details)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(history_id) DO UPDATE SET
                    recording_path=excluded.recording_path,
                    manifest_json=excluded.manifest_json,
                    updated_at=excluded.updated_at,
                    status=excluded.status,
                    last_check_at=excluded.last_check_at,
                    last_check_details=excluded.last_check_details
            """, (
                int(history_id),
                str(recording_path or ""),
                payload,
                str(manifest.get("created_at", now) or now),
                now,
                str(status or ""),
                now,
                str(details or ""),
            ))
            db.commit()
        finally:
            db.close()


def load_archive_manifest(history_id: int) -> dict[str, Any] | None:
    """Load the archive manifest row for a history id."""
    if not history_id:
        return None
    db = _connect(readonly=True)
    try:
        row = db.execute(
            "SELECT * FROM archive_manifests WHERE history_id=?",
            (int(history_id),),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["manifest"] = json.loads(data.get("manifest_json", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            data["manifest"] = {}
        return data
    finally:
        db.close()


def update_archive_manifest_check(history_id: int, status: str, details: str) -> None:
    """Persist the latest verification status for a manifest."""
    if not history_id:
        return
    with _write_lock:
        db = _connect()
        try:
            db.execute("""
                UPDATE archive_manifests
                   SET status=?, last_check_at=?, last_check_details=?
                 WHERE history_id=?
            """, (str(status or ""), _utc_now_iso(), str(details or ""), int(history_id)))
            db.commit()
        finally:
            db.close()


def archive_manifest_count() -> int:
    """Return total archive manifest rows."""
    db = _connect(readonly=True)
    try:
        return db.execute("SELECT COUNT(*) FROM archive_manifests").fetchone()[0]
    finally:
        db.close()


def save_failed_job(
    *,
    url: str,
    platform: str = "",
    title: str = "",
    stage: str,
    error: str,
    output_dir: str = "",
    resume_sidecar: str = "",
    queue_data: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    status: str = "",
    auto_retry: bool | None = None,
    now: float | None = None,
) -> int:
    """Insert or update a classified failed-job ledger row.

    Active rows are deduplicated by URL, stage, and output directory so a
    flapping network failure does not flood the recovery list.
    """
    from .retry import (
        CIRCUIT_FAILURE_THRESHOLD,
        CIRCUIT_OPEN_SECONDS,
        CIRCUIT_WINDOW_SECONDS,
        classify_failure,
        retry_delay_seconds,
        retry_source,
        utc_iso,
    )

    url = str(url or "").strip()
    stage = str(stage or "").strip() or "unknown"
    if not url and not output_dir:
        return 0
    current_time = float(time.time() if now is None else now)
    now_iso = utc_iso(current_time)
    queue_dict = dict(queue_data or {})
    queue_payload = json.dumps(queue_dict, ensure_ascii=False, sort_keys=True)
    context_payload = json.dumps(context or {}, ensure_ascii=False, sort_keys=True)
    decision = classify_failure(error, now=current_time)
    source_key, source_label = retry_source(
        url,
        platform,
        queue_dict.get("source_id", ""),
    )
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT id, retry_count, auto_retry
                  FROM failed_jobs
                 WHERE url=? AND stage=? AND output_dir=?
                   AND status IN ('retryable', 'retrying', 'intervention')
                 ORDER BY id DESC
                 LIMIT 1
            """, (url, stage, str(output_dir or ""))).fetchone()
            retry_count = int(row["retry_count"] or 0) if row else 0
            same_failure_retry = bool(
                row
                and int(queue_dict.get("failure_id", 0) or 0)
                == int(row["id"])
            )
            wants_auto_retry = (
                bool(auto_retry)
                if auto_retry is not None
                else bool(row["auto_retry"]) if same_failure_retry else True
            )
            effective_auto_retry = bool(decision.retryable and wants_auto_retry)
            effective_status = (
                "retryable" if effective_auto_retry else "intervention"
            )
            requested_status = str(status or "").strip()
            if requested_status in {"discarded", "resolved"}:
                effective_status = requested_status
                effective_auto_retry = False

            circuit = conn.execute(
                "SELECT failure_count, window_started_at, opened_until "
                "FROM retry_circuits WHERE source_key=?",
                (source_key,),
            ).fetchone()
            opened_until = 0.0
            if decision.retryable:
                if (
                    circuit is None
                    or current_time - float(circuit["window_started_at"] or 0)
                    > CIRCUIT_WINDOW_SECONDS
                ):
                    failure_count = 1
                    window_started_at = current_time
                else:
                    failure_count = int(circuit["failure_count"] or 0) + 1
                    window_started_at = float(
                        circuit["window_started_at"] or current_time
                    )
                opened_until = (
                    float(circuit["opened_until"] or 0) if circuit else 0.0
                )
                if failure_count >= CIRCUIT_FAILURE_THRESHOLD:
                    opened_until = max(
                        opened_until,
                        current_time + CIRCUIT_OPEN_SECONDS,
                    )
                conn.execute("""
                    INSERT INTO retry_circuits
                        (source_key, source_label, failure_count,
                         window_started_at, opened_until, last_category,
                         last_reason, updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_key) DO UPDATE SET
                        source_label=excluded.source_label,
                        failure_count=excluded.failure_count,
                        window_started_at=excluded.window_started_at,
                        opened_until=excluded.opened_until,
                        last_category=excluded.last_category,
                        last_reason=excluded.last_reason,
                        updated_at=excluded.updated_at
                """, (
                    source_key,
                    source_label,
                    failure_count,
                    window_started_at,
                    opened_until,
                    decision.category,
                    decision.reason,
                    now_iso,
                ))

            next_attempt_at = ""
            if effective_auto_retry:
                delay = retry_delay_seconds(
                    retry_count + 1,
                    source_key,
                    retry_after_seconds=decision.retry_after_seconds,
                )
                next_attempt_at = utc_iso(
                    max(current_time + delay, opened_until)
                )
            if row:
                job_id = int(row["id"])
                conn.execute("""
                    UPDATE failed_jobs
                       SET platform=?, title=?, error=?, resume_sidecar=?,
                           status=?, queue_data=?, context_json=?,
                           updated_at=?, category=?, retryable=?,
                           next_attempt_at=?, retry_after_seconds=?,
                           last_reason=?, source_key=?, source_label=?,
                           auto_retry=?
                     WHERE id=?
                """, (
                    str(platform or ""),
                    str(title or ""),
                    decision.reason,
                    str(resume_sidecar or ""),
                    effective_status,
                    queue_payload,
                    context_payload,
                    now_iso,
                    decision.category,
                    int(decision.retryable),
                    next_attempt_at,
                    decision.retry_after_seconds,
                    decision.reason,
                    source_key,
                    source_label,
                    int(effective_auto_retry),
                    job_id,
                ))
                conn.commit()
                return job_id
            cur = conn.execute("""
                INSERT INTO failed_jobs
                    (url, platform, title, stage, error, output_dir,
                     resume_sidecar, retry_count, status, queue_data,
                     context_json, created_at, updated_at, last_retry_at,
                     category, retryable, next_attempt_at,
                     retry_after_seconds, last_reason, source_key,
                     source_label, auto_retry)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                url,
                str(platform or ""),
                str(title or ""),
                stage,
                decision.reason,
                str(output_dir or ""),
                str(resume_sidecar or ""),
                0,
                effective_status,
                queue_payload,
                context_payload,
                now_iso,
                now_iso,
                "",
                decision.category,
                int(decision.retryable),
                next_attempt_at,
                decision.retry_after_seconds,
                decision.reason,
                source_key,
                source_label,
                int(effective_auto_retry),
            ))
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()


def load_failed_jobs(
    *,
    statuses: tuple[str, ...] = ("retryable", "retrying", "intervention"),
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return failed jobs ordered newest-first."""
    status_values = tuple(str(s) for s in statuses if str(s))
    if not status_values:
        return []
    placeholders = ",".join("?" for _ in status_values)
    db = _connect(readonly=True)
    try:
        rows = db.execute(
            f"""
            SELECT *
              FROM failed_jobs
             WHERE status IN ({placeholders})
             ORDER BY updated_at DESC, id DESC
             LIMIT ?
            """,
            (*status_values, max(1, int(limit or 50))),
        ).fetchall()
        return [_row_to_failed_job_dict(r) for r in rows]
    finally:
        db.close()


def load_failed_job(job_id: int) -> dict[str, Any] | None:
    """Load one failed job by id."""
    if not job_id:
        return None
    db = _connect(readonly=True)
    try:
        row = db.execute(
            "SELECT * FROM failed_jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()
        return _row_to_failed_job_dict(row) if row else None
    finally:
        db.close()


def mark_failed_job_retrying(job_id: int) -> dict[str, Any] | None:
    """Increment retry count and mark a failed job as being retried."""
    if not job_id:
        return None
    now = _utc_now_iso()
    with _write_lock:
        db = _connect()
        try:
            result = db.execute("""
                UPDATE failed_jobs
                   SET status='retrying',
                       retry_count=retry_count + 1,
                       last_retry_at=?,
                       next_attempt_at='',
                       updated_at=?
                 WHERE id=? AND status NOT IN ('resolved','discarded')
            """, (now, now, int(job_id)))
            db.commit()
        finally:
            db.close()
    return load_failed_job(job_id) if result.rowcount == 1 else None


def mark_failed_job_discarded(job_id: int) -> None:
    """Hide a failed job from active recovery lists without deleting it."""
    if not job_id:
        return
    with _write_lock:
        db = _connect()
        try:
            db.execute("""
                UPDATE failed_jobs SET status='discarded', auto_retry=0,
                       next_attempt_at='', updated_at=?
                 WHERE id=?
            """, (_utc_now_iso(), int(job_id)))
            db.commit()
        finally:
            db.close()


def mark_failed_job_resolved(job_id: int) -> None:
    """Mark a failed job resolved after a successful retry."""
    if not job_id:
        return
    with _write_lock:
        db = _connect()
        try:
            row = db.execute(
                "SELECT source_key FROM failed_jobs WHERE id=?",
                (int(job_id),),
            ).fetchone()
            db.execute("""
                UPDATE failed_jobs SET status='resolved', auto_retry=0,
                       next_attempt_at='', updated_at=?
                 WHERE id=?
            """, (_utc_now_iso(), int(job_id)))
            if row and row["source_key"]:
                db.execute(
                    "DELETE FROM retry_circuits WHERE source_key=?",
                    (str(row["source_key"]),),
                )
            db.commit()
        finally:
            db.close()


def mark_failed_jobs_resolved_for_url(url: str) -> None:
    """Resolve active failure rows for a source URL."""
    url = str(url or "").strip()
    if not url:
        return
    with _write_lock:
        db = _connect()
        try:
            source_rows = db.execute(
                "SELECT DISTINCT source_key FROM failed_jobs "
                "WHERE url=? AND source_key<>''",
                (url,),
            ).fetchall()
            db.execute("""
                UPDATE failed_jobs
                   SET status='resolved', auto_retry=0,
                       next_attempt_at='', updated_at=?
                 WHERE url=?
                   AND status IN ('retryable', 'retrying', 'intervention')
            """, (_utc_now_iso(), url))
            for row in source_rows:
                db.execute(
                    "DELETE FROM retry_circuits WHERE source_key=?",
                    (str(row["source_key"]),),
                )
            db.commit()
        finally:
            db.close()


def load_due_failed_jobs(
    *,
    now: float | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return automatically retryable failures whose durable delay elapsed."""
    from .retry import utc_iso

    due_at = utc_iso(time.time() if now is None else float(now))
    db = _connect(readonly=True)
    try:
        rows = db.execute("""
            SELECT *
              FROM failed_jobs
             WHERE status='retryable' AND retryable=1 AND auto_retry=1
               AND next_attempt_at<>'' AND next_attempt_at<=?
             ORDER BY next_attempt_at ASC, id ASC
             LIMIT ?
        """, (due_at, max(1, int(limit or 50)))).fetchall()
        return [_row_to_failed_job_dict(row) for row in rows]
    finally:
        db.close()


def promote_failed_job_retry(
    job_id: int,
    *,
    automatic: bool = False,
    owner_id: str = "",
    now: float | None = None,
) -> dict[str, Any] | None:
    """Atomically claim a failure and return it to the durable queue."""
    from .retry import iso_timestamp, utc_iso

    failure_id = int(job_id or 0)
    if failure_id <= 0:
        return None
    current_time = float(time.time() if now is None else now)
    now_iso = utc_iso(current_time)
    queued_job_id = ""
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            failure = conn.execute(
                "SELECT * FROM failed_jobs WHERE id=?",
                (failure_id,),
            ).fetchone()
            if failure is None or str(failure["status"]) in {
                "resolved", "discarded",
            }:
                conn.rollback()
                return None
            if automatic:
                lease = conn.execute(
                    "SELECT owner_id, expires_at FROM executor_leases "
                    "WHERE profile_id='default'"
                ).fetchone()
                if (
                    not owner_id
                    or lease is None
                    or str(lease["owner_id"]) != str(owner_id)
                    or float(lease["expires_at"] or 0) <= current_time
                    or str(failure["status"]) != "retryable"
                    or not bool(failure["retryable"])
                    or not bool(failure["auto_retry"])
                ):
                    conn.rollback()
                    return None
                due_at = iso_timestamp(failure["next_attempt_at"])
                if not due_at or due_at > current_time:
                    conn.rollback()
                    return None
                circuit = conn.execute(
                    "SELECT opened_until FROM retry_circuits WHERE source_key=?",
                    (str(failure["source_key"] or ""),),
                ).fetchone()
                opened_until = float(circuit["opened_until"] or 0) if circuit else 0
                if opened_until > current_time:
                    conn.execute(
                        "UPDATE failed_jobs SET next_attempt_at=?, updated_at=? "
                        "WHERE id=? AND status='retryable'",
                        (utc_iso(opened_until), now_iso, failure_id),
                    )
                    conn.commit()
                    return None

            try:
                data = json.loads(failure["queue_data"] or "{}")
            except (json.JSONDecodeError, TypeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.update({
                "url": str(data.get("url") or failure["url"] or ""),
                "title": str(data.get("title") or failure["title"] or ""),
                "platform": str(data.get("platform") or failure["platform"] or ""),
                "output_dir": str(
                    data.get("output_dir") or failure["output_dir"] or ""
                ),
                "failure_id": failure_id,
                "status": "queued",
                "error": "",
            })
            if not data["url"]:
                conn.execute(
                    "UPDATE failed_jobs SET status='intervention', auto_retry=0, "
                    "next_attempt_at='', updated_at=? WHERE id=?",
                    (now_iso, failure_id),
                )
                conn.commit()
                return None

            existing = None
            candidate_job_id = str(data.get("job_id", "") or "").strip()
            if candidate_job_id:
                existing = conn.execute(
                    "SELECT job_id, status FROM download_queue WHERE job_id=?",
                    (candidate_job_id,),
                ).fetchone()
            if existing is None:
                existing = conn.execute(
                    "SELECT job_id, status FROM download_queue "
                    "WHERE failure_id=? AND status IN "
                    "('queued','fetching','downloading','finalizing','running',"
                    "'cancelling') ORDER BY id DESC LIMIT 1",
                    (failure_id,),
                ).fetchone()

            already_retrying = str(failure["status"]) == "retrying"
            if (
                already_retrying
                and existing is not None
                and str(existing["status"]) in {
                    "queued", "fetching", "downloading", "finalizing",
                    "running", "cancelling",
                }
            ):
                queued_job_id = str(existing["job_id"])
                conn.commit()
            else:
                if not already_retrying:
                    expected_status = "retryable" if automatic else str(failure["status"])
                    result = conn.execute("""
                        UPDATE failed_jobs
                           SET status='retrying',
                               retry_count=retry_count + 1,
                               last_retry_at=?, next_attempt_at='', updated_at=?
                         WHERE id=? AND status=?
                    """, (now_iso, now_iso, failure_id, expected_status))
                    if result.rowcount != 1:
                        conn.rollback()
                        return None
                    retry_count = int(failure["retry_count"] or 0) + 1
                else:
                    retry_count = int(failure["retry_count"] or 0)
                data["note"] = (
                    f"automatic retry #{retry_count}"
                    if automatic else f"retry #{retry_count}"
                )
                data.pop("execution_owner", None)
                data.pop("revision", None)

                reusable = (
                    existing is not None
                    and str(existing["status"]) in {"failed", "cancelled"}
                )
                if reusable:
                    queued_job_id = str(existing["job_id"])
                    data["job_id"] = queued_job_id
                    conn.execute("""
                        UPDATE download_queue
                           SET url=?, title=?, platform=?, quality=?,
                               status='queued', recurrence=?, failure_id=?,
                               updated_at=?, data=?, execution_owner='',
                               revision=revision+1
                         WHERE job_id=?
                    """, (
                        data["url"],
                        data["title"],
                        data["platform"],
                        str(data.get("quality", "")),
                        str(data.get("recurrence", "")),
                        failure_id,
                        now_iso,
                        json.dumps(data, ensure_ascii=False),
                        queued_job_id,
                    ))
                else:
                    queued_job_id = uuid.uuid4().hex
                    data["job_id"] = queued_job_id
                    position = int(conn.execute(
                        "SELECT COALESCE(MAX(position), -1) + 1 "
                        "FROM download_queue"
                    ).fetchone()[0])
                    conn.execute("""
                        INSERT INTO download_queue
                            (job_id, position, url, title, platform, quality,
                             status, recurrence, failure_id, created_at,
                             updated_at, data, execution_owner, revision)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        queued_job_id,
                        position,
                        data["url"],
                        data["title"],
                        data["platform"],
                        str(data.get("quality", "")),
                        "queued",
                        str(data.get("recurrence", "")),
                        failure_id,
                        now_iso,
                        now_iso,
                        json.dumps(data, ensure_ascii=False),
                        "",
                        0,
                    ))
                conn.commit()
        finally:
            conn.close()
    return load_queue_job(queued_job_id) if queued_job_id else None


def promote_due_failed_jobs(
    owner_id: str,
    *,
    now: float | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Promote due failures while the caller owns the execution lease."""
    due = load_due_failed_jobs(now=now, limit=limit)
    promoted = []
    for failure in due:
        job = promote_failed_job_retry(
            int(failure["id"]),
            automatic=True,
            owner_id=owner_id,
            now=now,
        )
        if job:
            promoted.append(job)
    return promoted


def cancel_failed_job_retry(job_id: int) -> bool:
    """Disable automatic retry and cancel an unclaimed promoted queue row."""
    failure_id = int(job_id or 0)
    if failure_id <= 0:
        return False
    now = _utc_now_iso()
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute("""
                UPDATE failed_jobs
                   SET status='intervention', auto_retry=0,
                       next_attempt_at='', updated_at=?
                 WHERE id=? AND status NOT IN ('resolved','discarded')
            """, (now, failure_id))
            if result.rowcount != 1:
                conn.rollback()
                return False
            rows = conn.execute(
                "SELECT job_id, data FROM download_queue "
                "WHERE failure_id=? AND status='queued'",
                (failure_id,),
            ).fetchall()
            for row in rows:
                try:
                    data = json.loads(row["data"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                data.update({
                    "status": "cancelled",
                    "note": "automatic retry cancelled",
                })
                conn.execute(
                    "UPDATE download_queue SET status='cancelled', updated_at=?, "
                    "data=?, revision=revision+1 WHERE job_id=? AND status='queued'",
                    (now, json.dumps(data, ensure_ascii=False), row["job_id"]),
                )
            conn.commit()
            return True
        finally:
            conn.close()


def load_retry_circuits() -> list[dict[str, Any]]:
    """Return persisted source circuit health without source URLs."""
    db = _connect(readonly=True)
    try:
        rows = db.execute(
            "SELECT source_key, source_label, failure_count, opened_until, "
            "last_category, last_reason, updated_at "
            "FROM retry_circuits ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def failed_job_public_view(row: dict[str, Any]) -> dict[str, Any]:
    """Project a failure into the credential-free operations API shape."""
    from .retry import sanitize_failure_reason

    return {
        "id": int(row.get("id", 0) or 0),
        "title": sanitize_failure_reason(row.get("title", ""), limit=300),
        "platform": sanitize_failure_reason(row.get("platform", ""), limit=120),
        "source_label": sanitize_failure_reason(
            row.get("source_label", ""), limit=120
        ),
        "stage": str(row.get("stage", "") or ""),
        "category": str(row.get("category", "unknown") or "unknown"),
        "status": str(row.get("status", "") or ""),
        "retryable": bool(row.get("retryable", False)),
        "auto_retry": bool(row.get("auto_retry", False)),
        "retry_count": int(row.get("retry_count", 0) or 0),
        "retry_after_seconds": int(row.get("retry_after_seconds", 0) or 0),
        "next_attempt_at": str(row.get("next_attempt_at", "") or ""),
        "last_retry_at": str(row.get("last_retry_at", "") or ""),
        "updated_at": str(row.get("updated_at", "") or ""),
        "last_reason": sanitize_failure_reason(
            row.get("last_reason") or row.get("error", "")
        ),
        "resume_available": bool(
            row.get("resume_available", False) or row.get("resume_sidecar", "")
        ),
    }


def _row_to_history_dict(row):
    d = dict(row)
    d["favorite"] = bool(d.get("favorite", 0))
    d["watched"] = bool(d.get("watched", 0))
    d["watch_position_secs"] = float(d.get("watch_position_secs", 0) or 0)
    try:
        d["bookmarks"] = json.loads(d.get("bookmarks", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["bookmarks"] = []
    return d


def _row_to_failed_job_dict(row):
    d = dict(row)
    d["retry_count"] = int(d.get("retry_count", 0) or 0)
    d["retry_after_seconds"] = int(d.get("retry_after_seconds", 0) or 0)
    d["retryable"] = bool(d.get("retryable", 0))
    d["auto_retry"] = bool(d.get("auto_retry", 0))
    for key in ("queue_data", "context_json"):
        try:
            d[key] = json.loads(d.get(key, "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            d[key] = {}
    return d


def _row_to_monitor_dict(row):
    d = dict(row)
    d["auto_record"] = bool(d.get("auto_record", 0))
    d["subscribe_vods"] = bool(d.get("subscribe_vods", 0))
    d["auto_upgrade"] = bool(d.get("auto_upgrade", 0))
    try:
        d["archive_ids"] = json.loads(d.get("archive_ids", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["archive_ids"] = []
    return d


# ── Maintenance and diagnostics ────────────────────────────────────


def check_integrity() -> tuple[bool, str]:
    """Run a read-only integrity check. Returns (ok, detail)."""
    if not DB_PATH.is_file():
        return False, "Database file does not exist"
    db = _connect(readonly=True)
    try:
        rows = db.execute("PRAGMA integrity_check").fetchall()
        results = [str(r[0]) for r in rows]
        ok = len(results) == 1 and results[0] == "ok"
        return ok, "\n".join(results)
    except sqlite3.Error as e:
        return False, str(e)
    finally:
        db.close()


def run_optimize() -> str:
    """Run PRAGMA optimize to update query planner statistics."""
    if not DB_PATH.is_file():
        return "Database file does not exist"
    with _write_lock:
        db = _connect()
        try:
            db.execute("PRAGMA optimize")
            return "ok"
        except sqlite3.Error as e:
            return str(e)
        finally:
            db.close()


def rebuild_history_indexes() -> tuple[bool, str]:
    """Rebuild the external-content History FTS index and planner statistics."""
    if not DB_PATH.is_file():
        return False, "Database file does not exist"
    with _write_lock:
        db = _connect()
        try:
            db.execute("INSERT INTO history_fts(history_fts) VALUES('rebuild')")
            db.execute("ANALYZE")
            db.execute("PRAGMA optimize")
            db.commit()
            return True, "History search index and planner statistics rebuilt"
        except sqlite3.Error as exc:
            db.rollback()
            return False, str(exc)
        finally:
            db.close()


def checkpoint_wal() -> tuple[bool, str]:
    """Force a WAL checkpoint (TRUNCATE mode). Returns (ok, detail)."""
    if not DB_PATH.is_file():
        return False, "Database file does not exist"
    if runtime_status()["journal_mode"] != "wal":
        return True, "Rollback journal active; no WAL checkpoint is required"
    with _write_lock:
        db = _connect()
        try:
            row = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            blocked, pages_written, pages_total = int(row[0]), int(row[1]), int(row[2])
            if blocked:
                return False, f"Checkpoint blocked (wrote {pages_written}/{pages_total} pages)"
            return True, f"Checkpoint complete ({pages_written} pages written)"
        except sqlite3.Error as e:
            return False, str(e)
        finally:
            db.close()


def vacuum_after_backup(backup_fn=None) -> tuple[bool, str]:
    """Create a backup snapshot, then VACUUM the database.

    *backup_fn* is an optional callable that receives the DB path and
    should create a safe copy (e.g. ``backup.create_backup``).  If it
    returns a falsy first element, the vacuum is aborted.

    Returns (ok, detail).
    """
    if not DB_PATH.is_file():
        return False, "Database file does not exist"
    if backup_fn is not None:
        try:
            result = backup_fn(DB_PATH)
            if isinstance(result, tuple) and not result[0]:
                return False, f"Backup failed, vacuum aborted: {result[1]}"
        except Exception as e:
            return False, f"Backup failed, vacuum aborted: {e}"
    with _write_lock:
        db = _connect()
        try:
            db.execute("VACUUM")
            return True, "Vacuum complete"
        except sqlite3.Error as e:
            return False, str(e)
        finally:
            db.close()


def db_diagnostics() -> dict[str, Any]:
    """Return a diagnostic summary of the database state."""
    result: dict[str, Any] = {
        "exists": DB_PATH.is_file(),
        "path": str(DB_PATH),
        "sqlite_runtime": runtime_status(),
    }
    if not result["exists"]:
        return result
    try:
        result["size_bytes"] = DB_PATH.stat().st_size
    except OSError:
        result["size_bytes"] = -1

    wal_path = DB_PATH.parent / (DB_PATH.name + "-wal")
    result["wal_size_bytes"] = wal_path.stat().st_size if wal_path.is_file() else 0

    db = _connect(readonly=True)
    try:
        result["schema_version"] = db.execute("PRAGMA user_version").fetchone()[0]
        result["journal_mode"] = db.execute("PRAGMA journal_mode").fetchone()[0]
        result["page_size"] = db.execute("PRAGMA page_size").fetchone()[0]
        result["page_count"] = db.execute("PRAGMA page_count").fetchone()[0]
        result["freelist_count"] = db.execute("PRAGMA freelist_count").fetchone()[0]

        counts = {}
        for table in ("history", "monitor_channels", "download_queue",
                      "archive_manifests", "failed_jobs", "retry_circuits",
                      "bandwidth_daily", "channel_polls"):
            try:
                counts[table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                counts[table] = -1
        result["row_counts"] = counts

        integrity_rows = db.execute("PRAGMA quick_check").fetchall()
        qc = [str(r[0]) for r in integrity_rows]
        result["quick_check"] = "ok" if len(qc) == 1 and qc[0] == "ok" else "\n".join(qc[:10])
    except sqlite3.Error as e:
        result["error"] = str(e)
    finally:
        db.close()

    return result


# ── Migration from config.json ──────────────────────────────────────

def migrate_from_config(cfg: dict[str, Any]) -> bool:
    """One-time migration: move history, monitor_channels, and download_queue
    from the JSON config dict into SQLite.  Returns True if migration ran,
    False if already done or nothing to migrate.

    The caller should remove the migrated keys from cfg and re-save it
    so they don't persist in JSON.
    """
    if not any(k in cfg for k in ("history", "monitor_channels", "download_queue")):
        return False

    init_db()
    db = _connect(readonly=True)
    try:
        existing_history = db.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        existing_channels = db.execute("SELECT COUNT(*) FROM monitor_channels").fetchone()[0]
        existing_queue = db.execute("SELECT COUNT(*) FROM download_queue").fetchone()[0]
    finally:
        db.close()

    if existing_history > 0 or existing_channels > 0 or existing_queue > 0:
        # DB already has data — don't re-migrate.  Strip keys from config.
        for k in ("history", "monitor_channels", "download_queue"):
            cfg.pop(k, None)
        return False

    # Migrate history
    history = cfg.get("history", [])
    if isinstance(history, list):
        with _write_lock:
            db = _connect()
            try:
                for h in history:
                    if not isinstance(h, dict):
                        continue
                    db.execute("""
                        INSERT INTO history
                            (date, platform, source_id, title, channel, quality,
                             size, path, url, favorite, watched,
                             watch_position_secs, bookmarks)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        str(h.get("date", "")),
                        str(h.get("platform", "")),
                        str(h.get("source_id", "")),
                        str(h.get("title", "")),
                        str(h.get("channel", "")),
                        str(h.get("quality", "")),
                        str(h.get("size", "")),
                        str(h.get("path", "")),
                        str(h.get("url", "")),
                        int(bool(h.get("favorite", False))),
                        int(bool(h.get("watched", False))),
                        float(h.get("watch_position_secs", 0) or 0),
                        json.dumps(h.get("bookmarks", []) or []),
                    ))
                db.commit()
            finally:
                db.close()

    # Migrate monitor channels
    channels = cfg.get("monitor_channels", [])
    if isinstance(channels, list):
        entries = []
        for ch in channels:
            if not isinstance(ch, dict) or "url" not in ch:
                continue
            entries.append({
                "url": ch.get("url", ""),
                "platform": ch.get("platform", ""),
                "channel_id": ch.get("channel_id", ""),
                "interval_secs": ch.get("interval", 120),
                "auto_record": ch.get("auto_record", False),
                "subscribe_vods": ch.get("subscribe_vods", False),
                "archive_ids": ch.get("archive_ids", []),
                "override_output_dir": ch.get("override_output_dir", ""),
                "override_quality_pref": ch.get("override_quality_pref", ""),
                "override_filename_template": ch.get("override_filename_template", ""),
                "schedule_start_hhmm": ch.get("schedule_start_hhmm", ""),
                "schedule_end_hhmm": ch.get("schedule_end_hhmm", ""),
                "schedule_days_mask": ch.get("schedule_days_mask", 0),
                "retention_keep_last": ch.get("retention_keep_last", 0),
                "filter_keywords": ch.get("filter_keywords", ""),
                "override_pp_preset": ch.get("override_pp_preset", ""),
                "ytdlp_template_name": ch.get("ytdlp_template_name", ""),
                "auto_upgrade": ch.get("auto_upgrade", False),
                "min_upgrade_quality": ch.get("min_upgrade_quality", ""),
            })
        if entries:
            save_all_monitor_channels(entries)

    # Migrate download queue
    queue = cfg.get("download_queue", [])
    if isinstance(queue, list) and queue:
        save_queue(queue)

    # Strip migrated keys so they don't persist in JSON
    for k in ("history", "monitor_channels", "download_queue"):
        cfg.pop(k, None)

    return True
