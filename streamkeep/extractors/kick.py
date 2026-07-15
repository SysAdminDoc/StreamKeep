"""Kick.com — hybrid official + v2 API extractor.

Uses the official public API (api.kick.com/public/v1/) for channel
metadata and live-status checks where possible, and falls back to the
undocumented v2 endpoints for VOD listing and HLS playback URLs that
the official API does not yet expose.

Official API docs: https://docs.kick.com / https://api.kick.com/swagger/doc.yaml
"""

import logging
import re

from .. import CURL_UA
from ..http import curl, curl_json
from ..hls import parse_hls_master, parse_hls_duration
from ..models import StreamInfo, VODInfo
from ..utils import fmt_duration
from .base import Extractor

logger = logging.getLogger(__name__)

_SAFE_SLUG = re.compile(r'^[a-zA-Z0-9_-]{1,50}$')
_SAFE_UUID = re.compile(r'^[0-9a-fA-F-]{8,64}$')

# Channel page:  kick.com/<slug>
_CHANNEL_URL = re.compile(r'(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)/?$')
# VOD permalink: kick.com/<slug>/videos/<uuid>  or  kick.com/video[s]/<uuid>
_VOD_URL = re.compile(
    r'(?:https?://)?(?:www\.)?kick\.com/'
    r'(?:([a-zA-Z0-9_-]+)/)?videos?/'
    r'([0-9a-fA-F-]{8,})'
)

# Official public API base — requires no auth for App Access Token scopes
# when called without an Authorization header on read-only endpoints.
_OFFICIAL_API = "https://api.kick.com/public/v1"

# Undocumented v2 — still the only way to get VOD source URLs and live
# playback_url.  Keep these as fallback until the official API covers media.
_V2_API = "https://kick.com/api/v2"

# Undocumented v1 — the only endpoint that resolves a VOD UUID to its HLS
# master playlist (v2 no longer exposes video-by-uuid).
_V1_API = "https://kick.com/api/v1"

_JSON_HEADERS = {"User-Agent": CURL_UA, "Accept": "application/json"}


