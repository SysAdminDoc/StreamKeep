"""Discover and download Podcast Namespace transcript/chapter sidecars.

Podcast RSS feeds advertise per-episode transcripts and chapter files with
``<podcast:transcript>`` and ``<podcast:chapters>`` elements (Podcast Namespace
1.0). This module discovers those references for a given episode, downloads
them into hashed sidecars next to the recording through the shared SSRF-safe
fetch policy, skips unchanged files on refresh, and records a manifest so the
existing WebVTT / JSON-chapter parsers can consume them.

Everything here is bounded and non-fatal: malformed or absent metadata yields
an empty result rather than raising, and a failed sidecar download never blocks
the recording it accompanies.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse

from .image_fetch import ImageFetchError, fetch_url_bytes


MANIFEST_SUFFIX = ".sidecars.json"
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 20

# Podcast Namespace transcript/chapter MIME types → sidecar file extension.
_TYPE_EXTENSIONS = {
    "text/vtt": "vtt",
    "application/x-subrip": "srt",
    "application/srt": "srt",
    "text/srt": "srt",
    "text/html": "html",
    "text/plain": "txt",
    "application/json": "json",
    "application/json+chapters": "json",
    "application/json+chapters;charset=utf-8": "json",
}

_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"|([\w:-]+)\s*=\s*\'([^\']*)\'')
_TRANSCRIPT_RE = re.compile(
    r"<podcast:transcript\b([^>]*?)/?>", re.IGNORECASE | re.DOTALL
)
_CHAPTERS_RE = re.compile(
    r"<podcast:chapters\b([^>]*?)/?>", re.IGNORECASE | re.DOTALL
)
_ITEM_RE = re.compile(r"<item\b.*?</item>", re.IGNORECASE | re.DOTALL)
_ENCLOSURE_RE = re.compile(
    r"""<enclosure\b[^>]*?\burl\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)


def _parse_attrs(attr_text):
    attrs = {}
    for match in _ATTR_RE.finditer(attr_text or ""):
        name = (match.group(1) or match.group(3) or "").lower()
        value = match.group(2) if match.group(2) is not None else match.group(4)
        if name:
            attrs[name] = (value or "").strip()
    return attrs


def _extension_for(kind, type_hint, url):
    type_hint = (type_hint or "").split(";", 1)[0].strip().lower()
    if type_hint in _TYPE_EXTENSIONS:
        return _TYPE_EXTENSIONS[type_hint]
    # Fall back to the URL path extension, then a kind-appropriate default.
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        path = ""
    _root, ext = os.path.splitext(path)
    ext = ext.lstrip(".").lower()
    if ext in ("vtt", "srt", "json", "txt", "html", "sub"):
        return ext
    return "json" if kind == "chapters" else "vtt"


def parse_podcast_sidecar_refs(item_xml):
    """Parse transcript/chapter references from one ``<item>`` XML block.

    Returns a list of ``{"kind", "url", "type", "language"}`` dicts. ``kind`` is
    ``"transcript"`` or ``"chapters"``. Only HTTP(S) URLs are kept; duplicates
    (same kind + URL) are removed while preserving order.
    """
    if not isinstance(item_xml, str):
        return []
    refs = []
    seen = set()
    for kind, pattern in (("transcript", _TRANSCRIPT_RE), ("chapters", _CHAPTERS_RE)):
        for match in pattern.finditer(item_xml):
            attrs = _parse_attrs(match.group(1))
            url = (attrs.get("url") or "").strip()
            if not url:
                continue
            scheme = urllib.parse.urlsplit(url).scheme.lower()
            if scheme not in ("http", "https"):
                continue
            key = (kind, url)
            if key in seen:
                continue
            seen.add(key)
            refs.append({
                "kind": kind,
                "url": url,
                "type": (attrs.get("type") or "").strip(),
                "language": (attrs.get("language") or attrs.get("lang") or "").strip(),
            })
    return refs


def find_feed_sidecars(feed_body, enclosure_url):
    """Return the sidecar refs for the feed item matching ``enclosure_url``.

    Matches the ``<item>`` whose ``<enclosure url="...">`` equals the given
    episode URL (a normalized comparison ignoring a trailing query mismatch is
    intentionally *not* applied — the enclosure is compared verbatim, then by
    path as a fallback). Absent/malformed feeds yield ``[]``.
    """
    if not isinstance(feed_body, str) or not enclosure_url:
        return []
    target = enclosure_url.strip()
    target_path = urllib.parse.urlsplit(target).path
    fallback_item = None
    for item in _ITEM_RE.finditer(feed_body):
        block = item.group(0)
        enc = _ENCLOSURE_RE.search(block)
        if not enc:
            continue
        enc_url = enc.group(1).strip()
        if enc_url == target:
            return parse_podcast_sidecar_refs(block)
        if fallback_item is None and urllib.parse.urlsplit(enc_url).path == target_path:
            fallback_item = block
    if fallback_item is not None:
        return parse_podcast_sidecar_refs(fallback_item)
    return []


