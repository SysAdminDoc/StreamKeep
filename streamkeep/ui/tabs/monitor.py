"""Monitor tab — channel watch list with live detection + auto-record."""

import json
import os
import re
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from PyQt6.QtGui import QColor

from ..widgets import (
    PLATFORM_BADGES,
    ask_premium_confirmation,
    ask_premium_text_input,
    make_field_block,
    make_metric_card,
    show_premium_message,
    style_table,
)
from ...theme import CAT
from ...extractors import Extractor
from ...workers import SeedArchiveWorker, AutoRecordResolveWorker, DownloadWorker
from ...chat import ChatWorker
from ...monitor import entry_in_schedule_window
from ...models import default_media_tracks
from ...utils import default_output_dir as _default_output_dir
from ...resume import clear_resume_state


def build_monitor_tab(win):
    """Build the Monitor tab page. Stashes widget refs on `win.*`."""
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
    kicker = QLabel("Monitor")
    kicker.setObjectName("eyebrow")
    title = QLabel("Keep an eye on channels without babysitting them")
    title.setObjectName("heroTitle")
    title.setWordWrap(True)
    body = QLabel(
        "Track supported channels, watch live state changes, and "
        "automatically start recording when they go live."
    )
    body.setObjectName("heroBody")
    body.setWordWrap(True)
    hero_copy.addWidget(kicker)
    hero_copy.addWidget(title)
    hero_copy.addWidget(body)
    hero_lay.addLayout(hero_copy)

    monitor_metrics = QHBoxLayout()
    monitor_metrics.setSpacing(12)
    count_card, win.monitor_count_value, win.monitor_count_sub = make_metric_card(
        "Channels", "0", "active entries"
    )
    auto_card, win.monitor_auto_value, win.monitor_auto_sub = make_metric_card(
        "Auto Record", "0", "auto-record enabled"
    )
    live_card, win.monitor_live_value, win.monitor_live_sub = make_metric_card(
        "Live Now", "0", "currently live"
    )
    monitor_metrics.addWidget(count_card)
    monitor_metrics.addWidget(auto_card)
    monitor_metrics.addWidget(live_card)
    hero_lay.addLayout(monitor_metrics)
    lay.addWidget(hero)

    # Active Recordings panel — hidden when empty. Populated by
    # StreamKeep._refresh_active_recordings_panel as workers start/finish.
    win.active_recordings_panel = QFrame()
    win.active_recordings_panel.setObjectName("activeRecordings")
    win.active_recordings_panel.setVisible(False)
    ar_lay = QVBoxLayout(win.active_recordings_panel)
    ar_lay.setContentsMargins(16, 12, 16, 12)
    ar_lay.setSpacing(6)
    win.active_recordings_header = QLabel("Active recordings")
    win.active_recordings_header.setObjectName("sectionTitle")
    ar_lay.addWidget(win.active_recordings_header)
    win.active_recordings_rows_layout = QVBoxLayout()
    win.active_recordings_rows_layout.setSpacing(4)
    ar_lay.addLayout(win.active_recordings_rows_layout)
    lay.addWidget(win.active_recordings_panel)

    manage_card = QFrame()
    manage_card.setObjectName("card")
    manage_lay = QVBoxLayout(manage_card)
    manage_lay.setContentsMargins(18, 18, 18, 18)
    manage_lay.setSpacing(12)

    manage_header = QVBoxLayout()
    manage_header.setSpacing(4)
    sec = QLabel("Add Channel")
    sec.setObjectName("sectionTitle")
    sec_body = QLabel("Supported examples: kick.com/user or twitch.tv/user")
    sec_body.setObjectName("sectionBody")
    manage_header.addWidget(sec)
    manage_header.addWidget(sec_body)
    manage_lay.addLayout(manage_header)

    controls_row = QHBoxLayout()
    controls_row.setSpacing(12)

    url_block, url_block_lay = make_field_block(
        "Channel URL", "Paste the channel link you want StreamKeep to poll."
    )
    win.monitor_url_input = QLineEdit()
    win.monitor_url_input.setPlaceholderText("Channel URL (kick.com/user, twitch.tv/user)…")
    win.monitor_url_input.setClearButtonEnabled(True)
    url_block_lay.addWidget(win.monitor_url_input)
    controls_row.addWidget(url_block, 1)

    interval_block, interval_block_lay = make_field_block(
        "Check Every", "Polling interval"
    )
    win.monitor_interval_spin = QSpinBox()
    win.monitor_interval_spin.setRange(30, 600)
    win.monitor_interval_spin.setValue(120)
    win.monitor_interval_spin.setSuffix("s")
    interval_block_lay.addWidget(win.monitor_interval_spin)
    controls_row.addWidget(interval_block)

    auto_block, auto_block_lay = make_field_block(
        "Automation", "Live auto-record + VOD subscription"
    )
    win.monitor_auto_cb = QCheckBox("Enable auto-record (live)")
    auto_block_lay.addWidget(win.monitor_auto_cb)
    win.monitor_subscribe_cb = QCheckBox("Subscribe — queue new VODs")
    auto_block_lay.addWidget(win.monitor_subscribe_cb)
    auto_block_lay.addStretch(1)
    controls_row.addWidget(auto_block)

    add_btn = QPushButton("Add Channel")
    add_btn.setObjectName("primary")
    add_btn.clicked.connect(win._on_monitor_add)
    controls_row.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignBottom)
    manage_lay.addLayout(controls_row)

    win.monitor_summary_label = QLabel(
        "Add a channel URL to start passive live monitoring."
    )
    win.monitor_summary_label.setObjectName("subtleText")
    manage_lay.addWidget(win.monitor_summary_label)

    tools_bar = QFrame()
    tools_bar.setObjectName("toolbar")
    tools_lay = QHBoxLayout(tools_bar)
    tools_lay.setContentsMargins(14, 12, 14, 12)
    tools_lay.setSpacing(10)
    tools_copy = QVBoxLayout()
    tools_copy.setSpacing(3)
    tools_title = QLabel("Watch List Tools")
    tools_title.setObjectName("fieldLabel")
    tools_hint = QLabel(
        "Import or export a saved list, or switch into calendar mode when you want schedule context."
    )
    tools_hint.setObjectName("subtleText")
    tools_hint.setWordWrap(True)
    tools_copy.addWidget(tools_title)
    tools_copy.addWidget(tools_hint)
    tools_lay.addLayout(tools_copy, 1)
    import_btn = QPushButton("Import Watch List…")
    import_btn.setObjectName("secondary")
    import_btn.setFixedWidth(156)
    import_btn.clicked.connect(win._on_monitor_import)
    tools_lay.addWidget(import_btn)
    export_btn = QPushButton("Export Watch List…")
    export_btn.setObjectName("secondary")
    export_btn.setFixedWidth(156)
    export_btn.clicked.connect(win._on_monitor_export)
    tools_lay.addWidget(export_btn)
    win.monitor_view_toggle = QPushButton("Show Calendar")
    win.monitor_view_toggle.setObjectName("toggleAccent")
    win.monitor_view_toggle.setFixedWidth(148)
    win.monitor_view_toggle.setCheckable(True)
    tools_lay.addWidget(win.monitor_view_toggle)
    manage_lay.addWidget(tools_bar)

    lay.addWidget(manage_card)

    table_card = QFrame()
    table_card.setObjectName("card")
    table_lay = QVBoxLayout(table_card)
    table_lay.setContentsMargins(18, 18, 18, 18)
    table_lay.setSpacing(10)

    table_header = QVBoxLayout()
    table_header.setSpacing(4)
    table_title = QLabel("Watch List")
    table_title.setObjectName("sectionTitle")
    win.monitor_table_hint = QLabel(
        "Entries refresh automatically and can trigger auto-recording "
        "when a stream goes live."
    )
    win.monitor_table_hint.setObjectName("sectionBody")
    win.monitor_table_hint.setWordWrap(True)
    table_header.addWidget(table_title)
    table_header.addWidget(win.monitor_table_hint)
    table_lay.addLayout(table_header)

    win.monitor_table = QTableWidget()
    win.monitor_table.setColumnCount(6)
    win.monitor_table.setHorizontalHeaderLabels(
        ["Platform", "Channel", "Status", "Interval", "Auto-Record", ""]
    )
    mh = win.monitor_table.horizontalHeader()
    mh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    mh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    mh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    mh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    mh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    mh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
    win.monitor_table.setColumnWidth(0, 84)
    win.monitor_table.setColumnWidth(2, 90)
    win.monitor_table.setColumnWidth(3, 84)
    win.monitor_table.setColumnWidth(4, 108)
    win.monitor_table.setColumnWidth(5, 110)
    win.monitor_table.verticalHeader().setVisible(False)
    win.monitor_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.monitor_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    style_table(
        win.monitor_table,
        44,
        accessible_name="Monitored channels",
        accessible_description="Channels and their current live and recording state",
    )
    table_lay.addWidget(win.monitor_table)

    lay.addWidget(table_card, 1)

    # ── Schedule Calendar (F39) ───────────────────────────────────
    from ..calendar_widget import ScheduleCalendar
    cal_card = QFrame()
    cal_card.setObjectName("card")
    cal_card.setVisible(False)
    cal_lay = QVBoxLayout(cal_card)
    cal_lay.setContentsMargins(18, 14, 18, 14)
    cal_lay.setSpacing(8)
    cal_header = QVBoxLayout()
    cal_header.setSpacing(4)
    cal_hdr = QLabel("Stream Schedule")
    cal_hdr.setObjectName("sectionTitle")
    cal_header.addWidget(cal_hdr)
    cal_hint = QLabel(
        "Calendar view plots cached Twitch schedule windows in your local time so you can spot conflicts, quiet days, and upcoming recording opportunities at a glance."
    )
    cal_hint.setObjectName("sectionBody")
    cal_hint.setWordWrap(True)
    cal_header.addWidget(cal_hint)
    cal_lay.addLayout(cal_header)
    win.schedule_calendar = ScheduleCalendar()
    win.schedule_calendar.refresh_btn.clicked.connect(win._on_refresh_schedules)
    win.schedule_calendar.block_clicked.connect(win._on_schedule_block_clicked)
    cal_lay.addWidget(win.schedule_calendar)
    win._schedule_cal_card = cal_card
    lay.addWidget(cal_card, 1)

    def _on_view_toggle(checked):
        table_card.setVisible(not checked)
        cal_card.setVisible(checked)
        win.monitor_view_toggle.setText(
            "Show Watch List" if checked else "Show Calendar"
        )
        if checked:
            cache = win._config.get("schedules", {})
            win.schedule_calendar.set_cache(cache)
        win._refresh_monitor_summary()

    win.monitor_view_toggle.toggled.connect(_on_view_toggle)

    win._refresh_monitor_summary()
    return page


