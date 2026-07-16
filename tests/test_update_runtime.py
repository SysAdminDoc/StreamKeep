import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import update_runtime


class _ExitedProcess:
    returncode = 17

    def poll(self):
        return self.returncode


class _RunningProcess:
    returncode = None

    def poll(self):
        return None


class UpdateRuntimeTests(unittest.TestCase):
    def _seed(self, root):
        root = Path(root)
        config = root / "config"
        config.mkdir()
        (config / "config.json").write_text('{"theme":"dark"}', encoding="utf-8")
        database = sqlite3.connect(config / "library.db")
        database.execute("CREATE TABLE marker (value TEXT)")
        database.execute("INSERT INTO marker VALUES ('before')")
        database.commit()
        database.close()
        current = root / "StreamKeep.exe"
        staged = Path(f"{current}.new")
        helper = Path(f"{current}.update-helper.exe")
        current.write_bytes(b"old-binary")
        staged.write_bytes(b"new-binary")
        helper.write_bytes(b"old-binary")
        payload = {
            "manifest_sha256": "a" * 64,
            "sequence": 43108,
            "version": "4.31.8",
            "current_version": "4.31.7",
        }
        transaction = update_runtime.prepare_update_transaction(
            current_path=current,
            staged_path=staged,
            helper_path=helper,
            config_dir=config,
            release_payload=payload,
            timeout_seconds=10,
        )
        return config, current, staged, transaction

    def test_snapshot_restore_reverts_config_and_sqlite_migrations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, _current, _staged, transaction_path = self._seed(tmpdir)
            transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
            (config / "config.json").write_text('{"theme":"changed"}', encoding="utf-8")
            database = sqlite3.connect(config / "library.db")
            database.execute("UPDATE marker SET value='after'")
            database.commit()
            database.close()
            update_runtime.restore_state(
                config,
                transaction["snapshot_dir"],
                transaction["snapshot_entries"],
            )
            self.assertEqual((config / "config.json").read_text(encoding="utf-8"), '{"theme":"dark"}')
            database = sqlite3.connect(config / "library.db")
            try:
                value = database.execute("SELECT value FROM marker").fetchone()[0]
            finally:
                database.close()
            self.assertEqual(value, "before")

    def test_failed_new_startup_restores_binary_state_and_writes_notice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, current, _staged, transaction = self._seed(tmpdir)
            (config / "config.json").write_text('{"theme":"migrated"}', encoding="utf-8")
            launches = [_ExitedProcess(), _RunningProcess()]
            with mock.patch.object(update_runtime, "_wait_for_parent_exit", return_value=True), \
                 mock.patch.object(update_runtime, "_launch", side_effect=launches):
                result = update_runtime.run_update_watchdog(transaction)
            self.assertEqual(result, 5)
            self.assertEqual(current.read_bytes(), b"old-binary")
            self.assertEqual((config / "config.json").read_text(encoding="utf-8"), '{"theme":"dark"}')
            notice = json.loads((config / "update-recovery-notice.json").read_text(encoding="utf-8"))
            self.assertIn("restored v4.31.7", notice["message"])
            self.assertTrue((config / "update-recovery.log").is_file())

    def test_healthy_new_startup_commits_atomic_swap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _config, current, _staged, transaction = self._seed(tmpdir)
            with mock.patch.object(update_runtime, "_wait_for_parent_exit", return_value=True), \
                 mock.patch.object(update_runtime, "_launch", return_value=_RunningProcess()), \
                 mock.patch.object(update_runtime, "_healthy", return_value=True), \
                 mock.patch.object(update_runtime, "HEALTH_STABILITY_SECONDS", 0):
                result = update_runtime.run_update_watchdog(transaction)
            self.assertEqual(result, 0)
            self.assertEqual(current.read_bytes(), b"new-binary")
            self.assertFalse(Path(f"{current}.old").exists())

    def test_health_marker_commits_monotonic_state_last(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, _current, _staged, transaction = self._seed(tmpdir)
            transaction_data = json.loads(transaction.read_text(encoding="utf-8"))
            update_runtime.mark_transaction_healthy(transaction, "4.31.8")
            state = json.loads((config / "update-state.json").read_text(encoding="utf-8"))
            health = json.loads(Path(transaction_data["health_path"]).read_text(encoding="utf-8"))
            self.assertEqual(state["last_healthy_version"], "4.31.8")
            self.assertEqual(health["status"], "healthy")
            self.assertEqual(health["nonce"], transaction_data["nonce"])

    def test_transaction_rejects_config_root_path_substitution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _config, _current, _staged, transaction = self._seed(tmpdir)
            data = json.loads(transaction.read_text(encoding="utf-8"))
            data["config_dir"] = str(Path(tmpdir) / "substituted")
            transaction.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "recovery root"):
                update_runtime.mark_transaction_healthy(transaction, "4.31.8")


if __name__ == "__main__":
    unittest.main()
