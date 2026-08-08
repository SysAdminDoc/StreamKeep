"""Channel-statistics table family (V190).

The monitor-facing aggregation used to open ``library.db`` directly from
``streamkeep.channel_stats``.  Keeping the table and its connection lifecycle
inside the database package means profile switching, migrations, and tests all
use the same pooled connection policy as the rest of StreamKeep.
"""

from __future__ import annotations

import time
from typing import Any

from .connection import _connect
from .primitives import _write_lock


def ensure_channel_stats_table() -> None:
    """Create the transition table and indexes when first used."""
    with _write_lock:
        db = _connect()
        try:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS channel_polls (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    platform   TEXT NOT NULL DEFAULT '',
                    timestamp  REAL NOT NULL,
                    status     TEXT NOT NULL DEFAULT 'unknown',
                    viewers    INTEGER NOT NULL DEFAULT 0,
                    title      TEXT NOT NULL DEFAULT '',
                    game       TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_cp_channel
                    ON channel_polls(channel_id);
                CREATE INDEX IF NOT EXISTS idx_cp_ts
                    ON channel_polls(timestamp);
                """
            )
            db.commit()
        finally:
            db.close()


def log_channel_transition(
    channel_id: str,
    platform: str,
    status: str,
    *,
    viewers: int = 0,
    title: str = "",
    game: str = "",
    timestamp: float | None = None,
) -> None:
    """Persist one monitor state transition."""
    ensure_channel_stats_table()
    with _write_lock:
        db = _connect()
        try:
            db.execute(
                "INSERT INTO channel_polls "
                "(channel_id, platform, timestamp, status, viewers, title, game) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(channel_id or "")[:256],
                    str(platform or "")[:64],
                    float(time.time() if timestamp is None else timestamp),
                    str(status or "unknown")[:32],
                    max(0, int(viewers or 0)),
                    str(title or "")[:200],
                    str(game or "")[:100],
                ),
            )
            db.commit()
        finally:
            db.close()


def load_channel_polls(channel_id: str, *, cutoff: float = 0.0) -> list[dict[str, Any]]:
    """Return one channel's transition rows in chronological order."""
    ensure_channel_stats_table()
    db = _connect(readonly=True)
    try:
        rows = db.execute(
            "SELECT timestamp, status, viewers, title, game, platform "
            "FROM channel_polls WHERE channel_id=? AND timestamp>=? "
            "ORDER BY timestamp ASC",
            (str(channel_id or ""), float(cutoff or 0.0)),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def list_channel_stat_channels() -> list[str]:
    """Return the distinct channel identifiers that have transition rows."""
    ensure_channel_stats_table()
    db = _connect(readonly=True)
    try:
        rows = db.execute(
            "SELECT DISTINCT channel_id FROM channel_polls "
            "WHERE channel_id <> '' ORDER BY channel_id COLLATE NOCASE"
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        db.close()
