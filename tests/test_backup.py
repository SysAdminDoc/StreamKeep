import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from streamkeep import backup, db


class BackupTests(unittest.TestCase):
    def test_restore_of_legacy_backup_drops_auth_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "target"
            target.mkdir()
            legacy_db = root / "legacy.db"
            conn = sqlite3.connect(legacy_db)
            conn.execute(
                "CREATE TABLE accounts (platform TEXT PRIMARY KEY, "
                "credential TEXT, extra TEXT)"
            )
            conn.execute(
                "INSERT INTO accounts VALUES ('twitch','legacy-account-secret','{}')"
            )
            conn.commit()
            conn.close()
            backup_path = root / "legacy.skbackup"
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("_backup_meta.json", backup._meta_json())
                zf.writestr("config.json", json.dumps({
                    "theme": "dark", "hf_token": "legacy-config-secret",
                }))
                zf.writestr("library.db", legacy_db.read_bytes())
                zf.writestr(
                    "cookies.txt",
                    ".example.com\tTRUE\t/\tTRUE\t0\tsession\tlegacy-cookie-secret\n",
                )

            with mock.patch.object(backup, "CONFIG_DIR", target):
                ok, message = backup.restore_backup(backup_path)

            self.assertTrue(ok, message)
            restored_config = json.loads(
                (target / "config.json").read_text(encoding="utf-8")
            )
            conn = sqlite3.connect(target / "library.db")
            try:
                count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(restored_config["hf_token"], "")
            self.assertEqual(count, 0)
            self.assertFalse((target / "cookies.txt").exists())

    def test_ordinary_backup_excludes_config_accounts_cookies_and_log_secrets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            (config_dir / "config.json").write_text(json.dumps({
                "theme": "dark",
                "webhook_url": "https://hooks.example/config-secret",
                "media_server": {"token": "media-secret"},
            }), encoding="utf-8")
            (config_dir / "cookies.txt").write_text(
                ".example.com\tTRUE\t/\tTRUE\t0\tsession\tcookie-secret\n",
                encoding="utf-8",
            )
            (config_dir / "streamkeep.log").write_text(
                "Bearer log-secret\n", encoding="utf-8",
            )
            db_path = config_dir / "library.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE accounts (platform TEXT PRIMARY KEY, "
                "credential TEXT, extra TEXT)"
            )
            conn.execute(
                "INSERT INTO accounts VALUES ('twitch','account-secret','{}')"
            )
            conn.execute(
                "CREATE TABLE queued_urls (url TEXT)"
            )
            conn.execute(
                "INSERT INTO queued_urls VALUES "
                "('https://media.example/file?token=database-url-secret')"
            )
            conn.commit()
            conn.close()

            backup_path = config_dir / "ordinary.skbackup"
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, message = backup.create_backup(backup_path, include_logs=True)

            self.assertTrue(ok, message)
            raw = backup_path.read_bytes()
            for secret in (
                b"config-secret", b"media-secret", b"cookie-secret",
                b"account-secret", b"log-secret", b"database-url-secret",
            ):
                self.assertNotIn(secret, raw)
            with zipfile.ZipFile(backup_path, "r") as zf:
                self.assertNotIn("cookies.txt", zf.namelist())
                safe_config = json.loads(zf.read("config.json"))
                extracted_db = config_dir / "safe.db"
                extracted_db.write_bytes(zf.read("library.db"))
                safe_log = zf.read("logs/streamkeep.log").decode("utf-8")
            conn = sqlite3.connect(extracted_db)
            try:
                account_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(safe_config["webhook_url"], "")
            self.assertEqual(safe_config["media_server"]["token"], "")
            self.assertEqual(account_count, 0)
            conn = sqlite3.connect(extracted_db)
            try:
                safe_url = conn.execute("SELECT url FROM queued_urls").fetchone()[0]
            finally:
                conn.close()
            self.assertNotIn("database-url-secret", safe_url)
            self.assertIn("***REDACTED***", safe_url)
            self.assertIn("***REDACTED***", safe_log)

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


    def _make_valid_db(self, path):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE items (name TEXT)")
        conn.execute("INSERT INTO items (name) VALUES ('restored-row')")
        conn.commit()
        conn.close()

    def test_restore_rejects_corrupt_database_and_preserves_current_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            current_db = config_dir / "library.db"
            self._make_valid_db(current_db)
            original_bytes = current_db.read_bytes()

            # A backup whose library.db is not a valid SQLite database.
            backup_path = config_dir / "corrupt.skbackup"
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("_backup_meta.json", backup._meta_json())
                zf.writestr("library.db", b"SQLite format 3\x00 but not really")

            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, message = backup.restore_backup(backup_path)

            self.assertFalse(ok)
            self.assertIn("Restore", message)
            # Current database is untouched, byte-for-byte.
            self.assertEqual(current_db.read_bytes(), original_bytes)
            self.assertFalse((config_dir / "library.db.pre-restore").exists())

    def test_restore_rejects_newer_schema_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            current_db = config_dir / "library.db"
            self._make_valid_db(current_db)
            original_bytes = current_db.read_bytes()

            future_db = config_dir / "future.db"
            self._make_valid_db(future_db)
            conn = sqlite3.connect(future_db)
            conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 5}")
            conn.commit()
            conn.close()

            backup_path = config_dir / "future.skbackup"
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("_backup_meta.json", backup._meta_json())
                zf.writestr("library.db", future_db.read_bytes())

            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, message = backup.restore_backup(backup_path)

            self.assertFalse(ok)
            self.assertIn("newer", message)
            self.assertEqual(current_db.read_bytes(), original_bytes)

    def test_restore_rejects_backup_with_unparseable_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            current_db = config_dir / "library.db"
            self._make_valid_db(current_db)
            original_bytes = current_db.read_bytes()

            replacement_db = config_dir / "replacement.db"
            self._make_valid_db(replacement_db)

            backup_path = config_dir / "bad_meta.skbackup"
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("_backup_meta.json", "{ not valid json")
                zf.writestr("library.db", replacement_db.read_bytes())

            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, message = backup.restore_backup(backup_path)

            self.assertFalse(ok)
            self.assertEqual(current_db.read_bytes(), original_bytes)

    def test_restore_activates_only_after_all_files_validate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            current_db = config_dir / "library.db"
            self._make_valid_db(current_db)
            conn = sqlite3.connect(current_db)
            conn.execute("DELETE FROM items")
            conn.execute("INSERT INTO items (name) VALUES ('original-row')")
            conn.commit()
            conn.close()
            original_bytes = current_db.read_bytes()

            good_db = config_dir / "good.db"
            self._make_valid_db(good_db)

            # library.db is valid but search.db is corrupt; the whole restore
            # must abort without swapping the valid library.db in.
            backup_path = config_dir / "mixed.skbackup"
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("_backup_meta.json", backup._meta_json())
                zf.writestr("library.db", good_db.read_bytes())
                zf.writestr("search.db", b"this is not a database")

            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, _message = backup.restore_backup(backup_path)

            self.assertFalse(ok)
            self.assertEqual(current_db.read_bytes(), original_bytes)
            self.assertFalse((config_dir / "search.db").exists())

    def test_restore_rebuilds_search_fts_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            from streamkeep import search
            search_src = config_dir / "search_src.db"
            with mock.patch.object(search, "DB_PATH", search_src):
                conn = search._connect()
                conn.execute(
                    "INSERT INTO transcript_segments "
                    "(recording_path, text, start_sec, end_sec) "
                    "VALUES ('clip.mp4', 'hello searchable world', 0, 5)"
                )
                conn.commit()
                conn.close()

            backup_path = config_dir / "search.skbackup"
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("_backup_meta.json", backup._meta_json())
                zf.writestr("search.db", search_src.read_bytes())

            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, message = backup.restore_backup(backup_path)

            self.assertTrue(ok, message)
            restored = config_dir / "search.db"
            self.assertTrue(restored.is_file())
            with mock.patch.object(search, "DB_PATH", restored):
                hits = search.search_transcripts("searchable")
            self.assertTrue(any("clip.mp4" in str(h) for h in hits))


    def test_successful_restore_clears_marker_and_pre_restore_copies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            self._make_valid_db(config_dir / "library.db")
            (config_dir / "config.json").write_text('{"theme":"old"}', encoding="utf-8")

            replacement = config_dir / "good.db"
            self._make_valid_db(replacement)
            backup_path = config_dir / "r.skbackup"
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("_backup_meta.json", backup._meta_json())
                zf.writestr("library.db", replacement.read_bytes())
                zf.writestr("config.json", b'{"theme":"new"}')

            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, msg = backup.restore_backup(backup_path)

            self.assertTrue(ok, msg)
            self.assertFalse((config_dir / backup.RESTORE_MARKER).exists())
            self.assertFalse((config_dir / "library.db.pre-restore").exists())
            self.assertFalse((config_dir / "config.json.pre-restore").exists())
            self.assertIn("new", (config_dir / "config.json").read_text(encoding="utf-8"))

    def test_interrupted_restore_rolls_back_to_prior_state(self):
        # Simulate a crash mid-activation: os.replace fails on the 2nd swap.
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            self._make_valid_db(config_dir / "library.db")
            (config_dir / "config.json").write_text('{"theme":"old"}', encoding="utf-8")
            old_db_bytes = (config_dir / "library.db").read_bytes()

            replacement = config_dir / "good.db"
            self._make_valid_db(replacement)
            backup_path = config_dir / "r.skbackup"
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("_backup_meta.json", backup._meta_json())
                zf.writestr("library.db", replacement.read_bytes())
                zf.writestr("config.json", b'{"theme":"new"}')

            real_replace = backup.os.replace
            calls = {"n": 0}

            def flaky_replace(src, dst):
                # Let marker + first activation swap through, fail the next one.
                if str(dst).endswith((".db", "config.json")):
                    calls["n"] += 1
                    if calls["n"] == 2:
                        raise OSError("simulated power loss")
                return real_replace(src, dst)

            with mock.patch.object(backup, "CONFIG_DIR", config_dir), \
                 mock.patch.object(backup.os, "replace", side_effect=flaky_replace):
                ok, message = backup.restore_backup(backup_path)

            self.assertFalse(ok)
            self.assertIn("activation", message)
            # Rolled back: marker gone, config self-consistent (old DB + old config),
            # no leftover tmp/pre-restore litter.
            self.assertFalse((config_dir / backup.RESTORE_MARKER).exists())
            self.assertEqual((config_dir / "library.db").read_bytes(), old_db_bytes)
            self.assertIn("old", (config_dir / "config.json").read_text(encoding="utf-8"))
            self.assertFalse((config_dir / "library.db.pre-restore").exists())
            self.assertFalse((config_dir / "library.db.restore-tmp").exists())

    def test_finalize_interrupted_restore_is_noop_without_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                self.assertFalse(backup.finalize_interrupted_restore())

    def test_finalize_reverts_files_named_in_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            # New content in place, old content preserved as .pre-restore, marker set.
            (config_dir / "config.json").write_text("NEW", encoding="utf-8")
            (config_dir / "config.json.pre-restore").write_text("OLD", encoding="utf-8")
            (config_dir / backup.RESTORE_MARKER).write_text(
                json.dumps({"files": ["config.json"]}), encoding="utf-8"
            )
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                self.assertTrue(backup.finalize_interrupted_restore())
            self.assertEqual((config_dir / "config.json").read_text(encoding="utf-8"), "OLD")
            self.assertFalse((config_dir / backup.RESTORE_MARKER).exists())
            self.assertFalse((config_dir / "config.json.pre-restore").exists())


if __name__ == "__main__":
    unittest.main()
