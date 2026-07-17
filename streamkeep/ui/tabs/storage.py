"""Storage tab — disk-usage dashboard + bulk recycle-bin deletion.

Read-only scan by default. Delete actions always route through
send2trash so nothing is ever permanently removed from inside the app.
"""

import os

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QMenu, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ...storage import scan_storage
from ...theme import CAT
from ...utils import default_output_dir as _default_output_dir, fmt_size
from ..widgets import ask_premium_confirmation, make_metric_card, style_table


class _SparklineWidget(QWidget):
    """Tiny line chart showing storage size trend (up to 90 daily samples)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []
        self.setAccessibleName("Archive size trend")
        self.setAccessibleDescription("No storage trend data")

    def set_data(self, values):
        """*values* is a list of numeric values (bytes)."""
        self._data = list(values)[-90:]
        if self._data:
            self.setAccessibleDescription(
                f"{len(self._data)} daily samples; minimum {fmt_size(min(self._data))}; "
                f"maximum {fmt_size(max(self._data))}; latest {fmt_size(self._data[-1])}"
            )
        else:
            self.setAccessibleDescription("No storage trend data")
        self.update()

    def paintEvent(self, event):
        if len(self._data) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        lo = min(self._data)
        hi = max(self._data)
        rng = hi - lo or 1
        n = len(self._data)
        step = w / max(1, n - 1)
        pen_color = QColor(CAT["accent"])
        p.setPen(pen_color)
        for i in range(n - 1):
            x1 = int(i * step)
            y1 = int(h - (self._data[i] - lo) / rng * (h - 4) - 2)
            x2 = int((i + 1) * step)
            y2 = int(h - (self._data[i + 1] - lo) / rng * (h - 4) - 2)
            p.drawLine(x1, y1, x2, y2)
        p.end()


def _apply_storage_filter(win):
    """Filter storage table rows by selected platform/channel combos."""
    plat = win.storage_platform_filter.currentText()
    chan = win.storage_channel_filter.currentText()
    groups = getattr(win, "_storage_groups", [])
    visible = 0
    for i, g in enumerate(groups):
        show = True
        if plat != "All" and g.platform != plat:
            show = False
        if chan != "All" and g.channel != chan:
            show = False
        win.storage_table.setRowHidden(i, not show)
        if show:
            visible += 1
    if hasattr(win, "storage_filter_summary"):
        summary = f"{visible} folder group(s) shown"
        if plat != "All" or chan != "All":
            summary += f" • {plat} • {chan}"
        else:
            summary += " • all sources"
        win.storage_filter_summary.setText(summary)


def build_storage_tab(win):
    """Build the Storage tab page. Stashes widget refs on `win.*`."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(14)

    hero = QFrame()
    hero.setObjectName("heroCard")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(18, 18, 18, 18)
    hero_lay.setSpacing(14)

    head = QVBoxLayout()
    head.setSpacing(4)
    kicker = QLabel("Storage")
    kicker.setObjectName("eyebrow")
    title = QLabel("See what your archive weighs, then trim it safely")
    title.setObjectName("heroTitle")
    title.setWordWrap(True)
    body = QLabel(
        "Scans your default output folder and groups recordings by "
        "platform and channel. Deletes always go through the system "
        "Recycle Bin — nothing is permanently removed from inside StreamKeep."
    )
    body.setObjectName("heroBody")
    body.setWordWrap(True)
    head.addWidget(kicker)
    head.addWidget(title)
    head.addWidget(body)
    hero_lay.addLayout(head)

    metrics = QHBoxLayout()
    metrics.setSpacing(12)
    (total_card, win.storage_total_value,
        win.storage_total_sub) = make_metric_card("Total size", "0 B", "No scan yet")
    (files_card, win.storage_files_value,
        win.storage_files_sub) = make_metric_card("Files", "0", "media items found")
    (platforms_card, win.storage_platforms_value,
        win.storage_platforms_sub) = make_metric_card("Platforms", "0", "sources represented")
    (channels_card, win.storage_channels_value,
        win.storage_channels_sub) = make_metric_card("Channels", "0", "distinct channels")
    metrics.addWidget(total_card)
    metrics.addWidget(files_card)
    metrics.addWidget(platforms_card)
    metrics.addWidget(channels_card, 1)
    hero_lay.addLayout(metrics)
    lay.addWidget(hero)

    # Action row
    action_card = QFrame()
    action_card.setObjectName("card")
    act_lay = QHBoxLayout(action_card)
    act_lay.setContentsMargins(18, 14, 18, 14)
    act_lay.setSpacing(10)
    win.storage_root_label = QLabel(
        f"Scanning: {str(_default_output_dir())}"
    )
    win.storage_root_label.setObjectName("sectionBody")
    win.storage_root_label.setWordWrap(True)
    act_lay.addWidget(win.storage_root_label, 1)
    win.storage_rescan_btn = QPushButton("Rescan")
    win.storage_rescan_btn.setObjectName("primary")
    win.storage_rescan_btn.clicked.connect(win._on_storage_rescan)
    act_lay.addWidget(win.storage_rescan_btn)
    win.storage_delete_btn = QPushButton("Recycle selected")
    win.storage_delete_btn.setObjectName("danger")
    win.storage_delete_btn.setEnabled(False)
    win.storage_delete_btn.clicked.connect(win._on_storage_delete_selected)
    act_lay.addWidget(win.storage_delete_btn)
    lay.addWidget(action_card)

    # ── Filter row (F13) ────────────────────────────────────────────
    filter_card = QFrame()
    filter_card.setObjectName("card")
    filter_wrap = QVBoxLayout(filter_card)
    filter_wrap.setContentsMargins(18, 14, 18, 14)
    filter_wrap.setSpacing(10)
    filter_copy = QVBoxLayout()
    filter_copy.setSpacing(4)
    filter_title = QLabel("Refine the Archive")
    filter_title.setObjectName("sectionTitle")
    filter_body = QLabel("Filter by platform or channel, then clear back to the full archive in one click.")
    filter_body.setObjectName("sectionBody")
    filter_body.setWordWrap(True)
    filter_copy.addWidget(filter_title)
    filter_copy.addWidget(filter_body)
    filter_wrap.addLayout(filter_copy)
    filt_lay = QHBoxLayout()
    filt_lay.setSpacing(10)
    plat_label = QLabel("Platform")
    plat_label.setObjectName("fieldLabel")
    filt_lay.addWidget(plat_label)
    win.storage_platform_filter = QComboBox()
    win.storage_platform_filter.addItem("All")
    win.storage_platform_filter.setMinimumWidth(120)
    win.storage_platform_filter.currentIndexChanged.connect(
        lambda _: _apply_storage_filter(win))
    filt_lay.addWidget(win.storage_platform_filter)
    chan_label = QLabel("Channel")
    chan_label.setObjectName("fieldLabel")
    filt_lay.addWidget(chan_label)
    win.storage_channel_filter = QComboBox()
    win.storage_channel_filter.addItem("All")
    win.storage_channel_filter.setMinimumWidth(160)
    win.storage_channel_filter.currentIndexChanged.connect(
        lambda _: _apply_storage_filter(win))
    filt_lay.addWidget(win.storage_channel_filter)
    clear_filters_btn = QPushButton("Clear Filters")
    clear_filters_btn.setObjectName("ghost")
    clear_filters_btn.clicked.connect(
        lambda: (
            win.storage_platform_filter.setCurrentIndex(0),
            win.storage_channel_filter.setCurrentIndex(0),
        )
    )
    filt_lay.addWidget(clear_filters_btn)
    filt_lay.addStretch(1)
    # Sparkline widget (F13)
    win.storage_sparkline = _SparklineWidget()
    win.storage_sparkline.setFixedSize(120, 30)
    filt_lay.addWidget(win.storage_sparkline)
    filter_wrap.addLayout(filt_lay)
    win.storage_filter_summary = QLabel("0 folder group(s) shown • all sources")
    win.storage_filter_summary.setObjectName("subtleText")
    filter_wrap.addWidget(win.storage_filter_summary)
    lay.addWidget(filter_card)

    # Table
    card = QFrame()
    card.setObjectName("card")
    card_lay = QVBoxLayout(card)
    card_lay.setContentsMargins(18, 18, 18, 18)
    card_lay.setSpacing(10)
    hdr = QLabel("Recordings by folder (newest first)")
    hdr.setObjectName("sectionTitle")
    card_lay.addWidget(hdr)

    win.storage_table = QTableWidget()
    win.storage_table.setColumnCount(7)
    win.storage_table.setHorizontalHeaderLabels(
        ["", "Platform", "Channel", "Title", "Files", "Size", "Path"]
    )
    hh = win.storage_table.horizontalHeader()
    hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
    hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
    win.storage_table.setColumnWidth(0, 112)
    win.storage_table.setColumnWidth(1, 90)
    win.storage_table.setColumnWidth(2, 180)
    win.storage_table.setColumnWidth(4, 60)
    win.storage_table.setColumnWidth(5, 92)
    win.storage_table.verticalHeader().setVisible(False)
    win.storage_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    win.storage_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    win.storage_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    win.storage_table.itemSelectionChanged.connect(win._on_storage_selection_changed)
    style_table(
        win.storage_table,
        72,
        accessible_name="Archive storage",
        accessible_description="Recording folders; use Space to select rows",
    )
    card_lay.addWidget(win.storage_table)

    win.storage_empty_label = QLabel(
        "No recordings found in the scan root. Download something, then "
        "press Rescan."
    )
    win.storage_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    win.storage_empty_label.setVisible(False)
    card_lay.addWidget(win.storage_empty_label)

    lay.addWidget(card, 1)
    return page


