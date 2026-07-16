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
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .paths import CONFIG_DIR
from .sqlite_runtime import connect as sqlite_connect
from .sqlite_runtime import runtime_status

DB_PATH = CONFIG_DIR / "library.db"
SCHEMA_VERSION = 6

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
            _apply_schema(db)
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
        CREATE INDEX IF NOT EXISTS idx_history_channel  ON history(channel);
        CREATE INDEX IF NOT EXISTS idx_history_date     ON history(date);
        CREATE INDEX IF NOT EXISTS idx_history_url      ON history(url);

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
            data        TEXT    NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_queue_pos ON download_queue(position);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_job_id
            ON download_queue(job_id) WHERE job_id <> '';

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
            last_retry_at  TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_failed_jobs_status
            ON failed_jobs(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_failed_jobs_url
            ON failed_jobs(url);
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


def save_history_entry(entry_dict: dict[str, Any]) -> int | None:
    """Insert a single history entry. Returns the new row id."""
    with _write_lock:
        db = _connect()
        try:
            cur = db.execute("""
                INSERT INTO history
                    (date, platform, title, channel, quality, size, path, url,
                     favorite, watched, watch_position_secs, bookmarks)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(entry_dict.get("date", "")),
                str(entry_dict.get("platform", "")),
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
            db.commit()
            return cur.lastrowid
        finally:
            db.close()


def update_history_entry(entry_id: int, fields: dict[str, Any]) -> None:
    """Update specific fields on a history row by id.

    *fields* is a dict of column_name -> value.  Only known columns
    are applied (unknown keys are silently ignored).
    """
    allowed = {
        "date", "platform", "title", "channel", "quality", "size",
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
            "failure_id, created_at, updated_at, data "
            "FROM download_queue ORDER BY position ASC"
        ).fetchall()
        return [_queue_row_to_dict(r) for r in rows]
    finally:
        db.close()


def save_queue(items: list[dict[str, Any]]) -> None:
    """Replace the entire queue atomically.  Each item is a dict."""
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
            "failure_id, created_at, updated_at, data "
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
            "failure_id, created_at, updated_at, data "
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


def update_queue_job(job_id: str, **changes: Any) -> dict[str, Any] | None:
    """Atomically merge fields into one durable queue job."""
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
                "SELECT data FROM download_queue WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            try:
                data = json.loads(row[0]) if row[0] else {}
            except (json.JSONDecodeError, TypeError):
                data = {}
            data.update(changes)
            data["job_id"] = job_id
            assignments = ["updated_at = ?", "data = ?"]
            values: list[Any] = [now, json.dumps(data, ensure_ascii=False)]
            for name in sorted(typed.intersection(changes)):
                assignments.append(f"{name} = ?")
                value = changes[name]
                if name == "failure_id":
                    value = int(value or 0)
                else:
                    value = str(value or "")
                values.append(value)
            values.append(job_id)
            db.execute(
                f"UPDATE download_queue SET {', '.join(assignments)} "
                "WHERE job_id = ?",
                values,
            )
            db.commit()
        finally:
            db.close()
    return load_queue_job(job_id)


def cancel_queue_job(job_id: str) -> dict[str, Any] | None:
    """Persist cancellation unless a job is already terminal."""
    job = load_queue_job(job_id)
    if job is None or job.get("status") in {"done", "failed", "cancelled"}:
        return job
    return update_queue_job(job_id, status="cancelled", cancelled_at=_utc_now_iso())


def recover_interrupted_queue_jobs() -> int:
    """Return service-interrupted jobs to the runnable queue on startup."""
    with _write_lock:
        db = _connect()
        try:
            result = db.execute(
                "UPDATE download_queue SET status = 'queued', updated_at = ? "
                "WHERE status IN "
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
    status: str = "retryable",
) -> int:
    """Insert or update a retryable failed-job ledger row.

    Active rows are deduplicated by URL, stage, and output directory so a
    flapping network failure does not flood the recovery list.
    """
    url = str(url or "").strip()
    stage = str(stage or "").strip() or "unknown"
    if not url and not output_dir:
        return 0
    now = _utc_now_iso()
    queue_payload = json.dumps(queue_data or {}, ensure_ascii=False, sort_keys=True)
    context_payload = json.dumps(context or {}, ensure_ascii=False, sort_keys=True)
    with _write_lock:
        db = _connect()
        try:
            row = db.execute("""
                SELECT id, retry_count
                  FROM failed_jobs
                 WHERE url=? AND stage=? AND output_dir=?
                   AND status IN ('retryable', 'retrying')
                 ORDER BY id DESC
                 LIMIT 1
            """, (url, stage, str(output_dir or ""))).fetchone()
            if row:
                job_id = int(row["id"])
                db.execute("""
                    UPDATE failed_jobs
                       SET platform=?, title=?, error=?, resume_sidecar=?,
                           status=?, queue_data=?, context_json=?,
                           updated_at=?
                     WHERE id=?
                """, (
                    str(platform or ""),
                    str(title or ""),
                    str(error or ""),
                    str(resume_sidecar or ""),
                    str(status or "retryable"),
                    queue_payload,
                    context_payload,
                    now,
                    job_id,
                ))
                db.commit()
                return job_id
            cur = db.execute("""
                INSERT INTO failed_jobs
                    (url, platform, title, stage, error, output_dir,
                     resume_sidecar, retry_count, status, queue_data,
                     context_json, created_at, updated_at, last_retry_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                url,
                str(platform or ""),
                str(title or ""),
                stage,
                str(error or ""),
                str(output_dir or ""),
                str(resume_sidecar or ""),
                0,
                str(status or "retryable"),
                queue_payload,
                context_payload,
                now,
                now,
                "",
            ))
            db.commit()
            return int(cur.lastrowid or 0)
        finally:
            db.close()


def load_failed_jobs(
    *,
    statuses: tuple[str, ...] = ("retryable", "retrying"),
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
            db.execute("""
                UPDATE failed_jobs
                   SET status='retrying',
                       retry_count=retry_count + 1,
                       last_retry_at=?,
                       updated_at=?
                 WHERE id=?
            """, (now, now, int(job_id)))
            db.commit()
        finally:
            db.close()
    return load_failed_job(job_id)


def mark_failed_job_discarded(job_id: int) -> None:
    """Hide a failed job from active recovery lists without deleting it."""
    if not job_id:
        return
    with _write_lock:
        db = _connect()
        try:
            db.execute("""
                UPDATE failed_jobs
                   SET status='discarded', updated_at=?
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
            db.execute("""
                UPDATE failed_jobs
                   SET status='resolved', updated_at=?
                 WHERE id=?
            """, (_utc_now_iso(), int(job_id)))
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
            db.execute("""
                UPDATE failed_jobs
                   SET status='resolved', updated_at=?
                 WHERE url=?
                   AND status IN ('retryable', 'retrying')
            """, (_utc_now_iso(), url))
            db.commit()
        finally:
            db.close()


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
                       "archive_manifests", "failed_jobs"):
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
                            (date, platform, title, channel, quality, size,
                             path, url, favorite, watched,
                             watch_position_secs, bookmarks)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        str(h.get("date", "")),
                        str(h.get("platform", "")),
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
