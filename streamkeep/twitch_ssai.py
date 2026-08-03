"""Twitch server-side ad insertion detection and HLS filtering.

Twitch stitches advertisements into the same HLS media playlist as the
content stream.  The ad boundary is intentionally platform-specific: an
ordinary ``EXT-X-DISCONTINUITY`` is not enough to identify an advertisement,
because Twitch also emits discontinuities for normal encoder changes.

The filter renders a safe, self-contained media playlist.  Segment URLs and
URI attributes are made absolute so the rendered playlist can be staged in a
temporary local file while FFmpeg fetches the selected media through the
worker's existing guarded proxy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import os
from pathlib import Path
import re
import tempfile
from threading import Event, Lock, Thread
from typing import Callable
from urllib.parse import urljoin, urlsplit

from .hls import _parse_attributes, parse_hls_media_playlist


_AD_DATERANGE_CLASS = "twitch-stitched-ad"
_AD_DATERANGE_ID_PREFIX = "stitched-ad-"
_AD_CONTROL_TAGS = frozenset({
    "#EXT-X-CUE-OUT",
    "#EXT-X-CUE-OUT-CONT",
    "#EXT-X-CUE-IN",
    "#EXT-X-SCTE35-OUT",
    "#EXT-X-SCTE35-IN",
})
_URI_ATTRIBUTE_TAGS = frozenset({
    "#EXT-X-KEY",
    "#EXT-X-MAP",
    "#EXT-X-PART",
    "#EXT-X-PRELOAD-HINT",
    "#EXT-X-RENDITION-REPORT",
})
_GLOBAL_TAG_PREFIXES = (
    "#EXTM3U",
    "#EXT-X-VERSION:",
    "#EXT-X-TARGETDURATION:",
    "#EXT-X-MEDIA-SEQUENCE:",
    "#EXT-X-DISCONTINUITY-SEQUENCE:",
    "#EXT-X-PLAYLIST-TYPE:",
    "#EXT-X-INDEPENDENT-SEGMENTS",
    "#EXT-X-START:",
    "#EXT-X-SERVER-CONTROL:",
    "#EXT-X-PART-INF:",
    "#EXT-X-SKIP:",
    "#EXT-X-ALLOW-CACHE:",
    "#EXT-X-DEFINE:",
)
_PREFETCH_TAG = "#EXT-X-TWITCH-PREFETCH:"
_URI_ATTRIBUTE_RE = re.compile(r'(URI=")(.*?)(")', re.IGNORECASE)


@dataclass(frozen=True)
class TwitchSSAIResult:
    """Filtered playlist and the ad/content accounting it represents."""

    filtered_body: str
    source_segment_count: int = 0
    kept_segment_count: int = 0
    ad_segment_count: int = 0
    ad_duration: float = 0.0
    content_duration: float = 0.0
    marker_count: int = 0
    target_duration: float = 0.0
    is_endlist: bool = False

    @property
    def is_live(self) -> bool:
        return not self.is_endlist

    @property
    def filtered(self) -> bool:
        return self.ad_segment_count > 0


@dataclass
class _RawSegment:
    lines: tuple[str, ...]
    uri: str
    duration: float
    title: str = ""
    program_date_time: str = ""
    has_discontinuity: bool = False
    ad_daterange: bool = False
    cue_out: float | None = None
    cue_in: bool = False
    ad_title: bool = False
    prefetch: bool = False


@dataclass(frozen=True)
class _AdDateRange:
    start: datetime
    end: datetime | None
    duration: float


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_ad_daterange(line: str) -> tuple[bool, _AdDateRange | None]:
    if not line.upper().startswith("#EXT-X-DATERANGE:"):
        return False, None
    attrs = _parse_attributes(line.split(":", 1)[1])
    classname = str(attrs.get("CLASS", "")).strip().casefold()
    identifier = str(attrs.get("ID", "")).strip().casefold()
    is_ad = (
        classname == _AD_DATERANGE_CLASS
        or identifier.startswith(_AD_DATERANGE_ID_PREFIX)
    )
    if not is_ad:
        return False, None
    start = _parse_datetime(attrs.get("START-DATE", ""))
    if start is None:
        return True, None
    try:
        duration = max(0.0, float(attrs.get("DURATION", "0") or 0))
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        try:
            duration = max(
                0.0,
                float(attrs.get("X-TV-TWITCH-AD-POD-FILLED-DURATION", "0") or 0),
            )
        except (TypeError, ValueError):
            duration = 0.0
    end = _parse_datetime(attrs.get("END-DATE", ""))
    if end is None and duration > 0:
        end = start + timedelta(seconds=duration)
    return True, _AdDateRange(start, end, duration)


def _cue_out_duration(line: str) -> float | None:
    tag = line.split(":", 1)[0].upper()
    if tag not in {"#EXT-X-CUE-OUT", "#EXT-X-SCTE35-OUT"}:
        return None
    if ":" not in line:
        return 0.0
    value = line.split(":", 1)[1].strip()
    attrs = _parse_attributes(value)
    raw = attrs.get("DURATION", value)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _parse_segment(lines: list[str], uri: str, *, prefetch=False) -> _RawSegment:
    duration = 0.0
    title = ""
    program_date_time = ""
    has_discontinuity = False
    ad_daterange = False
    cue_out = None
    cue_in = False
    ad_title = False
    for line in lines:
        upper = line.upper()
        if upper.startswith("#EXTINF:"):
            value = line.split(":", 1)[1]
            raw_duration, _, raw_title = value.partition(",")
            try:
                duration = max(0.0, float(raw_duration.strip()))
            except (TypeError, ValueError):
                duration = 0.0
            title = raw_title.strip()
            ad_title = "amazon" in title.casefold()
        elif upper.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            program_date_time = line.split(":", 1)[1].strip()
        elif upper == "#EXT-X-DISCONTINUITY":
            has_discontinuity = True
        elif upper.startswith("#EXT-X-DATERANGE:"):
            ad_daterange, _ = _is_ad_daterange(line)
        elif upper.split(":", 1)[0] in {
            "#EXT-X-CUE-OUT", "#EXT-X-SCTE35-OUT",
        }:
            cue_out = _cue_out_duration(line)
        elif upper.split(":", 1)[0] in {
            "#EXT-X-CUE-IN", "#EXT-X-SCTE35-IN",
        }:
            cue_in = True
    return _RawSegment(
        lines=tuple(lines),
        uri=uri,
        duration=duration,
        title=title,
        program_date_time=program_date_time,
        has_discontinuity=has_discontinuity,
        ad_daterange=ad_daterange,
        cue_out=cue_out,
        cue_in=cue_in,
        ad_title=ad_title,
        prefetch=prefetch,
    )


def _split_segments(body: str) -> tuple[list[_RawSegment], list[str]]:
    blocks: list[_RawSegment] = []
    pending: list[str] = []
    for raw in str(body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith(_PREFETCH_TAG):
            blocks.append(_parse_segment(pending, line, prefetch=True))
            pending = []
            continue
        if line.startswith("#"):
            pending.append(line)
            continue
        if any(item.upper().startswith("#EXTINF:") for item in pending):
            blocks.append(_parse_segment(pending, line))
            pending = []
        else:
            # Keep an unexpected URI in the trailing document rather than
            # inventing a media segment with no duration.
            pending.append(line)
    return blocks, pending


def _is_global_line(line: str) -> bool:
    upper = line.upper()
    return any(upper.startswith(prefix) for prefix in _GLOBAL_TAG_PREFIXES)


def _rewrite_uri_attributes(line: str, base_url: str) -> str:
    tag = line.split(":", 1)[0].upper()
    if tag not in _URI_ATTRIBUTE_TAGS or not base_url:
        return line

    def replace(match):
        return (
            f'{match.group(1)}{_resolve_remote_uri(match.group(2), base_url)}'
            f'{match.group(3)}'
        )

    return _URI_ATTRIBUTE_RE.sub(replace, line)


def _resolve_remote_uri(value: str, base_url: str) -> str:
    resolved = urljoin(base_url, str(value or "")) if base_url else str(value or "")
    if base_url and urlsplit(resolved).scheme.casefold() not in {"http", "https"}:
        raise ValueError("Twitch HLS playlist contains a non-HTTP(S) URI")
    return resolved


def _rewrite_prefetch(line: str, base_url: str) -> str:
    if not base_url or not line.upper().startswith(_PREFETCH_TAG):
        return line
    return f"{line.split(':', 1)[0]}:{_resolve_remote_uri(line.split(':', 1)[1], base_url)}"


def _date_in_range(value: str, ranges: list[_AdDateRange]) -> bool:
    date = _parse_datetime(value)
    if date is None:
        return False
    for daterange in ranges:
        if date < daterange.start:
            continue
        if daterange.end is not None and date < daterange.end:
            return True
    return False


def _block_is_ad(
    block: _RawSegment,
    ranges: list[_AdDateRange],
    *,
    ad_active: bool,
    inferred_until_discontinuity: bool,
    previous_was_ad: bool,
    prefetch_ad_active: bool,
) -> tuple[bool, str]:
    if _date_in_range(block.program_date_time, ranges):
        return True, "daterange-time"
    if block.ad_daterange:
        return True, "daterange"
    if block.cue_in:
        return False, "cue-in"
    if block.ad_title:
        return True, "amazon-title"
    if block.cue_out is not None:
        return True, "cue-out"
    if ad_active:
        if (
            block.has_discontinuity
            and previous_was_ad
            and not block.ad_daterange
            and block.cue_out is None
        ):
            return False, "content-discontinuity"
        return True, "ad-break"
    if inferred_until_discontinuity:
        if block.has_discontinuity and previous_was_ad:
            return False, "content-discontinuity"
        return True, "ad-break"
    if block.prefetch and (block.has_discontinuity or prefetch_ad_active):
        return True, "prefetch-after-discontinuity"
    return False, ""


def _clean_lines(lines: tuple[str, ...], *, base_url: str) -> list[str]:
    cleaned = []
    for line in lines:
        upper = line.upper()
        if upper.startswith("#EXT-X-DATERANGE:"):
            is_ad, _ = _is_ad_daterange(line)
            if is_ad:
                continue
        if upper.split(":", 1)[0] in _AD_CONTROL_TAGS:
            continue
        if _is_global_line(line) or upper == "#EXT-X-ENDLIST":
            continue
        cleaned.append(_rewrite_uri_attributes(line, base_url))
    return cleaned


def filter_twitch_ssai_playlist(body: str, base_url: str = "") -> TwitchSSAIResult:
    """Remove detected Twitch SSAI ad segments from an HLS media playlist.

    The returned playlist keeps the original media/discontinuity sequence and
    program-date-time tags.  Removing ad durations therefore creates the
    expected content-only timeline while retaining a discontinuity at the
    point where the source stream resumes.
    """
    if "#EXTM3U" not in str(body or ""):
        raise ValueError("HLS playlist is missing #EXTM3U")

    typed = parse_hls_media_playlist(body, base_url)
    blocks, trailing = _split_segments(body)
    date_ranges: list[_AdDateRange] = []
    marker_count = 0
    for block in blocks:
        for line in block.lines:
            is_ad, daterange = _is_ad_daterange(line)
            if not is_ad:
                continue
            marker_count += 1
            if daterange is not None:
                date_ranges.append(daterange)
        if block.cue_out is not None:
            marker_count += 1
        if block.ad_title:
            marker_count += 1

    output_header: list[str] = []
    for block in blocks:
        output_header.extend(line for line in block.lines if _is_global_line(line))
    output_header.extend(line for line in trailing if _is_global_line(line))
    # Keep the header deterministic even when a live reload duplicated a
    # global tag in the pending block.
    deduped_header = []
    seen_header = set()
    for line in output_header:
        key = line.casefold()
        if key not in seen_header:
            seen_header.add(key)
            deduped_header.append(line)

    endlist = any(
        line.upper() == "#EXT-X-ENDLIST"
        for block in blocks
        for line in block.lines
    ) or any(line.upper() == "#EXT-X-ENDLIST" for line in trailing)

    output_lines = list(deduped_header)
    ad_active = False
    ad_remaining = 0.0
    inferred_until_discontinuity = False
    previous_was_ad = False
    prefetch_ad_active = False
    removed_since_content = False
    ad_segment_count = 0
    source_segment_count = 0
    kept_segment_count = 0
    ad_duration = 0.0
    content_duration = 0.0

    for block in blocks:
        source_segment_count += 1
        is_ad, _reason = _block_is_ad(
            block,
            date_ranges,
            ad_active=ad_active,
            inferred_until_discontinuity=inferred_until_discontinuity,
            previous_was_ad=previous_was_ad,
            prefetch_ad_active=prefetch_ad_active,
        )
        if block.cue_in:
            is_ad = False
            ad_active = False
            ad_remaining = 0.0
            inferred_until_discontinuity = False

        if block.cue_out is not None:
            ad_active = True
            ad_remaining = max(0.0, block.cue_out)
            inferred_until_discontinuity = block.cue_out <= 0
        elif block.ad_daterange:
            marker_duration = max(
                (
                    daterange.duration
                    for daterange in date_ranges
                    if daterange.duration > 0
                ),
                default=0.0,
            )
            ad_active = True
            ad_remaining = max(ad_remaining, marker_duration)
            inferred_until_discontinuity = marker_duration <= 0

        if not is_ad and _reason == "content-discontinuity":
            ad_active = False
            ad_remaining = 0.0
            inferred_until_discontinuity = False

        if is_ad:
            ad_segment_count += 1
            ad_duration += block.duration
            removed_since_content = True
            previous_was_ad = True
            if block.prefetch and (
                block.has_discontinuity or prefetch_ad_active
            ):
                prefetch_ad_active = True
            if block.cue_out is not None and block.cue_out > 0:
                ad_remaining = max(0.0, ad_remaining - block.duration)
                ad_active = ad_remaining > 0.05
            elif ad_remaining > 0:
                ad_remaining = max(0.0, ad_remaining - block.duration)
                ad_active = ad_remaining > 0.05
            continue

        cleaned = _clean_lines(block.lines, base_url=base_url)
        if removed_since_content and not any(
            line.upper() == "#EXT-X-DISCONTINUITY" for line in cleaned
        ):
            insert_at = next(
                (index for index, line in enumerate(cleaned)
                 if line.upper().startswith("#EXTINF:")),
                0,
            )
            cleaned.insert(insert_at, "#EXT-X-DISCONTINUITY")
        cleaned.append(
            _rewrite_prefetch(block.uri, base_url)
            if block.prefetch
            else _resolve_remote_uri(block.uri, base_url)
        )
        output_lines.extend(cleaned)
        kept_segment_count += 1
        content_duration += block.duration
        removed_since_content = False
        previous_was_ad = False
        if not block.prefetch:
            prefetch_ad_active = False
        if block.cue_in:
            ad_active = False
            inferred_until_discontinuity = False

    output_lines.extend(
        _rewrite_uri_attributes(line, base_url)
        for line in trailing
        if not _is_global_line(line) and line.upper() != "#EXT-X-ENDLIST"
    )
    if endlist:
        output_lines.append("#EXT-X-ENDLIST")
    filtered_body = "\n".join(output_lines).rstrip() + "\n"
    return TwitchSSAIResult(
        filtered_body=filtered_body,
        source_segment_count=source_segment_count,
        kept_segment_count=kept_segment_count,
        ad_segment_count=ad_segment_count,
        ad_duration=ad_duration,
        content_duration=content_duration,
        marker_count=marker_count,
        target_duration=typed.target_duration,
        is_endlist=endlist,
    )


def is_twitch_hls_job(platform: str, format_type: str, playlist_url: str) -> bool:
    """Return whether a worker job should use the Twitch SSAI filter."""
    return (
        str(platform or "").strip().casefold() == "twitch"
        and str(format_type or "").strip().casefold().startswith("hls")
        and str(playlist_url or "").strip().casefold().startswith(("http://", "https://"))
    )


class TwitchSSAIPlaylistRefresher:
    """Maintain a filtered local playlist while an HLS live capture runs."""

    def __init__(
        self,
        source_url: str,
        proxy_url: str,
        path: str,
        *,
        fetch: Callable[..., str | None],
    ):
        self.source_url = source_url
        self.proxy_url = proxy_url
        self.path = Path(path)
        self.fetch = fetch
        self._stop = Event()
        self._write_lock = Lock()
        self._thread: Thread | None = None
        self.last_result: TwitchSSAIResult | None = None

    def _write(self, body: str) -> TwitchSSAIResult:
        result = filter_twitch_ssai_playlist(body, self.source_url)
        if self._stop.is_set():
            return result
        with self._write_lock:
            if self._stop.is_set():
                return result
            fd, temp_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(result.filtered_body)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self.path)
            finally:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
        self.last_result = result
        return result

    def start(self, initial_body: str) -> TwitchSSAIResult:
        result = self._write(initial_body)
        if result.is_live:
            self._thread = Thread(
                target=self._refresh_loop,
                name="streamkeep-twitch-ssai-refresh",
                daemon=True,
            )
            self._thread.start()
        return result

    def _refresh_loop(self):
        interval = max(1.0, min(15.0, self.last_result.target_duration or 2.0))
        while not self._stop.wait(interval):
            body = self.fetch(self.source_url, self.proxy_url, timeout=10)
            if not body:
                continue
            try:
                result = self._write(body)
            except (OSError, ValueError):
                continue
            interval = max(1.0, min(15.0, result.target_duration or interval))

    def stop(self):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=13.0)