def _update_storage_filters(win, scan):
    """Refresh the Platform and Channel filter combos from scan results."""
    plat_combo = win.storage_platform_filter
    chan_combo = win.storage_channel_filter
    plat_combo.blockSignals(True)
    chan_combo.blockSignals(True)
    plat_combo.clear()
    plat_combo.addItem("All")
    chan_combo.clear()
    chan_combo.addItem("All")
    platforms = sorted(scan.by_platform.keys())
    channels = sorted(scan.by_channel.keys())
    for p in platforms:
        plat_combo.addItem(p)
    for c in channels:
        chan_combo.addItem(c)
    plat_combo.blockSignals(False)
    chan_combo.blockSignals(False)


def _record_daily_snapshot(win, total_bytes):
    """Persist today's total size for the sparkline trend."""
    from datetime import date
    key = date.today().isoformat()
    snapshots = win._config.get("storage_snapshots", {})
    snapshots[key] = total_bytes
    # Trim to 90 days
    sorted_keys = sorted(snapshots.keys())
    if len(sorted_keys) > 90:
        for k in sorted_keys[:-90]:
            del snapshots[k]
    win._config["storage_snapshots"] = snapshots
    return snapshots


def populate_storage_table(win, scan):
    """Fill the Storage tab's metrics + table from a StorageScan."""
    _update_storage_filters(win, scan)
    # Record daily snapshot + update sparkline
    snapshots = _record_daily_snapshot(win, scan.total_size)
    if hasattr(win, "storage_sparkline"):
        values = [snapshots[k] for k in sorted(snapshots.keys())]
        win.storage_sparkline.set_data(values)
    win.storage_total_value.setText(fmt_size(scan.total_size) if scan.total_size else "0 B")
    win.storage_total_sub.setText(
        f"{scan.total_files} media file(s)" if scan.total_files else "No scan yet"
    )
    win.storage_files_value.setText(str(scan.total_files))
    win.storage_platforms_value.setText(str(len(scan.by_platform)))
    win.storage_platforms_sub.setText(
        ", ".join(sorted(scan.by_platform.keys())[:3]) or "sources represented"
    )
    win.storage_channels_value.setText(str(len(scan.by_channel)))

    table = win.storage_table
    table.setRowCount(len(scan.groups))
    # Stash each group on the table widget so delete can find the target
    # directory from the selected row index.
    win._storage_groups = list(scan.groups)
    for i, g in enumerate(scan.groups):
        # Column 0 = thumbnail cell (lazy-filled by ThumbLoader).
        thumb_label = QLabel()
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_label.setStyleSheet(
            f"background-color: {CAT['mantle']}; border-radius: 6px; color: {CAT['overlay0']};"
        )
        thumb_label.setText("…")
        table.setCellWidget(i, 0, thumb_label)
        items = [
            g.platform,
            g.channel,
            g.title,
            str(len(g.files)),
            fmt_size(g.total_size),
            g.dir_path,
        ]
        for col, val in enumerate(items, start=1):
            item = QTableWidgetItem(val)
            if col in (1, 4, 5):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(i, col, item)
        # Pick the biggest video file in the folder for the thumbnail.
        media = None
        for f in g.files:
            ext = os.path.splitext(f.path)[1].lower()
            if ext in {".mp4", ".mkv", ".webm", ".mov", ".ts"}:
                if media is None or f.size > getattr(media, "size", 0):
                    media = f
        if media is not None and hasattr(win, "_storage_thumb_loader"):
            win._storage_thumb_loader.request(g.dir_path, media.path)
    win.storage_empty_label.setVisible(len(scan.groups) == 0)
    win.storage_delete_btn.setEnabled(False)
    _apply_storage_filter(win)


