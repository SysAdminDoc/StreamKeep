"""RSS Feed Generator — podcast-compatible feeds for recordings (F70).

Serves RSS 2.0 feeds via the local web server with ``<enclosure>`` tags
pointing to ``/media/{id}`` URLs. Compatible with Pocket Casts, AntennaPod,
and other podcast apps.

Feeds:
  /feed/all.xml       — all shared recordings
  /feed/{channel}.xml — per-channel feed
"""

import mimetypes
import os
import re
from datetime import datetime, timezone
from xml.sax.saxutils import escape
from urllib.parse import urlsplit


_PUBLISHING_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MIME_OVERRIDES = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
}


def _escape_xml_attribute(value):
    """Escape a value used inside a double-quoted XML attribute."""
    return escape(str(value or ""), {'"': "&quot;", "'": "&apos;"})


def generate_rss(entries, base_url, *, title="StreamKeep", channel=None,
                 limit=100):
    """Build RSS 2.0 XML from history entries.

    *entries* is a list of dicts with keys: share_id, title, channel,
    date, path, media_path, duration_secs.

    *base_url* is e.g. ``http://192.168.1.100:8080``.

    Returns XML string.
    """
    base_url = str(base_url or "").strip().rstrip("/")
    try:
        parsed_base = urlsplit(base_url)
    except ValueError:
        parsed_base = None
    if (
        parsed_base is None
        or parsed_base.scheme not in {"http", "https"}
        or not parsed_base.netloc
        or parsed_base.username
        or parsed_base.password
        or parsed_base.query
        or parsed_base.fragment
    ):
        raise ValueError("RSS base_url must use HTTP or HTTPS")
    if any(char.isspace() or ord(char) < 32 for char in base_url):
        raise ValueError("RSS base_url contains invalid characters")
    feed_title = f"{title} - {channel}" if channel else title
    feed_desc = f"Recordings from {channel}" if channel else "All StreamKeep recordings"
    try:
        limit = max(1, min(1000, int(limit or 100)))
    except (TypeError, ValueError):
        limit = 100

    # Filter by channel if specified
    if channel:
        entries = [e for e in entries if (e.get("channel", "") or "").lower() == channel.lower()]

    # Limit to most recent
    entries = list(entries or [])[-limit:]

    items_xml = ""
    for e in reversed(entries):
        sid = str(e.get("share_id", "") or "").strip()
        if not sid:
            continue
        etitle = escape(str(e.get("title", "Untitled") or "Untitled"))
        echannel = escape(str(e.get("channel", "") or ""))
        edate = str(e.get("date", "") or "")
        media_url = f"{base_url}/media/{sid}" if sid else ""
        try:
            duration = max(0, int(e.get("duration_secs", 0) or 0))
        except (TypeError, ValueError):
            duration = 0

        # RFC 822 date
        pub_date = ""
        try:
            dt = datetime.strptime(edate[:16], "%Y-%m-%d %H:%M")
            pub_date = dt.replace(tzinfo=timezone.utc).strftime(
                "%a, %d %b %Y %H:%M:%S +0000"
            )
        except (ValueError, TypeError):
            pass

        # File size estimate
        media_path = e.get("media_path", "")
        file_size = 0
        if media_path and os.path.isfile(media_path):
            try:
                file_size = os.path.getsize(media_path)
            except OSError:
                pass
        extension = os.path.splitext(str(media_path or ""))[1].lower()
        media_type = (
            _MIME_OVERRIDES.get(extension)
            or mimetypes.guess_type(str(media_path or ""))[0]
            or "application/octet-stream"
        )

        # Duration in HH:MM:SS for itunes:duration
        dur_str = ""
        if duration > 0:
            h = duration // 3600
            m = (duration % 3600) // 60
            s = duration % 60
            dur_str = f"{h}:{m:02d}:{s:02d}"

        items_xml += f"""    <item>
      <title>{etitle}</title>
      <description>{echannel}</description>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">{escape(str(sid))}</guid>"""

        if media_url:
            items_xml += f"""
      <enclosure url="{_escape_xml_attribute(media_url)}" length="{file_size}" type="{_escape_xml_attribute(media_type)}"/>"""

        if dur_str:
            items_xml += f"""
      <itunes:duration>{dur_str}</itunes:duration>"""

        items_xml += """
    </item>
"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(feed_title)}</title>
    <description>{escape(feed_desc)}</description>
    <link>{escape(base_url)}</link>
    <generator>StreamKeep</generator>
    <lastBuildDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>
{items_xml}  </channel>
</rss>"""


def channel_list(entries):
    """Return sorted list of unique channel names from entries."""
    channels = set()
    for e in entries:
        ch = e.get("channel", "")
        if ch:
            channels.add(ch)
    return sorted(channels)
