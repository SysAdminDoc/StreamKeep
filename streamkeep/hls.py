"""HLS m3u8 parsing."""

import re
from dataclasses import replace
from urllib.parse import urljoin

from .models import HLSMediaPlaylist, HLSSegment, MediaTrackInfo, QualityInfo


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


def parse_hls_media_playlist(body, base_url=""):
    """Parse an HLS media (segment) playlist into a typed model.

    Tracks EXT-X-MEDIA-SEQUENCE / EXT-X-DISCONTINUITY-SEQUENCE so each segment
    carries an absolute media-sequence number and its discontinuity sequence,
    which together form a stable resume identity across live rollover. Handles
    per-segment EXT-X-DISCONTINUITY, EXT-X-GAP, EXT-X-BYTERANGE, and
    EXT-X-PROGRAM-DATE-TIME, and distinguishes VOD (EXT-X-ENDLIST) from live.
    Malformed EXTINF values isolate to a skipped segment rather than aborting.
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
    return playlist


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
