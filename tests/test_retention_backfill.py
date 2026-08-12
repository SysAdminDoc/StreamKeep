"""V169: backfill whatever is closest to being deleted, not whatever is newest.

Kick keeps a replay 7 days unverified and 30 verified, with only 16 or 30 kept
at once. Backfilling newest-first spends that budget backwards: the newest item
has the whole window left and the oldest may have hours, so a long queue loses
the oldest reachable VOD first — permanently, because the platform deletes it.
"""

import pytest

from streamkeep import retention


class _Vod:
    def __init__(self, title, published_at=None, platform="kick"):
        self.title = title
        self.published_at = published_at
        self.platform = platform

    def __repr__(self):
        return self.title


NOW = 1_800_000_000.0


def _aged(title, days):
    return _Vod(title, NOW - days * retention.DAY_SECONDS)


# ── The documented windows ──────────────────────────────────────────

def test_kick_windows_match_the_documented_policy():
    unverified = retention.retention_window("kick")
    verified = retention.retention_window("kick", verified=True)

    assert (unverified.days, unverified.max_stored) == (7, 16)
    assert (verified.days, verified.max_stored) == (30, 30)
    assert unverified.source, "a window must record where its numbers came from"


@pytest.mark.parametrize("platform", ["rumble", "twitch", "youtube", "", None])
def test_a_platform_with_no_documented_window_is_not_guessed_at(platform):
    """Reordering someone's queue on a guessed policy is worse than nothing."""
    assert retention.retention_window(platform) is None
    assert not retention.has_retention_window(platform)


def test_platform_names_fold():
    assert retention.retention_window("KICK") is not None
    assert retention.retention_window(" Kick ") is not None


# ── Ordering ────────────────────────────────────────────────────────

def test_the_soonest_deadline_is_fetched_first():
    vods = [_aged("newest", 0.5), _aged("mid", 3), _aged("oldest", 6.5)]

    order = [vod.title for vod, _reason in
             retention.order_backfill(vods, "kick", now=NOW)]

    assert order == ["oldest", "mid", "newest"]


def test_a_verified_channel_has_more_room_so_nothing_is_urgent_yet():
    """The same items under the 30-day window are all far from expiry."""
    vods = [_aged("newest", 0.5), _aged("oldest", 6.5)]

    for _vod, reason in retention.order_backfill(
        vods, "kick", verified=True, now=NOW
    ):
        assert "30-day" in reason


def test_an_unknown_platform_keeps_the_extractor_order():
    vods = [_aged("a", 6), _aged("b", 1), _aged("c", 3)]

    result = retention.order_backfill(vods, "rumble", now=NOW)

    assert [vod.title for vod, _r in result] == ["a", "b", "c"]
    assert all(reason == "" for _v, reason in result)


def test_disabling_the_policy_keeps_the_extractor_order():
    vods = [_aged("newest", 0.5), _aged("oldest", 6.5)]

    result = retention.order_backfill(vods, "kick", now=NOW, enabled=False)

    assert [vod.title for vod, _r in result] == ["newest", "oldest"]


def test_undated_items_sort_last_rather_than_first():
    """Promoting an item whose urgency is unknown would displace a known one."""
    vods = [_Vod("undated"), _aged("oldest", 6.5), _Vod("undated2")]

    order = [vod.title for vod, _r in
             retention.order_backfill(vods, "kick", now=NOW)]

    assert order == ["oldest", "undated", "undated2"], "and stable among themselves"


def test_the_sort_is_stable_for_items_sharing_a_deadline():
    same = [_aged(f"item{i}", 3) for i in range(5)]

    order = [vod.title for vod, _r in
             retention.order_backfill(same, "kick", now=NOW)]

    assert order == [f"item{i}" for i in range(5)]


def test_an_empty_batch_is_handled():
    assert retention.order_backfill([], "kick") == []
    assert retention.order_backfill(None, "kick") == []


# ── Dates ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    NOW - 86_400, str(int(NOW - 86_400)), "2027-01-15", "2027-01-15 10:30:00",
    "2027-01-15T10:30:00Z",
])
def test_publication_dates_are_read_in_the_shapes_extractors_emit(value):
    assert retention._published_epoch(_Vod("v", value)) is not None


