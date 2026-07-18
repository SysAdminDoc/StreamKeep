import unittest
from unittest import mock

from streamkeep import scheduler


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        scheduler.configure({
            "enabled": True,
            "day_start": 8,
            "day_end": 23,
            "day_limit": "2M",
            "night_limit": "500K",
            "weekend_limit": "10M",
        }, static_limit="1M")

    def tearDown(self):
        scheduler.configure({"enabled": False}, static_limit="")

    def test_disabled_returns_static_limit(self):
        scheduler.configure({"enabled": False}, static_limit="5M")
        self.assertEqual(scheduler.get_active_limit(), "5M")

    @mock.patch("streamkeep.scheduler.datetime")
    def test_day_returns_day_limit(self, mock_dt):
        mock_dt.now.return_value = mock.Mock(hour=12, weekday=mock.Mock(return_value=2))
        self.assertEqual(scheduler.get_active_limit(), "2M")

    @mock.patch("streamkeep.scheduler.datetime")
    def test_night_returns_night_limit(self, mock_dt):
        mock_dt.now.return_value = mock.Mock(hour=2, weekday=mock.Mock(return_value=2))
        self.assertEqual(scheduler.get_active_limit(), "500K")

    @mock.patch("streamkeep.scheduler.datetime")
    def test_weekend_returns_weekend_limit(self, mock_dt):
        mock_dt.now.return_value = mock.Mock(hour=14, weekday=mock.Mock(return_value=5))
        self.assertEqual(scheduler.get_active_limit(), "10M")

    @mock.patch("streamkeep.scheduler.datetime")
    def test_weekend_no_limit_falls_through_to_day(self, mock_dt):
        scheduler.configure({
            "enabled": True,
            "day_start": 8, "day_end": 23,
            "day_limit": "2M", "night_limit": "500K", "weekend_limit": "",
        })
        mock_dt.now.return_value = mock.Mock(hour=14, weekday=mock.Mock(return_value=6))
        self.assertEqual(scheduler.get_active_limit(), "2M")

    def test_get_schedule_returns_copy(self):
        sched = scheduler.get_schedule()
        self.assertIsInstance(sched, dict)
        self.assertTrue(sched["enabled"])
        sched["enabled"] = False
        self.assertTrue(scheduler.get_schedule()["enabled"])


if __name__ == "__main__":
    unittest.main()
