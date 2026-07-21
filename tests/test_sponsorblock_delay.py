"""Tests for the SponsorBlock aggregation-delay heuristic (V31)."""

from datetime import datetime

from streamkeep.integrations.sponsorblock import (
    is_sponsorblock_eligible,
    sponsorblock_deferred_start,
)

_NOW = datetime(2026, 7, 20, 12, 0, 0)


def test_zero_delay_never_holds():
    assert sponsorblock_deferred_start("2026-07-20", 0, now=_NOW) == ""
    assert sponsorblock_deferred_start("2026-07-20", -5, now=_NOW) == ""


def test_recent_vod_is_held_from_publish_date():
    # Published 1h ago, 24h delay -> hold until publish+24h (~23h out).
    published = "2026-07-20T11:00:00"
    start_at = sponsorblock_deferred_start(published, 24, now=_NOW)
    assert start_at == "2026-07-21T11:00:00"


def test_old_vod_dispatches_immediately():
    # Published 3 days ago with a 24h delay -> target already past -> now.
    assert sponsorblock_deferred_start("2026-07-17", 24, now=_NOW) == ""


def test_unparseable_date_falls_back_to_now_plus_delay():
    start_at = sponsorblock_deferred_start("not-a-date", 6, now=_NOW)
    assert start_at == "2026-07-20T18:00:00"


def test_empty_date_falls_back_to_now_plus_delay():
    start_at = sponsorblock_deferred_start("", 2, now=_NOW)
    assert start_at == "2026-07-20T14:00:00"


def test_various_date_formats_parse():
    assert sponsorblock_deferred_start("20260720", 24, now=_NOW) == "2026-07-21T00:00:00"
    assert sponsorblock_deferred_start("2026/07/20", 24, now=_NOW) == "2026-07-21T00:00:00"


def test_eligibility_youtube_only():
    assert is_sponsorblock_eligible("youtube", "") is True
    assert is_sponsorblock_eligible("", "https://youtu.be/dQw4w9WgXcQ") is True
    assert is_sponsorblock_eligible("kick", "https://kick.com/x") is False
    assert is_sponsorblock_eligible("", "") is False


def test_delay_config_key_import_validated():
    from streamkeep.config import _INT_CONFIG_KEYS

    assert "sponsorblock_delay_hours" in _INT_CONFIG_KEYS
