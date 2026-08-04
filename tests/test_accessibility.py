import os
import subprocess
import sys
from datetime import date

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QCheckBox, QLabel, QLineEdit, QSlider, QTableWidget, QVBoxLayout, QWidget,
)

from streamkeep.ui.calendar_widget import _GridCanvas
from streamkeep.ui.clip_dialog import ScrubberView, WaveformWidget
from streamkeep.ui.widgets import (
    set_accessible_slider, set_accessible_switch, style_table,
    update_accessible_status, wrap_scroll_page,
)


def test_tables_and_status_expose_keyboard_and_text_state(qt_application):
    table = QTableWidget(2, 2)
    style_table(
        table,
        accessible_name="Test results",
        accessible_description="Arrow through result rows",
    )
    assert table.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert table.tabKeyNavigation()
    assert table.accessibleName() == "Test results"
    assert table.accessibleDescription() == "Arrow through result rows"

    status = QLabel()
    update_accessible_status(status, "Download failed", tone="error")
    first_revision = status.property("accessibleStatusRevision")
    update_accessible_status(status, "Ready to retry", tone="success")
    assert status.accessibleName() == "Status: Ready to retry"
    assert status.accessibleDescription() == "success status update"
    assert status.property("accessibleStatusRevision") == first_revision + 1


def test_calendar_blocks_are_keyboard_operable(qt_application):
    canvas = _GridCanvas()
    first = {
        "channel": "alpha",
        "title": "Morning stream",
        "start_iso": "2026-07-13T12:00:00Z",
        "end_iso": "2026-07-13T13:00:00Z",
    }
    second = {
        "channel": "beta",
        "title": "Afternoon stream",
        "start_iso": "2026-07-14T18:00:00Z",
        "end_iso": "2026-07-14T19:00:00Z",
    }
    selected = []
    canvas.block_clicked.connect(selected.append)
    canvas.set_segments([(0, 8.0, first), (1, 14.0, second)], date(2026, 7, 13))

    assert canvas.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert canvas.accessibleName() == "Weekly stream schedule"
    QTest.keyClick(canvas, Qt.Key.Key_Right)
    assert "Afternoon stream" in canvas.accessibleDescription()
    QTest.keyClick(canvas, Qt.Key.Key_Return)
    assert selected == [second]


def test_clip_visual_controls_have_keyboard_equivalents(qt_application):
    scrubber = ScrubberView()
    changes = []
    scrubber.handles_changed.connect(lambda start, end: changes.append((start, end)))
    scrubber.set_handles(0.2, 0.8, emit=False)
    QTest.keyClick(scrubber, Qt.Key.Key_Right)
    assert changes[-1] == pytest.approx((0.21, 0.8))
    QTest.keyClick(scrubber, Qt.Key.Key_Space)
    QTest.keyClick(scrubber, Qt.Key.Key_Left)
    assert changes[-1] == pytest.approx((0.21, 0.79))
    assert "keyboard controls end handle" in scrubber.accessibleDescription()

    waveform = WaveformWidget()
    seeks = []
    waveform.seek_requested.connect(seeks.append)
    QTest.keyClick(waveform, Qt.Key.Key_Right)
    assert seeks == [0.05]
    assert waveform.accessibleName() == "Audio waveform preview"


def test_drag_surfaces_have_24px_targets_and_native_role_hints(qt_application):
    check = QCheckBox("Normalize")
    set_accessible_switch(check, "Normalize audio")
    assert check.property("accessibleRole") == "switch"
    assert check.property("qtAccessibleRole") == "switch"

    slider = QSlider(Qt.Orientation.Horizontal)
    set_accessible_slider(slider, "Seek position")
    assert slider.orientation() == Qt.Orientation.Horizontal
    assert slider.property("accessibleRole") == "slider"
    assert slider.property("accessibleOrientation") == "horizontal"

    scrubber = ScrubberView()
    assert scrubber._start_handle.boundingRect().width() >= 24
    assert scrubber._end_handle.boundingRect().width() >= 24
    waveform = WaveformWidget()
    assert waveform.minimumHeight() >= 24

    canvas = _GridCanvas()
    segment = {
        "channel": "alpha",
        "title": "Short event",
        "start_iso": "2026-07-13T12:00:00Z",
        "end_iso": "2026-07-13T12:05:00Z",
    }
    canvas.resize(700, 400)
    canvas.set_segments([(0, 12.0, segment)], date(2026, 7, 13))
    rect = canvas._segment_rect(0, 12.0, segment)
    assert rect.width() >= 24
    assert rect.height() >= 24
    assert canvas.property("accessibleRole") == "grid"
    scrubber.close()
    waveform.close()
    canvas.close()


