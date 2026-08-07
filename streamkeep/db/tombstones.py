"""The user tombstone ledger (V163).

A tombstone records that the operator deleted something on purpose, so a later
monitor pass, backfill, or library rebuild does not helpfully download it
again. This module owns how one is normalised, written, and looked up; the
connection is always passed in by the caller.
"""

from __future__ import annotations

from typing import Any

from .primitives import _utc_now_iso

TOMBSTONE_REASONS = frozenset({"user", "retention", "lifecycle"})



def _normalize_tombstone_reason(reason):
    normalized = str(reason or "user").strip().lower()
    if normalized not in TOMBSTONE_REASONS:
        raise ValueError(
            f"tombstone reason must be one of {sorted(TOMBSTONE_REASONS)}"
        )
    return normalized


def _find_tombstone_in_connection(conn, fields, *, reasons=None):
    clauses = []
    params: list[Any] = []
    platform = str(fields.get("platform", "") or "")
    source_id = str(fields.get("source_id", "") or "")
    webpage_url = str(fields.get("webpage_url", "") or "")
    if source_id:
        if platform:
            clauses.append(
                "(platform=? COLLATE NOCASE AND source_id=?)"
            )
            params.extend((platform, source_id))
        else:
            clauses.append("source_id=?")
            params.append(source_id)
    if webpage_url:
        clauses.append("webpage_url=?")
        params.append(webpage_url)
    if not clauses:
        return None
    reason_values = tuple(
        _normalize_tombstone_reason(reason) for reason in reasons
    ) if reasons is not None else ()
    where = [f"({' OR '.join(clauses)})"]
    if reason_values:
        placeholders = ",".join("?" for _ in reason_values)
        where.append(f"reason IN ({placeholders})")
        params.extend(reason_values)
    row = conn.execute(
        "SELECT id, platform, source_id, webpage_url, deleted_at, reason, "
        "path, title, channel FROM media_tombstones "
        f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 1",
        params,
    ).fetchone()
    return dict(row) if row else None


def _upsert_tombstone_in_connection(
    conn, fields, *, reason="user", deleted_at=None,
):
    reason = _normalize_tombstone_reason(reason)
    if not fields.get("source_id") and not fields.get("webpage_url"):
        return None
    deleted_at = str(deleted_at or _utc_now_iso())
    existing = _find_tombstone_in_connection(conn, fields)
    if existing is None:
        cursor = conn.execute(
            "INSERT INTO media_tombstones "
            "(platform, source_id, webpage_url, deleted_at, reason, path, title, channel) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                fields.get("platform", ""), fields.get("source_id", ""),
                fields.get("webpage_url", ""), deleted_at, reason,
                fields.get("path", ""), fields.get("title", ""),
                fields.get("channel", ""),
            ),
        )
        return int(cursor.lastrowid)

    # A deliberate user deletion must never be downgraded by a later
    # retention/lifecycle pass for the same media identity.
    effective_reason = (
        "user" if existing.get("reason") == "user" or reason == "user"
        else reason
    )
    conn.execute(
        "UPDATE media_tombstones SET platform=?, source_id=?, webpage_url=?, "
        "deleted_at=?, reason=?, path=?, title=?, channel=? WHERE id=?",
        (
            fields.get("platform", ""), fields.get("source_id", ""),
            fields.get("webpage_url", ""), deleted_at, effective_reason,
            fields.get("path", ""), fields.get("title", ""),
            fields.get("channel", ""), int(existing["id"]),
        ),
    )
    return int(existing["id"])