def prompt_confirm_delete(parent, group_count, total_size, sample_paths):
    """Confirmation dialog for bulk recycle-bin delete."""
    details = "\n".join(
        f"- {os.path.basename(p) or p}" for p in sample_paths[:5]
    )
    if len(sample_paths) > 5:
        details += f"\n- ...and {len(sample_paths) - 5} more"
    return ask_premium_confirmation(
        parent,
        title="Recycle selected recordings?",
        body=(
            f"Move {group_count} recording folder(s) totalling {fmt_size(total_size)} "
            "to the system Recycle Bin."
        ),
        eyebrow="STORAGE",
        badge_text="Reversible",
        tone="warning",
        summary_title="Nothing will be permanently deleted inside StreamKeep.",
        summary_body="You can still restore the folders later from the system Recycle Bin.",
        details_title="Selected folders",
        details_body=details,
        primary_label="Move to Recycle Bin",
        secondary_label="Cancel",
        default_action="secondary",
        min_width=620,
    )


# ── Storage tab handler mixin ────────────────────────────────────────

class StorageTabMixin:

    def _storage_scan_root(self):
        return self.output_input.text().strip() or str(_default_output_dir())

    def _on_storage_rescan(self):
        root = self._storage_scan_root()
        self.storage_root_label.setText(f"Scanning: {root}")
        self.storage_rescan_btn.setEnabled(False)
        try:
            scan = scan_storage(root)
        except Exception as e:
            self._log(f"[STORAGE] Scan failed: {e}")
            scan = None
        finally:
            self.storage_rescan_btn.setEnabled(True)
        if scan is None:
            return
        populate_storage_table(self, scan)
        self._set_status(
            f"Storage scan complete — {scan.total_files} file(s), "
            f"{fmt_size(scan.total_size)}.",
            "success" if scan.total_files else "idle",
        )

    def _on_storage_context_menu(self, pos):
        if not hasattr(self, "storage_table"):
            return
        idx = self.storage_table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        groups = getattr(self, "_storage_groups", None) or []
        if not (0 <= row < len(groups)):
            return
        g = groups[row]
        menu = QMenu(self)
        bundle_act = menu.addAction("Export share bundle (.zip)...")
        trim_act = menu.addAction("Trim / Clip...")
        menu.addSeparator()
        open_act = menu.addAction("Open Folder")
        chosen = menu.exec(self.storage_table.viewport().mapToGlobal(pos))
        if chosen == bundle_act:
            self._start_bundle_export(g.dir_path)
        elif chosen == trim_act:
            self._open_clip_dialog_for_dir(g.dir_path)
        elif chosen == open_act and os.path.isdir(g.dir_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(g.dir_path))

    def _on_storage_selection_changed(self):
        rows = list(self.storage_table.selectionModel().selectedRows())
        count = len(rows)
        self.storage_delete_btn.setEnabled(count > 0)
        self.storage_delete_btn.setText(
            f"Recycle {count} Selected" if count else "Recycle Selected"
        )
        groups = getattr(self, "_storage_groups", None) or []
        total_size = sum(
            groups[idx.row()].total_size
            for idx in rows
            if 0 <= idx.row() < len(groups)
        )
        if count:
            self.storage_delete_btn.setToolTip(
                f"Move {count} folder group(s) totalling {fmt_size(total_size)} to the Recycle Bin."
            )
        else:
            self.storage_delete_btn.setToolTip(
                "Select one or more folder groups to recycle them safely."
            )

    def _on_storage_delete_selected(self):
        rows = sorted(
            {idx.row() for idx in self.storage_table.selectionModel().selectedRows()},
            reverse=True,
        )
        groups_attr = getattr(self, "_storage_groups", None) or []
        targets = [groups_attr[r] for r in rows if 0 <= r < len(groups_attr)]
        if not targets:
            return
        total_size = sum(g.total_size for g in targets)
        sample_paths = [g.dir_path for g in targets]
        if not prompt_confirm_delete(self, len(targets), total_size, sample_paths):
            return
        try:
            from send2trash import send2trash as _send2trash
        except ImportError:
            self._log(
                "[STORAGE] send2trash is not installed. Refusing to delete "
                "permanently. Install with: pip install send2trash"
            )
            self._set_status(
                "send2trash not installed — recycle-bin delete unavailable. "
                "No files were changed.",
                "error",
            )
            return
        recycled = 0
        for g in targets:
            try:
                _send2trash(g.dir_path)
                recycled += 1
            except Exception as e:
                self._log(f"[STORAGE] Could not recycle {g.dir_path}: {e}")
        if recycled:
            self._log(
                f"[STORAGE] Recycled {recycled} folder(s) totalling "
                f"{fmt_size(total_size)}."
            )
        self._set_status(
            f"Recycled {recycled} of {len(targets)} folder(s).",
            "success" if recycled == len(targets) else "warning",
        )
        self._on_storage_rescan()

    def _on_storage_thumb_ready(self, row_key, pix):
        groups = getattr(self, "_storage_groups", None) or []
        for i, g in enumerate(groups):
            if g.dir_path == row_key:
                label = self.storage_table.cellWidget(i, 0)
                if label is not None:
                    label.setPixmap(pix.scaled(
                        100, 56,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
                return
