"""Global crash handler. Writes tracebacks to crash.log and shows a MessageBox."""

import sys
from datetime import datetime

from . import VERSION
from .paths import CONFIG_DIR, CRASH_LOG

_CRASH_LOG_MAX_BYTES = 2_000_000
_CRASH_LOG_BACKUP = CRASH_LOG.parent / "crash.log.1"


def _rotate_crash_log():
    try:
        if CRASH_LOG.exists() and CRASH_LOG.stat().st_size > _CRASH_LOG_MAX_BYTES:
            if _CRASH_LOG_BACKUP.exists():
                _CRASH_LOG_BACKUP.unlink()
            CRASH_LOG.rename(_CRASH_LOG_BACKUP)
    except OSError:
        pass


def _append_entry(header, body):
    """Append one rotated, timestamped entry to ``crash.log``.

    Shared by the exception handler and by callers that need to record a
    non-fatal condition the operator must still see.
    """
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_crash_log()
        with open(CRASH_LOG, "a", encoding="utf-8") as handle:
            handle.write("\n" + "=" * 60 + "\n")
            handle.write(
                f"StreamKeep v{VERSION} {header} at "
                f"{datetime.now().isoformat()}\n"
            )
            text = str(body)
            handle.write(text if text.endswith("\n") else text + "\n")
        return True
    except Exception:
        return False


def record_startup_warning(detail):
    """Record a startup condition the operator needs to know about.

    Crash recovery runs before anything is on screen. When a rollback of a
    half-completed restore, rebuild or re-template fails, the app continues
    against a mixed config directory -- so the failure has to leave a trace
    somewhere the user will find it, not only in a rotating app log (V185).
    """
    return _append_entry("startup warning", str(detail))


def setup_crash_logging():
    """Install a global exception handler. Safe to call multiple times."""
    def handler(exc_type, exc_value, exc_tb):
        import traceback
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _rotate_crash_log()
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n")
                f.write(
                    f"StreamKeep v{VERSION} crash at "
                    f"{datetime.now().isoformat()}\n"
                )
                f.write(tb_str)
        except Exception:
            pass  # safe: best-effort fallback; preserve the primary operation
        # Show MessageBox if a QApplication already exists
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance()
            if app:
                QMessageBox.critical(
                    None,
                    "StreamKeep — Crash",
                    f"An unexpected error occurred:\n\n{exc_value}\n\n"
                    f"Details logged to:\n{CRASH_LOG}",
                )
        except Exception:
            pass  # safe: best-effort fallback; preserve the primary operation
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = handler
