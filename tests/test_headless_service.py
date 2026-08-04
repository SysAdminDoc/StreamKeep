import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from streamkeep import db
from streamkeep.headless_service import HeadlessJobService
from streamkeep.preflight import PreflightError, ProbeBusyError
from streamkeep.models import QualityInfo, StreamInfo, VODInfo


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


class _FakePickerFetchWorker(QObject):
    finished = pyqtSignal(object)
    vods_found = pyqtSignal(list, str, object)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, url, **kwargs):
        super().__init__()
        self.url = url
        self.vod_source = kwargs.get("vod_source")
        self._running = False

    def start(self):
        self._running = True
        if self.vod_source:
            self.finished.emit(StreamInfo(
                platform="Twitch",
                title="Selected VOD",
                url="https://media.example/selected.m3u8",
                qualities=[QualityInfo(name="best", url="https://media.example/selected.m3u8")],
            ))
        else:
            self.vods_found.emit(
                [
                    VODInfo(
                        title="First VOD",
                        source="100",
                        platform="Twitch",
                        source_id="100",
                    ),
                    VODInfo(
                        title="Second VOD",
                        source="200",
                        platform="Twitch",
                        source_id="200",
                    ),
                ],
                "Twitch",
                None,
            )
        self._running = False

    def isRunning(self):
        return self._running

    def wait(self, _timeout):
        return True

    def requestInterruption(self):
        self._running = False


class _StuckProbeWorker(QObject):
    finished = pyqtSignal(object)
    vods_found = pyqtSignal(list, str, object)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, _url, **_kwargs):
        super().__init__()
        self.interruption_requested = False
        self._finished = False

    def start(self):
        return None

    def requestInterruption(self):
        self.interruption_requested = True

    def wait(self, _timeout):
        return self._finished

    def isFinished(self):
        return self._finished

    def isRunning(self):
        return not self._finished


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

    @classmethod
    def from_spec(cls, spec):
        w = cls(spec.playlist_url, [list(s) for s in spec.segments],
                spec.output_dir, spec.format_type)
        spec.apply_to_worker(w)
        return w

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

    def test_enqueue_logs_when_user_tombstone_blocks_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with (
                mock.patch.object(db, "DB_PATH", root / "library.db"),
                mock.patch("streamkeep.headless_service.write_log_line") as log,
            ):
                db.init_db()
                db.record_tombstone(
                    platform="yt-dlp",
                    source_id="video-1",
                    webpage_url="https://example.com/watch?v=video-1",
                    reason="user",
                )
                service = HeadlessJobService(output_dir=str(root / "output"))
                job = service.enqueue({
                    "url": "https://example.com/watch?v=video-1",
                    "platform": "yt-dlp",
                    "source_id": "video-1",
                    "webpage_url": "https://example.com/watch?v=video-1",
                })

        self.assertEqual(job["status"], "cancelled")
        self.assertTrue(job["tombstone_skipped"])
        self.assertTrue(
            any("Skipped tombstoned media" in call.args[0]
                for call in log.call_args_list)
        )

    def test_probe_picker_selection_is_bound_to_the_requested_vod(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                mock.patch.object(db, "DB_PATH", Path(tmpdir) / "library.db"),
                mock.patch(
                    "streamkeep.headless_service.FetchWorker",
                    _FakePickerFetchWorker,
                ),
            ):
                db.init_db()
                service = HeadlessJobService(output_dir=str(Path(tmpdir) / "output"))
                response = service.probe({"url": "https://example.com/channel"})
                second = response["media_items"][1]
                queued = service.enqueue({
                    "url": "https://example.com/channel",
                    "validation_id": response["validation_id"],
                    "media_item_id": second["id"],
                })
                item = db.load_queue_job(queued["job_id"])

        self.assertEqual(response["status"], "picker")
        self.assertEqual(len(response["media_items"]), 2)
        self.assertEqual(item["vod_source"], "200")
        self.assertEqual(item["source_id"], "200")
        self.assertEqual(item["title"], "Second VOD")

    def test_timed_out_probe_is_reaped_without_destroying_running_worker(self):
        workers = []

        def factory(*_args, **_kwargs):
            worker = _StuckProbeWorker("https://example.com/stuck")
            workers.append(worker)
            return worker

        with mock.patch("streamkeep.headless_service.FetchWorker", factory):
            service = HeadlessJobService(
                output_dir="",
                max_concurrent=1,
                max_probe_concurrent=1,
            )
            service._probe_timeout = 0.01
            with self.assertRaisesRegex(PreflightError, "timed out"):
                service.probe({"url": "https://example.com/stuck"})

            worker = workers[0]
            self.assertTrue(worker.interruption_requested)
            self.assertFalse(worker.isFinished())
            self.assertIn(worker, service._probe_reapers)
            with self.assertRaises(ProbeBusyError):
                service.probe({"url": "https://example.com/second"})

            worker._finished = True
            for _ in range(20):
                if worker not in service._probe_reapers:
                    break
                time.sleep(0.01)
            self.assertNotIn(worker, service._probe_reapers)
            self.assertTrue(service._probe_slots.acquire(blocking=False))
            service._probe_slots.release()

    def test_second_executor_refuses_with_actionable_owner_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with mock.patch.object(db, "DB_PATH", root / "library.db"):
                db.init_db()
                desktop = db.acquire_executor_lease(
                    "desktop-owner", owner_kind="desktop app",
                )
                service = HeadlessJobService(
                    output_dir=str(root / "output"),
                    owner_id="server-owner",
                )
                with self.assertRaisesRegex(
                    RuntimeError, "already owned by desktop app",
                ):
                    service.start()
                lease = db.get_executor_lease()
                db.release_executor_lease("desktop-owner")

            self.assertTrue(desktop["acquired"])
            self.assertEqual(lease["owner_id"], "desktop-owner")

    def test_clean_stop_requeues_only_services_owned_work(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with mock.patch.object(db, "DB_PATH", root / "library.db"):
                db.init_db()
                owned = db.enqueue_queue_job(
                    {"url": "https://example.com/owned"},
                )
                untouched = db.enqueue_queue_job(
                    {"url": "https://example.com/untouched", "status": "failed"},
                )
                service = HeadlessJobService(
                    output_dir=str(root / "output"),
                    owner_id="server-owner",
                )
                service.start()
                db.claim_queue_job(owned["job_id"], "server-owner")
                service.stop()
                owned_after = db.load_queue_job(owned["job_id"])
                untouched_after = db.load_queue_job(untouched["job_id"])
                lease = db.get_executor_lease()

            self.assertEqual(owned_after["status"], "queued")
            self.assertEqual(owned_after["execution_owner"], "")
            self.assertEqual(untouched_after["status"], "failed")
            self.assertIsNone(lease)

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
