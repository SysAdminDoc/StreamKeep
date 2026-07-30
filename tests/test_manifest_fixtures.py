"""Offline fixture tests for DASH/HLS manifest parsers.

These tests use static fixture files so upstream API changes don't
break the test suite. Each fixture represents a real-world manifest
pattern that the parsers must handle correctly.
"""

import ipaddress
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep.dash import (
    parse_mpd_xml,
    preflight_dash_manifest,
    validate_dash_manifest,
)
from streamkeep.hls import (
    parse_hls_duration,
    parse_hls_master,
    parse_hls_media_playlist,
    preflight_hls_manifest_tree,
    resume_identity_matches,
    validate_hls_manifest,
)
from streamkeep.models import ResumeState, default_media_tracks
from streamkeep.net_guard import RemoteURLPolicyError

FIXTURES = Path(__file__).parent / "fixtures" / "manifests"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _resolved_addresses(host, _port):
    try:
        return (ipaddress.ip_address(host),)
    except ValueError:
        return (ipaddress.ip_address("93.184.216.34"),)


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

    def test_multi_representation_tracks_are_selectable_together(self):
        qualities = parse_mpd_xml(
            _read("multi_representation.mpd"),
            "https://cdn.example.com/live/main.mpd",
        )
        self.assertEqual(len(qualities), 6)
        video = next(q for q in qualities if q.resolution == "1920x1080")
        self.assertEqual(
            [track.kind for track in video.tracks],
            ["video", "video", "audio", "audio", "subtitle", "subtitle"],
        )
        self.assertTrue(all(
            track.url == "https://cdn.example.com/live/main.mpd"
            for track in video.tracks
        ))
        self.assertEqual(
            [(track.kind, track.language) for track in default_media_tracks(video)],
            [("video", ""), ("audio", "en"), ("subtitle", "en")],
        )
        forced = next(track for track in video.tracks if track.language == "es"
                      and track.kind == "subtitle")
        self.assertTrue(forced.forced)