@pytest.mark.parametrize("value", [None, "", "not a date", 0, 1234, "0"])
def test_an_unusable_date_yields_no_deadline_rather_than_a_wrong_one(value):
    assert retention._published_epoch(_Vod("v", value)) is None
    assert retention.reachable_until(_Vod("v", value), "kick") is None


def test_a_dict_shaped_vod_is_understood_too():
    assert retention._published_epoch({"published_at": NOW - 3600}) is not None


# ── The reason an operator reads ────────────────────────────────────

def test_the_reason_states_the_time_left_and_the_window():
    reason = retention.backfill_reason(_aged("x", 6.5), "kick", now=NOW)

    assert "oldest-reachable first" in reason
    assert "7-day" in reason
    assert "16 replays" in reason


def test_an_expired_item_says_the_fetch_may_already_fail():
    reason = retention.backfill_reason(_aged("gone", 9), "kick", now=NOW)

    assert "past" in reason and "may already fail" in reason


def test_an_undated_item_says_the_order_was_left_alone():
    reason = retention.backfill_reason(_Vod("x"), "kick", now=NOW)

    assert "no publication date" in reason


def test_an_unknown_platform_has_no_reason_to_give():
    assert retention.backfill_reason(_aged("x", 1), "rumble", now=NOW) == ""


# ── The monitor actually uses it ────────────────────────────────────

def test_the_subscribe_path_enqueues_oldest_first_with_the_reason():
    """The policy is worthless if the enqueue path ignores it."""
    from streamkeep.ui.tabs.monitor import MonitorTabMixin

    added = []

    class _Entry:
        channel_id = "chan"
        url = "https://kick.com/example"
        platform = "kick"
        ytdlp_template_name = ""
        capture_comments = False

    class _Monitor:
        entries = [_Entry()]

    class _Tab:
        monitor = _Monitor()
        _config = {"backfill_oldest_first": True}

        def _log(self, _message):
            pass

        def _check_quality_upgrade(self, _channel_id, _vod):
            return None

        def _find_duplicate(self, *_args, **_kwargs):
            return None

        _download_queue = []
        download_worker = None
        _queue_running = False

        def _apply_sponsorblock_delay(self, _item, _vod):
            pass

        def _advance_queue(self):
            pass

        def _refresh_queue_table(self):
            pass

        def _queue_add(self, url, **kwargs):
            added.append((kwargs.get("title"), kwargs.get("note", "")))
            return True

    vods = [_aged("newest", 0.5), _aged("oldest", 6.5), _aged("mid", 3)]
    for vod in vods:
        vod.source = f"https://kick.com/{vod.title}"
        vod.channel = "example"
        vod.source_id = vod.title
        vod.date = "2027-01-15"

    MonitorTabMixin._on_new_vods_found(_Tab(), "chan", vods)

    assert [title for title, _note in added] == ["oldest", "mid", "newest"]
    assert all("oldest-reachable first" in note for _t, note in added), added


def test_the_subscribe_path_honours_the_setting_being_off():
    from streamkeep.ui.tabs.monitor import MonitorTabMixin

    added = []

    class _Entry:
        channel_id = "chan"
        url = "https://kick.com/example"
        platform = "kick"
        ytdlp_template_name = ""
        capture_comments = False

    class _Tab:
        monitor = type("M", (), {"entries": [_Entry()]})()
        _config = {"backfill_oldest_first": False}

        def _log(self, _message):
            pass

        def _check_quality_upgrade(self, _c, _v):
            return None

        def _find_duplicate(self, *_a, **_k):
            return None

        _download_queue = []
        download_worker = None
        _queue_running = False

        def _apply_sponsorblock_delay(self, _item, _vod):
            pass

        def _advance_queue(self):
            pass

        def _refresh_queue_table(self):
            pass

        def _queue_add(self, url, **kwargs):
            added.append(kwargs.get("title"))
            return True

    vods = [_aged("newest", 0.5), _aged("oldest", 6.5)]
    for vod in vods:
        vod.source = f"https://kick.com/{vod.title}"
        vod.channel = "example"
        vod.source_id = vod.title
        vod.date = "2027-01-15"

    MonitorTabMixin._on_new_vods_found(_Tab(), "chan", vods)

    assert added == ["newest", "oldest"]
