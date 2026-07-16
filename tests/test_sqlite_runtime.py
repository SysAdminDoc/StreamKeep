import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import backup, db, sqlite_runtime


class SQLiteRuntimePolicyTests(unittest.TestCase):
    def test_fix_matrix_recognizes_mainline_and_backports(self):
        for version in ((3, 51, 3), (3, 52, 0), (3, 50, 7), (3, 44, 6)):
            with self.subTest(version=version):
                self.assertTrue(sqlite_runtime.wal_reset_is_fixed(version))
        for version in ((3, 51, 2), (3, 50, 6), (3, 49, 9), (3, 44, 5)):
            with self.subTest(version=version):
                self.assertFalse(sqlite_runtime.wal_reset_is_fixed(version))

    def test_vulnerable_source_runtime_enforces_rollback_journal(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(
                    sqlite_runtime.sqlite3, "sqlite_version_info", (3, 45, 1)
                ), mock.patch.object(
                    sqlite_runtime.sys, "frozen", False, create=True
                ):
            path = Path(tmpdir) / "policy.db"
            connection = sqlite_runtime.connect(path)
            try:
                journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
                foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            finally:
                connection.close()
            status = sqlite_runtime.runtime_status()

        self.assertEqual(journal, "delete")
        self.assertEqual(foreign_keys, 1)
        self.assertTrue(status["supported"])
        self.assertTrue(status["degraded"])
        self.assertIn("rollback journaling", status["detail"])

    def test_fixed_runtime_enables_wal(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(
                    sqlite_runtime.sqlite3, "sqlite_version_info", (3, 51, 3)
                ):
            connection = sqlite_runtime.connect(Path(tmpdir) / "fixed.db")
            try:
                journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
            finally:
                connection.close()
        self.assertEqual(journal, "wal")

    def test_vulnerable_frozen_runtime_is_rejected_before_open(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(
                    sqlite_runtime.sqlite3, "sqlite_version_info", (3, 50, 6)
                ), mock.patch.object(
                    sqlite_runtime.sys, "frozen", True, create=True
                ):
            path = Path(tmpdir) / "must-not-exist.db"
            with self.assertRaises(sqlite_runtime.UnsafeSQLiteRuntimeError) as raised:
                sqlite_runtime.connect(path)

        self.assertFalse(path.exists())
        self.assertIn("Frozen releases require SQLite", str(raised.exception))


class SQLiteRecoveryStressTests(unittest.TestCase):
    def test_writers_checkpoints_backups_and_interruption_preserve_state(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(db, "DB_PATH", Path(tmpdir) / "library.db"), \
                mock.patch.object(
                    sqlite_runtime.sqlite3, "sqlite_version_info", (3, 45, 1)
                ), mock.patch.object(
                    sqlite_runtime.sys, "frozen", False, create=True
                ):
            db.init_db()
            errors = []
            snapshots = []
            writer_done = threading.Event()

            def write_queue():
                try:
                    for index in range(40):
                        db.enqueue_queue_job({
                            "job_id": f"stress-{index}",
                            "url": f"https://example.com/{index}",
                            "title": f"Stress {index}",
                            "status": "queued",
                        })
                except Exception as error:  # pragma: no cover - asserted below
                    errors.append(error)
                finally:
                    writer_done.set()

            def checkpoint_and_backup():
                try:
                    while not writer_done.is_set():
                        ok, detail = db.checkpoint_wal()
                        if not ok:
                            raise AssertionError(detail)
                        snapshot = backup._snapshot_sqlite_db(db.DB_PATH)
                        if snapshot is not None:
                            snapshots.append(snapshot)
                    snapshot = backup._snapshot_sqlite_db(db.DB_PATH)
                    if snapshot is not None:
                        snapshots.append(snapshot)
                except Exception as error:  # pragma: no cover - asserted below
                    errors.append(error)

            writer = threading.Thread(target=write_queue)
            observer = threading.Thread(target=checkpoint_and_backup)
            observer.start()
            writer.start()
            writer.join(30)
            observer.join(30)

            self.assertFalse(writer.is_alive())
            self.assertFalse(observer.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(snapshots)

            interrupted = sqlite_runtime.connect(db.DB_PATH, check_same_thread=False)
            interrupted.execute("BEGIN IMMEDIATE")
            interrupted.execute(
                "INSERT INTO download_queue (job_id, position, url) VALUES (?, ?, ?)",
                ("interrupted", 999, "https://example.com/interrupted"),
            )
            interrupted.close()

            connection = sqlite_runtime.connect(db.DB_PATH)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0], "ok"
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM download_queue WHERE job_id LIKE 'stress-%'"
                    ).fetchone()[0],
                    40,
                )
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM download_queue WHERE job_id='interrupted'"
                ).fetchone())
            finally:
                connection.close()

            snapshot = sqlite3.connect(snapshots[-1])
            try:
                self.assertEqual(snapshot.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertGreater(
                    snapshot.execute("SELECT COUNT(*) FROM download_queue").fetchone()[0],
                    0,
                )
            finally:
                snapshot.close()
                for path in snapshots:
                    Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
