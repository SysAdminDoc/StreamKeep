"""Comprehensive tests for all StreamKeep extractors.

Tests URL matching, channel-ID extraction, capability flags,
resolve() with mocked API responses, error handling, list_vods(),
and the Extractor.detect() registry routing.

Uses unittest.mock to avoid any real network I/O. Does NOT import
PyQt6 (the test VM lacks it).
"""

import json
import importlib
import sys
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Guard against PyQt6 being imported transitively only when the GUI toolkit is
# unavailable. If PyQt6 is installed, keep the real modules in place so later
# QThread worker tests are not contaminated by MagicMock module stubs.
# ---------------------------------------------------------------------------
_PYQT_STUBS = {}
try:
    importlib.import_module("PyQt6.QtCore")
except ImportError:
    for _mod in (
        "PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
        "PyQt6.QtNetwork", "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets",
        "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebChannel",
        "PyQt6.sip",
    ):
        if _mod not in sys.modules:
            _PYQT_STUBS[_mod] = MagicMock()
            sys.modules[_mod] = _PYQT_STUBS[_mod]

# Now safe to import extractors.
from streamkeep.extractors.base import Extractor
from streamkeep.extractors.kick import KickExtractor
from streamkeep.extractors.twitch import TwitchExtractor
from streamkeep.extractors.rumble import RumbleExtractor
from streamkeep.extractors.soundcloud import SoundCloudExtractor
from streamkeep.extractors.reddit import RedditExtractor
from streamkeep.extractors.audius import AudiusExtractor
from streamkeep.extractors.podcast import PodcastRSSExtractor
from streamkeep.extractors import ytdlp as ytdlp_mod
from streamkeep.extractors.ytdlp import YtDlpExtractor
from streamkeep.http import CommandResult

# ---------------------------------------------------------------------------
# Mock-target helpers.
#
# Each extractor does ``from ..http import curl, curl_json`` etc. at the
# module level, so the name is bound in the *extractor* module's namespace.
# We must patch *there*, not in ``streamkeep.http``.
# ---------------------------------------------------------------------------
_KICK = "streamkeep.extractors.kick"
_TWITCH = "streamkeep.extractors.twitch"
_RUMBLE = "streamkeep.extractors.rumble"
_SOUNDCLOUD = "streamkeep.extractors.soundcloud"
_REDDIT = "streamkeep.extractors.reddit"
_AUDIUS = "streamkeep.extractors.audius"
_PODCAST = "streamkeep.extractors.podcast"
_YTDLP = "streamkeep.extractors.ytdlp"


# ===================================================================
# KickExtractor
# ===================================================================

class TestKickExtractorURL(unittest.TestCase):
    """URL pattern matching and channel-ID extraction for Kick."""

    def setUp(self):
        self.ext = KickExtractor()

    def test_valid_channel_url(self):
        self.assertIsNotNone(
            KickExtractor.URL_PATTERNS[0].match("https://kick.com/xqc")
        )

    def test_valid_channel_url_trailing_slash(self):
        self.assertIsNotNone(
            KickExtractor.URL_PATTERNS[0].match("https://kick.com/xqc/")
        )

    def test_valid_channel_url_no_scheme(self):
        self.assertIsNotNone(
            KickExtractor.URL_PATTERNS[0].match("kick.com/trainwreckstv")
        )

    def test_valid_channel_url_www(self):
        self.assertIsNotNone(
            KickExtractor.URL_PATTERNS[0].match("https://www.kick.com/destiny")
        )

    def test_invalid_url_youtube(self):
        self.assertIsNone(
            KickExtractor.URL_PATTERNS[0].match("https://youtube.com/xqc")
        )

    def test_invalid_url_subpath(self):
        self.assertIsNone(
            KickExtractor.URL_PATTERNS[0].match("https://kick.com/xqc/clips")
        )

    def test_extract_channel_id(self):
        self.assertEqual(
            self.ext.extract_channel_id("https://kick.com/xqc"), "xqc"
        )

    def test_extract_channel_id_trailing_slash(self):
        self.assertEqual(
            self.ext.extract_channel_id("https://kick.com/destiny/"), "destiny"
        )

    def test_extract_channel_id_invalid(self):
        self.assertIsNone(
            self.ext.extract_channel_id("https://twitch.tv/xqc")
        )

    def test_supports_vod_listing(self):
        self.assertTrue(self.ext.supports_vod_listing())

    def test_supports_live_check(self):
        self.assertTrue(self.ext.supports_live_check())

    def test_detect_vod_permalink(self):
        vod = "https://kick.com/blame/videos/36165d38-e240-4a14-8e38-003f0e0e2e86"
        self.assertIsInstance(Extractor.detect(vod), KickExtractor)

    def test_vod_url_channel_and_id(self):
        vod = "https://kick.com/blame/videos/36165d38-e240-4a14-8e38-003f0e0e2e86"
        self.assertEqual(self.ext.extract_channel_id(vod), "blame")
        self.assertEqual(
            self.ext.extract_vod_id(vod),
            "36165d38-e240-4a14-8e38-003f0e0e2e86",
        )

    def test_vod_url_channelless(self):
        vod = "https://kick.com/video/36165d38-e240-4a14-8e38-003f0e0e2e86"
        self.assertIsNone(self.ext.extract_channel_id(vod))
        self.assertEqual(
            self.ext.extract_vod_id(vod),
            "36165d38-e240-4a14-8e38-003f0e0e2e86",
        )

    def test_channel_url_has_no_vod_id(self):
        self.assertIsNone(self.ext.extract_vod_id("https://kick.com/xqc"))


