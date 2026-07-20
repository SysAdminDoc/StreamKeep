import os
import tempfile
import unittest
from pathlib import Path

from streamkeep.lifecycle import (
    evaluate_cleanup,
    keep_last_map_from_monitor,
    removal_real_paths,
)
from streamkeep.models import HistoryEntry, MonitorEntry


class LifecycleTests(unittest.TestCase):
    def test_evaluate_cleanup_uses_watched_state_from_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recording_dir = Path(tmpdir) / "recording"
            recording_dir.mkdir()
            (recording_dir / "clip.mp4").write_bytes(b"x" * 32)

            history = [
                HistoryEntry(title="Primary", path=str(recording_dir), watched=False),
                HistoryEntry(title="Duplicate", path=str(recording_dir), watched=True),
            ]

            removals = evaluate_cleanup(
                history,
                {"enabled": True, "delete_watched": True, "favorites_exempt": True},
            )

            self.assertEqual(len(removals), 1)
            self.assertEqual(removals[0][1], "watched")
            self.assertEqual(removal_real_paths(removals), {os.path.realpath(str(recording_dir))})

    def test_evaluate_cleanup_honors_favorite_exemption_across_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recording_dir = Path(tmpdir) / "recording"
            recording_dir.mkdir()
            (recording_dir / "clip.mp4").write_bytes(b"x" * 32)

            history = [
                HistoryEntry(title="Primary", path=str(recording_dir), watched=True),
                HistoryEntry(title="Duplicate", path=str(recording_dir), favorite=True),
            ]

            removals = evaluate_cleanup(
                history,
                {"enabled": True, "delete_watched": True, "favorites_exempt": True},
            )

            self.assertEqual(removals, [])


    def _recording(self, tmpdir, name, size_bytes):
        rec = Path(tmpdir) / name
        rec.mkdir()
        (rec / "clip.mp4").write_bytes(b"x" * size_bytes)
        return str(rec)

    def test_size_cap_skips_pruning_when_favorites_alone_exceed_cap(self):
        # Favorites (exempt) alone exceed the cap; pruning the lone non-favorite
        # cannot get under it, so nothing should be recycled for nothing.
        gb = 1024 ** 3
        with tempfile.TemporaryDirectory() as tmpdir:
            fav = self._recording(tmpdir, "fav", 32)
            small = self._recording(tmpdir, "small", 32)
            history = [
                HistoryEntry(title="Fav", path=fav, favorite=True),
                HistoryEntry(title="Small", path=small),
            ]
            # Force the over-cap condition regardless of real sizes by patching
            # the measured size to be huge for the favorite.
            import streamkeep.lifecycle as lc
            orig = lc._dir_size_bytes
            lc._dir_size_bytes = lambda p: (10 * gb) if p == fav else 1
            try:
                removals = evaluate_cleanup(
                    history,
                    {"enabled": True, "favorites_exempt": True, "max_total_gb": 1},
                )
            finally:
                lc._dir_size_bytes = orig
            self.assertEqual(removals, [])

    def test_size_cap_prunes_when_reclaimable(self):
        gb = 1024 ** 3
        with tempfile.TemporaryDirectory() as tmpdir:
            old = self._recording(tmpdir, "old", 32)
            new = self._recording(tmpdir, "new", 32)
            history = [
                HistoryEntry(title="Old", path=old),
                HistoryEntry(title="New", path=new),
            ]
            import streamkeep.lifecycle as lc
            orig = lc._dir_size_bytes
            # Two 2 GB recordings, cap 3 GB -> excess 1 GB -> prune one.
            lc._dir_size_bytes = lambda p: 2 * gb
            # Make "old" older so it is pruned first.
            os.utime(old, (1, 1))
            try:
                removals = evaluate_cleanup(
                    history,
                    {"enabled": True, "favorites_exempt": True, "max_total_gb": 3},
                )
            finally:
                lc._dir_size_bytes = orig
            self.assertEqual(len(removals), 1)
            self.assertEqual(os.path.realpath(removals[0][0].path), os.path.realpath(old))


    def _dated_recording(self, tmpdir, name, channel, mtime):
        rec = Path(tmpdir) / name
        rec.mkdir()
        (rec / "clip.mp4").write_bytes(b"x" * 32)
        os.utime(rec, (mtime, mtime))
        return HistoryEntry(title=name, channel=channel, path=str(rec))

    def test_keep_last_per_source_prunes_oldest_beyond_n(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            a = self._dated_recording(tmpdir, "a", "ChanX", mtime=100)
            b = self._dated_recording(tmpdir, "b", "ChanX", mtime=200)
            c = self._dated_recording(tmpdir, "c", "ChanX", mtime=300)
            removals = evaluate_cleanup(
                [a, b, c],
                {"enabled": True, "keep_last_per_source": 2},
            )
            # Keeps the two newest (b, c); recycles the oldest (a).
            self.assertEqual(len(removals), 1)
            self.assertEqual(os.path.realpath(removals[0][0].path),
                             os.path.realpath(a.path))
            self.assertIn("keeping last 2", removals[0][1])

    def test_keep_last_ignores_entries_without_channel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            a = self._dated_recording(tmpdir, "a", "", mtime=100)
            b = self._dated_recording(tmpdir, "b", "", mtime=200)
            removals = evaluate_cleanup(
                [a, b], {"enabled": True, "keep_last_per_source": 1})
            self.assertEqual(removals, [])

    def test_keep_last_is_per_channel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            x1 = self._dated_recording(tmpdir, "x1", "ChanX", mtime=100)
            x2 = self._dated_recording(tmpdir, "x2", "ChanX", mtime=200)
            y1 = self._dated_recording(tmpdir, "y1", "ChanY", mtime=100)
            removals = evaluate_cleanup(
                [x1, x2, y1], {"enabled": True, "keep_last_per_source": 1})
            # ChanX prunes its oldest (x1); ChanY has only one, keeps it.
            paths = {os.path.realpath(h.path) for h, _r in removals}
            self.assertEqual(paths, {os.path.realpath(x1.path)})

    def test_per_channel_map_overrides_policy_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            a = self._dated_recording(tmpdir, "a", "ChanX", mtime=100)
            b = self._dated_recording(tmpdir, "b", "ChanX", mtime=200)
            c = self._dated_recording(tmpdir, "c", "ChanX", mtime=300)
            # Policy default would keep 1, but the channel map says keep 3.
            removals = evaluate_cleanup(
                [a, b, c],
                {"enabled": True, "keep_last_per_source": 1},
                keep_last_map={"ChanX": 3},
            )
            self.assertEqual(removals, [])

    def test_keep_last_respects_favorite_exemption(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            a = self._dated_recording(tmpdir, "a", "ChanX", mtime=100)
            a.favorite = True
            b = self._dated_recording(tmpdir, "b", "ChanX", mtime=200)
            c = self._dated_recording(tmpdir, "c", "ChanX", mtime=300)
            removals = evaluate_cleanup(
                [a, b, c],
                {"enabled": True, "keep_last_per_source": 1, "favorites_exempt": True},
            )
            # Favorite 'a' is exempt entirely; of the remaining b,c keep newest c.
            paths = {os.path.realpath(h.path) for h, _r in removals}
            self.assertEqual(paths, {os.path.realpath(b.path)})

    def test_keep_last_map_from_monitor_builds_channel_map(self):
        entries = [
            MonitorEntry(channel_id="ChanX", retention_keep_last=3),
            MonitorEntry(channel_id="ChanY", retention_keep_last=0),
            MonitorEntry(channel_id="", retention_keep_last=5),
        ]
        self.assertEqual(keep_last_map_from_monitor(entries), {"ChanX": 3})


if __name__ == "__main__":
    unittest.main()
