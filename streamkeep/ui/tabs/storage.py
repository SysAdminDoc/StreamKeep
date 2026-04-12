"""Storage tab — disk-usage dashboard + bulk recycle-bin deletion.

Read-only scan by default. Delete actions always route through
send2trash so nothing is ever permanently removed from inside the app.
"""

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ...utils import default_output_dir as _default_output_dir, fmt_size
from ..widgets import make_metric_card, style_table


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
    style_table(win.storage_table, 72)
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


def populate_storage_table(win, scan):
    """Fill the Storage tab's metrics + table from a StorageScan."""
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
            "background-color: #181825; border-radius: 6px; color: #6c7086;"
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


def prompt_confirm_delete(parent, group_count, total_size, sample_paths):
    """Confirmation dialog for bulk recycle-bin delete."""
    sample = "\n".join(
        f"  • {os.path.basename(p)}" for p in sample_paths[:5]
    )
    more = ""
    if len(sample_paths) > 5:
        more = f"\n  …and {len(sample_paths) - 5} more"
    msg = (
        f"Move {group_count} recording folder(s) — totalling {fmt_size(total_size)} — "
        f"to the system Recycle Bin?\n\n{sample}{more}\n\n"
        "You can restore from the Recycle Bin later."
    )
    box = QMessageBox(parent)
    box.setWindowTitle("Recycle recordings")
    box.setText(msg)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Yes
