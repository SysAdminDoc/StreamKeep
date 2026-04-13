"""Chapter & Bookmark Navigation Panel — sidebar for the player (F55).

Lists chapters (from metadata.json, .chapters.auto.txt, embedded MP4
chapters) and user bookmarks (from HistoryEntry.bookmarks). Click to
seek. "Add bookmark" captures the current position.
"""

import json
import os

from PyQt6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal

from ..theme import CAT


def _fmt_time(secs):
    s = max(0, int(secs or 0))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class ChapterPanel(QWidget):
    """Collapsible sidebar listing chapters + bookmarks with seek-on-click."""

    seek_requested = pyqtSignal(float)          # seconds
    bookmark_added = pyqtSignal(str, float)      # name, seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self.setStyleSheet(f"background: {CAT['mantle']}; border-left: 1px solid {CAT['surface0']};")

        self._entries = []   # list of (label, secs, kind)  kind = "chapter" | "bookmark"
        self._current_pos = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QLabel("Chapters & Bookmarks")
        header.setStyleSheet(f"color: {CAT['text']}; font-weight: bold; font-size: 12px;")
        layout.addWidget(header)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: {CAT['base']}; color: {CAT['text']}; "
            f"border: 1px solid {CAT['surface0']}; border-radius: 4px; }}"
            f"QListWidget::item {{ padding: 4px; }}"
            f"QListWidget::item:selected {{ background: {CAT['surface1']}; }}"
        )
        self._list.itemClicked.connect(self._on_item_click)
        layout.addWidget(self._list, 1)

        # Add bookmark button
        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Bookmark")
        add_btn.setObjectName("secondary")
        add_btn.clicked.connect(self._on_add_bookmark)
        btn_row.addWidget(add_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    def load_chapters(self, recording_dir, mpv_chapters=None, bookmarks=None):
        """Load chapters from all sources and populate the list.

        *mpv_chapters* is a list of (title, secs) from MpvWidget.chapter_list.
        *bookmarks* is the HistoryEntry.bookmarks list [{name, secs}].
        """
        self._entries.clear()
        self._list.clear()

        # 1. Embedded MP4 chapters (from mpv)
        if mpv_chapters:
            for title, secs in mpv_chapters:
                self._entries.append((title, float(secs), "chapter"))

        # 2. metadata.json chapters
        if recording_dir:
            meta_path = os.path.join(recording_dir, "metadata.json")
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    for ch in meta.get("chapters", []):
                        title = ch.get("title", "Chapter")
                        start = float(ch.get("start", 0))
                        # Deduplicate against embedded chapters (within 5s)
                        if not any(abs(s - start) < 5 for _, s, _ in self._entries):
                            self._entries.append((title, start, "chapter"))
                except (OSError, json.JSONDecodeError, ValueError):
                    pass

            # 3. .chapters.auto.txt (Whisper-generated)
            auto_path = os.path.join(recording_dir, ".chapters.auto.txt")
            if os.path.isfile(auto_path):
                try:
                    with open(auto_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            parts = line.split(None, 1)
                            if len(parts) >= 2:
                                try:
                                    secs = _parse_ts(parts[0])
                                    title = parts[1]
                                    if not any(abs(s - secs) < 5 for _, s, _ in self._entries):
                                        self._entries.append((title, secs, "chapter"))
                                except ValueError:
                                    pass
                except OSError:
                    pass

        # 4. User bookmarks
        if bookmarks:
            for bm in bookmarks:
                name = bm.get("name", "Bookmark") if isinstance(bm, dict) else str(bm)
                secs = float(bm.get("secs", 0) if isinstance(bm, dict) else 0)
                self._entries.append((name, secs, "bookmark"))

        # Sort by time
        self._entries.sort(key=lambda e: e[1])

        # Populate list
        for label, secs, kind in self._entries:
            prefix = "[B] " if kind == "bookmark" else ""
            item = QListWidgetItem(f"{prefix}{_fmt_time(secs)}  {label}")
            item.setData(Qt.ItemDataRole.UserRole, secs)
            if kind == "bookmark":
                item.setForeground(Qt.GlobalColor.cyan)
            self._list.addItem(item)

    def set_position(self, secs):
        """Highlight the current chapter based on playback position."""
        self._current_pos = secs

    def _on_item_click(self, item):
        secs = item.data(Qt.ItemDataRole.UserRole)
        if secs is not None:
            self.seek_requested.emit(float(secs))

    def _on_add_bookmark(self):
        name, ok = QInputDialog.getText(
            self, "Add Bookmark",
            f"Bookmark name (at {_fmt_time(self._current_pos)}):",
        )
        if ok and name:
            self.bookmark_added.emit(name.strip(), self._current_pos)
            # Add to list immediately
            self._entries.append((name.strip(), self._current_pos, "bookmark"))
            item = QListWidgetItem(f"[B] {_fmt_time(self._current_pos)}  {name.strip()}")
            item.setData(Qt.ItemDataRole.UserRole, self._current_pos)
            item.setForeground(Qt.GlobalColor.cyan)
            self._list.addItem(item)


def _parse_ts(text):
    """Parse HH:MM:SS or MM:SS or seconds into float seconds."""
    parts = text.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(text)
