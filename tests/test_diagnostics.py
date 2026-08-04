import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import streamkeep.notifications as notifications_mod
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

    def test_redacts_signed_urls_and_request_headers(self):
        text = (
            "https://cdn.example/video.mp4"
            "?X-Amz-Credential=AKIASECRET&X-Amz-Signature=URLSECRET\n"
            "Authorization: Bearer HEADERSECRET\n"
            "Cookie: session=COOKIESECRET\n"
        )
        result = redact_text(text)
        self.assertIn("https://cdn.example/video.mp4", result)
        for secret in (
            "AKIASECRET",
            "URLSECRET",
            "HEADERSECRET",
            "COOKIESECRET",
        ):
            self.assertNotIn(secret, result)
        self.assertNotIn("X-Amz-", result)
        self.assertNotIn("Authorization:", result)
        self.assertNotIn("Cookie:", result)

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

    def test_config_redacts_mixed_case_delivery_credentials(self):
        cfg = {
            "Authorization": "Bearer HEADERSECRET",
            "HTTP-Headers": {"Cookie": "session=COOKIESECRET"},
            "X-Amz-Signature": "URLSECRET",
        }
        redacted = redact_config(cfg)
        self.assertEqual(redacted["Authorization"], "***REDACTED***")
        self.assertEqual(redacted["HTTP-Headers"], "***REDACTED***")
        self.assertEqual(redacted["X-Amz-Signature"], "***REDACTED***")

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
                self.assertIn("javascript_runtime", runtime)
                self.assertIn("source", runtime["javascript_runtime"])
                self.assertIn("managed", runtime["javascript_runtime"])
                managed = runtime["javascript_runtime"]["managed_runtime"]
                for key in ("available", "path", "version", "provenance", "source"):
                    self.assertIn(key, managed)
                capabilities = runtime["runtime_capabilities"]
                self.assertEqual(
                    set(capabilities),
                    {
                        "sqlite", "yt_dlp", "yt_dlp_ejs", "javascript", "youtube",
                        "pillow", "paramiko", "python_mpv", "libmpv", "mpv", "boto3",
                        "curl", "ffmpeg", "ffprobe",
                    },
                )
                for record in capabilities.values():
                    self.assertIn("path", record)
                    self.assertIn("version", record)
                    self.assertIn("provenance", record)
                    self.assertIn("capabilities", record)

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

    def test_snapshot_scrubs_delivery_credentials_from_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "streamkeep.log"
            crash_path = Path(tmpdir) / "crash.log"
            log_path.write_text(
                "GET https://cdn.example/video.mp4"
                "?token=QUERYSECRET&sig=SIGNATURESECRET\n"
                "Authorization: Bearer HEADERSECRET\n",
                encoding="utf-8",
            )
            crash_path.write_text(
                "Cookie: session=COOKIESECRET\n", encoding="utf-8"
            )
            out = Path(tmpdir) / "diag.zip"

            with (
                mock.patch(
                    "streamkeep.diagnostics.LOG_FILE", log_path
                ),
                mock.patch(
                    "streamkeep.diagnostics.CRASH_LOG", crash_path
                ),
            ):
                ok, _ = create_diagnostic_snapshot(str(out))

            self.assertTrue(ok)
            with zipfile.ZipFile(out, "r") as archive:
                public_text = "\n".join(
                    archive.read(name).decode("utf-8", errors="ignore")
                    for name in archive.namelist()
                )
            for secret in (
                "QUERYSECRET",
                "SIGNATURESECRET",
                "HEADERSECRET",
                "COOKIESECRET",
            ):
                self.assertNotIn(secret, public_text)
            self.assertNotIn("Authorization:", public_text)
            self.assertNotIn("Cookie:", public_text)

    def test_snapshot_includes_structured_local_server_security_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            event_log = Path(tmpdir) / "security-events.jsonl"
            out = Path(tmpdir) / "diag.zip"
            with mock.patch.object(notifications_mod, "SECURITY_EVENT_LOG", event_log):
                notifications_mod.record_security_event({
                    "route": "/api/queue?token=QUERYSECRET",
                    "reason": "request_replayed",
                    "client_id": "client-0123456789abcdef",
                })
                ok, _ = create_diagnostic_snapshot(str(out))

            self.assertTrue(ok)
            with zipfile.ZipFile(out, "r") as archive:
                runtime = json.loads(archive.read("runtime.json"))
            events = runtime["local_server_security_events"]
            self.assertEqual(events[0]["route"], "/api/queue")
            self.assertEqual(events[0]["reason"], "request_replayed")
            self.assertNotIn("QUERYSECRET", json.dumps(runtime))


if __name__ == "__main__":
    unittest.main()
