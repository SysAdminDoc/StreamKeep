import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from streamkeep.diagnostics import (
    create_diagnostic_snapshot,
    redact_config,
    redact_text,
)


class RedactionTests(unittest.TestCase):
    def test_redacts_bearer_tokens(self):
        text = 'Authorization: Bearer abc123secret'
        result = redact_text(text)
        self.assertNotIn("abc123secret", result)
        self.assertIn("***REDACTED***", result)

    def test_redacts_api_keys(self):
        text = 'api_key: "sk-12345"'
        result = redact_text(text)
        self.assertNotIn("sk-12345", result)

    def test_redacts_dpapi_payloads(self):
        text = 'value = dpapi:AQAAANCMnd8BFdERjHoAwE'
        result = redact_text(text)
        self.assertNotIn("AQAAANCMnd8BFdERjHoAwE", result)

    def test_redacts_cookies(self):
        text = 'cookie: "session=abc123"'
        result = redact_text(text)
        self.assertNotIn("session=abc123", result)

    def test_redacts_passwords(self):
        text = 'password = "hunter2"'
        result = redact_text(text)
        self.assertNotIn("hunter2", result)

    def test_config_redacts_sensitive_keys(self):
        cfg = {
            "output_dir": "C:\\Videos",
            "webhook_url": "https://hooks.slack.com/secret",
            "proxy": "socks5://user:pass@1.2.3.4:1080",
            "companion_token": "abc123hex",
            "theme": "dark",
        }
        redacted = redact_config(cfg)
        self.assertEqual(redacted["output_dir"], "C:\\Videos")
        self.assertEqual(redacted["theme"], "dark")
        self.assertEqual(redacted["webhook_url"], "***REDACTED***")
        self.assertEqual(redacted["proxy"], "***REDACTED***")
        self.assertEqual(redacted["companion_token"], "***REDACTED***")

    def test_config_redacts_empty_sensitive_keys_as_empty(self):
        cfg = {"webhook_url": "", "proxy": ""}
        redacted = redact_config(cfg)
        self.assertEqual(redacted["webhook_url"], "")
        self.assertEqual(redacted["proxy"], "")


class SnapshotTests(unittest.TestCase):
    def test_creates_valid_zip_with_runtime_info(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "diag.zip"
            ok, msg = create_diagnostic_snapshot(str(out))
            self.assertTrue(ok)
            self.assertTrue(out.is_file())
            with zipfile.ZipFile(out, "r") as zf:
                names = zf.namelist()
                self.assertIn("runtime.json", names)
                self.assertIn("config_redacted.json", names)
                self.assertIn("db_diagnostics.json", names)
                self.assertIn("_snapshot_meta.json", names)
                runtime = json.loads(zf.read("runtime.json"))
                self.assertIn("streamkeep_version", runtime)

    def test_config_in_snapshot_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "diag.zip"
            ok, _ = create_diagnostic_snapshot(str(out))
            self.assertTrue(ok)
            with zipfile.ZipFile(out, "r") as zf:
                cfg = json.loads(zf.read("config_redacted.json"))
                for key in ("webhook_url", "proxy", "companion_token"):
                    if key in cfg and cfg[key]:
                        self.assertEqual(cfg[key], "***REDACTED***")


if __name__ == "__main__":
    unittest.main()
