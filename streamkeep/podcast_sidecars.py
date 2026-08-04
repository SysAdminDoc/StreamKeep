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
import base64

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
    r"<(?:[A-Za-z_][\w.-]*:)?transcript\b([^>]*?)/?>",
    re.IGNORECASE | re.DOTALL,
)
_CHAPTERS_RE = re.compile(
    r"<(?:[A-Za-z_][\w.-]*:)?chapters\b([^>]*?)/?>",
    re.IGNORECASE | re.DOTALL,
)
_ITEM_RE = re.compile(r"<item\b[^>]*>.*?</item\s*>", re.IGNORECASE | re.DOTALL)
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
                "rel": (attrs.get("rel") or "").strip(),
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


def _atomic_write_bytes(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _chapter_timestamp(seconds):
    total_ms = max(0, int(round(float(seconds or 0) * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _ffmetadata_escape(value):
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace(";", "\\;")
        .replace("#", "\\#")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def _convert_chapter_sidecars(out_dir, base_name, source_entry, data):
    """Write ffmetadata and WebVTT forms beside a JSON chapters file."""
    try:
        from .extractors.podcast import parse_podcast_chapters_json
        chapters = parse_podcast_chapters_json(
            data.decode("utf-8", errors="replace")
        )
    except Exception:
        return []
    if not chapters:
        return []
    ff_lines = [";FFMETADATA1"]
    vtt_lines = ["WEBVTT", ""]
    for index, chapter in enumerate(chapters):
        start = max(0.0, float(chapter.get("start", 0) or 0))
        end = float(chapter.get("end", 0) or 0)
        if end <= start:
            end = (
                float(chapters[index + 1].get("start", 0) or 0)
                if index + 1 < len(chapters) else start + 1.0
            )
        end = max(start + 0.001, end)
        title = chapter.get("title", "Chapter") or "Chapter"
        ff_lines.extend([
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(round(start * 1000))}",
            f"END={int(round(end * 1000))}",
            f"title={_ffmetadata_escape(title)}",
        ])
        vtt_lines.extend([
            f"{_chapter_timestamp(start)} --> {_chapter_timestamp(end)}",
            str(title).replace("\r", "").replace("\n", " "),
            "",
        ])
    derived = []
    for suffix, content in (
        ("ffmetadata", "\n".join(ff_lines) + "\n"),
        ("vtt", "\n".join(vtt_lines) + "\n"),
    ):
        filename = f"{base_name}.chapters.{suffix}"
        path = os.path.join(out_dir, filename)
        encoded = content.encode("utf-8")
        try:
            _atomic_write_bytes(path, encoded)
        except OSError:
            continue
        derived.append({
            "kind": "chapters",
            "format": suffix,
            "derived_from": source_entry.get("file", ""),
            "file": filename,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded),
        })
    return derived


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
                pass  # safe: best-effort fallback; preserve the primary operation

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
            for derived in existing or []:
                if (
                    isinstance(derived, dict)
                    and derived.get("derived_from") == filename
                    and derived.get("file")
                    and os.path.isfile(os.path.join(out_dir, derived["file"]))
                ):
                    manifest.append(derived)
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
            "rel": ref.get("rel", ""),
            "file": filename,
            "sha256": digest,
            "bytes": len(data),
        })
        if ref["kind"] == "chapters":
            manifest.extend(_convert_chapter_sidecars(
                out_dir, base_name, manifest[-1], data,
            ))
        seen_files.add(filename)
    return manifest


