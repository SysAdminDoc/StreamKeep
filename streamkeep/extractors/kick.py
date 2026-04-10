"""Kick.com — native API v2 extractor."""

import re

from .. import CURL_UA
from ..http import curl, curl_json
from ..hls import parse_hls_master, parse_hls_duration
from ..models import StreamInfo, VODInfo
from ..utils import fmt_duration
from .base import Extractor


class KickExtractor(Extractor):
    NAME = "Kick"
    ICON = "K"
    COLOR = "green"
    URL_PATTERNS = [
        re.compile(r'(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)/?$'),
    ]

    def extract_channel_id(self, url):
        for p in self.URL_PATTERNS:
            m = p.match(url.strip())
            if m:
                return m.group(1)
        return None

    def supports_vod_listing(self):
        return True

    def supports_live_check(self):
        return True

    def check_live(self, url):
        slug = self.extract_channel_id(url)
        if not slug:
            return None
        data = curl_json(
            f"https://kick.com/api/v2/channels/{slug}/livestream",
            headers={"User-Agent": CURL_UA, "Accept": "application/json"},
        )
        if data and isinstance(data, dict):
            ls = data.get("data", data)
            return ls.get("playback_url") is not None
        return None

    def list_vods(self, url, log_fn=None):
        slug = self.extract_channel_id(url)
        if not slug:
            return []
        self._log(log_fn, f"Fetching VODs for Kick channel: {slug}")

        data = curl_json(
            f"https://kick.com/api/v2/channels/{slug}/videos",
            headers={"User-Agent": CURL_UA, "Accept": "application/json"},
        )
        if not data or not isinstance(data, list):
            self._log(log_fn, "No VODs found or API error")
            return []

        vods = []
        for v in data:
            if not isinstance(v, dict):
                continue
            source = v.get("source") or ""
            if not source:
                continue
            dur_ms = v.get("duration") or 0
            try:
                dur_str = fmt_duration(dur_ms / 1000) if dur_ms else ""
            except (TypeError, ValueError):
                dur_str = ""
                dur_ms = 0
            vods.append(VODInfo(
                title=str(v.get("session_title") or "Untitled"),
                date=str(v.get("created_at") or ""),
                source=str(source),
                is_live=bool(v.get("is_live", False)),
                viewers=int(v.get("viewer_count") or 0),
                duration=dur_str,
                duration_ms=int(dur_ms),
                platform="Kick",
                channel=slug,
            ))
        self._log(log_fn, f"Found {len(vods)} VOD(s)")
        return vods

    def resolve(self, url, log_fn=None):
        """Resolve Kick m3u8 URL to StreamInfo."""
        if ".m3u8" in url:
            return self._resolve_m3u8(url, log_fn)
        vods = self.list_vods(url, log_fn)
        if len(vods) == 1:
            return self._resolve_m3u8(vods[0].source, log_fn)
        return None  # Multiple VODs handled by UI

    def _resolve_m3u8(self, url, log_fn=None):
        self._log(log_fn, f"Fetching playlist: {url}")
        body = curl(url)
        if not body or not body.startswith("#EXTM3U"):
            return None

        info = StreamInfo(platform="Kick", url=url)

        if "#EXT-X-STREAM-INF" in body:
            info.is_master = True
            base = url.rsplit("/", 1)[0]
            info.qualities = parse_hls_master(body, base)
            if info.qualities:
                sub_body = curl(info.qualities[0].url)
                if sub_body:
                    info.total_secs, info.start_time, info.segment_count = parse_hls_duration(sub_body)
        else:
            info.total_secs, info.start_time, info.segment_count = parse_hls_duration(body)
            base = url.rsplit("/", 2)[0]
            master_url = f"{base}/master.m3u8"
            master_body = curl(master_url)
            if master_body and master_body.startswith("#EXTM3U"):
                mbase = master_url.rsplit("/", 1)[0]
                info.qualities = parse_hls_master(master_body, mbase)
                info.is_master = True
                info.url = master_url

        info.duration_str = fmt_duration(info.total_secs)
        return info
