"""Playlist expansion worker — probes a URL for playlist entries via yt-dlp."""

from PyQt6.QtCore import QThread, pyqtSignal

from .. import db
from ..http import http_interruptible
from ..extractors.ytdlp import YtDlpExtractor


class PlaylistExpandWorker(QThread):
    """Probe a URL for playlist entries via yt-dlp --flat-playlist."""

    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, url, **options):
        super().__init__()
        self.url = url
        self.options = dict(options)

    def _interrupted(self):
        return self.isInterruptionRequested()

    def run(self):
        try:
            with http_interruptible(self._interrupted):
                if self._interrupted():
                    return
                entries = YtDlpExtractor().list_playlist_entries(
                    self.url, log_fn=self.log.emit,
                    **self.options,
                )
                if self._interrupted():
                    return
                filtered = []
                for entry in entries or []:
                    try:
                        tombstone = db.find_tombstone_for_item({
                            "url": entry.get("url", ""),
                            "webpage_url": entry.get("webpage_url", "")
                            or entry.get("url", ""),
                            "source_id": entry.get("id", ""),
                            "platform": "yt-dlp",
                            "title": entry.get("title", ""),
                        })
                    except Exception:
                        tombstone = None
                    if tombstone is not None:
                        identity = (
                            entry.get("id", "")
                            or entry.get("webpage_url", "")
                            or entry.get("url", "")
                        )
                        self.log.emit(
                            f"[PLAYLIST] Skipped tombstoned media {identity}"
                        )
                        continue
                    filtered.append(entry)
                self.finished.emit(filtered)
        except Exception as e:
            if not self._interrupted():
                self.error.emit(str(e))
