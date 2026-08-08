"""Download-queue and executor-lease table family (V163).

Owns the ``queue_jobs`` rows and the executor lease that decides which process
is allowed to run them, including claim/transition/recovery for jobs interrupted
by a crash. Writes serialise behind the shared ``_write_lock`` from
``primitives`` -- the same object every other family uses.

Nothing here redefines a projection or a connection: rows are shaped by
``projections`` and handles come from ``connection``. The tombstone lookup is
imported from ``tombstones`` so a user deletion still suppresses a re-queue.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .connection import _connect
from .primitives import _utc_now_iso, _write_lock
from .projections import (
    _canonical_tombstone_fields,
    _queue_row_to_dict,
    _tombstone_skip_data,
)
from .tombstones import TOMBSTONE_BLOCKING_REASONS, _find_tombstone_in_connection


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

def skip_tombstoned_queue_jobs(
    *, statuses=("queued", "failed", "retrying"),
) -> list[dict[str, Any]]:
    """Cancel queued work whose canonical identity is user-tombstoned."""
    statuses = tuple(str(status) for status in statuses if str(status))
    if not statuses:
        return []
    placeholders = ",".join("?" for _ in statuses)
    changed_ids: list[str] = []
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT job_id, url, title, platform, quality, status, recurrence, "
                "failure_id, created_at, updated_at, data, revision, execution_owner "
                "FROM download_queue WHERE status IN (" + placeholders + ") "
                "ORDER BY position ASC",
                statuses,
            ).fetchall()
            now = _utc_now_iso()
            for row in rows:
                item = _queue_row_to_dict(row)
                tombstone = _find_tombstone_in_connection(
                    conn,
                    _canonical_tombstone_fields(item),
                    reasons=TOMBSTONE_BLOCKING_REASONS,
                )
                if tombstone is None:
                    continue
                data = _tombstone_skip_data(item, tombstone)
                conn.execute(
                    "UPDATE download_queue SET status='cancelled', "
                    "execution_owner='', updated_at=?, data=?, revision=revision+1 "
                    "WHERE job_id=?",
                    (
                        now, json.dumps(data, ensure_ascii=False),
                        str(item.get("job_id", "")),
                    ),
                )
                changed_ids.append(str(item.get("job_id", "")))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return [
        job for job_id in changed_ids
        if (job := load_queue_job(job_id)) is not None
    ]

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
            tombstone = _find_tombstone_in_connection(
                db,
                _canonical_tombstone_fields(data),
                reasons=TOMBSTONE_BLOCKING_REASONS,
            )
            if tombstone is not None:
                data = _tombstone_skip_data(data, tombstone)
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
            tombstone = _find_tombstone_in_connection(
                conn,
                _canonical_tombstone_fields(data),
                reasons=TOMBSTONE_BLOCKING_REASONS,
            )
            if tombstone is not None:
                skipped = _tombstone_skip_data(data, tombstone)
                conn.execute(
                    "UPDATE download_queue SET status='cancelled', "
                    "execution_owner='', updated_at=?, data=?, revision=revision+1 "
                    "WHERE job_id=? AND status='queued' AND revision=?",
                    (
                        _utc_now_iso(), json.dumps(skipped, ensure_ascii=False),
                        job_id, int(row[1] or 0),
                    ),
                )
                conn.commit()
                return None
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
