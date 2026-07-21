"""SponsorBlock integration — skip sponsors/intros/outros on YouTube (F58).

Uses the SHA256-prefix privacy API so the SponsorBlock server never knows
which exact video is being queried.

Categories: sponsor, selfpromo, interaction, intro, outro, preview,
music_offtopic, filler.

Usage::

    segments = fetch_segments("dQw4w9WgXcQ")
    # [{"start": 0.0, "end": 15.2, "category": "intro"}, ...]
"""

import hashlib
import json
import re
import urllib.parse
from datetime import datetime, timedelta

from ..http import curl

API_BASE = "https://sponsor.ajay.app"
CATEGORIES = [
    "sponsor", "selfpromo", "interaction", "intro", "outro",
    "preview", "music_offtopic", "filler",
]


def is_sponsorblock_eligible(platform="", url=""):
    """Whether SponsorBlock applies (YouTube only)."""
    low_platform = str(platform or "").lower()
    if "youtube" in low_platform or low_platform == "youtu":
        return True
    return bool(extract_video_id(url))


def sponsorblock_deferred_start(publish_date, delay_hours, *, now=None):
    """Compute a queue ``start_at`` that defers a download so SponsorBlock
    crowd-sourced segments have time to accumulate after publish (V31).

    Returns an ISO-8601 timestamp string when the item should be held, or
    ``""`` to download immediately. The delay is measured from the VOD's
    publish date so already-old VODs dispatch at once (the max-age cap). When
    the publish date can't be parsed, the delay falls back to *now* so recent
    discoveries still wait rather than racing an empty segment database.
    """
    try:
        hours = float(delay_hours)
    except (TypeError, ValueError):
        return ""
    if hours <= 0:
        return ""
    now = now or datetime.now()
    base = _parse_publish_date(publish_date)
    reference = base if base is not None else now
    target = reference + timedelta(hours=hours)
    if target <= now:
        return ""
    return target.replace(microsecond=0).isoformat()


def _parse_publish_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def extract_video_id(url):
    """Extract YouTube video ID from a URL, or '' if not YouTube."""
    patterns = [
        r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url or "")
        if m:
            return m.group(1)
    return ""


def fetch_segments(video_id, categories=None):
    """Fetch SponsorBlock segments for a YouTube video.

    Uses the SHA256 prefix lookup (first 4 hex chars) for privacy.

    Returns a list of dicts: ``[{start, end, category, uuid}, ...]``
    sorted by start time. Returns ``[]`` on error or no segments.
    """
    if not video_id or len(video_id) != 11:
        return []

    if categories is None:
        categories = list(CATEGORIES)

    # SHA256 prefix lookup — server returns all videos with matching prefix
    sha = hashlib.sha256(video_id.encode("utf-8")).hexdigest()
    prefix = sha[:4]
    cats_param = urllib.parse.quote(json.dumps(categories))

    url = f"{API_BASE}/api/skipSegments/{prefix}?categories={cats_param}"
    body = curl(url, timeout=10)
    if not body:
        return []

    try:
        results = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(results, list):
        return []

    # Find matching video in results
    segments = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        if entry.get("videoID") != video_id:
            continue
        for seg in entry.get("segments", []):
            if not isinstance(seg, dict):
                continue
            segment = seg.get("segment", [])
            if len(segment) < 2:
                continue
            segments.append({
                "start": float(segment[0]),
                "end": float(segment[1]),
                "category": seg.get("category", "sponsor"),
                "uuid": seg.get("UUID", ""),
            })

    segments.sort(key=lambda s: s["start"])
    return segments


def total_sponsor_time(segments):
    """Return total seconds of sponsor/skip content."""
    return sum(s["end"] - s["start"] for s in segments)


def segments_to_chapters(segments, total_duration=0):
    """Convert SponsorBlock segments to chapter markers.

    Returns a list of ``{title, start, end}`` dicts suitable for
    metadata chapter writing.
    """
    chapters = []
    for seg in segments:
        chapters.append({
            "title": f"[{seg['category']}]",
            "start": seg["start"],
            "end": seg["end"],
        })
    return chapters