def test_focus_reveal_keeps_minimum_size_controls_reachable(qt_application):
    page = QWidget()
    page.setMinimumSize(500, 900)
    layout = QVBoxLayout(page)
    layout.addStretch(1)
    field = QLineEdit()
    field.setMinimumWidth(480)
    layout.addWidget(field)
    scroll = wrap_scroll_page(page)
    scroll.resize(320, 220)
    scroll.show()
    try:
        qt_application.processEvents()
        field.setFocus()
        qt_application.processEvents()
        assert field.hasFocus()
        assert scroll.verticalScrollBar().value() > 0
    finally:
        scroll.close()


def test_system_contrast_signal_changes_only_system_theme(qt_application, monkeypatch):
    import streamkeep.theme as theme

    monkeypatch.setattr(theme, "_system_prefers_high_contrast", lambda _app: True)
    theme.apply_visual_system("system", "cozy", "", qt_application)
    assert theme.CAT["base"] == theme.CAT_HIGH_CONTRAST["base"]

    theme.apply_visual_system("dark", "cozy", "", qt_application)
    assert not theme._apply_system_accessibility_theme(qt_application)
    assert theme.CAT["base"] == theme.STREAMKEEP_DARK["base"]
    theme.apply_visual_system("system", "cozy", "", qt_application)
    monkeypatch.setattr(theme, "_system_prefers_high_contrast", lambda _app: False)
    monkeypatch.setattr(theme, "_detect_system_theme", lambda: "light")
    assert theme._apply_system_accessibility_theme(qt_application)
    assert theme.CAT["base"] == theme.STREAMKEEP_LIGHT["base"]
    theme.apply_visual_system("dark", "cozy", "", qt_application)


def test_clip_custom_paints_follow_theme_and_accent(qt_application):
    from streamkeep.theme import CAT, apply_visual_system

    scrubber = ScrubberView()
    waveform = WaveformWidget()
    scrubber.show()
    waveform.resize(100, 32)
    waveform.set_peaks([(-0.5, 0.5)] * 10)
    waveform.show()
    try:
        apply_visual_system("light", "cozy", "#123456", qt_application)
        qt_application.processEvents()
        scrubber.set_range_overlays([(0.0, 0.2), (0.3, 0.5)], active_idx=0)
        assert scrubber._start_handle.brush().color().name() == "#123456"
        assert scrubber._end_handle.brush().color().name() == CAT["green"]
        assert scrubber._placeholder_items[0].brush().color().name() == CAT["surface1"]

        image = QImage(100, 32, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        waveform.render(painter)
        painter.end()
        assert image.pixelColor(50, 0).name() == CAT["crust"]

        apply_visual_system("high_contrast", "cozy", "#abcdef", qt_application)
        qt_application.processEvents()
        assert scrubber._start_handle.brush().color().name() == "#abcdef"
        assert scrubber._placeholder_items[0].brush().color().name() == CAT["surface1"]
    finally:
        scrubber.close()
        waveform.close()
        apply_visual_system("dark", "cozy", "", qt_application)


def test_high_contrast_200_percent_scale_keeps_overflow_reachable():
    script = r'''
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget
from streamkeep.theme import apply_theme
from streamkeep.ui.widgets import wrap_scroll_page

app = QApplication([])
apply_theme("high_contrast", app)
page = QWidget()
page.setMinimumWidth(900)
layout = QVBoxLayout(page)
field = QLineEdit()
field.setMinimumWidth(850)
layout.addWidget(field)
scroll = wrap_scroll_page(page)
scroll.resize(480, 320)
scroll.show()
app.processEvents()
assert app.devicePixelRatio() >= 2.0
assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
assert scroll.horizontalScrollBar().maximum() > 0
assert "#ffffff" in app.styleSheet().lower()
scroll.close()
'''
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_SCALE_FACTOR"] = "2"
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
