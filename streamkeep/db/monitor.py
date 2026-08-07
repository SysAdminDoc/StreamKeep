"""Monitor-channel table family (V163).

Owns reading, upserting and deleting the ``monitor_channels`` rows that drive
the channel monitor. Writes serialise behind the shared ``_write_lock`` from
``primitives`` -- the same object every other writer uses, which is why the lock
lives in a leaf module rather than being imported back out of ``_legacy``.

The connection is acquired through ``connection._connect`` and rows are shaped
by ``projections._row_to_monitor_dict``; neither is redefined here.
"""

from __future__ import annotations

import json
from typing import Any

from .connection import _connect
from .primitives import _write_lock
from .projections import _row_to_monitor_dict


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
                     subscribe_vods, capture_comments, archive_ids,
                     override_output_dir, override_quality_pref,
                     override_filename_template,
                     schedule_start_hhmm, schedule_end_hhmm, schedule_days_mask,
                     retention_keep_last, filter_keywords, override_pp_preset,
                     ytdlp_template_name, auto_upgrade, min_upgrade_quality,
                     upgrade_profile_json,
                     auth_profile_id, media_server_layout)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(entry_dict.get("url", "")),
                str(entry_dict.get("platform", "")),
                str(entry_dict.get("channel_id", "")),
                int(entry_dict.get("interval_secs", 120) or 120),
                int(bool(entry_dict.get("auto_record", False))),
                int(bool(entry_dict.get("subscribe_vods", False))),
                int(bool(entry_dict.get("capture_comments", False))),
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
                json.dumps(
                    entry_dict.get("upgrade_profile", {})
                    if isinstance(entry_dict.get("upgrade_profile", {}), dict)
                    else {},
                    ensure_ascii=False, sort_keys=True,
                ),
                str(entry_dict.get("auth_profile_id", "") or ""),
                str(entry_dict.get("media_server_layout", "") or ""),
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
                         subscribe_vods, capture_comments, archive_ids,
                         override_output_dir, override_quality_pref,
                         override_filename_template,
                         schedule_start_hhmm, schedule_end_hhmm,
                         schedule_days_mask, retention_keep_last,
                         filter_keywords, override_pp_preset,
                         ytdlp_template_name, auto_upgrade, min_upgrade_quality,
                         upgrade_profile_json,
                         auth_profile_id, media_server_layout)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    str(d.get("url", "")),
                    str(d.get("platform", "")),
                    str(d.get("channel_id", "")),
                    int(d.get("interval_secs", 120) or 120),
                    int(bool(d.get("auto_record", False))),
                    int(bool(d.get("subscribe_vods", False))),
                    int(bool(d.get("capture_comments", False))),
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
                    json.dumps(
                        d.get("upgrade_profile", {})
                        if isinstance(d.get("upgrade_profile", {}), dict)
                        else {},
                        ensure_ascii=False, sort_keys=True,
                    ),
                    str(d.get("auth_profile_id", "") or ""),
                    str(d.get("media_server_layout", "") or ""),
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
