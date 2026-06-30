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


if __name__ == "__main__":
    unittest.main()
