"""Batch Rename Studio — premium multi-recording rename flow."""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..theme import CAT
from ..i18n import TranslatableDialog
from ..metadata import load_metadata_sidecar
from .widgets import (
    make_dialog_hero,
    make_dialog_section,
    make_empty_state,
    make_status_banner,
    style_table,
    update_status_banner,
)


def _read_metadata(dir_path):
    """Read metadata.json from a recording directory."""
    return load_metadata_sidecar(os.path.join(dir_path, "metadata.json"))


def _safe_name(s, max_len=120):
    if not s:
        return ""
    bad = '<>:"/\\|?*'
    out = "".join(c if c not in bad else "_" for c in s.strip())[:max_len]
    out = out.rstrip(". ")
    # Prevent empty results — fall back to underscores
    return out if out else "_"


def _resolve_template(template, meta, seq):
    """Resolve template tokens against metadata dict + sequence number."""
    result = template
    result = result.replace(
        "{channel}",
        _safe_name(meta.get("vod_channel", "") or meta.get("platform", "")),
    )
    result = result.replace("{platform}", _safe_name(meta.get("platform", "")))
    result = result.replace("{title}", _safe_name(meta.get("title", "")))
    result = result.replace("{quality}", _safe_name(meta.get("quality", "")))
    result = result.replace("{date}", (meta.get("downloaded_at", "") or "")[:10])

    dur_secs = int(meta.get("total_secs", 0) or 0)
    hh = dur_secs // 3600
    mm = (dur_secs % 3600) // 60
    result = result.replace("{duration}", f"{hh}h{mm:02d}m")

    if "{seq:" in result:
        import re

        m = re.search(r"\{seq:(\d+)\}", result)
        if m:
            width = len(m.group(1))
            result = result.replace(m.group(0), str(seq).zfill(width))
    elif "{seq}" in result:
        result = result.replace("{seq}", str(seq))

    return result.strip() or f"recording_{seq}"


@dataclass
class RenameUndoResult:
    """Outcome of replaying the newest durable rename batch."""

    restored: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    journal_error: str = ""


def _rename_undo_path(log_path=None):
    if log_path is not None:
        return Path(log_path)
    # Read the paths module at call time so test/startup config rebinding is
    # honoured rather than capturing the operator profile during import.
    from .. import paths

    return paths.CONFIG_DIR / "rename_undo.json"


def _load_rename_batches(log_path=None):
    path = _rename_undo_path(log_path)
    if not path.exists():
        return []
    batches = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(batches, list):
        raise ValueError("rename undo journal must contain a list")
    for batch in batches:
        if not isinstance(batch, dict) or not isinstance(batch.get("ops"), list):
            raise ValueError("rename undo journal contains an invalid batch")
        for operation in batch["ops"]:
            if not isinstance(operation, dict):
                raise ValueError("rename undo journal contains an invalid operation")
            if not str(operation.get("old", "")) or not str(operation.get("new", "")):
                raise ValueError("rename undo operation is missing a path")
    return batches


