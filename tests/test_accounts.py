import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest import mock

from streamkeep import accounts
from streamkeep.secrets import SecretStorageError


def _mock_crypto():
    store = {}
    return (
        mock.patch.object(
            accounts,
            "_encrypt",
            side_effect=lambda value, platform="": f"enc:{platform}:{value}",
        ),
        mock.patch.object(
            accounts,
            "_decrypt",
            side_effect=lambda value: str(value).rsplit(":", 1)[-1] if value else "",
        ),
        mock.patch.object(
            accounts,
            "set_secret_value",
            side_effect=lambda secret_id, value: (
                store.__setitem__(secret_id, value) or f"secretref:{secret_id}"
            ),
        ),
        mock.patch.object(
            accounts,
            "get_secret_value",
            side_effect=lambda secret_id: store.get(secret_id),
        ),
    )


class AccountTests(unittest.TestCase):
    def test_legacy_plaintext_credential_migrates_to_secret_reference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE accounts (platform TEXT PRIMARY KEY, "
                "credential TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '{}')"
            )
            conn.execute(
                "INSERT INTO accounts VALUES ('twitch','legacy-token','{}')"
            )
            conn.commit()
            conn.close()
            store = {}
            with (
                mock.patch.object(accounts, "DB_PATH", db_path),
                mock.patch.object(accounts, "CONFIG_DIR", Path(tmpdir)),
                mock.patch.object(
                    accounts, "set_secret_value",
                    side_effect=lambda key, value: (
                        store.__setitem__(key, value) or f"secretref:{key}"
                    ),
                ),
            ):
                value = accounts.get_credential("twitch")

            conn = sqlite3.connect(db_path)
            try:
                stored = conn.execute(
                    "SELECT credential FROM accounts WHERE platform='twitch'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(value, "legacy-token")
            self.assertEqual(stored, "secretref:account:twitch")
            self.assertEqual(store["account:twitch"], "legacy-token")

    def test_set_credential_preserves_existing_extra_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            encrypt_patch, decrypt_patch, set_patch, get_patch = _mock_crypto()
            with mock.patch.object(accounts, "DB_PATH", db_path), mock.patch.object(
                accounts, "CONFIG_DIR", Path(tmpdir)
            ), encrypt_patch, decrypt_patch, set_patch, get_patch:
                accounts.set_extra("twitch", {"region": "us"})
                accounts.set_credential("twitch", "secret-token")
                extra = accounts.get_extra("twitch")
                cred = accounts.get_credential("twitch")

            self.assertEqual(extra, {"region": "us"})
            self.assertEqual(cred, "secret-token")

    def test_set_extra_creates_row_when_platform_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            encrypt_patch, decrypt_patch, set_patch, get_patch = _mock_crypto()
            with mock.patch.object(accounts, "DB_PATH", db_path), mock.patch.object(
                accounts, "CONFIG_DIR", Path(tmpdir)
            ), encrypt_patch, decrypt_patch, set_patch, get_patch:
                accounts.set_extra("kick", {"header": "x-test"})
                extra = accounts.get_extra("kick")
                platforms = accounts.list_platforms()

            self.assertEqual(extra, {"header": "x-test"})
            self.assertIn("kick", platforms)

    def test_set_credential_raises_when_secure_storage_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            with mock.patch.object(accounts, "DB_PATH", db_path), mock.patch.object(
                accounts, "CONFIG_DIR", Path(tmpdir)
            ), mock.patch.object(
                accounts,
                "_encrypt",
                side_effect=SecretStorageError("secure store unavailable"),
            ):
                with self.assertRaises(SecretStorageError):
                    accounts.set_credential("twitch", "secret-token")
                self.assertEqual(accounts.list_platforms(), [])


if __name__ == "__main__":
    unittest.main()
