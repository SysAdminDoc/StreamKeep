"""Config persistence + rotating log file + structured logging bridge.

Free-form JSON dict for now. A typed dataclass wrapper lands in Phase 3b.
"""

import json
import logging
import os
import threading
from datetime import datetime

from .paths import CONFIG_DIR, CONFIG_FILE, LOG_FILE, LOG_FILE_BACKUP, LOG_FILE_MAX_BYTES

# Serializes config writes and log rotation across threads (QTimer polling,
# clipboard monitor, download workers) so two writers can't corrupt the file.
_SAVE_LOCK = threading.Lock()
_LOG_LOCK = threading.Lock()


def load_config():
    """Load the config JSON.

    Falls back to the last-known-good ``config.json.bak`` if the primary
    file is missing or corrupted. Returns ``{}`` on any unrecoverable error.
    """
    for candidate in (
        CONFIG_FILE,
        CONFIG_FILE.with_suffix(".json.bak"),
    ):
        try:
            if not candidate.exists():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def save_config(cfg):
    """Persist the config dict atomically. Silent on error.

    Writes to `config.json.tmp` then renames into place so a mid-write
    crash leaves the previous config intact. Also rotates a last-known-good
    sibling `config.json.bak` before each successful replace.
    """
    with _SAVE_LOCK:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".json.tmp")
            payload = json.dumps(cfg, indent=2, ensure_ascii=False)
            # Write + fsync so a power loss doesn't leave a zero-byte tmp.
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass
            # Keep a one-deep backup before the atomic replace.
            if CONFIG_FILE.exists():
                try:
                    bak = CONFIG_FILE.with_suffix(".json.bak")
                    if bak.exists():
                        bak.unlink()
                    CONFIG_FILE.replace(bak)
                except OSError:
                    pass
            os.replace(tmp, CONFIG_FILE)
        except Exception:
            # Best-effort cleanup of stale tmp on failure.
            try:
                tmp = CONFIG_FILE.with_suffix(".json.tmp")
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass


def write_log_line(msg):
    """Append a timestamped line to the rotating log file.
    Rotates streamkeep.log -> streamkeep.log.1 when it exceeds the cap."""
    with _LOG_LOCK:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            try:
                if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_FILE_MAX_BYTES:
                    if LOG_FILE_BACKUP.exists():
                        try:
                            LOG_FILE_BACKUP.unlink()
                        except OSError:
                            pass
                    try:
                        LOG_FILE.rename(LOG_FILE_BACKUP)
                    except (FileNotFoundError, OSError):
                        pass
            except OSError:
                pass
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass


# ── Structured logging bridge ──────────────────────────────────────

_LEVEL_LABELS = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARN",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRIT",
}


class GuiLogHandler(logging.Handler):
    """Forwards logging records to a callable (e.g. main window ``_log``).

    Install via ``install_gui_logging(callback)`` — attaches to the
    ``streamkeep`` root logger and writes through to the rotating log
    file as well.  Duplicate suppression: if a record with the same
    message was emitted within the last second, it is counted but not
    forwarded until the burst ends.

    The callback is invoked via QTimer.singleShot(0, ...) so it runs on
    the Qt main thread regardless of which thread emitted the log record.
    """

    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        self._last_msg = ""
        self._last_time = 0.0
        self._suppress_count = 0

    def _invoke_on_main_thread(self, text):
        try:
            from PyQt6.QtCore import QCoreApplication, QThread, QTimer
            app = QCoreApplication.instance()
            if app is None or QThread.currentThread() is app.thread():
                self._callback(text)
            else:
                QTimer.singleShot(0, lambda: self._callback(text))
        except Exception:
            self._callback(text)

    def emit(self, record):
        try:
            module = record.name.rsplit(".", 1)[-1] if record.name else ""
            level = _LEVEL_LABELS.get(record.levelno, str(record.levelno))
            msg = self.format(record) if self.formatter else record.getMessage()

            now = record.created
            if msg == self._last_msg and (now - self._last_time) < 1.0:
                self._suppress_count += 1
                self._last_time = now
                return
            if self._suppress_count > 0:
                self._invoke_on_main_thread(
                    f"[{module}] {level}: (repeated {self._suppress_count}x)"
                )
                self._suppress_count = 0

            self._last_msg = msg
            self._last_time = now
            formatted = f"[{module}] {level}: {msg}"
            self._invoke_on_main_thread(formatted)
            write_log_line(formatted)
        except Exception:
            pass


class FileLogHandler(logging.Handler):
    """Writes logging records to the rotating log file only."""

    def emit(self, record):
        try:
            msg = self.format(record) if self.formatter else record.getMessage()
            module = record.name.rsplit(".", 1)[-1] if record.name else ""
            level = _LEVEL_LABELS.get(record.levelno, str(record.levelno))
            write_log_line(f"[{module}] {level}: {msg}")
        except Exception:
            pass


def install_gui_logging(callback, *, level=logging.INFO):
    """Attach a ``GuiLogHandler`` to the ``streamkeep`` root logger.

    Call once at app startup. Returns the handler so it can be removed
    later if needed.
    """
    root = logging.getLogger("streamkeep")
    root.setLevel(level)
    for h in list(root.handlers):
        if isinstance(h, (GuiLogHandler, FileLogHandler)):
            root.removeHandler(h)
    handler = GuiLogHandler(callback)
    root.addHandler(handler)
    return handler


def install_file_logging(*, level=logging.WARNING):
    """Attach a ``FileLogHandler`` to the ``streamkeep`` root logger.

    Used in CLI/headless mode where there is no GUI log panel.
    """
    root = logging.getLogger("streamkeep")
    root.setLevel(level)
    for h in list(root.handlers):
        if isinstance(h, FileLogHandler):
            root.removeHandler(h)
    handler = FileLogHandler()
    root.addHandler(handler)
    return handler
