"""Batch Rename Studio — rename multiple recordings with a template
and live preview.

Template tokens: {channel}, {date}, {title}, {quality}, {duration},
{platform}, {seq:001}.  Reads metadata.json for token resolution.
Writes an undo log so the batch can be reverted.
"""

import json
import os
from datetime import datetime

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)


def _read_metadata(dir_path):
    """Read metadata.json from a recording directory."""
    p = os.path.join(dir_path, "metadata.json")
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _safe_name(s, max_len=120):
    if not s:
        return ""
    bad = '<>:"/\\|?*'
    return "".join(c if c not in bad else "_" for c in s.strip())[:max_len]


def _resolve_template(template, meta, seq):
    """Resolve template tokens against metadata dict + sequence number."""
    result = template
    result = result.replace("{channel}", _safe_name(meta.get("vod_channel", "") or meta.get("platform", "")))
    result = result.replace("{platform}", _safe_name(meta.get("platform", "")))
    result = result.replace("{title}", _safe_name(meta.get("title", "")))
    result = result.replace("{quality}", _safe_name(meta.get("quality", "")))
    result = result.replace("{date}", (meta.get("downloaded_at", "") or "")[:10])

    dur_secs = int(meta.get("total_secs", 0) or 0)
    hh = dur_secs // 3600
    mm = (dur_secs % 3600) // 60
    result = result.replace("{duration}", f"{hh}h{mm:02d}m")

    # Sequence token: {seq:001} -> zero-padded
    if "{seq:" in result:
        import re
        m = re.search(r"\{seq:(\d+)\}", result)
        if m:
            width = len(m.group(1))
            result = result.replace(m.group(0), str(seq).zfill(width))
    elif "{seq}" in result:
        result = result.replace("{seq}", str(seq))

    return result.strip() or f"recording_{seq}"


class RenameDialog(QDialog):
    """Batch rename dialog for History entries."""

    def __init__(self, parent, entries):
        """*entries* is a list of HistoryEntry objects."""
        super().__init__(parent)
        self.setWindowTitle("Batch Rename Studio")
        self.setMinimumSize(800, 500)
        self.setModal(True)
        self._entries = entries
        self._parent_win = parent

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        root.addWidget(QLabel(
            "<b>Rename Template</b> — tokens: {channel}, {date}, {title}, "
            "{quality}, {duration}, {platform}, {seq:001}"
        ))
        tpl_row = QHBoxLayout()
        tpl_row.setSpacing(8)
        self.template_input = QLineEdit("{channel} - {date} - {title}")
        self.template_input.textChanged.connect(self._refresh_preview)
        tpl_row.addWidget(self.template_input, 1)
        root.addLayout(tpl_row)

        # Preview table
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Current Name", "New Name"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 350)
        root.addWidget(self.table, 1)

        self.status_label = QLabel("")
        root.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self.rename_btn = QPushButton("Rename All")
        self.rename_btn.setObjectName("primary")
        self.rename_btn.clicked.connect(self._on_rename)
        btn_row.addWidget(self.rename_btn)
        root.addLayout(btn_row)

        self._metas = []
        for e in entries:
            self._metas.append(_read_metadata(e.path) if e.path else {})

        self._refresh_preview()

    def _refresh_preview(self):
        tpl = self.template_input.text().strip()
        self.table.setRowCount(len(self._entries))
        names = []
        for i, (e, meta) in enumerate(zip(self._entries, self._metas)):
            old_name = os.path.basename((e.path or "").rstrip("\\/")) or "(unknown)"
            new_name = _resolve_template(tpl, meta, i + 1)
            names.append(new_name)
            self.table.setItem(i, 0, QTableWidgetItem(old_name))
            item = QTableWidgetItem(new_name)
            self.table.setItem(i, 1, item)

        # Conflict detection
        seen = {}
        conflicts = 0
        for i, n in enumerate(names):
            nl = n.lower()
            if nl in seen:
                conflicts += 1
                for col in range(2):
                    self.table.item(i, col).setForeground(QColor("#f38ba8"))
                    self.table.item(seen[nl], col).setForeground(QColor("#f38ba8"))
            else:
                seen[nl] = i

        if conflicts:
            self.status_label.setText(f"{conflicts} name conflict(s) detected — duplicates shown in red.")
            self.rename_btn.setEnabled(False)
        else:
            self.status_label.setText(f"{len(self._entries)} recording(s) will be renamed.")
            self.rename_btn.setEnabled(True)

    def _on_rename(self):
        tpl = self.template_input.text().strip()
        undo_log = []
        renamed = 0
        for i, (e, meta) in enumerate(zip(self._entries, self._metas)):
            if not e.path or not os.path.isdir(e.path):
                continue
            parent = os.path.dirname(e.path.rstrip("\\/"))
            old_name = os.path.basename(e.path.rstrip("\\/"))
            new_name = _resolve_template(tpl, meta, i + 1)
            if new_name == old_name:
                continue
            new_path = os.path.join(parent, new_name)
            if os.path.exists(new_path):
                continue
            try:
                os.rename(e.path, new_path)
                undo_log.append({"old": e.path, "new": new_path})
                e.path = new_path
                renamed += 1
            except OSError:
                continue

        # Write undo log
        if undo_log:
            from ..paths import CONFIG_DIR
            log_path = CONFIG_DIR / "rename_undo.json"
            try:
                existing = []
                if log_path.exists():
                    with open(log_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                existing.append({
                    "ts": datetime.now().isoformat(),
                    "ops": undo_log,
                })
                with open(log_path, "w", encoding="utf-8") as f:
                    json.dump(existing[-20:], f, indent=2)
            except Exception:
                pass

        self.status_label.setText(f"Renamed {renamed} recording(s). Undo log saved.")
        if self._parent_win:
            self._parent_win._refresh_history_table()
            self._parent_win._persist_config()
        self.accept()
