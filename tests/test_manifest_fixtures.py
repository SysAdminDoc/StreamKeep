"""Offline fixture tests for DASH/HLS manifest parsers.

These tests use static fixture files so upstream API changes don't
break the test suite. Each fixture represents a real-world manifest
pattern that the parsers must handle correctly.
"""

import ipaddress
import json
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
    HLSPlaylistError,
    HLSUnsupportedVersionError,
    HLSVariableError,
    parse_hls_duration,
    parse_hls_master,
    parse_hls_media_playlist,
    preflight_hls_manifest_tree,
    resume_identity_matches,
    validate_hls_manifest,
)
from streamkeep.metadata import MetadataSaver
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
    XML_ENTITY_BOMB = """<!DOCTYPE bomb [
      <!ENTITY a "1234567890">
      <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
      <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
    ]><bomb>&c;</bomb>"""

    def test_entity_expansion_is_rejected_before_manifest_processing(self):
        messages = []
        self.assertEqual(
            parse_mpd_xml(
                self.XML_ENTITY_BOMB,
                "https://cdn.example.com/main.mpd",
                messages.append,
            ),
            [],
        )
        self.assertTrue(any("parse error" in message.lower() for message in messages))
        with self.assertRaises(RemoteURLPolicyError):
            validate_dash_manifest(
                self.XML_ENTITY_BOMB,
                "https://origin.example.com/main.mpd",
            )

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

    def test_a_declared_video_rendition_name_beats_the_path_component(self):
        """Some CDNs number their variant playlists, so the last path
        component is "0.m3u8" and useless as a thing to choose between. When
        the manifest declares a TYPE=VIDEO rendition, its NAME is the
        provider's own label for that variant and is used instead."""
        body = (
            '#EXTM3U\n'
            '#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="1080p60",NAME="1080p60",'
            'AUTOSELECT=YES,DEFAULT=YES\n'
            '#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="480p30",NAME="480p30",'
            'AUTOSELECT=YES,DEFAULT=NO\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,'
            'VIDEO="1080p60"\n'
            '0.m3u8\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480,'
            'VIDEO="480p30"\n'
            '1.m3u8\n'
        )
        qualities = parse_hls_master(body, "https://vod.example.com/x/")
        self.assertEqual([q.name for q in qualities], ["1080p60", "480p30"])

    def test_the_path_component_is_still_used_without_a_video_group(self):
        body = (
            '#EXTM3U\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n'
            '1080p60/playlist.m3u8\n'
        )
        qualities = parse_hls_master(body, "https://cdn.example.com/live/")
        self.assertEqual([q.name for q in qualities], ["playlist.m3u8"])

    def test_an_unnamed_video_group_does_not_blank_the_label(self):
        body = (
            '#EXTM3U\n'
            '#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="chunked",NAME=""\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=8000000,VIDEO="chunked"\n'
            'source.m3u8\n'
        )
        qualities = parse_hls_master(body, "https://cdn.example.com/live/")
        self.assertEqual([q.name for q in qualities], ["source.m3u8"])

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

    def test_playlist_variables_expand_master_uris_and_attributes(self):
        body = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:8\n"
            '#EXT-X-DEFINE:NAME="host",VALUE="cdn.example.com"\n'
            '#EXT-X-DEFINE:NAME="lang",VALUE="en"\n'
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="English",'
            'URI="audio/{$lang}.m3u8"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=8000000,AUDIO="a"\n'
            'https://{$host}/video.m3u8\n'
        )
        qualities = parse_hls_master(body, "https://origin.example.com/master.m3u8")
        self.assertEqual(qualities[0].url, "https://cdn.example.com/video.m3u8")
        self.assertEqual(
            next(track.url for track in qualities[0].tracks if track.kind == "audio"),
            "https://origin.example.com/audio/en.m3u8",
        )

    def test_undefined_playlist_variable_is_a_named_error(self):
        body = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1\n"
            "https://cdn.example.com/{$missing}.m3u8\n"
        )
        with self.assertRaisesRegex(HLSVariableError, "undefined HLS playlist variable"):
            parse_hls_master(body, "https://origin.example.com/master.m3u8")

    def test_playlist_variables_require_explicit_import_in_media_playlist(self):
        body = (
            "#EXTM3U\n#EXT-X-VERSION:8\n"
            "#EXTINF:4,\nseg-{$token}.ts\n"
        )
        with self.assertRaisesRegex(HLSVariableError, "undefined HLS playlist variable"):
            parse_hls_media_playlist(
                body,
                "https://origin.example.com/live/media.m3u8",
                variables={"token": "inherited"},
            )

    def test_playlist_variable_substitution_requires_protocol_version(self):
        with self.assertRaisesRegex(HLSPlaylistError, "requires EXT-X-VERSION"):
            parse_hls_media_playlist(
                "#EXTM3U\n#EXT-X-DEFINE:NAME=\"token\",VALUE=\"value\"\n"
                "#EXTINF:4,\nseg-{$token}.ts\n",
                "https://origin.example.com/live/media.m3u8",
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
    def test_media_import_and_queryparam_variables_expand_segments(self):
        imported = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:8\n"
            '#EXT-X-DEFINE:IMPORT="token"\n'
            "#EXTINF:4,\nseg-{$token}.ts\n"
        )
        playlist = parse_hls_media_playlist(
            imported,
            "https://origin.example.com/live/media.m3u8",
            variables={"token": "imported"},
        )
        self.assertEqual(
            playlist.segments[0].uri,
            "https://origin.example.com/live/seg-imported.ts",
        )

        queryparam = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:11\n"
            '#EXT-X-DEFINE:QUERYPARAM="token"\n'
            "#EXTINF:4,\nseg-{$token}.ts\n"
        )
        playlist = parse_hls_media_playlist(
            queryparam,
            "https://origin.example.com/live/media.m3u8?token=query-value",
        )
        self.assertEqual(
            playlist.segments[0].uri,
            "https://origin.example.com/live/seg-query-value.ts",
        )

    def test_queryparam_is_percent_decoded_and_empty_is_rejected(self):
        playlist = parse_hls_media_playlist(
            "#EXTM3U\n#EXT-X-VERSION:11\n"
            '#EXT-X-DEFINE:QUERYPARAM="token"\n'
            "#EXTINF:4,\nseg-{$token}.ts\n",
            "https://origin.example.com/live/media.m3u8?token=one%2Ftwo",
        )
        self.assertEqual(
            playlist.segments[0].uri,
            "https://origin.example.com/live/seg-one/two.ts",
        )
        with self.assertRaisesRegex(HLSVariableError, "query parameter is missing"):
            parse_hls_media_playlist(
                "#EXTM3U\n#EXT-X-VERSION:11\n"
                '#EXT-X-DEFINE:QUERYPARAM="token"\n'
                "#EXTINF:4,\nseg-{$token}.ts\n",
                "https://origin.example.com/live/media.m3u8?token=",
            )

    def test_playlist_type_vod_is_complete_without_endlist(self):
        playlist = parse_hls_media_playlist(
            "#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:2,\nseg.ts\n",
            "https://origin.example.com/live/media.m3u8",
        )
        self.assertEqual(playlist.playlist_type, "VOD")
        self.assertTrue(playlist.is_endlist)

    def test_playlist_version_above_supported_floor_is_named_error(self):
        with self.assertRaisesRegex(HLSUnsupportedVersionError, "unsupported HLS"):
            parse_hls_media_playlist(
                "#EXTM3U\n#EXT-X-VERSION:14\n",
                "https://origin.example.com/live/media.m3u8",
            )

    def test_master_variables_are_inherited_by_recursive_media_validation(self):
        documents = {
            "https://origin.example.com/master.m3u8": (
                "#EXTM3U\n#EXT-X-VERSION:8\n"
                '#EXT-X-DEFINE:NAME="token",VALUE="abc"\n'
                "#EXT-X-STREAM-INF:BANDWIDTH=1\nmedia.m3u8\n"
            ),
            "https://origin.example.com/media.m3u8": (
                "#EXTM3U\n#EXT-X-VERSION:8\n"
                '#EXT-X-DEFINE:IMPORT="token"\n'
                "#EXTINF:4,\nseg-{$token}.ts\n"
            ),
        }
        fetched = []
        contexts = []

        def fetch(url):
            fetched.append(url)
            return documents.get(url)

        with mock.patch(
            "streamkeep.net_guard.resolve_host_addresses",
            side_effect=_resolved_addresses,
        ):
            manifests = preflight_hls_manifest_tree(
                "https://origin.example.com/master.m3u8",
                fetch,
                on_manifest_context=(
                    lambda url, _body, variables:
                    contexts.append((url, variables))
                ),
            )
        self.assertEqual(
            manifests,
            ("https://origin.example.com/master.m3u8", "https://origin.example.com/media.m3u8"),
        )
        self.assertEqual(fetched, list(manifests))
        self.assertEqual(contexts[0][1], {"token": "abc"})
        self.assertEqual(contexts[1][1], {"token": "abc"})

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

    def test_twitch_prefetch_uri_is_checked_as_a_media_reference(self):
        manifest = (
            "#EXTM3U\n"
            "#EXT-X-TWITCH-PREFETCH:segments/prefetch.ts\n"
        )
        with mock.patch(
            "streamkeep.net_guard.resolve_host_addresses",
            side_effect=_resolved_addresses,
        ):
            references = validate_hls_manifest(
                manifest, "https://origin.example.com/live/media.m3u8"
            )

        self.assertEqual(
            references.resources,
            ("https://origin.example.com/live/segments/prefetch.ts",),
        )

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

    def test_daterange_asset_and_schedule_uris_are_resources_not_playlists(self):
        with mock.patch(
            "streamkeep.net_guard.resolve_host_addresses",
            side_effect=_resolved_addresses,
        ):
            references = validate_hls_manifest(
                _read("daterange_interstitial.m3u8"),
                "https://origin.example.com/live/media.m3u8",
            )
        self.assertIn(
            "https://origin.example.com/live/interstitial/asset.m3u8?token=secret",
            references.resources,
        )
        self.assertIn(
            "https://origin.example.com/live/schedules/dateranges.json",
            references.resources,
        )
        self.assertEqual(references.playlists, ())
        self.assertEqual(
            references.schedule_uris,
            ("https://origin.example.com/live/schedules/dateranges.json",),
        )

    def test_daterange_schedule_is_fetched_by_the_guarded_preflight(self):
        root = "https://origin.example.com/live/media.m3u8"
        schedule = "https://origin.example.com/live/schedules/dateranges.json"
        documents = {
            root: _read("daterange_interstitial.m3u8"),
            schedule: json.dumps({"events": [{"id": "e1", "token": "secret"}]}),
        }
        fetched = []
        schedules = []

        def fetch(url):
            fetched.append(url)
            return documents.get(url)

        with mock.patch(
            "streamkeep.net_guard.resolve_host_addresses",
            side_effect=_resolved_addresses,
        ):
            preflight_hls_manifest_tree(
                root, fetch, on_schedule=lambda url, body: schedules.append((url, body))
            )
        self.assertEqual(fetched, [root, schedule])
        self.assertEqual(schedules[0][0], schedule)
        self.assertIn('"events"', schedules[0][1])

    def test_nested_daterange_schedule_markers_are_parsed_and_offset_resolved(self):
        root = "https://origin.example.com/live/media.m3u8"
        schedule = "https://origin.example.com/live/schedules/dateranges.json"
        nested = "https://origin.example.com/live/schedules/nested/dateranges.json"
        documents = {
            root: _read("daterange_interstitial.m3u8"),
            schedule: _read("daterange_schedule.json"),
            nested: _read("daterange_schedule_nested.json"),
        }
        fetched = []
        archived = []
        markers = []

        def fetch(url):
            fetched.append(url)
            return documents.get(url)

        with mock.patch(
            "streamkeep.net_guard.resolve_host_addresses",
            side_effect=_resolved_addresses,
        ):
            preflight_hls_manifest_tree(
                root,
                fetch,
                on_schedule=lambda url, body: archived.append((url, body)),
                on_schedule_markers=lambda _url, _body, rows: markers.extend(rows),
            )

        self.assertEqual(fetched, [root, schedule, nested])
        self.assertEqual([item[0] for item in archived], [schedule, nested])
        by_id = {str(row["id"]): row for row in markers}
        self.assertEqual(
            set(by_id),
            {"schedule-json-1", "json-marker-2", "nested-marker-1", "nested-marker-2"},
        )
        self.assertEqual(by_id["schedule-json-1"]["start_date"], "2026-08-01T12:00:23.500000Z")
        self.assertEqual(by_id["nested-marker-1"]["start_date"], "2026-08-01T12:00:25.500000Z")
        self.assertEqual(by_id["schedule-json-1"]["attributes"]["DURATION"], 12)
        self.assertEqual(by_id["schedule-json-1"]["attributes"]["SCTE35-OUT"], "0xABCD")
        self.assertIn('"DATERANGES"', archived[0][1])
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(
                MetadataSaver.write_hls_markers(
                    tmpdir, markers, schedules=archived, file_base="hls",
                )
            )
            sidecar = json.loads(
                (Path(tmpdir) / "hls.markers.json").read_text(encoding="utf-8")
            )
        self.assertEqual(
            {str(row["id"]) for row in sidecar["markers"]}, set(by_id),
        )

    def test_nested_schedule_cycles_are_bounded_and_preload_types_are_distinct(self):
        root = "https://origin.example.com/live/media.m3u8"
        schedule = "https://origin.example.com/live/schedules/dateranges.json"
        documents = {
            root: (
                "#EXTM3U\n"
                "#EXT-X-DATERANGE:ID=\"schedule\",CLASS=\"com.apple.hls.daterange-schedule\","
                "START-DATE=\"2026-08-01T12:00:00Z\",X-URI=\"schedules/dateranges.json\"\n"
                "#EXT-X-PRELOAD-HINT:TYPE=KEY,URI=\"keys/key.bin\"\n"
                "#EXT-X-PRELOAD-HINT:TYPE=PART,URI=\"parts/part.m4s\"\n"
            ),
            schedule: (
                '{"DATERANGES":[{"ID":"cycle","CLASS":"com.apple.hls.daterange-schedule",'
                '"START-DATE":"2026-08-01T12:00:01Z",'
                '"X-URI":"../schedules/dateranges.json"}]}'
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
            references = validate_hls_manifest(
                documents[root], root,
            )
            preflight_hls_manifest_tree(root, fetch)

        self.assertEqual(
            references.preload_keys,
            ("https://origin.example.com/live/keys/key.bin",),
        )
        self.assertEqual(
            references.preload_parts,
            ("https://origin.example.com/live/parts/part.m4s",),
        )
        self.assertEqual(fetched, [root, schedule])


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

    def test_delta_playlist_merges_the_retained_window(self):
        previous = parse_hls_media_playlist(
            _read("media_delta_base.m3u8"), "https://cdn.example.com/live/"
        )
        current = parse_hls_media_playlist(
            _read("media_delta.m3u8"),
            "https://cdn.example.com/live/",
            previous_playlist=previous,
        )
        self.assertEqual(current.skipped_segments, 2)
        self.assertEqual(
            [segment.media_sequence for segment in current.segments],
            [100, 101, 102, 103],
        )
        self.assertEqual(
            [segment.uri for segment in current.segments],
            [
                "https://cdn.example.com/live/seg100.ts",
                "https://cdn.example.com/live/seg101.ts",
                "https://cdn.example.com/live/seg102.ts",
                "https://cdn.example.com/live/seg103.ts",
            ],
        )

    def test_dateranges_preserve_scte_interstitial_and_unknown_attributes(self):
        playlist = parse_hls_media_playlist(
            _read("daterange_interstitial.m3u8"),
            "https://cdn.example.com/live/media.m3u8",
        )
        self.assertEqual([s.media_sequence for s in playlist.segments], [500, 501])
        self.assertNotIn(
            "https://ads.example.com/asset.m3u8?sig=secret",
            [segment.uri for segment in playlist.segments],
        )
        splice = playlist.dateranges[0]
        self.assertEqual(splice["class"], "com.example.scte35")
        self.assertEqual(splice["scte35_out"], "/DAhAAAAAAAAAP/wFAUAAABf")
        self.assertEqual(splice["scte35_in"], "/DAhAAAAAAAAAP/wFAUAAAAA")
        self.assertEqual(splice["attributes"]["X-PUBLISHER-NOTE"], "preserve-me")
        interstitial = playlist.dateranges[1]
        self.assertEqual(interstitial["type"], "interstitial")
        self.assertEqual(
            interstitial["asset_uri"],
            "https://cdn.example.com/live/interstitial/asset.m3u8?token=secret",
        )
        self.assertIn("URI", interstitial["asset_list"])
        self.assertTrue(playlist.dateranges[2]["is_schedule"])

    def test_marker_sidecar_is_written_and_redacts_signed_asset_queries(self):
        playlist = parse_hls_media_playlist(
            _read("daterange_interstitial.m3u8"),
            "https://cdn.example.com/live/media.m3u8",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(
                MetadataSaver.write_hls_markers(
                    tmpdir,
                    playlist.dateranges,
                    schedules=[
                        (
                            "https://cdn.example.com/schedule.json?sig=secret",
                            '{"events":[{"token":"secret"}]}',
                        )
                    ],
                    file_base="capture",
                )
            )
            sidecar = Path(tmpdir) / "capture.markers.json"
            sidecar_text = sidecar.read_text(encoding="utf-8")
            payload = json.loads(sidecar_text)
        self.assertEqual(payload["schema"], "streamkeep.hls-markers")
        self.assertEqual(len(payload["markers"]), 3)
        self.assertNotIn("token=secret", sidecar_text)
        self.assertNotIn("token", payload["schedules"][0]["payload"]["events"][0])


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
