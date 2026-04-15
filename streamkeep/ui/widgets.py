"""Reusable UI widget helpers — pure functions that build styled Qt widgets.

These were previously methods on the StreamKeep class even though none of
them touched `self`. Moving them to module level makes the main window
smaller, easier to test, and lets future tab-widget splits reuse them
without importing the god object.
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QLabel, QScrollArea, QVBoxLayout,
)

from ..theme import CAT


# Platform badge mapping — key → (CAT colour key, display text).
# Resolved via PLATFORM_BADGES property so colours track theme changes.
_BADGE_MAP = {
    "Kick":       ("green",    "Kick"),
    "Twitch":     ("mauve",    "Twitch"),
    "Rumble":     ("green",    "Rumble"),
    "SoundCloud": ("peach",    "SoundCloud"),
    "Reddit":     ("peach",    "Reddit"),
    "Audius":     ("mauve",    "Audius"),
    "Podcast":    ("yellow",   "Podcast"),
    "Direct":     ("blue",     "Direct"),
    "yt-dlp":     ("overlay1", "yt-dlp"),
}


class _BadgeLookup(dict):
    """Dict-like that rebuilds badge colours from the live CAT dict on
    every access, so theme switches are reflected immediately."""

    def __getitem__(self, key):
        cat_key, text = _BADGE_MAP[key]
        return {"color": CAT[cat_key], "text": text}

    def __contains__(self, key):
        return key in _BADGE_MAP

    def get(self, key, default=None):
        if key in _BADGE_MAP:
            return self[key]
        return default


PLATFORM_BADGES = _BadgeLookup()


def TAB_STYLE():
    """Build tab stylesheet from the live CAT dict (theme-safe)."""
    return f"""
QPushButton#tab {{
    background-color: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {CAT['panelSoft']},
        stop: 1 {CAT['panel']}
    );
    color: {CAT['subtext1']};
    border: 1px solid {CAT['stroke']};
    padding: 11px 18px;
    font-weight: 600;
    font-size: 13px;
    border-radius: 14px;
}}
QPushButton#tab:hover {{
    color: {CAT['text']};
    border-color: {CAT['accent']};
    background-color: {CAT['panelHi']};
}}
QPushButton#tabActive {{
    background-color: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {CAT['accent']},
        stop: 1 {CAT['sky']}
    );
    color: #081120;
    border: 1px solid rgba(255, 255, 255, 40);
    padding: 11px 18px;
    font-weight: 700;
    font-size: 13px;
    border-radius: 14px;
}}
"""


def path_label(path_text, fallback="Choose folder"):
    """Return the basename of a path for display, or `fallback` if empty."""
    path_text = (path_text or "").strip()
    if not path_text:
        return fallback
    try:
        p = Path(path_text)
        if p.name:
            return p.name
    except Exception:
        pass
    return path_text


def make_metric_card(label_text, value_text="--", sub_text=""):
    """Build a dashboard metric card. Returns (card, value_label, sub_label)."""
    card = QFrame()
    card.setObjectName("metricCard")
    card.setMinimumHeight(102)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(6)

    label = QLabel(label_text)
    label.setObjectName("metricLabel")
    value = QLabel(value_text)
    value.setObjectName("metricValue")
    value.setWordWrap(True)
    sub = QLabel(sub_text)
    sub.setObjectName("metricSubvalue")
    sub.setWordWrap(True)
    sub.setVisible(bool(sub_text))

    lay.addWidget(label)
    lay.addWidget(value)
    lay.addWidget(sub)
    lay.addStretch(1)
    return card, value, sub


def make_field_block(title, hint=""):
    """Build a Settings-style titled container. Returns (card, vbox_layout)."""
    card = QFrame()
    card.setObjectName("subtleCard")
    card.setMinimumHeight(108)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)

    label = QLabel(title)
    label.setObjectName("fieldLabel")
    lay.addWidget(label)

    if hint:
        hint_label = QLabel(hint)
        hint_label.setObjectName("fieldHint")
        hint_label.setWordWrap(True)
        lay.addWidget(hint_label)

    return card, lay


def wrap_scroll_page(page):
    """Wrap a page widget in a QScrollArea with styled chrome."""
    page.setObjectName("chrome")
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    scroll = QScrollArea()
    scroll.setObjectName("chrome")
    scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.viewport().setObjectName("chrome")
    scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    scroll.setWidget(page)
    return scroll


def style_table(table, row_height=46):
    """Apply the premium midnight theme to a QTableWidget."""
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.horizontalHeader().setHighlightSections(False)


def set_metric(value_label, sub_label, value, sub=""):
    """Update a metric card's value and subtitle."""
    value_label.setText(value)
    sub_label.setText(sub)
    sub_label.setVisible(bool(sub))
