"""HAR (HTTP Archive) import: media/manifest URLs + replay headers.

Parses a browser-exported ``.har`` capture and surfaces the media and
streaming-manifest requests as a structured link table, each carrying the
minimal subset of request headers needed to replay the download (Referer,
Origin, User-Agent, Cookie, Authorization). This lets a user capture a
protected stream in their browser's network panel and hand the exact
request context to StreamKeep without DRM circumvention.

The parser is deliberately bounded and never executes anything: it reads
JSON, classifies entries, and returns plain dicts. Header values are
control-character-checked so they can be passed to yt-dlp as ``--add-header``
argv without shell interpretation.
"""

from __future__ import annotations

import json
import os
import urllib.parse


# Streaming manifests — yt-dlp/ffmpeg only need these, not the segments.
_MANIFEST_EXTENSIONS = (".m3u8", ".mpd")
_MANIFEST_MIMES = frozenset({
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
    "application/dash+xml",
    "application/vnd.ms-sstr+xml",  # Smooth Streaming manifest
})

# Whole-file media containers worth queueing directly.
_MEDIA_EXTENSIONS = (
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".flv", ".avi",
    ".m4a", ".mp3", ".aac", ".flac", ".wav", ".opus", ".ogg", ".oga",
)
# Segment files — noise once a manifest is present.
_SEGMENT_EXTENSIONS = (".ts", ".m4s", ".m4f", ".init", ".cmfv", ".cmfa")

# Only replay-relevant request headers are carried forward. HTTP/2 pseudo
# headers (":method", ":authority", …) and everything else are dropped.
_REPLAY_HEADERS = {
    "referer": "Referer",
    "origin": "Origin",
    "user-agent": "User-Agent",
    "cookie": "Cookie",
    "authorization": "Authorization",
}

_MAX_ENTRIES = 200_000
_MAX_URL_LEN = 4096
_MAX_HEADER_VALUE_LEN = 8192


def _url_extension(url):
    """Return the lower-case path extension of ``url`` without the query."""
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return ""
    _root, ext = os.path.splitext(path)
    return ext.lower()


def _classify(url, mime):
    """Return ``"manifest"``, ``"media"``, ``"segment"``, or ``""``."""
    ext = _url_extension(url)
    mime = (mime or "").split(";", 1)[0].strip().lower()
    if ext in _MANIFEST_EXTENSIONS or mime in _MANIFEST_MIMES:
        return "manifest"
    if ext in _SEGMENT_EXTENSIONS:
        return "segment"
    if ext in _MEDIA_EXTENSIONS:
        return "media"
    if mime.startswith("video/") or mime.startswith("audio/"):
        # A generic media content type with no telltale extension.
        return "media"
    return ""


def _clean_header_value(value):
    """Return a control-free header value, or ``None`` if unusable."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > _MAX_HEADER_VALUE_LEN:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _replay_headers(raw_headers):
    """Extract the allowlisted replay headers from a HAR header list."""
    headers = {}
    if not isinstance(raw_headers, list):
        return headers
    for header in raw_headers:
        if not isinstance(header, dict):
            continue
        name = str(header.get("name", "") or "").strip()
        if not name or name.startswith(":"):
            continue  # skip HTTP/2 pseudo headers
        canonical = _REPLAY_HEADERS.get(name.lower())
        if not canonical or canonical in headers:
            continue
        value = _clean_header_value(header.get("value", ""))
        if value is not None:
            headers[canonical] = value
    return headers


def parse_har(data, *, include_segments=False):
    """Parse a HAR document into a deduplicated media/manifest link table.

    ``data`` may be a JSON string, bytes, or an already-decoded mapping.
    Returns a list of dicts ``{"url", "method", "mime", "type", "headers"}``
    ordered by first appearance. Segment URLs are collapsed away by default
    when at least one manifest is present (and always dropped when any
    manifest exists), because yt-dlp/ffmpeg reconstruct segments from the
    manifest.
    """
    if isinstance(data, (str, bytes, bytearray)):
        try:
            document = json.loads(data)
        except (ValueError, TypeError) as error:
            raise ValueError(f"Not a valid HAR/JSON document: {error}") from error
    elif isinstance(data, dict):
        document = data
    else:
        raise ValueError("HAR data must be text, bytes, or a decoded mapping")

    log = document.get("log") if isinstance(document, dict) else None
    entries = log.get("entries") if isinstance(log, dict) else None
    if not isinstance(entries, list):
        raise ValueError("HAR document has no log.entries array")

    manifests = []
    media = []
    segments = []
    seen = set()
    for entry in entries[:_MAX_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request")
        if not isinstance(request, dict):
            continue
        url = str(request.get("url", "") or "").strip()
        if not url or len(url) > _MAX_URL_LEN:
            continue
        scheme = urllib.parse.urlsplit(url).scheme.lower()
        if scheme not in ("http", "https"):
            continue
        method = str(request.get("method", "GET") or "GET").strip().upper()
        if method not in ("GET", ""):
            continue  # media is fetched with GET; ignore POST/OPTIONS/etc.

        response = entry.get("response")
        content = response.get("content") if isinstance(response, dict) else None
        mime = ""
        if isinstance(content, dict):
            mime = str(content.get("mimeType", "") or "")

        kind = _classify(url, mime)
        if not kind:
            continue
        if url in seen:
            continue
        seen.add(url)

        record = {
            "url": url,
            "method": "GET",
            "mime": mime.split(";", 1)[0].strip().lower(),
            "type": "segment" if kind == "segment" else kind,
            "headers": _replay_headers(request.get("headers")),
        }
        if kind == "manifest":
            manifests.append(record)
        elif kind == "segment":
            segments.append(record)
        else:
            media.append(record)

    links = manifests + media
    if include_segments and not manifests:
        # Only surface raw segments when the user asked and no manifest was
        # captured to reconstruct them from.
        links += segments
    return links


def har_entry_ytdlp_headers(link):
    """Return ``--add-header NAME: VALUE`` argv for one link's replay headers.

    The values were already control-checked by :func:`parse_har`, so each is
    safe to pass as a single argv element (never a shell string).
    """
    argv = []
    for name, value in (link.get("headers") or {}).items():
        argv.extend(["--add-header", f"{name}: {value}"])
    return argv