class TestKickExtractorResolve(unittest.TestCase):
    """Kick resolve() and list_vods() with mocked HTTP."""

    def setUp(self):
        self.ext = KickExtractor()

    @patch(f"{_KICK}.curl_json")
    @patch(f"{_KICK}.curl")
    def test_resolve_live_stream(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = {
            "data": {
                "playback_url": "https://fa723fc1b171.us-west-2.playback.live-video.net/master.m3u8",
                "session_title": "Gaming session",
            }
        }
        master_body = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080\n"
            "https://cdn.kick.com/1080p/playlist.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\n"
            "https://cdn.kick.com/720p/playlist.m3u8\n"
        )
        sub_body = (
            "#EXTM3U\n"
            "#EXT-X-TARGETDURATION:2\n"
            "#EXTINF:2.0,\nseg0.ts\n"
            "#EXTINF:2.0,\nseg1.ts\n"
        )
        mock_curl.side_effect = [master_body, sub_body]
        info = self.ext.resolve("https://kick.com/streamer")
        self.assertIsNotNone(info)
        self.assertEqual(info.platform, "Kick")
        self.assertTrue(info.is_live)
        self.assertEqual(info.channel, "streamer")
        self.assertGreater(len(info.qualities), 0)

    @patch(f"{_KICK}.curl_json")
    def test_resolve_no_livestream_no_vods(self, mock_curl_json):
        mock_curl_json.side_effect = [
            {"data": {}},   # _v2_livestream_data — no playback_url
            [],              # list_vods API
        ]
        info = self.ext.resolve("https://kick.com/offline_user")
        self.assertIsNone(info)

    @patch(f"{_KICK}.curl_json")
    def test_resolve_api_returns_none(self, mock_curl_json):
        mock_curl_json.return_value = None
        info = self.ext.resolve("https://kick.com/offline_user")
        self.assertIsNone(info)

    @patch(f"{_KICK}.curl_json")
    @patch(f"{_KICK}.curl")
    def test_resolve_vod_permalink(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = {
            "source": "https://stream.kick.com/vod/media/hls/master.m3u8",
            "livestream": {"session_title": "Archived stream"},
        }
        master_body = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
            "1080p60/playlist.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\n"
            "720p60/playlist.m3u8\n"
        )
        sub_body = "#EXTM3U\n#EXTINF:2.0,\nseg0.ts\n#EXTINF:2.0,\nseg1.ts\n"
        mock_curl.side_effect = [master_body, sub_body]
        info = self.ext.resolve(
            "https://kick.com/blame/videos/36165d38-e240-4a14-8e38-003f0e0e2e86"
        )
        self.assertIsNotNone(info)
        self.assertEqual(info.platform, "Kick")
        self.assertEqual(info.channel, "blame")
        self.assertEqual(info.title, "Archived stream")
        self.assertGreater(len(info.qualities), 0)

    @patch(f"{_KICK}.curl_json")
    def test_resolve_vod_no_source(self, mock_curl_json):
        mock_curl_json.return_value = {"source": "", "livestream": {}}
        info = self.ext.resolve(
            "https://kick.com/blame/videos/36165d38-e240-4a14-8e38-003f0e0e2e86"
        )
        self.assertIsNone(info)

    @patch(f"{_KICK}.curl_json")
    def test_list_vods_success(self, mock_curl_json):
        mock_curl_json.return_value = [
            {
                "session_title": "Day 1",
                "created_at": "2024-01-10T12:00:00Z",
                "source": "https://cdn.kick.com/vod1/master.m3u8",
                "duration": 7200000,
                "is_live": False,
                "viewer_count": 500,
            },
            {
                "session_title": "Day 2",
                "created_at": "2024-01-11T12:00:00Z",
                "source": "https://cdn.kick.com/vod2/master.m3u8",
                "duration": 3600000,
                "is_live": False,
                "viewer_count": 300,
            },
        ]
        vods, cursor = self.ext.list_vods("https://kick.com/streamer")
        self.assertEqual(len(vods), 2)
        self.assertEqual(vods[0].title, "Day 1")
        self.assertEqual(vods[0].platform, "Kick")
        self.assertEqual(vods[0].channel, "streamer")
        self.assertIsNone(cursor)

    @patch(f"{_KICK}.curl_json")
    def test_list_vods_empty(self, mock_curl_json):
        mock_curl_json.return_value = []
        vods, cursor = self.ext.list_vods("https://kick.com/nobody")
        self.assertEqual(vods, [])
        self.assertIsNone(cursor)

    @patch(f"{_KICK}.curl_json")
    def test_list_vods_pagination(self, mock_curl_json):
        mock_curl_json.return_value = [
            {
                "session_title": f"VOD {i}",
                "created_at": "2024-01-01",
                "source": f"https://cdn.kick.com/vod{i}.m3u8",
                "duration": 1000,
            }
            for i in range(20)
        ]
        vods, cursor = self.ext.list_vods("https://kick.com/streamer")
        self.assertEqual(len(vods), 20)
        self.assertEqual(cursor, "2")

    @patch(f"{_KICK}.curl_json")
    def test_list_vods_skips_no_source(self, mock_curl_json):
        mock_curl_json.return_value = [
            {"session_title": "No source", "source": ""},
            {"session_title": "Has source", "source": "https://x.m3u8", "duration": 100},
        ]
        vods, _ = self.ext.list_vods("https://kick.com/streamer")
        self.assertEqual(len(vods), 1)
        self.assertEqual(vods[0].title, "Has source")

    @patch(f"{_KICK}.curl_json")
    def test_check_live_true_via_official_api(self, mock_curl_json):
        mock_curl_json.side_effect = [
            # _official_channel response
            {"data": [{"broadcaster_user_id": "12345", "slug": "streamer"}]},
            # _official_livestream response
            {"data": [{"is_live": True, "viewer_count": 100}]},
        ]
        self.assertTrue(self.ext.check_live("https://kick.com/streamer"))

    @patch(f"{_KICK}.curl_json")
    def test_check_live_false_via_official_api(self, mock_curl_json):
        mock_curl_json.side_effect = [
            # _official_channel response
            {"data": [{"broadcaster_user_id": "12345", "slug": "streamer"}]},
            # _official_livestream — no items = not live
            {"data": []},
        ]
        result = self.ext.check_live("https://kick.com/offline")
        self.assertFalse(result)

    @patch(f"{_KICK}.curl_json")
    def test_check_live_fallback_to_v2(self, mock_curl_json):
        mock_curl_json.side_effect = [
            None,  # _official_channel fails
            {"data": {"playback_url": "https://cdn.kick.com/live.m3u8"}},  # v2 fallback
        ]
        self.assertTrue(self.ext.check_live("https://kick.com/streamer"))

    @patch(f"{_KICK}.curl_json")
    def test_check_live_invalid_channel(self, mock_curl_json):
        result = self.ext.check_live("https://twitch.tv/nope")
        self.assertIsNone(result)
        mock_curl_json.assert_not_called()


# ===================================================================
# TwitchExtractor
# ===================================================================

class TestTwitchExtractorURL(unittest.TestCase):
    """URL matching and channel/VOD ID extraction for Twitch."""

    def setUp(self):
        self.ext = TwitchExtractor()

    def test_vod_url_matches(self):
        self.assertIsNotNone(
            TwitchExtractor.URL_PATTERNS[0].match("https://twitch.tv/videos/123456")
        )

    def test_channel_url_matches(self):
        self.assertIsNotNone(
            TwitchExtractor.URL_PATTERNS[1].match("https://twitch.tv/shroud")
        )

    def test_channel_url_with_www(self):
        self.assertIsNotNone(
            TwitchExtractor.URL_PATTERNS[1].match("https://www.twitch.tv/pokimane")
        )

    def test_invalid_url(self):
        self.assertIsNone(
            TwitchExtractor.URL_PATTERNS[0].match("https://kick.com/videos/123")
        )

    def test_extract_channel_id_channel(self):
        self.assertEqual(
            self.ext.extract_channel_id("https://twitch.tv/shroud"), "shroud"
        )

    def test_extract_channel_id_vod(self):
        self.assertEqual(
            self.ext.extract_channel_id("https://twitch.tv/videos/99999"),
            "vod_99999",
        )

    def test_extract_channel_id_invalid(self):
        self.assertIsNone(
            self.ext.extract_channel_id("https://youtube.com/watch?v=abc")
        )

    def test_supports_vod_listing(self):
        self.assertTrue(self.ext.supports_vod_listing())

    def test_supports_live_check(self):
        self.assertTrue(self.ext.supports_live_check())


class TestTwitchSafeGQL(unittest.TestCase):
    """The _SAFE_GQL_VALUE guard must reject injection attempts."""

    def test_safe_login(self):
        from streamkeep.extractors.twitch import _SAFE_GQL_VALUE
        self.assertIsNotNone(_SAFE_GQL_VALUE.match("shroud"))
        self.assertIsNotNone(_SAFE_GQL_VALUE.match("xQc"))
        self.assertIsNotNone(_SAFE_GQL_VALUE.match("a_b_c"))

    def test_injection_blocked(self):
        from streamkeep.extractors.twitch import _SAFE_GQL_VALUE
        self.assertIsNone(_SAFE_GQL_VALUE.match('"}; DROP TABLE users;'))
        self.assertIsNone(_SAFE_GQL_VALUE.match(""))
        self.assertIsNone(_SAFE_GQL_VALUE.match("a" * 51))
        self.assertIsNone(_SAFE_GQL_VALUE.match("has spaces"))
        self.assertIsNone(_SAFE_GQL_VALUE.match("has/slash"))