def _integrity_candidates(integrity):
    if not isinstance(integrity, dict):
        return []
    declared_type = str(integrity.get("type", "") or "").strip().casefold()
    value = str(integrity.get("value", "") or "").strip()
    if not value:
        return []
    candidates = []
    if declared_type in {"sri", ""}:
        for token in value.split():
            match = re.fullmatch(r"(sha256|sha384|sha512)-(.+)", token, re.I)
            if not match:
                continue
            algorithm, encoded = match.groups()
            try:
                decoded = base64.b64decode(
                    encoded + "=" * (-len(encoded) % 4), validate=False,
                )
            except (ValueError, TypeError):
                continue
            if decoded:
                candidates.append((algorithm.casefold(), decoded))
    elif declared_type in {"sha256", "sha384", "sha512"}:
        algorithm = declared_type
        try:
            if re.fullmatch(r"[0-9a-fA-F]+", value) and len(value) == int(algorithm[3:]) // 4:
                decoded = bytes.fromhex(value)
            else:
                decoded = base64.b64decode(
                    value + "=" * (-len(value) % 4), validate=False,
                )
        except (ValueError, TypeError):
            decoded = b""
        if decoded:
            candidates.append((algorithm, decoded))
    return candidates


def _urls_match(candidate, downloaded_url):
    candidate = str(candidate or "").strip()
    downloaded_url = str(downloaded_url or "").strip()
    if not candidate or not downloaded_url:
        return False
    if candidate == downloaded_url:
        return True
    try:
        left = urllib.parse.urlsplit(candidate)
        right = urllib.parse.urlsplit(downloaded_url)
    except ValueError:
        return False
    return (
        left.scheme.casefold() == right.scheme.casefold()
        and left.netloc.casefold() == right.netloc.casefold()
        and left.path == right.path
    )


def verify_podcast_integrity(media_path, alternate_enclosures, downloaded_urls=()):
    """Verify matching Podcasting 2.0 alternate-enclosure hashes.

    A declaration for a different alternate source remains ``not_downloaded``;
    only a hash whose source matches the downloaded delivery is checked. PGP
    signatures and malformed algorithms are retained as ``unsupported`` or
    ``invalid`` so they cannot be mistaken for a successful verification.
    """
    if isinstance(downloaded_urls, str):
        urls = [downloaded_urls.strip()]
    else:
        urls = [str(url or "").strip() for url in (downloaded_urls or ()) if url]
    results = []
    digest_cache = {}
    for enclosure in alternate_enclosures or []:
        if not isinstance(enclosure, dict):
            continue
        integrity = enclosure.get("integrity")
        if not isinstance(integrity, dict):
            continue
        sources = [
            str(row.get("uri", "") or "")
            for row in enclosure.get("sources", [])
            if isinstance(row, dict) and row.get("uri")
        ]
        matched = next(
            (source for source in sources if any(_urls_match(source, url) for url in urls)),
            "",
        )
        result = {
            "source": matched or (sources[0] if sources else ""),
            "type": str(integrity.get("type", "") or ""),
            "expected": str(integrity.get("value", "") or ""),
            "status": "not_downloaded",
        }
        if not matched:
            results.append(result)
            continue
        candidates = _integrity_candidates(integrity)
        if not candidates:
            result["status"] = "unsupported" if result["type"].casefold() == "pgp-signature" else "invalid"
            results.append(result)
            continue
        if not media_path or not os.path.isfile(media_path):
            result["status"] = "media_missing"
            results.append(result)
            continue
        verified = False
        actual = ""
        for algorithm, expected in candidates:
            if algorithm not in digest_cache:
                digest = hashlib.new(algorithm)
                try:
                    with open(media_path, "rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    digest_cache[algorithm] = digest.digest()
                except OSError:
                    digest_cache[algorithm] = None
            actual_bytes = digest_cache[algorithm]
            if actual_bytes is None:
                result["status"] = "media_unreadable"
                break
            actual = actual_bytes.hex()
            if actual_bytes == expected:
                verified = True
                result["algorithm"] = algorithm
                break
        else:
            result["algorithm"] = candidates[0][0]
        if result["status"] == "media_unreadable":
            pass
        elif verified:
            result["status"] = "verified"
        else:
            result["status"] = "mismatch"
            result["actual"] = actual
        results.append(result)
    return results


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
