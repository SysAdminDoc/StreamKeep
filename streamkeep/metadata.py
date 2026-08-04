"""Versioned, public-safe metadata sidecars and NFO export."""

import html
import ipaddress
import json
import math
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .models import ArchivalProvenance

METADATA_SCHEMA = "streamkeep.metadata"
METADATA_SCHEMA_VERSION = 3
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_IMPORT_SIDECAR_BYTES = 32 * 1024 * 1024

_PUBLIC_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_TWITCH_VOD_RE = re.compile(r"/(?:vod/|videos/)(\d+)(?:\.m3u8)?", re.I)
_TWITCH_LIVE_RE = re.compile(
    r"/api/channel/hls/([a-z0-9_]+)\.m3u8", re.I,
)
_KICK_VOD_RE = re.compile(r"/(?:videos?|vods?)/([^/?#]+)", re.I)
_RUMBLE_ID_RE = re.compile(r"\b(v[a-z0-9]+)\b", re.I)
_REDDIT_POST_RE = re.compile(
    r"/r/([^/?#]+)/comments/([a-z0-9]+)", re.I,
)
_SAFE_SOURCE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}\Z")
_SAFE_CHANNEL_RE = re.compile(r"[A-Za-z0-9_]{1,64}\Z")
_MEDIA_SUFFIXES = (
    ".m3u8", ".m3u", ".mpd", ".mp4", ".mkv", ".webm", ".mov", ".ts",
    ".avi", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac",
)
_SENSITIVE_FIELDS = frozenset({
    "authorization",
    "proxy_authorization",
    "cookie",
    "cookies",
    "set_cookie",
    "header",
    "headers",
    "http_header",
    "http_headers",
    "request_header",
    "request_headers",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "oauth_token",
    "sig",
    "signature",
    "secret",
    "client_secret",
    "password",
    "passphrase",
    "api_key",
    "x_plex_token",
    "credential",
    "credentials",
})

_TRACKING_QUERY_KEYS = frozenset({
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid",
    "mc_cid", "mc_eid", "oly_anon_id", "oly_enc_id", "rb_clickid",
    "s_cid", "vero_id", "wickedid", "yclid",
})
_VOLATILE_QUERY_KEYS = frozenset({
    "access_token", "auth", "authorization", "expires", "expiry",
    "key", "password", "secret", "sig", "signature", "token",
})


class MetadataWriteError(OSError):
    """Raised when a public sidecar cannot be written atomically."""


def archive_key_for_provenance(provenance):
    """Return the stable monitor/archive key represented by a provenance."""
    platform = scrub_public_text(
        getattr(provenance, "platform", "") or ""
    ).strip().casefold()
    source_id = _clean_source_id(
        getattr(provenance, "source_id", "") or ""
    )
    if not platform or not source_id:
        return ""
    return f"{platform}::{source_id}"


def _safe_tag_rows(value):
    """Normalize sidecar tags without allowing arbitrary metadata growth."""
    if not isinstance(value, (list, tuple)):
        return []
    rows = []
    seen = set()
    for item in value[:256]:
        if isinstance(item, dict):
            name = scrub_public_text(item.get("name", "")).strip()
            kind = scrub_public_text(item.get("kind", "user")).strip().lower()
        else:
            name = scrub_public_text(item).strip()
            kind = "user"
        if not name or kind not in {"system", "user"}:
            continue
        name = name[:256]
        key = (name.casefold(), kind)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": name, "kind": kind})
    return rows


def _clean_source_id(value):
    text = str(value or "").strip()
    if not _SAFE_SOURCE_ID_RE.fullmatch(text):
        return ""
    if "://" in text:
        return ""
    lowered = text.lower()
    if re.search(
        r"(?:^|[/@:._-])(?:token|sig|signature|secret|cookie|"
        r"auth(?:orization)?|bearer|password|credential|api[_-]?key)"
        r"(?:$|[/@:._-])",
        lowered,
    ):
        return ""
    return text


def _host_is(host, domain):
    return host == domain or host.endswith("." + domain)


def _safe_authority(parsed):
    if parsed.username is not None or parsed.password is not None:
        return ""
    host = str(parsed.hostname or "").rstrip(".")
    if not host:
        return ""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except (UnicodeError, ValueError):
            return ""
        if host == "localhost" or host.endswith((".localhost", ".local")):
            return ""
    else:
        if not literal.is_global:
            return ""
        host = str(literal)
    if ":" not in host and host.startswith("www."):
        host = host[4:]
    try:
        port = parsed.port
    except ValueError:
        return ""
    authority_host = f"[{host}]" if ":" in host else host
    if port and port != (443 if parsed.scheme.lower() == "https" else 80):
        return f"{authority_host}:{port}"
    return authority_host


def _youtube_video_id(parsed):
    host = str(parsed.hostname or "").lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    candidate = ""
    if host in {"youtu.be", "www.youtu.be"} and path_parts:
        candidate = path_parts[0]
    elif _host_is(host, "youtube.com"):
        if parsed.path.rstrip("/") == "/watch":
            candidate = dict(
                urllib.parse.parse_qsl(
                    parsed.query, keep_blank_values=True,
                )
            ).get("v", "")
        elif len(path_parts) >= 2 and path_parts[0].lower() in {
            "embed", "live", "shorts",
        }:
            candidate = path_parts[1]
    if re.fullmatch(r"[A-Za-z0-9_-]{6,64}", candidate or ""):
        return candidate
    return ""


