"""Closing the window must join every QThread the window owns.

Destroying a running ``QThread`` is a ``qFatal``, not a catchable exception, so
a worker that ``closeEvent`` does not know about takes the process down — and
takes whatever it was mid-write with it. Three shipped defects had exactly that
shape (`_maintenance_worker`, the sync-viewer cards, `_semantic_index_worker`),
each fixed by adding one more name to a hand-maintained list. These tests
assert the *sweep* instead: any worker the window holds is stopped, whether or
not anyone remembered to name it.
"""

import time

import pytest
from PyQt6.QtCore import QThread

from streamkeep.ui.main_window import StreamKeep


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
