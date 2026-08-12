"""Destructive actions are immediate with undo, except irreversible clears."""

from types import SimpleNamespace
from unittest import mock

from streamkeep.ui.tabs import history as history_mod
from streamkeep.ui.tabs import download_queue as queue_mod
from streamkeep.ui.tabs import settings_presets, storage as storage_mod
from streamkeep.storage import StorageGroup


def _fake_self(**extra):
    ns = SimpleNamespace(
        statuses=[],
    )
    ns._set_status = lambda msg, tone="info": ns.statuses.append((msg, tone))
    ns.notifications = []
    ns._notify_center = lambda msg, tone="info": ns.notifications.append((msg, tone))
    ns._persist_config = lambda: None
    ns._remove_queue_items_durably = (
        lambda items:
        queue_mod.DownloadQueueMixin._remove_queue_items_durably(ns, items)
    )
    ns._remember_removed_queue_items = (
        lambda snapshots:
        queue_mod.DownloadQueueMixin._remember_removed_queue_items(ns, snapshots)
    )
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


def test_clear_queue_is_immediate_and_retains_an_undo_snapshot():
    items = [{"url": "1"}, {"url": "2"}, {"url": "3"}]
    win = _fake_self(_download_queue=list(items), _queue_active_item=items[0])
    win._refresh_queue_table = lambda: None
    queue_mod.DownloadQueueMixin._on_clear_queue(win)
    assert win._download_queue == [items[0]]
    assert [item["url"] for item in win._removed_queue_items_for_undo] == ["2", "3"]
    assert "Undo queue removal" in win.notifications[-1][0]


def test_clear_queue_single_item_also_retains_an_undo_snapshot():
    items = [{"url": "1"}, {"url": "2"}]
    active = items[0]
    win = _fake_self(_download_queue=list(items), _queue_active_item=active)
    win._refresh_queue_table = lambda: None
    queue_mod.DownloadQueueMixin._on_clear_queue(win)
    assert win._download_queue == [active]
    assert win._removed_queue_items_for_undo == [{"url": "2"}]


def test_recycling_storage_is_immediate_and_explains_system_restore(tmp_path):
    recording = tmp_path / "recording"
    recording.mkdir()
    group = StorageGroup(
        dir_path=str(recording), title="Recording", total_size=123,
    )
    index = SimpleNamespace(row=lambda: 0)
    win = _fake_self(
        storage_table=SimpleNamespace(
            selectionModel=lambda: SimpleNamespace(selectedRows=lambda: [index]),
        ),
        storage_proxy_model=SimpleNamespace(group_at=lambda _row: group),
        _log=lambda _message: None,
        _report_failure=lambda *_args, **_kwargs: None,
        _on_storage_rescan=mock.Mock(),
    )

    with mock.patch("send2trash.send2trash") as recycle, mock.patch.object(
        storage_mod._db, "delete_history_for_paths",
    ):
        storage_mod.StorageTabMixin._on_storage_delete_selected(win)

    recycle.assert_called_once_with(str(recording))
    assert "Restore them from" in win.notifications[-1][0]
    win._on_storage_rescan.assert_called_once_with()


def test_deleting_a_custom_preset_is_immediate_and_undoable():
    win = _fake_self(
        _config={"pp_presets": {"Custom": {"extract_audio": True}}},
        pp_preset_combo=SimpleNamespace(currentData=lambda: "Custom"),
    )
    with mock.patch.object(settings_presets, "_populate_pp_presets"):
        settings_presets._on_pp_preset_delete(win)

    assert win._config["pp_presets"] == {}
    assert win._preset_change_for_undo == (
        "Custom", {"extract_audio": True},
    )
    assert "Undo preset change" in win.notifications[-1][0]


def test_low_disk_preflight_warns_without_interrupting_the_action():
    win = _fake_self(
        output_input=SimpleNamespace(text=lambda: "C:/archive"),
        stream_info=object(),
        _log=lambda _message: None,
    )
    with mock.patch.object(queue_mod, "_free_space_bytes", return_value=100), \
            mock.patch.object(queue_mod, "_estimate_download_bytes", return_value=90):
        allowed = queue_mod.DownloadQueueMixin._preflight_disk_space(win)

    assert allowed is True
    assert win.notifications[-1][1] == "warning"
    assert "continue" in win.notifications[-1][0]