class KickExtractor(Extractor):
    NAME = "Kick"
    ICON = "K"
    COLOR = "green"
    URL_PATTERNS = [_CHANNEL_URL, _VOD_URL]

    def extract_channel_id(self, url):
        u = url.strip()
        m = _VOD_URL.match(u)
        if m:
            # group(1) is the channel slug (may be absent on /video/<uuid>)
            return m.group(1)
        m = _CHANNEL_URL.match(u)
        if m:
            return m.group(1)
        return None

    def extract_vod_id(self, url):
        """Return the VOD UUID for a permalink URL, or None."""
        m = _VOD_URL.match(url.strip())
        return m.group(2) if m else None

    def is_direct_url(self, url):
        return self.extract_vod_id(url) is not None

    def supports_vod_listing(self):
        return True

    def supports_live_check(self):
        return True

    # ── Official API helpers ──────────────────────────────────────────

    def _official_channel(self, slug):
        """Fetch channel info via the official public API (by slug)."""
        if not _SAFE_SLUG.match(slug):
            return None
        data = curl_json(
            f"{_OFFICIAL_API}/channels?slug={slug}",
            headers=_JSON_HEADERS,
        )
        if not data or not isinstance(data, dict):
            return None
        items = data.get("data", [])
        if isinstance(items, list) and items:
            return items[0]
        return None

    def _official_livestream(self, broadcaster_user_id):
        """Check live status via the official livestreams endpoint."""
        if not broadcaster_user_id:
            return None
        data = curl_json(
            f"{_OFFICIAL_API}/livestreams?broadcaster_user_id={broadcaster_user_id}",
            headers=_JSON_HEADERS,
        )
        if not data or not isinstance(data, dict):
            return None
        items = data.get("data", [])
        if isinstance(items, list) and items:
            return items[0]
        return None

    # ── V2 API helpers (playback URLs, VOD sources) ───────────────────

    def _v2_livestream_data(self, slug):
        """Fetch livestream data from undocumented v2 — returns playback_url."""
        data = curl_json(
            f"{_V2_API}/channels/{slug}/livestream",
            headers=_JSON_HEADERS,
        )
        if data and isinstance(data, dict):
            return data.get("data", data)
        return None

    def _v1_video(self, uuid):
        """Resolve a VOD UUID to ``(source_m3u8, title)`` via undocumented v1."""
        if not uuid or not _SAFE_UUID.match(uuid):
            return "", ""
        data = curl_json(f"{_V1_API}/video/{uuid}", headers=_JSON_HEADERS)
        if not data or not isinstance(data, dict):
            return "", ""
        source = str(data.get("source") or "")
        ls = data.get("livestream")
        title = ""
        if isinstance(ls, dict):
            title = str(ls.get("session_title") or ls.get("title") or "")
        return source, title

    # ── Public interface ──────────────────────────────────────────────

    def check_live(self, url):
        slug = self.extract_channel_id(url)
        if not slug:
            return None
        # Try official API first — it returns live metadata without
        # exposing undocumented endpoints
        ch = self._official_channel(slug)
        if ch and isinstance(ch, dict):
            uid = ch.get("broadcaster_user_id") or ch.get("user_id")
            if uid:
                ls = self._official_livestream(uid)
                if ls and isinstance(ls, dict):
                    return True
                return False
        # Fallback to v2 — works without auth
        ls = self._v2_livestream_data(slug)
        if isinstance(ls, dict):
            return ls.get("playback_url") is not None
        return None

    def list_vods(self, url, log_fn=None, cursor=None):
        slug = self.extract_channel_id(url)
        if not slug:
            return [], None
        if not _SAFE_SLUG.match(slug):
            self._log(log_fn, f"Invalid Kick slug: {slug!r}")
            return [], None
        page = int(cursor) if cursor else 1
        self._log(log_fn, f"Fetching VODs for Kick channel: {slug} (page {page})")

        # VOD listing is only available via v2 — the official API has no
        # video/VOD endpoints yet.
        api_url = f"{_V2_API}/channels/{slug}/videos?page={page}"
        data = curl_json(api_url, headers=_JSON_HEADERS)
        if not data or not isinstance(data, list):
            self._log(log_fn, "No VODs found or API error")
            return [], None

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
        next_cursor = str(page + 1) if len(vods) >= 20 else None
        return vods, next_cursor

    def resolve(self, url, log_fn=None):
        """Resolve Kick URL to StreamInfo with qualities."""
        slug = self.extract_channel_id(url) or ""
        if ".m3u8" in url:
            return self._resolve_m3u8(url, log_fn, channel=slug)
        vod_id = self.extract_vod_id(url)
        if vod_id:
            source, title = self._v1_video(vod_id)
            if source:
                return self._resolve_m3u8(source, log_fn, channel=slug, title=title)
            self._log(log_fn, f"Kick VOD {vod_id}: no source URL (private/pruned?)")
            return None
        if slug:
            # Live check via v2 — only v2 returns playback_url
            ls = self._v2_livestream_data(slug)
            playback_url = str((ls or {}).get("playback_url") or "")
            if playback_url:
                title = str(
                    (ls or {}).get("session_title")
                    or (ls or {}).get("title")
                    or ""
                )
                info = self._resolve_m3u8(
                    playback_url, log_fn, channel=slug, title=title,
                )
                if info:
                    info.is_live = True
                    if not info.duration_str:
                        info.duration_str = "Live"
                return info
        vods, _ = self.list_vods(url, log_fn)
        if len(vods) == 1:
            return self._resolve_m3u8(
                vods[0].source,
                log_fn,
                channel=slug or vods[0].channel,
                title=vods[0].title,
            )
        return None  # Multiple VODs handled by UI

    def _resolve_m3u8(self, url, log_fn=None, channel="", title=""):
        self._log(log_fn, f"Fetching playlist: {url}")
        body = curl(url)
        if not body or not body.startswith("#EXTM3U"):
            return None

        info = StreamInfo(platform="Kick", url=url, channel=channel or "", title=title or "")

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
        if info.is_live and not info.duration_str:
            info.duration_str = "Live"
        return info
