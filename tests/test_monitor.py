"""Dedicated coverage for monitor schedule-window and in-flight dedup logic.

These exercise the pure decision functions and the ChannelMonitor round-robin
tick without any live network — extractor detection and the thread pool are
substituted so the dispatch guards can be asserted deterministically.
"""

import datetime
import tempfile
import unittest
from unittest import mock

from streamkeep import db, monitor as monitor_mod
from streamkeep.models import MonitorEntry, VODInfo
from streamkeep.monitor import ChannelMonitor, entry_in_schedule_window


def _at(year=2026, month=7, day=20, hour=12, minute=0):
    # 2026-07-20 is a Monday (weekday 0).
    return datetime.datetime(year, month, day, hour, minute)


class ScheduleWindowTests(unittest.TestCase):
    def test_no_window_is_always_active(self):
        entry = MonitorEntry()
        self.assertTrue(entry_in_schedule_window(entry, _at(hour=3)))

    def test_daytime_window_includes_and_excludes(self):
        entry = MonitorEntry(schedule_start_hhmm="09:00", schedule_end_hhmm="17:00")
        self.assertTrue(entry_in_schedule_window(entry, _at(hour=12)))
        self.assertFalse(entry_in_schedule_window(entry, _at(hour=8)))
        self.assertFalse(entry_in_schedule_window(entry, _at(hour=17)))  # end exclusive

    def test_overnight_window_wraps_midnight(self):
        entry = MonitorEntry(schedule_start_hhmm="22:00", schedule_end_hhmm="04:00")
        self.assertTrue(entry_in_schedule_window(entry, _at(hour=23)))
        self.assertTrue(entry_in_schedule_window(entry, _at(hour=2)))
        self.assertFalse(entry_in_schedule_window(entry, _at(hour=12)))

    def test_equal_start_end_is_always_active(self):
        entry = MonitorEntry(schedule_start_hhmm="10:00", schedule_end_hhmm="10:00")
        self.assertTrue(entry_in_schedule_window(entry, _at(hour=3)))

    def test_days_mask_gates_by_weekday(self):
        # Monday only (bit 0). 2026-07-20 is Monday; 2026-07-21 is Tuesday.
        entry = MonitorEntry(
            schedule_start_hhmm="00:00", schedule_end_hhmm="23:59",
            schedule_days_mask=1,
        )
        self.assertTrue(entry_in_schedule_window(entry, _at(day=20, hour=12)))
        self.assertFalse(entry_in_schedule_window(entry, _at(day=21, hour=12)))

    def test_malformed_times_fail_open(self):
        entry = MonitorEntry(schedule_start_hhmm="notatime", schedule_end_hhmm="17:00")
        self.assertTrue(entry_in_schedule_window(entry, _at(hour=12)))


class StreamImminentTests(unittest.TestCase):
    def test_imminent_when_segment_starts_soon(self):
        soon = (datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(minutes=5)).isoformat()
        entry = MonitorEntry(channel_id="c1")
        with mock.patch.object(monitor_mod, "get_cached_schedule", create=True):
            # get_cached_schedule is imported lazily inside the function; patch
            # the schedule module symbol it pulls from instead.
            with mock.patch("streamkeep.schedule.get_cached_schedule",
                            return_value=[{"start_iso": soon}]):
                self.assertTrue(monitor_mod._stream_imminent(entry))

    def test_not_imminent_when_far_away(self):
        far = (datetime.datetime.now(datetime.timezone.utc)
               + datetime.timedelta(hours=6)).isoformat()
        entry = MonitorEntry(channel_id="c1")
        with mock.patch("streamkeep.schedule.get_cached_schedule",
                        return_value=[{"start_iso": far}]):
            self.assertFalse(monitor_mod._stream_imminent(entry))

    def test_empty_schedule_is_not_imminent(self):
        entry = MonitorEntry(channel_id="c1")
        with mock.patch("streamkeep.schedule.get_cached_schedule", return_value=[]):
            self.assertFalse(monitor_mod._stream_imminent(entry))


class _FakeExtractor:
    NAME = "fake"

    def supports_live_check(self):
        return True

    def supports_vod_listing(self):
        return False

    def extract_channel_id(self, url):
        return url


class _TwitchWithoutApiAnswer(_FakeExtractor):
    NAME = "Twitch"

    def check_live(self, _url):
        return None


