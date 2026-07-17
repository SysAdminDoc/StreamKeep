"""Template, hook, converter, and config-transfer Settings handlers."""

import json
import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog

from ...config import save_config as _save_config
from ...postprocess import AUDIO_EXTS, VIDEO_EXTS, ConvertWorker
from ...utils import default_output_dir as _default_output_dir
from ..widgets import ask_premium_confirmation


class SettingsToolsMixin:
    """Advanced templates/hooks plus manual conversion and config transfer."""

    def _refresh_ytdlp_template_editor(self, selected=""):
        from ...download_options import normalize_ytdlp_arg_templates
        try:
            templates = normalize_ytdlp_arg_templates(
                self._config.get("ytdlp_arg_templates", {})
            )
        except ValueError:
            templates = {}
        self._config["ytdlp_arg_templates"] = templates
        combo = self.ytdlp_template_editor_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("New template", userData="")
        for name in sorted(templates, key=str.casefold):
            combo.addItem(name, userData=name)
        index = combo.findData(selected)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)
        self._on_ytdlp_template_selected()
        if hasattr(self, "adv_ytdlp_template_combo"):
            from .download import _populate_adv_ytdlp_templates
            _populate_adv_ytdlp_templates(self)

    def _on_ytdlp_template_selected(self):
        name = self.ytdlp_template_editor_combo.currentData() or ""
        templates = self._config.get("ytdlp_arg_templates", {})
        self.ytdlp_template_name_input.setText(name)
        self.ytdlp_template_args_edit.setPlainText(
            "\n".join(templates.get(name, [])) if name else ""
        )
        self.ytdlp_template_delete_btn.setEnabled(bool(name))

    def _on_ytdlp_template_save(self):
        from ...download_options import (
            normalize_ytdlp_arg_templates, parse_ytdlp_template_text,
        )
        name = self.ytdlp_template_name_input.text().strip()
        try:
            args = list(parse_ytdlp_template_text(
                self.ytdlp_template_args_edit.toPlainText()
            ))
            templates = dict(self._config.get("ytdlp_arg_templates", {}))
            templates[name] = args
            templates = normalize_ytdlp_arg_templates(templates)
        except ValueError as error:
            self._set_status(str(error), "warning")
            return
        self._config["ytdlp_arg_templates"] = templates
        self._refresh_ytdlp_template_editor(name)
        self._persist_config()
        self._set_status(f'Saved yt-dlp argument template "{name}".', "success")

    def _on_ytdlp_template_delete(self):
        name = self.ytdlp_template_editor_combo.currentData() or ""
        if not name:
            return
        templates = dict(self._config.get("ytdlp_arg_templates", {}))
        templates.pop(name, None)
        self._config["ytdlp_arg_templates"] = templates
        monitor = getattr(self, "monitor", None)
        for entry in getattr(monitor, "entries", []):
            if getattr(entry, "ytdlp_template_name", "") == name:
                entry.ytdlp_template_name = ""
        self._refresh_ytdlp_template_editor()
        self._persist_config()
        self._set_status(f'Deleted yt-dlp argument template "{name}".', "success")

    # ── Event hooks (structured, no-shell actions) ───────────────────

    def _refresh_hook_editor(self, selected=""):
        from ...hooks import HOOK_EVENTS
        combo = getattr(self, "hooks_event_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        for event in HOOK_EVENTS:
            combo.addItem(event, userData=event)
        index = combo.findData(selected)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)
        self._on_hook_event_selected()

    def _on_hook_event_selected(self):
        from ...hooks import normalize_hook
        combo = getattr(self, "hooks_event_combo", None)
        if combo is None:
            return
        event = combo.currentData() or ""
        hooks = self._config.get("hooks", {})
        kind, data = normalize_hook(hooks.get(event))
        legacy = kind == "legacy"
        self.hook_executable_input.setEnabled(not legacy)
        self.hook_args_edit.setEnabled(not legacy)
        self.hook_enabled_check.setEnabled(not legacy)
        self.hook_save_btn.setEnabled(True)
        if kind == "structured":
            self.hook_executable_input.setText(data["executable"])
            self.hook_args_edit.setPlainText("\n".join(data["args"]))
            self.hook_enabled_check.setChecked(data["enabled"])
            self.hook_status_label.setText(
                "Enabled structured action."
                if data["enabled"] else "Structured action (disabled)."
            )
        elif legacy:
            # A legacy shell string is retained but never executed. Show a
            # redacted preview and let the user replace it with a structured
            # action; saving overwrites the legacy value.
            from ...diagnostics import redact_text
            preview = redact_text(str(data))[:120]
            self.hook_executable_input.clear()
            self.hook_args_edit.clear()
            self.hook_enabled_check.setChecked(False)
            self.hook_status_label.setText(
                "Legacy shell command is disabled and will not run: "
                f"“{preview}”. Enter an executable and arguments, "
                "then Save to migrate it."
            )
        else:
            self.hook_executable_input.clear()
            self.hook_args_edit.clear()
            self.hook_enabled_check.setChecked(True)
            self.hook_status_label.setText(
                "No action configured for this event."
            )

    def _on_hook_save(self):
        from ...hooks import (
            normalize_hook, parse_hook_args_text, structured_hook,
        )
        combo = getattr(self, "hooks_event_combo", None)
        if combo is None:
            return
        event = combo.currentData() or ""
        executable = self.hook_executable_input.text().strip()
        args = parse_hook_args_text(self.hook_args_edit.toPlainText())
        hooks = dict(self._config.get("hooks", {}))
        if not executable:
            # Clearing the executable removes any action (including a disabled
            # legacy string) for this event.
            hooks.pop(event, None)
            self._config["hooks"] = hooks
            self._refresh_hook_editor(event)
            self._persist_config()
            self._set_status(f"Cleared the {event} hook.", "success")
            return
        candidate = structured_hook(
            executable, args, self.hook_enabled_check.isChecked()
        )
        kind, data = normalize_hook(candidate)
        if kind != "structured":
            self._set_status(
                f"Hook is invalid: {data}", "warning"
            )
            return
        hooks[event] = data
        self._config["hooks"] = hooks
        self._refresh_hook_editor(event)
        self._persist_config()
        state = "enabled" if data["enabled"] else "disabled"
        self._set_status(f"Saved {state} {event} action.", "success")

    # ── Manual converter ─────────────────────────────────────────────

    def _on_convert_files_clicked(self):
        """Open a multi-select file picker and kick off the converter."""
        if getattr(self, "_convert_worker", None) is not None and self._convert_worker.isRunning():
            self._set_status("A conversion is already running.", "warning")
            return
        # Apply current settings first so the worker picks them up
        self._on_save_settings()
        exts = sorted(VIDEO_EXTS | AUDIO_EXTS)
        filter_str = "Media files (" + " ".join(f"*{e}" for e in exts) + ");;All files (*)"
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select files to convert", str(_default_output_dir()), filter_str
        )
        if not paths:
            return
        self._start_convert_worker(list(paths))

    def _on_convert_folder_clicked(self):
        """Recursively collect media files from a chosen folder and convert."""
        if getattr(self, "_convert_worker", None) is not None and self._convert_worker.isRunning():
            self._set_status("A conversion is already running.", "warning")
            return
        self._on_save_settings()
        folder = QFileDialog.getExistingDirectory(
            self, "Select folder to convert", str(_default_output_dir())
        )
        if not folder:
            return
        files = []
        try:
            for root, _dirs, fnames in os.walk(folder):
                for f in fnames:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in VIDEO_EXTS or ext in AUDIO_EXTS:
                        # Skip files we produced ourselves
                        low = f.lower()
                        if ".converted." in low:
                            continue
                        files.append(os.path.join(root, f))
        except OSError as e:
            self._set_status(f"Folder scan failed: {e}", "error")
            return
        if not files:
            self._set_status("No media files found in that folder.", "warning")
            return
        self._log(f"[CONVERT] Found {len(files)} file(s) in {folder}")
        self._start_convert_worker(files)

    def _start_convert_worker(self, files):
        """Launch a ConvertWorker for the given list and wire up signals."""
        do_video = self.pp_convert_video_check.isChecked()
        do_audio = self.pp_convert_audio_check.isChecked()
        if not (do_video or do_audio):
            self._set_status(
                "Enable 'Convert video' or 'Convert audio' in Post-Processing first.",
                "warning"
            )
            return
        self._convert_worker = ConvertWorker(files, do_video, do_audio)
        self._convert_worker.progress.connect(self._on_convert_progress)
        self._convert_worker.log.connect(self._log)
        self._convert_worker.file_done.connect(self._on_convert_file_done)
        self._convert_worker.all_done.connect(self._on_convert_all_done)
        self.convert_files_btn.setEnabled(False)
        self.convert_folder_btn.setEnabled(False)
        self.convert_cancel_btn.setVisible(True)
        self._log(f"[CONVERT] Starting batch conversion ({len(files)} file(s))")
        self._set_status(f"Converting 0/{len(files)}...", "working")
        self._convert_worker.start()

    def _on_convert_progress(self, idx, total, name):
        if total:
            status = f"Converting {idx + 1}/{total}: {name}" if name else f"Converted {total}/{total}"
            self._set_status(status, "working" if idx < total else "success")

    def _on_convert_file_done(self, path, ok):
        marker = "[OK]" if ok else "[FAIL]"
        self._log(f"[CONVERT] {marker} {os.path.basename(path)}")

    def _on_convert_all_done(self, successes, failures):
        self.convert_files_btn.setEnabled(True)
        self.convert_folder_btn.setEnabled(True)
        self.convert_cancel_btn.setVisible(False)
        total = successes + failures
        if failures == 0:
            self._set_status(f"Conversion complete: {successes}/{total} succeeded.", "success")
        else:
            self._set_status(
                f"Conversion finished: {successes} ok, {failures} failed. See log.",
                "warning"
            )
        self._notify("StreamKeep", f"Converted {successes}/{total} file(s)")

    def _on_convert_cancel(self):
        w = getattr(self, "_convert_worker", None)
        if w is not None and w.isRunning():
            w.cancel()
            self._log("[CONVERT] Cancel requested — finishing current file first")
            self._set_status("Cancelling conversion...", "warning")

    # ── Config import / export ───────────────────────────────────────

    def _on_export_config(self):
        """Write current config to a user-chosen JSON file."""
        self._persist_config()  # sync latest UI state first
        default_name = f"StreamKeep-config-{datetime.now().strftime('%Y%m%d')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export StreamKeep Config",
            str(Path.home() / default_name),
            "JSON files (*.json)"
        )
        if not path:
            return
        try:
            from ...config import export_config
            with open(path, "w", encoding="utf-8") as f:
                json.dump(export_config(self._config), f, indent=2)
            self._log(f"[CONFIG] Exported to {path}")
            self._set_status(f"Config exported to {path}", "success")
        except Exception as e:
            self._log(f"[CONFIG] Export failed: {e}")
            self._set_status(f"Export failed: {e}", "error")

    def _on_import_config(self):
        """Validate, review, quarantine, then apply a versioned config export."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import StreamKeep Config",
            str(Path.home()),
            "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            from ...config import (
                finalize_config_import,
                get_import_capability_info,
                prepare_config_import,
            )
            preview = prepare_config_import(Path(path).read_bytes(), self._config)
        except Exception as e:
            self._log(f"[CONFIG] Import failed: {e}")
            self._set_status(f"Import failed: {e}", "error")
            return

        shown_diff = list(preview.diff_lines[:14])
        if len(preview.diff_lines) > len(shown_diff):
            shown_diff.append(
                f"... {len(preview.diff_lines) - len(shown_diff)} more change(s)"
            )
        held_labels = [
            get_import_capability_info(capability)[0]
            for capability in preview.capabilities
        ]
        held_summary = (
            "\n\nHeld disabled for separate review: " + ", ".join(held_labels) + "."
            if held_labels else "\n\nNo executable or outbound capabilities were detected."
        )
        if not ask_premium_confirmation(
            self,
            title="Review configuration import",
            body=(
                "StreamKeep validated this versioned export. Review the bounded "
                "preference diff before replacing the current configuration."
            ),
            eyebrow="CONFIGURATION",
            badge_text="Import review",
            tone="warning" if held_labels else "info",
            summary_title=f"{len(preview.diff_lines)} preference change(s)",
            summary_body="\n".join(shown_diff) + held_summary,
            primary_label="Continue review",
            secondary_label="Cancel import",
            default_action="secondary",
        ):
            self._set_status("Config import cancelled; no changes were applied.", "idle")
            return

        approved = []
        for capability in preview.capabilities:
            label, consequence = get_import_capability_info(capability)
            if ask_premium_confirmation(
                self,
                title=f"Enable imported {label}?",
                body=consequence,
                eyebrow="CAPABILITY REVIEW",
                badge_text="Disabled by default",
                tone="warning",
                summary_title=f"Imported {label} remain quarantined",
                summary_body=(
                    "Choose Enable only if you trust the source and intend this "
                    "specific behavior. Other imported capabilities are reviewed separately."
                ),
                primary_label=f"Enable {label}",
                secondary_label="Keep disabled",
                default_action="secondary",
            ):
                approved.append(capability)
        new_cfg = finalize_config_import(preview, approved)

        # Persist before mutating runtime/UI state. A failed save leaves the
        # pre-import config and all active behavior untouched.
        if not _save_config(new_cfg):
            from ...config import get_last_config_error
            detail = get_last_config_error() or "secure credential storage unavailable"
            self._log(f"[CONFIG] Import was not applied: {detail}")
            self._set_status("Import failed: secure credential storage unavailable.", "error")
            return
        self._config = new_cfg
        # Clear mutable state that _apply_config appends to
        self._history.clear()
        self.monitor.entries.clear()
        # Library/monitor/queue state is forbidden in config exports and remains
        # in the existing SQLite database.
        self.monitor.load_from_db()
        # Re-apply config to all UI elements
        self._apply_config()
        # Refresh derived views
        self._refresh_history_table()
        self._refresh_download_summary()
        self._refresh_monitor_table()
        self._refresh_monitor_summary()
        self._refresh_history_summary()
        if hasattr(self, "queue_table"):
            self._refresh_queue_table()
        self._log(f"[CONFIG] Imported from {path}")
        held_count = len(preview.capabilities) - len(approved)
        suffix = (
            f" {held_count} capability/capabilities remain disabled."
            if held_count else ""
        )
        self._set_status(
            f"Config imported from {path}.{suffix} Some changes may require a restart.",
            "success",
        )
