"""Pytest-native tests for streamkeep.models — demonstrates fixtures + parametrize."""

import importlib
import sys
from unittest.mock import MagicMock

# Stub PyQt6 before any streamkeep import only when the real GUI toolkit is
# unavailable. Keeping real PyQt6 modules prevents full-suite QThread tests
# from importing MagicMock-backed worker classes.
try:
    importlib.import_module("PyQt6.QtCore")
except ImportError:
    for mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui"):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

import pytest

from streamkeep.models import HistoryEntry, QualityInfo, StreamInfo, VODInfo


@pytest.fixture
def sample_history_entry():
    return HistoryEntry(
        date="2026-06-15",
        platform="Twitch",
        title="Test Stream",
        channel="testuser",
        quality="1080p",
        size="2.5 GB",
        path="/tmp/test",
        url="https://twitch.tv/videos/123",
        favorite=True,
        watched=False,
        watch_position_secs=120.5,
        bookmarks=[{"name": "highlight", "secs": 60}],
        db_id=42,
    )


class TestHistoryEntryRoundTrip:
    def test_to_dict_has_all_fields(self, sample_history_entry):
        d = sample_history_entry.to_dict()
        assert d["platform"] == "Twitch"
        assert d["title"] == "Test Stream"
        assert d["favorite"] is True
        assert d["watch_position_secs"] == 120.5

    def test_from_dict_round_trip(self, sample_history_entry):
        d = sample_history_entry.to_dict()
        d["id"] = 42
        restored = HistoryEntry.from_dict(d)
        assert restored.platform == sample_history_entry.platform
        assert restored.title == sample_history_entry.title
        assert restored.favorite == sample_history_entry.favorite
        assert restored.db_id == 42

    def test_from_dict_with_missing_fields(self):
        h = HistoryEntry.from_dict({})
        assert h.platform == ""
        assert h.title == ""
        assert h.favorite is False
        assert h.watch_position_secs == 0.0
        assert h.bookmarks == []

    def test_from_dict_coerces_types(self):
        h = HistoryEntry.from_dict({
            "favorite": 1,
            "watched": 0,
            "watch_position_secs": "123.4",
        })
        assert h.favorite is True
        assert h.watched is False
        assert h.watch_position_secs == 123.4


@pytest.mark.parametrize("name,url,fmt", [
    ("1080p", "https://example.com/v.m3u8", "hls"),
    ("720p", "https://example.com/v.mp4", "mp4"),
    ("audio", "https://example.com/a.m4a", "mp4"),
])
def test_quality_info_format_types(name, url, fmt):
    q = QualityInfo(name=name, url=url, format_type=fmt)
    assert q.name == name
    assert q.url == url
    assert q.format_type == fmt


class TestStreamInfo:
    def test_defaults(self):
        info = StreamInfo()
        assert info.platform == ""
        assert info.qualities == []
        assert info.total_secs == 0
        assert info.is_live is False

    def test_with_qualities(self):
        qs = [QualityInfo(name="1080p"), QualityInfo(name="720p")]
        info = StreamInfo(platform="Kick", qualities=qs)
        assert len(info.qualities) == 2
        assert info.qualities[0].name == "1080p"


class TestVODInfo:
    def test_defaults(self):
        v = VODInfo()
        assert v.title == ""
        assert v.duration_ms == 0
        assert v.is_live is False

    @pytest.mark.parametrize("platform", ["Kick", "Twitch", "Rumble", "YouTube"])
    def test_platform_assignment(self, platform):
        v = VODInfo(platform=platform, title="test")
        assert v.platform == platform
