"""DASH/MPD manifest parser — static and dynamic manifests (F50).

Parses MPEG-DASH Media Presentation Description (MPD) XML into
``QualityInfo`` entries.  Handles ``SegmentTemplate`` (including
``SegmentTimeline``), ``SegmentList`` (explicit URL lists), and
``SegmentBase``/``indexRange`` single-file addressing.

Both static and dynamic (live) MPD manifests are supported — dynamic
manifests are passed through to ffmpeg which handles segment polling
natively.  DRM-protected content (``ContentProtection`` elements) is
detected and skipped with a warning.
"""

import math
import re
import urllib.parse

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from .http import curl
from .models import DASHSegment, MediaTrackInfo, QualityInfo
from .net_guard import RemoteURLPolicyError, validate_remote_url

# MPD namespace — most manifests use this, but some omit it
_MPD_NS = "urn:mpeg:dash:schema:mpd:2011"
_NS = {"mpd": _MPD_NS}
_DASH_MAX_TIMELINE_SEGMENTS = 100_000
_DASH_TEMPLATE_RE = re.compile(
    r"\$\$|\$(RepresentationID|Number|Bandwidth|Time)(%0\d+d)?\$"
)


_DASH_URI_ATTRIBUTES = {
    "SegmentTemplate": (
        "media", "initialization", "index", "bitstreamSwitching",
    ),
    "SegmentURL": ("media", "index"),
    "Initialization": ("sourceURL",),
    "RepresentationIndex": ("sourceURL",),
    "BitstreamSwitching": ("sourceURL",),
    "ContentSteering": ("proxyServerURL",),
}


def _local_name(value):
    return str(value or "").rsplit("}", 1)[-1]


def validate_dash_manifest(
    xml_text,
    base_url,
    *,
    allow_private_network=False,
    max_references=20_000,
):
    """Normalize and policy-check every remotely dereferenced DASH URI."""
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, DefusedXmlException) as error:
        raise RemoteURLPolicyError(f"DASH manifest is malformed: {error}") from None
    root_url = validate_remote_url(
        base_url, allow_private_network=allow_private_network,
    ).url
    references = []
    seen = set()

    def add(raw_value, parent_url):
        value = str(raw_value or "").strip()
        if not value:
            return ""
        target = validate_remote_url(
            value,
            base_url=parent_url,
            allow_private_network=allow_private_network,
        ).url
        if target not in seen:
            seen.add(target)
            references.append(target)
        if len(references) > max_references:
            raise RemoteURLPolicyError(
                "DASH manifest exceeds the URI reference limit"
            )
        return target

    def walk(element, parent_bases):
        base_children = [
            child for child in list(element)
            if _local_name(child.tag) == "BaseURL"
            and str(child.text or "").strip()
        ]
        if base_children:
            bases = []
            for parent_base in parent_bases:
                for child in base_children:
                    target = add(child.text, parent_base)
                    if target and target not in bases:
                        bases.append(target)
        else:
            bases = list(parent_bases)

        tag = _local_name(element.tag)
        for attribute in _DASH_URI_ATTRIBUTES.get(tag, ()):
            value = element.attrib.get(attribute, "")
            for base in bases:
                add(value, base)

        if tag in {"Location", "PatchLocation"}:
            for base in parent_bases:
                add(element.text, base)
        if tag == "ContentSteering":
            for base in bases:
                add(element.text, base)
        if tag == "UTCTiming":
            value = str(element.attrib.get("value", "") or "").strip()
            scheme_id = str(
                element.attrib.get("schemeIdUri", "") or ""
            ).lower()
            try:
                value_scheme = urllib.parse.urlsplit(value).scheme.lower()
            except ValueError:
                value_scheme = "invalid"
            if (
                ":utc:http-" in scheme_id
                or value.startswith("//")
                or bool(value_scheme)
            ):
                for base in bases:
                    add(value, base)

        for attribute, value in element.attrib.items():
            if _local_name(attribute) != "href":
                continue
            if value == "urn:mpeg:dash:resolve-to-zero:2013":
                continue
            for base in bases:
                add(value, base)

        for child in list(element):
            if _local_name(child.tag) != "BaseURL":
                walk(child, bases)

    walk(root, [root_url])
    return tuple(references)


