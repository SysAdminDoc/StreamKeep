import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication, Qt

from streamkeep.updater import DownloadUpdateWorker


class _FakeResponse:
    def __init__(self, chunks, headers=None):
        self._chunks = list(chunks)
        self.headers = headers or {}

    def read(self, _size):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class UpdaterTests(unittest.TestCase):
    def test_cancelled_update_download_removes_partial_new_file(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        done_events = []
        with tempfile.TemporaryDirectory() as tmpdir:
            exe_path = Path(tmpdir) / "StreamKeep.exe"
            exe_path.write_bytes(b"old")
            worker = DownloadUpdateWorker("https://example.com/StreamKeep.exe", 0)
            worker._cancel = True
            worker.done.connect(
                lambda ok, msg: done_events.append((ok, msg)),
                type=Qt.ConnectionType.DirectConnection,
            )

            with mock.patch.object(worker, "_target_path", return_value=str(exe_path)), \
                 mock.patch("streamkeep.updater.urllib.request.urlopen", return_value=_FakeResponse([b"new-bits"])), \
                 mock.patch("streamkeep.updater.sys.executable", str(exe_path)), \
                 mock.patch("streamkeep.updater.sys.frozen", True, create=True):
                worker.run()

            app.processEvents()
            self.assertEqual(done_events, [(False, "Download cancelled.")])
            self.assertFalse((exe_path.parent / "StreamKeep.exe.new").exists())

    def test_invalid_content_length_header_does_not_crash_update_download(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        done_events = []
        with tempfile.TemporaryDirectory() as tmpdir:
            exe_path = Path(tmpdir) / "StreamKeep.exe"
            exe_path.write_bytes(b"old")
            worker = DownloadUpdateWorker("https://example.com/StreamKeep.exe", 0)
            worker.done.connect(
                lambda ok, msg: done_events.append((ok, msg)),
                type=Qt.ConnectionType.DirectConnection,
            )

            with mock.patch.object(worker, "_target_path", return_value=str(exe_path)), \
                 mock.patch(
                     "streamkeep.updater.urllib.request.urlopen",
                     return_value=_FakeResponse([b"fresh-binary"], headers={"Content-Length": "not-a-number"}),
                 ), \
                 mock.patch("streamkeep.updater.sys.executable", str(exe_path)), \
                 mock.patch("streamkeep.updater.sys.frozen", True, create=True):
                worker.run()

            app.processEvents()
            self.assertTrue(done_events)
            self.assertTrue(done_events[0][0], done_events[0][1])
            self.assertTrue((exe_path.parent / "StreamKeep.exe.new").exists())

    def test_sha256_mismatch_rejects_update_and_removes_new_file(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        done_events = []
        with tempfile.TemporaryDirectory() as tmpdir:
            exe_path = Path(tmpdir) / "StreamKeep.exe"
            exe_path.write_bytes(b"old")
            worker = DownloadUpdateWorker(
                "https://example.com/StreamKeep.exe",
                0,
                "0" * 64,
            )
            worker.done.connect(
                lambda ok, msg: done_events.append((ok, msg)),
                type=Qt.ConnectionType.DirectConnection,
            )

            with mock.patch.object(worker, "_target_path", return_value=str(exe_path)), \
                 mock.patch(
                     "streamkeep.updater.urllib.request.urlopen",
                     return_value=_FakeResponse([b"fresh-binary"]),
                 ), \
                 mock.patch("streamkeep.updater.sys.executable", str(exe_path)), \
                 mock.patch("streamkeep.updater.sys.frozen", True, create=True):
                worker.run()

            app.processEvents()
            self.assertTrue(done_events)
            self.assertFalse(done_events[0][0])
            self.assertIn("SHA-256 mismatch", done_events[0][1])
            self.assertFalse((exe_path.parent / "StreamKeep.exe.new").exists())

    def test_sha256_match_accepts_update_download(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        done_events = []
        payload = b"fresh-binary"
        expected_hash = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmpdir:
            exe_path = Path(tmpdir) / "StreamKeep.exe"
            exe_path.write_bytes(b"old")
            worker = DownloadUpdateWorker(
                "https://example.com/StreamKeep.exe",
                0,
                expected_hash,
            )
            worker.done.connect(
                lambda ok, msg: done_events.append((ok, msg)),
                type=Qt.ConnectionType.DirectConnection,
            )

            with mock.patch.object(worker, "_target_path", return_value=str(exe_path)), \
                 mock.patch(
                     "streamkeep.updater.urllib.request.urlopen",
                     return_value=_FakeResponse([payload]),
                 ), \
                 mock.patch("streamkeep.updater.sys.executable", str(exe_path)), \
                 mock.patch("streamkeep.updater.sys.frozen", True, create=True):
                worker.run()

            app.processEvents()
            new_path = exe_path.parent / "StreamKeep.exe.new"
            self.assertEqual(done_events, [(True, str(new_path))])
            self.assertEqual(new_path.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