class TestTwitchExtractorResolve(unittest.TestCase):
    """Twitch resolve() and list_vods() with mocked GQL/HLS."""

    def setUp(self):
        self.ext = TwitchExtractor()

    @patch(f"{_TWITCH}.curl")
    @patch(f"{_TWITCH}.curl_post_json")
    def test_resolve_vod(self, mock_post, mock_curl):
        mock_post.return_value = {
            "data": {
                "videoPlaybackAccessToken": {
                    "value": '{"expires":9999}',
                    "signature": "abcdef",
                }
            }
        }
        master_m3u8 = (
            "#EXTM3U\n"
            '#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="chunked",NAME="1080p60"\n'
            "#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080\n"
            "https://usher.ttvnw.net/vod/1080p/index.m3u8\n"
        )
        sub_m3u8 = (
            "#EXTM3U\n"
            "#EXT-X-TARGETDURATION:10\n"
            "#EXTINF:10.0,\nseg0.ts\n"
            "#EXTINF:10.0,\nseg1.ts\n"
            "#EXT-X-ENDLIST\n"
        )
        mock_curl.side_effect = [master_m3u8, sub_m3u8]
        info = self.ext.resolve("https://twitch.tv/videos/123456")
        self.assertIsNotNone(info)
        self.assertEqual(info.platform, "Twitch")
        self.assertTrue(info.is_master)
        self.assertGreater(len(info.qualities), 0)
        self.assertEqual(info.qualities[0].name, "1080p60")

    @patch(f"{_TWITCH}.curl_post_json")
    def test_resolve_vod_no_token(self, mock_post):
        mock_post.return_value = None
        info = self.ext.resolve("https://twitch.tv/videos/123456")
        self.assertIsNone(info)

    @patch(f"{_TWITCH}.curl_post_json")
    def test_check_live_true(self, mock_post):
        mock_post.return_value = {
            "data": {
                "user": {
                    "stream": {"id": "42", "type": "live"}
                }
            }
        }
        self.assertTrue(self.ext.check_live("https://twitch.tv/shroud"))

    @patch(f"{_TWITCH}.curl_post_json")
    def test_check_live_false(self, mock_post):
        mock_post.return_value = {
            "data": {"user": {"stream": None}}
        }
        self.assertFalse(self.ext.check_live("https://twitch.tv/offline_user"))

    @patch(f"{_TWITCH}.curl_post_json")
    def test_check_live_injection_rejected(self, mock_post):
        result = self.ext.check_live('https://twitch.tv/"};DROP')
        self.assertIsNone(result)
        mock_post.assert_not_called()

    @patch(f"{_TWITCH}.curl_post_json")
    def test_check_live_vod_url_returns_none(self, mock_post):
        result = self.ext.check_live("https://twitch.tv/videos/99999")
        self.assertIsNone(result)
        mock_post.assert_not_called()

    @patch(f"{_TWITCH}.curl_post_json")
    def test_list_vods_success(self, mock_post):
        mock_post.return_value = {
            "data": {
                "user": {
                    "displayName": "Shroud",
                    "videos": {
                        "edges": [
                            {
                                "node": {
                                    "id": "111",
                                    "title": "Valorant ranked",
                                    "createdAt": "2024-01-05T18:00:00Z",
                                    "lengthSeconds": 14400,
                                    "viewCount": 50000,
                                },
                                "cursor": "c1",
                            },
                        ],
                        "pageInfo": {"hasNextPage": False},
                    },
                }
            }
        }
        vods, cursor = self.ext.list_vods("https://twitch.tv/shroud")
        self.assertEqual(len(vods), 1)
        self.assertEqual(vods[0].title, "Valorant ranked")
        self.assertEqual(vods[0].platform, "Twitch")
        self.assertEqual(vods[0].channel, "shroud")
        self.assertEqual(vods[0].duration_ms, 14400000)
        self.assertIsNone(cursor)

    @patch(f"{_TWITCH}.curl_post_json")
    def test_list_vods_has_next_page(self, mock_post):
        mock_post.return_value = {
            "data": {
                "user": {
                    "displayName": "Shroud",
                    "videos": {
                        "edges": [
                            {
                                "node": {
                                    "id": "222",
                                    "title": "Stream",
                                    "createdAt": "2024-01-06",
                                    "lengthSeconds": 3600,
                                    "viewCount": 100,
                                },
                                "cursor": "cursor_abc",
                            },
                        ],
                        "pageInfo": {"hasNextPage": True},
                    },
                }
            }
        }
        vods, cursor = self.ext.list_vods("https://twitch.tv/shroud")
        self.assertEqual(len(vods), 1)
        self.assertEqual(cursor, "cursor_abc")

    @patch(f"{_TWITCH}.curl_post_json")
    def test_list_vods_empty_gql(self, mock_post):
        mock_post.return_value = None
        vods, cursor = self.ext.list_vods("https://twitch.tv/nobody")
        self.assertEqual(vods, [])
        self.assertIsNone(cursor)

    @patch(f"{_TWITCH}.curl_post_json")
    def test_list_vods_vod_url_returns_empty(self, mock_post):
        vods, cursor = self.ext.list_vods("https://twitch.tv/videos/12345")
        self.assertEqual(vods, [])
        mock_post.assert_not_called()


# ===================================================================
# RumbleExtractor
# ===================================================================

class TestRumbleExtractorURL(unittest.TestCase):
    """Rumble URL matching and channel-ID extraction."""

    def setUp(self):
        self.ext = RumbleExtractor()

    def test_video_url(self):
        self.assertIsNotNone(
            RumbleExtractor.URL_PATTERNS[0].match("https://rumble.com/vabcde-title.html")
        )

    def test_embed_url(self):
        self.assertIsNotNone(
            RumbleExtractor.URL_PATTERNS[1].match("https://rumble.com/embed/vabcde")
        )

    def test_invalid_url(self):
        self.assertIsNone(
            RumbleExtractor.URL_PATTERNS[0].match("https://youtube.com/v123")
        )

    def test_extract_channel_id(self):
        self.assertEqual(
            self.ext.extract_channel_id("https://rumble.com/vabcde-my-video.html"),
            "vabcde",
        )

    def test_extract_channel_id_embed(self):
        self.assertEqual(
            self.ext.extract_channel_id("https://rumble.com/embed/vxyz12"),
            "vxyz12",
        )

    def test_extract_channel_id_invalid(self):
        self.assertIsNone(
            self.ext.extract_channel_id("https://youtube.com/watch")
        )

    def test_supports_vod_listing(self):
        self.assertFalse(self.ext.supports_vod_listing())

    def test_supports_live_check(self):
        self.assertFalse(self.ext.supports_live_check())


