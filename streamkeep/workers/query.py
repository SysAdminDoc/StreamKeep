"""Supersedable background query worker for read-only UI data refreshes."""

from PyQt6.QtCore import QThread, pyqtSignal


class QueryWorker(QThread):
    """Run one callable off the UI thread and tag its result by generation."""

    succeeded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, generation, query, parent=None):
        super().__init__(parent)
        self.generation = int(generation)
        self._query = query
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        try:
            result = self._query()
        except Exception as error:
            if not self._cancelled:
                self.failed.emit(self.generation, str(error))
            return
        if not self._cancelled:
            self.succeeded.emit(self.generation, result)


__all__ = ["QueryWorker"]
