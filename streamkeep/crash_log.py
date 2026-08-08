"""Global crash handler. Writes tracebacks to crash.log and shows a MessageBox."""

import functools
import sys
import threading
from datetime import datetime

from . import VERSION
from .paths import CONFIG_DIR, CRASH_LOG

_CRASH_LOG_MAX_BYTES = 2_000_000
_CRASH_LOG_BACKUP = CRASH_LOG.parent / "crash.log.1"
#: Captured before installing ours so the default reporting still happens.
_previous_thread_hook = threading.excepthook


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


def record_crash(exc_type, exc_value, exc_tb, *, context=""):
    """Write one crash to ``crash.log`` and tell the user, if there is a UI.

    Shared by the main-thread hook and the thread hook so a crash is recorded
    the same way wherever it happened. Returns whether the log entry was written.
    """
    import traceback
    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    header = f"crash in {context}" if context else "crash"
    written = _append_entry(header, tb_str)
    try:
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        # Constructing a widget off the GUI thread is an access violation, not
        # an exception -- so a crash reporter that did it would take the process
        # down harder than the crash it was reporting. The log entry above is
        # the part that always runs; the dialog is main-thread only.
        on_gui_thread = bool(app) and QThread.currentThread() == app.thread()
        if on_gui_thread:
            where = f" ({context})" if context else ""
            QMessageBox.critical(
                None,
                "StreamKeep — Crash",
                f"An unexpected error occurred{where}:\n\n{exc_value}\n\n"
                f"Details logged to:\n{CRASH_LOG}",
            )
    except Exception:
        pass  # safe: best-effort fallback; preserve the primary operation
    return written


def setup_crash_logging():
    """Install global exception handlers for the main thread and for threads.

    ``sys.excepthook`` never fires for an exception raised inside a thread, and
    this app runs nearly everything in one: 41 ``QThread`` subclasses and a
    dozen raw ``threading.Thread`` sites. So a failing download, capture,
    finalize, transcribe, index, backup or health worker wrote no ``crash.log``
    entry and showed no dialog -- and because the shipped executable is built
    with ``console=False`` the interpreter's fallback traceback went to a
    discarded stderr, making the failure invisible in the release build (V217).

    Safe to call multiple times.
    """
    def handler(exc_type, exc_value, exc_tb):
        record_crash(exc_type, exc_value, exc_tb, context="the main thread")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def thread_handler(args):
        # ``threading.excepthook`` covers ``threading.Thread`` targets. A
        # ``QThread`` with an overridden ``run()`` is a Qt thread rather than a
        # Python one, so it is covered by ``guard_thread_run`` instead.
        if args.exc_type is SystemExit:
            return
        name = getattr(args.thread, "name", "") or "a background thread"
        record_crash(
            args.exc_type, args.exc_value, args.exc_traceback,
            context=f"thread {name}",
        )
        _previous_thread_hook(args)

    sys.excepthook = handler
    threading.excepthook = thread_handler
    install_worker_guards()


def guard_thread_run(run):
    """Wrap a ``QThread.run`` so an escaping exception is recorded.

    ``threading.excepthook`` does not see a ``QThread`` whose ``run()`` is
    overridden -- PyQt calls it on a Qt thread, and an exception escaping it
    aborts the process rather than raising anywhere Python can observe. Applying
    this decorator is what makes a worker crash visible (V217).
    """
    @functools.wraps(run)
    def guarded(self, *args, **kwargs):
        try:
            return run(self, *args, **kwargs)
        except Exception:
            record_crash(
                *sys.exc_info(),
                context=f"worker {type(self).__name__}",
            )
            return None
    guarded.__streamkeep_guarded__ = True
    return guarded


def install_worker_guards():
    """Guard ``run`` on every imported ``QThread`` subclass. Idempotent.

    Walking the subclass tree rather than editing 41 class bodies means a worker
    added later is covered the day it is imported instead of the day someone
    remembers to decorate it -- the same reasoning as the teardown sweep in
    ``ui/worker_teardown.py``. Safe to call again after a lazy import, because a
    guard already applied is recognised and skipped.

    Returns the number of ``run`` methods newly wrapped.
    """
    try:
        from PyQt6.QtCore import QThread
    except Exception:
        return 0

    wrapped = 0
    seen = set()

    def walk(cls):
        nonlocal wrapped
        for subclass in cls.__subclasses__():
            if id(subclass) in seen:
                continue
            seen.add(id(subclass))
            run = subclass.__dict__.get("run")
            if run is not None and not getattr(run, "__streamkeep_guarded__", False):
                subclass.run = guard_thread_run(run)
                wrapped += 1
            walk(subclass)

    walk(QThread)
    return wrapped
