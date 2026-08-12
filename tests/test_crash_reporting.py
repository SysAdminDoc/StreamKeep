"""A crash must be recorded wherever it happens, not only on the main thread.

``sys.excepthook`` never fires for an exception raised inside a thread, and this
app runs nearly everything in one -- 41 ``QThread`` subclasses and a dozen raw
``threading.Thread`` sites. Combined with a ``console=False`` release build,
whose stderr is discarded, a failing download, capture, transcribe, index or
health worker produced no ``crash.log`` entry, no dialog and no stderr at all
(V217).
"""

import sys
import threading

import pytest
from PyQt6.QtCore import QThread

from streamkeep import crash_log


@pytest.fixture
def crash_log_path(tmp_path, monkeypatch):
    """Point the crash log at a temp file for the duration of a test."""
    target = tmp_path / "crash.log"
    monkeypatch.setattr(crash_log, "CRASH_LOG", target)
    monkeypatch.setattr(crash_log, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(crash_log, "_CRASH_LOG_BACKUP", tmp_path / "crash.log.1")
    return target


@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)
def test_a_raw_thread_crash_reaches_the_crash_log(crash_log_path, monkeypatch):
    recorded = []
    monkeypatch.setattr(
        crash_log, "record_crash",
        lambda *a, **k: recorded.append(k.get("context", "")),
    )
    crash_log.setup_crash_logging()

    def _boom():
        raise RuntimeError("worker exploded")

    thread = threading.Thread(target=_boom, name="capture-1")
    thread.start()
    thread.join(5)

    assert recorded, "threading.excepthook never reached the crash recorder"
    assert any("capture-1" in context for context in recorded), recorded


def test_a_qthread_run_crash_reaches_the_crash_log(qt_application, crash_log_path):
    class _Exploding(QThread):
        def run(self):
            raise RuntimeError("qthread exploded")

    crash_log.install_worker_guards()
    worker = _Exploding()
    worker.start()
    assert worker.wait(5000), "the worker never finished"

    text = crash_log_path.read_text(encoding="utf-8")
    assert "qthread exploded" in text
    assert "_Exploding" in text, "the entry must name the worker class"


def test_a_raised_exception_rotates_and_writes_a_new_crash_entry(
    qt_application, crash_log_path, monkeypatch,
):
    previous = "previous crash\n" * 8
    crash_log_path.write_text(previous, encoding="utf-8")
    monkeypatch.setattr(crash_log, "_CRASH_LOG_MAX_BYTES", 32)
    monkeypatch.setattr(
        "PyQt6.QtWidgets.QMessageBox.critical", lambda *_args, **_kwargs: None,
    )

    try:
        raise RuntimeError("new crash")
    except RuntimeError:
        written = crash_log.record_crash(
            *sys.exc_info(), context="rotation test",
        )

    assert written is True
    assert crash_log._CRASH_LOG_BACKUP.read_text(encoding="utf-8") == previous
    text = crash_log_path.read_text(encoding="utf-8")
    assert "new crash" in text
    assert "rotation test" in text


def test_crash_recording_survives_an_unwritable_log(
    qt_application, crash_log_path, monkeypatch,
):
    del crash_log_path
    monkeypatch.setattr(
        "builtins.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read only")),
    )
    monkeypatch.setattr(
        "PyQt6.QtWidgets.QMessageBox.critical", lambda *_args, **_kwargs: None,
    )

    written = crash_log.record_crash(
        RuntimeError, RuntimeError("cannot log"), None,
        context="unwritable test",
    )

    assert written is False


def test_the_guard_is_idempotent(qt_application):
    class _Quiet(QThread):
        def run(self):
            return None

    first = crash_log.install_worker_guards()
    second = crash_log.install_worker_guards()
    assert first >= 1
    assert second == 0, "re-installing must not double-wrap"
    assert getattr(_Quiet.run, "__streamkeep_guarded__", False)


def test_a_guarded_run_still_returns_its_value():
    class _Fake:
        def run(self):
            return "done"

    guarded = crash_log.guard_thread_run(_Fake.run)
    assert guarded(_Fake()) == "done"


def test_systemexit_from_a_thread_is_not_reported_as_a_crash(monkeypatch):
    """A cancelled worker raising SystemExit is not a defect."""
    recorded = []
    monkeypatch.setattr(
        crash_log, "record_crash", lambda *a, **k: recorded.append(k))
    crash_log.setup_crash_logging()

    def _exit():
        raise SystemExit(0)

    thread = threading.Thread(target=_exit, name="quitting")
    thread.start()
    thread.join(5)

    assert not recorded, "SystemExit must not be logged as a crash"


def test_every_qthread_subclass_in_the_tree_is_guarded(qt_application):
    """Derived, not restated: a new worker is covered when it is imported.

    Importing the UI and worker packages pulls in every ``QThread`` subclass the
    app defines; after ``install_worker_guards`` none of them may still have an
    unguarded ``run``.
    """
    import importlib
    import pkgutil

    import streamkeep

    for module in pkgutil.walk_packages(
        streamkeep.__path__, prefix="streamkeep.",
    ):
        name = module.name
        if name.endswith("compile_translations") or name.endswith(
            "extract_translations"
        ):
            continue
        try:
            importlib.import_module(name)
        except Exception:
            # An optional dependency being absent is not this test's concern.
            continue

    crash_log.install_worker_guards()

    unguarded = []
    seen = set()

    def walk(cls):
        for subclass in cls.__subclasses__():
            if id(subclass) in seen:
                continue
            seen.add(id(subclass))
            run = subclass.__dict__.get("run")
            if run is not None and not getattr(
                run, "__streamkeep_guarded__", False
            ):
                unguarded.append(f"{subclass.__module__}.{subclass.__name__}")
            walk(subclass)

    walk(QThread)
    # Test-local classes defined in this session are not app code.
    unguarded = [name for name in unguarded if name.startswith("streamkeep.")]
    assert not unguarded, (
        "these QThread subclasses can crash without reaching crash.log: "
        + ", ".join(sorted(unguarded))
    )
