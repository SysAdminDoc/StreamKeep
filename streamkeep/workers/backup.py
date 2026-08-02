"""Worker that runs one due automatic profile backup off the UI thread."""

from PyQt6.QtCore import QThread, pyqtSignal

from ..backup import run_scheduled_backup


class ScheduledBackupWorker(QThread):
    """Claim and run one due rotating backup for the execution owner.

    The worker is a no-op unless the durable claim in ``backup_runs`` says a
    run is due, so it is safe to start it on a short scheduler tick. Only one
    instance should be alive per process; overlap across processes is already
    prevented by the claim.
    """

    finished_run = pyqtSignal(bool, str, dict)

    def __init__(self, config, owner_id, parent=None):
        super().__init__(parent)
        self.config = dict(config or {})
        self.owner_id = str(owner_id or "")
        self._message = ""

    def _log(self, text):
        self._message = str(text or "")

    def run(self):
        try:
            state = run_scheduled_backup(
                self.config, self.owner_id, log_fn=self._log,
            )
        except Exception as e:  # never let a backup take down the scheduler
            self.finished_run.emit(False, f"[BACKUP] Backup failed: {e}", {})
            return
        if state is None:
            return
        ok = not str(state.get("last_error", "") or "")
        self.finished_run.emit(ok, self._message, dict(state))