def _canonical_query(query):
    """Normalize a page query without discarding provider parameters."""
    try:
        pairs = urllib.parse.parse_qsl(
            str(query or ""), keep_blank_values=True, strict_parsing=False,
        )
    except ValueError:
        return ""
    filtered = []
    for key, value in pairs:
        key_text = str(key or "")
        key_lower = key_text.lower()
        if (
            key_lower.startswith(("utm_", "x-amz-", "x-goog-"))
            or key_lower in _TRACKING_QUERY_KEYS
            or key_lower in _VOLATILE_QUERY_KEYS
        ):
            continue
        filtered.append((key_text, str(value or "")))
    filtered.sort(key=lambda item: (item[0].casefold(), item[1], item[0]))
    return urllib.parse.urlencode(filtered, doseq=True)


def canonical_webpage_url(value, *, platform="", source_id="", channel=""):
    """Return a stable public page URL, never a signed delivery endpoint."""
    text = html.unescape(str(value or "").strip())
    if not text or any(
        character.isspace() or ord(character) < 0x20
        for character in text
    ):
        text = ""
    try:
        parsed = urllib.parse.urlsplit(text) if text else None
    except ValueError:
        parsed = None

    platform_key = str(platform or "").strip().lower()
    clean_id = _clean_source_id(source_id)
    clean_channel = str(channel or "").strip().lower()
    if parsed is not None and parsed.scheme.lower() in {"http", "https"}:
        host = str(parsed.hostname or "").lower()
        if _host_is(host, "twitch.tv") or _host_is(host, "ttvnw.net"):
            vod_match = _TWITCH_VOD_RE.search(parsed.path)
            if vod_match:
                return f"https://www.twitch.tv/videos/{vod_match.group(1)}"
            live_match = _TWITCH_LIVE_RE.search(parsed.path)
            if live_match:
                return f"https://www.twitch.tv/{live_match.group(1).lower()}"
            path_parts = [part for part in parsed.path.split("/") if part]
            if (
                _host_is(host, "twitch.tv")
                and len(path_parts) == 1
                and _SAFE_CHANNEL_RE.fullmatch(path_parts[0])
            ):
                return f"https://www.twitch.tv/{path_parts[0].lower()}"

        if _host_is(host, "kick.com"):
            path_parts = [part for part in parsed.path.split("/") if part]
            vod_match = _KICK_VOD_RE.search(parsed.path)
            if vod_match:
                vod_id = vod_match.group(1)
                if len(path_parts) >= 3 and path_parts[1].lower() in {
                    "video", "videos", "vod", "vods",
                }:
                    return (
                        f"https://kick.com/{path_parts[0].lower()}"
                        f"/videos/{vod_id}"
                    )
                return f"https://kick.com/video/{vod_id}"
            if len(path_parts) == 1 and _SAFE_CHANNEL_RE.fullmatch(
                path_parts[0]
            ):
                return f"https://kick.com/{path_parts[0].lower()}"

        if _host_is(host, "rumble.com"):
            match = _RUMBLE_ID_RE.search(parsed.path)
            if match:
                return f"https://rumble.com/{match.group(1).lower()}"

        if _host_is(host, "reddit.com"):
            match = _REDDIT_POST_RE.search(parsed.path)
            if match:
                return (
                    f"https://www.reddit.com/r/{match.group(1)}"
                    f"/comments/{match.group(2).lower()}"
                )

        youtube_id = _youtube_video_id(parsed)
        if youtube_id:
            return f"https://www.youtube.com/watch?v={youtube_id}"

    if (
        "twitch" in platform_key
        and (parsed is None or parsed.scheme.lower() not in {"http", "https"})
    ):
        if clean_id.startswith("vod:") and clean_id[4:].isdigit():
            return f"https://www.twitch.tv/videos/{clean_id[4:]}"
        if clean_id.startswith("channel:"):
            clean_channel = clean_id.split(":", 1)[1].lower()
        if _SAFE_CHANNEL_RE.fullmatch(clean_channel):
            return f"https://www.twitch.tv/{clean_channel}"

    if (
        "kick" in platform_key
        and (parsed is None or parsed.scheme.lower() not in {"http", "https"})
    ):
        if clean_id.startswith("vod:") and clean_id[4:]:
            return f"https://kick.com/video/{clean_id[4:]}"
        if clean_id.startswith("channel:"):
            clean_channel = clean_id.split(":", 1)[1].lower()
        if _SAFE_CHANNEL_RE.fullmatch(clean_channel):
            return f"https://kick.com/{clean_channel}"

    if "rumble" in platform_key and clean_id.startswith("video:"):
        rumble_id = clean_id.split(":", 1)[1].lower()
        if _RUMBLE_ID_RE.fullmatch(rumble_id):
            return f"https://rumble.com/{rumble_id}"

    if parsed is None or parsed.scheme.lower() not in {"http", "https"}:
        return ""
    authority = _safe_authority(parsed)
    if not authority:
        return ""
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    lower_path = path.lower().rstrip("/")
    if lower_path.endswith(_MEDIA_SUFFIXES) or lower_path.rsplit("/", 1)[-1] in {
        "manifest", "playlist",
    }:
        return ""
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(), authority, path, _canonical_query(parsed.query), "",
    ))


