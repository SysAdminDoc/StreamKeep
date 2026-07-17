"""Storage tab — disk-usage dashboard + bulk recycle-bin deletion.

Read-only scan by default. Delete actions always route through
send2trash so nothing is ever permanently removed from inside the app.
"""

import json
import os

from PyQt6.QtCore import QPoint, QThread, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QMenu, QPushButton, QTableView, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ...maintenance import (
    apply_maintenance, load_pending_plan, plan_maintenance, save_pending_plan,
)
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

    maintenance_card = QFrame()
    maintenance_card.setObjectName("card")
    maintenance_lay = QVBoxLayout(maintenance_card)
    maintenance_lay.setContentsMargins(18, 16, 18, 16)
    maintenance_lay.setSpacing(10)
    maintenance_title = QLabel("Archive Maintenance")
    maintenance_title.setObjectName("sectionTitle")
    maintenance_body = QLabel(
        "Preview disk-to-library imports, missing or moved recordings, backup "
        "and integrity health, disk thresholds, and search-index work before "
        "approving any change. Every applied action is backed up and audited."
    )
    maintenance_body.setObjectName("sectionBody")
    maintenance_body.setWordWrap(True)
    maintenance_lay.addWidget(maintenance_title)
    maintenance_lay.addWidget(maintenance_body)
    maintenance_actions = QHBoxLayout()
    maintenance_actions.setSpacing(8)
    win.maintenance_preview_btn = QPushButton("Preview Maintenance")
    win.maintenance_preview_btn.setObjectName("primary")
    win.maintenance_preview_btn.clicked.connect(win._on_maintenance_preview)
    maintenance_actions.addWidget(win.maintenance_preview_btn)
    win.maintenance_apply_btn = QPushButton("Apply Approved")
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
    win.maintenance_summary = QLabel("No maintenance preview yet.")
    win.maintenance_summary.setObjectName("subtleText")
    win.maintenance_summary.setWordWrap(True)
    maintenance_lay.addWidget(win.maintenance_summary)
    win.maintenance_tree = QTreeWidget()
    win.maintenance_tree.setHeaderLabels(["Apply", "Action", "Details"])
    win.maintenance_tree.setRootIsDecorated(False)
    win.maintenance_tree.setAlternatingRowColors(True)
    win.maintenance_tree.setMinimumHeight(150)
    win.maintenance_tree.setAccessibleName("Archive maintenance preview")
    win.maintenance_tree.setAccessibleDescription(
        "Check only maintenance actions that should be applied"
    )
    win.maintenance_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    win.maintenance_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    win.maintenance_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    maintenance_lay.addWidget(win.maintenance_tree)
    lay.addWidget(maintenance_card)
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

    def _set_maintenance_running(self, running):
        self.maintenance_preview_btn.setEnabled(not running)
        self.maintenance_apply_btn.setEnabled(
            not running and getattr(self, "_maintenance_plan", None) is not None
        )
        self.maintenance_cancel_btn.setEnabled(running)

    def _on_maintenance_preview(self):
        current = getattr(self, "_maintenance_worker", None)
        if current is not None and current.isRunning():
            return
        self._maintenance_plan = None
        self.maintenance_tree.clear()
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
            self.maintenance_summary.setText("Maintenance preview cancelled. No changes were made.")
            self._set_maintenance_running(False)
            self._set_status("Maintenance preview cancelled.", "idle")
            return
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
        if not ask_premium_confirmation(
            self,
            title="Apply approved archive maintenance?",
            body=(f"Apply {len(approved)} selected action(s) from the current preview. "
                  "StreamKeep creates a backup first and records every outcome."),
            eyebrow="MAINTENANCE", badge_text="Backup first", tone="warning",
            summary_title="Only checked actions will run.",
            summary_body="If the library changed since preview, the batch is refused.",
            details_title="Approved actions", details_body="\n".join(details),
            primary_label="Create Backup and Apply", secondary_label="Cancel",
            default_action="secondary", min_width=680,
        ):
            return
        self._set_maintenance_running(True)
        self._set_status("Applying approved maintenance in the background.", "working")
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
        self._set_maintenance_running(False)
        self.maintenance_apply_btn.setEnabled(False)
        if result is None or result.status == "cancelled":
            self.maintenance_summary.setText(
                "Maintenance stopped between actions; completed actions remain audited. Preview again."
            )
            self._set_status("Maintenance cancelled safely between actions.", "warning")
            return
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
            self.maintenance_summary.setText("Stopping safely between maintenance actions…")

    def _on_maintenance_failed(self, message):
        self._maintenance_worker = None
        self._maintenance_plan = None
        self._set_maintenance_running(False)
        self.maintenance_apply_btn.setEnabled(False)
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
