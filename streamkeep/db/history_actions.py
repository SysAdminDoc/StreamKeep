"""The history action log (V163).

Favourites, watched state, playback positions, bookmarks, and deletions are
recorded as an append-only action log so a restore or a rebuild can replay the
library's current state. This module owns how one action is shaped, identified,
and appended; the connection is always passed in by the caller.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .primitives import _sqlite_table_exists, _utc_now_iso
from .projections import _canonical_tombstone_fields
from .tombstones import (
    _normalize_tombstone_reason,
    _upsert_tombstone_in_connection,
)

HISTORY_ACTION_COMPACTION_LIMIT = 10_000



_HISTORY_ACTION_STATE_FIELDS = (
    "favorite", "watched", "watch_position_secs", "bookmarks",
)
_HISTORY_ACTION_RECORD_FIELDS = (
    "date", "platform", "source_id", "webpage_url", "title", "channel",
    "quality", "size", "path", "url", *_HISTORY_ACTION_STATE_FIELDS,
)


def _history_action_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _history_action_bookmarks(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except (json.JSONDecodeError, TypeError):
            value = []
    return value if isinstance(value, list) else []


def _history_action_record(entry: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Normalize one history row into a JSON-safe action payload."""
    if isinstance(entry, Mapping):
        source = entry
    else:
        try:
            source = dict(entry)
        except (TypeError, ValueError):
            source = {}
    nested = source.get("record")
    if isinstance(nested, Mapping):
        source = nested
    record: dict[str, Any] = {}
    for field in _HISTORY_ACTION_RECORD_FIELDS:
        value = source.get(field, "")
        if field in ("favorite", "watched"):
            record[field] = _history_action_bool(value)
        elif field == "watch_position_secs":
            try:
                record[field] = float(value or 0)
            except (TypeError, ValueError):
                record[field] = 0.0
        elif field == "bookmarks":
            record[field] = _history_action_bookmarks(value)
        else:
            record[field] = str(value or "")
    return record


def _history_action_identity_key(entry: Mapping[str, Any] | Any) -> str:
    """Return a bounded, stable identity for one history projection."""
    record = _history_action_record(entry)
    platform = record["platform"].strip().casefold()
    source_id = record["source_id"].strip()
    webpage_url = record["webpage_url"].strip()
    if source_id:
        parts = ["source", platform, source_id]
    elif webpage_url:
        parts = ["webpage", platform, webpage_url]
    else:
        # A legacy row may have no recognized source identity.  The fallback
        # remains stable across a database rebuild as long as the on-disk
        # record has the same path and display metadata.
        parts = [
            "fallback", platform, record["path"].strip().casefold(),
            record["title"], record["channel"], record["date"],
            record["quality"], record["size"],
        ]
    payload = json.dumps(
        parts, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return "v1:" + hashlib.sha256(payload).hexdigest()


def _append_history_action_in_connection(
    connection,
    history_id: int,
    action: str,
    entry: Mapping[str, Any] | Any,
    *,
    identity_key: str = "",
    reason: str = "",
    created_at: str | None = None,
) -> int | None:
    """Append a full history snapshot while the caller owns the transaction."""
    if not _sqlite_table_exists(connection, "history_actions"):
        return None
    normalized_action = str(action or "snapshot").strip().lower()
    if normalized_action not in {"snapshot", "delete"}:
        raise ValueError("history action must be snapshot or delete")
    record = _history_action_record(entry)
    payload: dict[str, Any] = dict(record)
    if reason:
        payload["reason"] = str(reason)
    key = str(identity_key or _history_action_identity_key(record))
    cursor = connection.execute(
        """
        INSERT INTO history_actions
            (history_id, identity_key, action, value_json, created_at)
        VALUES (?,?,?,?,?)
        """,
        (
            int(history_id or 0), key, normalized_action,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            str(created_at or _utc_now_iso()),
        ),
    )
    return int(cursor.lastrowid)


def _history_fts_query(query: str) -> str:
    """Build a literal prefix-token FTS query from untrusted user text."""
    tokens = re.findall(r"\w+", str(query or "").lower(), flags=re.UNICODE)
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)


