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
import hashlib
import os
import re
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


def normalize_replay_headers(raw_headers):
    """Return the bounded browser handoff header allowlist.

    The extension sends ``webRequest``'s list-of-dicts shape while REST/HAR
    callers commonly use a mapping.  Normalize both forms through the same
    control-character and size checks used by HAR import.  Hop-by-hop,
    pseudo, and browser-only headers are intentionally discarded: yt-dlp
    only needs the site-bound replay context represented by
    :data:`_REPLAY_HEADERS`.
    """
    if isinstance(raw_headers, dict):
        raw_headers = [
            {"name": name, "value": value}
            for name, value in raw_headers.items()
        ]
    if not isinstance(raw_headers, list):
        return {}
    return _replay_headers(raw_headers)


def merge_stream_headers(handoff, info):
    """Combine browser-handoff headers with the ones a stream needs.

    An extractor can report headers its origin requires in order to serve the
    manifest and its segments (``StreamInfo.http_headers``). Those are a
    property of the source, so a per-URL handoff captured from the browser
    wins where the two disagree — the user's own capture is the more specific
    statement about that request.
    """
    merged = dict(normalize_replay_headers(getattr(info, "http_headers", {})))
    merged.update(normalize_replay_headers(handoff))
    return merged


def replay_header_argv(headers):
    """Return safe ``--add-header`` argv for a normalized handoff mapping."""
    argv = []
    for name, value in normalize_replay_headers(headers).items():
        argv.extend(["--add-header", f"{name}: {value}"])
    return argv


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


def _draft_url(url):
    """Return a credential-free HTTP(S) URL suitable for a saved draft."""
    try:
        parsed = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.rstrip(".").lower()
    url_host = f"[{host}]" if ":" in host else host
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port:
        url_host = f"{url_host}:{port}"
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(), url_host, parsed.path or "/", "", "",
    ))


def _draft_adapter_id(value, source_host, links):
    requested = str(value or "").strip().lower()
    if requested:
        slug = re.sub(r"[^a-z0-9._-]+", "-", requested).strip(".-")
    else:
        host_slug = re.sub(r"[^a-z0-9]+", "-", source_host).strip("-")
        digest = hashlib.sha256(
            "\n".join(str(link.get("url", "")) for link in links).encode("utf-8")
        ).hexdigest()[:8]
        slug = f"har-{host_slug}-{digest}"
    if not slug:
        raise ValueError("Adapter id must contain a letter or number")
    return slug[:128]


def _draft_format_type(link):
    path = urllib.parse.urlsplit(str(link.get("url", ""))).path.lower()
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mpd"):
        return "dash"
    return "mp4"


def source_adapter_draft(data, *, adapter_id="", name="", include_segments=False):
    """Author a safe, inert-until-reviewed source adapter from a HAR capture.

    HAR query values, cookies, authorization headers, fragments, and user-info
    are never persisted in the YAML. The result is deliberately a draft: it
    uses the capture's page/manifest paths and may need edited selectors or
    query parameters before the operator approves its outbound contract.
    """
    links = parse_har(data, include_segments=include_segments)
    if not links:
        raise ValueError("HAR capture has no media request to draft an adapter from")

    source_url = ""
    for link in links:
        source_url = _draft_url((link.get("headers") or {}).get("Referer", ""))
        if source_url:
            break
    if not source_url:
        source_url = _draft_url(links[0].get("url", ""))
    parsed_source = urllib.parse.urlsplit(source_url)
    source_host = str(parsed_source.hostname or "").lower()
    if not source_host:
        raise ValueError("HAR capture has no usable HTTP(S) source URL")

    qualities = []
    quality_hosts = set()
    seen_urls = set()
    for link in links:
        media_url = _draft_url(link.get("url", ""))
        if not media_url or media_url in seen_urls:
            continue
        media_host = urllib.parse.urlsplit(media_url).hostname or ""
        if media_host not in quality_hosts and len(quality_hosts) >= 8:
            continue
        quality_hosts.add(media_host)
        seen_urls.add(media_url)
        kind = _draft_format_type(link)
        qualities.append({
            "name": f"captured-{kind}-{len(qualities) + 1}",
            "url": f"literal:{media_url}",
            "format_type": kind,
        })
        if len(qualities) >= 32:
            break
    if not qualities:
        raise ValueError("HAR capture has no credential-free media URL")

    display_host = source_host.removeprefix("www.")
    display_name = str(name or "").strip() or f"HAR draft: {display_host}"
    if len(display_name) > 128:
        display_name = display_name[:128]
    generated_id = _draft_adapter_id(adapter_id, source_host, links)
    source_path = parsed_source.path or "/"
    path_regex = f"^{re.escape(source_path)}$"
    if len(path_regex) > 512:
        path_regex = r"^/.*$"

    request_headers = {}
    first_headers = links[0].get("headers") or {}
    user_agent = first_headers.get("User-Agent", "")
    if user_agent:
        request_headers["User-Agent"] = user_agent
    referer = _draft_url(first_headers.get("Referer", ""))
    if referer:
        request_headers["Referer"] = referer

    from .declarative import serialize_definition
    return serialize_definition({
        "schema_version": 1,
        "id": generated_id,
        "name": display_name,
        "version": "0.1.0",
        "enabled": True,
        "platform": display_host,
        "direct": True,
        "match": {
            "hosts": [source_host],
            "path_regex": path_regex,
        },
        "resolve": {
            "request": {
                "url": qualities[0]["url"][8:],
                "method": "HEAD",
                "headers": request_headers,
                "timeout_seconds": 8,
                "max_response_bytes": 1024,
            },
            "response": {
                "format": "html",
                "fields": {
                    "title": f"literal:Captured media from {display_host}",
                    "webpage_url": f"literal:{source_url}",
                },
                "qualities": qualities,
            },
        },
    })
