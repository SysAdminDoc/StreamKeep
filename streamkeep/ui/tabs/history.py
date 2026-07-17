"""History tab — completed downloads table + stats dashboard.

Also contains ``HistoryTabMixin`` which holds every history-tab *handler*
method.  The mixin is mixed into ``StreamKeep`` via multiple inheritance so
that ``self`` is the full main-window instance at runtime.
"""

import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from streamkeep import db as _db
from streamkeep.models import HistoryEntry
from streamkeep.postprocess import PostProcessor
from streamkeep.theme import CAT
from streamkeep.verify import (
    STATUS_OK,
    rescan_archive_manifest,
    verify_archive_manifest,
)
from ..widgets import make_metric_card, style_table


def build_history_tab(win):
    """Build the History tab page. Stashes widget refs on `win.*`."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(14)

    hero = QFrame()
    hero.setObjectName("heroCard")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(18, 18, 18, 18)
    hero_lay.setSpacing(14)

    hero_copy = QVBoxLayout()
    hero_copy.setSpacing(4)
    kicker = QLabel("History")
    kicker.setObjectName("eyebrow")
    title = QLabel("Keep a clean record of what you captured")
    title.setObjectName("heroTitle")
    title.setWordWrap(True)
    body = QLabel(
        "Completed downloads are listed here so you can quickly revisit "
        "folders, compare qualities, and confirm recent jobs."
    )
    body.setObjectName("heroBody")
    body.setWordWrap(True)
    hero_copy.addWidget(kicker)
    hero_copy.addWidget(title)
    hero_copy.addWidget(body)
    hero_lay.addLayout(hero_copy)

    history_metrics = QHBoxLayout()
    history_metrics.setSpacing(12)
    count_card, win.history_count_value, win.history_count_sub = make_metric_card(
        "Downloads", "0", "saved downloads"
    )
    latest_card, win.history_latest_value, win.history_latest_sub = make_metric_card(
        "Latest", "No entries", "Completed downloads appear here"
    )
    platform_card, win.history_platform_value, win.history_platform_sub = make_metric_card(
        "Top Platform", "—", "appears most in history"
    )
    channel_card, win.history_channel_value, win.history_channel_sub = make_metric_card(
        "Top Channel", "—", "most downloaded"
    )
    history_metrics.addWidget(count_card)
    history_metrics.addWidget(latest_card, 1)
    history_metrics.addWidget(platform_card)
    history_metrics.addWidget(channel_card, 1)
    hero_lay.addLayout(history_metrics)
    lay.addWidget(hero)

    card = QFrame()
    card.setObjectName("card")
    card_lay = QVBoxLayout(card)
    card_lay.setContentsMargins(18, 18, 18, 18)
    card_lay.setSpacing(10)

    header = QHBoxLayout()
    header_copy = QVBoxLayout()
    header_copy.setSpacing(4)
    sec = QLabel("Download History")
    sec.setObjectName("sectionTitle")
    win.history_summary_label = QLabel(
        "Download history builds automatically after each completed job."
    )
    win.history_summary_label.setObjectName("sectionBody")
    win.history_summary_label.setWordWrap(True)
    header_copy.addWidget(sec)
    header_copy.addWidget(win.history_summary_label)
    header.addLayout(header_copy, 1)
    clear_btn = QPushButton("Clear History")
    clear_btn.setObjectName("secondary")
    clear_btn.clicked.connect(win._on_clear_history)
    header.addWidget(clear_btn)
    card_lay.addLayout(header)

    search_card = QFrame()
    search_card.setObjectName("toolbar")
    search_wrap = QVBoxLayout(search_card)
    search_wrap.setContentsMargins(14, 14, 14, 14)
    search_wrap.setSpacing(10)

    search_head = QHBoxLayout()
    search_head.setSpacing(12)
    search_copy = QVBoxLayout()
    search_copy.setSpacing(4)
    search_title = QLabel("Find a Download")
    search_title.setObjectName("fieldLabel")
    search_hint = QLabel(
        "Search metadata instantly or switch to transcript text when you need quoted moments."
    )
    search_hint.setObjectName("subtleText")
    search_hint.setWordWrap(True)
    search_copy.addWidget(search_title)
    search_copy.addWidget(search_hint)
    search_head.addLayout(search_copy, 1)
    win.history_filter_summary = QLabel("Showing all downloads")
    win.history_filter_summary.setObjectName("subtleText")
    search_head.addWidget(win.history_filter_summary)
    search_wrap.addLayout(search_head)

    search_row = QHBoxLayout()
    search_row.setSpacing(8)
    win.history_search = QLineEdit()
    win.history_search.setPlaceholderText("Search title, platform, channel, path, or URL…")
    win.history_search.setClearButtonEnabled(True)
    win.history_search.textChanged.connect(win._on_history_search)
    search_row.addWidget(win.history_search, 1)
    win.transcript_search_check = QCheckBox("Search Transcript Text")
    win.transcript_search_check.setToolTip(
        "When checked, queries search indexed transcript text (SRT/VTT) instead of metadata."
    )
    win.transcript_search_check.toggled.connect(lambda _: win._on_history_search(""))
    search_row.addWidget(win.transcript_search_check)
    search_wrap.addLayout(search_row)
    card_lay.addWidget(search_card)

    win.history_table = QTableWidget()
    win.history_table.setColumnCount(7)
    win.history_table.setHorizontalHeaderLabels(
        ["", "Date", "Platform", "Title", "Quality", "Size", "Path"]
    )
    hh = win.history_table.horizontalHeader()
    hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
    win.history_table.setColumnWidth(0, 112)
    win.history_table.setColumnWidth(1, 150)
    win.history_table.setColumnWidth(2, 84)
    win.history_table.setColumnWidth(4, 110)
    win.history_table.setColumnWidth(5, 88)
    win.history_table.verticalHeader().setVisible(False)
    win.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    win.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    win.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    win.history_table.doubleClicked.connect(win._on_history_double_click)
    # Enable hover preview (F46)
    win.history_table.setMouseTracking(True)
    win.history_table.cellEntered.connect(win._on_history_cell_hover)
    style_table(
        win.history_table,
        72,
        accessible_name="Download history",
        accessible_description="Completed downloads; use arrow keys to navigate rows",
    )
    card_lay.addWidget(win.history_table)

    lay.addWidget(card, 1)
    win._refresh_history_summary()
    return page


# ── History-tab handler mixin ────────────────────────────────────────────
#
# Every method below was formerly defined directly on the ``StreamKeep``
# class in ``main_window.py``.  They're injected back into that class via
# ``class StreamKeep(HistoryTabMixin, QMainWindow)``.


class HistoryTabMixin:
    """History-tab handler methods, mixed into ``StreamKeep``."""

    # ── Summary / metrics ────────────────────────────────────────────

    def _refresh_history_summary(self):
        if not hasattr(self, "history_count_value"):
            return
        total = len(self._history)
        latest = self._history[-1] if self._history else None
        visible = len(getattr(self, "_history_view", self._history))
        query = self.history_search.text().strip() if hasattr(self, "history_search") else ""
        transcript_mode = (
            hasattr(self, "transcript_search_check")
            and self.transcript_search_check.isChecked()
        )
        transcript_hits = sum(
            len(items) for items in getattr(self, "_transcript_hits", {}).values()
        )

        self._set_metric(self.history_count_value, self.history_count_sub, str(total), "saved downloads")
        latest_value = latest.date if latest else "No entries"
        latest_sub = (latest.title if latest else "Completed downloads appear here")[:40]
        self._set_metric(self.history_latest_value, self.history_latest_sub, latest_value, latest_sub)

        # Compute top platform and top channel from history
        if hasattr(self, "history_platform_value"):
            if total:
                plat_counts = Counter(h.platform for h in self._history if h.platform)
                top_plat, top_plat_n = (plat_counts.most_common(1) or [("—", 0)])[0]
                plat_share = f"{top_plat_n} / {total} downloads" if top_plat_n else "no data"
                self._set_metric(self.history_platform_value, self.history_platform_sub,
                                 top_plat or "—", plat_share)
                # Top channel: prefer stored creator/channel metadata, then fall back
                # to a conservative URL-derived guess for older history rows.
                ch_counts = Counter()
                for h in self._history:
                    key = self._history_channel_label(h)
                    if key:
                        ch_counts[key] += 1
                if ch_counts:
                    top_ch, top_ch_n = ch_counts.most_common(1)[0]
                    self._set_metric(self.history_channel_value, self.history_channel_sub,
                                     top_ch[:24], f"{top_ch_n} download(s)")
                else:
                    self._set_metric(self.history_channel_value, self.history_channel_sub,
                                     "—", "no channel data")
            else:
                self._set_metric(self.history_platform_value, self.history_platform_sub,
                                 "—", "appears most in history")
                self._set_metric(self.history_channel_value, self.history_channel_sub,
                                 "—", "most downloaded")
        self._refresh_shell_overview()

        if total:
            if query and transcript_mode:
                if hasattr(self, "history_filter_summary"):
                    self.history_filter_summary.setText(
                        f"{visible} matching download(s) • {transcript_hits} transcript hit(s)"
                    )
                if visible:
                    self.history_summary_label.setText(
                        "Transcript search matches indexed dialogue. Hover a title to preview matching lines, then double-click to open the folder."
                    )
                else:
                    self.history_summary_label.setText(
                        "No indexed transcript text matched that phrase. Clear the query or switch back to metadata search."
                    )
            elif query:
                if hasattr(self, "history_filter_summary"):
                    self.history_filter_summary.setText(
                        f"Showing {visible} of {total} download(s)"
                    )
                if visible:
                    self.history_summary_label.setText(
                        "Metadata search matches title, platform, channel, folder path, and source URL."
                    )
                else:
                    self.history_summary_label.setText(
                        "No downloads matched that search. Try a broader title, platform, channel, or folder term."
                    )
            else:
                if hasattr(self, "history_filter_summary"):
                    self.history_filter_summary.setText(f"Showing all {total} download(s)")
                self.history_summary_label.setText(
                    "Double-click a row to open the saved folder in Explorer. Use search to filter by title, platform, channel, folder, or source URL."
                )
        else:
            if hasattr(self, "history_filter_summary"):
                self.history_filter_summary.setText("History fills in automatically")
            self.history_summary_label.setText("Download history builds automatically after each completed job.")

    # ── Remove entries whose folders were recycled ───────────────────

    def _remove_history_for_paths(self, real_paths):
        """Remove all history rows that point at any recycled recording path."""
        real_paths = {os.path.realpath(path) for path in (real_paths or set()) if path}
        if not real_paths:
            return

        def _entry_real_path(entry):
            path = getattr(entry, "path", "") or ""
            return os.path.realpath(path) if path else ""

        db_ids = [
            h.db_id for h in self._history
            if getattr(h, "db_id", 0) and _entry_real_path(h) in real_paths
        ]
        self._history = [h for h in self._history if _entry_real_path(h) not in real_paths]
        if db_ids:
            _db.delete_history_entries(db_ids)
        self._refresh_history_table()

    # ── History Actions ──────────────────────────────────────────────

    def _add_history(self, platform, title, quality, size, path, url="", channel=""):
        entry = HistoryEntry(
            date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            platform=platform, title=title[:60],
            channel=self._infer_history_channel(url=url, platform=platform, channel=channel),
            quality=quality, size=size, path=path, url=url,
        )
        # Persist to SQLite immediately (F41)
        entry.db_id = _db.save_history_entry(entry.to_dict())
        self._history.append(entry)
        self._refresh_history_table()
        self._schedule_persist_config()
        return entry

    def _refresh_history_table(self):
        query = ""
        if hasattr(self, "history_search"):
            query = self.history_search.text().strip().lower()
        ordered = list(reversed(self._history))
        # Transcript FTS search mode (F27)
        transcript_mode = (
            hasattr(self, "transcript_search_check")
            and self.transcript_search_check.isChecked()
        )
        self._transcript_hits = {}  # path -> list of {text, start_sec, end_sec}
        if query and transcript_mode:
            from ...search import search_transcripts
            try:
                hits = search_transcripts(query, limit=200)
            except Exception:
                hits = []
            hit_paths = set()
            for h in hits:
                rp = h["recording_path"]
                hit_paths.add(rp)
                self._transcript_hits.setdefault(rp, []).append(h)
            ordered = [h for h in ordered if h.path in hit_paths]
        elif query:
            ordered = [
                h for h in ordered
                if query in (h.title or "").lower()
                or query in (h.platform or "").lower()
                or query in (self._history_channel_label(h) or "").lower()
                or query in (h.path or "").lower()
                or query in (h.url or "").lower()
            ]
        self._history_view = ordered  # used by double-click handler
        self.history_table.setRowCount(len(ordered))
        _dim_color = QColor(CAT["overlay0"])
        _warn_prefix = "⚠ "  # ⚠
        for i, h in enumerate(ordered):
            # Check if the recorded path still exists on disk (F14 orphan detection).
            orphan = bool(h.path) and not os.path.isdir(h.path)
            # Column 0 = thumbnail cell (lazy-loaded).
            thumb_label = QLabel()
            thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_label.setStyleSheet(
                f"background-color: {CAT['mantle']}; border-radius: 6px; color: {CAT['overlay0']};"
            )
            thumb_label.setText(_warn_prefix if orphan else "…")
            self.history_table.setCellWidget(i, 0, thumb_label)
            # Data columns 1..6
            # Build title with watch/favorite indicators (F38)
            title_display = h.title or ""
            status_prefix = ""
            if getattr(h, "favorite", False):
                status_prefix += "★ "  # ★
            if getattr(h, "watched", False):
                status_prefix += "✓ "  # ✓
            elif getattr(h, "watch_position_secs", 0) > 0:
                status_prefix += "▶ "  # ▶
            title_display = status_prefix + title_display
            for col, val in enumerate([h.date, h.platform, title_display, h.quality, h.size, h.path], start=1):
                display = val
                if orphan and col == 6 and val:
                    display = val + "  (missing)"
                item = QTableWidgetItem(display)
                if col in (1, 2, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if orphan:
                    item.setForeground(_dim_color)
                elif getattr(h, "watched", False) and col == 3:
                    item.setForeground(QColor(CAT["overlay0"]))
                # Transcript search hit snippet as tooltip (F27)
                if col == 3 and h.path and h.path in self._transcript_hits:
                    snippets = self._transcript_hits[h.path][:3]
                    tip_lines = []
                    for s in snippets:
                        mins = int(s["start_sec"]) // 60
                        secs = int(s["start_sec"]) % 60
                        tip_lines.append(f"[{mins}:{secs:02d}] {s['text']}")
                    item.setToolTip("\n".join(tip_lines))
                self.history_table.setItem(i, col, item)
            # Queue a thumb request (skip orphans — no file to thumbnail).
            if not orphan:
                media = self._first_media_file(h.path) if h.path else ""
                if media and hasattr(self, "_history_thumb_loader"):
                    self._history_thumb_loader.request((h.path, h.title), media)
        self._refresh_history_summary()

    def _first_media_file(self, dir_path):
        if not dir_path or not os.path.isdir(dir_path):
            return ""
        try:
            for entry in sorted(os.scandir(dir_path), key=lambda e: e.name):
                if not entry.is_file():
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                # Pick the biggest video file — matches user intuition for
                # "the recording" vs a tiny preview / chat json.
                if ext in {".mp4", ".mkv", ".webm", ".mov", ".ts"}:
                    return entry.path
        except OSError:
            return ""
        return ""

    def _on_history_thumb_ready(self, row_key, pix):
        """Loader emitted a thumb — find the matching row and paint it."""
        view = getattr(self, "_history_view", None) or []
        for i, h in enumerate(view):
            if (h.path, h.title) == row_key:
                label = self.history_table.cellWidget(i, 0)
                if label is not None:
                    label.setPixmap(pix.scaled(
                        100, 56,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
                return

    # ── Hover Preview (F46) ──────────────────────────────────────────

    def _on_history_cell_hover(self, row, col):
        """When the mouse enters a history table row, start the animated
        preview on the thumbnail column (col 0)."""
        view = getattr(self, "_history_view", None) or []
        if row < 0 or row >= len(view):
            self._preview_loader.stop_preview()
            return
        h = view[row]
        if not h.path or not os.path.isdir(h.path):
            self._preview_loader.stop_preview()
            return
        # Find first media file in the recording dir
        media = ""
        for fn in os.listdir(h.path):
            if fn.lower().endswith((".mp4", ".mkv", ".ts", ".webm", ".flv")):
                media = os.path.join(h.path, fn)
                break
        if not media:
            self._preview_loader.stop_preview()
            return
        self._preview_loader.start_preview((row, "history"), media)

    def _on_preview_frame(self, row_key, pix):
        """PreviewLoader emitted a frame — update the thumbnail cell."""
        row, source = row_key
        if source == "history":
            table = self.history_table
        else:
            return
        label = table.cellWidget(row, 0)
        if label is not None:
            label.setPixmap(pix.scaled(
                100, 56,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def _on_history_search(self, _text):
        self._refresh_history_table()

    def _on_clear_history(self):
        self._history.clear()
        _db.clear_history()
        self._refresh_history_table()
        self._persist_config()
        self._set_status("Download history cleared.", "success")

    def _on_history_context_menu(self, pos):
        """Right-click menu for the history table — offers Trim for files
        that still exist on disk."""
        table = self.history_table
        idx = table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        view = getattr(self, "_history_view", list(reversed(self._history)))
        if row >= len(view):
            return
        h = view[row]
        menu = QMenu(self)
        open_act = menu.addAction("Open Folder")
        open_act.setEnabled(bool(h.path and os.path.isdir(h.path)))
        trim_act = menu.addAction("Trim / Clip...")
        trim_act.setEnabled(bool(h.path and os.path.isdir(h.path)))
        bundle_act = menu.addAction("Export share bundle (.zip)...")
        bundle_act.setEnabled(bool(h.path and os.path.isdir(h.path)))
        verify_act = menu.addAction("Verify integrity manifest")
        verify_act.setEnabled(bool(h.path and os.path.isdir(h.path)))
        rescan_manifest_act = menu.addAction("Rescan integrity manifest")
        rescan_manifest_act.setEnabled(bool(h.path and os.path.isdir(h.path)))
        transcribe_act = menu.addAction("Transcribe (Whisper)...")
        transcribe_act.setEnabled(bool(h.path and os.path.isdir(h.path)))
        silence_act = menu.addAction("Remove silence...")
        silence_act.setEnabled(bool(h.path and os.path.isdir(h.path)))
        # Chat highlights (F8) — only if chat.jsonl exists
        has_chat = bool(
            h.path and os.path.isdir(h.path)
            and os.path.isfile(os.path.join(h.path, "chat.jsonl"))
        )
        chat_highlights_act = menu.addAction("Show chat highlights")
        chat_highlights_act.setEnabled(has_chat)
        chat_render_act = menu.addAction("Render chat overlay...")
        chat_render_act.setEnabled(has_chat)
        chat_preview_act = menu.addAction("Preview chat render (60s)")
        chat_preview_act.setEnabled(has_chat)
        storyboard_act = menu.addAction("Generate storyboard")
        storyboard_act.setEnabled(bool(h.path and os.path.isdir(h.path)))
        highlight_act = menu.addAction("Generate highlights (AI)")
        highlight_act.setEnabled(bool(h.path and os.path.isdir(h.path)))
        menu.addSeparator()
        # Watch status + bookmarks (F38)
        watched_label = "Mark as unwatched" if getattr(h, "watched", False) else "Mark as watched"
        watch_act = menu.addAction(watched_label)
        fav_label = "Remove from favorites" if getattr(h, "favorite", False) else "Add to favorites"
        fav_act = menu.addAction(fav_label)
        bookmark_act = menu.addAction("Add bookmark…")
        # Multi-stream sync (F54) — show when 2+ rows are selected
        selected_rows = sorted({idx.row() for idx in table.selectionModel().selectedRows()})
        sync_entries = [view[r] for r in selected_rows if 0 <= r < len(view)]
        sync_act = menu.addAction(f"Watch together ({len(sync_entries)} streams)")
        sync_act.setEnabled(len(sync_entries) >= 2)
        menu.addSeparator()
        redownload_act = menu.addAction("Re-download")
        redownload_act.setEnabled(bool(h.url))
        rename_act = menu.addAction("Batch Rename…")
        remove_act = menu.addAction("Remove from History")
        # Orphan cleanup (F14) — only show when orphans exist
        orphan_count = sum(
            1 for e in self._history if e.path and not os.path.isdir(e.path)
        )
        remove_missing_act = None
        if orphan_count > 0:
            remove_missing_act = menu.addAction(
                f"Remove missing entries ({orphan_count})"
            )
        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        if chosen == sync_act and len(sync_entries) >= 2:
            self._open_sync_viewer(sync_entries)
        elif chosen == open_act and h.path and os.path.isdir(h.path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(h.path))
        elif chosen == trim_act and h.path and os.path.isdir(h.path):
            self._open_clip_dialog_for_dir(h.path)
        elif chosen == bundle_act and h.path and os.path.isdir(h.path):
            self._start_bundle_export(h.path)
        elif chosen == verify_act and h.path and os.path.isdir(h.path):
            self._verify_archive_integrity(h)
        elif chosen == rescan_manifest_act and h.path and os.path.isdir(h.path):
            self._rescan_archive_manifest(h)
        elif chosen == transcribe_act and h.path and os.path.isdir(h.path):
            self._start_transcribe_for_dir(h.path)
        elif chosen == silence_act and h.path and os.path.isdir(h.path):
            self._run_silence_removal_for_dir(h.path)
        elif chosen == chat_highlights_act and has_chat:
            self._show_chat_highlights(h.path)
        elif chosen == chat_render_act and has_chat:
            self._start_chat_render(h.path, preview_secs=0)
        elif chosen == chat_preview_act and has_chat:
            self._start_chat_render(h.path, preview_secs=60)
        elif chosen == storyboard_act and h.path:
            self._generate_storyboard(h.path)
        elif chosen == highlight_act and h.path:
            self._generate_highlights(h.path)
        elif chosen == watch_act:
            h.watched = not getattr(h, "watched", False)
            if h.watched:
                h.watch_position_secs = 0.0
            if getattr(h, "db_id", 0):
                _db.update_history_entry(h.db_id, {
                    "watched": h.watched,
                    "watch_position_secs": h.watch_position_secs,
                })
            self._refresh_history_table()
            self._persist_config()
        elif chosen == fav_act:
            h.favorite = not getattr(h, "favorite", False)
            if getattr(h, "db_id", 0):
                _db.update_history_entry(h.db_id, {"favorite": h.favorite})
            self._refresh_history_table()
            self._persist_config()
        elif chosen == bookmark_act:
            self._add_bookmark_dialog(h)
        elif chosen == rename_act:
            from ..rename_dialog import RenameDialog
            entries = [e for e in self._history if e.path and os.path.isdir(e.path)]
            if entries:
                RenameDialog(self, entries).exec()
        elif chosen == redownload_act and h.url:
            self._redownload_from_history(h)
        elif chosen == remove_act:
            try:
                real = self._history.index(h)
                self._history.pop(real)
                if getattr(h, "db_id", 0):
                    _db.delete_history_entries([h.db_id])
                self._refresh_history_table()
                self._persist_config()
            except ValueError:
                pass
        elif remove_missing_act and chosen == remove_missing_act:
            orphans = [e for e in self._history if e.path and not os.path.isdir(e.path)]
            orphan_db_ids = [e.db_id for e in orphans if getattr(e, "db_id", 0)]
            before = len(self._history)
            self._history = [
                e for e in self._history
                if not e.path or os.path.isdir(e.path)
            ]
            removed = before - len(self._history)
            if orphan_db_ids:
                _db.delete_history_entries(orphan_db_ids)
            self._refresh_history_table()
            self._persist_config()
            self._set_status(
                f"Removed {removed} missing history entries.", "success"
            )

    def _verify_archive_integrity(self, h):
        """Verify a history row against its DB-backed or sidecar manifest."""
        if not h.path or not os.path.isdir(h.path):
            self._set_status("Recording folder is missing.", "warning")
            return
        row = _db.load_archive_manifest(getattr(h, "db_id", 0))
        manifest = row.get("manifest") if row else None
        status, details, report = verify_archive_manifest(h.path, manifest)
        if getattr(h, "db_id", 0):
            _db.update_archive_manifest_check(h.db_id, status, details)
        title = (h.title or os.path.basename(h.path) or "recording")[:60]
        self._log(f"[VERIFY] {title}: {details}")
        missing = report.get("missing", []) if isinstance(report, dict) else []
        changed = report.get("changed", []) if isinstance(report, dict) else []
        for item in (missing[:5] + changed[:5]):
            path = item.get("path", "") if isinstance(item, dict) else str(item)
            reason = item.get("reason", "missing") if isinstance(item, dict) else ""
            suffix = f" ({reason})" if reason else ""
            self._log(f"  - {path}{suffix}")
        if status == STATUS_OK:
            self._set_status(details, "success")
            self._notify_center(f"Integrity verified: {title}", "success")
        else:
            self._set_status(details, "warning")
            self._notify_center(f"Integrity drift: {title}", "warning")

    def _rescan_archive_manifest(self, h):
        """Rebaseline a history row manifest to the current file state."""
        if not h.path or not os.path.isdir(h.path):
            self._set_status("Recording folder is missing.", "warning")
            return
        try:
            manifest = rescan_archive_manifest(h.path, write_sidecar=True)
        except Exception as e:
            self._log(f"[VERIFY] Rescan failed: {e}")
            self._set_status("Integrity manifest rescan failed.", "error")
            return
        count = len(manifest.get("files", []) or [])
        details = f"Rescanned integrity manifest for {count} file(s)"
        if getattr(h, "db_id", 0):
            _db.save_archive_manifest(
                h.db_id,
                h.path,
                manifest,
                status="rescanned",
                details=details,
            )
        title = (h.title or os.path.basename(h.path) or "recording")[:60]
        self._log(f"[VERIFY] {title}: {details}")
        self._set_status(details, "success")
        self._notify_center(f"Integrity manifest updated: {title}", "success")

    def _add_bookmark_dialog(self, h):
        """Show a dialog to add a named timestamp bookmark to a recording (F38)."""
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Bookmark")
        dlg.setMinimumWidth(340)
        form = QFormLayout(dlg)
        name_input = QLineEdit()
        name_input.setPlaceholderText("e.g. Funny moment")
        form.addRow("Name:", name_input)
        time_input = QLineEdit()
        time_input.setPlaceholderText("HH:MM:SS")
        form.addRow("Timestamp:", time_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_input.text().strip() or "Bookmark"
        secs = self._parse_crop_secs(time_input.text().strip()) or 0
        if not hasattr(h, "bookmarks") or h.bookmarks is None:
            h.bookmarks = []
        h.bookmarks.append({"name": name, "secs": secs})
        if getattr(h, "db_id", 0):
            _db.update_history_entry(h.db_id, {"bookmarks": h.bookmarks})
        self._persist_config()
        self._log(f"[BOOKMARK] Added '{name}' at {time_input.text().strip()} to {h.title[:40]}")

    def _redownload_from_history(self, h):
        """Pre-fill the Download tab from a HistoryEntry and trigger fetch."""
        self._switch_tab(0)
        self.url_input.setText(h.url)
        # Restore output dir to the parent of the original recording folder
        if h.path:
            parent = os.path.dirname(h.path)
            if parent and os.path.isdir(parent):
                self.output_input.setText(parent)
        self._log(f"[RE-DOWNLOAD] {h.title or h.url[:80]}")
        self._on_fetch()

    def _show_chat_highlights(self, src_dir):
        """Show chat spike timestamps in the log and open Trim dialog."""
        jsonl = os.path.join(src_dir, "chat.jsonl")
        if not os.path.isfile(jsonl):
            return
        try:
            from ...chat.spike_detect import detect_spikes
        except Exception:
            self._log("[CHAT] Could not load spike detector.")
            return
        spikes = detect_spikes(jsonl)
        if not spikes:
            self._log("[CHAT] No chat activity spikes found.")
            self._set_status("No chat spikes detected in this recording.", "info")
            return
        self._log(f"\n[CHAT] Found {len(spikes)} chat spike(s):")
        for sp in spikes:
            t = sp["time"]
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = int(t % 60)
            self._log(
                f"  {h:02d}:{m:02d}:{s:02d}  —  "
                f"{sp['count']} msgs ({sp['score']:.1f}σ)"
            )
        self._set_status(f"{len(spikes)} chat spike(s) found — see log.", "success")
        # Open trim dialog so user sees the spike markers on the filmstrip
        self._open_clip_dialog_for_dir(src_dir)

    def _generate_storyboard(self, src_dir):
        """Run scene detection on the largest video in the folder."""
        vids = []
        try:
            for f in os.listdir(src_dir):
                ext = os.path.splitext(f)[1].lower()
                if ext in (".mp4", ".mkv", ".webm", ".ts"):
                    fp = os.path.join(src_dir, f)
                    if os.path.isfile(fp):
                        vids.append((os.path.getsize(fp), fp))
        except OSError:
            pass
        if not vids:
            self._set_status("No video files found.", "warning")
            return
        vids.sort(reverse=True)
        src = vids[0][1]
        try:
            from ...postprocess.scene_worker import SceneWorker
        except Exception:
            self._log("[SCENE] Could not load scene detector.")
            return
        worker = SceneWorker(src)
        worker.log.connect(self._log)
        worker.scenes_ready.connect(
            lambda scenes: self._on_storyboard_ready(scenes, src_dir))
        worker.progress.connect(lambda _p, s: self._set_status(s, "info"))
        self._storyboard_worker = worker
        worker.start()
        self._set_status("Running scene detection…", "info")

    def _on_storyboard_ready(self, scenes, src_dir):
        if scenes:
            n = len(scenes)
            self._log(f"[SCENE] Storyboard generated — {n} scene(s).")
            self._set_status(
                f"Storyboard ready — {n} scenes. Open Trim dialog to view.",
                "success")
        else:
            self._set_status(
                "No scenes detected or scenedetect not installed.", "warning")

    def _run_silence_removal_for_dir(self, src_dir):
        """Run silence removal on the largest video in the folder."""
        vids = []
        try:
            for f in os.listdir(src_dir):
                ext = os.path.splitext(f)[1].lower()
                if ext in (".mp4", ".mkv", ".webm", ".ts"):
                    fp = os.path.join(src_dir, f)
                    if ".nosilence" not in f and os.path.isfile(fp):
                        vids.append((os.path.getsize(fp), fp))
        except OSError:
            pass
        if not vids:
            self._set_status("No video files found in folder.", "warning")
            return
        vids.sort(reverse=True)
        src = vids[0][1]
        self._log(f"[POST] Running silence removal on: {os.path.basename(src)}")
        self._set_status("Removing silence — this may take a minute…", "working")
        PostProcessor._run_silence_removal(src, self._log)
        self._set_status("Silence removal complete.", "success")

    def _start_transcribe_for_dir(self, src_dir):
        """Pick the biggest video in the folder and launch TranscribeWorker."""
        from ...postprocess import TranscribeWorker, whisper_available
        if not whisper_available():
            self._set_status(
                "No Whisper runtime installed. Install `faster-whisper` "
                "or put whisper.cpp in PATH.",
                "warning",
            )
            self._log("[TRANSCRIBE] No runtime found. See status bar.")
            return
        if getattr(self, "_transcribe_worker", None) is not None and self._transcribe_worker.isRunning():
            self._set_status("A transcription is already running.", "warning")
            return
        # Find the largest mp4/mkv/webm in the folder.
        media = None
        biggest = 0
        try:
            scan_iter = os.scandir(src_dir)
        except OSError as e:
            self._log(f"[TRANSCRIBE] Cannot read directory: {e}")
            return
        for entry in scan_iter:
            if not entry.is_file():
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in {".mp4", ".mkv", ".webm", ".mov", ".ts"}:
                try:
                    sz = entry.stat().st_size
                except OSError:
                    continue
                if sz > biggest:
                    biggest = sz
                    media = entry.path
        if not media:
            self._set_status("No video file to transcribe in that folder.", "warning")
            return
        model = str(self._config.get("whisper_model", "tiny") or "tiny")
        worker = TranscribeWorker(
            media, model_name=model,
            enable_diarization=bool(self._config.get("enable_diarization")),
            hf_token=str(self._config.get("hf_token", "") or ""),
        )
        worker.progress.connect(self._on_transcribe_progress)
        worker.done.connect(self._on_transcribe_done)
        self._transcribe_worker = worker
        self._set_status(
            f"Transcribing {os.path.basename(media)} with Whisper ({model})...",
            "processing",
        )
        worker.start()

    def _on_transcribe_progress(self, pct, status):
        self._set_status(f"Transcribing: {status} ({pct}%)", "processing")

    def _on_transcribe_done(self, ok, path_or_err):
        w = getattr(self, "_transcribe_worker", None)
        if w is not None and not w.isRunning():
            try:
                w.wait(300)
            except Exception:
                pass
        self._transcribe_worker = None
        if ok:
            self._log(f"[TRANSCRIBE] Wrote .srt / .vtt / .json / .chapters.auto.txt next to {path_or_err}")
            self._set_status(
                "Transcribe complete. SRT, VTT, and auto-chapters saved.",
                "success",
            )
            self._notify_center(f"Transcribe finished: {os.path.basename(path_or_err)}", "success")
        else:
            self._log(f"[TRANSCRIBE] {path_or_err}")
            self._set_status(f"Transcribe failed: {path_or_err}", "error")

    # ── Chat render (F22) ────────────────────────────────────────────

    def _start_chat_render(self, src_dir, preview_secs=0):
        """Launch a ChatRenderWorker for a directory that has chat.jsonl."""
        jsonl_path = os.path.join(src_dir, "chat.jsonl")
        if not os.path.isfile(jsonl_path):
            self._set_status("No chat.jsonl found in that folder.", "warning")
            return
        if getattr(self, "_chat_render_worker", None) is not None and self._chat_render_worker.isRunning():
            self._set_status("A chat render is already running.", "warning")
            return
        from ...postprocess.chat_render_worker import ChatRenderWorker
        cfg = self._config
        suffix = "_preview" if preview_secs else ""
        out_path = os.path.join(src_dir, f"chat_render{suffix}.mp4")
        worker = ChatRenderWorker(
            jsonl_path, out_path,
            width=int(cfg.get("chat_render_width", 400) or 400),
            height=int(cfg.get("chat_render_height", 600) or 600),
            font_size=int(cfg.get("chat_render_font_size", 14) or 14),
            msg_duration=float(cfg.get("chat_render_msg_duration", 8.0) or 8.0),
            bg_opacity=int(cfg.get("chat_render_bg_opacity", 180) or 180),
            preview_secs=preview_secs,
        )
        worker.progress.connect(self._on_chat_render_progress)
        worker.log.connect(self._log)
        worker.done.connect(self._on_chat_render_done)
        self._chat_render_worker = worker
        label = "preview" if preview_secs else "full"
        self._set_status(f"Rendering chat overlay ({label})...", "processing")
        worker.start()

    def _on_chat_render_progress(self, pct, status):
        self._set_status(f"Chat render: {status} ({pct}%)", "processing")

    def _on_chat_render_done(self, ok, path_or_err):
        w = getattr(self, "_chat_render_worker", None)
        if w is not None and not w.isRunning():
            try:
                w.wait(300)
            except Exception:
                pass
        self._chat_render_worker = None
        if ok:
            self._log(f"[CHAT RENDER] Output: {path_or_err}")
            self._set_status("Chat render complete.", "success")
            self._notify_center(f"Chat render done: {os.path.basename(path_or_err)}", "success")
        else:
            self._log(f"[CHAT RENDER] Failed: {path_or_err}")
            self._set_status(f"Chat render failed: {path_or_err}", "error")

    def _start_bundle_export(self, src_dir):
        """Offer a save-file dialog then run a BundleWorker. One worker at
        a time — the button / context action disables until it finishes."""
        if getattr(self, "_bundle_worker", None) is not None and self._bundle_worker.isRunning():
            self._set_status("A bundle is already in progress.", "warning")
            return
        default_name = os.path.basename(src_dir.rstrip(os.sep)) + ".zip"
        parent = os.path.dirname(src_dir.rstrip(os.sep)) or src_dir
        default_path = os.path.join(parent, default_name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export share bundle", default_path, "Zip archive (*.zip)"
        )
        if not path:
            return
        from ...postprocess import BundleWorker
        worker = BundleWorker(src_dir, path)
        worker.progress.connect(self._on_bundle_progress)
        worker.done.connect(self._on_bundle_done)
        self._bundle_worker = worker
        self._set_status(f"Bundling {os.path.basename(src_dir)}...", "processing")
        worker.start()

    def _on_bundle_progress(self, pct, status):
        self._set_status(f"Bundling: {status} ({pct}%)", "processing")

    def _on_bundle_done(self, ok, path_or_err):
        w = getattr(self, "_bundle_worker", None)
        if w is not None and not w.isRunning():
            try:
                w.wait(300)
            except Exception:
                pass
        self._bundle_worker = None
        if ok:
            self._log(f"[BUNDLE] Wrote {path_or_err}")
            self._set_status(f"Bundle ready: {path_or_err}", "success")
            self._notify_center(f"Bundle exported: {os.path.basename(path_or_err)}", "success")
        else:
            self._log(f"[BUNDLE] {path_or_err}")
            self._set_status(f"Bundle failed: {path_or_err}", "error")

    def _open_clip_dialog_for_dir(self, dir_path):
        """Offer a file picker inside the given directory, then open the
        ClipDialog on the chosen file. Used from History right-click and
        the Download-complete summary."""
        from ..clip_dialog import ClipDialog
        from ...postprocess.codecs import VIDEO_EXTS, AUDIO_EXTS
        if not dir_path or not os.path.isdir(dir_path):
            return
        exts = {e.lower() for e in (VIDEO_EXTS | AUDIO_EXTS)}
        candidates = []
        for entry in sorted(os.scandir(dir_path), key=lambda e: e.name):
            if entry.is_file() and Path(entry.name).suffix.lower() in exts:
                candidates.append(entry.path)
        if not candidates:
            self._set_status(
                "No video/audio files found in that folder to trim.",
                "warning",
            )
            return
        if len(candidates) == 1:
            target = candidates[0]
        else:
            target, _ = QFileDialog.getOpenFileName(
                self, "Choose file to trim", dir_path,
                "Media files (*.mp4 *.mkv *.webm *.mov *.ts *.mp3 *.m4a *.aac *.flac *.wav)",
            )
            if not target:
                return
        dlg = ClipDialog(self, target)
        dlg.exec()

    def _on_history_double_click(self, index):
        row = index.row()
        view = getattr(self, "_history_view", list(reversed(self._history)))
        if row >= len(view):
            return
        h = view[row]
        # If the folder exists, try to play with embedded player (F52)
        if h.path and os.path.isdir(h.path):
            media = self._find_media_in_dir(h.path)
            if media:
                self._open_player(h, media)
                return
            # No playable media — fall back to opening the folder
            QDesktopServices.openUrl(QUrl.fromLocalFile(h.path))
            return
        # Otherwise offer to retry the download if we have the URL
        if h.url:
            self._log(f"[HISTORY] Folder missing — re-fetching {h.url}")
            self._set_status(f"Re-fetching {h.title or h.url}...", "working")
            self.url_input.setText(h.url)
            self._switch_tab(0)
            self._on_fetch()
        else:
            self._set_status("Path missing and no saved URL to retry.", "warning")

    def _find_media_in_dir(self, dir_path):
        """Return the first media file path in *dir_path*, or ''."""
        _MEDIA_EXTS = (
            ".mp4", ".mkv", ".ts", ".webm", ".flv", ".mov", ".avi", ".m4v",
            ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac",
        )
        try:
            for fn in sorted(os.listdir(dir_path)):
                if fn.lower().endswith(_MEDIA_EXTS) and not fn.startswith("."):
                    return os.path.join(dir_path, fn)
        except OSError:
            pass
        return ""

    def _open_player(self, history_entry, media_path):
        """Open the embedded media player for a recording (F52)."""
        from streamkeep.player import PlayerPanel, is_mpv_available
        if not is_mpv_available():
            # Fall back to opening the folder
            self._log("[PLAYER] python-mpv not available — opening folder instead.")
            if history_entry.path and os.path.isdir(history_entry.path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(history_entry.path))
            return
        panel = PlayerPanel(self)
        start = float(getattr(history_entry, "watch_position_secs", 0) or 0)
        panel.play_file(
            media_path,
            title=history_entry.title,
            channel=history_entry.channel,
            start_secs=start,
            history_entry=history_entry,
        )

        def _on_close(pos):
            # Persist playback position (F38 watch tracking)
            history_entry.watch_position_secs = pos
            if pos > 0 and not history_entry.watched:
                # Mark as in-progress
                pass
            if getattr(history_entry, "db_id", 0):
                _db.update_history_entry(history_entry.db_id, {
                    "watch_position_secs": pos,
                })
            self._refresh_history_table()

        panel.position_at_close.connect(_on_close)
        panel.exec()

    def _open_sync_viewer(self, history_entries):
        """Open the multi-stream sync viewer for 2-4 recordings (F54)."""
        from streamkeep.player.sync_viewer import SyncViewer
        from streamkeep.player.mpv_widget import is_mpv_available
        if not is_mpv_available():
            self._set_status("python-mpv not available for sync viewer.", "warning")
            return
        viewer = SyncViewer(self)
        for h in history_entries[:4]:
            media = self._find_media_in_dir(h.path) if h.path else ""
            if media:
                viewer.add_stream(media, label=f"{h.channel}: {h.title[:30]}")
        if not viewer._slots:
            self._set_status("No playable media found in selected entries.", "warning")
            viewer.close()
            return
        viewer.start_all()
        viewer.exec()

    def _generate_highlights(self, recording_dir):
        """Run AI highlight detection on a recording (F57)."""
        from streamkeep.intelligence.highlight import HighlightWorker
        self._set_status("Analyzing for highlights...", "processing")
        worker = HighlightWorker(recording_dir, top_n=10)
        worker.log.connect(self._log)

        def _on_done(results):
            if not results:
                self._set_status("No highlights found (needs chat/audio/scene data).", "warning")
                return
            lines = []
            for start, end, score, reason in results:
                h = int(start) // 3600
                m = (int(start) % 3600) // 60
                s = int(start) % 60
                ts = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
                lines.append(f"  {ts} (score {score:.1f}) - {reason}")
            self._log("[HIGHLIGHT] Top highlights:\n" + "\n".join(lines))
            self._set_status(f"Found {len(results)} highlight(s). See log.", "success")
            self._notify_center(f"Highlights: {len(results)} found", "info")

        worker.done.connect(_on_done)
        self._highlight_worker = worker
        worker.start()
