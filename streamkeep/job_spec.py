"""Immutable, schema-versioned download job specification.

Every surface that creates a download (GUI, CLI, headless, monitor, queue,
resume) builds one ``DownloadJobSpec`` and passes it to the worker. This
eliminates the property-bag mutation pattern where seven construction sites
manually copy fields and drift over time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DownloadJobSpec:
    """Immutable specification for a download job."""

    schema_version: int = SCHEMA_VERSION

    playlist_url: str = ""
    segments: tuple[tuple[str, str], ...] = ()
    output_dir: str = ""
    format_type: str = "hls"

    audio_url: str = ""
    selected_tracks: tuple[dict, ...] = ()

    ytdlp_source: str = ""
    ytdlp_format: str = ""
    ytdlp_format_sort: str = ""
    ytdlp_container: str = "mp4"
    ytdlp_audio_format: str = ""
    ytdlp_audio_quality: str = ""

    cookies_browser: str = ""
    rate_limit: str = ""
    proxy: str = ""

    download_subs: bool = False
    subtitle_languages: str = "en.*,en"
    subtitle_auto: bool = True
    subtitle_convert: str = ""
    subtitle_embed: bool = True

    capture_youtube_chat: bool = False

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

    ytdlp_template_name: str = ""
    ytdlp_template_args: tuple[str, ...] = ()

    ytdlp_external_downloader: str = ""
    ytdlp_aria2c_connections: int = 0
    ytdlp_aria2c_splits: int = 0
    ytdlp_aria2c_min_split_size: str = ""

    hls_key_override: str = ""
    hls_key_iv: str = ""

    download_sections: str = ""
    max_retries: int = 2
    parallel_connections: int = 4
    chunk_length_secs: int = 0

    feed_url: str = ""

    def to_dict(self):
        """Serialize to a dict safe for JSON/SQLite storage (no secrets)."""
        d = asdict(self)
        d["segments"] = [list(s) for s in self.segments]
        d["selected_tracks"] = list(self.selected_tracks)
        d["ytdlp_template_args"] = list(self.ytdlp_template_args)
        d.pop("hls_key_override", None)
        d.pop("hls_key_iv", None)
        return d

    @classmethod
    def from_dict(cls, d):
        """Deserialize from a dict. Rejects unsupported future versions."""
        if not isinstance(d, dict):
            raise ValueError("DownloadJobSpec requires a dict")
        version = d.get("schema_version", 1)
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"DownloadJobSpec schema v{version} is not supported "
                f"(max v{SCHEMA_VERSION})"
            )
        segments = tuple(tuple(s) for s in d.get("segments", ()))
        selected_tracks = tuple(d.get("selected_tracks", ()))
        template_args = tuple(d.get("ytdlp_template_args", ()))
        kwargs = {}
        for f in cls.__dataclass_fields__:
            if f in ("segments", "selected_tracks", "ytdlp_template_args"):
                continue
            if f in d:
                kwargs[f] = d[f]
        return cls(
            segments=segments,
            selected_tracks=selected_tracks,
            ytdlp_template_args=template_args,
            **kwargs,
        )

    def apply_to_worker(self, worker):
        """Copy all spec fields onto a DownloadWorker instance."""
        worker.playlist_url = self.playlist_url
        worker.segments = [list(s) for s in self.segments]
        worker.output_dir = self.output_dir
        worker.format_type = self.format_type
        worker.audio_url = self.audio_url
        worker.selected_tracks = list(self.selected_tracks)
        worker.ytdlp_source = self.ytdlp_source
        worker.ytdlp_format = self.ytdlp_format
        worker.ytdlp_format_sort = self.ytdlp_format_sort
        worker.ytdlp_container = self.ytdlp_container
        worker.ytdlp_audio_format = self.ytdlp_audio_format
        worker.ytdlp_audio_quality = self.ytdlp_audio_quality
        worker.cookies_browser = self.cookies_browser
        worker.rate_limit = self.rate_limit
        worker.proxy = self.proxy
        worker.download_subs = self.download_subs
        worker.subtitle_languages = self.subtitle_languages
        worker.subtitle_auto = self.subtitle_auto
        worker.subtitle_convert = self.subtitle_convert
        worker.subtitle_embed = self.subtitle_embed
        worker.capture_youtube_chat = self.capture_youtube_chat
        worker.sponsorblock = self.sponsorblock
        worker.sponsorblock_mark = self.sponsorblock_mark
        worker.sponsorblock_remove = self.sponsorblock_remove
        worker.sponsorblock_api = self.sponsorblock_api
        worker.download_archive = self.download_archive
        worker.break_on_existing = self.break_on_existing
        worker.ytdlp_concurrent_fragments = self.ytdlp_concurrent_fragments
        worker.ytdlp_retries = self.ytdlp_retries
        worker.ytdlp_fragment_retries = self.ytdlp_fragment_retries
        worker.ytdlp_retry_sleep = self.ytdlp_retry_sleep
        worker.ytdlp_unavailable_fragments = self.ytdlp_unavailable_fragments
        worker.ytdlp_throttled_rate = self.ytdlp_throttled_rate
        worker.ytdlp_live_from_start = self.ytdlp_live_from_start
        worker.ytdlp_wait_for_video = self.ytdlp_wait_for_video
        worker.ytdlp_embed_chapters = self.ytdlp_embed_chapters
        worker.ytdlp_embed_metadata = self.ytdlp_embed_metadata
        worker.ytdlp_embed_thumbnail = self.ytdlp_embed_thumbnail
        worker.ytdlp_template_name = self.ytdlp_template_name
        worker.ytdlp_template_args = self.ytdlp_template_args
        worker.ytdlp_external_downloader = self.ytdlp_external_downloader
        worker.ytdlp_aria2c_connections = self.ytdlp_aria2c_connections
        worker.ytdlp_aria2c_splits = self.ytdlp_aria2c_splits
        worker.ytdlp_aria2c_min_split_size = self.ytdlp_aria2c_min_split_size
        worker.hls_key_override = self.hls_key_override
        worker.hls_key_iv = self.hls_key_iv
        worker.download_sections = self.download_sections
        worker.max_retries = self.max_retries
        worker.parallel_connections = self.parallel_connections
        worker.chunk_length_secs = self.chunk_length_secs

    @classmethod
    def from_worker(cls, worker):
        """Capture the current state of a DownloadWorker as a frozen spec."""
        return cls(
            playlist_url=worker.playlist_url or "",
            segments=tuple(
                tuple(s) for s in (worker.segments or [])
            ),
            output_dir=worker.output_dir or "",
            format_type=worker.format_type or "hls",
            audio_url=worker.audio_url or "",
            selected_tracks=tuple(worker.selected_tracks or []),
            ytdlp_source=worker.ytdlp_source or "",
            ytdlp_format=worker.ytdlp_format or "",
            ytdlp_format_sort=worker.ytdlp_format_sort or "",
            ytdlp_container=worker.ytdlp_container or "mp4",
            ytdlp_audio_format=worker.ytdlp_audio_format or "",
            ytdlp_audio_quality=worker.ytdlp_audio_quality or "",
            cookies_browser=worker.cookies_browser or "",
            rate_limit=worker.rate_limit or "",
            proxy=worker.proxy or "",
            download_subs=bool(worker.download_subs),
            subtitle_languages=worker.subtitle_languages or "en.*,en",
            subtitle_auto=bool(worker.subtitle_auto),
            subtitle_convert=worker.subtitle_convert or "",
            subtitle_embed=bool(worker.subtitle_embed),
            capture_youtube_chat=bool(
                getattr(worker, "capture_youtube_chat", False)
            ),
            sponsorblock=bool(worker.sponsorblock),
            sponsorblock_mark=worker.sponsorblock_mark or "",
            sponsorblock_remove=worker.sponsorblock_remove or "",
            sponsorblock_api=worker.sponsorblock_api or "",
            download_archive=worker.download_archive or "",
            break_on_existing=bool(worker.break_on_existing),
            ytdlp_concurrent_fragments=int(
                worker.ytdlp_concurrent_fragments or 0
            ),
            ytdlp_retries=worker.ytdlp_retries or "",
            ytdlp_fragment_retries=worker.ytdlp_fragment_retries or "",
            ytdlp_retry_sleep=worker.ytdlp_retry_sleep or "",
            ytdlp_unavailable_fragments=(
                worker.ytdlp_unavailable_fragments or ""
            ),
            ytdlp_throttled_rate=worker.ytdlp_throttled_rate or "",
            ytdlp_live_from_start=bool(worker.ytdlp_live_from_start),
            ytdlp_wait_for_video=worker.ytdlp_wait_for_video or "",
            ytdlp_embed_chapters=worker.ytdlp_embed_chapters,
            ytdlp_embed_metadata=worker.ytdlp_embed_metadata,
            ytdlp_embed_thumbnail=worker.ytdlp_embed_thumbnail,
            ytdlp_template_name=worker.ytdlp_template_name or "",
            ytdlp_template_args=tuple(worker.ytdlp_template_args or ()),
            ytdlp_external_downloader=(
                worker.ytdlp_external_downloader or ""
            ),
            ytdlp_aria2c_connections=int(
                worker.ytdlp_aria2c_connections or 0
            ),
            ytdlp_aria2c_splits=int(worker.ytdlp_aria2c_splits or 0),
            ytdlp_aria2c_min_split_size=(
                worker.ytdlp_aria2c_min_split_size or ""
            ),
            hls_key_override=worker.hls_key_override or "",
            hls_key_iv=worker.hls_key_iv or "",
            download_sections=worker.download_sections or "",
            max_retries=int(getattr(worker, "max_retries", 2) or 2),
            parallel_connections=int(
                getattr(worker, "parallel_connections", 4) or 4
            ),
            chunk_length_secs=int(
                getattr(worker, "chunk_length_secs", 0) or 0
            ),
        )
