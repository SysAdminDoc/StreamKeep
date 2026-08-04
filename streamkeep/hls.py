"""HLS m3u8 parsing."""

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from urllib.parse import urljoin

from .models import HLSMediaPlaylist, HLSSegment, MediaTrackInfo, QualityInfo
from .net_guard import RemoteURLPolicyError, validate_remote_url


_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("(?:[^"\\]|\\.)*"|[^,]*)')


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_attributes(value):
    attrs = {}
    for key, raw in _ATTR_RE.findall(str(value or "")):
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            raw = raw[1:-1].replace(r'\"', '"')
        attrs[key] = raw
    return attrs


def _resolve(base_url, value):
    value = str(value or "").strip()
    return urljoin(base_url, value) if value else ""


@dataclass(frozen=True)
class HLSManifestReferences:
    """Policy-checked resources and child playlists in one HLS document."""

    resources: tuple
    playlists: tuple
    # Apple’s out-of-band daterange schedule is a fetched resource, not a
    # child media playlist. Keeping it separate lets callers archive it while
    # ensuring an interstitial asset can never become a primary input.
    schedule_uris: tuple = ()
    # Each entry carries the schedule document's parent start for resolving
    # X-SCHEDULE-OFFSET in the fetched JSON document.
    schedule_contexts: tuple = ()
    preload_keys: tuple = ()
    preload_parts: tuple = ()


@dataclass(frozen=True)
class HLSScheduleDocument:
    """Parsed marker rows and nested schedule references from one document."""

    markers: tuple = ()
    references: tuple = ()


_HLS_PLAYLIST_URI_TAGS = frozenset({
    "#EXT-X-I-FRAME-STREAM-INF",
    "#EXT-X-IMAGE-STREAM-INF",
    "#EXT-X-RENDITION-REPORT",
})
_HLS_URI_ATTRIBUTE_TAGS = frozenset({
    "#EXT-X-CONTENT-STEERING",
    "#EXT-X-I-FRAME-STREAM-INF",
    "#EXT-X-IMAGE-STREAM-INF",
    "#EXT-X-KEY",
    "#EXT-X-MAP",
    "#EXT-X-MEDIA",
    "#EXT-X-PART",
    "#EXT-X-PRELOAD-HINT",
    "#EXT-X-RENDITION-REPORT",
    "#EXT-X-SESSION-DATA",
    "#EXT-X-SESSION-KEY",
})
_HLS_URI_VALUE_TAGS = frozenset({
    # Twitch's low-latency prefetch tag carries a bare segment URI instead of
    # an RFC 8216 URI attribute. It still crosses the same remote URL gate.
    "#EXT-X-TWITCH-PREFETCH",
})


