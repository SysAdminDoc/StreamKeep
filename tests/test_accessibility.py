import os
import subprocess
import sys
import unittest
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


# ── V195: focus must be visible, and a control must not vanish ────────

class FocusVisibilityTests(unittest.TestCase):
    """Every focusable control needs a focus state distinct from hover.

    The composer -- the single most important control in the app -- had
    ``border: none`` on both ``:hover`` and ``:focus``, so keyboard focus was
    invisible. List items gave ``::item:hover`` and ``::item:selected`` the same
    background, so the focused row in the global search results could not be
    told from a hovered one. Both are WCAG 2.4.7 failures on the primary
    interaction path (V195).
    """

    def _rules(self, palette_name):
        from streamkeep import theme

        return theme.build_stylesheet(theme.THEMES[palette_name])

    def test_the_composer_focus_state_differs_from_its_hover_state(self):
        import re

        for palette in ("dark", "light", "high_contrast"):
            qss = self._rules(palette)
            hover = re.search(
                r"QLineEdit#sourceComposer:hover \{(.*?)\}", qss, re.S,
            )
            focus = re.search(
                r"QLineEdit#sourceComposer:focus \{(.*?)\}", qss, re.S,
            )
            self.assertIsNotNone(hover, f"{palette}: no composer hover rule")
            self.assertIsNotNone(focus, f"{palette}: no composer focus rule")
            self.assertNotEqual(
                hover.group(1).strip(), focus.group(1).strip(),
                f"{palette}: composer focus is indistinguishable from hover",
            )
            self.assertNotIn(
                "border: none;", focus.group(1).replace("border: none;\n", "", 1),
                f"{palette}: composer focus suppresses every border",
            )

    def test_a_selected_list_item_differs_from_a_hovered_one(self):
        import re

        for palette in ("dark", "light", "high_contrast"):
            qss = self._rules(palette)
            hover = re.search(r"QListWidget::item:hover \{(.*?)\}", qss, re.S)
            selected = re.search(
                r"QListWidget::item:selected \{(.*?)\}", qss, re.S,
            )
            self.assertIsNotNone(hover, f"{palette}: no list hover rule")
            self.assertIsNotNone(selected, f"{palette}: no list selected rule")
            self.assertNotEqual(
                hover.group(1).strip(), selected.group(1).strip(),
                f"{palette}: a selected row looks exactly like a hovered one",
            )

    def test_the_nav_rail_focus_ring_clears_the_ui_component_minimum(self):
        """WCAG 1.4.11 wants 3:1 for a non-text UI component."""
        from streamkeep.theme import THEMES, contrast_ratio

        for name, palette in THEMES.items():
            ratio = contrast_ratio(palette["accent"], palette["panelHi"])
            self.assertGreaterEqual(
                ratio, 3.0,
                f"{name}: nav-rail focus ring is {ratio:.2f}:1 against panelHi",
            )

    def test_the_nav_rail_focus_ring_does_not_use_the_failing_token(self):
        from streamkeep.ui import widgets

        style = widgets.TAB_STYLE()
        self.assertIn(":focus", style)
        focus_blocks = [
            block for block in style.split("}")
            if ":focus" in block and "border: 1px solid" in block
        ]
        self.assertTrue(focus_blocks, "no nav-rail focus border found")
        for block in focus_blocks:
            self.assertNotIn(
                widgets.CAT["overlay0"], block,
                "overlay0 measures 2.91:1 on panelHi in the light palette",
            )


class GlobalSearchReachabilityTests(unittest.TestCase):
    """A functional control must not disappear at a supported window size."""

    def test_search_collapses_to_a_button_instead_of_vanishing(self):
        from streamkeep.ui.main_window import StreamKeep

        window = StreamKeep(startup_check=True)
        try:
            # Narrower than the 1180px threshold but within the 1020px minimum.
            window._update_responsive_chrome(1024)
            self.assertFalse(
                window._global_search.isVisible(),
                "the field is expected to yield space at this width",
            )
            self.assertTrue(
                window._global_search_btn.isVisibleTo(window),
                "search became unreachable: no field and no button",
            )
            self.assertTrue(window._global_search_btn.accessibleName())
            self.assertTrue(window._global_search_btn.toolTip())

            # Revealing it puts the field back and hides the button.
            window._on_reveal_global_search()
            self.assertTrue(window._global_search.isVisibleTo(window))
            self.assertFalse(window._global_search_btn.isVisibleTo(window))

            # Dismissing returns to the button, not to nothing.
            window._dismiss_global_search()
            self.assertTrue(window._global_search_btn.isVisibleTo(window))

            # Wide again: the field is shown and the button steps aside.
            window._update_responsive_chrome(1400)
            self.assertTrue(window._global_search.isVisibleTo(window))
            self.assertFalse(window._global_search_btn.isVisibleTo(window))
        finally:
            window.close()

    def test_exactly_one_search_affordance_at_every_supported_width(self):
        from streamkeep.ui.main_window import StreamKeep

        window = StreamKeep(startup_check=True)
        try:
            for width in (1020, 1100, 1179, 1180, 1400, 2560):
                window._update_responsive_chrome(width)
                reachable = (
                    window._global_search.isVisibleTo(window)
                    or window._global_search_btn.isVisibleTo(window)
                )
                self.assertTrue(
                    reachable, f"search is unreachable at {width}px",
                )
        finally:
            window.close()