def _derived_identity(value, platform="", channel=""):
    text = html.unescape(str(value or "").strip())
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        host = str(parsed.hostname or "").lower()
        if _host_is(host, "twitch.tv") or _host_is(host, "ttvnw.net"):
            match = _TWITCH_VOD_RE.search(parsed.path)
            if match:
                return f"vod:{match.group(1)}"
            match = _TWITCH_LIVE_RE.search(parsed.path)
            if match:
                return f"channel:{match.group(1).lower()}"
            path_parts = [part for part in parsed.path.split("/") if part]
            if (
                _host_is(host, "twitch.tv")
                and len(path_parts) == 1
                and _SAFE_CHANNEL_RE.fullmatch(path_parts[0])
            ):
                return f"channel:{path_parts[0].lower()}"
        youtube_id = _youtube_video_id(parsed)
        if youtube_id:
            return youtube_id
        host_lower = host.lower()
        if _host_is(host_lower, "kick.com"):
            match = _KICK_VOD_RE.search(parsed.path)
            if match:
                return f"vod:{match.group(1)}"
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) == 1 and _SAFE_CHANNEL_RE.fullmatch(parts[0]):
                return f"channel:{parts[0].lower()}"
        if _host_is(host_lower, "rumble.com"):
            match = _RUMBLE_ID_RE.search(parsed.path)
            if match:
                return f"video:{match.group(1).lower()}"
        reddit_match = _REDDIT_POST_RE.search(parsed.path)
        if reddit_match and _host_is(host_lower, "reddit.com"):
            return f"post:{reddit_match.group(2).lower()}"
        if _host_is(host_lower, "soundcloud.com"):
            canonical = canonical_webpage_url(text, platform="SoundCloud")
            if canonical:
                import hashlib
                return "track:" + hashlib.sha256(
                    canonical.encode("utf-8")
                ).hexdigest()
        if _host_is(host_lower, "audius.co"):
            canonical = canonical_webpage_url(text, platform="Audius")
            if canonical:
                import hashlib
                return "track:" + hashlib.sha256(
                    canonical.encode("utf-8")
                ).hexdigest()
    if "twitch" in str(platform or "").lower():
        if text.isdigit():
            return f"vod:{text}"
    return ""


def build_archival_provenance(
    stream_info=None,
    vod_info=None,
    *,
    source_url="",
):
    """Build stable archival identity without retaining transport material."""
    platform = scrub_public_text(
        getattr(stream_info, "platform", "")
        or getattr(vod_info, "platform", "")
        or ""
    ).strip()
    channel = str(
        getattr(stream_info, "channel", "")
        or getattr(vod_info, "channel", "")
        or ""
    ).strip()
    explicit_id = (
        getattr(stream_info, "source_id", "")
        or getattr(vod_info, "source_id", "")
        or ""
    )
    source_id = _clean_source_id(explicit_id)
    if "twitch" in platform.lower() and source_id.isdigit():
        source_id = f"vod:{source_id}"

    candidates = [
        getattr(stream_info, "webpage_url", ""),
        getattr(vod_info, "webpage_url", ""),
        source_url,
        getattr(vod_info, "source", ""),
        getattr(stream_info, "url", ""),
    ]
    if not source_id:
        for candidate in candidates:
            source_id = _clean_source_id(
                _derived_identity(candidate, platform, channel)
            )
            if source_id:
                break
    if not source_id and "twitch" in platform.lower():
        clean_channel = channel.lower()
        if _SAFE_CHANNEL_RE.fullmatch(clean_channel):
            source_id = f"channel:{clean_channel}"

    webpage_url = ""
    for candidate in candidates:
        webpage_url = canonical_webpage_url(
            candidate,
            platform=platform,
            source_id=source_id,
            channel=channel,
        )
        if webpage_url:
            break
    if not webpage_url:
        webpage_url = canonical_webpage_url(
            "",
            platform=platform,
            source_id=source_id,
            channel=channel,
        )
    return ArchivalProvenance(platform, source_id, webpage_url)


def _is_sensitive_field(key):
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").lower()).strip("_")
    return (
        normalized in _SENSITIVE_FIELDS
        or normalized.endswith((
            "_token",
            "_secret",
            "_signature",
            "_cookie",
            "_password",
            "_credential",
            "_authorization",
            "_api_key",
            "_headers",
        ))
        or normalized.startswith(("x_amz_", "x_goog_"))
    )