# ── MonitorTabMixin ──────────────────────────────────────────────────────
# Extracted from main_window.StreamKeep so the god-class shrinks while
# every method signature, attribute access, and line of logic stays
# byte-identical.

class MonitorTabMixin:
    """Monitor-tab handler methods, mixed into StreamKeep."""

    # ── Summary / metrics ───────────────────────────────────────────

    def _refresh_monitor_summary(self):
        if not hasattr(self, "monitor_count_value"):
            return
        entries = self.monitor.entries
        total = len(entries)
        auto = sum(1 for e in entries if e.auto_record)
        live = sum(1 for e in entries if e.last_status == "live")
        recording = sum(1 for e in entries if e.is_recording)
        pending = len(getattr(self, "_pending_auto_records", []))

        self._set_metric(self.monitor_count_value, self.monitor_count_sub, str(total), "active entries")
        self._set_metric(self.monitor_auto_value, self.monitor_auto_sub, str(auto), "auto-record enabled")
        self._set_metric(self.monitor_live_value, self.monitor_live_sub, str(live), "currently live")

        if total:
            summary_parts = [
                f"Watching {total} channel(s)",
                f"{auto} armed for auto-record",
            ]
            if live:
                summary_parts.append(f"{live} live")
            if recording:
                summary_parts.append(f"{recording} recording")
            if pending:
                summary_parts.append(f"{pending} queued to retry")
            self.monitor_summary_label.setText(" • ".join(summary_parts))
        else:
            self.monitor_summary_label.setText("Add a channel URL to start passive live monitoring.")

        if hasattr(self, "monitor_table_hint"):
            calendar_on = bool(
                hasattr(self, "monitor_view_toggle")
                and self.monitor_view_toggle.isChecked()
            )
            if calendar_on:
                if total:
                    self.monitor_table_hint.setText(
                        "Calendar view shows cached schedule windows for the channels in your watch list."
                    )
                else:
                    self.monitor_table_hint.setText(
                        "Calendar view will show cached schedule windows after you add monitored channels."
                    )
            elif total:
                if live or recording:
                    self.monitor_table_hint.setText(
                        f"Watch list updates automatically. {live} live now and {recording} currently recording."
                    )
                else:
                    self.monitor_table_hint.setText(
                        "Entries refresh automatically and stay ready to trigger auto-record when a stream goes live."
                    )
            else:
                self.monitor_table_hint.setText(
                    "Entries refresh automatically and can trigger auto-recording when a stream goes live."
                )
        self._refresh_shell_overview()

    # ── Seed workers ────────────────────────────────────────────────

    def _start_monitor_seed_worker(self, url, channel_id):
        existing = self._monitor_seed_workers.get(channel_id)
        if existing is not None and existing.isRunning():
            return
        worker = SeedArchiveWorker(url, channel_id)
        worker.log.connect(self._log)
        worker.finished.connect(self._on_monitor_seed_done)
        worker.error.connect(self._on_monitor_seed_error)
        self._monitor_seed_workers[channel_id] = worker
        worker.start()

    def _clear_monitor_seed_worker(self, channel_id):
        worker = self._monitor_seed_workers.pop(channel_id, None)
        if worker is not None and worker.isRunning():
            try:
                worker.wait(200)
            except Exception:
                pass

    # ── Drag-reorder + bulk context menu ────────────────────────────

    def _on_monitor_rows_moved(self, _parent, start, end, _dest, dest_row):
        """Qt-level rowsMoved; sync `self.monitor.entries` to the new
        order so persistence + polling match what the user sees."""
        with self.monitor._entries_lock:
            entries = self.monitor.entries
            if not (0 <= start <= end < len(entries)):
                return
            moved = entries[start:end + 1]
            del entries[start:end + 1]
            # Adjust dest if the removal shifted it left.
            insert_at = dest_row if dest_row <= start else dest_row - len(moved)
            insert_at = max(0, min(insert_at, len(entries)))
            for i, m in enumerate(moved):
                entries.insert(insert_at + i, m)
        self._persist_config()

    def _on_monitor_context_menu(self, pos):
        if not hasattr(self, "monitor_table"):
            return
        sel_rows = sorted({idx.row() for idx in self.monitor_table.selectionModel().selectedRows()})
        if not sel_rows:
            idx = self.monitor_table.indexAt(pos)
            if idx.isValid():
                sel_rows = [idx.row()]
            else:
                return
        menu = QMenu(self)
        header = menu.addAction(f"{len(sel_rows)} channel(s) selected")
        header.setEnabled(False)
        menu.addSeparator()
        act_edit = menu.addAction("Edit profile...") if len(sel_rows) == 1 else None
        act_enable_ar = menu.addAction("Enable auto-record")
        act_disable_ar = menu.addAction("Disable auto-record")
        act_set_window = menu.addAction("Set schedule window for all selected...")
        menu.addSeparator()
        act_remove = menu.addAction("Remove selected")
        chosen = menu.exec(self.monitor_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        entries = self.monitor.entries
        targets = [entries[r] for r in sel_rows if 0 <= r < len(entries)]
        if chosen == act_edit and sel_rows:
            self._on_monitor_edit(sel_rows[0])
            return
        if chosen == act_enable_ar:
            for e in targets:
                e.auto_record = True
            self._log(f"[MONITOR] Enabled auto-record on {len(targets)} channel(s).")
        elif chosen == act_disable_ar:
            for e in targets:
                e.auto_record = False
            self._log(f"[MONITOR] Disabled auto-record on {len(targets)} channel(s).")
        elif chosen == act_set_window:
            def _validate_window(value):
                text = (value or "").strip()
                if not text:
                    return True, ""
                if not re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", text):
                    return False, "Use HH:MM-HH:MM, for example 20:00-23:00."
                start, end = text.split("-", 1)
                for label, raw in (("start", start), ("end", end)):
                    try:
                        hour, minute = [int(part) for part in raw.split(":", 1)]
                    except (TypeError, ValueError):
                        return False, f"The {label} time is not valid."
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        return False, f"The {label} time must stay within 00:00-23:59."
                return True, ""

            text, ok = ask_premium_text_input(
                self,
                title="Set a shared schedule window",
                body="Limit the selected channels to a specific recording window in your local time zone.",
                eyebrow="MONITOR",
                badge_text="Schedule",
                tone="info",
                summary_title=f"Updating {len(targets)} selected channel(s).",
                summary_body="Leave the field blank to clear the window for every selected profile.",
                field_label="Schedule window",
                field_hint="Format: HH:MM-HH:MM. Example: 20:00-23:00.",
                text="20:00-23:00",
                primary_label="Apply window",
                secondary_label="Cancel",
                validator=_validate_window,
            )
            if not ok:
                return
            text = text.strip()
            if text and "-" in text:
                try:
                    start, end = text.split("-", 1)
                    for e in targets:
                        e.schedule_start_hhmm = start.strip()
                        e.schedule_end_hhmm = end.strip()
                except Exception:
                    self._set_status("Could not parse window.", "error")
                    return
            else:
                for e in targets:
                    e.schedule_start_hhmm = ""
                    e.schedule_end_hhmm = ""
            self._log(f"[MONITOR] Updated schedule window for {len(targets)} channel(s).")
        elif chosen == act_remove:
            # Remove from the bottom up so indices stay valid.
            for r in reversed(sel_rows):
                if 0 <= r < len(entries):
                    self.monitor.remove_channel(r)
        self._refresh_monitor_table()
        self._persist_config()

    # ── Import / Export Monitor Channels (F10) ──────────────────────

    def _on_monitor_export(self):
        """Export monitored channel list to a JSON or OPML file."""
        if not self.monitor.entries:
            self._set_status("No channels to export.", "warning")
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Export Monitor Channels", "streamkeep_channels.json",
            "JSON files (*.json);;OPML files (*.opml)",
        )
        if not path:
            return
        cfg = {}
        self.monitor.save_to_config(cfg)
        data = cfg.get("monitor_channels", [])
        try:
            if path.lower().endswith(".opml") or "OPML" in selected_filter:
                from ...opml import export_opml
                xml_text = export_opml(data)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(xml_text)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            self._log(f"[EXPORT] {len(data)} channels exported to {path}")
            self._set_status(f"Exported {len(data)} channels to {os.path.basename(path)}", "success")
        except OSError as e:
            self._set_status(f"Export failed: {e}", "error")

    def _on_monitor_import(self):
        """Import monitored channels from a JSON or OPML file, skipping duplicates."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Monitor Channels", "",
            "Supported files (*.json *.opml);;JSON files (*.json);;OPML files (*.opml);;All files (*)",
        )
        if not path:
            return

        if path.lower().endswith(".opml"):
            self._import_opml_file(path)
        else:
            self._import_json_file(path)

    def _import_json_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self._set_status(f"Import failed: {e}", "error")
            return
        if not isinstance(data, list):
            self._set_status("Invalid channel list — expected a JSON array.", "error")
            return
        existing_urls = {e.url for e in self.monitor.entries}
        added = 0
        skipped = 0
        for ch in data:
            if not isinstance(ch, dict) or "url" not in ch:
                continue
            if ch["url"] in existing_urls:
                skipped += 1
                continue
            ok = self.monitor.add_channel(
                ch["url"],
                ch.get("interval", 120),
                ch.get("auto_record", False),
                ch.get("subscribe_vods", False),
            )
            if ok:
                for e in self.monitor.entries:
                    if e.url == ch["url"]:
                        e.override_output_dir = str(ch.get("override_output_dir", "") or "")
                        e.override_quality_pref = str(ch.get("override_quality_pref", "") or "")
                        e.override_filename_template = str(ch.get("override_filename_template", "") or "")
                        e.schedule_start_hhmm = str(ch.get("schedule_start_hhmm", "") or "")
                        e.schedule_end_hhmm = str(ch.get("schedule_end_hhmm", "") or "")
                        try:
                            e.schedule_days_mask = int(ch.get("schedule_days_mask", 0) or 0)
                        except (TypeError, ValueError):
                            e.schedule_days_mask = 0
                        try:
                            e.retention_keep_last = int(ch.get("retention_keep_last", 0) or 0)
                        except (TypeError, ValueError):
                            e.retention_keep_last = 0
                        e.filter_keywords = str(ch.get("filter_keywords", "") or "")
                        break
                added += 1
                existing_urls.add(ch["url"])
        self._refresh_monitor_table()
        self._persist_config()
        self._log(f"[IMPORT] {added} added, {skipped} skipped (duplicate) from {path}")
        self._set_status(
            f"Imported {added} channels ({skipped} skipped as duplicates).",
            "success" if added else "warning",
        )

    def _import_opml_file(self, path):
        from ...opml import import_opml
        try:
            with open(path, "r", encoding="utf-8") as f:
                xml_text = f.read()
        except OSError as e:
            self._set_status(f"Import failed: {e}", "error")
            return
        existing_urls = {e.url for e in self.monitor.entries}
        entries, report = import_opml(xml_text, existing_urls=existing_urls)
        added = 0
        for ch in entries:
            ok = self.monitor.add_channel(ch["url"], 120, False, False)
            if ok:
                added += 1
        if added:
            self._refresh_monitor_table()
            self._persist_config()
        errors_text = f" Errors: {'; '.join(report['errors'][:3])}" if report["errors"] else ""
        self._log(
            f"[IMPORT] OPML: {report['imported']} imported, "
            f"{report['duplicates']} duplicates, {report['invalid']} invalid from {path}"
        )
        self._set_status(
            f"OPML: {added} channels added, {report['duplicates']} duplicates skipped.{errors_text}",
            "success" if added else "warning",
        )

    # ── Schedule ────────────────────────────────────────────────────

    def _on_refresh_schedules(self):
        """Refresh stream schedules in a background thread (F39 audit fix)."""
        import threading
        from PyQt6.QtCore import QTimer

        if hasattr(self, "schedule_calendar"):
            self.schedule_calendar.set_refreshing(True)

        # Thread-safe log shim: marshal _log calls back to the GUI thread.
        def _safe_log(msg, _self=self):
            QTimer.singleShot(0, lambda: _self._log(msg))

        def _bg():
            from ...schedule import refresh_schedules
            cache = dict(self._config.get("schedules", {}))
            error = ""
            try:
                cache = refresh_schedules(
                    list(self.monitor.entries), cache, log_fn=_safe_log,
                )
            except Exception as exc:
                error = str(exc)
                _safe_log(f"[SCHEDULE] Schedule refresh failed: {exc}")
            QTimer.singleShot(0, lambda: self._apply_schedule_cache(cache, error))

        threading.Thread(target=_bg, daemon=True).start()

    def _apply_schedule_cache(self, cache, error=""):
        """Apply refreshed schedule cache on the main thread."""
        self._config["schedules"] = cache
        if hasattr(self, "schedule_calendar"):
            if error:
                self.schedule_calendar.set_refresh_error(error)
            else:
                self.schedule_calendar.set_refreshing(False)
            self.schedule_calendar.set_cache(cache)
        if error:
            self._set_status("Schedule refresh failed. See log for details.", "error")
            return
        self._log("[SCHEDULE] Schedule refresh complete.")
        self._set_status("Schedule cache refreshed.", "success")

    def _on_schedule_block_clicked(self, seg):
        """Handle click on a calendar schedule block (F39)."""
        channel = seg.get("channel", "")
        title = seg.get("title", "")
        cat = seg.get("category", "")
        start_dt = None
        end_dt = None
        start_iso = seg.get("start_iso", "")
        end_iso = seg.get("end_iso", "")
        try:
            if start_iso:
                start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone()
        except (TypeError, ValueError):
            start_dt = None
        try:
            if end_iso:
                end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).astimezone()
        except (TypeError, ValueError):
            end_dt = None
        lines = []
        if channel:
            lines.append(f"Channel: {channel}")
        if start_dt:
            start_text = start_dt.strftime("%a, %b %d at %I:%M %p").replace(" 0", " ")
            lines.append(f"Starts: {start_text}")
        if end_dt:
            end_text = end_dt.strftime("%I:%M %p").replace(" 0", " ")
            lines.append(f"Ends: {end_text}")
        if cat:
            lines.append(f"Category: {cat}")
        lines.append("")
        lines.append("Open the channel profile if you want to adjust auto-record rules or retention for this stream.")

        entry_idx = next(
            (
                idx
                for idx, entry in enumerate(self.monitor.entries)
                if (getattr(entry, "channel_id", "") or "").lower() == channel.lower()
            ),
            -1,
        )
        if entry_idx >= 0:
            open_profile = ask_premium_confirmation(
                self,
                title=title or channel or "Scheduled stream",
                body="This stream is in the cached Twitch schedule for one of your monitored channels.",
                eyebrow="SCHEDULE",
                badge_text="Monitored channel",
                tone="info",
                summary_title="Open the channel profile if you want to adjust auto-record rules or retention.",
                summary_body="Schedule blocks are informational until you decide how that channel should behave.",
                details_title="Scheduled stream details",
                details_body="\n".join(lines),
                primary_label="Open Channel Profile",
                secondary_label="Close",
                default_action="secondary",
                min_width=620,
            )
            if open_profile:
                self._on_monitor_edit(entry_idx)
        else:
            show_premium_message(
                self,
                title=title or channel or "Scheduled stream",
                body="This stream is in the cached Twitch schedule, but it is not linked to an editable monitored profile right now.",
                eyebrow="SCHEDULE",
                badge_text="Schedule detail",
                tone="info",
                summary_title="Schedule windows are shown in your local timezone.",
                summary_body="Add the channel to your watch list if you want to configure auto-record behavior from here.",
                details_title="Scheduled stream details",
                details_body="\n".join(lines),
                primary_label="Close",
                min_width=620,
            )

    # ── Add / Seed / Edit / Remove ──────────────────────────────────

    def _on_monitor_add(self):
        url = self.monitor_url_input.text().strip()
        if not url:
            return
        interval = self.monitor_interval_spin.value()
        auto = self.monitor_auto_cb.isChecked()
        subscribe = self.monitor_subscribe_cb.isChecked()
        if self.monitor.add_channel(url, interval, auto, subscribe):
            ext = Extractor.detect(url)
            channel_id = ext.extract_channel_id(url) if ext else url
            self.monitor_url_input.clear()
            self._log(
                f"[MONITOR] Added: {url} (every {interval}s, "
                f"auto-record: {auto}, subscribe: {subscribe})"
            )
            # Seed the archive with current VODs so we don't download the backlog
            if subscribe and ext and ext.supports_vod_listing():
                self._log(f"[SUBSCRIBE] Seeding archive for {channel_id} in the background...")
                self._start_monitor_seed_worker(url, channel_id)
            self._persist_config()
            if subscribe and ext and ext.supports_vod_listing():
                self._set_status("Channel added. Existing VODs are being seeded in the background.", "success")
            else:
                self._set_status("Channel added to the watch list.", "success")
        else:
            self._log("[MONITOR] Cannot add: unsupported or duplicate")
            self._set_status("Channel could not be added. It may already exist or be unsupported.", "error")

    def _on_monitor_seed_done(self, channel_id, sources):
        self._clear_monitor_seed_worker(channel_id)
        if not any(e.channel_id == channel_id for e in self.monitor.entries):
            return
        self.monitor.seed_archive(channel_id, sources)
        self._persist_config()
        self._log(
            f"[SUBSCRIBE] Seeded archive with {len(sources)} existing VOD(s). "
            f"Only new VODs will be queued from now on."
        )

    def _on_monitor_seed_error(self, channel_id, err):
        self._clear_monitor_seed_worker(channel_id)
        if any(e.channel_id == channel_id for e in self.monitor.entries):
            self._log(f"[SUBSCRIBE] Seed failed for {channel_id}: {err}")

    def _refresh_monitor_table(self):
        entries = self.monitor.entries
        self.monitor_table.setRowCount(len(entries))
        for i, e in enumerate(entries):
            badge = PLATFORM_BADGES.get(e.platform, {})
            plat = QTableWidgetItem(e.platform)
            plat.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if badge.get("color"):
                plat.setForeground(QColor(badge["color"]))
            self.monitor_table.setItem(i, 0, plat)

            ch = QTableWidgetItem(e.channel_id)
            self.monitor_table.setItem(i, 1, ch)

            status = QTableWidgetItem(e.last_status.upper())
            status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if e.last_status == "live":
                status.setForeground(QColor(CAT["green"]))
            elif e.last_status == "error":
                status.setForeground(QColor(CAT["red"]))
            else:
                status.setForeground(QColor(CAT["overlay1"]))
            self.monitor_table.setItem(i, 2, status)

            intv = QTableWidgetItem(f"{e.interval_secs}s")
            intv.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.monitor_table.setItem(i, 3, intv)

            auto = QTableWidgetItem("Yes" if e.auto_record else "No")
            auto.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if e.auto_record:
                auto.setForeground(QColor(CAT["green"]))
            self.monitor_table.setItem(i, 4, auto)

            # Column 5 now carries two buttons: Edit (profile) + Remove.
            cell = QWidget()
            cell_lay = QHBoxLayout(cell)
            cell_lay.setContentsMargins(2, 2, 2, 2)
            cell_lay.setSpacing(4)
            edit_btn = QPushButton("Edit")
            edit_btn.setAccessibleName(f"Edit monitored channel {e.channel_id or e.url}")
            edit_btn.setObjectName("ghost")
            edit_btn.setFixedHeight(28)
            edit_btn.setToolTip("Edit per-channel profile: output folder, quality, schedule, retention.")
            edit_btn.clicked.connect(lambda checked, idx=i: self._on_monitor_edit(idx))
            cell_lay.addWidget(edit_btn)
            rm_btn = QPushButton("Stop" if e.is_recording else "Remove")
            rm_btn.setAccessibleName(
                f"{'Stop and remove' if e.is_recording else 'Remove'} monitored channel "
                f"{e.channel_id or e.url}"
            )
            rm_btn.setObjectName("ghost")
            rm_btn.setFixedHeight(28)
            if e.is_recording:
                rm_btn.setToolTip("Stops the active auto-recording first, then removes this channel.")
            rm_btn.clicked.connect(lambda checked, idx=i: self._on_monitor_remove(idx))
            cell_lay.addWidget(rm_btn)
            self.monitor_table.setCellWidget(i, 5, cell)

            # Show a small schedule-window glyph next to the interval column
            # when one is configured so the user can see which channels
            # are time-gated without opening the profile dialog.
            if e.schedule_start_hhmm and e.schedule_end_hhmm:
                sched_item = self.monitor_table.item(i, 3)
                if sched_item is not None:
                    sched_item.setText(
                        f"{e.interval_secs}s  ⏰ {e.schedule_start_hhmm}-{e.schedule_end_hhmm}"
                    )
        self._refresh_monitor_summary()
        self._update_tray_badge()

    def _refresh_active_recordings_panel(self):
        """Update the Monitor-tab panel that shows every currently-active
        auto-record + resolver. No-op if the panel isn't built yet (early
        calls during __init__)."""
        panel = getattr(self, "active_recordings_panel", None)
        if panel is None:
            return
        rows = []
        for ch_id in sorted(self._autorecord_resolvers.keys()):
            if self._autorecord_resolvers[ch_id].isRunning():
                rows.append((ch_id, "Resolving live URL...", False))
        for ch_id in sorted(self._autorecord_workers.keys()):
            ctx = self._autorecord_contexts.get(ch_id, {})
            status = ctx.get("last_status") or "Starting"
            had_err = bool(ctx.get("had_errors"))
            label_text = f"{ch_id} — {status}"
            if had_err:
                label_text += "  (errors)"
            rows.append((ch_id, label_text, True))
        if not rows:
            panel.setVisible(False)
            return
        panel.setVisible(True)
        # Clear existing dynamic rows (keep the header label at index 0).
        while self.active_recordings_rows_layout.count():
            item = self.active_recordings_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for _ch_id, text, _live in rows:
            label = QLabel(text)
            label.setObjectName("sectionBody")
            label.setWordWrap(True)
            self.active_recordings_rows_layout.addWidget(label)
        # Header label reflects count.
        self.active_recordings_header.setText(
            f"Active recordings ({len(rows)})"
        )

    def _on_monitor_edit(self, idx):
        """Open the per-channel profile dialog for entries[idx]."""
        if not (0 <= idx < len(self.monitor.entries)):
            return
        entry = self.monitor.entries[idx]
        from ..monitor_entry_dialog import MonitorEntryDialog
        globals_preview = {
            "output_dir": self.output_input.text().strip() or str(_default_output_dir()),
            "file_template": self._file_template or "",
            "ytdlp_arg_templates": self._config.get(
                "ytdlp_arg_templates", {}
            ),
        }
        dlg = MonitorEntryDialog(self, entry, globals_preview=globals_preview)
        if dlg.exec():
            self._refresh_monitor_table()
            self._persist_config()
            desc = []
            if entry.override_output_dir:
                desc.append("custom output dir")
            if entry.override_quality_pref:
                desc.append(f"quality={entry.override_quality_pref}")
            if entry.schedule_start_hhmm and entry.schedule_end_hhmm:
                desc.append(f"window {entry.schedule_start_hhmm}-{entry.schedule_end_hhmm}")
            if entry.retention_keep_last:
                desc.append(f"keep last {entry.retention_keep_last}")
            if entry.ytdlp_template_name:
                desc.append(f"args={entry.ytdlp_template_name}")
            self._set_status(
                f"Updated profile for {entry.channel_id}"
                + (f" — {', '.join(desc)}." if desc else " — cleared overrides."),
                "success",
            )

    def _on_monitor_remove(self, idx):
        channel_id = None
        is_recording = False
        if 0 <= idx < len(self.monitor.entries):
            channel_id = self.monitor.entries[idx].channel_id
            is_recording = bool(self.monitor.entries[idx].is_recording)
        if channel_id:
            self._pending_auto_records = [cid for cid in self._pending_auto_records if cid != channel_id]
            seed_worker = self._monitor_seed_workers.pop(channel_id, None)
            if seed_worker is not None and seed_worker.isRunning():
                try:
                    seed_worker.requestInterruption()
                    seed_worker.wait(500)
                except Exception:
                    pass
            # Stop just this channel's resolver + auto-record worker if
            # they're active. Other parallel auto-records are unaffected.
            resolve_worker = self._autorecord_resolvers.pop(channel_id, None)
            if resolve_worker is not None and resolve_worker.isRunning():
                try:
                    resolve_worker.requestInterruption()
                    resolve_worker.wait(500)
                except Exception:
                    pass
            ar_worker = self._autorecord_workers.get(channel_id)
            if is_recording and ar_worker is not None and ar_worker.isRunning():
                self._log(f"[AUTO-RECORD] Stopping active recording before removing {channel_id}")
                try:
                    ar_worker.cancel()
                    if not ar_worker.wait(5000):
                        ar_worker.terminate()
                        ar_worker.wait(1000)
                except Exception:
                    pass
                self._autorecord_workers.pop(channel_id, None)
                self._autorecord_contexts.pop(channel_id, None)
                self._refresh_active_recordings_panel()
        self.monitor.remove_channel(idx)
        self._persist_config()
        self._set_status("Channel removed from the watch list.", "success")

    # ── Retention ───────────────────────────────────────────────────

    def _apply_retention_for_channel(self, entry, out_dir):
        """If the entry has a retention limit, recycle-bin the oldest
        recordings in `out_dir` beyond the keep-last count. Logs what it
        does; does not prompt — enabling retention on the profile is the
        opt-in."""
        keep = int(getattr(entry, "retention_keep_last", 0) or 0)
        if keep <= 0 or not out_dir or not os.path.isdir(out_dir):
            return
        # Treat each immediate subdir under the channel's output root as
        # one "recording". Group per-channel_id prefix so sibling channels
        # sharing an output dir don't cannibalize each other.
        prefix = f"auto_{entry.channel_id}_" if entry.channel_id else ""
        candidates = []
        try:
            for child in os.scandir(out_dir):
                if not child.is_dir():
                    continue
                if prefix and not child.name.startswith(prefix):
                    continue
                try:
                    mtime = child.stat().st_mtime
                except OSError:
                    continue
                candidates.append((mtime, child.path))
        except OSError:
            return
        if len(candidates) <= keep:
            return
        candidates.sort(reverse=True)  # newest first
        to_remove = candidates[keep:]
        removed = 0
        for _mtime, path in to_remove:
            try:
                from send2trash import send2trash as _send2trash
                _send2trash(path)
            except ImportError:
                # send2trash not available — leave the recording in place
                # rather than permanently deleting it. Retention without
                # recycle-bin fallback is too dangerous.
                self._log(
                    "[RETENTION] send2trash not installed — skipping "
                    "retention cleanup (would otherwise recycle "
                    f"{os.path.basename(path)})."
                )
                break
            except Exception as e:
                self._log(f"[RETENTION] Could not recycle {path}: {e}")
                continue
            removed += 1
        if removed:
            self._log(
                f"[RETENTION] {entry.channel_id}: recycled {removed} old "
                f"recording(s), keeping last {keep}."
            )

    # ── Auto-record pipeline ────────────────────────────────────────

    def _queue_auto_record_retry(self, channel_id):
        if channel_id not in self._pending_auto_records:
            self._pending_auto_records.append(channel_id)
        self._refresh_monitor_summary()

    def _drain_pending_auto_records(self):
        resolver = getattr(self, "_auto_record_resolve_worker", None)
        if resolver is not None and resolver.isRunning():
            return True
        worker = getattr(self, "download_worker", None)
        if worker is not None and worker.isRunning():
            return False
        while self._pending_auto_records:
            channel_id = self._pending_auto_records.pop(0)
            if self._try_start_auto_record(channel_id):
                self._refresh_monitor_summary()
                return True
        self._refresh_monitor_summary()
        return False

    def _auto_record_error(self, channel_id, err):
        ctx = self._autorecord_contexts.get(channel_id)
        if ctx is not None:
            ctx["had_errors"] = True
        self._log(f"[AUTO-RECORD] {channel_id}: {err}")

    def _active_autorecord_count(self):
        """Number of channels currently resolving + recording via auto-record."""
        resolvers = sum(
            1 for w in self._autorecord_resolvers.values()
            if w is not None and w.isRunning()
        )
        workers = sum(
            1 for w in self._autorecord_workers.values()
            if w is not None and w.isRunning()
        )
        return resolvers + workers

    def _try_start_auto_record(self, channel_id):
        target = None
        for e in self.monitor.entries:
            if e.channel_id == channel_id and e.auto_record and not e.is_recording:
                target = e
                break
        if target is None:
            return False
        # Already recording / resolving this specific channel — nothing to do.
        if channel_id in self._autorecord_workers or channel_id in self._autorecord_resolvers:
            return False
        # Parallel cap: don't exceed `parallel_autorecords`. Foreground
        # download_worker doesn't count toward this cap — it's a separate
        # track that doesn't interfere.
        cap = max(1, int(self._parallel_autorecords or 1))
        if self._active_autorecord_count() >= cap:
            # Re-queue for the next poll tick; don't fail outright.
            self._queue_auto_record_retry(channel_id)
            return False

        # Respect the per-channel schedule window — if we're outside it,
        # defer auto-record until the next poll tick that lands inside.
        if not entry_in_schedule_window(target):
            self._log(
                f"[AUTO-RECORD] Skipping {channel_id} — outside channel's "
                f"schedule window ({target.schedule_start_hhmm}-{target.schedule_end_hhmm})"
            )
            return False
        target.is_recording = True
        self._refresh_monitor_summary()
        self._log(f"[AUTO-RECORD] Preparing recording for {target.platform}/{channel_id}")
        # Per-channel override_output_dir wins over the global Download-tab
        # output. Empty string = use global.
        base_out = (
            target.override_output_dir.strip()
            if getattr(target, "override_output_dir", "")
            else ""
        ) or self.output_input.text().strip() or str(_default_output_dir())
        worker = AutoRecordResolveWorker(channel_id, target.url, base_out)
        worker.log.connect(self._log)
        worker.resolved.connect(self._on_auto_record_resolved)
        worker.error.connect(self._on_auto_record_resolve_error)
        self._autorecord_resolvers[channel_id] = worker
        worker.start()
        return True

    def _on_auto_record_resolved(self, channel_id, info, q, out_dir):
        resolver = self._autorecord_resolvers.pop(channel_id, None)
        if resolver is not None and not resolver.isRunning():
            try:
                resolver.wait(200)
            except Exception:
                pass

        target = None
        for e in self.monitor.entries:
            if e.channel_id == channel_id and e.auto_record and e.is_recording:
                target = e
                break
        if target is None:
            self._refresh_active_recordings_panel()
            self._start_next_background_job()
            return

        # Keyword filter check (F3) — skip if title doesn't match any keyword
        keywords = (target.filter_keywords or "").strip()
        if keywords and info and info.title:
            kw_list = [k.strip().lower() for k in keywords.split(",") if k.strip()]
            title_lower = info.title.lower()
            if kw_list and not any(kw in title_lower for kw in kw_list):
                self._log(
                    f"[AUTO-RECORD] Skipping {channel_id} — title \"{info.title[:60]}\" "
                    f"does not match keywords: {keywords}"
                )
                target.is_recording = False
                self._refresh_monitor_summary()
                self._refresh_active_recordings_panel()
                self._start_next_background_job()
                return

        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            self._log(f"[AUTO-RECORD] Cannot create output folder: {e}")
            target.is_recording = False
            self._refresh_monitor_summary()
            self._refresh_active_recordings_panel()
            self._start_next_background_job()
            return

        # Honor per-channel quality preference if set. "highest" and "" are
        # both no-ops (we were already handed the top quality). For a named
        # resolution ("720p", etc.) we look for a substring match and fall
        # back to the resolver's choice if nothing matches.
        pref = (target.override_quality_pref or "").strip().lower()
        if pref and pref not in ("", "highest") and getattr(info, "qualities", None):
            chosen = None
            for candidate in info.qualities:
                cname = (candidate.name or "").lower()
                cres = (candidate.resolution or "").lower()
                if pref in cname or pref in cres:
                    chosen = candidate
                    break
            if chosen is not None and chosen is not q:
                self._log(f"[AUTO-RECORD] Using channel profile quality: {chosen.name}")
                q = chosen

        segments = [(0, "live_recording", 0, 0)]
        worker = DownloadWorker(q.url, segments, out_dir, q.format_type)
        worker.audio_url = q.audio_url
        worker.selected_tracks = default_media_tracks(q)
        worker.ytdlp_source = q.ytdlp_source
        worker.ytdlp_format = q.ytdlp_format
        worker.parallel_connections = self._parallel_connections
        from ...download_options import (
            apply_external_downloader_options,
            apply_ytdlp_transfer_options, resolve_ytdlp_arg_template,
        )
        from ...extractors.ytdlp import YtDlpExtractor
        apply_ytdlp_transfer_options(worker, YtDlpExtractor)
        apply_external_downloader_options(worker, YtDlpExtractor)
        worker.cookies_browser = YtDlpExtractor.cookies_browser
        worker.rate_limit = YtDlpExtractor.rate_limit
        worker.proxy = YtDlpExtractor.proxy
        template_name = target.ytdlp_template_name or ""
        try:
            worker.ytdlp_template_args = resolve_ytdlp_arg_template(
                self._config.get("ytdlp_arg_templates", {}), template_name,
            )
            worker.ytdlp_template_name = template_name
        except ValueError as error:
            worker.ytdlp_template_args = ()
            worker.ytdlp_template_name = ""
            self._log(f"[AUTO-RECORD] Ignoring argument template: {error}")
        # Live auto-split: when enabled, long live captures are chunked.
        if self._chunk_long_captures:
            worker.chunk_length_secs = int(self._chunk_length_secs or 0)
        worker.log.connect(self._log)
        worker.error.connect(lambda _idx, err, ch=channel_id: self._auto_record_error(ch, err))
        worker.progress.connect(
            lambda _idx, pct, status, ch=channel_id:
            self._on_autorecord_progress(ch, pct, status)
        )
        worker.all_done.connect(lambda ch=channel_id: self._auto_record_done(ch))
        self._autorecord_workers[channel_id] = worker
        self._autorecord_contexts[channel_id] = {
            "out_dir": out_dir,
            "q_name": q.name or "Live Capture",
            "info": info,
            "history_url": target.url,
            "title": getattr(info, "title", "") or channel_id,
            "had_errors": False,
            "last_status": "",
        }
        self._attach_resume_to_worker(
            worker,
            context={
                "source_url": target.url,
                "platform": getattr(info, "platform", "") or target.platform,
                "title": getattr(info, "title", "") or "",
                "channel": getattr(info, "channel", "") or channel_id,
                "quality_name": q.name or "Live Capture",
                "info": info,
            },
        )
        worker.start()
        # Kick off live chat capture alongside the recording if the user
        # has opted in. Twitch (IRC) and Kick (Pusher) are supported —
        # other platforms fall through silently.
        platform_key = (getattr(info, "platform", "") or "").lower()
        if (bool(self._config.get("capture_live_chat", False))
                and platform_key in ("twitch", "kick")):
            try:
                chat_channel = getattr(info, "channel", "") or channel_id
                chat = ChatWorker(
                    chat_channel, out_dir,
                    platform=platform_key,
                    render_ass=bool(self._config.get("render_chat_ass", True)),
                )
                chat.log.connect(self._log)
                chat.message.connect(self._on_live_chat_message)
                chat.done.connect(
                    lambda cnt, ch=channel_id: self._on_live_chat_done(ch, cnt)
                )
                self._chat_workers[channel_id] = chat
                chat.start()
            except Exception as e:
                self._log(f"[CHAT] Could not start chat capture: {e}")
        self._refresh_active_recordings_panel()
        self._set_status(
            f"Auto-record started for {channel_id}"
            + (f" ({self._active_autorecord_count()} parallel)."
               if self._active_autorecord_count() > 1 else "."),
            "working",
        )

    def _on_live_chat_message(self, nick, text):
        """Append a line to the Download-tab chat dock. Kept lightweight
        so a fast-moving stream doesn't hitch the UI."""
        if not hasattr(self, "chat_log_view"):
            return
        if hasattr(self, "chat_dock") and not self.chat_dock.isVisible():
            self.chat_dock.setVisible(True)
        safe_nick = (nick or "")[:32]
        safe_text = (text or "")[:500]
        self.chat_log_view.append(f"<b>{safe_nick}</b>: {safe_text}")

    def _on_live_chat_done(self, channel_id, count):
        worker = self._chat_workers.pop(channel_id, None)
        if worker is not None and not worker.isRunning():
            try:
                worker.wait(500)
            except Exception:
                pass
        self._log(f"[CHAT] Capture for {channel_id} ended ({count} line(s))")

    def _on_auto_record_resolve_error(self, channel_id, err):
        resolver = self._autorecord_resolvers.pop(channel_id, None)
        if resolver is not None and not resolver.isRunning():
            try:
                resolver.wait(200)
            except Exception:
                pass
        for e in self.monitor.entries:
            if e.channel_id == channel_id:
                e.is_recording = False
        self._refresh_monitor_summary()
        self._refresh_active_recordings_panel()
        self._log(f"[AUTO-RECORD] Error: {err}")
        self._set_status(f"Auto-record could not start for {channel_id}: {err}", "warning")
        self._start_next_background_job()

    def _on_autorecord_progress(self, channel_id, _pct, status):
        """Update the Active Recordings panel entry for this channel."""
        ctx = self._autorecord_contexts.get(channel_id)
        if ctx is None:
            return
        ctx["last_status"] = status or ""
        self._refresh_active_recordings_panel()

    def _on_channel_live(self, channel_id):
        """Called when a monitored channel goes live."""
        self._set_status(f"{channel_id} went live.", "warning")
        self._notify_center(f"{channel_id} is live", "warning")
        self._fire_hook("channel_live", channel=channel_id)
        if self._try_start_auto_record(channel_id):
            return
        worker = getattr(self, "download_worker", None)
        if worker is not None and worker.isRunning():
            self._queue_auto_record_retry(channel_id)
            self._log(
                f"[AUTO-RECORD] Waiting for the current download to finish before retrying {channel_id}"
            )
            self._set_status(
                f"{channel_id} is live. Auto-record will retry when the current job finishes.",
                "warning",
            )

    def _auto_record_done(self, channel_id):
        ctx = self._autorecord_contexts.pop(channel_id, None) or {}
        worker = self._autorecord_workers.pop(channel_id, None)
        if worker is not None and not worker.isRunning():
            try:
                worker.wait(500)
            except Exception:
                pass
        # Stop the paired chat capture (if any) — it flushes .jsonl and .ass
        # sidecars on clean exit.
        chat = self._chat_workers.pop(channel_id, None)
        if chat is not None and chat.isRunning():
            try:
                chat.cancel()
                chat.wait(2000)
            except Exception:
                pass
        finished_entry = None
        for e in self.monitor.entries:
            if e.channel_id == channel_id:
                e.is_recording = False
                finished_entry = e
        out_dir = ctx.get("out_dir", "")
        had_errors = bool(ctx.get("had_errors", False))
        media_present = self._output_contains_media(out_dir)
        # Retention: if this channel has a keep-last limit, prune old
        # sibling recordings from the channel's output root after a
        # successful run.
        if finished_entry is not None and not had_errors and media_present:
            out_root = (
                finished_entry.override_output_dir.strip()
                if getattr(finished_entry, "override_output_dir", "")
                else ""
            ) or self.output_input.text().strip() or str(_default_output_dir())
            try:
                self._apply_retention_for_channel(finished_entry, out_root)
            except Exception as e:
                self._log(f"[RETENTION] error: {e}")
        self._log(f"[AUTO-RECORD] Recording ended for {channel_id}")
        self._refresh_monitor_summary()
        self._refresh_active_recordings_panel()
        if had_errors:
            self._set_status(
                f"Auto-record for {channel_id} ended with errors. Check the log.",
                "warning",
            )
        elif not media_present:
            self._set_status(
                f"Auto-record for {channel_id} finished without saving media.",
                "warning",
            )
        else:
            self._save_metadata(
                out_dir,
                ctx.get("q_name", "Live Capture") or "Live Capture",
                history_url=ctx.get("history_url", ""),
                info=ctx.get("info"),
            )
            # Clear the resume sidecar — live captures never really "finish"
            # via all_done for the single-segment worker, but a successful
            # stop produced media, so we don't need a future resume.
            try:
                clear_resume_state(out_dir)
            except Exception:
                pass
            self._set_status(f"Auto-record finished for {channel_id}.", "success")
        self._start_next_background_job()

    # ── VOD subscription + quality upgrade ──────────────────────────

    def _check_quality_upgrade(self, channel_id, vod):
        """Return True if *vod* should trigger a quality upgrade for
        an existing recording of the same content."""
        entry = None
        for e in self.monitor.entries:
            if e.channel_id == channel_id:
                entry = e
                break
        if not entry or not entry.auto_upgrade:
            return False
        # Find existing recording in history for this channel
        existing = None
        for h in reversed(self._history):
            if (h.channel or "").lower() == channel_id.lower():
                existing = h
                break
        if not existing or not existing.quality:
            return False
        # Compare quality rank
        existing_rank = self._quality_rank(existing.quality)
        # Use the VOD's resolution if available; otherwise skip
        vod_quality = getattr(vod, "quality", "") or ""
        if not vod_quality:
            return False
        new_rank = self._quality_rank(vod_quality)
        if new_rank <= existing_rank:
            return False
        # Check minimum upgrade threshold
        min_q = entry.min_upgrade_quality or ""
        if min_q:
            min_rank = self._quality_rank(min_q)
            if new_rank < min_rank:
                return False
        self._log(
            f"[UPGRADE] {channel_id}: {existing.quality} → {vod_quality} "
            f"— queuing quality upgrade"
        )
        return True

    def _on_new_vods_found(self, channel_id, vods):
        """New VODs from a subscribed channel — queue their source URLs
        so they get downloaded in the background."""
        added = 0
        source_url = ""
        template_name = ""
        for entry in self.monitor.entries:
            if entry.channel_id == channel_id:
                source_url = entry.url
                template_name = entry.ytdlp_template_name or ""
                break
        archive_path = ""
        if source_url:
            from ...paths import source_archive_path
            archive_path = source_archive_path(source_url)
        for v in vods:
            # Quality auto-upgrade check (F25)
            is_upgrade = self._check_quality_upgrade(channel_id, v)
            # Skip if already in history (prevents re-downloading on seed)
            if not is_upgrade and self._find_duplicate("", v.title, platform=v.platform):
                continue
            if self._queue_add(
                v.source,
                title=v.title,
                platform=v.platform,
                vod_source=v.source,
                vod_platform=v.platform,
                vod_title=v.title,
                vod_channel=v.channel,
                download_archive=archive_path,
                break_on_existing=bool(archive_path),
                ytdlp_template_name=template_name,
            ):
                if self._download_queue:
                    self._download_queue[-1]["vod_date"] = v.date
                    if is_upgrade:
                        self._download_queue[-1]["is_upgrade"] = True
                added += 1
                tag = "[UPGRADE]" if is_upgrade else "[SUBSCRIBE]"
                self._log(f"{tag} Queued: {v.title[:60]}")
        if added and hasattr(self, "queue_table"):
            self._refresh_queue_table()
        # Kick off the queue if nothing is downloading
        if getattr(self.download_worker, "isRunning", lambda: False)() is False:
            self._advance_queue()
