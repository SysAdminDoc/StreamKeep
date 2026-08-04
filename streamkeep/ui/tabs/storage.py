"""Storage tab — disk-usage dashboard + bulk recycle-bin deletion.

Read-only scan by default. Delete actions always route through
send2trash so nothing is ever permanently removed from inside the app.
"""

import json
import os
from pathlib import Path

from PyQt6.QtCore import QPoint, QThread, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QSpinBox, QTableView,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ...maintenance import (
    apply_library_adoption, apply_library_retemplate, apply_maintenance,
    load_pending_plan, plan_library_adoption, plan_library_retemplate,
    plan_maintenance, save_pending_plan, save_retemplate_plan,
)
from ... import db as _db
from ...integrity import IntegrityScrubWorker
from ...storage import scan_storage
from ...theme import CAT
from ...utils import default_output_dir as _default_output_dir, fmt_size
from ..widgets import ask_premium_confirmation, make_metric_card, style_table
from ..storage_model import StorageFilterProxyModel, StorageTableModel


class _StorageScanWorker(QThread):
    scanned = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root

    def run(self):
        try:
            result = scan_storage(
                self.root,
                cancel_fn=self.isInterruptionRequested,
            )
            if not self.isInterruptionRequested():
                self.scanned.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class _MaintenanceWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, root, config, *, plan=None, approved=None, parent=None):
        super().__init__(parent)
        self.root = root
        self.config = dict(config or {})
        self.plan = plan
        self.approved = list(approved or ())

    def run(self):
        try:
            if self.plan is None:
                result = plan_maintenance(
                    self.root, config=self.config,
                    cancel_fn=self.isInterruptionRequested,
                )
                save_pending_plan(result)
            else:
                result = apply_maintenance(
                    self.plan, self.approved,
                    cancel_fn=self.isInterruptionRequested,
                )
            self.completed.emit(result)
        except InterruptedError:
            self.completed.emit(None)
        except Exception as exc:
            self.failed.emit(str(exc))


class _RetemplateWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, root, folder_template, file_template, config, *, plan=None,
                 approved=None, parent=None):
        super().__init__(parent)
        self.root = root
        self.folder_template = str(folder_template or "")
        self.file_template = str(file_template or "")
        self.config = dict(config or {})
        self.plan = plan
        self.approved = list(approved or ())

    def run(self):
        try:
            if self.plan is None:
                result = plan_library_retemplate(
                    self.root, self.folder_template, self.file_template,
                    config=self.config,
                    cancel_fn=self.isInterruptionRequested,
                )
                save_retemplate_plan(
                    result,
                    Path(_db.DB_PATH).parent / "maintenance" / "retemplate-plan.json",
                )
            else:
                result = apply_library_retemplate(
                    self.plan, self.approved,
                    cancel_fn=self.isInterruptionRequested,
                )
            self.completed.emit(result)
        except InterruptedError:
            self.completed.emit(None)
        except Exception as exc:
            self.failed.emit(str(exc))