def _history_select_with_upgrade(alias: str = "h") -> str:
    """Select history plus the latest per-item upgrade audit summary."""
    return (
        f"SELECT {alias}.*, "
        f"COALESCE((SELECT d.decision FROM upgrade_decisions d "
        f"WHERE d.history_id={alias}.id ORDER BY d.id DESC LIMIT 1), '') "
        "AS upgrade_decision, "
        f"COALESCE((SELECT d.reason_code FROM upgrade_decisions d "
        f"WHERE d.history_id={alias}.id ORDER BY d.id DESC LIMIT 1), '') "
        "AS upgrade_reason_code, "
        f"COALESCE((SELECT d.reason FROM upgrade_decisions d "
        f"WHERE d.history_id={alias}.id ORDER BY d.id DESC LIMIT 1), '') "
        "AS upgrade_reason, "
        f"COALESCE((SELECT d.execution_status FROM upgrade_decisions d "
        f"WHERE d.history_id={alias}.id ORDER BY d.id DESC LIMIT 1), '') "
        "AS upgrade_execution_status"
    )


def _history_action_payload(row) -> dict[str, Any]:
    item = dict(row)
    try:
        value = json.loads(item.get("value_json", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        value = {}
    item["value"] = value if isinstance(value, dict) else {}
    return item


def _history_action_count_in_connection(connection) -> int:
    if not _sqlite_table_exists(connection, "history_actions"):
        return 0
    return int(connection.execute(
        "SELECT COUNT(*) FROM history_actions"
    ).fetchone()[0] or 0)


def _delete_history_projection_rows_in_connection(connection, entry_ids):
    """Delete rows during replay without emitting another action."""
    ids = sorted({int(entry_id) for entry_id in entry_ids if int(entry_id) > 0})
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    for table in ("published_recordings", "archive_manifests"):
        if _sqlite_table_exists(connection, table):
            connection.execute(
                f"DELETE FROM {table} WHERE history_id IN ({placeholders})",
                ids,
            )
    cursor = connection.execute(
        f"DELETE FROM history WHERE id IN ({placeholders})", ids
    )
    return int(cursor.rowcount or 0)


def _compact_history_actions_in_connection(connection, max_rows: int) -> int:
    if not _sqlite_table_exists(connection, "history_actions"):
        return 0
    rows = connection.execute(
        "SELECT id, history_id, identity_key FROM history_actions "
        "ORDER BY id DESC"
    ).fetchall()
    if len(rows) <= max_rows:
        return 0
    active_keys = {
        _history_action_identity_key(dict(row))
        for row in connection.execute("SELECT * FROM history").fetchall()
    }
    keep: dict[tuple[str, int], int] = {}
    for row in rows:
        key = str(row[2] or "")
        history_id = int(row[1] or 0)
        marker = (key, history_id) if key else ("", history_id)
        keep.setdefault(marker, int(row[0]))
    keep_ids = set(keep.values())
    # Every active projection keeps its newest event even if the caller asks
    # for a cap smaller than the number of active identities.  The remainder
    # of the cap is spent on the newest deletion/audit identities.
    active_keep = {
        action_id for marker, action_id in keep.items()
        if marker[0] in active_keys
    }
    if len(keep_ids) > max_rows:
        retained = set(active_keep)
        for row in rows:
            if len(retained) >= max_rows:
                break
            action_id = int(row[0])
            if action_id in keep_ids:
                retained.add(action_id)
        keep_ids = retained
    stale = [int(row[0]) for row in rows if int(row[0]) not in keep_ids]
    if stale:
        connection.executemany(
            "DELETE FROM history_actions WHERE id=?",
            ((action_id,) for action_id in stale),
        )
    return len(stale)


def _maybe_compact_history_actions_in_connection(connection) -> int:
    count = _history_action_count_in_connection(connection)
    if count <= HISTORY_ACTION_COMPACTION_LIMIT:
        return 0
    return _compact_history_actions_in_connection(
        connection, HISTORY_ACTION_COMPACTION_LIMIT,
    )


def _replay_history_actions_in_connection(
    connection, *, seed_missing: bool = True, prefer_identity: bool = True,
) -> dict[str, int]:
    """Reconcile materialized history state from the append-only log."""
    result = {"actions": 0, "applied": 0, "deleted": 0, "seeded": 0}
    if not (
        _sqlite_table_exists(connection, "history")
        and _sqlite_table_exists(connection, "history_actions")
    ):
        return result
    action_rows = connection.execute(
        "SELECT * FROM history_actions ORDER BY id ASC"
    ).fetchall()
    result["actions"] = len(action_rows)
    latest_by_id: dict[int, dict[str, Any]] = {}
    latest_by_identity: dict[str, dict[str, Any]] = {}
    for row in action_rows:
        action = _history_action_payload(row)
        history_id = int(action.get("history_id", 0) or 0)
        identity_key = str(action.get("identity_key", "") or "")
        if history_id > 0:
            latest_by_id[history_id] = action
        if identity_key:
            latest_by_identity[identity_key] = action

    rows = connection.execute("SELECT * FROM history ORDER BY id ASC").fetchall()
    delete_ids: list[int] = []
    for row in rows:
        row_dict = dict(row)
        history_id = int(row_dict.get("id", 0) or 0)
        identity_key = _history_action_identity_key(row_dict)
        by_id = latest_by_id.get(history_id)
        if by_id and str(by_id.get("identity_key", "") or "") not in {
            "", identity_key,
        }:
            by_id = None
        by_identity = latest_by_identity.get(identity_key)
        selected = by_identity if prefer_identity else by_id
        if by_id and by_identity:
            if selected is None or int(by_id.get("id", 0)) > int(
                selected.get("id", 0)
            ):
                selected = by_id
        elif selected is None:
            selected = by_id or by_identity
        if selected is None:
            if seed_missing:
                _append_history_action_in_connection(
                    connection, history_id, "snapshot", row_dict,
                )
                result["seeded"] += 1
            continue
        if str(selected.get("action", "snapshot")) == "delete":
            delete_ids.append(history_id)
            continue
        value = selected.get("value", {})
        record = _history_action_record(value)
        connection.execute(
            "UPDATE history SET favorite=?, watched=?, "
            "watch_position_secs=?, bookmarks=? WHERE id=?",
            (
                int(record["favorite"]), int(record["watched"]),
                float(record["watch_position_secs"]),
                json.dumps(record["bookmarks"], ensure_ascii=False),
                history_id,
            ),
        )
        result["applied"] += 1
    result["deleted"] = _delete_history_projection_rows_in_connection(
        connection, delete_ids,
    )
    if seed_missing:
        _maybe_compact_history_actions_in_connection(connection)
    return result


def _delete_history_rows_in_connection(conn, entry_ids, *, reason="user") -> int:
    """Record and remove history rows while the caller owns the transaction."""
    ids = sorted({int(entry_id) for entry_id in entry_ids})
    if not ids:
        return 0
    reason = _normalize_tombstone_reason(reason)
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM history WHERE id IN ({placeholders})", ids
    ).fetchall()
    for row in rows:
        _append_history_action_in_connection(
            conn, int(row[0]), "delete", dict(row), reason=reason,
        )
        _upsert_tombstone_in_connection(
            conn,
            _canonical_tombstone_fields(dict(row)),
            reason=reason,
        )
    conn.execute(
        f"DELETE FROM published_recordings WHERE history_id IN ({placeholders})",
        ids,
    )
    conn.execute(
        f"DELETE FROM archive_manifests WHERE history_id IN ({placeholders})",
        ids,
    )
    conn.execute(
        f"DELETE FROM history WHERE id IN ({placeholders})",
        ids,
    )
    _maybe_compact_history_actions_in_connection(conn)
    return len(rows)
