"""Offline fixture tests for DASH/HLS manifest parsers.

These tests use static fixture files so upstream API changes don't
break the test suite. Each fixture represents a real-world manifest
pattern that the parsers must handle correctly.
"""

import unittest
from pathlib import Path

from streamkeep.dash import parse_mpd_xml
from streamkeep.hls import parse_hls_master, parse_hls_duration

FIXTURES = Path(__file__).parent / "fixtures" / "manifests"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class DashStaticMPDTests(unittest.TestCase):
    def test_static_vod_parses_video_and_audio(self):
        qualities = parse_mpd_xml(
            _read("static_vod.mpd"),
            "https://cdn.example.com/manifest.mpd",
        )
        self.assertEqual(len(qualities), 3)
        video_qs = [q for q in qualities if q.resolution]
        audio_qs = [q for q in qualities if not q.resolution]
        self.assertGreaterEqual(len(video_qs), 2)
        self.assertGreaterEqual(len(audio_qs), 1)
        resolutions = {q.resolution for q in video_qs}
        self.assertIn("1920x1080", resolutions)

    def test_multi_period_parses_all_periods(self):
        qualities = parse_mpd_xml(
            _read("multi_period.mpd"),
            "https://cdn.example.com/manifest.mpd",
        )
        urls = [q.url for q in qualities]
        has_p1 = any("period1" in u for u in urls)
        has_p2 = any("period2" in u for u in urls)
        self.assertTrue(has_p1, "Period 1 representations missing")
        self.assertTrue(has_p2, "Period 2 representations missing")


class DashDynamicMPDTests(unittest.TestCase):
    def test_dynamic_mpd_returns_empty_with_message(self):
        messages = []
        qualities = parse_mpd_xml(
            _read("dynamic_live.mpd"),
            "https://cdn.example.com/live.mpd",
            log_fn=messages.append,
        )
        self.assertEqual(qualities, [])
        self.assertTrue(
            any("dynamic" in m.lower() or "not yet supported" in m.lower() for m in messages),
            f"Expected dynamic MPD rejection message, got: {messages}",
        )


class DashDRMTests(unittest.TestCase):
    def test_drm_protected_skipped_with_warning(self):
        messages = []
        qualities = parse_mpd_xml(
            _read("drm_protected.mpd"),
            "https://cdn.example.com/drm.mpd",
            log_fn=messages.append,
        )
        self.assertEqual(qualities, [])
        self.assertTrue(
            any("drm" in m.lower() for m in messages),
            f"Expected DRM skip message, got: {messages}",
        )


class HLSMasterPlaylistTests(unittest.TestCase):
    def test_master_playlist_parses_three_variants(self):
        qualities = parse_hls_master(
            _read("master.m3u8"),
            "https://cdn.example.com/live/",
        )
        self.assertEqual(len(qualities), 3)
        resolutions = {q.resolution for q in qualities}
        self.assertIn("1920x1080", resolutions)
        self.assertIn("1280x720", resolutions)
        self.assertIn("640x360", resolutions)

    def test_master_variant_urls_resolved(self):
        qualities = parse_hls_master(
            _read("master.m3u8"),
            "https://cdn.example.com/live/",
        )
        for q in qualities:
            self.assertTrue(
                q.url.startswith("https://"),
                f"Variant URL not resolved: {q.url}",
            )


class HLSMediaPlaylistTests(unittest.TestCase):
    def test_media_playlist_duration_and_segments(self):
        total, start_time, seg_count = parse_hls_duration(_read("media.m3u8"))
        self.assertEqual(seg_count, 4)
        self.assertIn("2026-07-01", start_time)

    def test_ll_hls_media_segment_count(self):
        _, _, seg_count = parse_hls_duration(_read("ll_hls.m3u8"))
        self.assertEqual(seg_count, 2)


if __name__ == "__main__":
    unittest.main()
