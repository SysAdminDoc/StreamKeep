"""Channel Statistics & Growth Trends — monitor poll logging + aggregation (F66).

Logs status transitions (live/offline) per monitored channel to a
``channel_polls`` table in library.db.  Aggregation queries provide
streams-per-week, average duration, and top game/category stats.

Usage::

    from streamkeep.channel_stats import log_transition, get_channel_stats
    log_transition("xqc", "twitch", "live", viewers=45000, title="Just Chatting")
    stats = get_channel_stats("xqc", weeks=8)
"""

from datetime import datetime, timedelta
from pathlib import Path
import time

from .paths import CONFIG_DIR
from . import db as _db

DB_PATH = CONFIG_DIR / "library.db"
_DEFAULT_CONFIG_DIR = Path(CONFIG_DIR)
_DEFAULT_DB_PATH = Path(DB_PATH)


def _sync_database_paths():
    """Keep the historical test/config path overrides working.

    All actual connections still go through ``streamkeep.db``; these aliases
    only let older callers redirect the active profile during isolated tests.
    """
    redirected = (
        Path(CONFIG_DIR).expanduser().resolve(strict=False) != _DEFAULT_CONFIG_DIR.resolve(strict=False)
        or Path(DB_PATH).expanduser().resolve(strict=False) != _DEFAULT_DB_PATH.resolve(strict=False)
    )
    if redirected:
        _db.CONFIG_DIR = Path(CONFIG_DIR)
        _db.DB_PATH = Path(DB_PATH)
    return redirected


def _release_redirected_database(redirected):
    if not redirected:
        return
    try:
        _db.close_connections()
    finally:
        _db.CONFIG_DIR = _DEFAULT_CONFIG_DIR
        _db.DB_PATH = _DEFAULT_DB_PATH


def _ensure_table():
    redirected = _sync_database_paths()
    try:
        _db.ensure_channel_stats_table()
    except Exception:
        pass  # safe: best-effort fallback; preserve the primary operation
    return redirected


def log_transition(channel_id, platform, status, *, viewers=0, title="", game=""):
    """Log a status transition (live->offline or offline->live).

    Should only be called on actual state changes, not every poll.
    """
    redirected = _ensure_table()
    try:
        _db.log_channel_transition(
            channel_id, platform, status,
            viewers=viewers, title=title, game=game,
        )
    except Exception:
        pass  # safe: best-effort fallback; preserve the primary operation
    finally:
        _release_redirected_database(redirected)


def get_channel_stats(channel_id, weeks=8):
    """Aggregate stats for a channel over the last *weeks* weeks.

    Returns a dict::

        {
            "streams_total": int,
            "streams_per_week": float,
            "avg_duration_mins": float,
            "top_games": [(game, count), ...],
            "weekly_counts": [(week_label, count), ...],  # for sparkline
            "last_live": str,  # ISO timestamp or ""
        }
    """
    redirected = _ensure_table()
    cutoff = time.time() - weeks * 7 * 86400
    try:
        rows = _db.load_channel_polls(channel_id, cutoff=cutoff)
    except Exception:
        rows = []
    finally:
        _release_redirected_database(redirected)

    if not rows:
        return {
            "streams_total": 0,
            "streams_per_week": 0,
            "avg_duration_mins": 0,
            "top_games": [],
            "weekly_counts": [],
            "last_live": "",
        }

    # Count stream sessions (each live->offline pair is one stream)
    sessions = []
    live_start = None
    live_game = ""
    for row in rows:
        ts = row["timestamp"]
        status = row["status"]
        game = row.get("game", "")
        if status == "live" and live_start is None:
            live_start = ts
            live_game = game or ""
        elif status == "offline" and live_start is not None:
            sessions.append({
                "start": live_start,
                "end": ts,
                "duration": ts - live_start,
                "game": live_game,
            })
            live_start = None
            live_game = ""

    streams_total = len(sessions)
    streams_per_week = streams_total / max(weeks, 1)

    avg_duration = 0
    if sessions:
        avg_duration = sum(s["duration"] for s in sessions) / len(sessions) / 60

    # Top games
    from collections import Counter
    game_counts = Counter(s["game"] for s in sessions if s["game"])
    top_games = game_counts.most_common(5)

    # Weekly counts for sparkline
    now = datetime.now()
    weekly = {}
    for i in range(weeks):
        week_start = now - timedelta(weeks=weeks - i)
        label = week_start.strftime("%m/%d")
        weekly[label] = 0
    for s in sessions:
        d = datetime.fromtimestamp(s["start"])
        week_offset = (now - d).days // 7
        if 0 <= week_offset < weeks:
            label = (now - timedelta(weeks=week_offset)).strftime("%m/%d")
            if label in weekly:
                weekly[label] += 1
    weekly_counts = list(weekly.items())

    # Last live timestamp
    last_live = ""
    live_rows = [r for r in rows if r["status"] == "live"]
    if live_rows:
        last_live = datetime.fromtimestamp(
            live_rows[-1]["timestamp"]
        ).isoformat(timespec="minutes")

    return {
        "streams_total": streams_total,
        "streams_per_week": round(streams_per_week, 1),
        "avg_duration_mins": round(avg_duration, 0),
        "top_games": top_games,
        "weekly_counts": weekly_counts,
        "last_live": last_live,
    }


def get_all_channel_summaries(weeks=4):
    """Return a dict of channel_id -> summary stats for all tracked channels."""
    redirected = _ensure_table()
    try:
        channels = _db.list_channel_stat_channels()
    except Exception:
        return {}
    finally:
        _release_redirected_database(redirected)
    return {channel: get_channel_stats(channel, weeks) for channel in channels}
