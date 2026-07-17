import os
import subprocess
import sys
from datetime import date

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel, QTableWidget

from streamkeep.ui.calendar_widget import _GridCanvas
from streamkeep.ui.clip_dialog import ScrubberView, WaveformWidget
from streamkeep.ui.widgets import style_table, update_accessible_status


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
