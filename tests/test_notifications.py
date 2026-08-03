import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep.notifications import (
    NotificationCenter,
    load_security_events,
    record_security_event,
)
import streamkeep.notifications as notifications_mod


class NotificationTests(unittest.TestCase):
    def test_notification_log_is_compacted_when_it_grows_too_large(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "notifications.jsonl"
            center = NotificationCenter(capacity=10)

            with mock.patch.object(notifications_mod, "NOTIF_LOG", log_path), \
                 mock.patch.object(notifications_mod, "NOTIF_LOG_MAX_BYTES", 1), \
                 mock.patch.object(notifications_mod, "NOTIF_LOG_KEEP_LINES", 2):
                center.push("first", "info")
                center.push("second", "warning")
                center.push("third", "error")
                history = center.load_history(limit=10)

            self.assertEqual([entry["text"] for entry in history], ["second", "third"])

    def test_security_events_are_structured_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            event_log = Path(tmpdir) / "security-events.jsonl"
            client_id = "client-0123456789abcdef"
            with mock.patch.object(notifications_mod, "SECURITY_EVENT_LOG", event_log):
                event = record_security_event({
                    "route": "https://127.0.0.1:9999/api/status?token=QUERYSECRET",
                    "reason": "token_invalid",
                    "client_id": client_id,
                })
                center = NotificationCenter(capacity=5)
                note = center.push_security_event(event)
                events = load_security_events()

            self.assertEqual(event["route"], "/api/status")
            self.assertEqual(event["client_id"], client_id)
            self.assertEqual(events, [event])
            self.assertEqual(note.level, "warning")
            self.assertIn("token_invalid", note.text)
            self.assertNotIn("QUERYSECRET", event_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
