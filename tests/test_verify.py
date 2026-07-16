import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep.verify import (
    MANIFEST_FILENAME,
    STATUS_FAIL,
    STATUS_OK,
    create_archive_manifest,
    rescan_archive_manifest,
    verify_archive_manifest,
    verify_media,
)


class VerifyTests(unittest.TestCase):
    def test_verify_media_handles_invalid_numeric_probe_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = Path(tmpdir) / "clip.mp4"
            media_path.write_bytes(b"not-empty")

            probe_stdout = json.dumps(
                {"format": {"duration": "N/A", "nb_streams": "oops"}}
            ).encode("utf-8")
            completed = mock.Mock(returncode=0, stdout=probe_stdout, stderr=b"")

            with mock.patch(
                    "streamkeep.verify.resolve_tool_command",
                    return_value=r"C:\Tools\ffprobe.exe",
            ), mock.patch("streamkeep.verify.subprocess.run", return_value=completed):
                status, details = verify_media(str(media_path), expected_duration=60)

            self.assertEqual(status, STATUS_FAIL)
            self.assertIn("invalid numeric metadata", details)

    def test_archive_manifest_detects_changed_media_and_missing_sidecar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            media = root / "clip.mp4"
            sidecar = root / "metadata.json"
            media.write_bytes(b"original-media")
            sidecar.write_text('{"title":"demo"}', encoding="utf-8")

            manifest = create_archive_manifest(root)
            self.assertTrue((root / MANIFEST_FILENAME).is_file())

            media.write_bytes(b"changed-media")
            sidecar.unlink()
            status, details, report = verify_archive_manifest(root, manifest)

            self.assertEqual(status, STATUS_FAIL)
            self.assertIn("Integrity drift", details)
            self.assertEqual(report["missing"][0]["path"], "metadata.json")
            self.assertEqual(report["changed"][0]["path"], "clip.mp4")

    def test_archive_manifest_rescan_accepts_intentional_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            media = root / "clip.mp4"
            media.write_bytes(b"first")
            manifest = create_archive_manifest(root)

            media.write_bytes(b"intentional-update")
            status, _details, _report = verify_archive_manifest(root, manifest)
            self.assertEqual(status, STATUS_FAIL)

            rescanned = rescan_archive_manifest(root)
            status, details, report = verify_archive_manifest(root, rescanned)

            self.assertEqual(status, STATUS_OK)
            self.assertIn("Integrity verified", details)
            self.assertEqual(report["checked"], 1)


if __name__ == "__main__":
    unittest.main()