class TestRumbleExtractorResolve(unittest.TestCase):
    """Rumble resolve() with mocked page HTML and embed API."""

    def setUp(self):
        self.ext = RumbleExtractor()

    @patch(f"{_RUMBLE}.curl_json")
    @patch(f"{_RUMBLE}.curl")
    def test_resolve_embed_url(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = {
            "title": "My Rumble Video",
            "duration": 600,
            "author": {"slug": "mychannel"},
            "ua": {
                "hls": {
                    "auto": {
                        "url": "https://cdn.rumble.com/auto.m3u8",
                        "meta": {"h": 1080, "w": 1920, "bitrate": 5000000},
                    }
                },
                "mp4": {
                    "720": {
                        "url": "https://cdn.rumble.com/720.mp4",
                        "meta": {"h": 720, "w": 1280, "bitrate": 2500000},
                    }
                },
            },
        }
        info = self.ext.resolve("https://rumble.com/embed/vabcde")
        self.assertIsNotNone(info)
        self.assertEqual(info.platform, "Rumble")
        self.assertEqual(info.title, "My Rumble Video")
        self.assertEqual(info.channel, "mychannel")
        self.assertEqual(len(info.qualities), 2)
        self.assertFalse(info.is_live)
        self.assertEqual(info.total_secs, 600)

    @patch(f"{_RUMBLE}.curl_json")
    @patch(f"{_RUMBLE}.curl")
    def test_resolve_page_url_scrapes_embed_id(self, mock_curl, mock_curl_json):
        mock_curl.return_value = (
            '<html><div data-rumble="embed/vxyz99"></div></html>'
        )
        mock_curl_json.return_value = {
            "title": "Page Video",
            "duration": 300,
            "channel": "somechannel",
            "ua": {
                "mp4": {
                    "360": {
                        "url": "https://cdn.rumble.com/360.mp4",
                        "meta": {"h": 360, "w": 640, "bitrate": 800000},
                    }
                },
                "hls": {},
            },
        }
        info = self.ext.resolve("https://rumble.com/vxyz99-my-video.html")
        self.assertIsNotNone(info)
        self.assertEqual(info.title, "Page Video")

    @patch(f"{_RUMBLE}.curl_json")
    @patch(f"{_RUMBLE}.curl")
    def test_resolve_no_embed_id_in_page(self, mock_curl, mock_curl_json):
        mock_curl.return_value = "<html>No embed here</html>"
        info = self.ext.resolve("https://rumble.com/vnone-missing.html")
        self.assertIsNone(info)

    @patch(f"{_RUMBLE}.curl_json")
    @patch(f"{_RUMBLE}.curl")
    def test_resolve_api_returns_none(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = None
        info = self.ext.resolve("https://rumble.com/embed/vabcde")
        self.assertIsNone(info)

    @patch(f"{_RUMBLE}.curl_json")
    @patch(f"{_RUMBLE}.curl")
    def test_resolve_api_returns_non_dict(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = "not a dict"
        info = self.ext.resolve("https://rumble.com/embed/vabcde")
        self.assertIsNone(info)

    @patch(f"{_RUMBLE}.curl_json")
    @patch(f"{_RUMBLE}.curl")
    def test_resolve_live_stream(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = {
            "title": "Live Now",
            "duration": 0,
            "author": "livechannel",
            "ua": {
                "hls": {
                    "live": {
                        "url": "https://cdn.rumble.com/live.m3u8",
                        "meta": {"h": 1080, "w": 1920, "bitrate": 6000000},
                    }
                },
                "mp4": {},
            },
        }
        # Rumble probes HLS when duration==0 and qualities exist.
        mock_curl.return_value = None
        info = self.ext.resolve("https://rumble.com/embed/vlive1")
        self.assertIsNotNone(info)
        self.assertTrue(info.is_live)
        self.assertEqual(info.duration_str, "Live")

    @patch(f"{_RUMBLE}.curl_json")
    @patch(f"{_RUMBLE}.curl")
    def test_resolve_channel_extraction_nested(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = {
            "title": "Test",
            "duration": 120,
            "author": {"name": "AuthorName"},
            "ua": {"hls": {}, "mp4": {}},
        }
        info = self.ext.resolve("https://rumble.com/embed/vtest1")
        self.assertIsNotNone(info)
        self.assertEqual(info.channel, "AuthorName")


# ===================================================================
# SoundCloudExtractor
# ===================================================================

class TestSoundCloudExtractorURL(unittest.TestCase):
    """SoundCloud URL matching and channel-ID extraction."""

    def setUp(self):
        self.ext = SoundCloudExtractor()

    def test_track_url(self):
        self.assertIsNotNone(
            SoundCloudExtractor.URL_PATTERNS[0].match(
                "https://soundcloud.com/artist/track-name"
            )
        )

    def test_set_url(self):
        self.assertIsNotNone(
            SoundCloudExtractor.URL_PATTERNS[1].match(
                "https://soundcloud.com/artist/sets/playlist-name"
            )
        )

    def test_invalid_url(self):
        self.assertIsNone(
            SoundCloudExtractor.URL_PATTERNS[0].match(
                "https://youtube.com/artist/track"
            )
        )

    def test_extract_channel_id(self):
        self.assertEqual(
            self.ext.extract_channel_id("https://soundcloud.com/deadmau5/some-track"),
            "deadmau5",
        )

    def test_extract_channel_id_invalid(self):
        self.assertIsNone(
            self.ext.extract_channel_id("https://example.com/")
        )

    def test_supports_vod_listing(self):
        self.assertFalse(self.ext.supports_vod_listing())

    def test_supports_live_check(self):
        self.assertFalse(self.ext.supports_live_check())


class TestSoundCloudExtractorResolve(unittest.TestCase):
    """SoundCloud resolve() with mocked client_id extraction and API."""

    def setUp(self):
        self.ext = SoundCloudExtractor()
        SoundCloudExtractor._client_id = None

    @patch(f"{_SOUNDCLOUD}.curl_json")
    @patch(f"{_SOUNDCLOUD}.curl")
    def test_resolve_track(self, mock_curl, mock_curl_json):
        page_html = (
            '<html><script src="https://a-v2.sndcdn.com/assets/app123.js"></script></html>'
        )
        js_body = 'var config={client_id:"fakeClientId123"};'
        mock_curl.side_effect = [page_html, js_body]

        mock_curl_json.side_effect = [
            {
                "title": "Test Track",
                "duration": 180000,
                "media": {
                    "transcodings": [
                        {
                            "url": "https://api-v2.soundcloud.com/media/abc/stream",
                            "format": {
                                "protocol": "progressive",
                                "mime_type": "audio/mpeg",
                            },
                        },
                    ]
                },
            },
            {"url": "https://cf-media.sndcdn.com/stream.mp3"},
        ]
        info = self.ext.resolve("https://soundcloud.com/artist/cool-track")
        self.assertIsNotNone(info)
        self.assertEqual(info.platform, "SoundCloud")
        self.assertEqual(info.title, "Test Track")
        self.assertEqual(len(info.qualities), 1)
        self.assertEqual(info.qualities[0].format_type, "mp4")
        self.assertEqual(info.channel, "artist")

    @patch(f"{_SOUNDCLOUD}.curl_json")
    @patch(f"{_SOUNDCLOUD}.curl")
    def test_resolve_hls_transcoding(self, mock_curl, mock_curl_json):
        SoundCloudExtractor._client_id = "cached_id"
        mock_curl_json.side_effect = [
            {
                "title": "HLS Track",
                "duration": 60000,
                "media": {
                    "transcodings": [
                        {
                            "url": "https://api-v2.soundcloud.com/media/xyz/stream",
                            "format": {
                                "protocol": "hls",
                                "mime_type": "audio/mpeg",
                            },
                        },
                    ]
                },
            },
            {"url": "https://cf-hls.sndcdn.com/playlist.m3u8"},
        ]
        info = self.ext.resolve("https://soundcloud.com/artist/hls-track")
        self.assertIsNotNone(info)
        self.assertEqual(info.qualities[0].format_type, "hls")

    @patch(f"{_SOUNDCLOUD}.curl_json")
    @patch(f"{_SOUNDCLOUD}.curl")
    def test_resolve_no_client_id(self, mock_curl, mock_curl_json):
        mock_curl.return_value = "<html>No scripts here</html>"
        info = self.ext.resolve("https://soundcloud.com/artist/track")
        self.assertIsNone(info)

    @patch(f"{_SOUNDCLOUD}.curl_json")
    @patch(f"{_SOUNDCLOUD}.curl")
    def test_resolve_api_failure_retries_client_id(self, mock_curl, mock_curl_json):
        SoundCloudExtractor._client_id = "stale_id"
        page_html = '<html><script src="https://a-v2.sndcdn.com/assets/new.js"></script></html>'
        js_body = 'client_id:"freshId999"'
        mock_curl.side_effect = [page_html, js_body]
        mock_curl_json.side_effect = [
            None,
            {
                "title": "Retried",
                "duration": 60000,
                "media": {"transcodings": []},
            },
        ]
        info = self.ext.resolve("https://soundcloud.com/artist/track")
        self.assertIsNotNone(info)
        self.assertEqual(info.title, "Retried")

    @patch(f"{_SOUNDCLOUD}.curl_json")
    @patch(f"{_SOUNDCLOUD}.curl")
    def test_resolve_both_retries_fail(self, mock_curl, mock_curl_json):
        SoundCloudExtractor._client_id = "stale_id"
        mock_curl.return_value = "<html>No JS</html>"
        mock_curl_json.side_effect = [None, None]
        info = self.ext.resolve("https://soundcloud.com/artist/track")
        self.assertIsNone(info)


# ===================================================================
# RedditExtractor
# ===================================================================

class TestRedditExtractorURL(unittest.TestCase):
    """Reddit URL matching and subreddit extraction."""

    def setUp(self):
        self.ext = RedditExtractor()

    def test_post_url(self):
        self.assertIsNotNone(
            RedditExtractor.URL_PATTERNS[0].match(
                "https://www.reddit.com/r/gaming/comments/abc123"
            )
        )

    def test_old_reddit_url(self):
        self.assertIsNotNone(
            RedditExtractor.URL_PATTERNS[0].match(
                "https://old.reddit.com/r/pics/comments/xyz789"
            )
        )

    def test_v_redd_it_url(self):
        self.assertIsNotNone(
            RedditExtractor.URL_PATTERNS[1].match("https://v.redd.it/abcdef123")
        )

    def test_invalid_url(self):
        self.assertIsNone(
            RedditExtractor.URL_PATTERNS[0].match("https://kick.com/something")
        )

    def test_extract_channel_id_subreddit(self):
        self.assertEqual(
            self.ext.extract_channel_id(
                "https://www.reddit.com/r/gaming/comments/abc123"
            ),
            "gaming",
        )

    def test_extract_channel_id_v_redd_it(self):
        self.assertEqual(
            self.ext.extract_channel_id("https://v.redd.it/qwerty"),
            "qwerty",
        )

    def test_supports_vod_listing(self):
        self.assertFalse(self.ext.supports_vod_listing())

    def test_supports_live_check(self):
        self.assertFalse(self.ext.supports_live_check())


class TestRedditExtractorResolve(unittest.TestCase):
    """Reddit resolve() with mocked JSON API."""

    def setUp(self):
        self.ext = RedditExtractor()

    @patch(f"{_REDDIT}.curl_json")
    @patch(f"{_REDDIT}.curl")
    def test_resolve_video_post(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = [
            {
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "Cool clip",
                                "is_video": True,
                                "subreddit": "gaming",
                                "secure_media": {
                                    "reddit_video": {
                                        "fallback_url": "https://v.redd.it/abc/DASH_720.mp4",
                                        "dash_url": "https://v.redd.it/abc/DASHPlaylist.mpd",
                                        "duration": 30,
                                        "height": 720,
                                        "width": 1280,
                                        "bitrate_kbps": 2400,
                                    }
                                },
                            }
                        }
                    ]
                }
            }
        ]
        info = self.ext.resolve("https://www.reddit.com/r/gaming/comments/abc123")
        self.assertIsNotNone(info)
        self.assertEqual(info.platform, "Reddit")
        self.assertEqual(info.title, "Cool clip")
        self.assertEqual(info.channel, "gaming")
        self.assertEqual(len(info.qualities), 2)

    @patch(f"{_REDDIT}.curl_json")
    @patch(f"{_REDDIT}.curl")
    def test_resolve_fallback_only(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = [
            {
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "Fallback",
                                "is_video": True,
                                "subreddit": "test",
                                "secure_media": {
                                    "reddit_video": {
                                        "fallback_url": "https://v.redd.it/x/DASH_480.mp4",
                                        "duration": 15,
                                        "height": 480,
                                    }
                                },
                            }
                        }
                    ]
                }
            }
        ]
        info = self.ext.resolve("https://www.reddit.com/r/test/comments/xyz")
        self.assertIsNotNone(info)
        self.assertEqual(len(info.qualities), 1)
        self.assertIn("fallback", info.qualities[0].name)

    @patch(f"{_REDDIT}.curl_json")
    @patch(f"{_REDDIT}.curl")
    def test_resolve_not_video(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = [
            {
                "data": {
                    "children": [
                        {"data": {"title": "Text post", "is_video": False}}
                    ]
                }
            }
        ]
        info = self.ext.resolve("https://www.reddit.com/r/AskReddit/comments/xyz")
        self.assertIsNone(info)

    @patch(f"{_REDDIT}.curl_json")
    @patch(f"{_REDDIT}.curl")
    def test_resolve_api_returns_none(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = None
        info = self.ext.resolve("https://www.reddit.com/r/test/comments/abc")
        self.assertIsNone(info)

    @patch(f"{_REDDIT}.curl_json")
    @patch(f"{_REDDIT}.curl")
    def test_resolve_empty_list(self, mock_curl, mock_curl_json):
        mock_curl_json.return_value = []
        info = self.ext.resolve("https://www.reddit.com/r/test/comments/abc")
        self.assertIsNone(info)

    @patch(f"{_REDDIT}.curl_json")
    @patch(f"{_REDDIT}.curl")
    def test_resolve_v_redd_it_redirect(self, mock_curl, mock_curl_json):
        # curl for v.redd.it returns HTML with reddit.com link
        mock_curl.return_value = (
            '<html><a href="https://www.reddit.com/r/funny/comments/redir1">link</a></html>'
        )
        mock_curl_json.return_value = [
            {
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "Redirected",
                                "is_video": True,
                                "subreddit": "funny",
                                "secure_media": {
                                    "reddit_video": {
                                        "fallback_url": "https://v.redd.it/z/DASH_720.mp4",
                                        "duration": 10,
                                        "height": 720,
                                    }
                                },
                            }
                        }
                    ]
                }
            }
        ]
        info = self.ext.resolve("https://v.redd.it/someid")
        self.assertIsNotNone(info)
        self.assertEqual(info.channel, "funny")


