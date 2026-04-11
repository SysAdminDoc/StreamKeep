"""Shared data classes — no runtime dependencies on anything but stdlib."""

from dataclasses import dataclass, field


@dataclass
class QualityInfo:
    name: str = ""
    url: str = ""
    resolution: str = ""
    bandwidth: int = 0
    format_type: str = "hls"       # hls, mp4, dash, ytdlp_direct
    audio_url: str = ""             # If set, video is video-only and needs audio merge
    ytdlp_source: str = ""          # Original page URL for ytdlp_direct downloads
    ytdlp_format: str = ""          # Format spec (e.g. "137+140")


@dataclass
class StreamInfo:
    platform: str = ""
    channel: str = ""
    title: str = ""
    url: str = ""
    qualities: list = field(default_factory=list)
    total_secs: float = 0
    duration_str: str = ""
    start_time: str = ""
    is_live: bool = False
    is_master: bool = False
    segment_count: int = 0
    thumbnail_url: str = ""
    chapters: list = field(default_factory=list)  # list of {title, start, end}


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
    archive_ids: list = field(default_factory=list)  # already-seen VOD source IDs
    _cancel_requested: bool = field(default=False, repr=False, compare=False)
