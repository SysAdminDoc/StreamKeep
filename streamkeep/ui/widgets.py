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


# Platform badge colors — the detected extractor paints this label next
# to the URL input. Map is populated at UI build time using CAT[] lookups.
PLATFORM_BADGES = {
    "Kick":       {"color": CAT["green"],   "text": "Kick"},
    "Twitch":     {"color": CAT["mauve"],   "text": "Twitch"},
    "Rumble":     {"color": CAT["green"],   "text": "Rumble"},
    "SoundCloud": {"color": CAT["peach"],   "text": "SoundCloud"},
    "Reddit":     {"color": CAT["peach"],   "text": "Reddit"},
    "Audius":     {"color": CAT["mauve"],   "text": "Audius"},
    "Podcast":    {"color": CAT["yellow"],  "text": "Podcast"},
    "Direct":     {"color": CAT["blue"],    "text": "Direct"},
    "yt-dlp":     {"color": CAT["overlay1"], "text": "yt-dlp"},
}


TAB_STYLE = f"""
QPushButton#tab {{
    background-color: {CAT['panelSoft']};
    color: {CAT['muted']};
    border: 1px solid {CAT['stroke']};
    padding: 10px 18px;
    font-weight: 600;
    font-size: 13px;
    border-radius: 999px;
}}
QPushButton#tab:hover {{
    color: {CAT['text']};
    border-color: {CAT['accent']};
    background-color: {CAT['panel']};
}}
QPushButton#tabActive {{
    background-color: {CAT['accent']};
    color: {CAT['crust']};
    border: 1px solid {CAT['accent']};
    padding: 10px 18px;
    font-weight: 600;
    font-size: 13px;
    border-radius: 999px;
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
    card.setMinimumHeight(92)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(4)

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
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.viewport().setObjectName("chrome")
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
