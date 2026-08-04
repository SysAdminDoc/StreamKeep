import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from streamkeep import db
from streamkeep.retry import (
    classify_failure,
    failure_remediation,
    iso_timestamp,
    parse_retry_after,
    retry_delay_seconds,
    retry_source,
    sanitize_failure_reason,
)


class RetryPolicyTests(unittest.TestCase):
    def test_failure_remediation_is_bounded_and_category_specific(self):
        categories = {
            "disk", "permission", "drm", "authentication", "missing_media",
            "invalid_config", "rate_limit", "server", "timeout", "network",
            "unknown",
        }
        for category in categories:
            with self.subTest(category=category):
                remediation = failure_remediation(category)
                self.assertTrue(remediation["message"])
                self.assertNotIn("://", remediation["message"])
                self.assertNotIn("\\", remediation["message"])
                self.assertNotIn("/", remediation["message"])
        unknown = failure_remediation("future-category")
        self.assertIn("No safe remediation", unknown["message"])

    def test_youtube_capability_failure_opens_health_guidance(self):
        remediation = failure_remediation(
            "invalid_config", reason="yt-dlp JavaScript runtime is unavailable"
        )
        self.assertEqual(remediation["target"], "settings.youtube")
        self.assertIn("YouTube", remediation["message"])
        self.assertTrue(remediation["action"])

    def test_failure_categories_choose_retry_or_intervention(self):
        cases = {
            "network": ("connection reset by peer", True),
            "timeout": ("operation timed out", True),
            "rate_limit": ("HTTP Error 429: Too Many Requests", True),
            "server": ("HTTP Error 503: Service Unavailable", True),
            "authentication": ("HTTP Error 401: login required", False),
            "drm": ("Widevine DRM content protection detected", False),
            "missing_media": ("HTTP Error 410: video unavailable", False),
            "invalid_config": ("requested format is not available", False),
            "permission": ("Permission denied while opening output", False),
            "disk": ("No space left on device", False),
        }
        for category, (message, retryable) in cases.items():
            with self.subTest(category=category):
                decision = classify_failure(message, now=1_700_000_000)
                self.assertEqual(decision.category, category)
                self.assertEqual(decision.retryable, retryable)

    def test_unknown_failure_stops_for_intervention(self):
        decision = classify_failure("fixture-specific decoder failure")
        self.assertEqual(decision.category, "unknown")
        self.assertFalse(decision.retryable)

    def test_reason_removes_urls_headers_and_credentials(self):
        reason = sanitize_failure_reason(
            "GET https://user:pass@example.com/watch?v=secret "
            "Authorization: Bearer token-value api_key=hunter2"
        )
        self.assertNotIn("example.com", reason)
        self.assertNotIn("secret", reason)
        self.assertNotIn("hunter2", reason)
        self.assertNotIn("token-value", reason)
        self.assertIn("URL removed]", reason)

    def test_retry_after_supports_seconds_and_http_date(self):
        now = datetime(2026, 7, 29, tzinfo=timezone.utc).timestamp()
        self.assertEqual(parse_retry_after("Retry-After: 600", now=now), 600)
        self.assertEqual(
            parse_retry_after(
                "Retry-After: Wed, 29 Jul 2026 00:10:00 GMT",
                now=now,
            ),
            600,
        )

    def test_backoff_is_deterministic_exponential_and_honors_retry_after(self):
        first = retry_delay_seconds(1, "source-a")
        again = retry_delay_seconds(1, "source-a")
        second = retry_delay_seconds(2, "source-a")
        server_floor = retry_delay_seconds(
            1, "source-a", retry_after_seconds=86_400
        )
        self.assertEqual(first, again)
        self.assertGreater(second, first)
        self.assertEqual(server_floor, 86_400)

    def test_circuit_identity_is_site_scoped_and_opaque(self):
        first_key, first_label = retry_source(
            "https://media.example/a", "Example", "video:a"
        )
        second_key, _ = retry_source(
            "https://media.example/b", "Example", "video:b"
        )
        other_key, _ = retry_source(
            "https://other.example/a", "Example", "video:a"
        )
        self.assertEqual(first_key, second_key)
        self.assertNotEqual(first_key, other_key)
        self.assertEqual(first_label, "Example")
        self.assertNotIn("media.example", first_key)