class DashDynamicMPDTests(unittest.TestCase):
    def test_dynamic_mpd_returns_qualities_with_live_format(self):
        messages = []
        qualities = parse_mpd_xml(
            _read("dynamic_live.mpd"),
            "https://cdn.example.com/live.mpd",
            log_fn=messages.append,
        )
        self.assertGreater(len(qualities), 0)
        self.assertTrue(
            any("dynamic" in m.lower() or "live" in m.lower() for m in messages),
            f"Expected dynamic MPD info message, got: {messages}",
        )
        self.assertTrue(
            all(q.format_type == "dash-live" for q in qualities),
            "Dynamic MPD qualities should have format_type='dash-live'",
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


class DashManifestPolicyTests(unittest.TestCase):
    def test_base_template_list_initialization_and_cdn_changes_are_checked(self):
        manifest = """\
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
  <ContentSteering proxyServerURL="https://proxy.example.net/steer">
    https://steering.example.com/api
  </ContentSteering>
  <BaseURL>https://media.example.com/root/</BaseURL>
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <SegmentTemplate initialization="init-$RepresentationID$.mp4"
                       media="//segments.example.net/v-$Number$.m4s"
                       bitstreamSwitching="switch-$RepresentationID$.mp4"/>
      <Representation id="v1">
        <SegmentList>
          <Initialization sourceURL="https://init.example.org/v1.mp4"/>
          <BitstreamSwitching sourceURL="switch-init.mp4"/>
          <SegmentURL media="chunk-1.m4s" index="chunk-1.sidx"/>
        </SegmentList>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>"""
        with mock.patch(
            "streamkeep.net_guard.resolve_host_addresses",
            side_effect=_resolved_addresses,
        ):
            references = validate_dash_manifest(
                manifest, "https://origin.example.com/main.mpd"
            )

        self.assertIn(
            "https://media.example.com/root/init-$RepresentationID$.mp4",
            references,
        )
        self.assertIn(
            "https://segments.example.net/v-$Number$.m4s", references
        )
        self.assertIn("https://init.example.org/v1.mp4", references)
        self.assertIn(
            "https://media.example.com/root/chunk-1.m4s", references
        )
        self.assertIn(
            "https://media.example.com/root/chunk-1.sidx", references
        )
        self.assertIn(
            "https://media.example.com/root/switch-$RepresentationID$.mp4",
            references,
        )
        self.assertIn(
            "https://media.example.com/root/switch-init.mp4", references
        )
        self.assertIn(
            "https://steering.example.com/api", references
        )
        self.assertIn(
            "https://proxy.example.net/steer", references
        )

    def test_malicious_dash_fixture_cannot_reach_sentinel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sentinel = Path(tmpdir) / "sentinel.txt"
            sentinel.write_text("do-not-read", encoding="utf-8")
            manifest = _read("malicious_remote.mpd").replace(
                "file:///STREAMKEEP_SENTINEL", sentinel.as_uri()
            )
            with mock.patch(
                "streamkeep.net_guard.resolve_host_addresses",
                side_effect=_resolved_addresses,
            ), self.assertRaises(RemoteURLPolicyError):
                validate_dash_manifest(
                    manifest, "https://origin.example.com/main.mpd"
                )
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"), "do-not-read"
            )

    def test_dash_preflight_fetches_only_through_supplied_guard(self):
        manifest = """\
<MPD><Period><AdaptationSet mimeType="video/mp4">
<Representation id="v"><BaseURL>https://cdn.example.net/video.mp4</BaseURL>
</Representation></AdaptationSet></Period></MPD>"""
        fetched = []

        def fetch(url):
            fetched.append(url)
            return manifest

        with mock.patch(
            "streamkeep.net_guard.resolve_host_addresses",
            side_effect=_resolved_addresses,
        ):
            result = preflight_dash_manifest(
                "https://origin.example.com/main.mpd", fetch
            )
        self.assertEqual(result, "https://origin.example.com/main.mpd")
        self.assertEqual(fetched, [result])


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

    def test_alternate_audio_and_subtitle_renditions_are_attached(self):
        qualities = parse_hls_master(
            _read("alt_renditions.m3u8"),
            "https://cdn.example.com/live/master.m3u8",
        )
        self.assertEqual(len(qualities), 2)
        tracks = qualities[0].tracks
        self.assertEqual(
            [track.kind for track in tracks],
            ["video", "audio", "audio", "subtitle", "subtitle"],
        )
        self.assertEqual(
            next(track.url for track in tracks if track.language == "es"
                 and track.kind == "audio"),
            "https://cdn.example.com/live/audio/es.m3u8",
        )
        self.assertEqual(
            [(track.kind, track.language) for track in default_media_tracks(qualities[0])],
            [("video", ""), ("audio", "en"), ("subtitle", "en")],
        )


    def test_frame_rate_hdr_and_average_bandwidth_reach_selection(self):
        qualities = parse_hls_master(
            _read("master_hdr_fps.m3u8"),
            "https://cdn.example.com/live/master.m3u8",
        )
        self.assertEqual(len(qualities), 2)
        uhd = next(q for q in qualities if q.resolution == "3840x2160")
        self.assertAlmostEqual(uhd.frame_rate, 59.94, places=2)
        self.assertEqual(uhd.video_range, "PQ")
        self.assertEqual(uhd.bandwidth, 16000000)
        self.assertEqual(uhd.average_bandwidth, 12000000)
        video = next(t for t in uhd.tracks if t.kind == "video")
        self.assertAlmostEqual(video.frame_rate, 59.94, places=2)
        self.assertEqual(video.video_range, "PQ")
        sdr = next(q for q in qualities if q.resolution == "1920x1080")
        self.assertEqual(sdr.video_range, "SDR")
        self.assertAlmostEqual(sdr.frame_rate, 29.97, places=2)


