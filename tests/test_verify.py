import base64
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from streamkeep import db
from streamkeep.integrity import run_rolling_integrity_scrub
from streamkeep.storage import scan_storage
from streamkeep.verify import (
    MANIFEST_FILENAME,
    STATUS_FAIL,
    STATUS_OK,
    create_archive_manifest,
    export_bagit,
    rescan_archive_manifest,
    verify_archive_manifest,
    verify_media,
)


class VerifyTests(unittest.TestCase):
    @staticmethod
    def _seed_recording(db_path, root, name, payload=b"archive-media"):
        recording = Path(root) / name
        recording.mkdir(parents=True, exist_ok=True)
        media = recording / "clip.mp4"
        media.write_bytes(payload)
        manifest = create_archive_manifest(recording)
        with mock.patch.object(db, "DB_PATH", Path(db_path)):
            db.init_db()
            history_id = db.save_history_entry({
                "date": "2026-08-01T00:00:00Z",
                "platform": "Test",
                "source_id": f"test:{name}",
                "title": name,
                "channel": "fixture",
                "path": str(recording),
                "url": f"https://example.test/{name}",
            })
            db.save_archive_manifest(history_id, str(recording), manifest)
        return recording, media, manifest, history_id

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

    def test_bagit_export_uses_manifest_hashes_and_records_sri(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            media = root / "episode.mp3"
            sidecar = root / "metadata.json"
            media.write_bytes(b"bagit media")
            sidecar.write_text('{"title":"BagIt"}\n', encoding="utf-8")
            manifest = create_archive_manifest(root)
            original_sidecar = (root / MANIFEST_FILENAME).read_bytes()

            with mock.patch(
                "streamkeep.verify._hash_file_digests",
                side_effect=AssertionError("BagIt export must not rehash payload"),
            ):
                result = export_bagit(root, manifest)

            self.assertEqual(
                (root / MANIFEST_FILENAME).read_bytes(), original_sidecar,
            )
            self.assertEqual(
                (root / "bagit.txt").read_text(encoding="utf-8"),
                "BagIt-Version: 0.97\nTag-File-Character-Encoding: UTF-8\n",
            )
            self.assertIn("Payload-Oxum: 30.2", (root / "bag-info.txt").read_text(encoding="utf-8"))

            payload_lines = (root / "manifest-sha256.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                payload_lines,
                [f"{entry['sha256']}  {entry['path']}" for entry in manifest["files"]],
            )
            sri_rows = {
                line.rsplit("  ", 1)[1]: line.split("  ", 1)[0]
                for line in (root / "manifest-sha384-sri.txt").read_text(encoding="utf-8").splitlines()
            }
            by_path = {entry["path"]: entry for entry in manifest["files"]}
            self.assertEqual(
                sri_rows,
                {path: entry["sha384_sri"] for path, entry in by_path.items()},
            )
            for path, sri in sri_rows.items():
                encoded = sri.split("-", 1)[1]
                self.assertEqual(
                    base64.b64decode(encoded),
                    hashlib.sha384((root / path).read_bytes()).digest(),
                )

            from streamkeep.podcast_sidecars import verify_podcast_integrity
            verified = verify_podcast_integrity(
                str(media),
                [{
                    "sources": [{"uri": "https://cdn.example/episode.mp3"}],
                    "integrity": {
                        "type": "sri",
                        "value": by_path["episode.mp3"]["sha384_sri"],
                    },
                }],
                "https://cdn.example/episode.mp3",
            )
            self.assertEqual(verified[0]["status"], "verified")
            self.assertEqual(result["payload_files"], 2)

            tag_lines = (root / "tagmanifest-sha256.txt").read_text(encoding="utf-8").splitlines()
            tag_names = {line.rsplit("  ", 1)[1] for line in tag_lines}
            self.assertIn("bagit.txt", tag_names)
            self.assertIn(MANIFEST_FILENAME, tag_names)

    def test_storage_scan_runs_cheap_manifest_check_without_hashing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            recording = root / "recording"
            recording.mkdir()
            media = recording / "clip.mp4"
            media.write_bytes(b"original")
            create_archive_manifest(recording)
            media.write_bytes(b"changed-size")

            with mock.patch(
                "streamkeep.verify._sha256_file",
                side_effect=AssertionError("storage scan must not hash"),
            ):
                scan = scan_storage(str(root))

            self.assertEqual(scan.integrity_checked, 0)
            self.assertEqual(len(scan.integrity_issues), 1)
            self.assertEqual(scan.integrity_issues[0]["path"], "clip.mp4")
            self.assertIn("size", scan.integrity_issues[0]["reason"])

    def test_rolling_scrub_covers_fraction_and_persists_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "library"
            db_path = Path(tmpdir) / "library.db"
            root.mkdir()
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                for index in range(4):
                    self._seed_recording(db_path, root, f"recording-{index}")
                first_now = datetime(2026, 8, 1, tzinfo=timezone.utc)
                config = {
                    "integrity_scrub_interval_hours": 1,
                    "integrity_scrub_period_days": 30,
                    "integrity_scrub_fraction": 0.5,
                    "integrity_scrub_max_bytes": 1024 * 1024,
                    "integrity_scrub_rate_mbps": 0,
                }
                first = run_rolling_integrity_scrub(
                    str(root), config=config, now=first_now,
                )
                second = run_rolling_integrity_scrub(
                    str(root), config=config, now=first_now + timedelta(hours=2),
                )
                states = db.list_integrity_scrub_states()
                run_state = db.get_integrity_scrub_state(0)

            self.assertEqual(first.status, "completed")
            self.assertEqual(first.checked, 2)
            self.assertEqual(second.status, "completed")
            self.assertEqual(second.checked, 2)
            self.assertEqual(len(states), 4)
            self.assertTrue(all(row["last_full_at"] for row in states))
            self.assertEqual(run_state["run_status"], "completed")
            self.assertEqual(run_state["run_checked"], 2)

    def test_rolling_scrub_reports_same_size_hash_mismatch_and_notifies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "library"
            db_path = Path(tmpdir) / "library.db"
            root.mkdir()
            recording, media, manifest, history_id = self._seed_recording(
                db_path, root, "changed", payload=b"original"
            )
            original_mtime = int(manifest["files"][0]["mtime_ns"])
            media.write_bytes(b"modified")
            os.utime(media, ns=(original_mtime, original_mtime))
            notes = []
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                result = run_rolling_integrity_scrub(
                    str(root),
                    config={
                        "integrity_scrub_interval_hours": 1,
                        "integrity_scrub_fraction": 1.0,
                        "integrity_scrub_max_bytes": 1024 * 1024,
                    },
                    notify_fn=lambda text, level: notes.append((text, level)),
                    now=datetime(2026, 8, 1, tzinfo=timezone.utc),
                )
                state = db.get_integrity_scrub_state(history_id)

            self.assertEqual(result.status, "completed_with_mismatches")
            self.assertEqual(result.mismatches, 1)
            self.assertEqual(result.issues[0]["files"][0]["path"], "clip.mp4")
            self.assertEqual(notes[0][1], "error")
            self.assertEqual(state["status"], "failed")
            self.assertTrue(state["last_full_at"])
            self.assertEqual(media.read_bytes(), b"modified")
            self.assertTrue(recording.is_dir())

    def test_rolling_scrub_leaves_offline_volume_due_and_not_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "library"
            db_path = Path(tmpdir) / "library.db"
            root.mkdir()
            _recording, _media, _manifest, history_id = self._seed_recording(
                db_path, root, "offline"
            )
            with mock.patch.object(db, "DB_PATH", db_path), mock.patch(
                "streamkeep.integrity._volume_online", return_value=False,
            ):
                db.init_db()
                result = run_rolling_integrity_scrub(
                    str(root),
                    config={
                        "integrity_scrub_interval_hours": 1,
                        "integrity_scrub_fraction": 1.0,
                    },
                    now=datetime(2026, 8, 1, tzinfo=timezone.utc),
                )
                state = db.get_integrity_scrub_state(history_id)

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.checked, 0)
            self.assertEqual(result.offline, 1)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(state["status"], "offline")
            self.assertEqual(state["last_full_at"], "")

    def test_rolling_scrub_cancellation_is_durable_and_non_mutating(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "library"
            db_path = Path(tmpdir) / "library.db"
            root.mkdir()
            _recording, media, _manifest, _history_id = self._seed_recording(
                db_path, root, "cancelled"
            )
            before = media.read_bytes()
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                result = run_rolling_integrity_scrub(
                    str(root),
                    config={
                        "integrity_scrub_interval_hours": 1,
                        "integrity_scrub_fraction": 1.0,
                    },
                    cancel_fn=lambda: True,
                    now=datetime(2026, 8, 1, tzinfo=timezone.utc),
                )
                state = db.get_integrity_scrub_state(0)

            self.assertEqual(result.status, "cancelled")
            self.assertEqual(result.checked, 0)
            self.assertEqual(state["run_status"], "cancelled")
            self.assertEqual(media.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