def validate_hls_manifest(
    body,
    base_url,
    *,
    allow_private_network=False,
    max_references=10_000,
):
    """Normalize and policy-check every URI carried by an HLS document.

    This covers master variants and renditions as well as media segments,
    encryption keys, initialization maps, low-latency parts, preload hints,
    iframe/image playlists, session resources, and content steering.
    """
    base = validate_remote_url(
        base_url, allow_private_network=allow_private_network,
    ).url
    resources = []
    playlists = []
    resource_seen = set()
    playlist_seen = set()
    schedule_seen = set()
    schedule_uris = []
    schedule_contexts = []
    preload_keys = []
    preload_parts = []
    pending_playlist = False

    def add(raw_value, *, playlist=False):
        if not str(raw_value or "").strip():
            return ""
        target = validate_remote_url(
            raw_value,
            base_url=base,
            allow_private_network=allow_private_network,
        ).url
        if target not in resource_seen:
            resource_seen.add(target)
            resources.append(target)
        if playlist and target not in playlist_seen:
            playlist_seen.add(target)
            playlists.append(target)
        if len(resources) > max_references:
            raise RemoteURLPolicyError(
                "HLS manifest exceeds the URI reference limit"
            )
        return target

    for raw_line in str(body or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            tag, separator, value = line.partition(":")
            if tag == "#EXT-X-STREAM-INF":
                pending_playlist = True
            if separator and tag in _HLS_URI_VALUE_TAGS:
                add(value)
                continue
            if separator and tag == "#EXT-X-DATERANGE":
                attrs = _parse_attributes(value)
                # X-ASSET-URI is intentionally a resource only. It is a
                # client-side interstitial asset and must not be added to the
                # playlist graph consumed as primary media.
                if attrs.get("X-ASSET-URI"):
                    add(attrs["X-ASSET-URI"])
                schedule_uri = attrs.get("X-URI", "")
                if (
                    schedule_uri
                    and attrs.get("CLASS", "").casefold()
                    == "com.apple.hls.daterange-schedule"
                ):
                    target = add(schedule_uri)
                    if target and target not in schedule_seen:
                        schedule_seen.add(target)
                        schedule_uris.append(target)
                        schedule_contexts.append(
                            (target, str(attrs.get("START-DATE", "") or ""))
                        )
                continue
            if not separator or tag not in _HLS_URI_ATTRIBUTE_TAGS:
                continue
            attrs = _parse_attributes(value)
            uri_values = []
            if attrs.get("URI"):
                uri_values.append(attrs["URI"])
            if attrs.get("SERVER-URI"):
                uri_values.append(attrs["SERVER-URI"])
            for uri_value in uri_values:
                if tag == "#EXT-X-PRELOAD-HINT":
                    target = add(uri_value)
                    hint_type = attrs.get("TYPE", "").upper()
                    if hint_type == "KEY" and target and target not in preload_keys:
                        preload_keys.append(target)
                    elif (
                        hint_type in {"PART", "MAP"}
                        and target
                        and target not in preload_parts
                    ):
                        preload_parts.append(target)
                    continue
                is_playlist = tag in _HLS_PLAYLIST_URI_TAGS
                if tag == "#EXT-X-MEDIA":
                    is_playlist = attrs.get("TYPE", "").upper() in {
                        "AUDIO", "SUBTITLES", "VIDEO",
                    }
                add(uri_value, playlist=is_playlist)
            continue
        add(line, playlist=pending_playlist)
        pending_playlist = False

    return HLSManifestReferences(
        resources=tuple(resources),
        playlists=tuple(playlists),
        schedule_uris=tuple(schedule_uris),
        schedule_contexts=tuple(schedule_contexts),
        preload_keys=tuple(preload_keys),
        preload_parts=tuple(preload_parts),
    )


def preflight_hls_manifest_tree(
    url,
    fetch_text,
    *,
    allow_private_network=False,
    max_depth=8,
    max_manifests=128,
    max_schedule_depth=8,
    max_schedules=128,
    on_manifest=None,
    on_schedule=None,
    on_schedule_markers=None,
):
    """Fetch and validate a bounded recursive HLS playlist graph.

    ``on_manifest`` receives ``(url, body)`` after the body has passed the
    URI policy.  Daterange schedules are fetched through the same guarded
    ``fetch_text`` callback and delivered to ``on_schedule`` as
    ``(url, body)``. ``on_schedule_markers`` receives ``(url, body,
    markers)`` after a JSON schedule has been parsed. All callbacks are
    observation hooks; they cannot widen the set of URLs that the graph
    walker will fetch.
    """
    root = validate_remote_url(
        url, allow_private_network=allow_private_network,
    ).url
    pending = [(root, 0)]
    seen = []
    seen_set = set()
    pending_schedules = []
    queued_schedules = set()
    seen_schedules = set()

    def queue_schedule(schedule_url, parent_start, depth):
        if schedule_url in queued_schedules or schedule_url in seen_schedules:
            return
        if depth > max_schedule_depth:
            raise RemoteURLPolicyError(
                "HLS daterange schedule graph exceeds the recursion limit"
            )
        queued_schedules.add(schedule_url)
        pending_schedules.append((schedule_url, parent_start, depth))

    def drain_schedules():
        while pending_schedules:
            schedule_url, parent_start, depth = pending_schedules.pop(0)
            if schedule_url in seen_schedules:
                continue
            if len(seen_schedules) >= max_schedules:
                raise RemoteURLPolicyError(
                    "HLS daterange schedule graph exceeds the schedule limit"
                )
            schedule_body = fetch_text(schedule_url)
            if schedule_body is None:
                continue
            seen_schedules.add(schedule_url)
            parsed = parse_hls_schedule_document(
                schedule_body,
                schedule_url,
                parent_start_date=parent_start,
                allow_private_network=allow_private_network,
            )
            if on_schedule is not None:
                on_schedule(schedule_url, schedule_body)
            if on_schedule_markers is not None:
                on_schedule_markers(
                    schedule_url, schedule_body, parsed.markers,
                )
            for nested_url, nested_parent_start in parsed.references:
                queue_schedule(
                    nested_url, nested_parent_start or parent_start, depth + 1,
                )

    while pending:
        current, depth = pending.pop(0)
        if current in seen_set:
            continue
        if len(seen) >= max_manifests:
            raise RemoteURLPolicyError(
                "HLS playlist graph exceeds the manifest limit"
            )
        body = fetch_text(current)
        if body is None:
            raise RemoteURLPolicyError(
                "HLS manifest could not be fetched through guarded transport"
            )
        seen.append(current)
        seen_set.add(current)
        references = validate_hls_manifest(
            body,
            current,
            allow_private_network=allow_private_network,
        )
        if on_manifest is not None:
            on_manifest(current, body)
        contexts = dict(references.schedule_contexts)
        for schedule_url in references.schedule_uris:
            queue_schedule(schedule_url, contexts.get(schedule_url, ""), 0)
        drain_schedules()
        if references.playlists and depth >= max_depth:
            raise RemoteURLPolicyError(
                "HLS playlist graph exceeds the recursion limit"
            )
        pending.extend(
            (playlist, depth + 1)
            for playlist in references.playlists
            if playlist not in seen_set
        )
    return tuple(seen)


def parse_hls_master(body, base_url):
    """Parse HLS variants with their alternate audio/subtitle renditions."""
    qualities = []
    # urljoin expects a resource URL, not a directory. If base_url looks
    # like a directory (no trailing file), append a / so relative variants
    # resolve under it instead of replacing the last segment.
    if base_url and not base_url.endswith("/") and "/" in base_url.split("://", 1)[-1]:
        tail = base_url.rsplit("/", 1)[-1]
        if "." not in tail:
            base_url = base_url + "/"

    rendition_groups = {"audio": {}, "subtitle": {}}
    group_indexes = {}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("#EXT-X-MEDIA:"):
            continue
        attrs = _parse_attributes(line.split(":", 1)[1])
        media_type = attrs.get("TYPE", "").upper()
        kind = (
            "audio" if media_type == "AUDIO"
            else "subtitle" if media_type in {"SUBTITLES", "CLOSED-CAPTIONS"}
            else ""
        )
        group_id = attrs.get("GROUP-ID", "")
        if not kind or not group_id:
            continue
        key = (kind, group_id)
        stream_index = group_indexes.get(key, 0)
        group_indexes[key] = stream_index + 1
        label = attrs.get("NAME", "") or attrs.get("LANGUAGE", "") or kind
        track = MediaTrackInfo(
            id=f"hls-{kind}-{group_id}-{stream_index}",
            kind=kind,
            label=label,
            language=attrs.get("LANGUAGE", ""),
            url=_resolve(base_url, attrs.get("URI", "")),
            group_id=group_id,
            stream_index=stream_index if not attrs.get("URI") else 0,
            default=attrs.get("DEFAULT", "").upper() == "YES",
            autoselect=attrs.get("AUTOSELECT", "").upper() == "YES",
            forced=attrs.get("FORCED", "").upper() == "YES",
        )
        rendition_groups[kind].setdefault(group_id, []).append(track)

    pending = None
    variant_index = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXT-X-STREAM-INF"):
            pending = _parse_attributes(line.split(":", 1)[1])
        elif pending is not None and line and not line.startswith("#"):
            q_url = _resolve(base_url, line)
            res = pending.get("RESOLUTION", "?")
            # BANDWIDTH is the required peak; AVERAGE-BANDWIDTH is optional.
            peak_bw = _to_int(pending.get("BANDWIDTH"))
            avg_bw = _to_int(pending.get("AVERAGE-BANDWIDTH"))
            bw = peak_bw or avg_bw
            frame_rate = _to_float(pending.get("FRAME-RATE"))
            # VIDEO-RANGE signals HDR (PQ/HLG) vs SDR for format selection.
            video_range = pending.get("VIDEO-RANGE", "").upper()
            # Human-facing name: last path component, fall back to resolution.
            tail = q_url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
            name = tail or res or "stream"
            codecs = pending.get("CODECS", "")
            video_codec = codecs.split(",", 1)[0] if codecs else ""
            video_track = MediaTrackInfo(
                id=f"hls-video-{variant_index}",
                kind="video",
                label=name,
                url=q_url,
                codec=video_codec,
                bandwidth=bw,
                average_bandwidth=avg_bw,
                resolution=res if res != "?" else "",
                frame_rate=frame_rate,
                video_range=video_range,
                stream_index=0,
                default=True,
            )
            tracks = [video_track]
            audio_group = pending.get("AUDIO", "")
            for track in rendition_groups["audio"].get(audio_group, []):
                tracks.append(replace(track, url=track.url or q_url))
            subtitle_group = (
                pending.get("SUBTITLES", "")
                or pending.get("CLOSED-CAPTIONS", "")
            )
            for track in rendition_groups["subtitle"].get(subtitle_group, []):
                tracks.append(replace(track, url=track.url or q_url))
            if not any(track.kind == "audio" for track in tracks) and "," in codecs:
                tracks.append(MediaTrackInfo(
                    id=f"hls-audio-muxed-{variant_index}",
                    kind="audio",
                    label="Muxed audio",
                    url=q_url,
                    codec=codecs.split(",", 1)[1],
                    stream_index=0,
                    default=True,
                    autoselect=True,
                ))
            default_audio = next(
                (track for track in tracks if track.kind == "audio" and track.default),
                next((track for track in tracks if track.kind == "audio"), None),
            )
            qualities.append(QualityInfo(
                name=name, url=q_url, resolution=res,
                bandwidth=bw, average_bandwidth=avg_bw,
                frame_rate=frame_rate, video_range=video_range,
                format_type="hls",
                audio_url=(
                    default_audio.url
                    if default_audio is not None and default_audio.url != q_url
                    else ""
                ),
                tracks=tracks,
                primary_track_id=video_track.id,
            ))
            variant_index += 1
            pending = None
    return qualities


def parse_hls_duration(body):
    """Parse HLS playlist for duration metadata.
    Returns (total_secs, start_time, segment_count).

    Handles both standard HLS and LL-HLS playlists. Duration is
    calculated from EXTINF tags; LL-HLS partial segments (EXT-X-PART)
    are counted but not added to total_secs (they're sub-segment).
    """
    total_secs = 0.0
    start_time = ""
    m = re.search(r'TOTAL-SECS[=:](\d+\.?\d*)', body)
    if m:
        total_secs = float(m.group(1))
    m2 = re.search(r'PROGRAM-DATE-TIME:(.+)', body)
    if m2:
        start_time = m2.group(1).strip()
    seg_count = len(re.findall(r'#EXTINF:', body))
    if not total_secs and seg_count:
        for dur_m in re.finditer(r'#EXTINF:([\d.]+)', body):
            total_secs += float(dur_m.group(1))
    return total_secs, start_time, seg_count


def _normalize_daterange_attributes(value):
    if isinstance(value, dict):
        return {
            str(key).upper(): item
            for key, item in value.items()
        }
    return _parse_attributes(value)


def _attribute_text(attrs, key):
    value = attrs.get(key, "")
    return "" if value is None else str(value)


def _resolve_schedule_offset(parent_start_date, raw_offset):
    """Resolve a JSON schedule's decimal offset against its parent start."""
    parent = str(parent_start_date or "").strip()
    if not parent or raw_offset is None or isinstance(raw_offset, bool):
        return ""
    try:
        offset = float(raw_offset)
        start = datetime.fromisoformat(parent.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return ""
    if offset != offset or offset in {float("inf"), float("-inf")}:
        return ""
    resolved = (start + timedelta(seconds=offset)).isoformat()
    if resolved.endswith("+00:00"):
        return resolved[:-6] + "Z"
    return resolved


def _schedule_daterange_items(payload):
    if isinstance(payload, dict):
        raw_items = next(
            (
                value for key, value in payload.items()
                if str(key).upper() == "DATERANGES"
            ),
            None,
        )
    elif isinstance(payload, list):
        raw_items = payload
    else:
        return ()
    if isinstance(raw_items, dict):
        raw_items = list(raw_items.values())
    if not isinstance(raw_items, list):
        return ()
    items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        wrapped = next(
            (
                value for key, value in item.items()
                if str(key).upper() == "DATERANGE"
                and isinstance(value, dict)
            ),
            None,
        )
        items.append(wrapped if wrapped is not None else item)
    return tuple(items)


def parse_hls_schedule_document(
    body,
    base_url="",
    *,
    parent_start_date="",
    allow_private_network=False,
):
    """Parse a guarded HLS DATERANGES JSON document.

    Schedule attributes retain their JSON scalar types in ``attributes``;
    in particular, numeric durations and offsets remain numbers while SCTE
    hexadecimal sequences remain strings. Nested schedule URIs are normalized
    and policy-checked before they are returned to the graph walker.
    """
    try:
        payload = json.loads(str(body or ""))
    except (TypeError, ValueError):
        return HLSScheduleDocument()

    markers = []
    references = []
    reference_seen = set()
    for raw_item in _schedule_daterange_items(payload):
        attrs = _normalize_daterange_attributes(raw_item)
        class_name = _attribute_text(attrs, "CLASS")
        if not class_name:
            # CLASS is required by the schedule document contract. Ignore
            # unrelated JSON records in otherwise valid archive documents.
            continue
        raw_start = _attribute_text(attrs, "START-DATE").strip()
        raw_offset = attrs.get("X-SCHEDULE-OFFSET")
        has_offset = raw_offset is not None and str(raw_offset).strip() != ""
        if bool(raw_start) == bool(has_offset):
            # Exactly one of START-DATE and X-SCHEDULE-OFFSET is required.
            continue
        start_date = raw_start
        if has_offset:
            start_date = _resolve_schedule_offset(
                parent_start_date, raw_offset,
            )
            if not start_date:
                continue
        marker = _parse_daterange(attrs, base_url)
        if marker is None:
            continue
        if has_offset:
            marker["start_date"] = start_date
        markers.append(marker)

        if marker["is_schedule"] and marker["x_uri_raw"]:
            nested_url = validate_remote_url(
                marker["x_uri_raw"],
                base_url=base_url,
                allow_private_network=allow_private_network,
            ).url
            if nested_url not in reference_seen:
                reference_seen.add(nested_url)
                references.append((nested_url, start_date))
    return HLSScheduleDocument(
        markers=tuple(markers), references=tuple(references),
    )


# Name the parser after the HLS feature as well as its document shape so
# integrations written against either terminology remain straightforward.
parse_hls_daterange_schedule = parse_hls_schedule_document


def _parse_daterange(value, base_url=""):
    """Return a lossless, sidecar-ready representation of DATERANGE."""
    attrs = _normalize_daterange_attributes(value)
    if not attrs:
        return None
    class_name = _attribute_text(attrs, "CLASS")
    asset_uri_raw = _attribute_text(attrs, "X-ASSET-URI")
    asset_list = _attribute_text(attrs, "X-ASSET-LIST")
    is_interstitial = bool(asset_uri_raw or asset_list) or (
        class_name.casefold() == "com.apple.hls.interstitial"
    )
    duration = _to_float(attrs.get("DURATION"), default=None)
    planned_duration = _to_float(
        attrs.get("PLANNED-DURATION"), default=None,
    )
    return {
        "type": "interstitial" if is_interstitial else "daterange",
        "id": _attribute_text(attrs, "ID"),
        # Keep CLASS verbatim; unknown vendor classes are part of the public
        # marker contract and must not be normalized away.
        "class": class_name,
        "start_date": _attribute_text(attrs, "START-DATE"),
        "end_date": _attribute_text(attrs, "END-DATE"),
        "duration": duration,
        "planned_duration": planned_duration,
        "scte35_out": _attribute_text(attrs, "SCTE35-OUT"),
        "scte35_in": _attribute_text(attrs, "SCTE35-IN"),
        "asset_uri": _resolve(base_url, asset_uri_raw),
        "asset_uri_raw": asset_uri_raw,
        "asset_list": asset_list,
        "x_uri": _resolve(base_url, _attribute_text(attrs, "X-URI")),
        "x_uri_raw": _attribute_text(attrs, "X-URI"),
        "is_schedule": class_name.casefold()
        == "com.apple.hls.daterange-schedule",
        # This is deliberately retained in addition to the typed convenience
        # fields above so new RFC/vendor attributes survive unchanged.
        "attributes": dict(attrs),
    }


def merge_hls_delta_playlist(previous_playlist, current_playlist):
    """Merge retained segments from a prior playlist into an EXT-X-SKIP response.

    A delta response advertises the sequence immediately after the skipped
    window. The caller supplies the still-retained prior playlist; only the
    exact skipped sequence interval is copied, then current entries win on
    overlap. The response's advertised ``media_sequence`` remains intact for
    resume identity, while the effective segment list contains the full
    retained window.
    """
    if current_playlist is None or not getattr(current_playlist, "skipped_segments", 0):
        return current_playlist
    if previous_playlist is None:
        return current_playlist
    skip_count = max(0, _to_int(current_playlist.skipped_segments))
    if skip_count <= 0:
        return current_playlist
    current_segments = list(getattr(current_playlist, "segments", []) or [])
    current_start = _to_int(getattr(current_playlist, "media_sequence", 0))
    retained_start = current_start - skip_count
    retained = [
        segment for segment in (getattr(previous_playlist, "segments", []) or [])
        if retained_start <= _to_int(getattr(segment, "media_sequence", 0))
        < current_start
    ]
    current_keys = {
        (
            _to_int(getattr(segment, "media_sequence", 0)),
            _to_int(getattr(segment, "discontinuity_sequence", 0)),
        )
        for segment in current_segments
    }
    merged = [
        segment for segment in retained
        if (
            _to_int(getattr(segment, "media_sequence", 0)),
            _to_int(getattr(segment, "discontinuity_sequence", 0)),
        ) not in current_keys
    ]
    merged.extend(current_segments)
    merged.sort(key=lambda segment: (
        _to_int(getattr(segment, "media_sequence", 0)),
        _to_int(getattr(segment, "discontinuity_sequence", 0)),
    ))

    dateranges = []
    seen_ids = set()
    for marker in [
        *(getattr(previous_playlist, "dateranges", []) or []),
        *(getattr(current_playlist, "dateranges", []) or []),
    ]:
        marker_id = str(marker.get("id", "") if isinstance(marker, dict) else "")
        key = marker_id or repr(marker)
        if key in seen_ids:
            # The newest response is authoritative for a repeated ID.
            for index, old in enumerate(dateranges):
                old_key = str(old.get("id", "") if isinstance(old, dict) else "")
                if old_key == marker_id:
                    dateranges[index] = marker
                    break
            continue
        seen_ids.add(key)
        dateranges.append(marker)

    return replace(
        current_playlist,
        segments=merged,
        total_duration=sum(
            _to_float(getattr(segment, "duration", 0.0))
            for segment in merged
        ),
        dateranges=dateranges,
    )


def parse_hls_media_playlist(
    body, base_url="", *, previous_playlist=None,
):
    """Parse an HLS media (segment) playlist into a typed model.

    Tracks EXT-X-MEDIA-SEQUENCE / EXT-X-DISCONTINUITY-SEQUENCE so each segment
    carries an absolute media-sequence number and its discontinuity sequence,
    which together form a stable resume identity across live rollover. Handles
    per-segment EXT-X-DISCONTINUITY, EXT-X-GAP, EXT-X-BYTERANGE, and
    EXT-X-PROGRAM-DATE-TIME, and distinguishes VOD (EXT-X-ENDLIST) from live.
    Malformed EXTINF values isolate to a skipped segment rather than aborting.
    When ``previous_playlist`` is supplied, EXT-X-SKIP retained entries are
    merged into the returned effective segment list. EXT-X-DATERANGE rows are
    preserved with their full attribute map, including interstitial and
    vendor-specific fields.
    """
    playlist = HLSMediaPlaylist()
    media_sequence = 0
    discontinuity_sequence = 0
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            media_sequence = _to_int(line.split(":", 1)[1])
            playlist.media_sequence = media_sequence
        elif line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE:"):
            discontinuity_sequence = _to_int(line.split(":", 1)[1])
            playlist.discontinuity_sequence = discontinuity_sequence
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            playlist.target_duration = _to_float(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-SKIP:"):
            attrs = _parse_attributes(line.split(":", 1)[1])
            playlist.skipped_segments = max(
                0, _to_int(attrs.get("SKIPPED-SEGMENTS", 0))
            )
        elif line.startswith("#EXT-X-DATERANGE:"):
            marker = _parse_daterange(
                line.split(":", 1)[1], base_url,
            )
            if marker is not None:
                playlist.dateranges.append(marker)
        elif line == "#EXT-X-ENDLIST":
            playlist.is_endlist = True

    next_seq = media_sequence
    disc_seq = discontinuity_sequence
    pending_pdt = ""
    pending_byterange = ""
    pending_gap = False
    pending_duration = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "#EXT-X-DISCONTINUITY":
            disc_seq += 1
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pending_pdt = line.split(":", 1)[1].strip()
            if not playlist.start_time:
                playlist.start_time = pending_pdt
        elif line == "#EXT-X-GAP":
            pending_gap = True
        elif line.startswith("#EXT-X-BYTERANGE:"):
            pending_byterange = line.split(":", 1)[1].strip()
        elif line.startswith("#EXTINF:"):
            value = line.split(":", 1)[1].split(",", 1)[0].strip()
            pending_duration = _to_float(value, default=None)
        elif line.startswith("#"):
            continue
        else:
            # A URI line ends the current segment.
            if pending_duration is None:
                # A URI without a preceding valid EXTINF — skip it but keep
                # sequence numbering aligned with the malformed entry.
                next_seq += 1
                pending_pdt = pending_byterange = ""
                pending_gap = False
                continue
            playlist.segments.append(HLSSegment(
                uri=_resolve(base_url, line) if base_url else line,
                duration=pending_duration,
                media_sequence=next_seq,
                discontinuity_sequence=disc_seq,
                program_date_time=pending_pdt,
                byterange=pending_byterange,
                gap=pending_gap,
            ))
            playlist.total_duration += pending_duration
            next_seq += 1
            pending_pdt = pending_byterange = ""
            pending_gap = False
            pending_duration = None
    return merge_hls_delta_playlist(previous_playlist, playlist)


def resume_identity_matches(state, playlist):
    """Return True if *state* can still be safely resumed against *playlist*.

    A resume is invalidated when the media playlist's strong validator changed,
    when the live window has rolled past the segments we recorded, or when a
    discontinuity has been crossed since the resume was written.
    """
    if state is None or playlist is None:
        return False
    stored_validator = str(getattr(state, "playlist_validator", "") or "")
    fresh_validator = str(getattr(playlist, "validator", "") or "")
    if stored_validator and fresh_validator and stored_validator != fresh_validator:
        return False
    stored_media_seq = _to_int(getattr(state, "media_sequence", 0))
    stored_count = _to_int(getattr(state, "playlist_segment_count", 0))
    fresh_media_seq = _to_int(getattr(playlist, "media_sequence", 0))
    # If the earliest segment we still needed has already fallen off the live
    # window, the byte offsets no longer line up — force a full restart.
    if stored_count and fresh_media_seq > stored_media_seq + stored_count:
        return False
    stored_disc = _to_int(getattr(state, "discontinuity_sequence", 0))
    fresh_disc = _to_int(getattr(playlist, "discontinuity_sequence", 0))
    if fresh_disc > stored_disc:
        return False
    return True
