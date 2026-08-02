"""Automatic rotating backup schedule (V51).

Every case drives a fake clock so cadence, restart, backoff, and rotation are
deterministic and never depend on wall time.
"""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from streamkeep import backup, db


class BackupSettingsTests(unittest.TestCase):
    def test_defaults_are_off_daily_and_inside_the_profile(self):
        settings = backup.backup_settings({})
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["cadence"], "daily")
        self.assertEqual(settings["cadence_seconds"], 24 * 60 * 60)
        self.assertEqual(settings["keep_last"], 5)
        self.assertTrue(settings["dir"].endswith("backups"))

    def test_operator_values_are_read_and_clamped(self):
        settings = backup.backup_settings({
            "auto_backup_enabled": True,
            "auto_backup_dir": r"D:\archive\sk",
            "auto_backup_cadence": "WEEKLY",
            "auto_backup_keep_last": 999,
        })
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["dir"], r"D:\archive\sk")
        self.assertEqual(settings["cadence"], "weekly")
        self.assertEqual(settings["keep_last"], 50)

    def test_unknown_cadence_falls_back_to_daily(self):
        settings = backup.backup_settings({"auto_backup_cadence": "fortnightly"})
        self.assertEqual(settings["cadence"], "daily")

    def test_failure_backoff_grows_but_never_exceeds_the_cadence(self):
        cap = 60 * 60
        first = backup.failure_backoff_seconds(1, cap_seconds=cap)
        second = backup.failure_backoff_seconds(2, cap_seconds=cap)
        far = backup.failure_backoff_seconds(20, cap_seconds=cap)
        self.assertEqual(first, backup.FAILURE_BACKOFF_SECONDS)
        self.assertGreater(second, first)
        self.assertLessEqual(far, cap)


class BackupArchiveValidationTests(unittest.TestCase):
    def test_truncated_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "broken.skbackup"
            path.write_bytes(b"PK\x03\x04 not really a zip")
            ok, message = backup.validate_backup_archive(path)
            self.assertFalse(ok)
            self.assertIn("validation failed", message.lower())

    def test_archive_without_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.skbackup"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("config.json", "{}")
            ok, message = backup.validate_backup_archive(path)
            self.assertFalse(ok)
            self.assertIn("metadata", message.lower())

    def test_archive_with_metadata_but_no_profile_data_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "meta-only.skbackup"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("_backup_meta.json", '{"version": "1.0.0"}')
            ok, message = backup.validate_backup_archive(path)
            self.assertFalse(ok)
            self.assertIn("no profile data", message.lower())


