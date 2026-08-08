import tempfile
import threading
import unittest
import multiprocessing
from pathlib import Path
from unittest import mock

from streamkeep import db


def _enqueue_queue_job_process(db_path, url, result_queue):
    from streamkeep import db as process_db
    process_db.DB_PATH = Path(db_path)
    process_db.init_db()
    result_queue.put(process_db.enqueue_queue_job({"url": url})["job_id"])


def _claim_queue_job_process(
    db_path, job_id, owner_id, start_event, result_queue,
):
    from streamkeep import db as process_db
    process_db.DB_PATH = Path(db_path)
    start_event.wait(10)
    claimed = process_db.claim_queue_job(
        job_id, owner_id, now=101.0,
    )
    result_queue.put(bool(claimed))


class DbMigrationTests(unittest.TestCase):
    def test_profile_connections_are_reused_per_thread_and_close_explicitly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "profile"
            profile_dir.mkdir()
            db_path = profile_dir / "library.db"
            with mock.patch.object(db, "CONFIG_DIR", profile_dir), mock.patch.object(
                db, "DB_PATH", db_path
            ), mock.patch.object(
                db, "sqlite_connect", wraps=db.sqlite_connect
            ) as connect:
                db.close_connections()
                first = db._connect()
                first.close()
                second = db._connect()
                self.assertIs(first._connection, second._connection)
                second.close()
                self.assertEqual(connect.call_count, 1)

                db.close_connections()
                third = db._connect()
                self.assertIsNot(first._connection, third._connection)
                third.close()
                self.assertEqual(connect.call_count, 2)
                db.close_connections()

    def test_concurrent_v10_initialization_serializes_schema_migration(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute("ALTER TABLE monitor_channels DROP COLUMN auth_profile_id")
                conn.execute("PRAGMA user_version = 10")
                conn.commit()
            finally:
                conn.close()

            original_connect = db._connect
            connected = threading.Barrier(2)
            errors = []

            def synchronized_connect(*args, **kwargs):
                connection = original_connect(*args, **kwargs)
                try:
                    connected.wait(timeout=10)
                except BaseException:
                    connection.close()
                    raise
                return connection

            def initialize():
                try:
                    db.init_db()
                except BaseException as error:
                    errors.append(error)

            with mock.patch.object(db, "DB_PATH", db_path), mock.patch.object(
                db, "_connect", side_effect=synchronized_connect,
            ):
                threads = [threading.Thread(target=initialize) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=15)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            conn = sqlite3.connect(str(db_path))
            try:
                columns = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(monitor_channels)"
                    ).fetchall()
                }
                version = conn.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertIn("auth_profile_id", columns)
            self.assertEqual(version, db.SCHEMA_VERSION)

    def test_newer_schema_is_refused_before_schema_or_fts_writes(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
            conn.commit()
            conn.close()
            original_bytes = db_path.read_bytes()

            with mock.patch.object(db, "DB_PATH", db_path), mock.patch.object(
                db, "_configure_history_fts"
            ) as configure_fts:
                with self.assertRaises(db.DatabaseSchemaError) as error:
                    db.init_db()

            self.assertEqual(error.exception.database_version, db.SCHEMA_VERSION + 1)
            self.assertEqual(error.exception.supported_version, db.SCHEMA_VERSION)
            self.assertIn(str(db.SCHEMA_VERSION + 1), str(error.exception))
            self.assertIn(str(db.SCHEMA_VERSION), str(error.exception))
            self.assertIn("newer StreamKeep build", str(error.exception))
            self.assertEqual(db_path.read_bytes(), original_bytes)
            configure_fts.assert_not_called()
            conn = sqlite3.connect(str(db_path))
            try:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(tables, [])

    def test_v8_history_identity_is_backfilled_and_exactly_queryable(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT '',
                    quality TEXT NOT NULL DEFAULT '',
                    size TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    watched INTEGER NOT NULL DEFAULT 0,
                    watch_position_secs REAL NOT NULL DEFAULT 0.0,
                    bookmarks TEXT NOT NULL DEFAULT '[]'
                )
            """)
            conn.execute(
                "INSERT INTO history(platform, title, url) VALUES(?,?,?)",
                (
                    "Twitch",
                    "Archived VOD",
                    "https://www.twitch.tv/videos/123456",
                ),
            )
            conn.execute("PRAGMA user_version = 8")
            conn.commit()
            conn.close()

            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                found = db.find_history_by_identity(
                    "twitch", "vod:123456"
                )
                wrong_platform = db.find_history_by_identity(
                    "Kick", "vod:123456"
                )

            self.assertIsNotNone(found)
            self.assertEqual(found["source_id"], "vod:123456")
            self.assertIsNone(wrong_platform)

    def test_v16_migration_persists_canonical_url_and_leaves_unknown_id_blank(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    source_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT '',
                    quality TEXT NOT NULL DEFAULT '',
                    size TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    watched INTEGER NOT NULL DEFAULT 0,
                    watch_position_secs REAL NOT NULL DEFAULT 0.0,
                    bookmarks TEXT NOT NULL DEFAULT '[]'
                )
            """)
            conn.execute(
                "INSERT INTO history(platform, title, url) VALUES(?,?,?)",
                (
                    "Direct",
                    "Unknown page",
                    "https://WWW.Example.com/watch?b=2&utm_source=test&a=1",
                ),
            )
            conn.execute("PRAGMA user_version = 15")
            conn.commit()
            conn.close()

            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                row = db.find_history_by_url(
                    "https://example.com/watch?a=1&b=2"
                )

            self.assertIsNotNone(row)
            self.assertEqual(row["source_id"], "")
            self.assertEqual(
                row["webpage_url"],
                "https://example.com/watch?a=1&b=2",
            )

    def test_history_persistence_deduplicates_three_url_forms_by_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                for index, url in enumerate((
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "HTTP://YOUTUBE.COM/watch?utm_source=x&v=dQw4w9WgXcQ",
                    "https://youtu.be/dQw4w9WgXcQ?si=shared",
                )):
                    db.save_history_entry({
                        "title": f"Copy {index}",
                        "platform": "yt-dlp",
                        "url": url,
                    })
                found = db.find_history_by_identity(
                    "yt-dlp", "dQw4w9WgXcQ"
                )

            self.assertIsNotNone(found)
            self.assertEqual(
                found["webpage_url"],
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            )
            self.assertEqual(found["source_id"], "dQw4w9WgXcQ")

    def test_monitor_argument_template_attachment_persists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                db.save_monitor_channel({
                    "url": "https://example.com/channel",
                    "ytdlp_template_name": "Authenticated archive",
                })
                channels = db.load_monitor_channels()
            self.assertEqual(
                channels[0]["ytdlp_template_name"], "Authenticated archive"
            )

    def test_v5_monitor_schema_migrates_template_column(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE monitor_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE
                )
            """)
            conn.execute("PRAGMA user_version = 5")
            conn.commit()
            conn.close()
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                conn = sqlite3.connect(str(db_path))
                columns = {
                    row[1] for row in conn.execute(
                        "PRAGMA table_info(monitor_channels)"
                    ).fetchall()
                }
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                conn.close()
            self.assertIn("ytdlp_template_name", columns)
            self.assertEqual(version, db.SCHEMA_VERSION)

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

    def test_completed_history_and_manifest_roll_back_together(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                with self.assertRaises(TypeError):
                    db.save_completed_recording(
                        {
                            "platform": "Twitch",
                            "source_id": "vod:123",
                            "title": "Upgrade",
                            "path": str(Path(tmpdir) / "upgrade"),
                        },
                        {"files": {"not", "json", "serializable"}},
                    )
                count = db.history_count()
                manifest_count = db.archive_manifest_count()

            self.assertEqual(count, 0)
            self.assertEqual(manifest_count, 0)

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


class DbTombstoneTests(unittest.TestCase):
    def test_user_history_delete_records_canonical_tombstone_and_clear_allows_refetch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                history_id = db.save_history_entry({
                    "platform": "Twitch",
                    "title": "Archived VOD",
                    "path": str(Path(tmpdir) / "recording"),
                    "url": "https://WWW.Twitch.tv/videos/123456?utm_source=old",
                })
                db.delete_history_entries([history_id])
                tombstones = db.list_tombstones()
                blocked = db.is_tombstoned(
                    "twitch", "vod:123456",
                    "https://www.twitch.tv/videos/123456?utm_campaign=new",
                )
                cleared = db.clear_tombstone(tombstones[0]["id"])
                unblocked = db.is_tombstoned(
                    "twitch", "vod:123456",
                    "https://www.twitch.tv/videos/123456",
                )

            self.assertEqual(len(tombstones), 1)
            self.assertEqual(tombstones[0]["reason"], "user")
            self.assertEqual(tombstones[0]["source_id"], "vod:123456")
            self.assertEqual(
                tombstones[0]["webpage_url"],
                "https://www.twitch.tv/videos/123456",
            )
            self.assertTrue(blocked)
            self.assertTrue(cleared)
            self.assertFalse(unblocked)

    def test_lifecycle_and_retention_tombstones_are_audited_but_not_blocking(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                history_id = db.save_history_entry({
                    "platform": "yt-dlp",
                    "title": "Episode",
                    "path": str(Path(tmpdir) / "episode"),
                    "url": "https://www.youtube.com/watch?v=abc123",
                })
                db.delete_history_entries([history_id], reason="lifecycle")
                marker = db.find_tombstone(
                    "yt-dlp", "abc123", blocking_only=False,
                )
                blocked = db.is_tombstoned("yt-dlp", "abc123")

            self.assertEqual(marker["reason"], "lifecycle")
            self.assertFalse(blocked)

    def test_path_delete_and_queue_dispatch_skip_share_the_same_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            recording = Path(tmpdir) / "recording"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                history_id = db.save_history_entry({
                    "platform": "yt-dlp",
                    "title": "Episode",
                    "path": str(recording),
                    "url": "https://www.youtube.com/watch?v=queue123",
                })
                self.assertEqual(
                    db.delete_history_for_paths([str(recording)]), 1,
                )
                remaining = db.history_count()
                queued = db.enqueue_queue_job({
                    "url": "https://youtube.com/watch?v=queue123&utm_medium=mail",
                    "platform": "yt-dlp",
                    "source_id": "queue123",
                    "webpage_url": "https://youtube.com/watch?v=queue123",
                })

            self.assertGreater(history_id, 0)
            self.assertEqual(remaining, 0)
            self.assertEqual(queued["status"], "cancelled")
            self.assertTrue(queued["tombstone_skipped"])
            self.assertEqual(queued["tombstone_reason"], "user")

    def test_dispatch_cancels_legacy_queued_row_after_tombstone_is_added(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                db.save_queue([{
                    "url": "https://www.youtube.com/watch?v=legacy-queued",
                    "platform": "yt-dlp",
                    "source_id": "legacy-queued",
                    "webpage_url": "https://www.youtube.com/watch?v=legacy-queued",
                    "status": "queued",
                }])
                db.record_tombstone(
                    platform="yt-dlp", source_id="legacy-queued",
                    webpage_url="https://www.youtube.com/watch?v=legacy-queued",
                )
                skipped = db.skip_tombstoned_queue_jobs()

            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["status"], "cancelled")
            self.assertTrue(skipped[0]["tombstone_skipped"])


class DbQueueNormalizationTests(unittest.TestCase):
    def test_executor_lease_takeover_recovers_only_expired_owner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                job = db.enqueue_queue_job({"url": "https://a.example/video"})
                first = db.acquire_executor_lease(
                    "owner-a", owner_kind="desktop", now=100.0,
                    lease_seconds=10,
                )
                claimed = db.claim_queue_job(
                    job["job_id"], "owner-a", now=101.0,
                )
                refused = db.acquire_executor_lease(
                    "owner-b", owner_kind="server", now=105.0,
                    lease_seconds=10,
                )
                takeover = db.acquire_executor_lease(
                    "owner-b", owner_kind="server", now=111.0,
                    lease_seconds=10,
                )
                recovered = db.load_queue_job(job["job_id"])
                stale_transition = db.transition_owned_queue_job(
                    job["job_id"], "owner-a",
                    expected_statuses="fetching", status="done",
                )
                reclaimed = db.claim_queue_job(
                    job["job_id"], "owner-b", now=112.0,
                )

            self.assertTrue(first["acquired"])
            self.assertEqual(claimed["execution_owner"], "owner-a")
            self.assertFalse(refused["acquired"])
            self.assertIn("desktop", refused["message"])
            self.assertTrue(takeover["acquired"])
            self.assertEqual(takeover["recovered"], 1)
            self.assertEqual(recovered["status"], "queued")
            self.assertEqual(recovered["execution_owner"], "")
            self.assertIsNone(stale_transition)
            self.assertEqual(reclaimed["execution_owner"], "owner-b")

    def test_stale_snapshot_merge_preserves_concurrent_enqueue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                first = db.enqueue_queue_job({"url": "https://a.example/video"})
                stale_snapshot = db.load_queue()

                context = multiprocessing.get_context("spawn")
                result_queue = context.Queue()
                process = context.Process(
                    target=_enqueue_queue_job_process,
                    args=(
                        str(db_path), "https://b.example/video", result_queue,
                    ),
                )
                process.start()
                process.join(20)
                self.assertEqual(process.exitcode, 0)
                second_id = result_queue.get(timeout=5)

                stale_snapshot[0]["title"] = "Edited in desktop"
                merged = db.sync_queue_items(stale_snapshot)

            self.assertEqual(
                {item["job_id"] for item in merged},
                {first["job_id"], second_id},
            )
            self.assertEqual(merged[0]["title"], "Edited in desktop")

    def test_stale_snapshot_cannot_requeue_completed_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                job = db.enqueue_queue_job({"url": "https://a.example/video"})
                stale_snapshot = db.load_queue()
                completed = db.update_queue_job(
                    job["job_id"], status="done", progress=100,
                )
                merged = db.sync_queue_items(stale_snapshot)
                stale_snapshot[0].update(merged[0])
                merged_again = db.sync_queue_items(stale_snapshot)

            self.assertEqual(completed["status"], "done")
            self.assertEqual(merged[0]["status"], "done")
            self.assertEqual(merged[0]["progress"], 100)
            self.assertEqual(merged_again[0]["status"], "done")

    def test_multiprocess_claim_compare_and_swap_has_one_winner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                job = db.enqueue_queue_job({"url": "https://a.example/video"})
                db.acquire_executor_lease(
                    "owner-a", owner_kind="test", now=100.0,
                    lease_seconds=30,
                )

                context = multiprocessing.get_context("spawn")
                start_event = context.Event()
                result_queue = context.Queue()
                processes = [
                    context.Process(
                        target=_claim_queue_job_process,
                        args=(
                            str(db_path), job["job_id"], "owner-a",
                            start_event, result_queue,
                        ),
                    )
                    for _ in range(2)
                ]
                for process in processes:
                    process.start()
                start_event.set()
                for process in processes:
                    process.join(20)
                    self.assertEqual(process.exitcode, 0)
                results = [result_queue.get(timeout=5) for _ in processes]
                durable = db.load_queue_job(job["job_id"])

            self.assertEqual(sorted(results), [False, True])
            self.assertEqual(durable["status"], "fetching")
            self.assertEqual(durable["execution_owner"], "owner-a")

    def test_delete_refuses_another_executors_active_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                job = db.enqueue_queue_job({"url": "https://a.example/video"})
                db.acquire_executor_lease(
                    "owner-a", owner_kind="server", now=100.0,
                )
                db.claim_queue_job(job["job_id"], "owner-a", now=101.0)
                removed = db.delete_queue_job(
                    job["job_id"], requester_owner="owner-b",
                )
                durable = db.load_queue_job(job["job_id"])

            self.assertFalse(removed)
            self.assertEqual(durable["status"], "fetching")

    def test_optimistic_update_rejects_stale_revision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                job = db.enqueue_queue_job({"url": "https://a.example/video"})
                first = db.update_queue_job(
                    job["job_id"],
                    expected_revision=job["revision"],
                    title="first",
                )
                stale = db.update_queue_job(
                    job["job_id"],
                    expected_revision=job["revision"],
                    title="stale",
                )
                durable = db.load_queue_job(job["job_id"])

            self.assertEqual(first["title"], "first")
            self.assertIsNone(stale)
            self.assertEqual(durable["title"], "first")

    def test_queue_job_ids_survive_full_queue_rewrites(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                items = [{"url": "https://example.com/video", "status": "queued"}]
                db.save_queue(items)
                first_id = items[0]["job_id"]
                loaded = db.load_queue()
                db.save_queue(loaded)
                reloaded = db.load_queue()

            self.assertTrue(first_id)
            self.assertEqual(reloaded[0]["job_id"], first_id)

    def test_atomic_queue_transitions_and_restart_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                first = db.enqueue_queue_job({"url": "https://a.example/video"})
                second = db.enqueue_queue_job({"url": "https://b.example/video"})
                third = db.enqueue_queue_job({"url": "https://c.example/video"})
                db.update_queue_job(first["job_id"], status="downloading", progress=40)
                db.update_queue_job(third["job_id"], status="finalizing")
                recovered = db.recover_interrupted_queue_jobs()
                first_after = db.load_queue_job(first["job_id"])
                third_after = db.load_queue_job(third["job_id"])
                cancelled = db.cancel_queue_job(second["job_id"])
                db.update_queue_job(first["job_id"], status="done")
                terminal = db.cancel_queue_job(first["job_id"])

            self.assertEqual(recovered, 2)
            self.assertEqual(first_after["status"], "queued")
            self.assertEqual(third_after["status"], "queued")
            self.assertEqual(first_after["progress"], 40)
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(terminal["status"], "done")

    def test_v4_queue_migration_backfills_unique_job_ids(self):
        import json
        import sqlite3
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE download_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position INTEGER NOT NULL DEFAULT 0,
                    url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '', quality TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued', recurrence TEXT NOT NULL DEFAULT '',
                    failure_id INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '',
                    data TEXT NOT NULL DEFAULT '{}'
                )
            """)
            duplicate = json.dumps({"job_id": "legacy", "url": "https://example.com"})
            conn.executemany(
                "INSERT INTO download_queue (position, url, data) VALUES (?, ?, ?)",
                [(0, "https://a.example", duplicate), (1, "https://b.example", duplicate)],
            )
            conn.execute("PRAGMA user_version = 4")
            conn.commit()
            conn.close()

            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                jobs = db.load_queue()

            self.assertEqual(len({job["job_id"] for job in jobs}), 2)
            self.assertIn("legacy", {job["job_id"] for job in jobs})

    def test_save_and_load_queue_preserves_typed_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                items = [
                    {"url": "https://a.com/v1", "title": "Video 1", "platform": "Kick",
                     "status": "queued", "quality": "1080p"},
                    {"url": "https://b.com/v2", "title": "Video 2", "platform": "Twitch",
                     "status": "running", "recurrence": "daily"},
                ]
                db.save_queue(items)
                loaded = db.load_queue()

            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["url"], "https://a.com/v1")
            self.assertEqual(loaded[0]["platform"], "Kick")
            self.assertEqual(loaded[1]["recurrence"], "daily")

    def test_load_queue_by_status_filters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                db.save_queue([
                    {"url": "https://a.com", "status": "queued"},
                    {"url": "https://b.com", "status": "running"},
                    {"url": "https://c.com", "status": "queued"},
                ])
                queued = db.load_queue_by_status("queued")
                running = db.load_queue_by_status("running")

            self.assertEqual(len(queued), 2)
            self.assertEqual(len(running), 1)
            self.assertEqual(running[0]["url"], "https://b.com")

    def test_legacy_json_only_queue_migrates_losslessly(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE download_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position INTEGER NOT NULL DEFAULT 0,
                    data TEXT NOT NULL DEFAULT '{}'
                )
            """)
            import json
            legacy_item = {"url": "https://old.com/video", "title": "Old", "platform": "Rumble"}
            conn.execute(
                "INSERT INTO download_queue (position, data) VALUES (0, ?)",
                (json.dumps(legacy_item),),
            )
            conn.execute("PRAGMA user_version = 3")
            conn.commit()
            conn.close()

            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                loaded = db.load_queue()

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["url"], "https://old.com/video")
            self.assertEqual(loaded[0]["platform"], "Rumble")


class DbMaintenanceTests(unittest.TestCase):
    def test_check_integrity_on_healthy_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                ok, detail = db.check_integrity()
            self.assertTrue(ok)
            self.assertEqual(detail, "ok")

    def test_check_integrity_missing_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nonexistent.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                ok, detail = db.check_integrity()
            self.assertFalse(ok)
            self.assertIn("does not exist", detail)

    def test_optimize_on_healthy_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                result = db.run_optimize()
            self.assertEqual(result, "ok")

    def test_checkpoint_wal_on_healthy_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                ok, detail = db.checkpoint_wal()
            self.assertTrue(ok)
            self.assertTrue(
                "pages written" in detail or "Rollback journal active" in detail
            )

    def test_vacuum_after_backup_skips_on_backup_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                ok, detail = db.vacuum_after_backup(
                    backup_fn=lambda _: (False, "disk full"),
                )
            self.assertFalse(ok)
            self.assertIn("disk full", detail)

    def test_vacuum_without_backup_succeeds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                db.save_history_entry({"title": "Test", "url": "https://x.com/v"})
                ok, detail = db.vacuum_after_backup()
            self.assertTrue(ok)
            self.assertIn("complete", detail.lower())

    def test_db_diagnostics_on_healthy_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                db.save_history_entry({"title": "T", "url": "https://x.com/v"})
                diag = db.db_diagnostics()
            self.assertTrue(diag["exists"])
            self.assertEqual(diag["schema_version"], db.SCHEMA_VERSION)
            self.assertEqual(diag["quick_check"], "ok")
            self.assertEqual(diag["row_counts"]["history"], 1)

    def test_db_diagnostics_missing_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nonexistent.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                diag = db.db_diagnostics()
            self.assertFalse(diag["exists"])


if __name__ == "__main__":
    unittest.main()


# ── V185: crash recovery reports its own failure ─────────────────────

def test_a_failed_recovery_is_recorded_and_logged_without_aborting_startup(
    tmp_path, monkeypatch,
):
    """A failed rollback must be visible, and must not stop the library opening.

    ``init_db`` runs three crash-recovery entry points before and after opening
    the schema. Their failure used to be swallowed, so the app carried on
    against a half-restored config directory with no trace at all (V185).
    """
    from streamkeep import crash_log
    from streamkeep.db import recovery as db_recovery

    warnings = []
    monkeypatch.setattr(crash_log, "record_startup_warning", warnings.append)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("rollback could not complete")

    monkeypatch.setattr(db_recovery, "call_recovery", _boom)
    db_recovery.consume_failures()

    db_path = tmp_path / "library.db"
    with mock.patch.object(db, "DB_PATH", db_path):
        db.init_db()
        # Startup still completed and the schema is usable.
        row_id = db.save_history_entry(
            {"platform": "test", "title": "ok", "url": "u"}
        )
        assert row_id, "the library must open despite a failed recovery"

    failures = db_recovery.consume_failures()
    stages = {entry["stage"] for entry in failures}
    assert stages == {"restore", "rebuild", "re-template"}, stages
    assert all("rollback could not complete" in entry["error"] for entry in failures)
    assert len(warnings) == 3, "each failed recovery must reach the crash log"
    assert any("restore recovery failed" in text for text in warnings)


def test_recovery_failures_are_consumed_once():
    from streamkeep.db import recovery as db_recovery

    db_recovery.consume_failures()
    db_recovery._FAILURES.append({"stage": "restore", "error": "x"})
    assert len(db_recovery.consume_failures()) == 1
    assert db_recovery.consume_failures() == []
