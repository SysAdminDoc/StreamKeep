"""Closing the window must join every QThread the window owns.

Destroying a running ``QThread`` is a ``qFatal``, not a catchable exception, so
a worker that ``closeEvent`` does not know about takes the process down — and
takes whatever it was mid-write with it. Three shipped defects had exactly that
shape (`_maintenance_worker`, the sync-viewer cards, `_semantic_index_worker`),
each fixed by adding one more name to a hand-maintained list. These tests
assert the *sweep* instead: any worker the window holds is stopped, whether or
not anyone remembered to name it.
"""

import threading
import time

import pytest
from PyQt6.QtCore import QThread

from streamkeep.ui.main_window import StreamKeep
from streamkeep.ui.worker_teardown import iter_owned_workers


class _SleepingWorker(QThread):
    """A worker that runs until asked to stop, like the real ones."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancelled = False
        self.observed_cancel = False

    def cancel(self):
        self.cancelled = True
        self.observed_cancel = True
        self.requestInterruption()

    def run(self):
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self.isInterruptionRequested() or self.cancelled:
                return
            self.msleep(10)


@pytest.fixture
def window(qt_application, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "streamkeep.ui.main_window.StreamKeep._start_update_check",
        lambda self: None,
        raising=False,
    )
    win = StreamKeep()
    yield win
    win.deleteLater()


def _running_workers(win):
    return [label for label, _worker in win._owned_workers()]


def test_owned_workers_discovers_plain_attributes(window):
    worker = _SleepingWorker()
    worker.start()
    try:
        window._some_brand_new_worker = worker
        assert "some_brand_new_worker" in _running_workers(window)
    finally:
        worker.cancel()
        worker.wait(3000)


def test_owned_workers_discovers_workers_held_in_containers(window):
    in_dict = _SleepingWorker()
    in_list = _SleepingWorker()
    in_dict.start()
    in_list.start()
    try:
        window._jobs_by_id = {"job-7": in_dict}
        window._pending_jobs = [in_list]
        labels = _running_workers(window)
        assert "jobs_by_id[job-7]" in labels
        assert "pending_jobs[0]" in labels
    finally:
        for worker in (in_dict, in_list):
            worker.cancel()
            worker.wait(3000)


def test_owned_workers_skips_finished_workers_and_deduplicates(window):
    finished = _SleepingWorker()
    finished.start()
    finished.cancel()
    assert finished.wait(3000)

    shared = _SleepingWorker()
    shared.start()
    try:
        window._finished_worker = finished
        window._shared_worker = shared
        window._shared_alias = shared
        labels = _running_workers(window)
        assert "finished_worker" not in labels
        assert sum(1 for label in labels if "shared" in label) == 1
    finally:
        shared.cancel()
        shared.wait(3000)


def test_close_event_joins_a_worker_nobody_named_in_the_teardown_list(window):
    """The regression guard: an unlisted worker must still be stopped."""
    worker = _SleepingWorker()
    worker.start()
    window._an_unlisted_worker = worker
    assert worker.isRunning()

    window.close()

    assert worker.observed_cancel, "closeEvent never asked the worker to stop"
    assert worker.wait(3000)
    assert not worker.isRunning()


def test_close_event_joins_workers_held_in_a_container(window):
    worker = _SleepingWorker()
    worker.start()
    window._unlisted_worker_map = {"a": worker}

    window.close()

    assert worker.observed_cancel
    assert worker.wait(3000)
    assert not worker.isRunning()


# ── V186: the invariant must not stop at the window boundary ─────────

class _DialogWorker(QThread):
    """A worker whose run() ignores quit(), like the real ones."""

    def __init__(self):
        super().__init__()
        self.observed_cancel = False
        self._stop = threading.Event()

    def cancel(self):
        self.observed_cancel = True
        self._stop.set()

    def run(self):
        self._stop.wait(10.0)


def _dialog_workers(dialog):
    return [label for label, _worker in iter_owned_workers(dialog)]


def test_accepting_a_dialog_joins_its_workers(qt_application):
    """Every dialog exit path must join owned threads, not just reject().

    ClipDialog joined five workers in reject() only, so pressing Export while
    the waveform, scene, thumbnail or preview worker ran left four live threads
    -- and the main window's sweep walks its own attributes and cannot see a
    dialog's (V186). Destroying a running QThread is a qFatal.
    """
    from PyQt6.QtWidgets import QDialog

    from streamkeep.ui.worker_teardown import WorkerOwnerMixin

    class _Dialog(WorkerOwnerMixin, QDialog):
        pass

    dialog = _Dialog()
    worker = _DialogWorker()
    dialog._scene_worker = worker
    worker.start()
    assert worker.isRunning()

    dialog.accept()

    assert worker.observed_cancel, "accept() never asked the worker to stop"
    assert not worker.isRunning(), "accept() left a QThread running"


def test_rejecting_a_dialog_joins_its_workers(qt_application):
    from PyQt6.QtWidgets import QDialog

    from streamkeep.ui.worker_teardown import WorkerOwnerMixin

    class _Dialog(WorkerOwnerMixin, QDialog):
        pass

    dialog = _Dialog()
    worker = _DialogWorker()
    dialog._thumb_worker = worker
    worker.start()

    dialog.reject()

    assert not worker.isRunning(), "reject() left a QThread running"


def test_the_clip_dialog_inherits_the_sweep():
    """The real dialogs must use the shared teardown, not a per-dialog list."""
    from streamkeep.ui.clip_dialog import ClipDialog
    from streamkeep.ui.recover_dialog import RecoverDialog
    from streamkeep.ui.worker_teardown import WorkerOwnerMixin

    for dialog_class in (ClipDialog, RecoverDialog):
        assert issubclass(dialog_class, WorkerOwnerMixin), (
            f"{dialog_class.__name__} owns QThreads and must join them on "
            "every exit path"
        )


def test_every_dialog_that_owns_a_qthread_uses_the_mixin():
    """Derived, not restated: a new thread-owning dialog fails this test.

    A hand-kept list is the defect this pattern already replaced once, so the
    set of dialogs is discovered from the source rather than enumerated here.
    """
    import ast
    from pathlib import Path

    import streamkeep.ui as ui_package
    from streamkeep.ui.worker_teardown import WorkerOwnerMixin

    ui_dir = Path(ui_package.__file__).parent
    offenders = []
    for path in sorted(ui_dir.glob("*_dialog.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        starts_worker = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "start"
            and isinstance(node.func.value, ast.Attribute)
            and "worker" in node.func.value.attr.lower()
            for node in ast.walk(tree)
        )
        if not starts_worker:
            continue
        module = __import__(
            f"streamkeep.ui.{path.stem}", fromlist=["*"],
        )
        classes = [
            obj for name, obj in vars(module).items()
            if isinstance(obj, type) and obj.__module__ == module.__name__
            and name.endswith("Dialog")
        ]
        for cls in classes:
            if not issubclass(cls, WorkerOwnerMixin):
                offenders.append(f"{path.name}:{cls.__name__}")
    assert not offenders, (
        "these dialogs start workers but do not inherit WorkerOwnerMixin: "
        + ", ".join(offenders)
    )
