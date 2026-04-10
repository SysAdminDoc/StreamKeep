"""Fetch worker — resolves URLs via the extractor system."""

from PyQt6.QtCore import QThread, pyqtSignal

from ..extractors import Extractor
from ..extractors.kick import KickExtractor
from ..extractors.twitch import TwitchExtractor
from ..scrape import detect_direct_media


class FetchWorker(QThread):
    """Resolves URLs using the extractor system."""

    finished = pyqtSignal(object)        # StreamInfo
    vods_found = pyqtSignal(list, str)   # list[VODInfo], platform_name
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, url, vod_source=None, vod_platform=None):
        super().__init__()
        self.url = url.strip()
        self.vod_source = vod_source
        self.vod_platform = vod_platform

    def run(self):
        try:
            if self.vod_source:
                info = self._resolve_direct(self.vod_source)
                if info:
                    self.finished.emit(info)
                else:
                    self.error.emit("Failed to resolve VOD source")
                return

            ext = Extractor.detect(self.url)
            if not ext:
                # Try direct media URL detection before giving up
                direct = detect_direct_media(self.url, log_fn=self.log.emit)
                if direct:
                    self.finished.emit(direct)
                    return
                self.error.emit("No extractor found for this URL")
                return

            # If yt-dlp fallback matched, try direct media detection first
            if ext.NAME == "yt-dlp":
                direct = detect_direct_media(self.url, log_fn=self.log.emit)
                if direct:
                    self.finished.emit(direct)
                    return

            self.log.emit(f"Detected platform: {ext.NAME}")

            if ext.supports_vod_listing():
                vods = ext.list_vods(self.url, log_fn=self.log.emit)
                if len(vods) > 1:
                    self.vods_found.emit(vods, ext.NAME)
                    return
                elif len(vods) == 1:
                    self.log.emit(f"Auto-selecting only VOD: {vods[0].title}")
                    info = self._resolve_source(vods[0], ext)
                    if info:
                        self.finished.emit(info)
                        return

            info = ext.resolve(self.url, log_fn=self.log.emit)
            if info:
                self.finished.emit(info)
            else:
                # Maybe there were VODs but none to auto-select
                if ext.supports_vod_listing():
                    vods = ext.list_vods(self.url, log_fn=self.log.emit)
                    if vods:
                        self.vods_found.emit(vods, ext.NAME)
                        return
                self.error.emit("Failed to resolve stream URL")

        except Exception as e:
            self.error.emit(str(e))

    def _resolve_direct(self, source):
        """Resolve a direct source URL (m3u8 or VOD ID)."""
        # Twitch VOD IDs are numeric strings
        if source.isdigit():
            return TwitchExtractor()._resolve_vod(source, log_fn=self.log.emit)
        # Try as m3u8 URL — use Kick extractor's generic m3u8 resolver
        return KickExtractor()._resolve_m3u8(source, log_fn=self.log.emit)

    def _resolve_source(self, vod, ext):
        """Resolve a VODInfo to StreamInfo."""
        if vod.platform == "Twitch" and vod.source.isdigit():
            return TwitchExtractor()._resolve_vod(vod.source, log_fn=self.log.emit)
        if ".m3u8" in vod.source or "stream.kick.com" in vod.source:
            return KickExtractor()._resolve_m3u8(vod.source, log_fn=self.log.emit)
        return ext.resolve(vod.source, log_fn=self.log.emit)
