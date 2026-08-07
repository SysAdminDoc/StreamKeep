"""Coverage for post-download local indexing running off the GUI thread (V146).

The worker's ``run`` is called directly rather than started, so the three
best-effort steps can be asserted without a Qt event loop. The queueing side
is exercised against the mixin with a stub worker class.
"""

import unittest
from unittest import mock

from streamkeep.ui.tabs.download_finalize import DownloadFinalizeMixin
from streamkeep.workers.index import RecordingIndexWorker


class RecordingIndexWorkerTests(unittest.TestCase):
    def test_every_step_runs_and_is_reported(self):
        worker = RecordingIndexWorker("C:/rec/one", info={"title": "x"})
        emitted = []
        worker.done.connect(emitted.append)

        db = mock.Mock()
        with mock.patch("streamkeep.tags._connect", return_value=db) as connect, \
                mock.patch("streamkeep.tags.auto_tag_recording") as auto_tag, \
                mock.patch("streamkeep.search.index_recording") as index_text, \
                mock.patch("streamkeep.semantic.is_enabled", return_value=True), \
                mock.patch("streamkeep.semantic.index_recording") as index_vec:
            worker.run()

        connect.assert_called_once()
        auto_tag.assert_called_once()
        db.close.assert_called_once()
        index_text.assert_called_once_with("C:/rec/one")
        index_vec.assert_called_once()
        self.assertEqual(emitted[0]["tagged"], True)
        self.assertEqual(emitted[0]["transcript_indexed"], True)
        self.assertEqual(emitted[0]["semantic_indexed"], True)

    def test_one_failing_step_does_not_cost_the_others(self):
        worker = RecordingIndexWorker("C:/rec/two")
        emitted = []
        logged = []
        worker.done.connect(emitted.append)
        worker.log.connect(logged.append)

        with mock.patch("streamkeep.tags._connect", side_effect=OSError("locked")), \
                mock.patch("streamkeep.search.index_recording") as index_text, \
                mock.patch("streamkeep.semantic.is_enabled", return_value=False):
            worker.run()

        index_text.assert_called_once_with("C:/rec/two")
        self.assertFalse(emitted[0]["tagged"])
        self.assertTrue(emitted[0]["transcript_indexed"])
        # The failure is surfaced rather than swallowed silently.
        self.assertTrue(any("Auto-tag skipped" in line for line in logged))

    def test_a_disabled_semantic_index_is_not_reported_as_indexed(self):
        worker = RecordingIndexWorker("C:/rec/three")
        emitted = []
        worker.done.connect(emitted.append)
        with mock.patch("streamkeep.tags._connect", return_value=mock.Mock()), \
                mock.patch("streamkeep.tags.auto_tag_recording"), \
                mock.patch("streamkeep.search.index_recording"), \
                mock.patch("streamkeep.semantic.is_enabled", return_value=False), \
                mock.patch("streamkeep.semantic.index_recording") as index_vec:
            worker.run()
        index_vec.assert_not_called()
        self.assertFalse(emitted[0]["semantic_indexed"])

    def test_an_empty_directory_is_a_no_op(self):
        worker = RecordingIndexWorker("")
        emitted = []
        worker.done.connect(emitted.append)
        with mock.patch("streamkeep.tags._connect") as connect:
            worker.run()
        connect.assert_not_called()
        self.assertEqual(emitted[0]["out_dir"], "")


class _StubWorker:
    """Stands in for the QThread so the queue logic is testable in-process."""

    instances = []

    def __init__(self, out_dir, info=None):
        self.out_dir = out_dir
        self.info = info
        self.started = False
        self.log = mock.Mock()
        self.done = mock.Mock()
        _StubWorker.instances.append(self)

    def isRunning(self):
        return self.started

    def start(self):
        self.started = True


class IndexQueueTests(unittest.TestCase):
    def setUp(self):
        _StubWorker.instances = []
        self.window = DownloadFinalizeMixin.__new__(DownloadFinalizeMixin)
        self.window._log = lambda *_a, **_k: None

    def test_indexing_is_handed_to_a_worker_not_run_inline(self):
        with mock.patch(
            "streamkeep.ui.tabs.download_finalize.RecordingIndexWorker",
            _StubWorker,
        ):
            self.window._index_finalized_recording("C:/rec/one", {"title": "x"})
        self.assertEqual(len(_StubWorker.instances), 1)
        self.assertTrue(_StubWorker.instances[0].started)
        self.assertEqual(_StubWorker.instances[0].out_dir, "C:/rec/one")

    def test_a_second_recording_waits_for_the_first(self):
        with mock.patch(
            "streamkeep.ui.tabs.download_finalize.RecordingIndexWorker",
            _StubWorker,
        ):
            self.window._index_finalized_recording("C:/rec/one")
            self.window._index_finalized_recording("C:/rec/two")
            # Only one worker at a time — the databases are shared.
            self.assertEqual(len(_StubWorker.instances), 1)
            self.window._on_recording_indexed({"cancelled": False})
            self.assertEqual(len(_StubWorker.instances), 2)
            self.assertEqual(_StubWorker.instances[1].out_dir, "C:/rec/two")

    def test_a_cancelled_worker_drops_the_rest_of_the_queue(self):
        with mock.patch(
            "streamkeep.ui.tabs.download_finalize.RecordingIndexWorker",
            _StubWorker,
        ):
            self.window._index_finalized_recording("C:/rec/one")
            self.window._index_finalized_recording("C:/rec/two")
            self.window._on_recording_indexed({"cancelled": True})
        self.assertEqual(len(_StubWorker.instances), 1)
        self.assertEqual(self.window._index_tasks, [])

    def test_an_empty_output_directory_never_starts_a_worker(self):
        with mock.patch(
            "streamkeep.ui.tabs.download_finalize.RecordingIndexWorker",
            _StubWorker,
        ):
            self.window._index_finalized_recording("")
        self.assertEqual(_StubWorker.instances, [])


if __name__ == "__main__":
    unittest.main()
