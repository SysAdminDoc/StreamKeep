import gc
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from streamkeep.player import mpv_widget, player_controls, player_panel, sync_viewer
from streamkeep.player.pip_window import PiPWindow


def _signal_values(signal):
    values = []
    signal.connect(lambda *args: values.append(args))
    return values


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


def test_mpv_widget_stub_covers_transport_audio_and_metadata(qt_application):
    widget = mpv_widget.MpvWidget()
    fake = SimpleNamespace(
        volume=100,
        pause=False,
        speed=1.0,
        track_list=[{"type": "sub", "id": 3, "title": "English"}],
        chapter_list=[{"title": "Intro", "time": 12}],
        time_pos=4.5,
        duration=90.0,
        af="",
        audio_channels="auto",
        sid=False,
        start="",
        play=mock.Mock(),
        stop=mock.Mock(),
        seek=mock.Mock(),
        terminate=mock.Mock(),
    )
    widget._mpv = fake
    loaded = _signal_values(widget.file_loaded)
    try:
        assert widget.play("recording.mp4", start_secs=2.5) is True
        widget.toggle_pause()
        widget.seek(12)
        widget.seek_relative(-2)
        widget.volume = 180
        widget.speed = 1.5
        widget.set_eq([1, 2, 3, 4, 5])
        widget.set_normalize(True)
        widget.set_mono(True)
        widget.set_subtitle_track(3)
        widget._poll_state()

        assert loaded == [()]
        assert fake.play.call_args.args == ("recording.mp4",)
        assert fake.start == "2.5"
        assert fake.volume == 150
        assert fake.speed == 1.5
        assert fake.seek.call_args_list[-1].args == (-2, "relative")
        assert fake.audio_channels == "mono"
        assert fake.sid == 3
        assert widget.subtitle_tracks == [(3, "English")]
        assert widget.chapter_list == [("Intro", 12.0)]
        assert widget.position == 4.5
        assert widget.duration == 90.0
        assert "superequalizer" in fake.af
        assert "dynaudnorm" in fake.af
    finally:
        widget.destroy_mpv()
        qt_application.processEvents()
    assert fake.terminate.call_count == 1


def test_player_controls_and_panel_wire_stubbed_mpv_offscreen(qt_application):
    controls = player_controls.PlayerControls()
    controls.set_duration(120)
    controls.set_position(30)
    controls.set_paused(True)
    controls.set_subtitle_tracks([(2, "English captions")])
    assert controls.dur_label.text() == "2:00"
    assert controls.time_label.text() == "0:30"
    assert controls.play_btn.text() == ">"
    assert controls.sub_combo.count() == 2
    assert controls.sub_combo.itemData(1) == 2

    panel = player_panel.PlayerPanel()
    fake = SimpleNamespace(
        volume=100,
        pause=False,
        speed=1.0,
        track_list=[],
        chapter_list=[],
        time_pos=0.0,
        duration=0.0,
        af="",
        audio_channels="auto",
        sid=False,
        play=mock.Mock(),
        stop=mock.Mock(),
        seek=mock.Mock(),
        terminate=mock.Mock(),
    )
    panel.mpv._mpv = fake
    try:
        panel.play_file("archive/video.mp4", title="A stream", channel="Channel")
        panel._set_volume(55)
        panel._set_speed(1.25)
        panel._on_duration(60)
        panel._stop()
        assert panel.title_label.text() == "A stream"
        assert panel.channel_label.text() == "Channel • video.mp4"
        assert fake.volume == 55
        assert fake.speed == 1.25
        assert panel.controls.dur_label.text() == "1:00"
        assert panel.last_position == 0.0
    finally:
        panel.mpv.destroy_mpv()
        panel.close()
        controls.deleteLater()
        qt_application.processEvents()


def test_pip_window_reparents_stub_player_without_showing_it(qt_application):
    panel = player_panel.PlayerPanel()
    panel.mpv._mpv = _stub_mpv()
    original_parent = panel.mpv.parent()
    pip = PiPWindow(panel.mpv)
    try:
        assert panel.mpv.parent() is not panel
        assert pip.isAncestorOf(panel.mpv)
        pip.return_mpv_widget()
        assert panel.mpv.parent() is original_parent
    finally:
        panel.mpv.setParent(panel)
        pip.deleteLater()
        panel.mpv.destroy_mpv()
        panel.close()
        qt_application.processEvents()
