"""Fetch worker — resolves URLs via the extractor system."""

import copy

from PyQt6.QtCore import QThread, pyqtSignal

from ..extractors import Extractor
from ..extractors.kick import KickExtractor
from ..extractors.twitch import TwitchExtractor
from ..har import normalize_replay_headers
from ..http import http_interruptible
from ..retry import classify_failure, sanitize_failure_reason
from ..scrape import detect_direct_media


class FetchWorker(QThread):
    """Resolves URLs using the extractor system."""

    finished = pyqtSignal(object)        # StreamInfo
    vods_found = pyqtSignal(list, str, object)  # list[VODInfo], platform_name, next_cursor
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(
        self, url, vod_source=None, vod_platform=None, vod_title=None,
        vod_channel=None, source_id=None, webpage_url=None,
        request_headers=None,
    ):
        super().__init__()
        self.url = url.strip()
        self.vod_source = vod_source
        self.vod_platform = vod_platform
        self.vod_title = str(vod_title or "")
        self.vod_channel = str(vod_channel or "")
        self.source_id = str(source_id or "")
        self.webpage_url = str(webpage_url or "")
        self.request_headers = normalize_replay_headers(request_headers)
        self._last_resolve_error = ""

    def _interrupted(self):
        return self.isInterruptionRequested()

    def _capture_resolve_log(self, message):
        """Forward extractor logs while retaining the last useful failure."""
        text = str(message or "")
        self.log.emit(sanitize_failure_reason(text))
        decision = classify_failure(text)
        lowered = text.casefold()
        if (
            decision.category != "unknown"
            or " error" in lowered
            or "failed" in lowered
        ):
            self._last_resolve_error = text

    def _resolve_failure(self, fallback):
        return self._last_resolve_error or str(fallback)

    def _resolve_or_fallback(self, ext, url):
        """Resolve *url* with the detected extractor, falling back to the
        yt-dlp catch-all when a platform-specific extractor fails.

        Native extractors (Kick, Twitch, Rumble, SoundCloud, …) exist for the
        features yt-dlp lacks (live-check, VOD listing, clip recovery), but a
        site can change its markup/API at any time and break them. yt-dlp is
        broadly maintained and supports 1700+ sites, so treating it as a
        second chance keeps a one-off native breakage from turning into a hard
        "Failed to resolve" for the user. yt-dlp already runs directly for
        catch-all URLs, so the fallback is skipped there to avoid double work.
        """
        info = None
        try:
            info = ext.resolve(url, log_fn=self._capture_resolve_log)
        except Exception as e:  # noqa: BLE001 — any extractor failure is fallback-worthy
            if self._interrupted():
                raise
            self._capture_resolve_log(f"[{ext.NAME}] resolve error: {e}")
        if info or self._interrupted():
            return info
        if ext.NAME == "yt-dlp":
            return None
        self.log.emit(
            f"[FALLBACK] {ext.NAME} could not resolve this URL - retrying with yt-dlp..."
        )
        from ..extractors.ytdlp import YtDlpExtractor
        try:
            fallback = YtDlpExtractor()
            fallback.request_headers = dict(self.request_headers)
            info = fallback.resolve(
                url, log_fn=self._capture_resolve_log
            )
        except Exception as e:  # noqa: BLE001
            if self._interrupted():
                raise
            self._capture_resolve_log(f"[FALLBACK] yt-dlp also failed: {e}")
            return None
        if info:
            self.log.emit("[FALLBACK] yt-dlp resolved the URL successfully.")
        return info

    def run(self):
        try:
            with http_interruptible(self._interrupted):
                if self._interrupted():
                    return
                if self.vod_source:
                    info = self._resolve_direct(self.vod_source)
                    if self._interrupted():
                        return
                    if info:
                        self.finished.emit(info)
                    else:
                        self.error.emit(
                            self._resolve_failure(
                                "Failed to resolve VOD source"
                            )
                        )
                    return

                ext = Extractor.detect(self.url)
                if self._interrupted():
                    return
                if not ext:
                    # Try direct media URL detection before giving up
                    direct = detect_direct_media(
                        self.url,
                        log_fn=self._capture_resolve_log,
                        headers=self.request_headers,
                    )
                    if self._interrupted():
                        return
                    if direct:
                        self.finished.emit(direct)
                        return
                    self.error.emit("No extractor found for this URL")
                    return

                # If yt-dlp fallback matched, try direct media detection first
                if ext.NAME == "yt-dlp":
                    ext.request_headers = dict(self.request_headers)
                    direct = detect_direct_media(
                        self.url,
                        log_fn=self._capture_resolve_log,
                        headers=self.request_headers,
                    )
                    if self._interrupted():
                        return
                    if direct:
                        self.finished.emit(direct)
                        return

                self.log.emit(f"Detected platform: {ext.NAME}")
                try:
                    ext.request_headers = dict(self.request_headers)
                except Exception:
                    pass  # safe: best-effort fallback; preserve the primary operation

                # A direct permalink (e.g. a VOD-by-UUID URL) resolves to a
                # single item — skip live-check / channel-wide VOD listing,
                # which would target the channel rather than this URL.
                if ext.is_direct_url(self.url):
                    info = self._resolve_or_fallback(ext, self.url)
                    if self._interrupted():
                        return
                    if info:
                        self.finished.emit(info)
                    else:
                        self.error.emit(
                            self._resolve_failure(
                                "Failed to resolve stream URL"
                            )
                        )
                    return

                if ext.supports_live_check():
                    is_live = False
                    try:
                        is_live = bool(ext.check_live(self.url))
                    except Exception as live_err:
                        self.log.emit(f"[LIVE CHECK] {live_err}")
                    if self._interrupted():
                        return
                    if is_live:
                        info = self._resolve_or_fallback(ext, self.url)
                        if self._interrupted():
                            return
                        if info:
                            self.finished.emit(info)
                            return
                        self.log.emit("[LIVE CHECK] Live source detected but resolve failed; falling back to VOD lookup.")

                if ext.supports_vod_listing():
                    vods, next_cursor = ext.list_vods(
                        self.url, log_fn=self._capture_resolve_log
                    )
                    if self._interrupted():
                        return
                    if len(vods) > 1:
                        self.vods_found.emit(vods, ext.NAME, next_cursor)
                        return
                    elif len(vods) == 1:
                        self.log.emit(f"Auto-selecting only VOD: {vods[0].title}")
                        info = self._resolve_source(vods[0], ext)
                        if self._interrupted():
                            return
                        if info:
                            self.finished.emit(info)
                            return

                info = self._resolve_or_fallback(ext, self.url)
                if self._interrupted():
                    return
                if info:
                    self.finished.emit(info)
                else:
                    # Maybe there were VODs but none to auto-select
                    if ext.supports_vod_listing():
                        vods, next_cursor = ext.list_vods(
                            self.url, log_fn=self._capture_resolve_log
                        )
                        if self._interrupted():
                            return
                        if vods:
                            self.vods_found.emit(vods, ext.NAME, next_cursor)
                            return
                    self.error.emit(
                        self._resolve_failure("Failed to resolve stream URL")
                    )

        except Exception as e:
            if not self._interrupted():
                self.error.emit(str(e))

    def _apply_vod_metadata(
        self, info, platform="", title="", channel="", source_id="",
        webpage_url="", feed_url="", thumbnail_url="", podcast_metadata=None,
    ):
        if info is None:
            return None
        if platform and not getattr(info, "platform", ""):
            info.platform = platform
        if title and not getattr(info, "title", ""):
            info.title = title
        if channel and not getattr(info, "channel", ""):
            info.channel = channel
        if source_id and (
            not getattr(info, "source_id", "")
            or str(platform or "").casefold() == "podcast"
        ):
            info.source_id = source_id
        if webpage_url and (
            not getattr(info, "webpage_url", "")
            or str(platform or "").casefold() == "podcast"
        ):
            info.webpage_url = webpage_url
        if feed_url and not getattr(info, "feed_url", ""):
            info.feed_url = feed_url
        if thumbnail_url and not getattr(info, "thumbnail_url", ""):
            info.thumbnail_url = thumbnail_url
        if podcast_metadata and not getattr(info, "podcast_metadata", None):
            info.podcast_metadata = copy.deepcopy(podcast_metadata)
        from ..metadata import build_archival_provenance
        provenance = build_archival_provenance(
            info,
            source_url=getattr(info, "webpage_url", "") or "",
        )
        info.source_id = provenance.source_id
        info.webpage_url = provenance.webpage_url
        return info

    def _resolve_direct(self, source):
        """Resolve a direct source URL (m3u8 or VOD ID)."""
        # Twitch VOD IDs are numeric strings
        if source.isdigit():
            info = TwitchExtractor()._resolve_vod(
                source, log_fn=self._capture_resolve_log
            )
            return self._apply_vod_metadata(
                info,
                platform=self.vod_platform or "Twitch",
                title=self.vod_title,
                channel=self.vod_channel,
                source_id=self.source_id,
                webpage_url=self.webpage_url,
            )
        # Try as m3u8 URL — use Kick extractor's generic m3u8 resolver
        info = KickExtractor()._resolve_m3u8(
            source,
            log_fn=self._capture_resolve_log,
            headers=self.request_headers,
        )
        return self._apply_vod_metadata(
            info,
            platform=self.vod_platform,
            title=self.vod_title,
            channel=self.vod_channel,
            source_id=self.source_id,
            webpage_url=self.webpage_url,
        )

    def _resolve_source(self, vod, ext):
        """Resolve a VODInfo to StreamInfo."""
        if vod.platform == "Twitch" and vod.source.isdigit():
            info = TwitchExtractor()._resolve_vod(
                vod.source, log_fn=self._capture_resolve_log
            )
        elif ".m3u8" in vod.source or "stream.kick.com" in vod.source:
            info = KickExtractor()._resolve_m3u8(
                vod.source, log_fn=self._capture_resolve_log
            )
        else:
            info = ext.resolve(
                vod.source, log_fn=self._capture_resolve_log
            )
        return self._apply_vod_metadata(
            info,
            platform=getattr(vod, "platform", ""),
            title=getattr(vod, "title", ""),
            channel=getattr(vod, "channel", ""),
            source_id=getattr(vod, "source_id", ""),
            webpage_url=getattr(vod, "webpage_url", ""),
            feed_url=getattr(vod, "feed_url", ""),
            thumbnail_url=getattr(vod, "thumbnail_url", ""),
            podcast_metadata=getattr(vod, "podcast_metadata", None),
        )


class VodPageWorker(QThread):
    """Fetches the next page of VODs for a channel (pagination)."""

    page_ready = pyqtSignal(list, object)  # list[VODInfo], next_cursor
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, url, cursor):
        super().__init__()
        self.url = url.strip()
        self.cursor = cursor

    def _interrupted(self):
        return self.isInterruptionRequested()

    def run(self):
        try:
            from ..http import http_interruptible
            with http_interruptible(self._interrupted):
                ext = Extractor.detect(self.url)
                if not ext or not ext.supports_vod_listing():
                    self.error.emit("Extractor does not support VOD listing")
                    return
                vods, next_cursor = ext.list_vods(
                    self.url, log_fn=self.log.emit, cursor=self.cursor,
                )
                if self._interrupted():
                    return
                self.page_ready.emit(vods, next_cursor)
        except Exception as e:
            if not self._interrupted():
                self.error.emit(str(e))
