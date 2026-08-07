"""Row and view projections for the StreamKeep database (V163).

These turn a ``sqlite3.Row`` into a plain dict, and a dict into the
credential-free shape the operations API and the web remote are allowed to
see. Nothing here touches a connection, a cursor, or a lock, which is why it
belongs outside the connection-owning module rather than being re-exported
from it.

This module *implements* what it exports. ``test_architecture_boundaries``
asserts that by ``__module__``, so it cannot quietly decay back into a
forwarding shim the way the earlier "split" modules did.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any


def _circuit_engine(queue_data, context) -> str:
    """Name the engine a failed attempt actually used (V165).

    Jobs carry the engine implicitly: ``format_type`` distinguishes a yt-dlp
    run from a native FFmpeg capture, and the optional live engines announce
    themselves in the failure context. An unrecognised shape yields ``""``
    rather than a guess, because a wrong engine name would send the operator
    to the wrong switch.
    """
    queue_data = queue_data if isinstance(queue_data, dict) else {}
    context = context if isinstance(context, dict) else {}
    for source in (context, queue_data):
        declared = str(source.get("engine", "") or "").strip().casefold()
        if declared:
            return declared[:32]
    format_type = str(
        queue_data.get("format_type", "") or ""
    ).strip().casefold()
    if format_type == "ytdlp_direct":
        return "yt-dlp"
    if format_type in {"hls", "direct", "raw"}:
        return "native"
    return ""


def _history_like_filter(query: str, alias="h"):
    """Build a case-insensitive metadata filter for runtimes without FTS5."""
    tokens = re.findall(r"\w+", str(query or "").lower(), flags=re.UNICODE)
    fields = ("title", "platform", "channel", "path", "url")
    clauses = []
    params = []
    for token in tokens:
        escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(" + " OR ".join(
            f"COALESCE({alias}.{field}, '') LIKE ? ESCAPE '\\' COLLATE NOCASE"
            for field in fields
        ) + ")")
        params.extend([f"%{escaped}%"] * len(fields))
    return " AND ".join(clauses), params


def _canonical_history_entry(entry_dict: dict[str, Any]) -> dict[str, Any]:
    """Normalize history identity before it can be indexed or searched."""
    from ..metadata import build_archival_provenance
    from ..models import StreamInfo

    raw = dict(entry_dict or {})
    raw_url = str(raw.get("webpage_url", "") or raw.get("url", "") or "")
    provenance = build_archival_provenance(
        StreamInfo(
            platform=str(raw.get("platform", "") or ""),
            channel=str(raw.get("channel", "") or ""),
            source_id=str(raw.get("source_id", "") or ""),
            webpage_url=raw_url,
        ),
        source_url=raw_url,
    )
    raw["platform"] = provenance.platform or str(raw.get("platform", ""))
    raw["source_id"] = provenance.source_id
    raw["webpage_url"] = provenance.webpage_url
    raw["url"] = provenance.webpage_url or str(raw.get("url", "") or "")
    return raw


def _canonical_tombstone_fields(
    record=None,
    *,
    platform="",
    source_id="",
    webpage_url="",
    url="",
    path="",
    title="",
    channel="",
):
    """Return canonical identity and display fields for a deletion record."""
    if isinstance(record, dict):
        platform = platform or record.get("vod_platform") or record.get("platform", "")
        source_id = source_id or record.get("source_id", "")
        webpage_url = webpage_url or record.get("webpage_url", "")
        url = url or record.get("url", "")
        url = url or record.get("vod_source", "")
        path = path or record.get("path", "") or record.get("output_dir", "")
        title = title or record.get("title", "") or record.get("vod_title", "")
        channel = channel or record.get("channel", "") or record.get("vod_channel", "")
    elif record is not None:
        platform = platform or getattr(record, "vod_platform", "") or getattr(record, "platform", "")
        source_id = source_id or getattr(record, "source_id", "")
        webpage_url = webpage_url or getattr(record, "webpage_url", "")
        url = url or getattr(record, "url", "") or getattr(record, "source", "")
        path = path or getattr(record, "path", "") or getattr(record, "output_dir", "")
        title = title or getattr(record, "title", "") or getattr(record, "vod_title", "")
        channel = channel or getattr(record, "channel", "") or getattr(record, "vod_channel", "")

    from ..metadata import build_archival_provenance
    from ..models import StreamInfo

    platform = str(platform or "").strip()
    source_id = str(source_id or "").strip()
    webpage_url = str(webpage_url or "").strip()
    source_url = webpage_url or str(url or "").strip()
    provenance = build_archival_provenance(
        StreamInfo(
            platform=platform,
            channel=str(channel or "").strip(),
            source_id=source_id,
            webpage_url=webpage_url,
        ),
        source_url=source_url,
    )
    return {
        "platform": provenance.platform or platform,
        "source_id": provenance.source_id,
        "webpage_url": provenance.webpage_url,
        "path": str(path or ""),
        "title": str(title or ""),
        "channel": str(channel or ""),
    }


def _tombstone_skip_data(item, tombstone):
    data = dict(item or {})
    data.update({
        "status": "cancelled",
        "note": (
            "Skipped: media was deliberately removed; clear its tombstone "
            "before downloading again."
        ),
        "tombstone_skipped": True,
        "tombstone_id": int(tombstone.get("id", 0) or 0),
        "tombstone_reason": str(tombstone.get("reason", "user") or "user"),
    })
    return data


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


def _decode_upload_profile(row) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        config = json.loads(row["config_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    return {
        "profile_id": str(row["profile_id"] or ""),
        "label": str(row["label"] or ""),
        "adapter": str(row["adapter"] or ""),
        "config": config,
        "secret_ref": str(row["secret_ref"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _decode_upload_job(row) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    result = dict(row)
    result.pop("metadata_json", None)
    result["metadata"] = metadata
    for key in ("bytes_sent", "total_bytes", "attempts"):
        result[key] = int(result.get(key, 0) or 0)
    return result


def _decode_intelligence_profile(row) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        config = json.loads(row["config_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    return {
        "profile_id": str(row["profile_id"] or ""),
        "label": str(row["label"] or ""),
        "provider": str(row["provider"] or ""),
        "model": str(row["model"] or ""),
        "api_url": str(row["api_url"] or ""),
        "config": config,
        "secret_ref": str(row["secret_ref"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _decode_intelligence_job(row) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        result = json.loads(row["result_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        result = {}
    if not isinstance(result, dict):
        result = {}
    item = dict(row)
    item.pop("result_json", None)
    item["result"] = result
    item["history_id"] = int(item.get("history_id", 0) or 0)
    item["payload_chars"] = int(item.get("payload_chars", 0) or 0)
    item["redaction_applied"] = bool(item.get("redaction_applied", 0))
    item["cancel_requested"] = bool(item.get("cancel_requested", 0))
    item["edited"] = bool(item.get("edited", 0))
    item["progress"] = max(0.0, min(1.0, float(item.get("progress", 0) or 0)))
    return item


def failed_job_public_view(row: dict[str, Any]) -> dict[str, Any]:
    """Project a failure into the credential-free operations API shape."""
    from ..retry import failure_remediation, sanitize_failure_reason

    category = str(row.get("category", "unknown") or "unknown")
    last_reason = sanitize_failure_reason(
        row.get("last_reason") or row.get("error", "")
    )

    return {
        "id": int(row.get("id", 0) or 0),
        "title": sanitize_failure_reason(row.get("title", ""), limit=300),
        "platform": sanitize_failure_reason(row.get("platform", ""), limit=120),
        "source_label": sanitize_failure_reason(
            row.get("source_label", ""), limit=120
        ),
        "stage": str(row.get("stage", "") or ""),
        "category": category,
        # The stable machine-readable code a client can branch on (V154).
        # ``category`` remains the coarse bucket the remediation text is keyed
        # by; ``reason_code`` names the specific condition, and ``terminal``
        # says the item will never succeed no matter what is done to it.
        "reason_code": str(row.get("reason_code", "") or "unknown"),
        "terminal": bool(row.get("terminal", False)),
        "status": str(row.get("status", "") or ""),
        "retryable": bool(row.get("retryable", False)),
        "auto_retry": bool(row.get("auto_retry", False)),
        "retry_count": int(row.get("retry_count", 0) or 0),
        "retry_after_seconds": int(row.get("retry_after_seconds", 0) or 0),
        "next_attempt_at": str(row.get("next_attempt_at", "") or ""),
        "last_retry_at": str(row.get("last_retry_at", "") or ""),
        "updated_at": str(row.get("updated_at", "") or ""),
        "last_reason": last_reason,
        "remediation": failure_remediation(category, reason=last_reason),
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
    d["reason_code"] = str(d.get("reason_code", "") or "unknown")
    d["terminal"] = bool(d.get("terminal", 0))
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
    d["capture_comments"] = bool(d.get("capture_comments", 0))
    d["auto_upgrade"] = bool(d.get("auto_upgrade", 0))
    try:
        d["archive_ids"] = json.loads(d.get("archive_ids", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["archive_ids"] = []
    try:
        profile = json.loads(d.get("upgrade_profile_json", "{}") or "{}")
        d["upgrade_profile"] = profile if isinstance(profile, dict) else {}
    except (json.JSONDecodeError, TypeError):
        d["upgrade_profile"] = {}
    return d
