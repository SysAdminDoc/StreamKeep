import tempfile
import unittest
from unittest import mock
from pathlib import Path


class BandwidthTrackerTests(unittest.TestCase):
    def test_lazy_tracker_initializes_on_access(self):
        from streamkeep.bandwidth import _LazyTracker, BandwidthTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "library.db")
            with mock.patch("streamkeep.bandwidth.DB_PATH", db_path):
                with mock.patch("streamkeep.bandwidth.CONFIG_DIR", Path(tmpdir)):
                    t = BandwidthTracker()
                    self.assertEqual(t.today_bytes, 0)

    def test_add_bytes_increments(self):
        from streamkeep.bandwidth import BandwidthTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("streamkeep.bandwidth.DB_PATH", str(Path(tmpdir) / "library.db")):
                with mock.patch("streamkeep.bandwidth.CONFIG_DIR", Path(tmpdir)):
                    t = BandwidthTracker()
                    t.add_bytes(100)
                    t.add_bytes(200)
                    self.assertEqual(t.today_bytes, 300)

    def test_format_sizes(self):
        from streamkeep.bandwidth import _fmt

        self.assertEqual(_fmt(500), "500 B")
        self.assertEqual(_fmt(1024), "1 KB")
        self.assertIn("MB", _fmt(5 * 1024 * 1024))
        self.assertIn("GB", _fmt(2 * 1024 ** 3))

    def test_daily_cap_exceeded(self):
        from streamkeep.bandwidth import BandwidthTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("streamkeep.bandwidth.DB_PATH", str(Path(tmpdir) / "library.db")):
                with mock.patch("streamkeep.bandwidth.CONFIG_DIR", Path(tmpdir)):
                    t = BandwidthTracker()
                    t.configure(daily_cap_gb=0.001)
                    t.add_bytes(2 * 1024 * 1024)
                    self.assertTrue(t.daily_cap_exceeded)

    def test_no_cap_not_exceeded(self):
        from streamkeep.bandwidth import BandwidthTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("streamkeep.bandwidth.DB_PATH", str(Path(tmpdir) / "library.db")):
                with mock.patch("streamkeep.bandwidth.CONFIG_DIR", Path(tmpdir)):
                    t = BandwidthTracker()
                    t.add_bytes(999999)
                    self.assertFalse(t.daily_cap_exceeded)
                    self.assertFalse(t.monthly_cap_exceeded)


if __name__ == "__main__":
    unittest.main()