def preflight_dash_manifest(
    url,
    fetch_text,
    *,
    allow_private_network=False,
):
    """Fetch one MPD through guarded transport and validate its URI graph."""
    manifest_url = validate_remote_url(
        url, allow_private_network=allow_private_network,
    ).url
    body = fetch_text(manifest_url)
    if body is None:
        raise RemoteURLPolicyError(
            "DASH manifest could not be fetched through guarded transport"
        )
    validate_dash_manifest(
        body,
        manifest_url,
        allow_private_network=allow_private_network,
    )
    return manifest_url


def parse_mpd(url, log_fn=None, *, allow_private_network=False):
    """Fetch and parse a DASH MPD manifest.

    Returns a list of ``QualityInfo`` entries, or an empty list on error.
    """
    manifest_url = validate_remote_url(
        url, allow_private_network=allow_private_network,
    ).url
    body = curl(manifest_url, timeout=15)
    if not body:
        if log_fn:
            log_fn("[DASH] Failed to fetch MPD manifest.")
        return []

    return parse_mpd_xml(
        body,
        manifest_url,
        log_fn,
        allow_private_network=allow_private_network,
    )


def parse_mpd_xml(
    xml_text, base_url, log_fn=None, *, allow_private_network=False,
):
    """Parse MPD XML text into ``QualityInfo`` entries."""
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, DefusedXmlException) as e:
        if log_fn:
            log_fn(f"[DASH] MPD parse error: {e}")
        return []

    base_url = validate_remote_url(
        base_url, allow_private_network=allow_private_network,
    ).url
    # Validate the complete URI graph before any parser-specific URL is
    # materialized.  The individual resolvers below repeat the check at each
    # construction point so this function remains safe if the graph walker
    # and parser support diverge in the future.
    validate_dash_manifest(
        xml_text,
        base_url,
        allow_private_network=allow_private_network,
    )

    # Detect namespace — some manifests don't declare it
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    mpd_type = root.attrib.get("type", "static")
    is_dynamic = mpd_type == "dynamic"

    if is_dynamic and log_fn:
        log_fn("[DASH] Dynamic/live MPD — ffmpeg will handle segment polling.")

    total_secs = _parse_duration(root.attrib.get("mediaPresentationDuration", ""))
    minimum_update_period = _parse_duration(
        root.attrib.get("minimumUpdatePeriod", "")
    )

    qualities = []
    manifest_dir = _resolve_dash_url(
        "./", base_url, allow_private_network=allow_private_network,
    )
    root_base = _child_base_url(
        root,
        manifest_dir,
        ns,
        allow_private_network=allow_private_network,
    )
    fmt = "dash-live" if is_dynamic else "dash"
    root_template = _find(root, "SegmentTemplate", ns)
    root_list = _find(root, "SegmentList", ns)
    root_base_segment = _find(root, "SegmentBase", ns)
    periods = _findall(root, "Period", ns)

    for period_index, period in enumerate(periods):
        period_id = period.attrib.get("id", "") or f"period-{period_index + 1}"
        period_base = _child_base_url(
            period,
            root_base,
            ns,
            allow_private_network=allow_private_network,
        )
        period_duration_secs = _parse_duration(
            period.attrib.get("duration", "")
        )
        if not period_duration_secs and len(periods) == 1:
            period_duration_secs = total_secs
        period_template = _first_element(
            _find(period, "SegmentTemplate", ns), root_template
        )
        period_list = _first_element(
            _find(period, "SegmentList", ns), root_list
        )
        period_base_segment = _first_element(
            _find(period, "SegmentBase", ns), root_base_segment
        )
        period_tracks = []
        track_metadata = {}
        kind_indexes = {"video": 0, "audio": 0, "subtitle": 0}
        for adapt_index, adapt_set in enumerate(
            _findall(period, "AdaptationSet", ns)
        ):
            if _findall(adapt_set, "ContentProtection", ns):
                if log_fn:
                    log_fn("[DASH] Skipping DRM-protected AdaptationSet.")
                continue
            adapt_base = _child_base_url(
                adapt_set,
                period_base,
                ns,
                allow_private_network=allow_private_network,
            )
            adapt_mime = adapt_set.attrib.get("mimeType", "")
            adapt_type = adapt_set.attrib.get("contentType", "")
            adapt_lang = adapt_set.attrib.get("lang", "")
            adapt_codecs = adapt_set.attrib.get("codecs", "")
            role_values = {
                role.attrib.get("value", "").lower()
                for role in _findall(adapt_set, "Role", ns)
            }
            adapt_template = _first_element(
                _find(adapt_set, "SegmentTemplate", ns), period_template
            )
            adapt_list = _first_element(
                _find(adapt_set, "SegmentList", ns), period_list
            )
            adapt_base_segment = _first_element(
                _find(adapt_set, "SegmentBase", ns), period_base_segment
            )
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

                rep_base = _child_base_url(
                    rep,
                    adapt_base,
                    ns,
                    allow_private_network=allow_private_network,
                )
                seg_template = _first_element(
                    _find(rep, "SegmentTemplate", ns), adapt_template
                )
                seg_list = _first_element(
                    _find(rep, "SegmentList", ns), adapt_list
                )
                seg_base = _first_element(
                    _find(rep, "SegmentBase", ns), adapt_base_segment
                )
                segments = []
                initialization_url = ""
                initialization_range = ""
                index_range = ""
                if seg_template is not None:
                    timeline = _find(seg_template, "SegmentTimeline", ns)
                    if timeline is not None:
                        timescale = max(
                            1,
                            _parse_int_value(
                                seg_template.attrib.get("timescale"), 1
                            ) or 1,
                        )
                        period_duration_units = (
                            int(round(period_duration_secs * timescale))
                            if period_duration_secs > 0 else None
                        )
                        segments = _expand_segment_timeline(
                            seg_template,
                            timeline,
                            rep_base,
                            representation_id=rid,
                            bandwidth=bandwidth,
                            period_duration_units=period_duration_units,
                            ns=ns,
                            allow_private_network=allow_private_network,
                        )
                    initialization_template = seg_template.attrib.get(
                        "initialization", ""
                    )
                    if initialization_template:
                        parsed_start_number = _parse_int_value(
                            seg_template.attrib.get("startNumber"), None
                        )
                        start_number = (
                            parsed_start_number
                            if parsed_start_number is not None else 1
                        )
                        initialization_url = _resolve_segment_template(
                            initialization_template,
                            rep_base,
                            representation_id=rid,
                            number=start_number,
                            time=0,
                            bandwidth=bandwidth,
                            allow_private_network=allow_private_network,
                        )
                if seg_list is not None:
                    (
                        segments,
                        initialization_url,
                        initialization_range,
                        _list_timescale,
                    ) = _expand_segment_list(
                        seg_list,
                        rep_base,
                        representation_id=rid,
                        bandwidth=bandwidth,
                        ns=ns,
                        allow_private_network=allow_private_network,
                    )
                if seg_base is not None:
                    timescale = max(
                        1,
                        _parse_int_value(
                            seg_base.attrib.get("timescale"), 1
                        ) or 1,
                    )
                    index_range = str(
                        seg_base.attrib.get("indexRange", "") or ""
                    )
                    initialization = _find(seg_base, "Initialization", ns)
                    if initialization is not None:
                        source_url = str(
                            initialization.attrib.get("sourceURL", "") or ""
                        )
                        initialization_url = (
                            _resolve_dash_url(
                                source_url,
                                rep_base,
                                allow_private_network=allow_private_network,
                            )
                            if source_url else rep_base
                        )
                        initialization_range = str(
                            initialization.attrib.get("range", "") or ""
                        )
                    period_duration_units = (
                        int(round(period_duration_secs * timescale))
                        if period_duration_secs > 0 else 0
                    )
                    parsed_start_number = _parse_int_value(
                        seg_base.attrib.get("startNumber"), None
                    )
                    segments = [DASHSegment(
                        uri=rep_base,
                        number=(
                            parsed_start_number
                            if parsed_start_number is not None else 1
                        ),
                        start=0,
                        duration=period_duration_units,
                        timescale=timescale,
                        index_range=index_range,
                    )]

                rep_base_el = _find(rep, "BaseURL", ns)
                has_rep_base = bool(
                    rep_base_el is not None and (rep_base_el.text or "").strip()
                )
                segmented = any((
                    seg_template is not None,
                    seg_list is not None,
                    seg_base is not None,
                ))
                if seg_base is not None:
                    rep_url = rep_base
                elif segmented:
                    rep_url = base_url
                elif has_rep_base:
                    rep_url = _resolve_dash_url(
                        rep_base_el.text.strip(),
                        adapt_base,
                        allow_private_network=allow_private_network,
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
                track_id = f"dash-{period_id}-{kind}-{rid}"
                period_tracks.append(MediaTrackInfo(
                    id=track_id,
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
                    index_range=index_range,
                    initialization_range=initialization_range,
                ))
                track_metadata[track_id] = {
                    "segments": list(segments),
                    "initialization_url": initialization_url,
                    "initialization_range": initialization_range,
                    "index_range": index_range,
                }

        for kind in ("video", "audio"):
            candidates = [track for track in period_tracks if track.kind == kind]
            if candidates and not any(track.default for track in candidates):
                candidates[0].default = True
        default_audio = next(
            (track for track in period_tracks if track.kind == "audio" and track.default),
            None,
        )
        for track in period_tracks:
            metadata = track_metadata.get(track.id, {})
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
                segments=list(metadata.get("segments", [])),
                initialization_url=str(
                    metadata.get("initialization_url", "") or ""
                ),
                initialization_range=str(
                    metadata.get("initialization_range", "") or ""
                ),
                index_range=str(metadata.get("index_range", "") or ""),
                minimum_update_period=minimum_update_period,
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


def _first_element(*elements):
    """Return the first present XML element without relying on truthiness."""
    for element in elements:
        if element is not None:
            return element
    return None


def _resolve_dash_url(value, base_url, *, allow_private_network=False):
    """Resolve one DASH URI only after it crosses the remote URL policy."""
    value = str(value or "").strip()
    if not value:
        return ""
    return validate_remote_url(
        value,
        base_url=base_url,
        allow_private_network=allow_private_network,
    ).url


def _child_base_url(
    element, parent_url, ns, *, allow_private_network=False,
):
    base_el = _find(element, "BaseURL", ns)
    if base_el is None or not (base_el.text or "").strip():
        return parent_url
    return _resolve_dash_url(
        base_el.text,
        parent_url,
        allow_private_network=allow_private_network,
    )


def _parse_int_value(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def _int_attr(element, name):
    try:
        return max(0, int(element.attrib.get(name, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _substitute_segment_template(
    template, *, representation_id, number, time, bandwidth,
):
    """Expand the DASH URL-template identifiers in one media URL."""
    values = {
        "RepresentationID": str(representation_id or ""),
        "Number": number,
        "Time": time,
        "Bandwidth": bandwidth,
    }

    def replace(match):
        token = match.group(0)
        if token == "$$":
            return "$"
        name = match.group(1)
        value = values.get(name)
        if value is None:
            return token
        formatter = match.group(2)
        if formatter:
            try:
                return formatter % int(value)
            except (TypeError, ValueError):
                return str(value)
        return str(value)

    return _DASH_TEMPLATE_RE.sub(replace, str(template or ""))


def _resolve_segment_template(
    template,
    base_url,
    *,
    representation_id,
    number,
    time,
    bandwidth,
    allow_private_network=False,
):
    value = _substitute_segment_template(
        template,
        representation_id=representation_id,
        number=number,
        time=time,
        bandwidth=bandwidth,
    )
    return _resolve_dash_url(
        value,
        base_url,
        allow_private_network=allow_private_network,
    )


def _expand_segment_timeline(
    template,
    timeline,
    base_url,
    *,
    representation_id,
    bandwidth,
    period_duration_units=None,
    ns="",
    max_segments=_DASH_MAX_TIMELINE_SEGMENTS,
    allow_private_network=False,
):
    """Expand ``SegmentTimeline/S`` rows into a bounded resolved snapshot."""
    if template is None or timeline is None:
        return []
    timescale = max(
        1,
        _parse_int_value(template.attrib.get("timescale"), 1) or 1,
    )
    parsed_start_number = _parse_int_value(
        template.attrib.get("startNumber"), None,
    )
    start_number = (
        parsed_start_number if parsed_start_number is not None else 1
    )
    media_template = template.attrib.get("media", "")
    cursor = None
    segments = []
    rows = _findall(timeline, "S", ns)
    for row_index, row in enumerate(rows):
        duration = _parse_int_value(row.attrib.get("d"), None)
        if duration is None or duration <= 0:
            continue
        explicit_start = _parse_int_value(row.attrib.get("t"), None)
        if explicit_start is not None:
            cursor = explicit_start
        elif cursor is None:
            cursor = 0
        repeat = _parse_int_value(row.attrib.get("r"), 0) or 0
        if repeat < -1:
            repeat = 0
        if repeat == -1:
            next_start = None
            if row_index + 1 < len(rows):
                next_start = _parse_int_value(
                    rows[row_index + 1].attrib.get("t"), None
                )
            boundary = (
                next_start if next_start is not None else period_duration_units
            )
            if boundary is not None and boundary > cursor:
                repeat = max(0, math.ceil((boundary - cursor) / duration) - 1)
            else:
                # A dynamic MPD can intentionally leave the final repeat open.
                # Keep one current segment in the snapshot; ffmpeg remains the
                # live refresher and the cap prevents an allocation storm.
                repeat = 0
        count = min(repeat + 1, max(0, max_segments - len(segments)))
        for offset in range(count):
            start = cursor + offset * duration
            number = start_number + len(segments)
            segments.append(DASHSegment(
                uri=_resolve_segment_template(
                    media_template,
                    base_url,
                    representation_id=representation_id,
                    number=number,
                    time=start,
                    bandwidth=bandwidth,
                    allow_private_network=allow_private_network,
                ),
                number=number,
                start=start,
                duration=duration,
                timescale=timescale,
            ))
        cursor += (repeat + 1) * duration
        if len(segments) >= max_segments:
            break
    return segments


def _expand_segment_list(
    segment_list,
    base_url,
    *,
    representation_id,
    bandwidth,
    ns="",
    allow_private_network=False,
):
    """Resolve explicit SegmentList rows into the DASH segment model."""
    del representation_id, bandwidth  # reserved for future template parity
    if segment_list is None:
        return [], "", "", 1
    timescale = max(
        1,
        _parse_int_value(segment_list.attrib.get("timescale"), 1) or 1,
    )
    duration = _parse_int_value(segment_list.attrib.get("duration"), 0) or 0
    parsed_start_number = _parse_int_value(
        segment_list.attrib.get("startNumber"), None,
    )
    start_number = (
        parsed_start_number if parsed_start_number is not None else 1
    )
    initialization_url = ""
    initialization_range = ""
    initialization = _find(segment_list, "Initialization", ns)
    if initialization is not None:
        source_url = str(initialization.attrib.get("sourceURL", "") or "")
        initialization_url = (
            _resolve_dash_url(
                source_url,
                base_url,
                allow_private_network=allow_private_network,
            )
            if source_url else base_url
        )
        initialization_range = str(
            initialization.attrib.get("range", "") or ""
        )
    segments = []
    for index, segment_url in enumerate(_findall(segment_list, "SegmentURL", ns)):
        media = segment_url.attrib.get("media", "")
        if not media:
            continue
        number = start_number + index
        segments.append(DASHSegment(
            uri=_resolve_dash_url(
                media,
                base_url,
                allow_private_network=allow_private_network,
            ),
            number=number,
            start=index * duration,
            duration=duration,
            timescale=timescale,
            media_range=str(segment_url.attrib.get("mediaRange", "") or ""),
            index_range=str(
                segment_url.attrib.get("indexRange", "")
                or segment_url.attrib.get("index", "")
                or ""
            ),
        ))
    return segments, initialization_url, initialization_range, timescale


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
