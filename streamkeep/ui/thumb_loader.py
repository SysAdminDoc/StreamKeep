"""ThumbLoader — throttled async thumbnail loader for table views.

Usage: one instance per table. Call `request(row_key, media_path)` — when
the thumb is ready (either from disk cache or freshly generated) the
`thumb_ready(row_key, qpixmap)` signal fires. At most
`max_concurrent` ffmpeg jobs run at the same time.
"""

import os
from collections import deque

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap

from ..postprocess import ThumbWorker, single_thumb_path


class ThumbLoader(QObject):
    thumb_ready = pyqtSignal(object, QPixmap)   # row_key, pixmap

    def __init__(self, parent=None, *, max_concurrent=2, size=(160, 90)):
        super().__init__(parent)
        self._pending = deque()                  # (row_key, media_path)
        self._in_flight = {}                     # row_key -> ThumbWorker
        self._max = int(max_concurrent)
        self._size = size

    def request(self, row_key, media_path):
        """Queue a thumbnail request. If the cache already has one, emits
        immediately on the event loop. De-dups by row_key."""
        if not media_path or not os.path.exists(media_path):
            return
        if row_key in self._in_flight:
            return
        cache = single_thumb_path(media_path)
        if cache and os.path.exists(cache) and os.path.getsize(cache) > 0:
            pix = QPixmap(cache)
            if not pix.isNull():
                self.thumb_ready.emit(row_key, pix)
                return
        # Remove any stale pending entry for this row (scroll re-requests).
        self._pending = deque(
            (k, p) for (k, p) in self._pending if k != row_key
        )
        self._pending.append((row_key, media_path))
        self._pump()

    def clear(self):
        """Forget all pending requests (but let in-flight workers finish)."""
        self._pending.clear()

    def _pump(self):
        while self._pending and len(self._in_flight) < self._max:
            row_key, media_path = self._pending.popleft()
            worker = ThumbWorker(media_path, count=1, width=self._size[0])
            worker.thumb_ready.connect(
                lambda _idx, path, rk=row_key:
                self._on_worker_thumb(rk, path)
            )
            worker.done.connect(
                lambda _ok, rk=row_key: self._on_worker_done(rk)
            )
            self._in_flight[row_key] = worker
            worker.start()

    def _on_worker_thumb(self, row_key, path):
        pix = QPixmap(path)
        if not pix.isNull():
            self.thumb_ready.emit(row_key, pix)

    def _on_worker_done(self, row_key):
        w = self._in_flight.pop(row_key, None)
        if w is not None:
            try:
                w.wait(100)
            except Exception:
                pass
        self._pump()
