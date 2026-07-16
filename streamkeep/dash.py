"""DASH/MPD manifest parser — static and dynamic manifests (F50).

Parses MPEG-DASH Media Presentation Description (MPD) XML into
``QualityInfo`` entries.  Handles ``SegmentTemplate`` (pattern-based
URLs) and ``SegmentList`` (explicit URL lists) addressing.

Both static and dynamic (live) MPD manifests are supported — dynamic
manifests are passed through to ffmpeg which handles segment polling
natively.  DRM-protected content (``ContentProtection`` elements) is
detected and skipped with a warning.
"""

import re
import urllib.parse
import xml.etree.ElementTree as ET

from .http import curl
from .models import MediaTrackInfo, QualityInfo

# MPD namespace — most manifests use this, but some omit it
_MPD_NS = "urn:mpeg:dash:schema:mpd:2011"
_NS = {"mpd": _MPD_NS}


def parse_mpd(url, log_fn=None):
    """Fetch and parse a DASH MPD manifest.

    Returns a list of ``QualityInfo`` entries, or an empty list on error.
    """
    body = curl(url, timeout=15)
    if not body:
        if log_fn:
            log_fn("[DASH] Failed to fetch MPD manifest.")
        return []

    return parse_mpd_xml(body, url, log_fn)


def parse_mpd_xml(xml_text, base_url, log_fn=None):
    """Parse MPD XML text into ``QualityInfo`` entries."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        if log_fn:
            log_fn(f"[DASH] MPD parse error: {e}")
        return []

    # Detect namespace — some manifests don't declare it
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    mpd_type = root.attrib.get("type", "static")
    is_dynamic = mpd_type == "dynamic"

    if is_dynamic and log_fn:
        log_fn("[DASH] Dynamic/live MPD — ffmpeg will handle segment polling.")

    total_secs = _parse_duration(root.attrib.get("mediaPresentationDuration", ""))

    qualities = []
    manifest_dir = urllib.parse.urljoin(base_url, "./")
    root_base = _child_base_url(root, manifest_dir, ns)
    fmt = "dash-live" if is_dynamic else "dash"

    for period_index, period in enumerate(_findall(root, "Period", ns)):
        period_id = period.attrib.get("id", "") or f"period-{period_index + 1}"
        period_base = _child_base_url(period, root_base, ns)
        period_tracks = []
        kind_indexes = {"video": 0, "audio": 0, "subtitle": 0}
        for adapt_index, adapt_set in enumerate(
            _findall(period, "AdaptationSet", ns)
        ):
            if _findall(adapt_set, "ContentProtection", ns):
                if log_fn:
                    log_fn("[DASH] Skipping DRM-protected AdaptationSet.")
                continue
            adapt_base = _child_base_url(adapt_set, period_base, ns)
            adapt_mime = adapt_set.attrib.get("mimeType", "")
            adapt_type = adapt_set.attrib.get("contentType", "")
            adapt_lang = adapt_set.attrib.get("lang", "")
            adapt_codecs = adapt_set.attrib.get("codecs", "")
            role_values = {
                role.attrib.get("value", "").lower()
                for role in _findall(adapt_set, "Role", ns)
            }
            for rep_index, rep in enumerate(
                _findall(adapt_set, "Representation", ns)
            ):
                if _findall(rep, "ContentProtection", ns):
                    if log_fn:
                        log_fn("[DASH] Skipping DRM-protected Representation.")
                    continue
                rid = rep.attrib.get("id", "") or f"{adapt_index}-{rep_index}"
                width = _int_attr(rep, "width")
                height = _int_attr(rep, "height")
                bandwidth = _int_attr(rep, "bandwidth")
                mime = rep.attrib.get("mimeType", "") or adapt_mime
                content_type = rep.attrib.get("contentType", "") or adapt_type
                codec = rep.attrib.get("codecs", "") or adapt_codecs
                language = rep.attrib.get("lang", "") or adapt_lang
                kind = _representation_kind(mime, content_type, codec)
                stream_index = kind_indexes[kind]
                kind_indexes[kind] += 1

                rep_base_el = _find(rep, "BaseURL", ns)
                has_rep_base = bool(
                    rep_base_el is not None and (rep_base_el.text or "").strip()
                )
                segmented = any((
                    _find(rep, "SegmentTemplate", ns) is not None,
                    _find(adapt_set, "SegmentTemplate", ns) is not None,
                    _find(rep, "SegmentList", ns) is not None,
                    _find(adapt_set, "SegmentList", ns) is not None,
                ))
                if segmented:
                    rep_url = base_url
                elif has_rep_base:
                    rep_url = urllib.parse.urljoin(
                        adapt_base, rep_base_el.text.strip()
                    )
                elif adapt_base != period_base:
                    rep_url = adapt_base
                else:
                    rep_url = base_url

                resolution = f"{width}x{height}" if width and height else ""
                if kind == "video" and height:
                    label = f"{height}p"
                    if bandwidth:
                        label += f" ({bandwidth // 1000}kbps)"
                elif kind == "audio":
                    label = (
                        f"{language or 'audio'} {bandwidth // 1000}kbps"
                        if bandwidth else language or "audio"
                    )
                elif kind == "subtitle":
                    label = language or f"subtitle {rid}"
                else:
                    label = f"rep-{rid}"
                label_el = _find(rep, "Label", ns)
                if label_el is None:
                    label_el = _find(adapt_set, "Label", ns)
                if label_el is not None and (label_el.text or "").strip():
                    label = label_el.text.strip()
                period_tracks.append(MediaTrackInfo(
                    id=f"dash-{period_id}-{kind}-{rid}",
                    kind=kind,
                    label=label,
                    language=language,
                    url=rep_url,
                    group_id=adapt_set.attrib.get("id", "") or str(adapt_index),
                    codec=codec,
                    bandwidth=bandwidth,
                    resolution=resolution,
                    stream_index=stream_index if rep_url == base_url else 0,
                    default="main" in role_values,
                    autoselect=True,
                    forced="forced-subtitle" in role_values,
                    period_id=period_id,
                ))

        for kind in ("video", "audio"):
            candidates = [track for track in period_tracks if track.kind == kind]
            if candidates and not any(track.default for track in candidates):
                candidates[0].default = True
        default_audio = next(
            (track for track in period_tracks if track.kind == "audio" and track.default),
            None,
        )
        for track in period_tracks:
            qualities.append(QualityInfo(
                name=track.label,
                url=track.url,
                resolution=track.resolution,
                bandwidth=track.bandwidth,
                format_type=fmt,
                audio_url=(
                    default_audio.url
                    if track.kind == "video" and default_audio is not None
                    and default_audio.url != track.url
                    else ""
                ),
                tracks=list(period_tracks),
                primary_track_id=track.id,
            ))

    kind_order = {"video": 0, "audio": 1, "subtitle": 2}
    qualities.sort(key=lambda quality: (
        kind_order.get(
            next((track.kind for track in quality.tracks
                  if track.id == quality.primary_track_id), ""),
            3,
        ),
        -(quality.bandwidth or 0),
    ))

    if log_fn:
        log_fn(f"[DASH] Parsed {len(qualities)} quality/ies from MPD "
               f"(duration: {total_secs:.0f}s).")

    return qualities


# ── XML helpers (namespace-agnostic) ────────────────────────────────

def _findall(parent, tag, ns):
    """Find child elements with or without namespace."""
    results = parent.findall(f"{ns}{tag}") if ns else parent.findall(tag)
    if not results and ns:
        results = parent.findall(tag)
    if not results:
        results = parent.findall(f"{{*}}{tag}")
    return results


def _find(parent, tag, ns):
    """Find first child element with or without namespace."""
    results = _findall(parent, tag, ns)
    return results[0] if results else None


def _child_base_url(element, parent_url, ns):
    base_el = _find(element, "BaseURL", ns)
    if base_el is None or not (base_el.text or "").strip():
        return parent_url
    return urllib.parse.urljoin(parent_url, base_el.text.strip())


def _int_attr(element, name):
    try:
        return max(0, int(element.attrib.get(name, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _representation_kind(mime, content_type, codec):
    value = " ".join((mime, content_type, codec)).lower()
    if "video" in value:
        return "video"
    if "audio" in value:
        return "audio"
    if any(token in value for token in (
        "text", "subtitle", "stpp", "wvtt", "ttml", "vtt",
    )):
        return "subtitle"
    return "video"


def _parse_duration(iso_str):
    """Parse ISO 8601 duration (e.g. ``PT1H23M45.6S``) to seconds."""
    if not iso_str:
        return 0
    m = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?",
        iso_str,
    )
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    secs = float(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + secs