class PersistentRetrySchedulerTests(unittest.TestCase):
    def _db_path(self, root):
        return Path(root) / "library.db"

    def test_v9_migration_classifies_and_schedules_existing_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = self._db_path(tmpdir)
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE failed_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    output_dir TEXT NOT NULL DEFAULT '',
                    resume_sidecar TEXT NOT NULL DEFAULT '',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'retryable',
                    queue_data TEXT NOT NULL DEFAULT '{}',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    last_retry_at TEXT NOT NULL DEFAULT ''
                );
                INSERT INTO failed_jobs
                    (url, platform, stage, error, status, queue_data)
                VALUES
                    ('https://example.com/video', 'Example', 'fetch',
                     'network timeout', 'retryable', '{}');
                PRAGMA user_version = 9;
            """)
            conn.commit()
            conn.close()

            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                failure = db.load_failed_job(1)
                conn = sqlite3.connect(db_path)
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                conn.close()

            self.assertEqual(version, db.SCHEMA_VERSION)
            self.assertEqual(failure["category"], "timeout")
            self.assertTrue(failure["retryable"])
            self.assertTrue(failure["auto_retry"])
            self.assertTrue(failure["next_attempt_at"])

    def test_transient_failure_persists_redacted_retry_after_schedule(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(db, "DB_PATH", self._db_path(tmpdir)):
                db.init_db()
                failure_id = db.save_failed_job(
                    url="https://example.com/video?session=private",
                    platform="Example",
                    title="Video",
                    stage="fetch",
                    error=(
                        "HTTP Error 429 for "
                        "https://example.com/video?token=private\n"
                        "Retry-After: 600\nAuthorization: Bearer super-secret"
                    ),
                    queue_data={"url": "https://example.com/video"},
                    now=now,
                )
                failure = db.load_failed_job(failure_id)

            self.assertEqual(failure["category"], "rate_limit")
            self.assertEqual(failure["retry_after_seconds"], 600)
            self.assertGreaterEqual(
                iso_timestamp(failure["next_attempt_at"]), now + 600
            )
            self.assertNotIn("example.com", failure["last_reason"])
            self.assertNotIn("super-secret", failure["last_reason"])

    def test_due_retry_survives_restart_and_promotes_once_under_lease(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(db, "DB_PATH", self._db_path(tmpdir)):
                db.init_db()
                failure_id = db.save_failed_job(
                    url="https://example.com/video",
                    platform="Example",
                    title="Video",
                    stage="download",
                    error="connection reset by peer",
                    queue_data={"url": "https://example.com/video"},
                    now=now,
                )
                due = iso_timestamp(
                    db.load_failed_job(failure_id)["next_attempt_at"]
                )
                db.init_db()
                refused = db.promote_due_failed_jobs(
                    "not-owner", now=due
                )
                db.acquire_executor_lease(
                    "owner", owner_kind="test", now=due - 1,
                    lease_seconds=120,
                )
                promoted = db.promote_due_failed_jobs("owner", now=due)
                duplicate = db.promote_due_failed_jobs("owner", now=due)
                failure = db.load_failed_job(failure_id)
                queue = db.load_queue()

            self.assertEqual(refused, [])
            self.assertEqual(len(promoted), 1)
            self.assertEqual(duplicate, [])
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0]["status"], "queued")
            self.assertEqual(queue[0]["failure_id"], failure_id)
            self.assertEqual(failure["status"], "retrying")
            self.assertEqual(failure["retry_count"], 1)

    def test_non_retryable_failure_never_becomes_due(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(db, "DB_PATH", self._db_path(tmpdir)):
                db.init_db()
                failure_id = db.save_failed_job(
                    url="https://example.com/private",
                    stage="fetch",
                    error="HTTP Error 403: login required",
                    now=1_700_000_000,
                )
                failure = db.load_failed_job(failure_id)
                due = db.load_due_failed_jobs(now=1_800_000_000)

            self.assertEqual(failure["category"], "authentication")
            self.assertEqual(failure["status"], "intervention")
            self.assertFalse(failure["auto_retry"])
            self.assertEqual(failure["next_attempt_at"], "")
            self.assertEqual(due, [])

    def test_three_site_failures_open_circuit_and_defer_earlier_jobs(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(db, "DB_PATH", self._db_path(tmpdir)):
                db.init_db()
                ids = []
                for index in range(3):
                    ids.append(db.save_failed_job(
                        url=f"https://media.example/video/{index}",
                        platform="Example",
                        stage="fetch",
                        error="temporary network failure",
                        queue_data={"source_id": f"video:{index}"},
                        now=now + index * 10,
                    ))
                third = db.load_failed_job(ids[-1])
                circuits = db.load_retry_circuits()
                db.acquire_executor_lease(
                    "owner", owner_kind="test", now=now + 99,
                    lease_seconds=120,
                )
                promoted = db.promote_due_failed_jobs(
                    "owner", now=now + 100
                )
                first = db.load_failed_job(ids[0])
                db.mark_failed_job_resolved(ids[0])
                circuits_after_success = db.load_retry_circuits()

            self.assertEqual(len(circuits), 1)
            self.assertEqual(circuits[0]["failure_count"], 3)
            self.assertGreaterEqual(
                iso_timestamp(third["next_attempt_at"]), now + 920
            )
            self.assertEqual(promoted, [])
            self.assertGreater(
                iso_timestamp(first["next_attempt_at"]), now + 100
            )
            self.assertEqual(circuits_after_success, [])

    def test_cancel_stops_scheduled_or_promoted_retry_but_keeps_manual_retry(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(db, "DB_PATH", self._db_path(tmpdir)):
                db.init_db()
                failure_id = db.save_failed_job(
                    url="https://example.com/video",
                    stage="fetch",
                    error="network is unreachable",
                    now=now,
                )
                due = iso_timestamp(
                    db.load_failed_job(failure_id)["next_attempt_at"]
                )
                db.acquire_executor_lease(
                    "owner", owner_kind="test", now=due - 1,
                    lease_seconds=120,
                )
                promoted = db.promote_due_failed_jobs("owner", now=due)[0]
                cancelled = db.cancel_failed_job_retry(failure_id)
                cancelled_job = db.load_queue_job(promoted["job_id"])
                failure = db.load_failed_job(failure_id)
                manual = db.promote_failed_job_retry(failure_id, now=due + 1)

            self.assertTrue(cancelled)
            self.assertEqual(cancelled_job["status"], "cancelled")
            self.assertEqual(failure["status"], "intervention")
            self.assertFalse(failure["auto_retry"])
            self.assertEqual(manual["status"], "queued")

    def test_public_failure_projection_omits_recovery_secrets_and_urls(self):
        row = {
            "id": 9,
            "title": "https://example.com/watch?token=private",
            "platform": "Example",
            "url": "https://example.com/watch?token=private",
            "stage": "fetch",
            "last_reason": (
                "failed https://example.com/watch?token=private "
                "Authorization: Bearer secret"
            ),
            "queue_data": {"headers": {"Authorization": "Bearer secret"}},
            "context_json": {"cookie": "secret"},
            "retryable": 1,
            "auto_retry": 1,
        }
        public = db.failed_job_public_view(row)
        encoded = json.dumps(public)
        self.assertNotIn("url", public)
        self.assertNotIn("queue_data", public)
        self.assertNotIn("context_json", public)
        self.assertNotIn("example.com", encoded)
        self.assertNotIn("secret", encoded)


if __name__ == "__main__":
    unittest.main()
