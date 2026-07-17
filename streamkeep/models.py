"""Shared data classes — no runtime dependencies on anything but stdlib."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MediaTrackInfo:
    """One selectable video, audio, or subtitle representation."""
    id: str = ""
    kind: str = ""                  # video, audio, subtitle
    label: str = ""
    language: str = ""
    url: str = ""
    group_id: str = ""
    codec: str = ""
    bandwidth: int = 0              # peak BANDWIDTH (bits/s)
    average_bandwidth: int = 0      # AVERAGE-BANDWIDTH when advertised
    resolution: str = ""
    frame_rate: float = 0.0         # FRAME-RATE (HLS) / @frameRate (DASH)
    video_range: str = ""           # SDR / PQ / HLG (VIDEO-RANGE)
    stream_index: int = 0
    default: bool = False
    autoselect: bool = False
    forced: bool = False
    period_id: str = ""


@dataclass
class QualityInfo:
    name: str = ""
    url: str = ""
    resolution: str = ""
    bandwidth: int = 0
    average_bandwidth: int = 0
    frame_rate: float = 0.0
    video_range: str = ""           # SDR / PQ / HLG for HDR-aware selection
    format_type: str = "hls"       # hls, mp4, dash, ytdlp_direct
    audio_url: str = ""             # If set, video is video-only and needs audio merge
    ytdlp_source: str = ""          # Original page URL for ytdlp_direct downloads
    ytdlp_format: str = ""          # Format spec (e.g. "137+140")
    tracks: list[MediaTrackInfo] = field(default_factory=list)
    primary_track_id: str = ""


@dataclass
class HLSSegment:
    """One media segment from an HLS media playlist."""
    uri: str = ""
    duration: float = 0.0
    media_sequence: int = 0         # absolute EXT-X-MEDIA-SEQUENCE index
    discontinuity_sequence: int = 0
    program_date_time: str = ""
    byterange: str = ""
    gap: bool = False               # EXT-X-GAP — segment is a placeholder


@dataclass
class HLSMediaPlaylist:
    """A parsed HLS media (segment) playlist with sequence identity."""
    target_duration: float = 0.0
    media_sequence: int = 0
    discontinuity_sequence: int = 0
    is_endlist: bool = False        # VOD (EXT-X-ENDLIST) vs live
    total_duration: float = 0.0
    start_time: str = ""
    validator: str = ""             # strong HTTP validator (ETag/Last-Modified)
    segments: list[HLSSegment] = field(default_factory=list)

    @property
    def is_live(self) -> bool:
        return not self.is_endlist


def default_media_tracks(quality):
    """Return the primary representation plus default companion tracks."""
    tracks = list(getattr(quality, "tracks", []) or [])
    if not tracks:
        return []
    selected = []
    primary_id = str(getattr(quality, "primary_track_id", "") or "")
    primary = next((track for track in tracks if track.id == primary_id), None)
    if primary is not None:
        selected.append(primary)
    elif tracks:
        selected.append(tracks[0])
    primary_kind = primary.kind if primary is not None else selected[0].kind
    if primary_kind != "audio":
        audio_tracks = [track for track in tracks if track.kind == "audio"]
        default_audio = next(
            (track for track in audio_tracks if track.default),
            audio_tracks[0] if audio_tracks else None,
        )
        if default_audio is not None:
            selected.append(default_audio)
    selected.extend(
        track for track in tracks
        if track.kind == "subtitle" and track.default
    )
    return selected


@dataclass
class SubtitleInfo:
    language: str = ""
    name: str = ""
    manual: bool = False
    automatic: bool = False
    formats: list[str] = field(default_factory=list)


@dataclass
class StreamInfo:
    platform: str = ""
    channel: str = ""
    title: str = ""
    url: str = ""
    qualities: list[QualityInfo] = field(default_factory=list)
    total_secs: float = 0
    duration_str: str = ""
    start_time: str = ""
    is_live: bool = False
    is_master: bool = False
    segment_count: int = 0
    thumbnail_url: str = ""
    chapters: list[dict[str, str | float]] = field(default_factory=list)  # list of {title, start, end}
    subtitles: list[SubtitleInfo] = field(default_factory=list)
    # Originating podcast RSS feed, when this download came from a browsed
    # feed. Lets finalize auto-fetch transcript/chapter sidecars for the
    # episode (the enclosure URL alone doesn't reference its feed).
    feed_url: str = ""


@dataclass
class VODInfo:
    title: str = ""
    date: str = ""
    source: str = ""
    is_live: bool = False
    viewers: int = 0
    duration: str = ""
    duration_ms: int = 0
    platform: str = ""
    channel: str = ""
    feed_url: str = ""  # originating RSS feed (podcast episodes)


@dataclass
class HistoryEntry:
    """A single completed download entry for the history tab."""
    date: str = ""
    platform: str = ""
    title: str = ""
    channel: str = ""
    quality: str = ""
    size: str = ""
    path: str = ""
    url: str = ""
    favorite: bool = False                 # exempt from lifecycle cleanup (F32)
    watched: bool = False                  # playback status (F32/F38)
    watch_position_secs: float = 0.0       # resume position (F38)
    bookmarks: list[dict[str, str | float]] = field(default_factory=list)  # [{name, secs}] (F38)
    db_id: int = 0                         # SQLite row id (F41, 0=not persisted)

    def to_dict(self) -> dict[str, str | bool | float | list[dict[str, str | float]]]:
        """Serialize to a dict suitable for ``db.save_history_entry()``."""
        return {
            "date": self.date, "platform": self.platform,
            "title": self.title, "channel": self.channel,
            "quality": self.quality, "size": self.size,
            "path": self.path, "url": self.url,
            "favorite": self.favorite, "watched": self.watched,
            "watch_position_secs": self.watch_position_secs,
            "bookmarks": list(self.bookmarks or []),
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> HistoryEntry:
        """Deserialize from a dict (DB row or legacy JSON)."""
        return cls(
            date=str(d.get("date", "")),
            platform=str(d.get("platform", "")),
            title=str(d.get("title", "")),
            channel=str(d.get("channel", "")),
            quality=str(d.get("quality", "")),
            size=str(d.get("size", "")),
            path=str(d.get("path", "")),
            url=str(d.get("url", "")),
            favorite=bool(d.get("favorite", False)),
            watched=bool(d.get("watched", False)),
            watch_position_secs=float(d.get("watch_position_secs", 0) or 0),
            bookmarks=list(d.get("bookmarks", []) or []),
            db_id=int(d.get("id", 0) or 0),
        )


@dataclass
class MonitorEntry:
    url: str = ""
    platform: str = ""
    channel_id: str = ""
    interval_secs: int = 120
    auto_record: bool = False
    subscribe_vods: bool = False          # Check for new VODs and queue them
    last_check: float = 0
    last_status: str = "unknown"          # live, offline, error
    is_recording: bool = False
    archive_ids: list[str] = field(default_factory=list)  # already-seen VOD source IDs
    # Per-channel overrides (v4.14.0). None means "use the global default".
    override_output_dir: str = ""         # empty = inherit global output dir
    override_quality_pref: str = ""       # "", "highest", "source", "720p", "480p", ...
    override_filename_template: str = ""  # empty = inherit global template
    schedule_start_hhmm: str = ""         # "20:00" or "" = always active
    schedule_end_hhmm: str = ""           # "23:00" or "" = always active
    schedule_days_mask: int = 0           # 0 = all days; bit 0=Mon ... bit 6=Sun
    retention_keep_last: int = 0          # 0 = keep everything
    filter_keywords: str = ""             # comma-separated keywords for title matching (F3)
    override_pp_preset: str = ""          # named post-processing preset (F7)
    ytdlp_template_name: str = ""         # named structured argv template
    auto_upgrade: bool = False            # re-download when higher quality VOD appears (F25)
    min_upgrade_quality: str = ""         # minimum quality to trigger upgrade (e.g. "1080p")
    _cancel_requested: bool = field(default=False, repr=False, compare=False)


@dataclass
class ResumeState:
    """Sidecar written next to an in-flight download so it can be resumed
    across app crashes, network drops, and power loss.

    Persisted as <outdir>/.streamkeep_resume.json. One per output directory;
    the worker refreshes it on start, segment_done, and cancel, and deletes
    it on clean all_done."""
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    # Source identity — used both for URL re-resolve and to tell the user
    # what they're resuming.
    source_url: str = ""                  # original page URL the user pasted
    platform: str = ""
    title: str = ""
    channel: str = ""
    # Playback target that was actually handed to ffmpeg / yt-dlp. May be
    # a stale token URL — on resume we re-resolve via the extractor before
    # trusting it.
    playlist_url: str = ""
    format_type: str = "hls"
    audio_url: str = ""
    # HLS resume identity — a live playlist that has rolled past our window,
    # changed its strong validator, or crossed a discontinuity is no longer
    # safe to resume against and must fall back to a full restart.
    playlist_validator: str = ""          # ETag or Last-Modified of the media playlist
    media_sequence: int = 0               # EXT-X-MEDIA-SEQUENCE at download start
    discontinuity_sequence: int = 0       # EXT-X-DISCONTINUITY-SEQUENCE at start
    playlist_segment_count: int = 0       # segments present when resume was written
    selected_tracks: list[dict[str, object]] = field(default_factory=list)
    ytdlp_source: str = ""
    ytdlp_format: str = ""
    ytdlp_format_sort: str = ""
    ytdlp_container: str = "mp4"
    ytdlp_audio_format: str = ""
    ytdlp_audio_quality: str = ""
    download_subs: bool = False
    capture_youtube_chat: bool = False
    subtitle_languages: str = ""
    subtitle_auto: bool = True
    subtitle_convert: str = ""
    subtitle_embed: bool = True
    sponsorblock: bool = False
    sponsorblock_mark: str = ""
    sponsorblock_remove: str = ""
    sponsorblock_api: str = ""
    download_archive: str = ""
    break_on_existing: bool = False
    ytdlp_concurrent_fragments: int = 0
    ytdlp_retries: str = ""
    ytdlp_fragment_retries: str = ""
    ytdlp_retry_sleep: str = ""
    ytdlp_unavailable_fragments: str = ""
    ytdlp_throttled_rate: str = ""
    ytdlp_live_from_start: bool = False
    ytdlp_wait_for_video: str = ""
    ytdlp_embed_chapters: bool | None = None
    ytdlp_embed_metadata: bool | None = None
    ytdlp_embed_thumbnail: bool | None = None
    ytdlp_external_downloader: str = ""
    ytdlp_aria2c_connections: int = 0
    ytdlp_aria2c_splits: int = 0
    ytdlp_aria2c_min_split_size: str = ""
    ytdlp_template_name: str = ""
    quality_name: str = ""
    # Per-segment state. `segments` stores the original tuples as lists so
    # JSON round-trips cleanly. `completed` is a set-as-list of seg_idx ints.
    segments: list[list[int | str | float]] = field(default_factory=list)     # list[[idx, label, start, duration]]
    completed: list[int] = field(default_factory=list)    # list[int]
    output_dir: str = ""
    # For yt-dlp direct downloads, the outfile layout is single-file; we
    # record the expected path so the resume banner can show progress.
    expected_outfile: str = ""