class _AdoptionWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, root, archives, *, plan=None, parent=None):
        super().__init__(parent)
        self.root = root
        self.archives = list(archives or ())
        self.plan = plan

    def run(self):
        try:
            if self.plan is None:
                result = plan_library_adoption(
                    self.root, self.archives,
                    cancel_fn=self.isInterruptionRequested,
                )
            else:
                result = apply_library_adoption(
                    self.plan, cancel_fn=self.isInterruptionRequested,
                )
            self.completed.emit(result)
        except InterruptedError:
            self.completed.emit(None)
        except Exception as exc:
            self.failed.emit(str(exc))


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
    """Filter storage rows through the proxy model."""
    plat = win.storage_platform_filter.currentText()
    chan = win.storage_channel_filter.currentText()
    win.storage_proxy_model.set_filters(plat, chan)
    visible = win.storage_proxy_model.rowCount()
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
    lay.setSpacing(6)

    hero = QFrame()
    hero.setObjectName("heroCard")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(2, 2, 2, 4)
    hero_lay.setSpacing(4)

    head = QVBoxLayout()
    head.setSpacing(4)
    kicker = QLabel("Storage")
    kicker.setObjectName("eyebrow")
    kicker.setVisible(False)
    title = QLabel("Archive storage")
    title.setObjectName("heroTitle")
    title.setWordWrap(True)
    body = QLabel(
        "Disk usage, maintenance, and safe cleanup."
    )
    body.setObjectName("heroBody")
    body.setWordWrap(True)
    body.setVisible(False)
    head.addWidget(kicker)
    head.addWidget(title)
    head.addWidget(body)
    hero_lay.addLayout(head)

    metrics = QHBoxLayout()
    metrics.setSpacing(18)
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
    act_lay.setContentsMargins(4, 6, 4, 6)
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
    win.storage_adopt_btn = QPushButton("Adopt external library…")
    win.storage_adopt_btn.setObjectName("secondary")
    win.storage_adopt_btn.clicked.connect(win._on_storage_adopt)
    act_lay.addWidget(win.storage_adopt_btn)
    win.storage_delete_btn = QPushButton("Recycle selected")
    win.storage_delete_btn.setObjectName("danger")
    win.storage_delete_btn.setEnabled(False)
    win.storage_delete_btn.clicked.connect(win._on_storage_delete_selected)
    act_lay.addWidget(win.storage_delete_btn)
    lay.addWidget(action_card)

    integrity_card = QFrame()
    integrity_card.setObjectName("card")
    integrity_lay = QVBoxLayout(integrity_card)
    integrity_lay.setContentsMargins(4, 8, 4, 8)
    integrity_lay.setSpacing(6)
    integrity_title = QLabel("Rolling archive integrity")
    integrity_title.setObjectName("sectionTitle")
    integrity_lay.addWidget(integrity_title)
    integrity_body = QLabel(
        "Storage scans check manifest presence, size, and timestamps. A bounded "
        "background scrub hashes older recordings over the configured coverage period."
    )
    integrity_body.setObjectName("sectionBody")
    integrity_body.setWordWrap(True)
    integrity_body.setVisible(False)
    integrity_lay.addWidget(integrity_body)
    integrity_settings = QHBoxLayout()
    integrity_settings.setSpacing(8)
    win.integrity_scrub_enabled_check = QCheckBox("Enable rolling scrub")
    win.integrity_scrub_enabled_check.setChecked(
        bool(win._config.get("integrity_scrub_enabled", True))
    )
    win.integrity_scrub_enabled_check.setAccessibleName("Enable rolling archive integrity scrub")
    win.integrity_scrub_enabled_check.toggled.connect(win._on_integrity_settings_changed)
    integrity_settings.addWidget(win.integrity_scrub_enabled_check)
    interval_label = QLabel("Run every")
    interval_label.setObjectName("fieldLabel")
    integrity_settings.addWidget(interval_label)
    win.integrity_interval_spin = QSpinBox()
    win.integrity_interval_spin.setRange(1, 24 * 30)
    win.integrity_interval_spin.setSuffix(" h")
    win.integrity_interval_spin.setValue(
        max(1, min(24 * 30, int(win._config.get("integrity_scrub_interval_hours", 24) or 24)))
    )
    win.integrity_interval_spin.setAccessibleName("Integrity scrub interval")
    win.integrity_interval_spin.valueChanged.connect(win._on_integrity_settings_changed)
    integrity_settings.addWidget(win.integrity_interval_spin)
    period_label = QLabel("Cover in")
    period_label.setObjectName("fieldLabel")
    integrity_settings.addWidget(period_label)
    win.integrity_period_spin = QSpinBox()
    win.integrity_period_spin.setRange(1, 3650)
    win.integrity_period_spin.setSuffix(" d")
    win.integrity_period_spin.setValue(
        max(1, min(3650, int(win._config.get("integrity_scrub_period_days", 30) or 30)))
    )
    win.integrity_period_spin.setAccessibleName("Integrity scrub coverage period")
    win.integrity_period_spin.valueChanged.connect(win._on_integrity_settings_changed)
    integrity_settings.addWidget(win.integrity_period_spin)
    fraction_label = QLabel("per run")
    fraction_label.setObjectName("fieldLabel")
    integrity_settings.addWidget(fraction_label)
    win.integrity_fraction_spin = QSpinBox()
    win.integrity_fraction_spin.setRange(1, 100)
    win.integrity_fraction_spin.setSuffix(" %")
    try:
        fraction_value = int(round(float(win._config.get("integrity_scrub_fraction", 0.10)) * 100))
    except (TypeError, ValueError):
        fraction_value = 10
    win.integrity_fraction_spin.setValue(max(1, min(100, fraction_value)))
    win.integrity_fraction_spin.setAccessibleName("Integrity scrub fraction")
    win.integrity_fraction_spin.valueChanged.connect(win._on_integrity_settings_changed)
    integrity_settings.addWidget(win.integrity_fraction_spin)
    integrity_settings.addStretch(1)
    integrity_lay.addLayout(integrity_settings)
    integrity_actions = QHBoxLayout()
    integrity_actions.setSpacing(8)
    win.storage_scrub_btn = QPushButton("Run integrity scrub")
    win.storage_scrub_btn.setObjectName("secondary")
    win.storage_scrub_btn.clicked.connect(win._on_integrity_scrub)
    integrity_actions.addWidget(win.storage_scrub_btn)
    win.storage_scrub_cancel_btn = QPushButton("Cancel")
    win.storage_scrub_cancel_btn.setObjectName("ghost")
    win.storage_scrub_cancel_btn.setEnabled(False)
    win.storage_scrub_cancel_btn.clicked.connect(win._on_integrity_scrub_cancel)
    integrity_actions.addWidget(win.storage_scrub_cancel_btn)
    integrity_actions.addStretch(1)
    integrity_lay.addLayout(integrity_actions)
    win.storage_integrity_summary = QLabel("No integrity scan yet.")
    win.storage_integrity_summary.setObjectName("subtleText")
    win.storage_integrity_summary.setWordWrap(True)
    win.storage_integrity_summary.setVisible(False)
    integrity_lay.addWidget(win.storage_integrity_summary)
    win.storage_integrity_tree = QTreeWidget()
    win.storage_integrity_tree.setHeaderLabels(["Status", "Recording", "Affected file / reason"])
    win.storage_integrity_tree.setRootIsDecorated(False)
    win.storage_integrity_tree.setAlternatingRowColors(True)
    win.storage_integrity_tree.setMinimumHeight(112)
    win.storage_integrity_tree.setVisible(False)
    win.storage_integrity_tree.setAccessibleName("Archive integrity issues")
    win.storage_integrity_tree.setAccessibleDescription(
        "Manifest files whose cheap or full integrity checks found drift"
    )
    win.storage_integrity_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    win.storage_integrity_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    win.storage_integrity_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    integrity_lay.addWidget(win.storage_integrity_tree)
    lay.addWidget(integrity_card)

    maintenance_card = QFrame()
    maintenance_card.setObjectName("card")
    maintenance_lay = QVBoxLayout(maintenance_card)
    maintenance_lay.setContentsMargins(4, 8, 4, 8)
    maintenance_lay.setSpacing(6)
    maintenance_title = QLabel("Archive maintenance")
    maintenance_title.setObjectName("sectionTitle")
    maintenance_body = QLabel(
        "Preview imports, missing paths, integrity, backups, and index work before applying."
    )
    maintenance_body.setObjectName("sectionBody")
    maintenance_body.setWordWrap(True)
    maintenance_body.setVisible(False)
    maintenance_lay.addWidget(maintenance_title)
    maintenance_lay.addWidget(maintenance_body)
    maintenance_actions = QHBoxLayout()
    maintenance_actions.setSpacing(8)
    win.maintenance_preview_btn = QPushButton("Preview maintenance")
    win.maintenance_preview_btn.setObjectName("secondary")
    win.maintenance_preview_btn.clicked.connect(win._on_maintenance_preview)
    maintenance_actions.addWidget(win.maintenance_preview_btn)
    win.maintenance_apply_btn = QPushButton("Apply approved")
    win.maintenance_apply_btn.setEnabled(False)
    win.maintenance_apply_btn.clicked.connect(win._on_maintenance_apply)
    maintenance_actions.addWidget(win.maintenance_apply_btn)
    win.maintenance_cancel_btn = QPushButton("Cancel")
    win.maintenance_cancel_btn.setObjectName("ghost")
    win.maintenance_cancel_btn.setEnabled(False)
    win.maintenance_cancel_btn.clicked.connect(win._on_maintenance_cancel)
    maintenance_actions.addWidget(win.maintenance_cancel_btn)
    maintenance_actions.addStretch(1)
    maintenance_lay.addLayout(maintenance_actions)
    retemplate_row = QHBoxLayout()
    retemplate_row.setSpacing(8)
    retemplate_label = QLabel("Re-template archive")
    retemplate_label.setObjectName("fieldLabel")
    retemplate_row.addWidget(retemplate_label)
    win.retemplate_folder_input = QLineEdit()
    win.retemplate_folder_input.setPlaceholderText("Folder template, e.g. {channel}/{year}")
    win.retemplate_folder_input.setAccessibleName("New archive folder template")
    win.retemplate_folder_input.setAccessibleDescription(
        "Relative folder template used by the archive-wide migration preview"
    )
    retemplate_row.addWidget(win.retemplate_folder_input, 1)
    win.retemplate_file_input = QLineEdit()
    win.retemplate_file_input.setPlaceholderText("Filename template, e.g. {title}")
    win.retemplate_file_input.setAccessibleName("New archive filename template")
    win.retemplate_file_input.setAccessibleDescription(
        "Filename template used to rename media and matching sidecars"
    )
    retemplate_row.addWidget(win.retemplate_file_input, 1)
    win.retemplate_preview_btn = QPushButton("Preview re-template")
    win.retemplate_preview_btn.setObjectName("secondary")
    win.retemplate_preview_btn.clicked.connect(win._on_retemplate_preview)
    retemplate_row.addWidget(win.retemplate_preview_btn)
    maintenance_lay.addLayout(retemplate_row)
    win.maintenance_summary = QLabel("No maintenance preview yet.")
    win.maintenance_summary.setObjectName("subtleText")
    win.maintenance_summary.setWordWrap(True)
    win.maintenance_summary.setVisible(False)
    maintenance_lay.addWidget(win.maintenance_summary)
    win.maintenance_tree = QTreeWidget()
    win.maintenance_tree.setHeaderLabels(["Apply", "Action", "Details"])
    win.maintenance_tree.setRootIsDecorated(False)
    win.maintenance_tree.setAlternatingRowColors(True)
    win.maintenance_tree.setMinimumHeight(128)
    win.maintenance_tree.setVisible(False)
    win.maintenance_tree.setAccessibleName("Archive maintenance preview")
    win.maintenance_tree.setAccessibleDescription(
        "Check only maintenance actions that should be applied"
    )
    win.maintenance_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    win.maintenance_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    win.maintenance_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    maintenance_lay.addWidget(win.maintenance_tree)
    lay.addWidget(maintenance_card)

    adoption_card = QFrame()
    adoption_card.setObjectName("card")
    adoption_lay = QVBoxLayout(adoption_card)
    adoption_lay.setContentsMargins(4, 8, 4, 8)
    adoption_lay.setSpacing(6)
    adoption_title = QLabel("External library adoption")
    adoption_title.setObjectName("sectionTitle")
    adoption_lay.addWidget(adoption_title)
    adoption_body = QLabel(
        "Preview folders, yt-dlp archives, and sidecars before adding library rows. "
        "Media files are never moved or rewritten."
    )
    adoption_body.setObjectName("sectionBody")
    adoption_body.setWordWrap(True)
    adoption_body.setVisible(False)
    adoption_lay.addWidget(adoption_body)
    adoption_actions = QHBoxLayout()
    adoption_actions.setSpacing(8)
    win.adoption_apply_btn = QPushButton("Apply adoption preview")
    win.adoption_apply_btn.setEnabled(False)
    win.adoption_apply_btn.clicked.connect(win._on_adoption_apply)
    adoption_actions.addWidget(win.adoption_apply_btn)
    win.adoption_cancel_btn = QPushButton("Cancel")
    win.adoption_cancel_btn.setObjectName("ghost")
    win.adoption_cancel_btn.setEnabled(False)
    win.adoption_cancel_btn.clicked.connect(win._on_adoption_cancel)
    adoption_actions.addWidget(win.adoption_cancel_btn)
    adoption_actions.addStretch(1)
    adoption_lay.addLayout(adoption_actions)
    win.adoption_summary = QLabel("Choose an external library to preview adoption.")
    win.adoption_summary.setObjectName("subtleText")
    win.adoption_summary.setWordWrap(True)
    win.adoption_summary.setVisible(False)
    adoption_lay.addWidget(win.adoption_summary)
    win.adoption_tree = QTreeWidget()
    win.adoption_tree.setHeaderLabels(["Decision", "Path", "Reason"])
    win.adoption_tree.setRootIsDecorated(False)
    win.adoption_tree.setAlternatingRowColors(True)
    win.adoption_tree.setMinimumHeight(128)
    win.adoption_tree.setVisible(False)
    win.adoption_tree.setAccessibleName("External library adoption preview")
    win.adoption_tree.setAccessibleDescription(
        "Read-only adoption decisions before adding existing recordings"
    )
    win.adoption_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    win.adoption_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    win.adoption_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    adoption_lay.addWidget(win.adoption_tree)
    lay.addWidget(adoption_card)
    try:
        pending_plan = load_pending_plan()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        pending_plan = None
    if pending_plan is not None:
        QTimer.singleShot(
            0, lambda plan=pending_plan: win._on_maintenance_preview_done(plan)
        )

    # ── Filter row (F13) ────────────────────────────────────────────
    filter_card = QFrame()
    filter_card.setObjectName("toolbar")
    filter_wrap = QVBoxLayout(filter_card)
    filter_wrap.setContentsMargins(4, 6, 4, 6)
    filter_wrap.setSpacing(6)
    filter_copy = QVBoxLayout()
    filter_copy.setSpacing(4)
    filter_title = QLabel("Refine the Archive")
    filter_title.setObjectName("sectionTitle")
    filter_title.setVisible(False)
    filter_body = QLabel("Platform and channel filters.")
    filter_body.setObjectName("sectionBody")
    filter_body.setWordWrap(True)
    filter_body.setVisible(False)
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
    clear_filters_btn = QPushButton("Clear filters")
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
    card.setObjectName("dataPane")
    card_lay = QVBoxLayout(card)
    card_lay.setContentsMargins(14, 14, 14, 10)
    card_lay.setSpacing(6)
    hdr = QLabel("Recordings by folder (newest first)")
    hdr.setObjectName("sectionTitle")
    card_lay.addWidget(hdr)

    win.storage_table = QTableView()
    win.storage_model = StorageTableModel(win)
    win.storage_proxy_model = StorageFilterProxyModel(win)
    win.storage_proxy_model.setSourceModel(win.storage_model)
    win.storage_table.setModel(win.storage_proxy_model)
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
    win.storage_table.selectionModel().selectionChanged.connect(
        lambda *_args: win._on_storage_selection_changed()
    )
    win.storage_table.verticalScrollBar().valueChanged.connect(
        lambda _value: QTimer.singleShot(0, win._schedule_visible_storage_thumbnails)
    )
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
    channels = sorted({group.channel for group in scan.groups if group.channel})
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

    win.storage_model.set_groups(scan.groups)
    win.storage_empty_label.setVisible(len(scan.groups) == 0)
    win.storage_delete_btn.setEnabled(False)
    _apply_storage_filter(win)
    QTimer.singleShot(0, win._schedule_visible_storage_thumbnails)


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
        existing = getattr(self, "_storage_scan_worker", None)
        if existing is not None and existing.isRunning():
            existing.requestInterruption()
            existing.wait(500)
        root = self._storage_scan_root()
        self.storage_root_label.setText(f"Scanning: {root}")
        self.storage_rescan_btn.setEnabled(False)
        self._set_status("Scanning archive storage in the background.", "working")
        worker = _StorageScanWorker(root, self)
        worker.scanned.connect(self._on_storage_scan_done)
        worker.failed.connect(self._on_storage_scan_failed)
        worker.finished.connect(worker.deleteLater)
        self._storage_scan_worker = worker
        worker.start()

    def _on_storage_scan_done(self, scan):
        self._storage_scan_worker = None
        self.storage_rescan_btn.setEnabled(True)
        populate_storage_table(self, scan)
        self._render_integrity_issues(
            scan.integrity_issues,
            checked=scan.integrity_checked,
            source="Storage scan",
        )
        self._set_status(
            f"Storage scan complete — {scan.total_files} file(s), "
            f"{fmt_size(scan.total_size)}.",
            "success" if scan.total_files else "idle",
        )

    def _on_storage_scan_failed(self, message):
        self._storage_scan_worker = None
        self.storage_rescan_btn.setEnabled(True)
        self._log(f"[STORAGE] Scan failed: {message}")
        self._set_status("Storage scan failed. See the log for details.", "error")

    def _on_integrity_settings_changed(self, *_args):
        """Persist the small set of scrub controls shown in Storage."""
        if not hasattr(self, "integrity_scrub_enabled_check"):
            return
        self._config["integrity_scrub_enabled"] = bool(
            self.integrity_scrub_enabled_check.isChecked()
        )
        self._config["integrity_scrub_interval_hours"] = int(
            self.integrity_interval_spin.value()
        )
        self._config["integrity_scrub_period_days"] = int(
            self.integrity_period_spin.value()
        )
        self._config["integrity_scrub_fraction"] = (
            self.integrity_fraction_spin.value() / 100.0
        )
        self._persist_config()

    def _render_integrity_issues(self, issues, *, checked=0, source="Integrity scrub"):
        self.storage_integrity_tree.clear()
        issue_count = 0
        for issue in list(issues or ()):
            status = str(issue.get("status", "failed") or "failed").upper()
            recording = str(issue.get("recording_path", "") or "")
            files = list(issue.get("files", ()) or ())
            if not files:
                files = [{
                    "path": issue.get("path", "") or "manifest",
                    "reason": issue.get("details", "Integrity drift detected"),
                }]
            for affected in files:
                issue_count += 1
                detail = str(affected.get("path", "") or "manifest")
                reason = str(affected.get("reason", "") or "Integrity drift detected")
                self.storage_integrity_tree.addTopLevelItem(
                    QTreeWidgetItem([status, recording, f"{detail}: {reason}"])
                )
        self.storage_integrity_tree.setVisible(bool(issue_count))
        self.storage_integrity_summary.setVisible(True)
        if issue_count:
            self.storage_integrity_summary.setText(
                f"{source}: {checked} manifest file(s) checked; "
                f"{issue_count} affected file/reason row(s) require review. "
                "Nothing was repaired or deleted."
            )
        else:
            self.storage_integrity_summary.setText(
                f"{source}: {checked} manifest file(s) checked; no drift reported."
            )

    def _set_integrity_running(self, running):
        self.storage_scrub_btn.setEnabled(not running)
        self.storage_scrub_cancel_btn.setEnabled(running)
        self.integrity_scrub_enabled_check.setEnabled(not running)
        self.integrity_interval_spin.setEnabled(not running)
        self.integrity_period_spin.setEnabled(not running)
        self.integrity_fraction_spin.setEnabled(not running)

    def _start_integrity_scrub(self, *, automatic=False):
        current = getattr(self, "_integrity_worker", None)
        if current is not None and current.isRunning():
            return False
        self._set_integrity_running(True)
        self.storage_integrity_summary.setVisible(True)
        self.storage_integrity_summary.setText(
            "Running a bounded archive integrity scrub; no repair or deletion is performed…"
        )
        if not automatic:
            self._set_status("Scrubbing archive integrity in the background.", "working")
        worker = IntegrityScrubWorker(
            self._storage_scan_root(), self._config, parent=self,
        )
        worker.completed.connect(
            lambda result, automatic=automatic: self._on_integrity_scrub_done(
                result, automatic=automatic,
            )
        )
        worker.failed.connect(self._on_integrity_scrub_failed)
        worker.finished.connect(worker.deleteLater)
        self._integrity_worker = worker
        worker.start()
        return True

    def _on_integrity_scrub(self):
        self._start_integrity_scrub(automatic=False)

    def _on_integrity_scrub_done(self, result, *, automatic=False):
        self._integrity_worker = None
        self._set_integrity_running(False)
        if result is None:
            self.storage_integrity_summary.setText(
                "Integrity scrub cancelled. No repair or deletion was performed."
            )
            self._set_status("Integrity scrub cancelled safely.", "warning")
            return
        checked = int(getattr(result, "checked", 0) or 0)
        skipped = int(getattr(result, "skipped", 0) or 0)
        mismatches = int(getattr(result, "mismatches", 0) or 0)
        status = str(getattr(result, "status", "completed") or "completed")
        self._render_integrity_issues(
            getattr(result, "issues", ()),
            checked=checked,
            source="Integrity scrub",
        )
        if status == "cancelled":
            self.storage_integrity_summary.setText(
                f"Integrity scrub cancelled after {checked} recording(s); "
                f"{skipped} skipped. No repair or deletion was performed."
            )
            self._set_status("Integrity scrub cancelled safely.", "warning")
            return
        if status == "disabled":
            self.storage_integrity_summary.setText("Rolling integrity scrub is disabled in Storage settings.")
            self._set_status("Integrity scrub is disabled.", "idle")
            return
        if status == "not_due":
            self.storage_integrity_summary.setText("Integrity scrub is not due yet; the configured cadence is active.")
            if not automatic:
                self._set_status("Integrity scrub is not due yet.", "idle")
            return
        if mismatches:
            for issue in list(getattr(result, "issues", ()) or ()):
                self._notify_center(
                    f"Archive integrity drift: {issue.get('recording_path', '')}",
                    "error",
                )
            tone = "error"
        elif status == "failed":
            tone = "error"
        elif skipped:
            tone = "warning"
        else:
            tone = "success"
        self.storage_integrity_summary.setText(
            f"Integrity scrub {status}: {checked} recording(s) hashed, "
            f"{mismatches} mismatch(es), {skipped} skipped "
            f"({getattr(result, 'offline', 0)} offline). "
            "Nothing was repaired or deleted."
        )
        self._set_status(
            f"Archive integrity scrub {status}.", tone,
        )

    def _on_integrity_scrub_cancel(self):
        worker = getattr(self, "_integrity_worker", None)
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            self.storage_scrub_cancel_btn.setEnabled(False)
            self.storage_integrity_summary.setVisible(True)
            self.storage_integrity_summary.setText(
                "Stopping between recording hashes; no repair or deletion will run…"
            )

    def _on_integrity_scrub_failed(self, message):
        self._integrity_worker = None
        self._set_integrity_running(False)
        self.storage_integrity_summary.setVisible(True)
        self.storage_integrity_summary.setText(
            "Integrity scrub failed before completion. No repair or deletion was performed."
        )
        self._log(f"[INTEGRITY] {message}")
        self._set_status("Archive integrity scrub failed. See the log for details.", "error")

    def _tick_integrity_scrub(self):
        """Start one due scrub while this desktop owns execution."""
        if not getattr(self, "_queue_execution_enabled", False):
            return
        if not bool(self._config.get("integrity_scrub_enabled", True)):
            return
        worker = getattr(self, "_integrity_worker", None)
        if worker is not None and worker.isRunning():
            return
        try:
            interval_hours = max(
                1, min(24 * 30, int(float(
                    self._config.get("integrity_scrub_interval_hours", 24)
                )))
            )
        except (TypeError, ValueError, OverflowError):
            interval_hours = 24
        if not _db.integrity_scrub_is_due(interval_hours * 3600):
            return
        self._start_integrity_scrub(automatic=True)

    def _set_adoption_running(self, running):
        self.storage_adopt_btn.setEnabled(not running)
        self.adoption_apply_btn.setEnabled(
            not running and getattr(self, "_adoption_plan", None) is not None
        )
        self.adoption_cancel_btn.setEnabled(running)

    def _on_storage_adopt(self):
        current = getattr(self, "_adoption_worker", None)
        if current is not None and current.isRunning():
            return
        root = QFileDialog.getExistingDirectory(
            self, "Choose external library", self._storage_scan_root()
        )
        if not root:
            return
        archives, _selected = QFileDialog.getOpenFileNames(
            self, "Select yt-dlp archive files (optional)", root,
            "Download archives (*.txt);;All files (*.*)",
        )
        self._adoption_plan = None
        self.adoption_tree.clear()
        self.adoption_tree.setVisible(True)
        self.adoption_summary.setVisible(True)
        self.adoption_summary.setText("Building a read-only adoption preview…")
        self._set_adoption_running(True)
        self._set_status("Previewing external library adoption in the background.", "working")
        worker = _AdoptionWorker(root, archives, parent=self)
        worker.completed.connect(self._on_adoption_preview_done)
        worker.failed.connect(self._on_adoption_failed)
        worker.finished.connect(worker.deleteLater)
        self._adoption_worker = worker
        worker.start()

    def _on_adoption_preview_done(self, plan):
        self._adoption_worker = None
        if plan is None:
            self.adoption_tree.setVisible(False)
            self.adoption_summary.setVisible(True)
            self.adoption_summary.setText("Adoption preview cancelled. No changes were made.")
            self._set_adoption_running(False)
            self._set_status("Adoption preview cancelled.", "idle")
            return
        self._adoption_plan = plan
        self.adoption_tree.clear()
        for item in plan.items:
            row = QTreeWidgetItem([
                str(item.get("action", "conflict")).upper(),
                str(item.get("path", "")),
                str(item.get("reason", "")),
            ])
            self.adoption_tree.addTopLevelItem(row)
        self.adoption_tree.setVisible(bool(plan.items or plan.archive_issues))
        counts = plan.diagnostics
        self.adoption_summary.setVisible(True)
        self.adoption_summary.setText(
            f"{counts['adopt']} adopt, {counts['skip']} skip, "
            f"{counts['conflict']} conflict; {counts['archive_entries']} archive id(s). "
            "Conflicts are review-only and will not be resolved silently."
        )
        self._set_adoption_running(False)
        self._set_status("Adoption preview ready for approval.", "success")

    def _on_adoption_apply(self):
        plan = getattr(self, "_adoption_plan", None)
        if plan is None:
            return
        if not ask_premium_confirmation(
            self,
            title="Apply external library adoption?",
            body=(
                "Add only the ADOPT rows from the current preview. "
                "StreamKeep creates a backup and never moves or rewrites media."
            ),
            eyebrow="ADOPTION", badge_text="Backup first", tone="warning",
            summary_title="Existing files stay in place.",
            summary_body="If the library changed since preview, the batch is refused.",
            details_title="Preview", details_body=self.adoption_summary.text(),
            primary_label="Create Backup and Apply", secondary_label="Cancel",
            default_action="secondary", min_width=680,
        ):
            return
        self._set_adoption_running(True)
        self._set_status("Applying external library adoption in the background.", "working")
        worker = _AdoptionWorker(
            plan.root, plan.archive_paths, plan=plan, parent=self,
        )
        worker.completed.connect(self._on_adoption_apply_done)
        worker.failed.connect(self._on_adoption_failed)
        worker.finished.connect(worker.deleteLater)
        self._adoption_worker = worker
        worker.start()

    def _on_adoption_apply_done(self, result):
        self._adoption_worker = None
        self._adoption_plan = None
        self._set_adoption_running(False)
        if result is None or result.status == "cancelled":
            self.adoption_summary.setVisible(True)
            self.adoption_summary.setText("Adoption cancelled. No library changes were made.")
            self._set_status("Adoption cancelled safely.", "warning")
            return
        self.adoption_summary.setVisible(True)
        self.adoption_summary.setText(
            f"Adoption {result.status}: {result.adopted} adopted, "
            f"{result.skipped} skipped, {result.conflicts} conflict(s). "
            f"Backup: {result.backup_path or 'not created'}."
        )
        for error in result.errors:
            self._log(f"[ADOPTION] {error}")
        tone = "success" if result.status == "completed" else "warning"
        self._set_status(f"External library adoption {result.status}.", tone)
        if result.status == "completed":
            self._on_storage_rescan()

    def _on_adoption_cancel(self):
        worker = getattr(self, "_adoption_worker", None)
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            self.adoption_cancel_btn.setEnabled(False)
            self.adoption_summary.setVisible(True)
            self.adoption_summary.setText("Stopping adoption before any library changes…")

    def _on_adoption_failed(self, message):
        self._adoption_worker = None
        self._adoption_plan = None
        self._set_adoption_running(False)
        self.adoption_summary.setVisible(True)
        self.adoption_summary.setText(
            "Adoption failed before completion. No unreported change ran."
        )
        self._log(f"[ADOPTION] {message}")
        self._set_status("External library adoption failed. See the log for details.", "error")

    def _set_maintenance_running(self, running):
        self.maintenance_preview_btn.setEnabled(not running)
        self.retemplate_preview_btn.setEnabled(not running)
        self.maintenance_apply_btn.setEnabled(
            not running and getattr(self, "_maintenance_plan", None) is not None
        )
        self.maintenance_cancel_btn.setEnabled(running)

    def _on_retemplate_preview(self):
        current = getattr(self, "_maintenance_worker", None)
        if current is not None and current.isRunning():
            return
        self._maintenance_plan = None
        self._maintenance_mode = "retemplate"
        self.maintenance_tree.clear()
        self.maintenance_tree.setVisible(True)
        self.maintenance_summary.setVisible(True)
        self.maintenance_summary.setText("Building a read-only re-template preview…")
        self._set_maintenance_running(True)
        self._set_status("Previewing the archive re-template in the background.", "working")
        worker = _RetemplateWorker(
            self._storage_scan_root(),
            self.retemplate_folder_input.text().strip(),
            self.retemplate_file_input.text().strip(),
            self._config,
            parent=self,
        )
        worker.completed.connect(self._on_maintenance_preview_done)
        worker.failed.connect(self._on_maintenance_failed)
        worker.finished.connect(worker.deleteLater)
        self._maintenance_worker = worker
        worker.start()

    def _on_maintenance_preview(self):
        current = getattr(self, "_maintenance_worker", None)
        if current is not None and current.isRunning():
            return
        self._maintenance_plan = None
        self._maintenance_mode = "maintenance"
        self.maintenance_tree.clear()
        self.maintenance_tree.setVisible(True)
        self.maintenance_summary.setVisible(True)
        self.maintenance_summary.setText("Building a read-only archive preview…")
        self._set_maintenance_running(True)
        self._set_status("Previewing archive maintenance in the background.", "working")
        worker = _MaintenanceWorker(
            self._storage_scan_root(), self._config, parent=self
        )
        worker.completed.connect(self._on_maintenance_preview_done)
        worker.failed.connect(self._on_maintenance_failed)
        worker.finished.connect(worker.deleteLater)
        self._maintenance_worker = worker
        worker.start()

    def _on_maintenance_preview_done(self, plan):
        self._maintenance_worker = None
        if plan is None:
            self.maintenance_tree.setVisible(False)
            self.maintenance_summary.setVisible(True)
            self.maintenance_summary.setText("Maintenance preview cancelled. No changes were made.")
            self._set_maintenance_running(False)
            self._set_status("Maintenance preview cancelled.", "idle")
            return
        if plan.diagnostics.get("kind") == "retemplate":
            return self._on_retemplate_preview_done(plan)
        self._maintenance_plan = plan
        self.maintenance_tree.clear()
        for action in plan.actions:
            item = QTreeWidgetItem(["", action.label, action.detail])
            item.setData(0, Qt.ItemDataRole.UserRole, action.action_id)
            item.setCheckState(
                0, Qt.CheckState.Unchecked if action.kind == "remove_missing"
                else Qt.CheckState.Checked,
            )
            if action.kind == "remove_missing":
                item.setToolTip(0, "Destructive library cleanup is never preselected.")
            self.maintenance_tree.addTopLevelItem(item)
        self.maintenance_tree.setVisible(bool(plan.actions))
        self.maintenance_summary.setVisible(True)
        diag = plan.diagnostics
        library = diag["library"]
        disk = diag["disk"]
        database = diag["database"]
        backup_status = diag["backup"]["status"]
        self.maintenance_summary.setText(
            f"{len(plan.actions)} proposed action(s): {library['untracked']} orphaned on disk, "
            f"{library['missing']} missing, {library['moved']} moved. "
            f"Database: {database.get('quick_check', 'unknown')}; "
            f"backup: {backup_status}; disk: {disk['status']} "
            f"({disk['free_gb']:.2f} GiB free; warning at {disk['warning_gb']:.2f}, "
            f"critical at {disk['critical_gb']:.2f})."
        )
        self._set_maintenance_running(False)
        self._set_status("Maintenance preview ready for approval.", "success")

    def _on_retemplate_preview_done(self, plan):
        self._maintenance_mode = "retemplate"
        self._maintenance_plan = plan
        self.maintenance_tree.clear()
        for action in plan.actions:
            ready = (
                action.kind == "retemplate"
                and action.payload.get("status") == "ready"
            )
            item = QTreeWidgetItem(["", action.label, action.detail])
            item.setData(0, Qt.ItemDataRole.UserRole, action.action_id)
            item.setCheckState(0, Qt.CheckState.Checked if ready else Qt.CheckState.Unchecked)
            if not ready:
                item.setToolTip(
                    0, str(action.payload.get("reason") or "This result is review-only.")
                )
            self.maintenance_tree.addTopLevelItem(item)
        self.maintenance_tree.setVisible(bool(plan.actions))
        counts = plan.diagnostics["retemplate"]
        templates = plan.diagnostics.get("templates", {})
        self.maintenance_summary.setVisible(True)
        self.maintenance_summary.setText(
            f"Re-template preview: {counts['ready']} ready, "
            f"{counts['unchanged']} unchanged, {counts['conflicts']} conflict(s). "
            f"Folder: {templates.get('folder', '')}; file: {templates.get('file', '')}. "
            "Conflicts, reserved names, and long paths remain unchecked."
        )
        self._set_maintenance_running(False)
        self._set_status("Re-template preview ready for approval.", "success")

    def _on_maintenance_apply(self):
        plan = getattr(self, "_maintenance_plan", None)
        if plan is None:
            return
        approved = []
        details = []
        for index in range(self.maintenance_tree.topLevelItemCount()):
            item = self.maintenance_tree.topLevelItem(index)
            if item.checkState(0) == Qt.CheckState.Checked:
                approved.append(str(item.data(0, Qt.ItemDataRole.UserRole)))
                details.append(f"- {item.text(1)}: {item.text(2)}")
        if not approved:
            self._set_status("Select at least one maintenance action to apply.", "warning")
            return
        is_retemplate = getattr(self, "_maintenance_mode", "maintenance") == "retemplate"
        if not ask_premium_confirmation(
            self,
            title=("Apply approved archive re-template?" if is_retemplate
                   else "Apply approved archive maintenance?"),
            body=(f"Apply {len(approved)} selected action(s) from the current preview. "
                  "StreamKeep creates a backup first and records every outcome."),
            eyebrow=("RE-TEMPLATE" if is_retemplate else "MAINTENANCE"),
            badge_text="Backup first", tone="warning",
            summary_title="Only checked actions will run.",
            summary_body="If the library changed since preview, the batch is refused.",
            details_title="Approved actions", details_body="\n".join(details),
            primary_label="Create Backup and Apply", secondary_label="Cancel",
            default_action="secondary", min_width=680,
        ):
            return
        self._set_maintenance_running(True)
        self._set_status("Applying approved maintenance in the background.", "working")
        if is_retemplate:
            templates = plan.diagnostics.get("templates", {})
            worker = _RetemplateWorker(
                plan.root, templates.get("folder", ""), templates.get("file", ""),
                self._config, plan=plan, approved=approved, parent=self,
            )
        else:
            worker = _MaintenanceWorker(
                plan.root, self._config, plan=plan, approved=approved, parent=self
            )
        worker.completed.connect(self._on_maintenance_apply_done)
        worker.failed.connect(self._on_maintenance_failed)
        worker.finished.connect(worker.deleteLater)
        self._maintenance_worker = worker
        worker.start()

    def _on_maintenance_apply_done(self, result):
        self._maintenance_worker = None
        self._maintenance_plan = None
        self._maintenance_mode = "maintenance"
        self._set_maintenance_running(False)
        self.maintenance_apply_btn.setEnabled(False)
        if result is None or result.status == "cancelled":
            self.maintenance_summary.setVisible(True)
            self.maintenance_summary.setText(
                "Maintenance stopped between actions; completed actions remain audited. Preview again."
            )
            self._set_status("Maintenance cancelled safely between actions.", "warning")
            return
        self.maintenance_summary.setVisible(True)
        self.maintenance_summary.setText(
            f"Maintenance {result.status}: {result.applied} applied, "
            f"{result.failed} failed, {result.skipped} skipped. "
            f"Backup: {result.backup_path or 'not created'}."
        )
        for error in result.errors:
            self._log(f"[MAINTENANCE] {error}")
        tone = "success" if result.status == "completed" and not result.failed else "warning"
        self._set_status(
            f"Archive maintenance {result.status}: {result.applied} action(s) applied.", tone
        )
        self._on_storage_rescan()

    def _on_maintenance_cancel(self):
        worker = getattr(self, "_maintenance_worker", None)
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            self.maintenance_cancel_btn.setEnabled(False)
            self.maintenance_summary.setVisible(True)
            self.maintenance_summary.setText("Stopping safely between maintenance actions…")

    def _on_maintenance_failed(self, message):
        self._maintenance_worker = None
        self._maintenance_plan = None
        self._maintenance_mode = "maintenance"
        self._set_maintenance_running(False)
        self.maintenance_apply_btn.setEnabled(False)
        self.maintenance_summary.setVisible(True)
        self.maintenance_summary.setText("Maintenance failed before completion. No unreported action ran.")
        self._log(f"[MAINTENANCE] {message}")
        self._set_status("Archive maintenance failed. See the log for details.", "error")

    def _on_storage_context_menu(self, pos):
        if not hasattr(self, "storage_table"):
            return
        idx = self.storage_table.indexAt(pos)
        if not idx.isValid():
            return
        g = self.storage_proxy_model.group_at(idx.row())
        if g is None:
            return
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
        total_size = sum(
            group.total_size
            for idx in rows
            if (group := self.storage_proxy_model.group_at(idx.row())) is not None
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
        targets = [
            group for row in rows
            if (group := self.storage_proxy_model.group_at(row)) is not None
        ]
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
                continue
            try:
                _db.delete_history_for_paths([g.dir_path], reason="user")
            except Exception as e:
                self._log(
                    f"[STORAGE] Could not record tombstone for {g.dir_path}: {e}"
                )
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
        self.storage_model.set_thumbnail(row_key, pix.scaled(
            100, 56,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def _schedule_visible_storage_thumbnails(self):
        if not hasattr(self, "_storage_thumb_loader") or not hasattr(self, "storage_model"):
            return
        count = self.storage_proxy_model.rowCount()
        if not count:
            self._storage_thumb_loader.clear()
            return
        top = self.storage_table.indexAt(QPoint(0, 0)).row()
        bottom = self.storage_table.indexAt(
            QPoint(0, max(0, self.storage_table.viewport().height() - 1))
        ).row()
        if top < 0:
            top = 0
        if bottom < top:
            bottom = min(count - 1, top + 12)
        requests = []
        for row in range(max(0, top - 2), min(count, bottom + 3)):
            group = self.storage_proxy_model.group_at(row)
            if group is None:
                continue
            media = None
            for candidate in group.files:
                extension = os.path.splitext(candidate.path)[1].lower()
                if extension in {".mp4", ".mkv", ".webm", ".mov", ".ts"}:
                    if media is None or candidate.size > media.size:
                        media = candidate
            if media is not None:
                requests.append((group.dir_path, media.path))
        self._storage_thumb_loader.retain(key for key, _path in requests)
        for key, path in requests:
            self._storage_thumb_loader.request(key, path)