class HLSManifestPolicyTests(unittest.TestCase):
    def test_variants_renditions_keys_maps_parts_and_cdn_changes_are_checked(self):
        manifest = """\
#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="//audio.example.net/en.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",URI="subs/en.m3u8"
#EXT-X-KEY:METHOD=AES-128,URI="https://keys.example.org/key.bin"
#EXT-X-MAP:URI="init.mp4"
#EXT-X-PART:DURATION=0.5,URI="//parts.example.net/part-1.m4s"
#EXT-X-STREAM-INF:BANDWIDTH=1200000,AUDIO="a",SUBTITLES="s"
https://video.example.com/720p.m3u8
"""
        with mock.patch(
            "streamkeep.net_guard.resolve_host_addresses",
            side_effect=_resolved_addresses,
        ):
            references = validate_hls_manifest(
                manifest, "https://origin.example.com/live/master.m3u8"
            )

        self.assertEqual(
            set(references.playlists),
            {
                "https://audio.example.net/en.m3u8",
                "https://origin.example.com/live/subs/en.m3u8",
                "https://video.example.com/720p.m3u8",
            },
        )
        self.assertIn("https://keys.example.org/key.bin", references.resources)
        self.assertIn(
            "https://origin.example.com/live/init.mp4", references.resources
        )
        self.assertIn(
            "https://parts.example.net/part-1.m4s", references.resources
        )

    def test_recursive_hls_graph_validates_media_segments(self):
        documents = {
            "https://origin.example.com/master.m3u8": (
                "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n"
                "https://cdn.example.net/media.m3u8\n"
            ),
            "https://cdn.example.net/media.m3u8": (
                "#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\n"
                "#EXTINF:4,\nseg-1.ts\n#EXT-X-ENDLIST\n"
            ),
        }
        fetched = []

        def fetch(url):
            fetched.append(url)
            return documents.get(url)

        with mock.patch(
            "streamkeep.net_guard.resolve_host_addresses",
            side_effect=_resolved_addresses,
        ):
            manifests = preflight_hls_manifest_tree(
                "https://origin.example.com/master.m3u8", fetch
            )

        self.assertEqual(manifests, tuple(documents))
        self.assertEqual(fetched, list(documents))

    def test_malicious_hls_fixture_cannot_reach_sentinel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sentinel = Path(tmpdir) / "sentinel.txt"
            sentinel.write_text("do-not-read", encoding="utf-8")
            manifest = _read("malicious_remote.m3u8").replace(
                "file:///STREAMKEEP_SENTINEL", sentinel.as_uri()
            )
            with mock.patch(
                "streamkeep.net_guard.resolve_host_addresses",
                side_effect=_resolved_addresses,
            ), self.assertRaises(RemoteURLPolicyError):
                validate_hls_manifest(
                    manifest, "https://origin.example.com/media.m3u8"
                )
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"), "do-not-read"
            )


