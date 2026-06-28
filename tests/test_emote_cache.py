"""Tests for the BTTV/FFZ/7TV emote cache module."""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    importlib.import_module("PyQt6.QtCore")
except ImportError:
    for _mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui"):
        if _mod not in sys.modules:
            sys.modules[_mod] = MagicMock()

from streamkeep.postprocess.emote_cache import (
    fetch_bttv_emotes,
    fetch_ffz_emotes,
    fetch_7tv_emotes,
    load_channel_emotes,
    get_emote_image,
)

_MOD = "streamkeep.postprocess.emote_cache"


class TestBTTVEmotes(unittest.TestCase):
    @patch(f"{_MOD}._fetch_json")
    def test_global_and_channel_emotes(self, mock_fetch):
        mock_fetch.side_effect = [
            [{"code": "monkaS", "id": "bttv1"}],
            {
                "channelEmotes": [{"code": "catJAM", "id": "bttv2"}],
                "sharedEmotes": [{"code": "widepeepoHappy", "id": "bttv3"}],
            },
        ]
        result = fetch_bttv_emotes("12345")
        self.assertEqual(result["monkaS"], ("bttv", "bttv1"))
        self.assertEqual(result["catJAM"], ("bttv", "bttv2"))
        self.assertEqual(result["widepeepoHappy"], ("bttv", "bttv3"))
        self.assertEqual(len(result), 3)

    @patch(f"{_MOD}._fetch_json")
    def test_global_only_no_user_id(self, mock_fetch):
        mock_fetch.return_value = [{"code": "PogChamp", "id": "pg1"}]
        result = fetch_bttv_emotes(None)
        self.assertEqual(len(result), 1)
        mock_fetch.assert_called_once()

    @patch(f"{_MOD}._fetch_json")
    def test_empty_response(self, mock_fetch):
        mock_fetch.return_value = None
        result = fetch_bttv_emotes("12345")
        self.assertEqual(result, {})


class TestFFZEmotes(unittest.TestCase):
    @patch(f"{_MOD}._fetch_json")
    def test_global_and_channel_emotes(self, mock_fetch):
        mock_fetch.side_effect = [
            {"sets": {"1": {"emoticons": [{"name": "LULW", "id": 100}]}}},
            {"sets": {"5": {"emoticons": [{"name": "Pepega", "id": 200}]}}},
        ]
        result = fetch_ffz_emotes("12345")
        self.assertEqual(result["LULW"], ("ffz", "100"))
        self.assertEqual(result["Pepega"], ("ffz", "200"))

    @patch(f"{_MOD}._fetch_json")
    def test_missing_sets(self, mock_fetch):
        mock_fetch.return_value = {}
        result = fetch_ffz_emotes(None)
        self.assertEqual(result, {})


class TestSevenTVEmotes(unittest.TestCase):
    @patch(f"{_MOD}._fetch_json")
    def test_global_and_channel_emotes(self, mock_fetch):
        mock_fetch.side_effect = [
            {"emotes": [{"name": "Aware", "id": "7tv1"}]},
            {"emote_set": {"emotes": [{"name": "Clueless", "id": "7tv2"}]}},
        ]
        result = fetch_7tv_emotes("12345")
        self.assertEqual(result["Aware"], ("7tv", "7tv1"))
        self.assertEqual(result["Clueless"], ("7tv", "7tv2"))


class TestLoadChannelEmotes(unittest.TestCase):
    @patch(f"{_MOD}.fetch_7tv_emotes")
    @patch(f"{_MOD}.fetch_ffz_emotes")
    @patch(f"{_MOD}.fetch_bttv_emotes")
    def test_combines_all_providers(self, mock_bttv, mock_ffz, mock_7tv):
        mock_bttv.return_value = {"emoteA": ("bttv", "1")}
        mock_ffz.return_value = {"emoteB": ("ffz", "2")}
        mock_7tv.return_value = {"emoteC": ("7tv", "3")}
        result = load_channel_emotes("12345")
        self.assertEqual(len(result), 3)
        self.assertIn("emoteA", result)
        self.assertIn("emoteB", result)
        self.assertIn("emoteC", result)

    @patch(f"{_MOD}.fetch_7tv_emotes", side_effect=Exception("timeout"))
    @patch(f"{_MOD}.fetch_ffz_emotes", return_value={})
    @patch(f"{_MOD}.fetch_bttv_emotes", return_value={"ok": ("bttv", "1")})
    def test_partial_failure_still_returns(self, *_):
        result = load_channel_emotes("12345")
        self.assertIn("ok", result)


class TestGetEmoteImage(unittest.TestCase):
    @patch(f"{_MOD}._download")
    @patch(f"{_MOD}.EMOTE_CACHE_DIR")
    def test_downloads_and_caches(self, mock_dir, mock_dl):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_dir.__truediv__ = lambda s, n: Path(tmpdir) / n
            mock_dir.mkdir = MagicMock()
            dest = Path(tmpdir) / "bttv_abc.png"
            dest.write_bytes(b"fake-png")
            mock_dl.return_value = True
            result = get_emote_image("bttv", "abc")
            self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