# ===================================================================
# AudiusExtractor
# ===================================================================

class TestAudiusExtractorURL(unittest.TestCase):
    """Audius URL matching and artist extraction."""

    def setUp(self):
        self.ext = AudiusExtractor()

    def test_track_url(self):
        self.assertIsNotNone(
            AudiusExtractor.URL_PATTERNS[0].match(
                "https://audius.co/artist/track-title"
            )
        )

    def test_invalid_url(self):
        self.assertIsNone(
            AudiusExtractor.URL_PATTERNS[0].match(
                "https://soundcloud.com/artist/track"
            )
        )

    def test_extract_channel_id(self):
        self.assertEqual(
            self.ext.extract_channel_id("https://audius.co/deadmau5/something"),
            "deadmau5",
        )

    def test_extract_channel_id_invalid(self):
        self.assertIsNone(
            self.ext.extract_channel_id("https://example.com/")
        )

    def test_supports_vod_listing(self):
        self.assertFalse(self.ext.supports_vod_listing())

    def test_supports_live_check(self):
        self.assertFalse(self.ext.supports_live_check())


class TestAudiusExtractorResolve(unittest.TestCase):
    """Audius resolve() with mocked discovery API."""

    def setUp(self):
        self.ext = AudiusExtractor()

    @patch(f"{_AUDIUS}.curl_json")
    def test_resolve_track(self, mock_curl_json):
        mock_curl_json.return_value = {
            "data": {
                "id": "track123",
                "title": "Chill Beats",
                "duration": 240,
            }
        }
        info = self.ext.resolve("https://audius.co/artist/chill-beats")
        self.assertIsNotNone(info)
        self.assertEqual(info.platform, "Audius")
        self.assertEqual(info.title, "Chill Beats")
        self.assertEqual(info.total_secs, 240)
        self.assertEqual(len(info.qualities), 1)
        self.assertIn("stream", info.qualities[0].name)
        self.assertIn("track123", info.qualities[0].url)

    @patch(f"{_AUDIUS}.curl_json")
    def test_resolve_api_empty_data(self, mock_curl_json):
        mock_curl_json.return_value = {"data": None}
        info = self.ext.resolve("https://audius.co/artist/missing")
        self.assertIsNone(info)

    @patch(f"{_AUDIUS}.curl_json")
    def test_resolve_api_returns_none(self, mock_curl_json):
        mock_curl_json.return_value = None
        info = self.ext.resolve("https://audius.co/artist/gone")
        self.assertIsNone(info)

    @patch(f"{_AUDIUS}.curl_json")
    def test_resolve_api_missing_data_key(self, mock_curl_json):
        mock_curl_json.return_value = {"error": "not found"}
        info = self.ext.resolve("https://audius.co/artist/notfound")
        self.assertIsNone(info)


# ===================================================================
# PodcastRSSExtractor
# ===================================================================

