import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import accounts, config, cookies, secrets
from streamkeep.portable_secrets import (
    create_portable_secret_backup,
    restore_portable_secret_backup,
)


class PortableSecretBackupTests(unittest.TestCase):
    def setUp(self):
        secrets._SECRET_CACHE.clear()

    def tearDown(self):
        secrets._SECRET_CACHE.clear()

    def test_round_trip_and_wrong_password_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_file = root / "config.json"
            account_db = root / "library.db"
            cookie_file = root / "cookies.txt"
            backup_file = root / "portable.sksbackup"
            store = {}

            def set_secret(key, value):
                store[key] = value
                return True

            patches = (
                mock.patch.object(config, "CONFIG_DIR", root),
                mock.patch.object(config, "CONFIG_FILE", config_file),
                mock.patch.object(accounts, "CONFIG_DIR", root),
                mock.patch.object(accounts, "DB_PATH", account_db),
                mock.patch.object(cookies, "CONFIG_DIR", root),
                mock.patch.object(cookies, "COOKIES_FILE", cookie_file),
                mock.patch.object(secrets, "_keyring_set", side_effect=set_secret),
                mock.patch.object(secrets, "_keyring_get", side_effect=lambda key: store.get(key)),
                mock.patch.object(
                    secrets, "_keyring_delete",
                    side_effect=lambda key: store.pop(key, None) is not None,
                ),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                    patches[5], patches[6], patches[7], patches[8]:
                self.assertTrue(config.save_config({
                    "theme": "dark",
                    "hf_token": "hf_portable_secret",
                    "media_server": {"token": "media_portable_secret"},
                }))
                accounts.set_credential("twitch", "twitch_portable_secret")
                ok, message = cookies.restore_cookie_text(
                    ".example.com\tTRUE\t/\tTRUE\t0\tsession\tcookie_portable_secret\n"
                )
                self.assertTrue(ok, message)
                ok, message = create_portable_secret_backup(
                    backup_file, "correct horse battery staple"
                )
                self.assertTrue(ok, message)
                raw = backup_file.read_bytes()
                self.assertNotIn(b"portable_secret", raw)

                wrong, wrong_message = restore_portable_secret_backup(
                    backup_file, "wrong password"
                )
                self.assertFalse(wrong)
                self.assertIn("Wrong password", wrong_message)

                store.clear()
                secrets._SECRET_CACHE.clear()
                cookie_file.unlink()
                ok, message = restore_portable_secret_backup(
                    backup_file, "correct horse battery staple"
                )
                self.assertTrue(ok, message)
                restored_config = config.load_config()
                self.assertEqual(restored_config["hf_token"], "hf_portable_secret")
                self.assertEqual(
                    restored_config["media_server"]["token"],
                    "media_portable_secret",
                )
                self.assertEqual(
                    accounts.get_credential("twitch"), "twitch_portable_secret"
                )
                self.assertIn("cookie_portable_secret", cookies.export_cookie_text())

                envelope = json.loads(backup_file.read_text(encoding="utf-8"))
                ciphertext = envelope["ciphertext"]
                envelope["ciphertext"] = (
                    ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
                )
                backup_file.write_text(json.dumps(envelope), encoding="utf-8")
                tampered, tampered_message = restore_portable_secret_backup(
                    backup_file, "correct horse battery staple"
                )
                self.assertFalse(tampered)
                self.assertIn("modified", tampered_message)


if __name__ == "__main__":
    unittest.main()
