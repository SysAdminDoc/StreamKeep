"""The history action log (V163).

Favourites, watched state, playback positions, bookmarks, and deletions are
recorded as an append-only action log so a restore or a rebuild can replay the
library's current state. This module owns how one action is shaped, identified,
and appended; the connection is always passed in by the caller.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .primitives import _sqlite_table_exists, _utc_now_iso


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