class TestPodcastRSSExtractorURL(unittest.TestCase):
    """Podcast RSS URL matching and channel-ID extraction."""

    def setUp(self):
        self.ext = PodcastRSSExtractor()

    def test_rss_url(self):
        self.assertIsNotNone(
            PodcastRSSExtractor.URL_PATTERNS[0].match(
                "https://feeds.example.com/podcast.rss"
            )
        )

    def test_xml_url(self):
        self.assertIsNotNone(
            PodcastRSSExtractor.URL_PATTERNS[0].match(
                "https://feeds.example.com/show.xml"
            )
        )

    def test_feed_url(self):
        self.assertIsNotNone(
            PodcastRSSExtractor.URL_PATTERNS[1].match(
                "https://example.com/podcast/feed/"
            )
        )

    def test_rss_path_url(self):
        self.assertIsNotNone(
            PodcastRSSExtractor.URL_PATTERNS[2].match(
                "https://example.com/podcast/rss"
            )
        )

    def test_invalid_url(self):
        self.assertIsNone(
            PodcastRSSExtractor.URL_PATTERNS[0].match(
                "https://youtube.com/watch?v=abc"
            )
        )

    def test_extract_channel_id(self):
        self.assertEqual(
            self.ext.extract_channel_id("https://feeds.example.com/pod.rss"),
            "feeds_example_com",
        )

    def test_supports_vod_listing(self):
        self.assertTrue(self.ext.supports_vod_listing())

    def test_supports_live_check(self):
        self.assertFalse(self.ext.supports_live_check())


class TestPodcastRSSExtractorResolve(unittest.TestCase):
    """Podcast resolve() and list_vods() with mocked RSS feed."""

    def setUp(self):
        self.ext = PodcastRSSExtractor()

    @patch(f"{_PODCAST}.curl")
    def test_list_vods_parses_episodes(self, mock_curl):
        mock_curl.return_value = """<?xml version="1.0" encoding="UTF-8"?>
<rss><channel>
<title>My Podcast</title>
<item>
  <title>Episode 1</title>
  <pubDate>Mon, 01 Jan 2024 08:00:00 GMT</pubDate>
  <enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg"/>
  <itunes:duration>01:30:00</itunes:duration>
</item>
<item>
  <title><![CDATA[Episode 2 & More]]></title>
  <pubDate>Mon, 08 Jan 2024 08:00:00 GMT</pubDate>
  <enclosure url="https://cdn.example.com/ep2.mp3" type="audio/mpeg"/>
  <itunes:duration>45:30</itunes:duration>
</item>
</channel></rss>"""
        vods, cursor = self.ext.list_vods("https://example.com/podcast.rss")
        self.assertEqual(len(vods), 2)
        self.assertEqual(vods[0].title, "Episode 1")
        self.assertEqual(vods[0].duration, "01h 30m")
        self.assertEqual(vods[1].title, "Episode 2 & More")
        self.assertEqual(vods[1].duration, "45m 30s")
        self.assertEqual(vods[0].platform, "Podcast")
        self.assertIsNone(cursor)

    @patch(f"{_PODCAST}.curl")
    def test_list_vods_single_number_duration(self, mock_curl):
        mock_curl.return_value = """<rss><channel>
<item>
  <title>Short</title>
  <enclosure url="https://cdn.example.com/short.mp3" type="audio/mpeg"/>
  <itunes:duration>120</itunes:duration>
</item>
</channel></rss>"""
        vods, cursor = self.ext.list_vods("https://example.com/pod.rss")
        self.assertEqual(len(vods), 1)
        self.assertEqual(vods[0].duration, "120s")

    @patch(f"{_PODCAST}.curl")
    def test_list_vods_empty_feed(self, mock_curl):
        mock_curl.return_value = '<?xml version="1.0"?><rss><channel></channel></rss>'
        vods, cursor = self.ext.list_vods("https://example.com/podcast.rss")
        self.assertEqual(vods, [])

    @patch(f"{_PODCAST}.curl")
    def test_list_vods_fetch_failure(self, mock_curl):
        mock_curl.return_value = None
        vods, cursor = self.ext.list_vods("https://example.com/podcast.rss")
        self.assertEqual(vods, [])

    @patch(f"{_PODCAST}.curl")
    def test_list_vods_no_enclosure_skipped(self, mock_curl):
        mock_curl.return_value = """<rss><channel>
<item><title>No audio</title></item>
<item>
  <title>Has audio</title>
  <enclosure url="https://cdn.example.com/has.mp3" type="audio/mpeg"/>
</item>
</channel></rss>"""
        vods, _ = self.ext.list_vods("https://example.com/pod.rss")
        self.assertEqual(len(vods), 1)
        self.assertEqual(vods[0].title, "Has audio")

    def test_resolve_direct_audio_mp3(self):
        info = self.ext.resolve("https://cdn.example.com/episode.mp3")
        self.assertIsNotNone(info)
        self.assertEqual(info.platform, "Podcast")
        self.assertEqual(len(info.qualities), 1)
        self.assertEqual(info.qualities[0].resolution, "audio")

    def test_resolve_direct_audio_m4a(self):
        info = self.ext.resolve("https://cdn.example.com/episode.m4a")
        self.assertIsNotNone(info)

    def test_resolve_rss_feed_url_returns_none(self):
        info = self.ext.resolve("https://example.com/podcast.rss")
        self.assertIsNone(info)


# ===================================================================
# YtDlpExtractor
# ===================================================================

class TestYtDlpExtractorURL(unittest.TestCase):
    """yt-dlp catch-all URL matching and channel-ID extraction."""

    def setUp(self):
        self.ext = YtDlpExtractor()

    def test_catches_any_http_url(self):
        self.assertIsNotNone(
            YtDlpExtractor.URL_PATTERNS[0].match("https://www.youtube.com/watch?v=abc")
        )

    def test_catches_vimeo(self):
        self.assertIsNotNone(
            YtDlpExtractor.URL_PATTERNS[0].match("https://vimeo.com/123456")
        )

    def test_no_match_non_http(self):
        self.assertIsNone(
            YtDlpExtractor.URL_PATTERNS[0].match("ftp://files.example.com/video.mp4")
        )

    def test_extract_channel_id_youtube(self):
        cid = self.ext.extract_channel_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("youtube.com", cid)

    def test_extract_channel_id_fallback(self):
        cid = self.ext.extract_channel_id("https://example.com/")
        self.assertEqual(cid, "example.com")

    def test_supports_vod_listing(self):
        self.assertFalse(self.ext.supports_vod_listing())

    def test_supports_live_check(self):
        self.assertFalse(self.ext.supports_live_check())


