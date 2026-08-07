"""Background local indexing for a recording whose path is final.

Auto-tagging, transcript indexing and the optional semantic index all read
sidecars and write SQLite. Running them in the finalize-done slot put every
one of those on the GUI thread, so a long transcript froze the window right
at the moment the user was told the download had finished.

Each of the three steps stays best-effort and independent: a corrupt sidecar
must not cost the recording its tags, and none of them may fail the download
that produced them.
"""

from PyQt6.QtCore import QThread, pyqtSignal


class RecordingIndexWorker(QThread):
    """Run the post-download local indexes for one recording off the UI thread."""

    log = pyqtSignal(str)
    done = pyqtSignal(dict)

    def __init__(self, out_dir, info=None, parent=None):
        super().__init__(parent)
        self.out_dir = str(out_dir or "")
        self.info = info
        self._cancel = False

    def cancel(self):
        self._cancel = True
        self.requestInterruption()

    def _cancelled(self):
        return self._cancel or self.isInterruptionRequested()

    def run(self):
        result = {
            "out_dir": self.out_dir,
            "tagged": False,
            "transcript_indexed": False,
            "semantic_indexed": False,
            "cancelled": False,
        }
        if not self.out_dir:
            self.done.emit(result)
            return

        # Auto-tag recording (F35)
        if not self._cancelled():
            try:
                from ..tags import _connect, auto_tag_recording
                db = _connect()
                try:
                    auto_tag_recording(db, self.out_dir, info=self.info)
                finally:
                    db.close()
                result["tagged"] = True
            except Exception as error:
                self.log.emit(f"[INDEX] Auto-tag skipped for {self.out_dir}: {error}")

        # Index transcripts for this recording (F27)
        if not self._cancelled():
            try:
                from ..search import index_recording
                index_recording(self.out_dir)
                result["transcript_indexed"] = True
            except Exception as error:
                self.log.emit(
                    f"[INDEX] Transcript index skipped for {self.out_dir}: {error}"
                )

        # The optional semantic index is local-only and bounded.
        if not self._cancelled():
            try:
                from .. import semantic
                if semantic.is_enabled():
                    semantic.index_recording(
                        self.out_dir, cancel_check=self._cancelled,
                    )
                    result["semantic_indexed"] = True
            except Exception as error:
                self.log.emit(
                    f"[INDEX] Semantic index skipped for {self.out_dir}: {error}"
                )

        result["cancelled"] = self._cancelled()
        self.done.emit(result)
