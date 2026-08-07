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


    def test_create_backup_preserves_media_tombstones(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            db_path = config_dir / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                db.record_tombstone(
                    platform="yt-dlp",
                    source_id="backup-vod",
                    webpage_url="https://www.youtube.com/watch?v=backup-vod",
                    reason="user",
                )

            backup_path = config_dir / "tombstones.skbackup"
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, message = backup.create_backup(backup_path)

            self.assertTrue(ok, message)
            extracted_db = config_dir / "tombstone_snapshot.db"
            with zipfile.ZipFile(backup_path, "r") as archive:
                extracted_db.write_bytes(archive.read("library.db"))
            conn = sqlite3.connect(extracted_db)
            try:
                row = conn.execute(
                    "SELECT platform, source_id, reason FROM media_tombstones"
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row, ("yt-dlp", "backup-vod", "user"))


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


class DownloadArchiveBackupTests(unittest.TestCase):
    """The archive files are what stop a restored profile re-downloading."""

    def _profile(self, root):
        config_dir = root / "config"
        (config_dir / "download-archives").mkdir(parents=True)
        (config_dir / "config.json").write_text("{}", encoding="utf-8")
        return config_dir

    def test_round_trip_restores_download_archives(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_dir = self._profile(root)
            archives = config_dir / "download-archives"
            (archives / "aaa.txt").write_text("youtube abc123\n", encoding="utf-8")
            (archives / "bbb.txt").write_text("twitch 999\n", encoding="utf-8")
            backup_path = root / "profile.skbackup"

            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, message = backup.create_backup(backup_path)
            self.assertTrue(ok, message)

            with zipfile.ZipFile(backup_path) as zf:
                names = set(zf.namelist())
            self.assertIn("download-archives/aaa.txt", names)
            self.assertIn("download-archives/bbb.txt", names)

            restored = root / "restored"
            restored.mkdir()
            (restored / "config.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(backup, "CONFIG_DIR", restored):
                ok, message = backup.restore_backup(backup_path)
            self.assertTrue(ok, message)
            self.assertEqual(
                (restored / "download-archives" / "aaa.txt").read_text(encoding="utf-8"),
                "youtube abc123\n",
            )
            self.assertEqual(
                (restored / "download-archives" / "bbb.txt").read_text(encoding="utf-8"),
                "twitch 999\n",
            )

    def test_restore_merges_rather_than_deleting_unlisted_archives(self):
        """Deleting an archive the backup lacks would cause a re-download."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_dir = self._profile(root)
            (config_dir / "download-archives" / "aaa.txt").write_text(
                "youtube abc123\n", encoding="utf-8"
            )
            backup_path = root / "profile.skbackup"
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, _message = backup.create_backup(backup_path)
            self.assertTrue(ok)

            restored = root / "restored"
            (restored / "download-archives").mkdir(parents=True)
            (restored / "config.json").write_text("{}", encoding="utf-8")
            (restored / "download-archives" / "newer.txt").write_text(
                "kick 42\n", encoding="utf-8"
            )
            with mock.patch.object(backup, "CONFIG_DIR", restored):
                ok, _message = backup.restore_backup(backup_path)
            self.assertTrue(ok)
            self.assertTrue((restored / "download-archives" / "aaa.txt").is_file())
            self.assertTrue((restored / "download-archives" / "newer.txt").is_file())

    def test_backup_excludes_credentials_plugins_and_source_adapters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_dir = self._profile(root)
            for name, payload in (
                ("auth", "cookie-jar-secret"),
                ("plugins", "print('code')"),
                ("source_adapters", "id: adapter"),
            ):
                directory = config_dir / name
                directory.mkdir()
                (directory / "entry.txt").write_text(payload, encoding="utf-8")
            backup_path = root / "profile.skbackup"

            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, _message = backup.create_backup(backup_path)
            self.assertTrue(ok)

            with zipfile.ZipFile(backup_path) as zf:
                names = zf.namelist()
                blob = b"".join(zf.read(name) for name in names)
            for excluded in ("auth/", "plugins/", "source_adapters/"):
                self.assertFalse(
                    any(name.startswith(excluded) for name in names),
                    f"{excluded} must not be in a secret-free backup",
                )
            self.assertNotIn(b"cookie-jar-secret", blob)

    def test_restore_refuses_traversing_directory_members(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            backup_path = root / "evil.skbackup"
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("_backup_meta.json", backup._meta_json())
                zf.writestr("config.json", "{}")
                zf.writestr("download-archives/../../escaped.txt", "pwned")
                zf.writestr("download-archives/nested/deep.txt", "pwned")
                zf.writestr("plugins/evil.py", "import os")

            restored = root / "restored"
            restored.mkdir()
            with mock.patch.object(backup, "CONFIG_DIR", restored):
                ok, message = backup.restore_backup(backup_path)
            self.assertTrue(ok, message)
            self.assertFalse((root / "escaped.txt").exists())
            self.assertFalse((restored.parent / "escaped.txt").exists())
            self.assertFalse((restored / "plugins").exists())
            archives = restored / "download-archives"
            if archives.exists():
                self.assertEqual(list(archives.rglob("*.txt")), [])

    def test_directory_member_validator_rejects_unsafe_names(self):
        self.assertIsNone(backup._safe_directory_member("download-archives/../x"))
        self.assertIsNone(backup._safe_directory_member("download-archives/a/b"))
        self.assertIsNone(backup._safe_directory_member("plugins/x.py"))
        self.assertIsNone(backup._safe_directory_member("download-archives/"))
        self.assertIsNone(backup._safe_directory_member("nope/x.txt"))
        self.assertEqual(
            backup._safe_directory_member("download-archives/ok.txt"),
            ("download-archives", "ok.txt"),
        )


# ── V148: the semantic index is reconciled against the restored library ──

def test_semantic_index_drops_hits_for_recordings_the_library_lost(tmp_path, monkeypatch):
    """semantic.db is outside the backup set, so restoring an older library.db
    used to leave semantic search answering for recordings that are gone."""
    from streamkeep import semantic

    monkeypatch.setattr(semantic, "DB_PATH", tmp_path / "semantic.db")
    connection = semantic._connect()
    try:
        for path in ("C:/rec/kept", "C:/rec/orphan"):
            connection.execute(
                "INSERT INTO semantic_moments(recording_path, start_sec, end_sec,"
                " modality, provenance, text, confidence, vector)"
                " VALUES (?, 0, 1, 'transcript', 'p', 'hello', 1, ?)",
                (path, semantic._pack_vector(semantic.local_embedding("hello"))),
            )
        connection.commit()
    finally:
        connection.close()

    # The restored library knows about one of them, plus one never indexed.
    result = semantic.reconcile_with_library(["C:/rec/kept", "C:/rec/fresh"])
    assert result == {"pruned": 1, "kept": 1, "unindexed": 1, "ran": True}

    hits = semantic.search_moments("hello")
    assert {hit["recording_path"] for hit in hits} == {"C:/rec/kept"}


def test_reconcile_is_a_no_op_when_the_index_already_matches(tmp_path, monkeypatch):
    from streamkeep import semantic

    monkeypatch.setattr(semantic, "DB_PATH", tmp_path / "semantic.db")
    connection = semantic._connect()
    try:
        connection.execute(
            "INSERT INTO semantic_moments(recording_path, start_sec, end_sec,"
            " modality, provenance, text, confidence, vector)"
            " VALUES ('C:/rec/one', 0, 1, 'transcript', 'p', 'hi', 1, ?)",
            (semantic._pack_vector(semantic.local_embedding("hi")),),
        )
        connection.commit()
    finally:
        connection.close()
    assert semantic.reconcile_with_library(["C:/rec/one"]) == {
        "pruned": 0, "kept": 1, "unindexed": 0, "ran": True,
    }


def test_reconcile_without_an_index_file_is_not_an_error(tmp_path, monkeypatch):
    from streamkeep import semantic

    monkeypatch.setattr(semantic, "DB_PATH", tmp_path / "missing.db")
    assert semantic.reconcile_with_library(["C:/rec/one"])["ran"] is False


def test_a_restore_reports_what_it_did_to_the_semantic_index(monkeypatch):
    from streamkeep import backup as backup_module, semantic

    monkeypatch.setattr(
        semantic, "reconcile_with_library",
        lambda *_a, **_k: {"pruned": 2, "kept": 5, "unindexed": 3, "ran": True},
    )
    note = backup_module.reconcile_derived_indexes()
    assert "dropped 2 semantic index entries" in note
    assert "3 restored recording(s) are not in the semantic index yet" in note


def test_a_failing_reconcile_never_fails_the_restore(monkeypatch):
    from streamkeep import backup as backup_module, semantic

    def boom(*_args, **_kwargs):
        raise sqlite3.DatabaseError("index is corrupt")

    monkeypatch.setattr(semantic, "reconcile_with_library", boom)
    assert backup_module.reconcile_derived_indexes() == ""