class HLSMediaPlaylistTests(unittest.TestCase):
    def test_media_playlist_duration_and_segments(self):
        total, start_time, seg_count = parse_hls_duration(_read("media.m3u8"))
        self.assertEqual(seg_count, 4)
        self.assertIn("2026-07-01", start_time)
        self.assertAlmostEqual(total, 38.5, places=1)

    def test_typed_media_playlist_is_vod_with_sequence(self):
        playlist = parse_hls_media_playlist(
            _read("media.m3u8"), "https://cdn.example.com/",
        )
        self.assertTrue(playlist.is_endlist)
        self.assertFalse(playlist.is_live)
        self.assertEqual(playlist.media_sequence, 0)
        self.assertEqual(len(playlist.segments), 4)
        self.assertEqual(playlist.target_duration, 10.0)
        self.assertEqual(
            [s.media_sequence for s in playlist.segments], [0, 1, 2, 3]
        )
        self.assertEqual(
            playlist.segments[0].uri, "https://cdn.example.com/seg0.ts"
        )
        self.assertAlmostEqual(playlist.total_duration, 38.5, places=1)

    def test_live_rollover_tracks_sequence_and_discontinuity(self):
        playlist = parse_hls_media_playlist(_read("live_rollover.m3u8"))
        self.assertTrue(playlist.is_live)
        self.assertEqual(playlist.media_sequence, 947210)
        self.assertEqual(playlist.discontinuity_sequence, 31)
        self.assertEqual(
            [s.media_sequence for s in playlist.segments],
            [947210, 947211, 947212],
        )
        # The discontinuity between segment 0 and 1 advances the per-segment
        # discontinuity sequence.
        self.assertEqual(
            [s.discontinuity_sequence for s in playlist.segments],
            [31, 32, 32],
        )
        self.assertAlmostEqual(playlist.total_duration, 17.5, places=1)

    def test_gaps_and_byterange_are_captured(self):
        playlist = parse_hls_media_playlist(_read("media_gap.m3u8"))
        self.assertEqual([s.gap for s in playlist.segments], [False, True, False])
        self.assertEqual(playlist.segments[2].byterange, "75232@0")
        self.assertTrue(playlist.is_endlist)
        self.assertEqual(playlist.discontinuity_sequence, 2)

    def test_malformed_extinf_isolates_bad_segment(self):
        playlist = parse_hls_media_playlist(_read("media_malformed.m3u8"))
        # seg6 has a non-numeric EXTINF and is skipped, but sequence numbering
        # stays aligned so seg7 keeps its true media-sequence position.
        self.assertEqual(
            [s.uri for s in playlist.segments], ["seg5.ts", "seg7.ts"]
        )
        self.assertEqual(
            [s.media_sequence for s in playlist.segments], [5, 7]
        )
        self.assertAlmostEqual(playlist.total_duration, 19.0, places=1)


class HLSResumeIdentityTests(unittest.TestCase):
    def _state(self, **kw):
        base = dict(
            playlist_validator="etag-1",
            media_sequence=100,
            discontinuity_sequence=2,
            playlist_segment_count=3,
        )
        base.update(kw)
        return ResumeState(**base)

    def test_identical_identity_can_resume(self):
        playlist = parse_hls_media_playlist(_read("media_gap.m3u8"))
        playlist.validator = "etag-1"
        self.assertTrue(resume_identity_matches(self._state(), playlist))

    def test_changed_validator_forces_restart(self):
        playlist = parse_hls_media_playlist(_read("media_gap.m3u8"))
        playlist.validator = "etag-2"
        self.assertFalse(resume_identity_matches(self._state(), playlist))

    def test_window_rolled_past_forces_restart(self):
        playlist = parse_hls_media_playlist(_read("media_gap.m3u8"))
        playlist.validator = "etag-1"
        playlist.media_sequence = 200  # far beyond stored 100 + 3 segments
        self.assertFalse(resume_identity_matches(self._state(), playlist))

    def test_crossed_discontinuity_forces_restart(self):
        playlist = parse_hls_media_playlist(_read("media_gap.m3u8"))
        playlist.validator = "etag-1"
        playlist.discontinuity_sequence = 5  # advanced past stored 2
        self.assertFalse(resume_identity_matches(self._state(), playlist))

    def test_ll_hls_media_segment_count(self):
        total, _, seg_count = parse_hls_duration(_read("ll_hls.m3u8"))
        self.assertEqual(seg_count, 2)
        self.assertAlmostEqual(total, 8.0, places=1)

    def test_live_rollover_and_discontinuity_keep_duration_and_count(self):
        total, start_time, seg_count = parse_hls_duration(
            _read("live_rollover.m3u8")
        )
        self.assertEqual(seg_count, 3)
        self.assertAlmostEqual(total, 17.5, places=1)
        self.assertEqual(start_time, "2026-07-16T14:00:00.000Z")


if __name__ == "__main__":
    unittest.main()