class ChannelMonitorDedupTests(unittest.TestCase):
    def setUp(self):
        self.monitor = ChannelMonitor()
        self.detect_patch = mock.patch(
            "streamkeep.monitor.Extractor.detect", return_value=_FakeExtractor())
        self.detect_patch.start()

    def tearDown(self):
        self.detect_patch.stop()

    def test_add_channel_rejects_duplicates(self):
        self.assertTrue(self.monitor.add_channel("https://x/chan", interval=1))
        self.assertFalse(self.monitor.add_channel("https://x/chan", interval=1))
        self.assertEqual(len(self.monitor.entries), 1)

    def test_remove_channel_clears_in_flight(self):
        self.monitor.add_channel("https://x/chan", interval=1)
        self.monitor._in_flight.add("https://x/chan")
        self.monitor.remove_channel(0)
        self.assertNotIn("https://x/chan", self.monitor._in_flight)
        self.assertEqual(len(self.monitor.entries), 0)

    def test_poll_tick_skips_in_flight_channel(self):
        self.monitor.add_channel("https://x/chan", interval=1)
        self.monitor._in_flight.add("https://x/chan")
        with mock.patch.object(self.monitor._pool, "start") as start:
            self.monitor._poll_tick()
            start.assert_not_called()

    def test_poll_tick_respects_interval_gate(self):
        import time
        self.monitor.add_channel("https://x/chan", interval=3600)
        self.monitor.entries[0].last_check = time.time()  # just checked
        with mock.patch.object(self.monitor._pool, "start") as start:
            self.monitor._poll_tick()
            start.assert_not_called()

    def test_poll_tick_dispatches_and_marks_in_flight(self):
        self.monitor.add_channel("https://x/chan", interval=1)
        self.monitor.entries[0].last_check = 0  # never checked
        with mock.patch.object(self.monitor._pool, "start") as start:
            self.monitor._poll_tick()
            start.assert_called_once()
        self.assertIn("https://x/chan", self.monitor._in_flight)

    def test_poll_tick_skips_outside_schedule_window(self):
        self.monitor.add_channel("https://x/chan", interval=1)
        entry = self.monitor.entries[0]
        entry.last_check = 0
        with mock.patch("streamkeep.monitor.entry_in_schedule_window",
                        return_value=False), \
                mock.patch.object(self.monitor._pool, "start") as start:
            self.monitor._poll_tick()
            start.assert_not_called()

    def test_twitch_streamlink_probe_fills_an_empty_platform_answer(self):
        entry = MonitorEntry(
            url="https://twitch.tv/channel",
            platform="Twitch",
            channel_id="channel",
        )
        signals = mock.Mock()
        with mock.patch.object(
            monitor_mod.Extractor,
            "detect",
            return_value=_TwitchWithoutApiAnswer(),
        ), mock.patch(
            "streamkeep.integrations.streamlink.streamlink_live_status",
            return_value=True,
        ) as probe:
            monitor_mod._PollTask(entry, signals, False).run()
        probe.assert_called_once_with(entry.url, platform="Twitch")
        signals.went_live.emit.assert_called_once_with("channel")
        signals.finished.emit.assert_called_once_with("channel", "live")

    def test_db_roundtrip_preserves_auth_profile_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                self.assertTrue(self.monitor.add_channel("https://x/chan"))
                self.monitor.entries[0].auth_profile_id = "ap_members"
                self.monitor.save_to_db()

                restored = ChannelMonitor()
                restored.load_from_db()

            self.assertEqual(len(restored.entries), 1)
            self.assertEqual(restored.entries[0].auth_profile_id, "ap_members")

    def test_db_roundtrip_preserves_comment_capture_opt_in(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                self.assertTrue(self.monitor.add_channel("https://x/chan"))
                self.monitor.entries[0].capture_comments = True
                self.monitor.save_to_db()

                restored = ChannelMonitor()
                restored.load_from_db()

            assert restored.entries[0].capture_comments is True

    def test_new_vods_filter_user_tombstones_but_keep_lifecycle_markers_nonblocking(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/library.db"
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                db.record_tombstone(
                    platform="Twitch", source_id="vod:blocked",
                    webpage_url="https://www.twitch.tv/videos/blocked",
                    reason="user",
                )
                db.record_tombstone(
                    platform="Twitch", source_id="vod:retained",
                    webpage_url="https://www.twitch.tv/videos/retained",
                    reason="retention",
                )
                self.monitor.entries.append(MonitorEntry(
                    channel_id="channel", _cancel_requested=False,
                ))
                emitted = []
                self.monitor.new_vods_found.connect(
                    lambda _channel_id, vods: emitted.extend(vods)
                )
                self.monitor._on_new_vods("channel", [
                    VODInfo(
                        title="Blocked", source="blocked", platform="Twitch",
                        source_id="vod:blocked",
                        webpage_url="https://www.twitch.tv/videos/blocked",
                    ),
                    VODInfo(
                        title="Retention", source="retained", platform="Twitch",
                        source_id="vod:retained",
                        webpage_url="https://www.twitch.tv/videos/retained",
                    ),
                ])

            self.assertEqual([vod.title for vod in emitted], ["Retention"])
            self.assertEqual(self.monitor.entries[0].archive_ids, ["twitch::vod:retained"])


if __name__ == "__main__":
    unittest.main()
