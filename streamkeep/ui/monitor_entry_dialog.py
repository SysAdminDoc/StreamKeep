"""Monitor-entry profile editor — per-channel overrides for output dir,
filename template, quality, schedule window, and retention."""

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTimeEdit, QVBoxLayout,
)
from PyQt6.QtCore import QTime

from ..theme import CAT


DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class MonitorEntryDialog(QDialog):
    """Edit overrides for a single MonitorEntry. Returns True on accept,
    False on cancel. Mutates the entry in-place on accept."""

    def __init__(self, parent, entry, *, globals_preview=None):
        super().__init__(parent)
        self.entry = entry
        self.setWindowTitle(f"Channel profile — {entry.channel_id or entry.url}")
        self.setMinimumWidth(560)
        self.setModal(True)
        globals_preview = globals_preview or {}

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        head = QLabel(
            f"<b>Channel:</b> {entry.channel_id or '—'}<br>"
            f"<span style='color:{CAT['subtext0']}'>{entry.url}</span>"
        )
        head.setWordWrap(True)
        root.addWidget(head)

        # Output dir
        root.addWidget(self._section_label(
            "Output folder",
            f"Leave blank to inherit the global default: "
            f"{globals_preview.get('output_dir') or '(not set)'}",
        ))
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        self.out_input = QLineEdit(entry.override_output_dir or "")
        self.out_input.setPlaceholderText("Use global default")
        out_row.addWidget(self.out_input, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.setObjectName("secondary")
        browse_btn.clicked.connect(self._on_browse)
        out_row.addWidget(browse_btn)
        root.addLayout(out_row)

        # Filename template
        root.addWidget(self._section_label(
            "Filename template",
            f"Leave blank to inherit global template: "
            f"{globals_preview.get('file_template') or '(not set)'}",
        ))
        self.template_input = QLineEdit(entry.override_filename_template or "")
        self.template_input.setPlaceholderText("Use global template")
        root.addWidget(self.template_input)

        # Quality preference
        root.addWidget(self._section_label(
            "Quality preference",
            "What to pick when auto-recording. Falls back to highest "
            "available if the exact match is not offered.",
        ))
        self.quality_combo = QComboBox()
        for key, label in [
            ("", "Use global default"),
            ("highest", "Highest available"),
            ("source", "Source / original"),
            ("1080p", "1080p"),
            ("720p", "720p"),
            ("480p", "480p"),
            ("360p", "360p"),
        ]:
            self.quality_combo.addItem(label, userData=key)
        current_idx = max(0, self.quality_combo.findData(entry.override_quality_pref or ""))
        self.quality_combo.setCurrentIndex(current_idx)
        root.addWidget(self.quality_combo)

        # Schedule window
        root.addWidget(self._section_label(
            "Schedule window",
            "Only poll and auto-record during this window. Leave unset "
            "for 24/7 monitoring.",
        ))
        sched_row = QHBoxLayout()
        sched_row.setSpacing(10)
        self.schedule_enabled = QCheckBox("Restrict to window")
        has_window = bool(entry.schedule_start_hhmm and entry.schedule_end_hhmm)
        self.schedule_enabled.setChecked(has_window)
        self.schedule_enabled.toggled.connect(self._on_schedule_toggled)
        sched_row.addWidget(self.schedule_enabled)
        self.start_time = QTimeEdit(self._parse_hhmm(entry.schedule_start_hhmm, 20, 0))
        self.start_time.setDisplayFormat("HH:mm")
        sched_row.addWidget(QLabel("from"))
        sched_row.addWidget(self.start_time)
        self.end_time = QTimeEdit(self._parse_hhmm(entry.schedule_end_hhmm, 23, 0))
        self.end_time.setDisplayFormat("HH:mm")
        sched_row.addWidget(QLabel("to"))
        sched_row.addWidget(self.end_time)
        sched_row.addStretch(1)
        root.addLayout(sched_row)

        days_row = QHBoxLayout()
        days_row.setSpacing(4)
        days_row.addWidget(QLabel("Days:"))
        self.day_boxes = []
        mask = int(entry.schedule_days_mask or 0)
        # A zero mask means "all days" (the default / backwards-compatible
        # interpretation). Preselect everything so the UI matches.
        all_days = mask == 0
        for i, name in enumerate(DAY_NAMES):
            cb = QCheckBox(name)
            cb.setChecked(all_days or bool(mask & (1 << i)))
            days_row.addWidget(cb)
            self.day_boxes.append(cb)
        days_row.addStretch(1)
        root.addLayout(days_row)
        self._on_schedule_toggled(self.schedule_enabled.isChecked())

        # Retention
        root.addWidget(self._section_label(
            "Retention",
            "After a successful auto-record, trim older recordings from "
            "the channel's output folder down to this count. Set to 0 to "
            "keep everything.",
        ))
        # Keyword filter (F3)
        root.addWidget(self._section_label(
            "Title Keywords",
            "Comma-separated keywords. Auto-record only triggers when the "
            "stream title contains at least one keyword. Leave blank to "
            "record all streams.",
        ))
        self.keywords_input = QLineEdit(entry.filter_keywords or "")
        self.keywords_input.setPlaceholderText("e.g. speedrun, tournament, collab")
        root.addWidget(self.keywords_input)

        # Post-processing preset (F7)
        root.addWidget(self._section_label(
            "Post-Processing Preset",
            "Apply a named preset when this channel's recordings are "
            "processed. Leave on 'Use global default' to inherit "
            "Settings → Post-Processing.",
        ))
        self.pp_preset_combo = QComboBox()
        self.pp_preset_combo.addItem("Use global default", userData="")
        try:
            from .tabs.settings import BUILTIN_PRESETS, _get_user_presets
            for name in BUILTIN_PRESETS:
                self.pp_preset_combo.addItem(f"★ {name}", userData=name)
            if parent:
                for name in _get_user_presets(parent):
                    self.pp_preset_combo.addItem(name, userData=name)
        except Exception:
            pass
        current_preset = entry.override_pp_preset or ""
        idx = max(0, self.pp_preset_combo.findData(current_preset))
        self.pp_preset_combo.setCurrentIndex(idx)
        root.addWidget(self.pp_preset_combo)

        # Quality auto-upgrade (F25)
        root.addWidget(self._section_label(
            "Quality Auto-Upgrade",
            "When a VOD appears at higher quality than the existing recording, "
            "re-download automatically. Requires VOD subscription to be active.",
        ))
        self.auto_upgrade_check = QCheckBox("Auto-upgrade when better quality VOD appears")
        self.auto_upgrade_check.setChecked(bool(entry.auto_upgrade))
        root.addWidget(self.auto_upgrade_check)
        upgrade_row = QHBoxLayout()
        upgrade_row.setSpacing(8)
        upgrade_row.addWidget(QLabel("Minimum quality to trigger:"))
        self.min_upgrade_combo = QComboBox()
        for key, label in [
            ("", "Any improvement"),
            ("480p", "480p or better"),
            ("720p", "720p or better"),
            ("1080p", "1080p or better"),
            ("source", "Source / original only"),
        ]:
            self.min_upgrade_combo.addItem(label, userData=key)
        cur_up_idx = max(0, self.min_upgrade_combo.findData(entry.min_upgrade_quality or ""))
        self.min_upgrade_combo.setCurrentIndex(cur_up_idx)
        upgrade_row.addWidget(self.min_upgrade_combo)
        upgrade_row.addStretch(1)
        root.addLayout(upgrade_row)

        ret_row = QHBoxLayout()
        ret_row.setSpacing(8)
        ret_row.addWidget(QLabel("Keep last"))
        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(0, 999)
        self.retention_spin.setValue(int(entry.retention_keep_last or 0))
        self.retention_spin.setSpecialValueText("Keep everything")
        ret_row.addWidget(self.retention_spin)
        ret_row.addWidget(QLabel("recording(s)"))
        ret_row.addStretch(1)
        root.addLayout(ret_row)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save Profile")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    def _section_label(self, title, helper):
        w = QLabel(f"<b>{title}</b><br><span style='color:{CAT['subtext0']}'>{helper}</span>")
        w.setWordWrap(True)
        return w

    def _parse_hhmm(self, text, default_h, default_m):
        if not text or ":" not in text:
            return QTime(default_h, default_m)
        try:
            h, m = text.split(":", 1)
            return QTime(int(h), int(m))
        except (ValueError, TypeError):
            return QTime(default_h, default_m)

    def _on_browse(self):
        path = QFileDialog.getExistingDirectory(
            self, "Channel output folder", self.out_input.text().strip() or ""
        )
        if path:
            self.out_input.setText(path)

    def _on_schedule_toggled(self, checked):
        for w in [self.start_time, self.end_time] + self.day_boxes:
            w.setEnabled(bool(checked))

    def _on_save(self):
        entry = self.entry
        entry.override_output_dir = self.out_input.text().strip()
        entry.override_filename_template = self.template_input.text().strip()
        entry.override_quality_pref = self.quality_combo.currentData() or ""
        if self.schedule_enabled.isChecked():
            s = self.start_time.time()
            e = self.end_time.time()
            entry.schedule_start_hhmm = f"{s.hour():02d}:{s.minute():02d}"
            entry.schedule_end_hhmm = f"{e.hour():02d}:{e.minute():02d}"
        else:
            entry.schedule_start_hhmm = ""
            entry.schedule_end_hhmm = ""
        # Mask: 0 = all days (back-compat); otherwise bit-per-day.
        mask = 0
        for i, cb in enumerate(self.day_boxes):
            if cb.isChecked():
                mask |= 1 << i
        # If user ticked every day, store 0 (== "always") so future UI
        # reads cleanly match the default-all preselect behavior.
        if mask == 0b1111111:
            mask = 0
        entry.schedule_days_mask = mask
        entry.retention_keep_last = int(self.retention_spin.value())
        entry.filter_keywords = self.keywords_input.text().strip()
        entry.override_pp_preset = self.pp_preset_combo.currentData() or ""
        entry.auto_upgrade = self.auto_upgrade_check.isChecked()
        entry.min_upgrade_quality = self.min_upgrade_combo.currentData() or ""
        self.accept()
