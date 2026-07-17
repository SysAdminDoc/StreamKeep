"""Destructive one-click actions must be gated behind a confirmation dialog."""

from types import SimpleNamespace
from unittest import mock

from streamkeep.ui.tabs import history as history_mod
from streamkeep.ui.tabs import download_queue as queue_mod


def _fake_self(**extra):
    ns = SimpleNamespace(
        statuses=[],
    )
    ns._set_status = lambda msg, tone="info": ns.statuses.append((msg, tone))
    ns._persist_config = lambda: None
    for k, v in extra.items():
        setattr(ns, k, v)
    return ns


def test_clear_history_aborts_when_declined():
    win = _fake_self(_history=[{"title": "a"}, {"title": "b"}])
    win._refresh_history_table = lambda: None
    with mock.patch.object(history_mod, "ask_premium_confirmation", return_value=False) as ask, \
         mock.patch.object(history_mod, "_db") as db:
        history_mod.HistoryTabMixin._on_clear_history(win)
    ask.assert_called_once()
    db.clear_history.assert_not_called()
    assert win._history == [{"title": "a"}, {"title": "b"}]


def test_clear_history_proceeds_when_confirmed():
    win = _fake_self(_history=[{"title": "a"}])
    win._refresh_history_table = lambda: None
    with mock.patch.object(history_mod, "ask_premium_confirmation", return_value=True), \
         mock.patch.object(history_mod, "_db") as db:
        history_mod.HistoryTabMixin._on_clear_history(win)
    db.clear_history.assert_called_once()
    assert win._history == []


def test_clear_history_noop_on_empty_does_not_prompt():
    win = _fake_self(_history=[])
    win._refresh_history_table = lambda: None
    with mock.patch.object(history_mod, "ask_premium_confirmation") as ask, \
         mock.patch.object(history_mod, "_db"):
        history_mod.HistoryTabMixin._on_clear_history(win)
    ask.assert_not_called()


def test_clear_queue_aborts_when_declined():
    items = [{"url": "1"}, {"url": "2"}, {"url": "3"}]
    win = _fake_self(_download_queue=list(items), _queue_active_item=items[0])
    win._refresh_queue_table = lambda: None
    with mock.patch.object(queue_mod, "ask_premium_confirmation", return_value=False) as ask:
        queue_mod.DownloadQueueMixin._on_clear_queue(win)
    ask.assert_called_once()
    assert win._download_queue == items


def test_clear_queue_single_item_needs_no_prompt():
    items = [{"url": "1"}, {"url": "2"}]
    active = items[0]
    win = _fake_self(_download_queue=list(items), _queue_active_item=active)
    win._refresh_queue_table = lambda: None
    with mock.patch.object(queue_mod, "ask_premium_confirmation") as ask:
        queue_mod.DownloadQueueMixin._on_clear_queue(win)
    ask.assert_not_called()
    assert win._download_queue == [active]