def _sidecar_filename(base_name, ref):
    kind = ref["kind"]
    ext = _extension_for(kind, ref.get("type"), ref["url"])
    lang = re.sub(r"[^A-Za-z0-9_-]", "", (ref.get("language") or "")).strip("-_")
    if kind == "chapters":
        return f"{base_name}.chapters.{ext}"
    if lang:
        return f"{base_name}.{lang}.{ext}"
    return f"{base_name}.transcript.{ext}"


def manifest_path(out_dir, base_name):
    return os.path.join(out_dir, base_name + MANIFEST_SUFFIX)


def read_manifest(out_dir, base_name):
    """Return the persisted sidecar manifest list, or ``[]``."""
    path = manifest_path(out_dir, base_name)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    entries = data.get("sidecars") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def write_manifest(out_dir, base_name, entries):
    path = manifest_path(out_dir, base_name)
    tmp = path + ".tmp"
    payload = {"version": 1, "base": base_name, "sidecars": list(entries)}
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def download_podcast_sidecars(
    refs,
    out_dir,
    base_name,
    *,
    existing=None,
    max_bytes=DEFAULT_MAX_BYTES,
    timeout=DEFAULT_TIMEOUT,
    log_fn=None,
):
    """Download each ref into a hashed sidecar next to ``base_name``.

    Reuses the shared SSRF-safe fetch. A ref whose freshly-fetched content hash
    matches an existing manifest entry (same filename) is left untouched — this
    is what makes refresh idempotent. Returns the new manifest list. Individual
    fetch failures are logged and skipped, never raised.
    """
    def _log(message):
        if log_fn:
            try:
                log_fn(message)
            except Exception:
                pass

    prior = {}
    for entry in existing or []:
        if isinstance(entry, dict) and entry.get("file"):
            prior[entry["file"]] = entry

    manifest = []
    seen_files = set()
    for ref in refs or []:
        if not isinstance(ref, dict) or not ref.get("url"):
            continue
        filename = _sidecar_filename(base_name, ref)
        if filename in seen_files:
            _log(f"[SIDECAR] Skipping duplicate target {filename}")
            continue
        try:
            data = fetch_url_bytes(
                ref["url"], max_bytes=max_bytes, timeout=timeout,
                accept="text/vtt, application/json, text/*;q=0.8, */*;q=0.5",
            )
        except (ImageFetchError, OSError) as error:
            _log(f"[SIDECAR] Skipped {ref['kind']} {ref['url']}: {error}")
            continue
        digest = hashlib.sha256(data).hexdigest()
        dest = os.path.join(out_dir, filename)
        previous = prior.get(filename)
        if (
            previous
            and previous.get("sha256") == digest
            and os.path.isfile(dest)
        ):
            _log(f"[SIDECAR] Unchanged, kept {filename}")
            manifest.append(previous)
            seen_files.add(filename)
            continue
        try:
            tmp = dest + ".tmp"
            with open(tmp, "wb") as handle:
                handle.write(data)
            os.replace(tmp, dest)
        except OSError as error:
            _log(f"[SIDECAR] Could not write {filename}: {error}")
            continue
        _log(f"[SIDECAR] Saved {ref['kind']} → {filename} ({len(data)} bytes)")
        manifest.append({
            "kind": ref["kind"],
            "url": ref["url"],
            "type": ref.get("type", ""),
            "language": ref.get("language", ""),
            "file": filename,
            "sha256": digest,
            "bytes": len(data),
        })
        seen_files.add(filename)
    return manifest


def sync_podcast_sidecars(
    feed_body,
    enclosure_url,
    out_dir,
    base_name,
    *,
    max_bytes=DEFAULT_MAX_BYTES,
    timeout=DEFAULT_TIMEOUT,
    log_fn=None,
):
    """Discover an episode's sidecars from its feed and download/refresh them.

    High-level reachable entry point used by the CLI. Returns the persisted
    manifest list (possibly empty). Never raises for missing metadata.
    """
    refs = find_feed_sidecars(feed_body, enclosure_url)
    if not refs:
        return []
    existing = read_manifest(out_dir, base_name)
    manifest = download_podcast_sidecars(
        refs, out_dir, base_name,
        existing=existing, max_bytes=max_bytes, timeout=timeout, log_fn=log_fn,
    )
    if manifest:
        try:
            write_manifest(out_dir, base_name, manifest)
        except OSError as error:
            if log_fn:
                log_fn(f"[SIDECAR] Could not write manifest: {error}")
    return manifest
