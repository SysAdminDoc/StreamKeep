import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from streamkeep import db
from streamkeep.headless_service import HeadlessJobService
from streamkeep.models import QualityInfo, StreamInfo


class _FakeFetchWorker(QObject):
    finished = pyqtSignal(object)
    vods_found = pyqtSignal(list, str, object)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, url, **_kwargs):
        super().__init__()
        self.url = url
        self._running = False

    def start(self):
        self._running = True
        self.finished.emit(StreamInfo(
            platform="Test",
            channel="Fixture",
            title="Durable job",
            url=self.url,
            qualities=[QualityInfo(
                name="720p", url="https://media.example/video.mp4",
                resolution="1280x720", format_type="mp4",
            )],
            total_secs=60,
        ))
        self._running = False

    def isRunning(self):
        return self._running

    def wait(self, _timeout):
        return True

    def requestInterruption(self):
        self._running = False


class _FakeDownloadWorker(QObject):
    progress = pyqtSignal(int, int, str)
    error = pyqtSignal(int, str)
    all_done = pyqtSignal()
    finished = pyqtSignal()
    log = pyqtSignal(str)

    def __init__(self, _url, segments, output_dir, _format_type):
        super().__init__()
        self.segments = segments
        self.output_dir = output_dir
        self.audio_url = ""
        self.ytdlp_source = ""
        self.ytdlp_format = ""
        self.parallel_connections = 1
        self._running = False

    def start(self):
        self._running = True
        path = Path(self.output_dir) / f"{self.segments[0][1]}.mp4"
        path.write_bytes(b"fixture-media")
        self.progress.emit(0, 100, "Complete")
        self.all_done.emit()
        self._running = False
        self.finished.emit()

    def cancel(self):
        self._running = False

    def isRunning(self):
        return self._running

    def wait(self, _timeout):
        return True


class _FakeFinalizeWorker(QObject):
    log = pyqtSignal(str)
    progress = pyqtSignal(str, int, int)
    done = pyqtSignal(dict)
    finished = pyqtSignal()

    def __init__(self, task):
        super().__init__()
        self.task = dict(task)
        self._running = False

    def start(self):
        self._running = True
        self.progress.emit("Capturing integrity manifest", 1, 1)
        self.done.emit({
            **self.task,
            "cancelled": False,
            "size_label": "13.0 B",
            "finalize_error": "",
            "archive_manifest_error": "",
            "archive_manifest": {"files": [{"path": "Durable job.mp4"}]},
        })
        self._running = False
        self.finished.emit()

    def cancel(self):
        self._running = False

    def isRunning(self):
        return self._running

    def wait(self, _timeout):
        return True


class HeadlessJobServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])

    def test_acknowledged_job_reaches_terminal_state_and_library(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "library.db"
            output = root / "output"
            with (
                mock.patch.object(db, "DB_PATH", db_path),
                mock.patch("streamkeep.headless_service.FetchWorker", _FakeFetchWorker),
                mock.patch("streamkeep.headless_service.DownloadWorker", _FakeDownloadWorker),
                mock.patch("streamkeep.headless_service.FinalizeWorker", _FakeFinalizeWorker),
            ):
                service = HeadlessJobService(output_dir=str(output), max_concurrent=1)
                service.start()
                acknowledged = service.enqueue({
                    "url": "https://example.com/video", "quality": "720p",
                })
                for _ in range(5):
                    self.app.processEvents()
                state = service.state_snapshot()
                manifest_count = db.archive_manifest_count()
                service.stop()

            self.assertTrue(acknowledged["job_id"])
            self.assertEqual(state["queue"][0]["job_id"], acknowledged["job_id"])
            self.assertEqual(state["queue"][0]["status"], "done")
            self.assertEqual(state["queue"][0]["progress"], 100)
            self.assertEqual(state["history"][0]["title"], "Durable job")
            self.assertEqual(manifest_count, 1)
            self.assertTrue((output / "Durable job.mp4").is_file())

    def test_cancelled_queued_job_is_terminal_and_observable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with mock.patch.object(db, "DB_PATH", root / "library.db"):
                service = HeadlessJobService(output_dir=str(root / "output"))
                db.init_db()
                job = service.enqueue("https://example.com/waiting")
                cancelled = service.cancel(job["job_id"])
                state = service.state_snapshot()

            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(state["queue"][0]["status"], "cancelled")

    def test_failure_retry_reuses_durable_job_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with mock.patch.object(db, "DB_PATH", root / "library.db"):
                db.init_db()
                original = db.enqueue_queue_job({
                    "url": "https://example.com/retry", "status": "failed",
                })
                failure_id = db.save_failed_job(
                    url=original["url"], stage="download", error="fixture failure",
                    queue_data=original,
                )
                db.update_queue_job(
                    original["job_id"], status="failed", failure_id=failure_id,
                )
                service = HeadlessJobService(output_dir=str(root / "output"))
                retried = service.retry_failure(failure_id)
                failure = db.load_failed_job(failure_id)

            self.assertEqual(retried["job_id"], original["job_id"])
            self.assertEqual(retried["status"], "queued")
            self.assertEqual(failure["status"], "retrying")
            self.assertEqual(failure["retry_count"], 1)


if __name__ == "__main__":
    unittest.main()
