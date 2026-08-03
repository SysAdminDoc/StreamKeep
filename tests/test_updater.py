import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication, Qt

from streamkeep.updater import (
    DownloadUpdateWorker,
    UpdateCheckWorker,
    arm_self_replace,
    load_update_state,
    validate_download_payload,
)
from streamkeep.update_security import UpdateSecurityError


class _FakeResponse:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, _size):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _payload(binary, *, certificate="a" * 64):
    return {
        "available": True,
        "error": "",
        "tag": "v4.31.8",
        "version": "4.31.8",
        "notes": "",
        "sequence": 43108,
        "manifest_sha256": "b" * 64,
        "current_version": "4.31.7",
        "signer_subject": "CN=StreamKeep Test",
        "asset": {
            "name": "StreamKeep.exe",
            "format": "portable-exe",
            "size": len(binary),
            "sha256": hashlib.sha256(binary).hexdigest(),
            "signer_sha256": certificate,
            "url": "https://github.com/SysAdminDoc/StreamKeep/releases/download/v4.31.8/StreamKeep.exe",
        },
    }


def _public_release(tag="v4.31.9", *, digest="sha256:" + "c" * 64, assets=True):
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "body": "Bug fixes",
        "assets": ([{
            "name": "StreamKeep.exe",
            "size": 123,
            "digest": digest,
            "browser_download_url": (
                f"https://github.com/SysAdminDoc/StreamKeep/releases/download/"
                f"{tag}/StreamKeep.exe"
            ),
        }] if assets else []),
    }


class UpdaterCheckTests(unittest.TestCase):
    def _run_check(self, release, current="4.31.8"):
        app = QCoreApplication.instance() or QCoreApplication([])
        worker = UpdateCheckWorker(current)
        events = []
        worker.result.connect(
            events.append,
            type=Qt.ConnectionType.DirectConnection,
        )
        with mock.patch(
            "streamkeep.updater._fetch_bytes",
            return_value=json.dumps(release).encode("utf-8"),
        ), mock.patch("streamkeep.updater.require_authenticode") as authenticode:
            worker.run()
        app.processEvents()
        self.assertEqual(len(events), 1)
        return events[0], authenticode

    def test_newer_release_is_manual_only_and_keeps_published_digest(self):
        payload, authenticode = self._run_check(_public_release())
        self.assertTrue(payload["available"])
        self.assertTrue(payload["manual_only"])
        self.assertFalse(payload["self_replace_allowed"])
        self.assertEqual(payload["published_sha256"], "c" * 64)
        self.assertEqual(payload["asset"]["sha256"], "c" * 64)
        self.assertEqual(
            payload["asset"]["url"],
            "https://github.com/SysAdminDoc/StreamKeep/releases/download/"
            "v4.31.9/StreamKeep.exe",
        )
        self.assertIn("/releases/tag/v4.31.9", payload["release_url"])
        authenticode.assert_not_called()

    def test_same_and_older_releases_are_noops_without_signature_checks(self):
        for tag in ("v4.31.8", "v4.31.7"):
            with self.subTest(tag=tag):
                payload, authenticode = self._run_check(
                    _public_release(tag, assets=False)
                )
                self.assertFalse(payload["available"])
                self.assertEqual(payload["tag"], tag)
                self.assertEqual(payload["version"], tag[1:])
                authenticode.assert_not_called()

    def test_missing_published_digest_does_not_become_an_installable_update(self):
        payload, authenticode = self._run_check(
            _public_release(digest="")
        )
        self.assertFalse(payload["available"])
        self.assertIn("SHA-256", payload["error"])
        authenticode.assert_not_called()

    def test_public_payload_cannot_enter_download_or_self_replace_paths(self):
        payload, _authenticode = self._run_check(_public_release())
        with self.assertRaisesRegex(UpdateSecurityError, "manual-only"):
            validate_download_payload(payload, {})
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            staged = root / "StreamKeep.exe.new"
            staged.write_bytes(b"new")
            with mock.patch.object(sys, "executable", str(root / "StreamKeep.exe")), \
                    mock.patch("streamkeep.updater.sys.frozen", True, create=True), \
                    mock.patch("streamkeep.updater.subprocess.Popen") as popen:
                self.assertFalse(arm_self_replace(staged, payload))
            popen.assert_not_called()


