import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import db


class DbMigrationTests(unittest.TestCase):
    def test_migrate_from_config_skips_when_non_history_tables_already_have_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            cfg = {
                "monitor_channels": [
                    {"url": "https://kick.com/example", "platform": "Kick"}
                ]
            }

            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                db.save_queue([{"url": "https://example.com/video"}])
                migrated = db.migrate_from_config(cfg)
                queue = db.load_queue()
                channels = db.load_monitor_channels()

            self.assertFalse(migrated)
            self.assertEqual(len(queue), 1)
            self.assertEqual(channels, [])
            self.assertNotIn("monitor_channels", cfg)

    def test_archive_manifest_persists_and_is_removed_with_history_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            manifest = {
                "version": 1,
                "algorithm": "sha256",
                "files": [{"path": "clip.mp4", "sha256": "abc", "size": 3}],
            }

            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                history_id = db.save_history_entry({
                    "date": "2026-06-29",
                    "platform": "Test",
                    "title": "Clip",
                    "path": str(Path(tmpdir) / "recording"),
                    "url": "https://example.com/clip",
                })
                db.save_archive_manifest(
                    history_id,
                    str(Path(tmpdir) / "recording"),
                    manifest,
                    status="created",
                    details="Captured 1 file",
                )
                loaded = db.load_archive_manifest(history_id)
                db.update_archive_manifest_check(
                    history_id,
                    "verified",
                    "Integrity verified: 1/1 file(s) match",
                )
                updated = db.load_archive_manifest(history_id)
                db.delete_history_entries([history_id])
                count = db.archive_manifest_count()

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["manifest"]["files"][0]["path"], "clip.mp4")
            self.assertEqual(updated["status"], "verified")
            self.assertEqual(count, 0)

    def test_failed_job_ledger_persists_retry_and_discard_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"

            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                job_id = db.save_failed_job(
                    url="https://example.com/video",
                    platform="Example",
                    title="Example video",
                    stage="download",
                    error="network timeout",
                    output_dir=str(Path(tmpdir) / "recording"),
                    resume_sidecar=str(Path(tmpdir) / "recording" / ".streamkeep_resume.json"),
                    queue_data={"url": "https://example.com/video", "title": "Example video"},
                )
                first = db.load_failed_job(job_id)
                retrying = db.mark_failed_job_retrying(job_id)
                active_after_retry = db.load_failed_jobs()
                db.mark_failed_job_discarded(job_id)
                active_after_discard = db.load_failed_jobs()
                discarded = db.load_failed_job(job_id)

            self.assertGreater(job_id, 0)
            self.assertEqual(first["stage"], "download")
            self.assertEqual(first["queue_data"]["url"], "https://example.com/video")
            self.assertEqual(retrying["status"], "retrying")
            self.assertEqual(retrying["retry_count"], 1)
            self.assertEqual(len(active_after_retry), 1)
            self.assertEqual(active_after_discard, [])
            self.assertEqual(discarded["status"], "discarded")


if __name__ == "__main__":
    unittest.main()