def _scrub_url_match(match):
    value = match.group(0)
    trailing = ""
    while value and value[-1] in ".,);]}":
        trailing = value[-1] + trailing
        value = value[:-1]
    try:
        parsed = urllib.parse.urlsplit(html.unescape(value))
        redaction = (
            " [***REDACTED***]"
            if parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            else ""
        )
        authority = _safe_authority(parsed)
        if not authority:
            return "[private URL removed]" + redaction + trailing
        value = urllib.parse.urlunsplit((
            parsed.scheme.lower(), authority, parsed.path or "/", "", "",
        ))
    except ValueError:
        return "[invalid URL removed]" + trailing
    return value + redaction + trailing


def scrub_public_text(value):
    """Remove credentials and signed query material from shareable text."""
    text = _PUBLIC_URL_RE.sub(_scrub_url_match, str(value or ""))
    text = re.sub(
        r"(?im)^[ \t]*(?:authorization|proxy-authorization|cookie|"
        r"set-cookie)[ \t]*:.*(?:\r?\n|$)",
        "***REDACTED***\n",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+\S+", "***REDACTED***", text)
    text = re.sub(
        r"(?i)\b(?:access[_-]?token|refresh[_-]?token|oauth[_-]?token|"
        r"token|sig|signature|cookie|authorization|credential|"
        r"x-amz-[a-z-]+|x-goog-[a-z-]+|api[_-]?key|secret)"
        r"\s*[=:]\s*[\"']?[^\"'\s,;<>&]+[\"']?",
        "***REDACTED***",
        text,
    )
    return text


def scrub_public_data(value, *, _depth=0):
    """Return a bounded, recursively scrubbed public representation."""
    if _depth > 32:
        return None
    if isinstance(value, dict):
        cleaned = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100_000:
                break
            if _is_sensitive_field(key):
                continue
            cleaned[scrub_public_text(key)] = scrub_public_data(
                item, _depth=_depth + 1
            )
        return cleaned
    if isinstance(value, list):
        return [
            scrub_public_data(item, _depth=_depth + 1)
            for item in value[:100_000]
        ]
    if isinstance(value, tuple):
        return [
            scrub_public_data(item, _depth=_depth + 1)
            for item in value[:100_000]
        ]
    if isinstance(value, str):
        return scrub_public_text(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub_public_text(value)


def _podcast_text(value, limit=4096):
    return scrub_public_text(str(value or ""))[:limit]


def _podcast_url(value):
    text = _podcast_text(value, 2048)
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def _podcast_int(value):
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def normalize_podcast_metadata(value):
    """Keep the bounded, public Podcasting 2.0 item contract.

    ``podcast:value`` is deliberately retained as declaration data, including
    its raw XML, but no StreamKeep code interprets it as a payment instruction.
    """
    raw = value if isinstance(value, dict) else {}
    payload = {}
    for key in ("guid", "podcast_guid", "medium"):
        text = _podcast_text(raw.get(key, ""), 512)
        if text:
            payload[key] = text

    for key in ("season", "episode"):
        row = raw.get(key)
        if isinstance(row, dict):
            clean = {}
            for field in ("number", "display", "name"):
                if field in row and row.get(field) not in (None, ""):
                    item = row.get(field)
                    clean[field] = (
                        _podcast_text(item, 256)
                        if field in {"display", "name"}
                        else item
                    )
            if clean:
                payload[key] = clean

    people = []
    for row in raw.get("person", []) if isinstance(raw.get("person", []), list) else []:
        if not isinstance(row, dict):
            continue
        name = _podcast_text(row.get("name", ""), 256)
        if not name:
            continue
        people.append({
            "name": name,
            **{
                field: _podcast_text(row.get(field, ""), 512)
                for field in ("role", "group")
                if row.get(field) not in (None, "")
            },
            **{
                field: _podcast_url(row.get(field, ""))
                for field in ("img", "href")
                if row.get(field)
            },
        })
    if people:
        payload["person"] = people[:256]

    soundbites = []
    raw_soundbites = raw.get("soundbite", [])
    if isinstance(raw_soundbites, list):
        for row in raw_soundbites[:256]:
            if not isinstance(row, dict):
                continue
            try:
                start = max(0.0, float(row.get("start_time", 0) or 0))
                duration = max(0.0, float(row.get("duration", 0) or 0))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(start) or not math.isfinite(duration):
                continue
            soundbites.append({
                "title": _podcast_text(row.get("title", ""), 256),
                "start_time": start,
                "duration": duration,
            })
    if soundbites:
        payload["soundbite"] = soundbites

    funding = []
    raw_funding = raw.get("funding", [])
    if isinstance(raw_funding, list):
        for row in raw_funding[:256]:
            if not isinstance(row, dict):
                continue
            url = _podcast_url(row.get("url", ""))
            if url:
                funding.append({
                    "text": _podcast_text(row.get("text", ""), 256),
                    "url": url,
                })
    if funding:
        payload["funding"] = funding

    license_row = raw.get("license")
    if isinstance(license_row, dict):
        payload["license"] = {
            "name": _podcast_text(license_row.get("name", ""), 256),
            "url": _podcast_url(license_row.get("url", "")),
        }

    locations = []
    raw_locations = raw.get("location", [])
    if isinstance(raw_locations, list):
        for row in raw_locations[:256]:
            if not isinstance(row, dict):
                continue
            name = _podcast_text(row.get("name", ""), 256)
            if not name:
                continue
            locations.append({
                "name": name,
                **{
                    field: _podcast_text(row.get(field, ""), 512)
                    for field in ("rel", "geo", "osm", "country")
                    if row.get(field) not in (None, "")
                },
            })
    if locations:
        payload["location"] = locations

    txt_rows = []
    raw_txt = raw.get("txt", [])
    if isinstance(raw_txt, list):
        for row in raw_txt[:256]:
            if not isinstance(row, dict):
                continue
            text = _podcast_text(row.get("value", ""), 4096)
            if text:
                txt_rows.append({
                    "value": text,
                    "purpose": _podcast_text(row.get("purpose", ""), 256),
                })
    if txt_rows:
        payload["txt"] = txt_rows

    values = []
    raw_values = raw.get("value", [])
    if isinstance(raw_values, (dict, str)):
        raw_values = [raw_values]
    if isinstance(raw_values, list):
        for row in raw_values[:64]:
            if isinstance(row, str):
                values.append({"raw_xml": row[:65536]})
                continue
            if not isinstance(row, dict):
                continue
            clean = {
                field: _podcast_text(row.get(field, ""), 512)
                for field in ("type", "method", "suggested")
                if row.get(field) not in (None, "")
            }
            recipients = []
            for recipient in row.get("recipients", []) if isinstance(row.get("recipients", []), list) else []:
                if not isinstance(recipient, dict):
                    continue
                recipients.append({
                    field: _podcast_text(recipient.get(field, ""), 1024)
                    for field in (
                        "name", "address", "type", "split", "fee",
                        "custom_key", "custom_value",
                    )
                    if recipient.get(field) not in (None, "")
                })
            if recipients:
                clean["recipients"] = recipients[:256]
            if row.get("raw_xml"):
                # Keep this exact public declaration text; it is data only.
                clean["raw_xml"] = str(row.get("raw_xml"))[:65536]
            if clean:
                values.append(clean)
    if values:
        payload["value"] = values

    alternates = []
    raw_alternates = raw.get("alternate_enclosures", [])
    if isinstance(raw_alternates, list):
        for row in raw_alternates[:256]:
            if not isinstance(row, dict):
                continue
            sources = []
            for source in row.get("sources", []) if isinstance(row.get("sources", []), list) else []:
                if not isinstance(source, dict):
                    continue
                uri = _podcast_url(source.get("uri", ""))
                if uri:
                    sources.append({
                        "uri": uri,
                        "content_type": _podcast_text(source.get("content_type", ""), 256),
                    })
            if not sources:
                continue
            clean = {
                "type": _podcast_text(row.get("type", ""), 256),
                "length": _podcast_int(row.get("length", 0)),
                "bitrate": _podcast_int(row.get("bitrate", 0)),
                "height": _podcast_int(row.get("height", 0)),
                "lang": _podcast_text(row.get("lang", ""), 64),
                "title": _podcast_text(row.get("title", ""), 256),
                "rel": _podcast_text(row.get("rel", ""), 256),
                "codecs": _podcast_text(row.get("codecs", ""), 256),
                "default": bool(row.get("default", False)),
                "sources": sources,
            }
            integrity = row.get("integrity")
            if isinstance(integrity, dict):
                clean["integrity"] = {
                    "type": _podcast_text(integrity.get("type", ""), 64),
                    "value": _podcast_text(integrity.get("value", ""), 4096),
                }
            alternates.append(clean)
    if alternates:
        payload["alternate_enclosures"] = alternates

    artwork = []
    raw_artwork = raw.get("artwork", [])
    if isinstance(raw_artwork, list):
        for row in raw_artwork[:256]:
            if not isinstance(row, dict):
                continue
            href = _podcast_url(row.get("href", ""))
            if not href:
                continue
            artwork.append({
                "href": href,
                "alt": _podcast_text(row.get("alt", ""), 512),
                "aspect_ratio": _podcast_text(row.get("aspect_ratio", ""), 64),
                "width": _podcast_int(row.get("width", 0)),
                "height": _podcast_int(row.get("height", 0)),
                "type": _podcast_text(row.get("type", ""), 256),
                "purpose": _podcast_text(row.get("purpose", ""), 256),
            })
    if artwork:
        payload["artwork"] = artwork

    sidecars = []
    raw_sidecars = raw.get("sidecars", [])
    if isinstance(raw_sidecars, list):
        for row in raw_sidecars[:256]:
            if not isinstance(row, dict):
                continue
            url = _podcast_url(row.get("url", ""))
            if not url:
                continue
            sidecars.append({
                "kind": _podcast_text(row.get("kind", ""), 32),
                "url": url,
                "type": _podcast_text(row.get("type", ""), 256),
                "language": _podcast_text(row.get("language", ""), 64),
                "rel": _podcast_text(row.get("rel", ""), 64),
            })
    if sidecars:
        payload["sidecars"] = sidecars

    verification = raw.get("integrity_verification")
    if isinstance(verification, list):
        payload["integrity_verification"] = scrub_public_data(verification[:256])
    return payload


def _safe_quality_rows(value):
    rows = []
    for item in list(value or [])[:500]:
        if isinstance(item, dict):
            get = item.get
        else:
            def get(name, default=None, row=item):
                return getattr(row, name, default)
        try:
            bandwidth = int(float(get("bandwidth", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            bandwidth = 0
        rows.append({
            "name": scrub_public_text(get("name", "") or ""),
            "resolution": scrub_public_text(get("resolution", "") or ""),
            "bandwidth": max(0, bandwidth),
            "format": scrub_public_text(
                get("format", "") or get("format_type", "") or ""
            ),
        })
    return rows


def normalize_metadata_payload(data):
    """Migrate legacy metadata into the current public sidecar schema."""
    raw = data if isinstance(data, dict) else {}
    raw_provenance = raw.get("provenance")
    raw_provenance = raw_provenance if isinstance(raw_provenance, dict) else {}
    platform = scrub_public_text(
        raw_provenance.get("platform") or raw.get("platform") or ""
    ).strip()
    source_id = _clean_source_id(
        raw_provenance.get("source_id") or raw.get("source_id") or ""
    )
    channel = scrub_public_text(
        raw.get("channel") or raw.get("vod_channel") or ""
    )
    legacy_url = (
        raw_provenance.get("webpage_url")
        or raw.get("webpage_url")
        or raw.get("url")
        or ""
    )
    if not source_id:
        source_id = _clean_source_id(
            _derived_identity(legacy_url, platform, channel)
        )
    webpage_url = canonical_webpage_url(
        legacy_url,
        platform=platform,
        source_id=source_id,
        channel=channel,
    )
    provenance = ArchivalProvenance(platform, source_id, webpage_url)
    archive_key = scrub_public_text(raw.get("archive_key", "")).strip()
    if not archive_key:
        archive_key = archive_key_for_provenance(provenance)
    try:
        parsed_total_secs = float(raw.get("total_secs", 0) or 0)
        total_secs = (
            parsed_total_secs
            if 0 <= parsed_total_secs < float("inf")
            else 0
        )
    except (TypeError, ValueError, OverflowError):
        total_secs = 0
    payload = {
        "schema": METADATA_SCHEMA,
        "schema_version": METADATA_SCHEMA_VERSION,
        "provenance": provenance.to_dict(),
        "platform": platform,
        "source_id": provenance.source_id,
        "webpage_url": provenance.webpage_url,
        "archive_key": archive_key,
        "channel": channel,
        "title": scrub_public_text(raw.get("title", "") or ""),
        "duration": scrub_public_text(raw.get("duration", "") or ""),
        "total_secs": total_secs,
        "start_time": scrub_public_text(raw.get("start_time", "") or ""),
        "is_live": bool(raw.get("is_live", False)),
        "qualities": _safe_quality_rows(raw.get("qualities", [])),
        "downloaded_at": scrub_public_text(raw.get("downloaded_at", "") or ""),
        "tags": _safe_tag_rows(raw.get("tags", [])),
    }
    for key in ("vod_date", "vod_channel", "quality"):
        if key in raw:
            payload[key] = scrub_public_data(raw.get(key))
    if "vod_viewers" in raw:
        try:
            payload["vod_viewers"] = max(
                0, int(raw.get("vod_viewers", 0) or 0)
            )
        except (TypeError, ValueError, OverflowError):
            payload["vod_viewers"] = 0
    thumbnail = str(raw.get("thumbnail", "") or "")
    if thumbnail == "thumbnail.jpg":
        payload["thumbnail"] = thumbnail
    podcast = normalize_podcast_metadata(
        raw.get("podcast", raw.get("podcast_metadata", {}))
    )
    if podcast:
        payload["podcast"] = podcast
    return payload


def load_metadata_sidecar(path_or_dir):
    """Read current or legacy metadata and return a safe current payload."""
    path = Path(path_or_dir)
    if path.is_dir():
        path = path / "metadata.json"
    try:
        if not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return normalize_metadata_payload(data)


def load_ytdlp_info_sidecar(path_or_dir):
    """Read the bounded, public fields needed to adopt a yt-dlp sidecar."""
    path = Path(path_or_dir)
    if path.is_dir():
        return {}
    try:
        if not path.is_file() or path.stat().st_size > MAX_IMPORT_SIDECAR_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    fields = (
        "id", "display_id", "webpage_url", "original_url", "extractor",
        "extractor_key", "title", "channel", "channel_id", "uploader",
        "uploader_id", "upload_date", "timestamp", "duration",
        "duration_string", "format_note", "resolution", "height",
        "webpage_url_domain",
    )
    return {
        key: scrub_public_data(data.get(key))
        for key in fields if key in data
    }


def load_nfo_sidecar(path_or_dir):
    """Read safe identity/title fields from a Kodi/Jellyfin NFO sidecar."""
    path = Path(path_or_dir)
    if path.is_dir():
        return {}
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_IMPORT_SIDECAR_BYTES:
            return {}
        root = ET.fromstring(raw)
    except (OSError, ValueError, ET.ParseError):
        return {}
    values = {}
    for element in root.iter():
        tag = str(element.tag).rsplit("}", 1)[-1].lower()
        text = scrub_public_text(element.text or "").strip()
        if tag == "uniqueid" and text and "source_id" not in values:
            values["source_id"] = text
            values["uniqueid_type"] = scrub_public_text(
                element.attrib.get("type", "")
            ).strip()
        elif tag in {"title", "studio", "director", "credits", "premiered", "url"}:
            if text and tag not in values:
                values[tag] = text
    return values


def _atomic_write_text(path, text):
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except OSError as error:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise MetadataWriteError(
            f"Could not write public sidecar {target.name}: {error}"
        ) from error
    return str(target)


class MetadataSaver:
    @staticmethod
    def save_thumbnail(output_dir, stream_info):
        """Download a public local thumbnail without persisting its source URL."""
        if not output_dir or not os.path.isdir(output_dir):
            raise MetadataWriteError("Recording directory does not exist")
        target = os.path.join(output_dir, "thumbnail.jpg")
        remote = str(getattr(stream_info, "thumbnail_url", "") or "")
        if remote:
            from .image_fetch import download_image
            if download_image(remote, target):
                return target
        return target if os.path.isfile(target) else ""

    @staticmethod
    def save(output_dir, stream_info, vod_info=None, *, source_url=""):
        """Atomically save a versioned, credential-free metadata sidecar."""
        if stream_info is None:
            raise MetadataWriteError("Stream metadata is unavailable")
        if not output_dir or not os.path.isdir(output_dir):
            raise MetadataWriteError("Recording directory does not exist")
        thumbnail_path = MetadataSaver.save_thumbnail(output_dir, stream_info)
        provenance = build_archival_provenance(
            stream_info, vod_info, source_url=source_url,
        )
        raw = {
            "provenance": provenance.to_dict(),
            "platform": provenance.platform,
            "channel": getattr(stream_info, "channel", "") or "",
            "title": (
                getattr(stream_info, "title", "")
                or (getattr(vod_info, "title", "") if vod_info else "")
            ),
            "duration": getattr(stream_info, "duration_str", "") or "",
            "total_secs": getattr(stream_info, "total_secs", 0) or 0,
            "start_time": getattr(stream_info, "start_time", "") or "",
            "is_live": bool(getattr(stream_info, "is_live", False)),
            "qualities": list(getattr(stream_info, "qualities", []) or []),
            "downloaded_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "archive_key": archive_key_for_provenance(provenance),
            "tags": list(getattr(stream_info, "tags", []) or []),
        }
        if thumbnail_path:
            raw["thumbnail"] = os.path.basename(thumbnail_path)
        if vod_info:
            raw["vod_date"] = getattr(vod_info, "date", "") or ""
            raw["vod_channel"] = getattr(vod_info, "channel", "") or ""
            raw["vod_viewers"] = getattr(vod_info, "viewers", 0) or 0
        podcast_metadata = getattr(stream_info, "podcast_metadata", None)
        if podcast_metadata:
            raw["podcast"] = podcast_metadata
        payload = normalize_metadata_payload(raw)
        metadata_path = _atomic_write_text(
            os.path.join(output_dir, "metadata.json"),
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )
        return {
            "metadata_path": metadata_path,
            "thumbnail_path": thumbnail_path,
            "provenance": provenance,
        }

    @staticmethod
    def update_podcast_integrity(output_dir, verification):
        """Persist publisher-hash verification into the library sidecar."""
        path = Path(output_dir or "") / "metadata.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ""
        payload = normalize_metadata_payload(raw)
        podcast = dict(payload.get("podcast") or {})
        if not podcast:
            return ""
        podcast["integrity_verification"] = scrub_public_data(
            list(verification or [])[:256]
        )
        payload["podcast"] = podcast
        return _atomic_write_text(
            str(path), json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        )

    @staticmethod
    def write_chapters(output_dir, stream_info, file_base=""):
        """Write chapter text/JSON sidecars and surface local write failures."""
        if not stream_info or not output_dir or not os.path.isdir(output_dir):
            return False
        chapters = getattr(stream_info, "chapters", None) or []
        if not chapters:
            return False
        base = os.path.basename(file_base) if file_base else "chapters"
        clean_chapters = []
        text_lines = []
        for chapter in chapters:
            try:
                start = float(chapter.get("start", 0) or 0)
                end = float(chapter.get("end", 0) or 0)
            except (TypeError, ValueError):
                start, end = 0.0, 0.0
            title = scrub_public_text(chapter.get("title", "Chapter"))
            clean_chapters.append({
                "title": title,
                "start": start,
                "end": end,
            })
            secs = int(start)
            hh = secs // 3600
            mm = (secs % 3600) // 60
            ss = secs % 60
            text_lines.append(f"{hh:02d}:{mm:02d}:{ss:02d} {title}")
        _atomic_write_text(
            os.path.join(output_dir, f"{base}.chapters.txt"),
            "\n".join(text_lines) + "\n",
        )
        _atomic_write_text(
            os.path.join(output_dir, f"{base}.chapters.json"),
            json.dumps(
                {"chapters": clean_chapters}, indent=2, ensure_ascii=False,
            ) + "\n",
        )
        return True

    @staticmethod
    def write_hls_markers(
        output_dir, markers, schedules=(), file_base="",
    ):
        """Write public-safe HLS DATERANGE and schedule metadata.

        Interstitial assets are represented as marker rows only. Their URIs
        are never promoted to media inputs, and query material is scrubbed in
        the sidecar just like the rest of the public metadata surface.
        """
        if not output_dir or not os.path.isdir(output_dir):
            return False
        marker_rows = list(markers or [])[:10_000]
        schedule_rows = []
        for item in list(schedules or [])[:256]:
            if isinstance(item, dict):
                uri = item.get("uri", "")
                body = item.get("body", "")
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                uri, body = item[0], item[1]
            else:
                continue
            row = {"uri": scrub_public_text(uri)}
            text_body = str(body or "")
            try:
                decoded = json.loads(text_body)
            except (TypeError, ValueError):
                row["body"] = scrub_public_text(text_body[:MAX_IMPORT_SIDECAR_BYTES])
            else:
                row["payload"] = scrub_public_data(decoded)
            schedule_rows.append(row)
        if not marker_rows and not schedule_rows:
            return False
        payload = {
            "schema": "streamkeep.hls-markers",
            "schema_version": 1,
            "markers": scrub_public_data(marker_rows),
            "schedules": schedule_rows,
        }
        base = os.path.basename(file_base) if file_base else "hls"
        if not base or base in {".", ".."}:
            base = "hls"
        _atomic_write_text(
            os.path.join(output_dir, f"{base}.markers.json"),
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )
        return True

    @staticmethod
    def write_markers(output_dir, markers, schedules=(), file_base=""):
        """Compatibility alias for generic marker-sidecar callers."""
        return MetadataSaver.write_hls_markers(
            output_dir, markers, schedules=schedules, file_base=file_base,
        )

    @staticmethod
    def _xml_escape(value):
        if not value:
            return ""
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    @staticmethod
    def write_nfo(
        output_dir,
        stream_info,
        vod_info=None,
        file_base="",
        *,
        source_url="",
    ):
        """Write a local-only Kodi/Jellyfin NFO with stable source identity."""
        if stream_info is None:
            raise MetadataWriteError("Stream metadata is unavailable")
        if not output_dir or not os.path.isdir(output_dir):
            raise MetadataWriteError("Recording directory does not exist")
        title = (
            getattr(stream_info, "title", "")
            or (getattr(vod_info, "title", "") if vod_info else "")
        ).strip() or "Untitled"
        provenance = build_archival_provenance(
            stream_info, vod_info, source_url=source_url,
        )
        channel = (
            getattr(vod_info, "channel", "") if vod_info else ""
        ) or getattr(stream_info, "channel", "") or ""
        date_str = ""
        start_time = getattr(stream_info, "start_time", "") or ""
        vod_date = getattr(vod_info, "date", "") if vod_info else ""
        try:
            if start_time:
                date_str = start_time.split("T")[0]
            elif vod_date:
                date_str = vod_date.split("T")[0].split(" ")[0]
        except (AttributeError, IndexError):
            date_str = ""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            date_str = ""
        runtime_min = int(
            (getattr(stream_info, "total_secs", 0) or 0) // 60
        )

        esc = MetadataSaver._xml_escape
        lines = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            "<movie>",
            f"  <title>{esc(scrub_public_text(title))}</title>",
            f"  <originaltitle>{esc(scrub_public_text(title))}</originaltitle>",
            f"  <studio>{esc(provenance.platform)}</studio>",
        ]
        if provenance.source_id:
            source_type = re.sub(
                r"[^a-z0-9_-]+", "-", provenance.platform.lower(),
            ).strip("-") or "streamkeep"
            lines.append(
                f'  <uniqueid type="{esc(source_type)}" default="true">'
                f"{esc(provenance.source_id)}</uniqueid>"
            )
        if channel:
            lines.append(
                f"  <director>{esc(scrub_public_text(channel))}</director>"
            )
            lines.append(
                f"  <credits>{esc(scrub_public_text(channel))}</credits>"
            )
        if date_str:
            lines.append(f"  <premiered>{esc(date_str)}</premiered>")
            lines.append(f"  <year>{esc(date_str[:4])}</year>")
        if runtime_min > 0:
            lines.append(f"  <runtime>{runtime_min}</runtime>")
        if os.path.isfile(os.path.join(output_dir, "thumbnail.jpg")):
            lines.append("  <thumb>thumbnail.jpg</thumb>")
        lines.append(
            f"  <plot>Archived from {esc(provenance.platform)} on "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.</plot>"
        )
        lines.append("</movie>")
        safe_base = os.path.basename(file_base) if file_base else ""
        nfo_path = os.path.join(
            output_dir,
            (safe_base + ".nfo") if safe_base else "movie.nfo",
        )
        return _atomic_write_text(nfo_path, "\n".join(lines) + "\n")
