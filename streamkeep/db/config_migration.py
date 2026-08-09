"""Atomic migration of library state from the legacy JSON configuration."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .connection import _connect
from .monitor import _save_all_monitor_channels_in_connection
from .primitives import _write_lock
from .projections import _canonical_history_entry
from .queue import _save_queue_in_connection


def migrate_from_config(
    cfg: dict[str, Any],
    init_db: Callable[[], None],
) -> bool:
    """Move legacy library sections into SQLite in one write transaction."""
    if not any(k in cfg for k in ("history", "monitor_channels", "download_queue")):
        return False

    init_db()
    migrated = False
    with _write_lock:
        db = _connect()
        try:
            # The empty-database check and every import below must share one
            # write transaction; otherwise two processes can both observe an
            # empty library and duplicate the config payload.
            db.execute("BEGIN IMMEDIATE")
            existing = [
                db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("history", "monitor_channels", "download_queue")
            ]
            if any(existing):
                db.rollback()
            else:
                history = cfg.get("history", [])
                if isinstance(history, list):
                    for entry in history:
                        if not isinstance(entry, dict):
                            continue
                        entry = _canonical_history_entry(entry)
                        db.execute(
                            """
                            INSERT INTO history
                                (date, platform, source_id, webpage_url, title,
                                 channel, quality, size, path, url, favorite, watched,
                                 watch_position_secs, bookmarks)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                str(entry.get("date", "")),
                                str(entry.get("platform", "")),
                                str(entry.get("source_id", "")),
                                str(entry.get("webpage_url", "")),
                                str(entry.get("title", "")),
                                str(entry.get("channel", "")),
                                str(entry.get("quality", "")),
                                str(entry.get("size", "")),
                                str(entry.get("path", "")),
                                str(entry.get("url", "")),
                                int(bool(entry.get("favorite", False))),
                                int(bool(entry.get("watched", False))),
                                float(entry.get("watch_position_secs", 0) or 0),
                                json.dumps(entry.get("bookmarks", []) or []),
                            ),
                        )

                channels = cfg.get("monitor_channels", [])
                if isinstance(channels, list):
                    entries = []
                    for channel in channels:
                        if not isinstance(channel, dict) or "url" not in channel:
                            continue
                        entries.append({
                            "url": channel.get("url", ""),
                            "platform": channel.get("platform", ""),
                            "channel_id": channel.get("channel_id", ""),
                            "interval_secs": channel.get("interval", 120),
                            "auto_record": channel.get("auto_record", False),
                            "subscribe_vods": channel.get("subscribe_vods", False),
                            "capture_comments": channel.get("capture_comments", False),
                            "archive_ids": channel.get("archive_ids", []),
                            "override_output_dir": channel.get("override_output_dir", ""),
                            "override_quality_pref": channel.get("override_quality_pref", ""),
                            "override_filename_template": channel.get("override_filename_template", ""),
                            "schedule_start_hhmm": channel.get("schedule_start_hhmm", ""),
                            "schedule_end_hhmm": channel.get("schedule_end_hhmm", ""),
                            "schedule_days_mask": channel.get("schedule_days_mask", 0),
                            "retention_keep_last": channel.get("retention_keep_last", 0),
                            "filter_keywords": channel.get("filter_keywords", ""),
                            "override_pp_preset": channel.get("override_pp_preset", ""),
                            "ytdlp_template_name": channel.get("ytdlp_template_name", ""),
                            "auto_upgrade": channel.get("auto_upgrade", False),
                            "min_upgrade_quality": channel.get("min_upgrade_quality", ""),
                            "auth_profile_id": channel.get("auth_profile_id", ""),
                            "media_server_layout": channel.get("media_server_layout", ""),
                        })
                    if entries:
                        _save_all_monitor_channels_in_connection(db, entries)

                queue = cfg.get("download_queue", [])
                if isinstance(queue, list) and queue:
                    _save_queue_in_connection(db, queue)
                db.commit()
                migrated = True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # Strip migrated keys so they don't persist in JSON, including when a
    # concurrent process completed the migration first.
    for key in ("history", "monitor_channels", "download_queue"):
        cfg.pop(key, None)
    return migrated