class TestYtDlpRuntimeReadiness(unittest.TestCase):
    """yt-dlp version, EJS, and JavaScript runtime readiness helpers."""

    @staticmethod
    def _registry(*, yt=True, ejs=True, javascript=True, runtime="deno"):
        def record(name, supported, *, version="", path="", display=""):
            return {
                "name": name,
                "display_name": display or name,
                "path": path,
                "version": version,
                "minimum": "",
                "provenance": "test-fixture" if supported else "missing",
                "available": supported,
                "supported": supported,
                "capabilities": [],
                "command": [path] if path else [],
                "repair": f"Repair {display or name}.",
                "detail": "",
                "state": "ready" if supported else "missing",
            }

        yt_record = record(
            "yt_dlp", yt, version="2026.07.04" if yt else "",
            path=r"C:\Python\yt_dlp\__init__.py", display="yt-dlp",
        )
        ejs_record = record(
            "yt_dlp_ejs", ejs, version="0.8.0" if ejs else "",
            path=r"C:\Python\yt_dlp_ejs\__init__.py", display="yt-dlp-ejs",
        )
        ejs_record["required_by_ytdlp"] = "==0.8.0"
        js_record = record(
            "javascript", javascript, version="22.3.0" if runtime == "node" else "2.7.11",
            path=rf"C:\Tools\{runtime}.exe", display=runtime,
        )
        js_record["runtime"] = runtime if javascript else ""
        youtube_ready = yt and ejs and javascript
        youtube = record("youtube", youtube_ready, display="YouTube support")
        youtube["available"] = yt
        youtube["detail"] = (
            "Full YouTube support is ready."
            if youtube_ready else "YouTube components need repair."
        )
        return {
            "yt_dlp": yt_record,
            "yt_dlp_ejs": ejs_record,
            "javascript": js_record,
            "youtube": youtube,
        }

    @patch("streamkeep.capabilities.resolve_command_prefix")
    def test_command_uses_registry_command(self, mock_resolve):
        mock_resolve.return_value = [r"C:\Apps\StreamKeep.exe", "--internal-ytdlp"]
        self.assertEqual(ytdlp_mod.ytdlp_command(), mock_resolve.return_value)
        mock_resolve.assert_called_once_with("yt_dlp")

    @patch("streamkeep.capabilities.get_runtime_capabilities")
    def test_status_reads_registry_without_spawning_app(self, mock_registry):
        mock_registry.return_value = self._registry()
        status = ytdlp_mod.ytdlp_runtime_status()

        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["yt_dlp_version"], "2026.07.04")
        self.assertEqual(status["yt_dlp_path"], r"C:\Python\yt_dlp\__init__.py")

    def test_parse_version_parts_handles_cli_formats(self):
        self.assertEqual(ytdlp_mod._parse_version_parts("2026.06.09")[0], (2026, 6, 9))
        self.assertEqual(ytdlp_mod._parse_version_parts("v22.3.0")[0], (22, 3, 0))
        self.assertEqual(ytdlp_mod._parse_version_parts("deno 2.3.1 (stable)")[0], (2, 3, 1))

    @patch("streamkeep.capabilities.get_runtime_capabilities")
    def test_status_reports_missing_ytdlp_with_actionable_hint(self, mock_registry):
        mock_registry.return_value = self._registry(yt=False, ejs=False, javascript=False)
        status = ytdlp_mod.ytdlp_runtime_status()

        self.assertEqual(status["state"], "missing")
        self.assertIn("repair", status["detail"].lower())

    @patch("streamkeep.capabilities.get_runtime_capabilities")
    def test_status_reports_missing_ejs_and_js_runtime(self, mock_registry):
        mock_registry.return_value = self._registry(ejs=False, javascript=False)
        status = ytdlp_mod.ytdlp_runtime_status()

        self.assertEqual(status["state"], "limited")
        self.assertIn("yt-dlp-ejs", " ".join(status["problems"]))
        self.assertIn("deno", " ".join(status["problems"]).lower())

    @patch("streamkeep.capabilities.get_runtime_capabilities")
    def test_status_accepts_node_runtime_and_builds_runtime_args(self, mock_registry):
        mock_registry.return_value = self._registry(runtime="node")
        status = ytdlp_mod.ytdlp_runtime_status()

        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["js_runtime"]["name"], "node")
        self.assertEqual(
            ytdlp_mod.ytdlp_runtime_args(status),
            ["--no-js-runtimes", "--js-runtimes", r"node:C:\Tools\node.exe"],
        )

    @patch(f"{_YTDLP}.ytdlp_runtime_status")
    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_logs_youtube_runtime_warning(self, mock_run, mock_status):
        ext = YtDlpExtractor()
        mock_status.return_value = {
            "state": "limited",
            "summary": "Limited",
            "detail": "yt-dlp 2026.06.09 found. Install Deno 2.3+.",
            "js_runtime": {"supported": False, "name": ""},
            "problems": ["Install Deno 2.3+."],
        }
        mock_run.return_value = CommandResult(
            returncode=1, stderr="ERROR: Video unavailable"
        )
        logs = []

        info = ext.resolve("https://www.youtube.com/watch?v=abc", log_fn=logs.append)

        self.assertIsNone(info)
        self.assertTrue(any("yt-dlp runtime support is not ready" in line for line in logs))


class TestYtDlpExtractorResolve(unittest.TestCase):
    """yt-dlp resolve() with mocked subprocess."""

    def setUp(self):
        self.ext = YtDlpExtractor()
        YtDlpExtractor.cookies_browser = ""
        YtDlpExtractor.cookies_file = ""
        YtDlpExtractor.proxy = ""

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_youtube_video(self, mock_run):
        yt_json = {
            "title": "Never Gonna Give You Up",
            "channel": "RickAstleyVEVO",
            "duration": 212,
            "is_live": False,
            "formats": [
                {
                    "format_id": "137",
                    "ext": "mp4",
                    "width": 1920,
                    "height": 1080,
                    "vcodec": "avc1",
                    "acodec": "none",
                    "tbr": 4000,
                    "format_note": "1080p",
                    "url": "https://rr.google.com/videoplayback?id=137",
                },
                {
                    "format_id": "140",
                    "ext": "m4a",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "abr": 128,
                    "url": "https://rr.google.com/videoplayback?id=140",
                },
            ],
        }
        mock_run.return_value = CommandResult(
            returncode=0, stdout=json.dumps(yt_json)
        )
        info = self.ext.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIsNotNone(info)
        self.assertEqual(info.platform, "yt-dlp")
        self.assertEqual(info.title, "Never Gonna Give You Up")
        self.assertEqual(info.channel, "RickAstleyVEVO")
        self.assertGreater(len(info.qualities), 0)
        video_q = [q for q in info.qualities if q.resolution != "audio"]
        if video_q:
            self.assertIn("140", video_q[0].ytdlp_format)

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_no_ytdlp_installed(self, mock_run):
        mock_run.return_value = CommandResult(returncode=127, stderr="not found")
        with patch.object(self.ext, "_has_ytdlp", return_value=False):
            info = self.ext.resolve("https://youtube.com/watch?v=abc")
        self.assertIsNone(info)
        mock_run.assert_not_called()

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_ytdlp_error(self, mock_run):
        mock_run.return_value = CommandResult(
            returncode=1, stderr="ERROR: Video unavailable"
        )
        info = self.ext.resolve("https://youtube.com/watch?v=deleted")
        self.assertIsNone(info)

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_bad_json(self, mock_run):
        mock_run.return_value = CommandResult(returncode=0, stdout="not json at all")
        info = self.ext.resolve("https://youtube.com/watch?v=badjson")
        self.assertIsNone(info)

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_interrupted(self, mock_run):
        mock_run.return_value = CommandResult(interrupted=True)
        info = self.ext.resolve("https://youtube.com/watch?v=int")
        self.assertIsNone(info)

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_timed_out(self, mock_run):
        mock_run.return_value = CommandResult(timed_out=True)
        info = self.ext.resolve("https://youtube.com/watch?v=slow")
        self.assertIsNone(info)

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_filters_zero_resolution(self, mock_run):
        yt_json = {
            "title": "Test",
            "duration": 60,
            "formats": [
                {
                    "format_id": "999",
                    "ext": "mp4",
                    "width": 0,
                    "height": 0,
                    "vcodec": "avc1",
                    "acodec": "aac",
                    "tbr": 100,
                    "url": "https://example.com/0x0.mp4",
                },
                {
                    "format_id": "137",
                    "ext": "mp4",
                    "width": 1920,
                    "height": 1080,
                    "vcodec": "avc1",
                    "acodec": "aac",
                    "tbr": 4000,
                    "url": "https://example.com/1080.mp4",
                },
            ],
        }
        mock_run.return_value = CommandResult(
            returncode=0, stdout=json.dumps(yt_json)
        )
        info = self.ext.resolve("https://youtube.com/watch?v=filter")
        self.assertIsNotNone(info)
        resolutions = [q.resolution for q in info.qualities]
        self.assertNotIn("0x0", resolutions)

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_chapters_extracted(self, mock_run):
        yt_json = {
            "title": "Chapters Video",
            "duration": 600,
            "formats": [
                {
                    "format_id": "18",
                    "ext": "mp4",
                    "width": 640,
                    "height": 360,
                    "vcodec": "avc1",
                    "acodec": "aac",
                    "tbr": 500,
                    "url": "https://example.com/360.mp4",
                },
            ],
            "chapters": [
                {"title": "Intro", "start_time": 0, "end_time": 60},
                {"title": "Main", "start_time": 60, "end_time": 500},
            ],
        }
        mock_run.return_value = CommandResult(
            returncode=0, stdout=json.dumps(yt_json)
        )
        info = self.ext.resolve("https://youtube.com/watch?v=chapters")
        self.assertIsNotNone(info)
        self.assertEqual(len(info.chapters), 2)
        self.assertEqual(info.chapters[0]["title"], "Intro")

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_merges_manual_and_automatic_subtitle_listing(self, mock_run):
        yt_json = {
            "title": "Subtitle Video",
            "duration": 60,
            "formats": [{
                "format_id": "18", "ext": "mp4", "width": 640,
                "height": 360, "vcodec": "avc1", "acodec": "aac",
                "tbr": 500, "url": "https://example.com/video.mp4",
            }],
            "subtitles": {
                "en": [{"ext": "vtt", "name": "English"}],
                "es": [{"ext": "srt", "name": "Spanish"}],
            },
            "automatic_captions": {
                "en": [{"ext": "json3", "name": "English"}],
                "fr": [{"ext": "vtt", "name": "French"}],
            },
        }
        mock_run.return_value = CommandResult(
            returncode=0, stdout=json.dumps(yt_json)
        )

        info = self.ext.resolve("https://youtube.com/watch?v=subtitles")

        tracks = {track.language: track for track in info.subtitles}
        self.assertEqual(set(tracks), {"en", "es", "fr"})
        self.assertTrue(tracks["en"].manual)
        self.assertTrue(tracks["en"].automatic)
        self.assertEqual(tracks["en"].formats, ["vtt", "json3"])
        self.assertTrue(tracks["es"].manual)
        self.assertFalse(tracks["es"].automatic)
        self.assertFalse(tracks["fr"].manual)
        self.assertTrue(tracks["fr"].automatic)

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_live_flag(self, mock_run):
        yt_json = {
            "title": "Live Stream",
            "is_live": True,
            "formats": [],
        }
        mock_run.return_value = CommandResult(
            returncode=0, stdout=json.dumps(yt_json)
        )
        info = self.ext.resolve("https://youtube.com/watch?v=live")
        self.assertIsNotNone(info)
        self.assertTrue(info.is_live)

    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_resolve_auth_error_triggers_browser_scan(self, mock_run):
        mock_run.return_value = CommandResult(
            returncode=1, stderr="ERROR: Sign in to confirm your age"
        )
        with patch(f"{_YTDLP}.scan_browser_cookies", return_value=[]):
            info = self.ext.resolve("https://youtube.com/watch?v=agelock")
        self.assertIsNone(info)

    @patch(f"{_YTDLP}.ytdlp_command", return_value=["yt-dlp"])
    @patch(f"{_YTDLP}.run_capture_interruptible")
    def test_playlist_probe_builds_range_filter_and_archive_flags(
        self, mock_run, _mock_command
    ):
        mock_run.return_value = CommandResult(
            returncode=0,
            stdout=json.dumps({
                "_type": "playlist",
                "entries": [{
                    "id": "video-2", "extractor_key": "Youtube",
                    "url": "video-2", "title": "Second",
                }],
            }),
        )
        with patch.object(self.ext, "_has_ytdlp", return_value=True):
            entries = self.ext.list_playlist_entries(
                "https://example.com/playlist",
                playlist_items="2:5", date_after="20260101",
                date_before="20261231", match_filter="duration > 60",
                max_downloads=3, archive_path="C:/archives/source.txt",
                break_on_existing=True,
            )

        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--playlist-items") + 1], "2:5")
        self.assertEqual(cmd[cmd.index("--dateafter") + 1], "20260101")
        self.assertEqual(cmd[cmd.index("--datebefore") + 1], "20261231")
        self.assertEqual(
            cmd[cmd.index("--match-filters") + 1], "duration > 60"
        )
        self.assertEqual(cmd[cmd.index("--max-downloads") + 1], "3")
        self.assertEqual(
            cmd[cmd.index("--download-archive") + 1],
            "C:/archives/source.txt",
        )
        self.assertIn("--break-on-existing", cmd)
        self.assertEqual(entries[0]["id"], "video-2")


