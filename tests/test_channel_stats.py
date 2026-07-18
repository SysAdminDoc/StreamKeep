import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


class ChannelStatsTests(unittest.TestCase):
    def _make_stats_module(self, tmpdir):
        """Import channel_stats with DB redirected to a temp directory."""
        from streamkeep import channel_stats
        channel_stats.DB_PATH = Path(tmpdir) / "library.db"
        channel_stats.CONFIG_DIR = Path(tmpdir)
        return channel_stats

    def test_log_and_retrieve(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cs = self._make_stats_module(tmpdir)
            cs.log_transition("xqc", "twitch", "live", viewers=10000, title="Just Chatting", game="Just Chatting")
            cs.log_transition("xqc", "twitch", "offline")

            stats = cs.get_channel_stats("xqc", weeks=1)
            self.assertEqual(stats["streams_total"], 1)
            self.assertIn("Just Chatting", [g[0] for g in stats["top_games"]])

    def test_empty_channel_returns_zero_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cs = self._make_stats_module(tmpdir)
            stats = cs.get_channel_stats("nobody", weeks=4)
            self.assertEqual(stats["streams_total"], 0)
            self.assertEqual(stats["streams_per_week"], 0)
            self.assertEqual(stats["top_games"], [])

    def test_get_all_channel_summaries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cs = self._make_stats_module(tmpdir)
            cs.log_transition("ch1", "twitch", "live")
            cs.log_transition("ch1", "twitch", "offline")
            cs.log_transition("ch2", "kick", "live")
            cs.log_transition("ch2", "kick", "offline")

            summaries = cs.get_all_channel_summaries(weeks=1)
            self.assertIn("ch1", summaries)
            self.assertIn("ch2", summaries)

    def test_connection_closed_on_exception(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cs = self._make_stats_module(tmpdir)
            cs._ensure_table()
            cs.log_transition("x", "t", "live")
            stats = cs.get_channel_stats("x", weeks=1)
            self.assertIsInstance(stats, dict)


if __name__ == "__main__":
    unittest.main()
