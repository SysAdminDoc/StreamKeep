import os
import tempfile
import unittest
from pathlib import Path

from streamkeep.lifecycle import evaluate_cleanup, removal_real_paths
from streamkeep.models import HistoryEntry


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


if __name__ == "__main__":
    unittest.main()