class AutoBackupRotationTests(unittest.TestCase):
    def _profile(self, tmpdir):
        config_dir = Path(tmpdir) / "profile"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.json").write_text("{}", encoding="utf-8")
        return config_dir

    def test_backup_is_written_atomically_and_validated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = self._profile(tmpdir)
            dest = Path(tmpdir) / "backups"
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, message, path = backup.auto_backup(
                    str(dest), keep_last=3, timestamp="20260801_120000",
                )
            self.assertTrue(ok, message)
            self.assertTrue(Path(path).is_file())
            # The staging file must not survive a successful run.
            self.assertFalse(list(dest.glob("*.part")))
            valid, _ = backup.validate_backup_archive(path)
            self.assertTrue(valid)

    def test_failed_write_leaves_no_partial_and_preserves_older_backups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = self._profile(tmpdir)
            dest = Path(tmpdir) / "backups"
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                ok, _, keeper = backup.auto_backup(
                    str(dest), keep_last=3, timestamp="20260801_120000",
                )
                self.assertTrue(ok)
                with mock.patch.object(
                    backup, "create_backup",
                    return_value=(False, "Backup failed: destination is gone"),
                ):
                    ok, message, path = backup.auto_backup(
                        str(dest), keep_last=3, timestamp="20260801_130000",
                    )
            self.assertFalse(ok)
            self.assertEqual(path, "")
            self.assertIn("destination is gone", message)
            self.assertTrue(Path(keeper).is_file())
            self.assertFalse(list(dest.glob("*.part")))
            self.assertEqual(len(list(dest.glob("*.skbackup"))), 1)

    def test_corrupt_archive_never_takes_a_rotation_slot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = self._profile(tmpdir)
            dest = Path(tmpdir) / "backups"
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                with mock.patch.object(
                    backup, "validate_backup_archive",
                    return_value=(False, "Backup is corrupt: config.json"),
                ):
                    ok, message, path = backup.auto_backup(
                        str(dest), keep_last=3, timestamp="20260801_120000",
                    )
            self.assertFalse(ok)
            self.assertIn("corrupt", message)
            self.assertEqual(path, "")
            self.assertEqual(list(dest.glob("*.skbackup")), [])
            self.assertFalse(list(dest.glob("*.part")))

    def test_rotation_keeps_only_the_newest_archives(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = self._profile(tmpdir)
            dest = Path(tmpdir) / "backups"
            with mock.patch.object(backup, "CONFIG_DIR", config_dir):
                for hour in range(10, 15):
                    ok, message, _ = backup.auto_backup(
                        str(dest), keep_last=2,
                        timestamp=f"20260801_{hour:02d}0000",
                    )
                    self.assertTrue(ok, message)
            names = sorted(p.name for p in dest.glob("*.skbackup"))
            self.assertEqual(names, [
                "streamkeep_20260801_130000.skbackup",
                "streamkeep_20260801_140000.skbackup",
            ])


class BackupScheduleStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "library.db"
        patcher = mock.patch.object(db, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()

    def test_first_contact_schedules_an_immediate_run(self):
        now = 1_700_000_000.0
        claim = db.claim_due_backup("owner-a", cadence_seconds=3600, now=now)
        self.assertIsNotNone(claim)
        self.assertEqual(claim["running_owner"], "owner-a")

    def test_second_owner_cannot_claim_a_live_run(self):
        now = 1_700_000_000.0
        self.assertIsNotNone(
            db.claim_due_backup("owner-a", cadence_seconds=3600, now=now)
        )
        self.assertIsNone(
            db.claim_due_backup("owner-b", cadence_seconds=3600, now=now + 5)
        )

    def test_abandoned_claim_is_taken_over_after_the_stale_window(self):
        now = 1_700_000_000.0
        db.claim_due_backup("owner-a", cadence_seconds=3600, now=now)
        later = now + db.BACKUP_CLAIM_STALE_SECONDS + 1
        claim = db.claim_due_backup("owner-b", cadence_seconds=3600, now=later)
        self.assertIsNotNone(claim)
        self.assertEqual(claim["running_owner"], "owner-b")

    def test_success_defers_the_next_run_by_one_cadence_across_restart(self):
        now = 1_700_000_000.0
        db.claim_due_backup("owner-a", cadence_seconds=3600, now=now)
        db.finish_backup_run(
            "owner-a", ok=True, now=now + 10, cadence_seconds=3600,
            path=r"C:\backups\streamkeep_x.skbackup", size=4096,
        )
        # A process restart re-reads the durable schedule, not memory.
        self.assertIsNone(
            db.claim_due_backup("owner-b", cadence_seconds=3600, now=now + 20)
        )
        state = db.load_backup_state()
        self.assertEqual(state["last_size"], 4096)
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertEqual(state["running_owner"], "")
        due = db.claim_due_backup(
            "owner-b", cadence_seconds=3600, now=now + 3611,
        )
        self.assertIsNotNone(due)

    def test_failure_backs_off_instead_of_retrying_every_tick(self):
        now = 1_700_000_000.0
        db.claim_due_backup("owner-a", cadence_seconds=86_400, now=now)
        db.finish_backup_run(
            "owner-a", ok=False, now=now + 1, cadence_seconds=86_400,
            error="Backup destination is unavailable",
            failure_backoff_seconds=backup.FAILURE_BACKOFF_SECONDS,
        )
        state = db.load_backup_state()
        self.assertEqual(state["consecutive_failures"], 1)
        self.assertIn("unavailable", state["last_error"])
        self.assertIsNone(
            db.claim_due_backup("owner-a", cadence_seconds=86_400, now=now + 60)
        )
        self.assertIsNotNone(
            db.claim_due_backup(
                "owner-a", cadence_seconds=86_400,
                now=now + backup.FAILURE_BACKOFF_SECONDS + 2,
            )
        )

    def test_a_cadence_change_reanchors_from_the_last_success(self):
        now = 1_700_000_000.0
        db.claim_due_backup("owner-a", cadence_seconds=86_400, now=now)
        db.finish_backup_run(
            "owner-a", ok=True, now=now, cadence_seconds=86_400, size=10,
        )
        # Shortening daily to hourly must make the run due one hour after the
        # last success, not one full day.
        self.assertIsNone(
            db.claim_due_backup("owner-a", cadence_seconds=3600, now=now + 60)
        )
        self.assertIsNotNone(
            db.claim_due_backup("owner-a", cadence_seconds=3600, now=now + 3601)
        )

    def test_releasing_a_claim_does_not_record_an_attempt(self):
        now = 1_700_000_000.0
        db.claim_due_backup("owner-a", cadence_seconds=3600, now=now)
        self.assertTrue(db.release_backup_claim("owner-a"))
        state = db.load_backup_state()
        self.assertEqual(state["running_owner"], "")
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertEqual(state["last_success_at"], "")
        # The run is still due, so a later owner picks it up.
        self.assertIsNotNone(
            db.claim_due_backup("owner-b", cadence_seconds=3600, now=now + 1)
        )

    def test_finish_from_a_foreign_owner_is_ignored(self):
        now = 1_700_000_000.0
        db.claim_due_backup("owner-a", cadence_seconds=3600, now=now)
        db.finish_backup_run(
            "owner-b", ok=True, now=now + 5, cadence_seconds=3600, size=99,
        )
        state = db.load_backup_state()
        self.assertEqual(state["running_owner"], "owner-a")
        self.assertEqual(state["last_success_at"], "")

    def test_request_backup_now_only_moves_the_due_time(self):
        now = 1_700_000_000.0
        db.claim_due_backup("owner-a", cadence_seconds=86_400, now=now)
        db.finish_backup_run(
            "owner-a", ok=True, now=now, cadence_seconds=86_400, size=7,
        )
        self.assertTrue(db.request_backup_now(cadence_seconds=86_400))
        state = db.load_backup_state()
        self.assertEqual(state["last_size"], 7)
        self.assertTrue(state["last_success_at"])
        self.assertIsNotNone(
            db.claim_due_backup("owner-a", cadence_seconds=86_400, now=now + 1)
        )

    def test_public_view_reports_status_without_leaking_host_paths(self):
        now = 1_700_000_000.0
        db.claim_due_backup("owner-a", cadence_seconds=3600, now=now)
        db.finish_backup_run(
            "owner-a", ok=True, now=now, cadence_seconds=3600,
            path=os.path.join("C:", "Users", "matt", "sk_backup.skbackup"),
            size=2048,
        )
        view = db.backup_state_public_view(db.load_backup_state())
        self.assertEqual(view["last_name"], "sk_backup.skbackup")
        self.assertEqual(view["last_size"], 2048)
        self.assertFalse(view["running"])
        self.assertTrue(view["next_run_at"])
        self.assertEqual(view["last_error"], "")
        self.assertNotIn("Users", str(view))

    def test_public_view_scrubs_a_failure_reason(self):
        now = 1_700_000_000.0
        db.claim_due_backup("owner-a", cadence_seconds=3600, now=now)
        db.finish_backup_run(
            "owner-a", ok=False, now=now, cadence_seconds=3600,
            error="Backup failed: upload to https://user:pw@example.com/x denied",
            failure_backoff_seconds=900,
        )
        view = db.backup_state_public_view(db.load_backup_state())
        self.assertNotIn("example.com", view["last_error"])
        self.assertNotIn("user:pw", view["last_error"])
        self.assertIn("removed", view["last_error"].lower())
        self.assertEqual(view["consecutive_failures"], 1)


class ScheduledBackupRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.config_dir = self.root / "profile"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "config.json").write_text("{}", encoding="utf-8")
        self.dest = self.root / "backups"
        patcher = mock.patch.object(db, "DB_PATH", self.root / "library.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        config_patcher = mock.patch.object(backup, "CONFIG_DIR", self.config_dir)
        config_patcher.start()
        self.addCleanup(config_patcher.stop)
        db.init_db()

    def _config(self, **overrides):
        config = {
            "auto_backup_enabled": True,
            "auto_backup_dir": str(self.dest),
            "auto_backup_cadence": "hourly",
            "auto_backup_keep_last": 2,
        }
        config.update(overrides)
        return config

    def test_disabled_schedule_never_writes_and_frees_any_claim(self):
        now = 1_700_000_000.0
        db.claim_due_backup("owner-a", cadence_seconds=3600, now=now)
        state = backup.run_scheduled_backup(
            self._config(auto_backup_enabled=False), "owner-a", now=now,
        )
        self.assertIsNone(state)
        self.assertEqual(db.load_backup_state()["running_owner"], "")
        self.assertFalse(self.dest.exists())

    def test_due_run_writes_records_and_defers_the_next_run(self):
        now = 1_700_000_000.0
        state = backup.run_scheduled_backup(
            self._config(), "owner-a", now=now,
        )
        self.assertIsNotNone(state)
        self.assertEqual(state["last_error"], "")
        self.assertGreater(state["last_size"], 0)
        self.assertEqual(len(list(self.dest.glob("*.skbackup"))), 1)
        # Immediately ticking again must not produce a second archive.
        self.assertIsNone(
            backup.run_scheduled_backup(self._config(), "owner-a", now=now + 1)
        )
        self.assertEqual(len(list(self.dest.glob("*.skbackup"))), 1)

    def test_failed_run_is_recorded_and_backed_off(self):
        now = 1_700_000_000.0
        with mock.patch.object(
            backup, "auto_backup",
            return_value=(False, "Backup destination is unavailable", ""),
        ):
            state = backup.run_scheduled_backup(
                self._config(), "owner-a", now=now,
            )
        self.assertIsNotNone(state)
        self.assertIn("unavailable", state["last_error"])
        self.assertEqual(state["consecutive_failures"], 1)
        self.assertEqual(state["last_success_at"], "")
        self.assertIsNone(
            backup.run_scheduled_backup(self._config(), "owner-a", now=now + 60)
        )

    def test_a_scheduled_archive_restores_cleanly(self):
        now = 1_700_000_000.0
        state = backup.run_scheduled_backup(self._config(), "owner-a", now=now)
        archive = next(self.dest.glob("*.skbackup"))
        self.assertEqual(
            os.path.basename(state["last_path"]), archive.name,
        )
        ok, message = backup.restore_backup(str(archive))
        self.assertTrue(ok, message)
