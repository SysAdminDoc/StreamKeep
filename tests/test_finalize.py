"""Dedicated coverage for FinalizeWorker's pure planning/decision helpers.

The QThread is never started — the helpers are called directly so the step
planning, chat-VOD detection, podcast-feed resolution, and output-size labels
can be asserted without running the finalize pipeline against real files.
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from streamkeep.workers.finalize import FinalizeWorker


def _worker(task=None):
    return FinalizeWorker(task or {})


class HasPostprocessWorkTests(unittest.TestCase):
    def test_empty_snapshot_is_no_work(self):
        self.assertFalse(_worker()._has_postprocess_work({}))
        self.assertFalse(_worker()._has_postprocess_work(None))

    def test_any_active_flag_is_work(self):
        self.assertTrue(_worker()._has_postprocess_work({"extract_audio": True}))
        self.assertTrue(_worker()._has_postprocess_work({"convert_video": "mp4"}))

    def test_unrelated_keys_are_not_work(self):
        self.assertFalse(_worker()._has_postprocess_work({"unrelated": True}))


class ChatVodIdTests(unittest.TestCase):
    def test_twitch_vod_url_yields_id(self):
        info = SimpleNamespace(
            platform="Twitch",
            url="https://vod-secure.twitch.tv/abc/vod/12345.m3u8",
        )
        self.assertEqual(_worker()._chat_vod_id(info), "12345")

    def test_non_twitch_is_empty(self):
        info = SimpleNamespace(platform="Kick", url="https://x/vod/999.m3u8")
        self.assertEqual(_worker()._chat_vod_id(info), "")

    def test_twitch_without_vod_pattern_is_empty(self):
        info = SimpleNamespace(platform="Twitch", url="https://x/live.m3u8")
        self.assertEqual(_worker()._chat_vod_id(info), "")


class PodcastFeedUrlTests(unittest.TestCase):
    def test_feed_from_task(self):
        info = SimpleNamespace(platform="Podcast", url="https://x/ep.mp3")
        task = {"feed_url": "https://x/feed.xml"}
        self.assertEqual(_worker()._podcast_feed_url(task, info), "https://x/feed.xml")

    def test_feed_from_info_when_task_absent(self):
        info = SimpleNamespace(platform="podcast", feed_url="https://y/feed.xml")
        self.assertEqual(_worker()._podcast_feed_url({}, info), "https://y/feed.xml")

    def test_non_podcast_is_empty(self):
        info = SimpleNamespace(platform="Twitch", feed_url="https://y/feed.xml")
        self.assertEqual(_worker()._podcast_feed_url({}, info), "")


class PlannedStepsTests(unittest.TestCase):
    def test_minimal_plan_is_metadata_and_manifest(self):
        info = SimpleNamespace(platform="Direct", url="https://x/v.mp4", chapters=None)
        steps = _worker()._planned_steps({}, info, {})
        keys = [k for _label, k in steps]
        self.assertEqual(keys[0], "metadata")
        self.assertIn("manifest", keys)

    def test_full_plan_includes_all_stages_in_order(self):
        info = SimpleNamespace(
            platform="Twitch",
            url="https://vod-secure.twitch.tv/a/vod/777.m3u8",
            chapters=[{"title": "intro", "start": 0}],
        )
        task = {
            "write_nfo": True,
            "download_chat": True,
            "record_manifest": True,
        }
        steps = _worker()._planned_steps(task, info, {"extract_audio": True})
        keys = [k for _label, k in steps]
        self.assertEqual(
            keys,
            ["metadata", "nfo", "chapters", "chat", "postprocess", "manifest"],
        )

    def test_manifest_can_be_opted_out(self):
        info = SimpleNamespace(platform="Direct", url="https://x/v.mp4", chapters=None)
        steps = _worker()._planned_steps({"record_manifest": False}, info, {})
        self.assertNotIn("manifest", [k for _l, k in steps])


class OutputSizeLabelTests(unittest.TestCase):
    def test_missing_dir_is_empty(self):
        self.assertEqual(_worker()._output_size_label("/no/such/dir/here"), "")

    def test_sums_file_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.bin"), "wb") as fh:
                fh.write(b"x" * 2048)
            label = _worker()._output_size_label(tmp)
            self.assertTrue(label)  # non-empty human-readable size

    def test_empty_dir_is_empty_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_worker()._output_size_label(tmp), "")


class InterruptTests(unittest.TestCase):
    def test_cancel_flag_marks_interrupted(self):
        worker = _worker()
        self.assertFalse(worker._interrupted())
        worker.cancel()
        self.assertTrue(worker._interrupted())


if __name__ == "__main__":
    unittest.main()
