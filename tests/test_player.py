import gc
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from streamkeep.player import sync_viewer


def _stub_mpv():
    return SimpleNamespace(
        volume=100,
        pause=False,
        terminate=lambda: None,
    )


def _assert_slots_alive(viewer, slots):
    gc.collect()
    for slot in slots:
        assert slot.card is not None
        assert slot.widget.parent() is slot.card
        assert slot.widget._mpv is not None


def test_sync_viewer_reuses_slot_cards_across_relayout_and_audio_switch(
    tmp_path, qt_application,
):
    with mock.patch.object(sync_viewer, "is_mpv_available", return_value=True):
        viewer = sync_viewer.SyncViewer()
        try:
            for name in ("one.mp4", "two.mp4"):
                viewer.add_stream(str(tmp_path / name), name)
            slots = list(viewer._slots)
            cards = [slot.card for slot in slots]
            for slot in slots:
                slot.widget._mpv = _stub_mpv()

            viewer._on_audio_switch(1)
            qt_application.processEvents()
            _assert_slots_alive(viewer, slots)
            assert [slot.card for slot in slots] == cards

            viewer.add_stream(str(Path(tmp_path) / "three.mp4"), "three.mp4")
            viewer._slots[-1].widget._mpv = _stub_mpv()
            viewer._on_audio_switch(2)
            qt_application.processEvents()
            _assert_slots_alive(viewer, viewer._slots)
            assert all(slot.widget._mpv is not None for slot in viewer._slots)
            assert [slot.audio_badge.text() for slot in viewer._slots] == [
                "Silent", "Silent", "Audio",
            ]
        finally:
            viewer.close()
            qt_application.processEvents()
