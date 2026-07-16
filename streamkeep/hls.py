"""HLS m3u8 parsing."""

import re
from dataclasses import replace
from urllib.parse import urljoin

from .models import MediaTrackInfo, QualityInfo


_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("(?:[^"\\]|\\.)*"|[^,]*)')


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
            try:
                bw = int(pending.get("AVERAGE-BANDWIDTH") or pending.get("BANDWIDTH") or 0)
            except (TypeError, ValueError):
                bw = 0
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
                resolution=res if res != "?" else "",
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
                bandwidth=bw, format_type="hls",
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