# ===================================================================
# Extractor.detect() Registry
# ===================================================================

class TestExtractorDetectRegistry(unittest.TestCase):
    """Extractor.detect() must route URLs to the correct extractor class."""

    def test_detect_kick(self):
        ext = Extractor.detect("https://kick.com/xqc")
        self.assertIsInstance(ext, KickExtractor)

    def test_detect_twitch_channel(self):
        ext = Extractor.detect("https://twitch.tv/shroud")
        self.assertIsInstance(ext, TwitchExtractor)

    def test_detect_twitch_vod(self):
        ext = Extractor.detect("https://twitch.tv/videos/123456789")
        self.assertIsInstance(ext, TwitchExtractor)

    def test_detect_rumble(self):
        ext = Extractor.detect("https://rumble.com/vabcde-title.html")
        self.assertIsInstance(ext, RumbleExtractor)

    def test_detect_soundcloud(self):
        ext = Extractor.detect("https://soundcloud.com/artist/track")
        self.assertIsInstance(ext, SoundCloudExtractor)

    def test_detect_reddit(self):
        ext = Extractor.detect(
            "https://www.reddit.com/r/gaming/comments/abc123"
        )
        self.assertIsInstance(ext, RedditExtractor)

    def test_detect_audius(self):
        ext = Extractor.detect("https://audius.co/artist/track-name")
        self.assertIsInstance(ext, AudiusExtractor)

    def test_detect_podcast_rss(self):
        ext = Extractor.detect("https://feeds.example.com/podcast.rss")
        self.assertIsInstance(ext, PodcastRSSExtractor)

    def test_detect_podcast_xml(self):
        ext = Extractor.detect("https://feeds.example.com/show.xml")
        self.assertIsInstance(ext, PodcastRSSExtractor)

    def test_detect_podcast_feed_path(self):
        ext = Extractor.detect("https://example.com/my-show/feed/")
        self.assertIsInstance(ext, PodcastRSSExtractor)

    def test_detect_ytdlp_fallback(self):
        ext = Extractor.detect("https://www.dailymotion.com/video/x8abc")
        self.assertIsInstance(ext, YtDlpExtractor)

    def test_detect_none_for_empty(self):
        self.assertIsNone(Extractor.detect(""))

    def test_detect_none_for_none(self):
        self.assertIsNone(Extractor.detect(None))

    def test_detect_none_for_nonsense(self):
        self.assertIsNone(Extractor.detect("not a url"))

    def test_detect_none_for_non_string(self):
        self.assertIsNone(Extractor.detect(42))

    def test_detect_whitespace_stripped(self):
        ext = Extractor.detect("  https://kick.com/xqc  ")
        self.assertIsInstance(ext, KickExtractor)

    def test_all_names_populated(self):
        names = Extractor.all_names()
        self.assertIn("Kick", names)
        self.assertIn("Twitch", names)
        self.assertIn("Rumble", names)
        self.assertIn("SoundCloud", names)
        self.assertIn("Reddit", names)
        self.assertIn("Audius", names)
        self.assertIn("Podcast", names)
        self.assertIn("yt-dlp", names)


# ===================================================================
# Base Extractor abstract interface
# ===================================================================

class TestExtractorBase(unittest.TestCase):
    """Base Extractor class defaults."""

    def test_resolve_raises(self):
        ext = Extractor()
        with self.assertRaises(NotImplementedError):
            ext.resolve("https://example.com")

    def test_list_vods_default(self):
        ext = Extractor()
        vods, cursor = ext.list_vods("https://example.com")
        self.assertEqual(vods, [])
        self.assertIsNone(cursor)

    def test_supports_flags_default_false(self):
        ext = Extractor()
        self.assertFalse(ext.supports_vod_listing())
        self.assertFalse(ext.supports_live_check())

    def test_check_live_default_none(self):
        ext = Extractor()
        self.assertIsNone(ext.check_live("https://example.com"))

    def test_extract_channel_id_default_none(self):
        ext = Extractor()
        self.assertIsNone(ext.extract_channel_id("https://example.com"))

    def test_log_helper_calls_fn(self):
        ext = Extractor()
        captured = []
        ext._log(captured.append, "hello")
        self.assertEqual(captured, ["hello"])

    def test_log_helper_none_fn(self):
        ext = Extractor()
        ext._log(None, "hello")


if __name__ == "__main__":
    unittest.main()
