import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from streamkeep import config, secrets


class SecretsTests(unittest.TestCase):
    def setUp(self):
        secrets._SECRET_CACHE.clear()

    def tearDown(self):
        secrets._SECRET_CACHE.clear()

    @staticmethod
    def _memory_backend():
        store = {}
        return store, (
            mock.patch.object(
                secrets, "_keyring_set",
                side_effect=lambda key, value: store.__setitem__(key, value) is None,
            ),
            mock.patch.object(
                secrets, "_keyring_get", side_effect=lambda key: store.get(key),
            ),
            mock.patch.object(
                secrets, "_keyring_delete",
                side_effect=lambda key: store.pop(key, None) is not None,
            ),
        )

    def test_protect_requires_secure_backend_by_default(self):
        with mock.patch("streamkeep.secrets.sys.platform", "linux"), \
             mock.patch("streamkeep.secrets._keyring_set", return_value=False):
            with self.assertRaises(secrets.SecretStorageError):
                secrets.protect("secret", field_name="token")

    def test_insecure_fallback_must_be_explicit(self):
        with mock.patch("streamkeep.secrets.sys.platform", "linux"), \
             mock.patch("streamkeep.secrets._keyring_set", return_value=False):
            stored = secrets.protect(
                "secret",
                field_name="token",
                allow_insecure_fallback=True,
            )

        self.assertTrue(stored.startswith("b64:"))
        self.assertEqual(secrets.unprotect(stored), "secret")

    def test_nested_config_secrets_are_references_and_exports_are_empty(self):
        cfg = {
            "theme": "dark",
            "webhook_url": "https://hooks.example/secret",
            "hf_token": "hf_private",
            "companion_token": "companion-master-token-must-stay-private",
            "proxy_pool": [{"url": "socks5://user:pass@proxy"}],
            "ytdlp_arg_templates": {
                "Authenticated": [
                    "--add-header", "Authorization: Bearer template-secret",
                ],
            },
            "media_server": {
                "url": "https://media.internal",
                "token": "media-token",
                "library_id": "1",
            },
            "recent_urls": ["https://media.example/file?token=signed-value"],
        }
        store, patches = self._memory_backend()
        with patches[0], patches[1], patches[2]:
            stored, changed = secrets.prepare_config_for_storage(cfg)
            resolved = secrets.resolve_config_secrets(stored)
            exported = secrets.secret_free_config(cfg)

        serialized = json.dumps(stored)
        self.assertTrue(changed)
        self.assertNotIn("hf_private", serialized)
        self.assertNotIn("media-token", serialized)
        self.assertNotIn("companion-master-token", serialized)
        self.assertNotIn("template-secret", serialized)
        self.assertTrue(stored["hf_token"].startswith("secretref:"))
        self.assertTrue(stored["companion_token"].startswith("secretref:"))
        self.assertEqual(resolved, cfg)
        self.assertEqual(exported["hf_token"], "")
        self.assertEqual(exported["proxy_pool"], [])
        self.assertEqual(exported["media_server"]["token"], "")
        self.assertEqual(exported["companion_token"], "")
        self.assertEqual(exported["ytdlp_arg_templates"], {})
        self.assertNotIn("signed-value", exported["recent_urls"][0])
        self.assertGreaterEqual(len(store), 5)

    def test_plaintext_config_migrates_only_after_secure_storage_succeeds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_file = root / "config.json"
            backup_file = root / "config.json.bak"
            plaintext = {
                "theme": "dark",
                "webhook_url": "https://hooks.example/plaintext-secret",
            }
            config_file.write_text(json.dumps(plaintext), encoding="utf-8")
            backup_file.write_text(json.dumps(plaintext), encoding="utf-8")
            store, patches = self._memory_backend()
            with (
                mock.patch.object(config, "CONFIG_DIR", root),
                mock.patch.object(config, "CONFIG_FILE", config_file),
                patches[0], patches[1], patches[2],
            ):
                runtime = config.load_config()

            on_disk = config_file.read_text(encoding="utf-8")
            self.assertEqual(runtime["webhook_url"], plaintext["webhook_url"])
            self.assertNotIn("plaintext-secret", on_disk)
            self.assertIn("secretref:config:webhook_url", on_disk)
            self.assertFalse(backup_file.exists())
            self.assertTrue(store)

    def test_sensitive_save_fails_closed_without_secure_backend(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_file = root / "config.json"
            config_file.write_text('{"theme":"dark"}', encoding="utf-8")
            with (
                mock.patch.object(config, "CONFIG_DIR", root),
                mock.patch.object(config, "CONFIG_FILE", config_file),
                mock.patch.object(secrets.sys, "platform", "linux"),
                mock.patch.object(secrets, "_keyring_set", return_value=False),
            ):
                ok = config.save_config({
                    "theme": "light", "hf_token": "must-not-leak",
                })

            self.assertFalse(ok)
            self.assertEqual(
                json.loads(config_file.read_text(encoding="utf-8")),
                {"theme": "dark"},
            )
            self.assertNotIn("must-not-leak", config_file.read_text(encoding="utf-8"))

    def test_companion_refuses_even_existing_master_when_secure_save_fails(self):
        from streamkeep.ui.tabs.settings import SettingsTabMixin

        window = SimpleNamespace(
            _config={"companion_token": "a" * 32},
            _persist_config=mock.Mock(return_value=False),
        )
        with self.assertRaisesRegex(ValueError, "Secure credential storage"):
            SettingsTabMixin._ensure_companion_master_token(window)

        self.assertNotIn("companion_token", window._config)
        window._persist_config.assert_called_once_with()

    def test_config_backup_rotation_never_copies_legacy_plaintext(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_file = root / "config.json"
            config_file.write_text(json.dumps({
                "theme": "dark", "hf_token": "legacy-rotation-secret",
            }), encoding="utf-8")
            store, patches = self._memory_backend()
            with (
                mock.patch.object(config, "CONFIG_DIR", root),
                mock.patch.object(config, "CONFIG_FILE", config_file),
                patches[0], patches[1], patches[2],
            ):
                ok = config.save_config({
                    "theme": "light", "hf_token": "new-rotation-secret",
                })

            self.assertTrue(ok)
            primary = config_file.read_text(encoding="utf-8")
            backup_text = (root / "config.json.bak").read_text(encoding="utf-8")
            self.assertNotIn("new-rotation-secret", primary)
            self.assertNotIn("legacy-rotation-secret", backup_text)
            self.assertIn("secretref:config:hf_token", primary)
            self.assertEqual(json.loads(backup_text)["hf_token"], "")
            self.assertEqual(
                secrets.get_secret_value("config:hf_token"),
                "new-rotation-secret",
            )


if __name__ == "__main__":
    unittest.main()
