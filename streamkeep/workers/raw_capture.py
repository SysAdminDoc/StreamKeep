"""Qt worker wrapper for validated raw-protocol capture jobs."""

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from ..raw_capture import RawCaptureSpec, run_raw_capture


class RawCaptureWorker(QThread):
    """Run one :class:`RawCaptureSpec` without blocking the UI thread."""

    line = pyqtSignal(str)
    completed = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, spec: RawCaptureSpec):
        super().__init__()
        self.spec = spec
        self._stop_event = threading.Event()

    def cancel(self):
        self._stop_event.set()

    def run(self):
        try:
            result = run_raw_capture(
                self.spec,
                stop_event=self._stop_event,
                on_line=self.line.emit,
            )
            self.completed.emit(result)
        except Exception as error:
            self.error.emit(str(error))
