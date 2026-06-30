import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from streamkeep import backup, db


class BackupTests(unittest.TestCase):
    def test_create_backup_captures_latest_sqlite_wal_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            db_path = config_dir / "library.db"
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE items (name TEXT)")
            conn.execute("INSERT INTO items (name) VALUES ('fresh-row')")
            conn.commit()

            backup_path = config_dir / "streamkeep.skbackup"
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, msg = backup.create_backup(backup_path)

            self.assertTrue(ok, msg)
            self.assertTrue(backup_path.is_file())
            self.assertTrue((db_path.parent / "library.db-wal").exists())

            extracted_db = config_dir / "snapshot.db"
            with zipfile.ZipFile(backup_path, "r") as zf:
                extracted_db.write_bytes(zf.read("library.db"))

            snap_conn = sqlite3.connect(extracted_db)
            try:
                row = snap_conn.execute("SELECT name FROM items").fetchone()
            finally:
                snap_conn.close()
                conn.close()

            self.assertEqual(row[0], "fresh-row")

    def test_restore_backup_replaces_db_and_clears_sqlite_sidecars(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            current_db = config_dir / "library.db"
            current_conn = sqlite3.connect(current_db)
            current_conn.execute("CREATE TABLE items (name TEXT)")
            current_conn.execute("INSERT INTO items (name) VALUES ('old-row')")
            current_conn.commit()
            current_conn.close()
            (config_dir / "library.db-wal").write_text("stale-wal", encoding="utf-8")
            (config_dir / "library.db-shm").write_text("stale-shm", encoding="utf-8")

            replacement_db = config_dir / "replacement.db"
            replacement_conn = sqlite3.connect(replacement_db)
            replacement_conn.execute("CREATE TABLE items (name TEXT)")
            replacement_conn.execute("INSERT INTO items (name) VALUES ('restored-row')")
            replacement_conn.commit()
            replacement_conn.close()

            backup_path = config_dir / "restore.skbackup"
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("_backup_meta.json", backup._meta_json())
                zf.writestr("library.db", replacement_db.read_bytes())

            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, msg = backup.restore_backup(backup_path)

            self.assertTrue(ok, msg)
            self.assertFalse((config_dir / "library.db-wal").exists())
            self.assertFalse((config_dir / "library.db-shm").exists())

            restored_conn = sqlite3.connect(current_db)
            try:
                row = restored_conn.execute("SELECT name FROM items").fetchone()
            finally:
                restored_conn.close()

            self.assertEqual(row[0], "restored-row")

    def test_create_backup_preserves_archive_manifest_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            db_path = config_dir / "library.db"
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
                    "path": str(config_dir / "recording"),
                    "url": "https://example.com/clip",
                })
                db.save_archive_manifest(
                    history_id,
                    str(config_dir / "recording"),
                    manifest,
                    status="created",
                    details="Captured 1 file",
                )

            backup_path = config_dir / "streamkeep.skbackup"
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, msg = backup.create_backup(backup_path)

            self.assertTrue(ok, msg)
            extracted_db = config_dir / "manifest_snapshot.db"
            with zipfile.ZipFile(backup_path, "r") as zf:
                extracted_db.write_bytes(zf.read("library.db"))

            conn = sqlite3.connect(extracted_db)
            try:
                row = conn.execute(
                    "SELECT manifest_json FROM archive_manifests"
                ).fetchone()
            finally:
                conn.close()

            self.assertIsNotNone(row)
            self.assertIn("clip.mp4", row[0])


if __name__ == "__main__":
    unittest.main()