def _write_rename_batches(batches, log_path=None):
    """Replace the journal atomically, or remove it when fully consumed."""
    path = _rename_undo_path(log_path)
    if not batches:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(batches[-20:], handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def record_rename_undo(operations, log_path=None):
    """Append one completed rename batch to the durable undo journal."""
    batches = _load_rename_batches(log_path)
    batches.append({"ts": datetime.now().isoformat(), "ops": list(operations)})
    _write_rename_batches(batches, log_path)


def rename_undo_available(log_path=None):
    """Return whether a valid, non-empty rename batch can be restored."""
    try:
        batches = _load_rename_batches(log_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return bool(batches and batches[-1].get("ops"))


def undo_last_rename(log_path=None, *, relocate_history=None):
    """Restore the newest rename batch and retain only operations that failed."""
    result = RenameUndoResult()
    try:
        batches = _load_rename_batches(log_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result.journal_error = f"Could not read rename undo history: {error}"
        return result
    if not batches:
        return result

    batch = batches[-1]
    failed_operations = []
    for operation in reversed(batch["ops"]):
        old_path = str(operation["old"])
        new_path = str(operation["new"])
        failure = dict(operation)
        if os.path.exists(old_path):
            failure["error"] = "the original path is occupied"
            result.failed.append(failure)
            failed_operations.append(operation)
            continue
        if not os.path.exists(new_path):
            failure["error"] = "the renamed path is missing"
            result.failed.append(failure)
            failed_operations.append(operation)
            continue
        try:
            os.rename(new_path, old_path)
        except OSError as error:
            failure["error"] = str(error)
            result.failed.append(failure)
            failed_operations.append(operation)
            continue

        history_relocated = bool(
            operation.get("history_relocated", operation.get("db_id"))
        )
        if history_relocated:
            try:
                if relocate_history is None:
                    from streamkeep import db as _db

                    _db.relocate_history_recording(
                        int(operation["db_id"]), new_path, old_path,
                    )
                else:
                    relocate_history(
                        int(operation["db_id"]), new_path, old_path,
                    )
            except Exception as error:
                # Put the directory back under its current database path. The
                # failed operation remains journaled and visible to the user.
                try:
                    os.rename(old_path, new_path)
                    failure["error"] = f"library update failed: {error}"
                except OSError as rollback_error:
                    failure["error"] = (
                        f"library update failed: {error}; filesystem rollback "
                        f"also failed: {rollback_error}"
                    )
                result.failed.append(failure)
                failed_operations.append(operation)
                continue
        result.restored.append(dict(operation))

    if failed_operations:
        batch["ops"] = list(reversed(failed_operations))
    else:
        batches.pop()
    try:
        _write_rename_batches(batches, log_path)
    except OSError as error:
        result.journal_error = f"Could not update rename undo history: {error}"
    return result


class RenameDialog(TranslatableDialog):
    """Batch rename dialog for History entries."""

    _PRESETS = [
        ("Balanced", "{channel} - {date} - {title}"),
        ("Archive", "{date} - {channel} - {title} - {quality}"),
        ("Minimal", "{title}"),
        ("Series", "{channel} - {seq:001} - {title}"),
    ]

    def __init__(self, parent, entries):
        super().__init__(parent)
        self.setWindowTitle("Batch Rename Studio")
        self.setMinimumSize(900, 700)
        self.setModal(True)
        self._entries = entries
        self._parent_win = parent

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        hero, _, _, self._hero_badge = make_dialog_hero(
            "Rename recordings with a live preview",
            "Build a consistent naming pattern before you commit changes. Preview updates instantly, duplicate names are flagged, and an undo log is written after the batch runs.",
            eyebrow="BATCH TOOLS",
            badge_text=f"{len(entries)} selected",
        )
        root.addWidget(hero)

        template_card, template_content = make_dialog_section(
            "Template",
            "Use tokens to build a naming pattern that stays readable across platforms, dates, and long recording libraries.",
        )
        token_hint = QLabel(
            "Tokens: {channel}  {date}  {title}  {quality}  {duration}  {platform}  {seq:001}"
        )
        token_hint.setObjectName("fieldHint")
        token_hint.setWordWrap(True)
        template_content.addWidget(token_hint)

        self.template_input = QLineEdit("{channel} - {date} - {title}")
        self.template_input.setClearButtonEnabled(True)
        self.template_input.textChanged.connect(self._refresh_preview)
        template_content.addWidget(self.template_input)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_label = QLabel("Quick presets")
        preset_label.setObjectName("fieldLabel")
        preset_row.addWidget(preset_label)
        for label, template in self._PRESETS:
            btn = QPushButton(label)
            btn.setObjectName("ghost")
            btn.clicked.connect(lambda _checked=False, tpl=template: self._apply_template(tpl))
            preset_row.addWidget(btn)
        preset_row.addStretch(1)
        template_content.addLayout(preset_row)

        self.status_banner, self.status_title, self.status_body = make_status_banner()
        self.status_banner.setMinimumHeight(48)
        template_content.addWidget(self.status_banner)
        root.addWidget(template_card)

        preview_card, preview_content = make_dialog_section(
            "Preview",
            "Review the current folder names against the generated results before renaming anything on disk.",
        )
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Current name", "New name"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 340)
        style_table(
            self.table,
            accessible_name="Rename preview",
            accessible_description="Current and proposed recording names",
        )
        preview_content.addWidget(self.table)

        self.empty_card, self.empty_title, self.empty_body = make_empty_state(
            "Nothing to rename",
            "Select one or more finished recordings from History, then reopen Batch Rename Studio.",
        )
        preview_content.addWidget(self.empty_card)
        root.addWidget(preview_card, 1)

        summary_row = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setObjectName("statusLabel")
        summary_row.addWidget(self.count_label)
        summary_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self.reject)
        summary_row.addWidget(cancel_btn)
        self.rename_btn = QPushButton("Rename selected")
        self.rename_btn.setObjectName("primary")
        self.rename_btn.clicked.connect(self._on_rename)
        summary_row.addWidget(self.rename_btn)
        root.addLayout(summary_row)

        self._metas = []
        for entry in entries:
            self._metas.append(_read_metadata(entry.path) if entry.path else {})

        self._refresh_preview()

    def _apply_template(self, template):
        self.template_input.setText(template)

    def _refresh_preview(self):
        tpl = self.template_input.text().strip()
        self.table.setRowCount(len(self._entries))
        names = []

        for i, (entry, meta) in enumerate(zip(self._entries, self._metas)):
            old_name = os.path.basename((entry.path or "").rstrip("\\/")) or "(unknown)"
            new_name = _resolve_template(tpl, meta, i + 1)
            names.append(new_name)
            self.table.setItem(i, 0, QTableWidgetItem(old_name))
            new_item = QTableWidgetItem(new_name)
            self.table.setItem(i, 1, new_item)

        seen = {}
        conflicts = 0
        for i, name in enumerate(names):
            lowered = name.lower()
            if lowered in seen:
                conflicts += 1
                for col in range(2):
                    self.table.item(i, col).setForeground(QColor(CAT["red"]))
                    self.table.item(seen[lowered], col).setForeground(QColor(CAT["red"]))
            else:
                seen[lowered] = i

        has_entries = bool(self._entries)
        self.table.setVisible(has_entries)
        self.empty_card.setVisible(not has_entries)
        self.rename_btn.setEnabled(has_entries and conflicts == 0)

        if not has_entries:
            self.count_label.setText("No recordings selected")
            update_status_banner(
                self.status_banner,
                self.status_title,
                self.status_body,
                title="Nothing selected",
                body="",
                tone="warning",
            )
            return

        if conflicts:
            self.count_label.setText(f"{conflicts} naming conflict(s) detected")
            update_status_banner(
                self.status_banner,
                self.status_title,
                self.status_body,
                title="Duplicate names detected",
                body="",
                tone="error",
            )
        else:
            self.count_label.setText(f"{len(self._entries)} recording(s) ready to rename")
            update_status_banner(
                self.status_banner,
                self.status_title,
                self.status_body,
                title="Preview looks good",
                body="",
                tone="success",
            )

    def _on_rename(self):
        tpl = self.template_input.text().strip()
        undo_log = []
        renamed = 0
        skipped = 0
        recovery_warnings = 0

        for i, (entry, meta) in enumerate(zip(self._entries, self._metas)):
            if not entry.path or not os.path.isdir(entry.path):
                skipped += 1
                continue
            parent = os.path.dirname(entry.path.rstrip("\\/"))
            old_name = os.path.basename(entry.path.rstrip("\\/"))
            new_name = _resolve_template(tpl, meta, i + 1)
            if new_name == old_name:
                skipped += 1
                continue
            new_path = os.path.join(parent, new_name)
            if os.path.exists(new_path):
                skipped += 1
                continue
            try:
                os.rename(entry.path, new_path)
                old_path = entry.path
                entry.path = new_path
                history_relocated = False
                if getattr(entry, "db_id", 0):
                    from streamkeep import db as _db

                    try:
                        _db.relocate_history_recording(
                            entry.db_id, old_path, new_path,
                        )
                        history_relocated = True
                    except Exception:
                        # A failed database move must not leave the filesystem
                        # silently ahead of History. Roll it back when possible;
                        # otherwise journal the filesystem-only move for undo.
                        try:
                            os.rename(new_path, old_path)
                            entry.path = old_path
                            skipped += 1
                            continue
                        except OSError:
                            recovery_warnings += 1
                undo_log.append({
                    "old": old_path,
                    "new": new_path,
                    "db_id": int(getattr(entry, "db_id", 0) or 0),
                    "history_relocated": history_relocated,
                })
                renamed += 1
            except OSError:
                skipped += 1
                continue

        journal_error = ""
        if undo_log:
            try:
                record_rename_undo(undo_log)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                journal_error = str(error)

        if self._parent_win:
            self._parent_win._refresh_history_table()
            self._parent_win._persist_config()
            if renamed and not journal_error:
                self._parent_win._set_status(
                    f"Renamed {renamed} recording(s).", "success",
                )
                self._parent_win._toast(
                    f"Renamed {renamed} recording(s). Use Undo last rename.",
                    "success",
                )
            elif renamed:
                self._parent_win._report_failure(
                    f"Renamed {renamed} recording(s), but undo history could "
                    f"not be saved: {journal_error}",
                    level="warning",
                )
            if recovery_warnings:
                self._parent_win._report_failure(
                    f"{recovery_warnings} recording(s) were renamed on disk "
                    "but could not be updated in History; use Undo last rename.",
                    level="warning",
                )

        update_status_banner(
            self.status_banner,
            self.status_title,
            self.status_body,
            title="Rename pass complete",
            body=(
                f"Renamed {renamed} recording(s)"
                + (f" and skipped {skipped}." if skipped else ".")
                + (
                    " Use Undo last rename to restore them."
                    if undo_log and not journal_error
                    else " Undo history could not be saved."
                    if journal_error
                    else ""
                )
            ),
            tone="success" if renamed and not journal_error else "warning",
        )
        self.count_label.setText(
            f"Renamed {renamed} • Skipped {skipped}"
        )
        self.accept()
