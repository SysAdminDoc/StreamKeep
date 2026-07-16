"""Podcast RSS — parses RSS/XML feeds for episode listing."""

import html
import json
import re
import urllib.parse

from .. import CURL_UA
from ..http import curl
from ..models import QualityInfo, StreamInfo, VODInfo
from .base import Extractor


def parse_podcast_chapters_json(text):
    """Parse a Podcast Namespace ``application/json+chapters`` document.

    Returns a list of ``{title, start, end, img, url}`` dicts ordered by start
    time, matching the player's chapter model. Each chapter ends where the next
    begins (per the spec, ``endTime`` is optional); a chapter flagged
    ``toc: false`` is excluded from the table of contents. Malformed entries are
    skipped rather than aborting the whole file.
    """
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    raw = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    parsed = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if entry.get("toc") is False:
            continue
        try:
            start = float(entry.get("startTime"))
        except (TypeError, ValueError):
            continue
        end = None
        if entry.get("endTime") is not None:
            try:
                end = float(entry.get("endTime"))
            except (TypeError, ValueError):
                end = None
        parsed.append({
            "title": str(entry.get("title") or "").strip(),
            "start": start,
            "end": end,
            "img": str(entry.get("img") or "").strip(),
            "url": str(entry.get("url") or "").strip(),
        })
    parsed.sort(key=lambda c: c["start"])
    # Fill missing end times from the next chapter's start.
    for index, chapter in enumerate(parsed):
        if chapter["end"] is None:
            chapter["end"] = (
                parsed[index + 1]["start"] if index + 1 < len(parsed) else 0.0
            )
    return parsed


class PodcastRSSExtractor(Extractor):
    NAME = "Podcast"
    ICON = "P"
    COLOR = "yellow"
    URL_PATTERNS = [
        re.compile(r'(?:https?://).+\.(rss|xml)(\?.*)?$'),
        re.compile(r'(?:https?://).+/feed/?(\?.*)?$'),
        re.compile(r'(?:https?://).+/rss/?(\?.*)?$'),
    ]

    def extract_channel_id(self, url):
        try:
            parsed = urllib.parse.urlparse(url.strip())
            return parsed.netloc.replace(".", "_")
        except Exception:
            return "podcast"

    def supports_vod_listing(self):
        return True

    def list_vods(self, url, log_fn=None, cursor=None):
        self._log(log_fn, f"Fetching podcast RSS: {url}")
        body = curl(
            url,
            headers={
                "User-Agent": CURL_UA,
                "Accept": "application/rss+xml, application/xml, text/xml",
            },
        )
        if not body:
            self._log(log_fn, "Failed to fetch RSS feed")
            return [], None

        vods = []
        items = re.findall(r'<item>(.*?)</item>', body, re.DOTALL)
        for item in items:
            title_m = re.search(
                r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item
            )
            title = html.unescape(title_m.group(1).strip()) if title_m else "Untitled"

            date_m = re.search(r'<pubDate>(.*?)</pubDate>', item)
            date = date_m.group(1).strip() if date_m else ""

            enc_m = re.search(r'<enclosure[^>]+url="([^"]+)"', item)
            if not enc_m:
                enc_m = re.search(r"<enclosure[^>]+url='([^']+)'", item)
            if not enc_m:
                continue
            enc_url = enc_m.group(1)

            dur_m = re.search(r'<itunes:duration>([\d:]+)</itunes:duration>', item)
            dur_str = ""
            if dur_m:
                parts = dur_m.group(1).split(":")
                if len(parts) == 3:
                    dur_str = f"{parts[0]}h {parts[1]}m"
                elif len(parts) == 2:
                    dur_str = f"{parts[0]}m {parts[1]}s"
                else:
                    dur_str = f"{parts[0]}s"

            vods.append(VODInfo(
                title=title, date=date, source=enc_url,
                is_live=False, viewers=0, duration=dur_str,
                duration_ms=0, platform="Podcast", channel="",
            ))

        self._log(log_fn, f"Found {len(vods)} episode(s)")
        return vods, None  # RSS feeds are not paginated

    def resolve(self, url, log_fn=None):
        if any(url.endswith(ext) for ext in (".mp3", ".m4a", ".ogg", ".wav", ".aac")):
            info = StreamInfo(
                platform="Podcast",
                url=url,
                title=url.split("/")[-1],
                channel=self.extract_channel_id(url) or "",
            )
            info.qualities.append(QualityInfo(
                name="audio", url=url, resolution="audio", format_type="mp4",
            ))
            return info
        return None  # list_vods handles the RSS feed
