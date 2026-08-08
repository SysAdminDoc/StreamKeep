"""Shared data classes — no runtime dependencies on anything but stdlib."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DASHSegment:
    """One resolved DASH media reference from a SegmentTimeline/List/Base."""
    uri: str = ""
    number: int = 0
    start: int = 0                  # media timescale units
    duration: int = 0               # media timescale units
    timescale: int = 1
    media_range: str = ""
    index_range: str = ""


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
    # DASH SegmentBase metadata.  These values are optional for HLS/direct
    # tracks and let a native worker preserve the byte-range contract when a
    # Representation is carried in one remote file.
    index_range: str = ""
    initialization_range: str = ""


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
    # DASH addressing metadata.  ``segments`` is populated for
    # SegmentTimeline/SegmentList and contains a bounded, fully resolved
    # snapshot; SegmentBase keeps its single-file index/initialization ranges.
    segments: list[DASHSegment] = field(default_factory=list)
    initialization_url: str = ""
    initialization_range: str = ""
    index_range: str = ""
    minimum_update_period: float = 0.0

    @property
    def segment_urls(self) -> list[str]:
        """Return the resolved media URLs in this DASH snapshot."""
        return [str(segment.uri) for segment in self.segments if segment.uri]


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
    version: int = 0
    playlist_type: str = ""       # EVENT or VOD when declared
    target_duration: float = 0.0
    media_sequence: int = 0
    discontinuity_sequence: int = 0
    is_endlist: bool = False        # VOD (EXT-X-ENDLIST) vs live
    total_duration: float = 0.0
    start_time: str = ""
    validator: str = ""             # strong HTTP validator (ETag/Last-Modified)
    segments: list[HLSSegment] = field(default_factory=list)
    # RFC 8216bis delta-playlist metadata.  ``segments`` can be expanded with
    # retained entries by ``merge_hls_delta_playlist`` while this remains the
    # sequence advertised by the response itself.
    skipped_segments: int = 0
    # Parsed EXT-X-DATERANGE rows.  Attribute names and values are retained in
    # each row so new/unknown vendor classes survive a round trip to a marker
    # sidecar instead of being silently discarded.
    dateranges: list[dict] = field(default_factory=list)

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
    description: str = ""
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
    markers: list[dict] = field(default_factory=list)
    marker_schedules: list[dict] = field(default_factory=list)
    subtitles: list[SubtitleInfo] = field(default_factory=list)
    # Originating podcast RSS feed, when this download came from a browsed
    # feed. Lets finalize auto-fetch transcript/chapter sidecars for the
    # episode (the enclosure URL alone doesn't reference its feed).
    feed_url: str = ""
    # Stable archival identity. ``url`` remains the short-lived delivery
    # endpoint used by the downloader; these fields are safe for sidecars.
    source_id: str = ""
    webpage_url: str = ""
    # Request headers the origin needs in order to serve ``url`` and its
    # segments. Some hosts reject a bare request outright — Kick's reworked
    # delivery host answers a manifest fetch with no User-Agent by returning a
    # JSON security block — so whatever the extractor used to resolve has to
    # travel with the stream to the downloader. Never credential material:
    # cookies and auth are carried by their own profile-bound channels.
    http_headers: dict[str, str] = field(default_factory=dict)
    # Parsed Podcasting 2.0 item metadata. Kept separate from the delivery
    # fields so a queue/finalizer can preserve the publisher's public tags.
    podcast_metadata: dict = field(default_factory=dict)


@dataclass
class VODInfo:
    title: str = ""
    description: str = ""
    date: str = ""
    source: str = ""
    is_live: bool = False
    # Podcasting 2.0 liveItem scheduling metadata.  These remain public feed
    # declarations; queue code decides whether a future start is actionable.
    live_status: str = ""
    start_time: str = ""
    end_time: str = ""
    content_links: list[dict[str, str]] = field(default_factory=list)
    viewers: int = 0
    duration: str = ""
    duration_ms: int = 0
    platform: str = ""
    channel: str = ""
    feed_url: str = ""  # originating RSS feed (podcast episodes)
    source_id: str = ""
    webpage_url: str = ""
    media_type: str = "video"  # video, audio, photo, or gif
    background_audio: list[dict[str, str]] = field(default_factory=list)
    thumbnail_url: str = ""
    podcast_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ArchivalProvenance:
    """Stable, public identity kept separate from delivery credentials."""

    platform: str = ""
    source_id: str = ""
    webpage_url: str = ""

    def to_dict(self):
        return {
            "platform": self.platform,
            "source_id": self.source_id,
            "webpage_url": self.webpage_url,
        }


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
    source_id: str = ""                    # stable platform-scoped media identity
    webpage_url: str = ""                  # canonical public page URL
    favorite: bool = False                 # exempt from lifecycle cleanup (F32)
    watched: bool = False                  # playback status (F32/F38)
    watch_position_secs: float = 0.0       # resume position (F38)
    bookmarks: list[dict[str, str | float]] = field(default_factory=list)  # [{name, secs}] (F38)
    db_id: int = 0                         # SQLite row id (F41, 0=not persisted)
    # Latest durable quality-upgrade evaluation, populated by paged history
    # queries for a per-item audit tooltip. These are read-only projections,
    # not columns written back with the history row.
    upgrade_decision: str = ""
    upgrade_reason_code: str = ""
    upgrade_reason: str = ""
    upgrade_execution_status: str = ""

    def to_dict(self) -> dict[str, str | bool | float | list[dict[str, str | float]]]:
        """Serialize to a dict suitable for ``db.save_history_entry()``."""
        return {
            "date": self.date, "platform": self.platform,
            "title": self.title, "channel": self.channel,
            "quality": self.quality, "size": self.size,
            "path": self.path, "url": self.url,
            "source_id": self.source_id,
            "webpage_url": self.webpage_url,
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
            source_id=str(d.get("source_id", "")),
            webpage_url=str(d.get("webpage_url", "") or d.get("url", "")),
            favorite=bool(d.get("favorite", False)),
            watched=bool(d.get("watched", False)),
            watch_position_secs=float(d.get("watch_position_secs", 0) or 0),
            bookmarks=list(d.get("bookmarks", []) or []),
            db_id=int(d.get("id", 0) or 0),
            upgrade_decision=str(d.get("upgrade_decision", "") or ""),
            upgrade_reason_code=str(d.get("upgrade_reason_code", "") or ""),
            upgrade_reason=str(d.get("upgrade_reason", "") or ""),
            upgrade_execution_status=str(
                d.get("upgrade_execution_status", "") or ""
            ),
        )


@dataclass
class MonitorEntry:
    url: str = ""
    platform: str = ""
    channel_id: str = ""
    interval_secs: int = 120
    auto_record: bool = False
    subscribe_vods: bool = False          # Check for new VODs and queue them
    capture_comments: bool = False        # Archive public comments for VOD jobs
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
    auth_profile_id: str = ""             # site-bound auth profile (V50); "" = resolve by URL
    auto_upgrade: bool = False            # re-download when higher quality VOD appears (F25)
    min_upgrade_quality: str = ""         # minimum quality to trigger upgrade (e.g. "1080p")
    upgrade_profile: dict = field(default_factory=dict)  # explicit ladder/cutoff/matchers
    media_server_layout: str = ""         # "seasoned"/"flat"; empty = global media-server layout
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
    source_id: str = ""
    webpage_url: str = ""
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
    # Credential-free diagnostics from bounded mid-capture delivery refreshes.
    # Delivery URLs never belong in this list; the active playlist_url above
    # is refreshed in place for compatibility with the existing resume flow.
    refresh_events: list[dict[str, object]] = field(default_factory=list)
    refresh_elapsed_secs: float = 0.0
    selected_tracks: list[dict[str, object]] = field(default_factory=list)
    ytdlp_source: str = ""
    ytdlp_format: str = ""
    ytdlp_format_sort: str = ""
    ytdlp_container: str = "mp4"
    ytdlp_audio_format: str = ""
    ytdlp_audio_quality: str = ""
    dub_lang: str = ""
    mute: bool = False
    allow_synthesised_tracks: bool = False
    download_subs: bool = False
    capture_youtube_chat: bool = False
    capture_comments: bool = False
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
