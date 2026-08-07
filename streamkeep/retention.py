"""Source-side retention windows and backfill ordering (V169).

A platform that deletes its own VODs decides how long StreamKeep has to
capture them. Backfilling newest-first spends that budget in exactly the wrong
order: the newest VOD has the whole window left, the oldest may have hours, and
if the queue is long enough the oldest is gone before its turn. Whatever is
closest to being deleted should be fetched first.

Only platforms with a *documented* window are reordered. Everything else keeps
the order the extractor returned, because guessing a retention policy and
reordering someone's queue on the strength of it would be worse than doing
nothing.

Pure: no Qt, no network, no config reads, so the policy is testable directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

DAY_SECONDS = 86_400


@dataclass(frozen=True)
class RetentionWindow:
    """How long a platform keeps a replay, and how many it keeps."""

    platform: str
    days: int
    #: Replays kept at once; the oldest is evicted when a new one lands.
    #: ``0`` means the platform documents no cap.
    max_stored: int = 0
    source: str = ""

    @property
    def seconds(self) -> int:
        return int(self.days) * DAY_SECONDS


#: Documented windows only. Keyed by platform, then by whether the channel is
#: verified/partnered, because that is what changes the number.
#:
#: Kick: 7 days and 16 stored replays unverified, 30 days and 30 stored when
#: verified. Sourced from Kick's own help centre (stream replays / missing VOD
#: articles) and recorded here rather than in a comment so a test can read it.
RETENTION_WINDOWS = {
    "kick": {
        False: RetentionWindow("kick", 7, 16, "Kick help centre: stream replays"),
        True: RetentionWindow("kick", 30, 30, "Kick help centre: stream replays"),
    },
}


def normalize_platform(platform) -> str:
    return str(platform or "").strip().casefold()


def retention_window(platform, *, verified=False):
    """Return the documented window for a platform, or ``None`` if unknown."""
    table = RETENTION_WINDOWS.get(normalize_platform(platform))
    if not table:
        return None
    return table.get(bool(verified)) or table.get(False)


def has_retention_window(platform, *, verified=False) -> bool:
    return retention_window(platform, verified=verified) is not None


def _published_epoch(vod) -> float | None:
    """Best-effort publication time for one VOD, or None when unknown.

    A VOD with no usable timestamp cannot be placed on the deadline axis, and
    is deliberately left in its original position rather than guessed at.
    """
    for attr in ("published_at", "timestamp", "created_at", "date"):
        value = getattr(vod, attr, None) if not isinstance(vod, dict) else vod.get(attr)
        if value in (None, ""):
            continue
        if isinstance(value, (int, float)):
            moment = float(value)
            # A plausible epoch; anything smaller is a duration or a year.
            if moment > 10_000_000:
                return moment
            continue
        text = str(value).strip()
        if not text:
            continue
        if text.isdigit() and len(text) >= 8:
            try:
                return float(text)
            except ValueError:
                continue
        from datetime import datetime

        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text.replace("Z", "+0000"), fmt)
            except ValueError:
                continue
            return parsed.timestamp()
    return None


def reachable_until(vod, platform, *, verified=False):
    """Epoch at which this VOD stops being fetchable, or ``None``."""
    window = retention_window(platform, verified=verified)
    if window is None:
        return None
    published = _published_epoch(vod)
    if published is None:
        return None
    return published + window.seconds


def backfill_reason(vod, platform, *, verified=False, now=None) -> str:
    """One line for the queue row explaining why this was ordered where it was."""
    window = retention_window(platform, verified=verified)
    if window is None:
        return ""
    deadline = reachable_until(vod, platform, verified=verified)
    if deadline is None:
        return (
            f"{window.platform} keeps replays {window.days} days; this item has "
            "no publication date, so queue order was left unchanged"
        )
    moment = float(time.time() if now is None else now)
    remaining = deadline - moment
    if remaining <= 0:
        return (
            f"past {window.platform}'s {window.days}-day retention window; "
            "fetch may already fail"
        )
    hours = remaining / 3600
    left = f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.1f}d"
    detail = (
        f"oldest-reachable first: {left} left of {window.platform}'s "
        f"{window.days}-day window"
    )
    if window.max_stored:
        detail += f" (max {window.max_stored} replays kept)"
    return detail


def order_backfill(vods, platform, *, verified=False, now=None, enabled=True):
    """Return ``[(vod, reason), ...]`` with the soonest deadline first.

    The sort is stable, so items that share a deadline — or have no usable date
    at all — keep the order the extractor gave them. Platforms with no
    documented window are returned untouched with empty reasons, which is what
    makes this safe to call unconditionally.
    """
    items = list(vods or [])
    if not enabled or not has_retention_window(platform, verified=verified):
        return [(vod, "") for vod in items]

    moment = float(time.time() if now is None else now)
    decorated = []
    for index, vod in enumerate(items):
        deadline = reachable_until(vod, platform, verified=verified)
        # Undated items sort last rather than first: promoting an item whose
        # urgency is unknown would displace one whose urgency is known.
        key = (deadline is None, deadline if deadline is not None else 0.0, index)
        decorated.append((key, vod))
    decorated.sort(key=lambda pair: pair[0])
    return [
        (vod, backfill_reason(vod, platform, verified=verified, now=moment))
        for _key, vod in decorated
    ]
