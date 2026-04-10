"""Playlist expansion worker — probes a URL for playlist entries via yt-dlp."""

from PyQt6.QtCore import QThread, pyqtSignal

from ..extractors.ytdlp import YtDlpExtractor


class PlaylistExpandWorker(QThread):
    """Probe a URL for playlist entries via yt-dlp --flat-playlist."""

    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            entries = YtDlpExtractor().list_playlist_entries(
                self.url, log_fn=self.log.emit,
            )
            self.finished.emit(entries)
        except Exception as e:
            self.error.emit(str(e))