class UpdaterDownloadTests(unittest.TestCase):
    def _run_worker(self, tmpdir, binary, *, payload=None, cancel=False):
        app = QCoreApplication.instance() or QCoreApplication([])
        root = Path(tmpdir)
        executable = root / "StreamKeep.exe"
        executable.write_bytes(b"old-signed-binary")
        state = {
            "highest_sequence": 43108,
            "highest_version": "4.31.8",
            "manifest_sha256": "b" * 64,
        }
        (root / "update-state.json").write_text(json.dumps(state), encoding="utf-8")
        worker = DownloadUpdateWorker(payload or _payload(binary))
        worker._cancel = cancel
        events = []
        worker.done.connect(
            lambda ok, message: events.append((ok, message)),
            type=Qt.ConnectionType.DirectConnection,
        )
        with mock.patch.object(worker, "_target_path", return_value=str(executable)), \
             mock.patch("streamkeep.paths.CONFIG_DIR", root), \
             mock.patch("streamkeep.updater.urllib.request.urlopen", return_value=_FakeResponse([binary])), \
             mock.patch("streamkeep.updater.require_authenticode") as authenticode, \
             mock.patch("streamkeep.updater.sys.frozen", True, create=True):
            worker.run()
        app.processEvents()
        return executable, events, authenticode

    def test_authenticated_download_is_staged(self):
        binary = b"fresh-signed-binary"
        with tempfile.TemporaryDirectory() as tmpdir:
            executable, events, authenticode = self._run_worker(tmpdir, binary)
            staged = Path(f"{executable}.new")
            self.assertEqual(events, [(True, str(staged))])
            self.assertEqual(staged.read_bytes(), binary)
            self.assertEqual(authenticode.call_count, 2)

    def test_signed_digest_mismatch_is_rejected_and_removed(self):
        binary = b"tampered-binary"
        payload = _payload(b"publisher-binary")
        payload["asset"]["size"] = len(binary)
        with tempfile.TemporaryDirectory() as tmpdir:
            executable, events, _authenticode = self._run_worker(
                tmpdir, binary, payload=payload
            )
            self.assertFalse(events[0][0])
            self.assertIn("SHA-256", events[0][1])
            self.assertFalse(Path(f"{executable}.new").exists())

    def test_cancelled_download_is_removed(self):
        binary = b"fresh-signed-binary"
        with tempfile.TemporaryDirectory() as tmpdir:
            executable, events, _authenticode = self._run_worker(
                tmpdir, binary, cancel=True
            )
            self.assertEqual(events, [(False, "Download cancelled.")])
            self.assertFalse(Path(f"{executable}.new").exists())

    def test_publisher_mismatch_is_rejected(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        binary = b"fresh-signed-binary"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            executable = root / "StreamKeep.exe"
            executable.write_bytes(b"old")
            (root / "update-state.json").write_text(json.dumps({
                "highest_sequence": 43108,
                "highest_version": "4.31.8",
                "manifest_sha256": "b" * 64,
            }), encoding="utf-8")
            worker = DownloadUpdateWorker(_payload(binary))
            events = []
            worker.done.connect(
                lambda ok, message: events.append((ok, message)),
                type=Qt.ConnectionType.DirectConnection,
            )
            with mock.patch.object(worker, "_target_path", return_value=str(executable)), \
                 mock.patch("streamkeep.paths.CONFIG_DIR", root), \
                 mock.patch("streamkeep.updater.require_authenticode", side_effect=[{}, UpdateSecurityError("wrong publisher")]), \
                 mock.patch("streamkeep.updater.urllib.request.urlopen", return_value=_FakeResponse([binary])), \
                 mock.patch("streamkeep.updater.sys.frozen", True, create=True):
                worker.run()
            app.processEvents()
            self.assertFalse(events[0][0])
            self.assertIn("wrong publisher", events[0][1])
            self.assertFalse(Path(f"{executable}.new").exists())

    def test_corrupt_local_rollback_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "update-state.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "rollback state"):
                load_update_state(path)


if __name__ == "__main__":
    unittest.main()
