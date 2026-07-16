"""Settings tab — the biggest tab (510+ lines of field blocks) plus the
``SettingsTabMixin`` handler class that is mixed into ``StreamKeep``.

Groups: default output, toolchain probe, cookies, network + rate limit +
bandwidth schedule + parallel connections, YouTube extras, templates,
webhook, dedup, media library, post-processing + converter, manual
converter buttons, import/export/save row.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ... import VERSION
from ... import db as _db
from ...capabilities import get_runtime_capabilities
from ...extractors import Extractor
from ...extractors.twitch import TwitchExtractor
from ...extractors.ytdlp import YtDlpExtractor, ytdlp_runtime_status
from ...http import set_native_proxy
from ...i18n import available_languages, install_translator
from ...paths import CONFIG_FILE
from ...config import save_config as _save_config
from ...postprocess import (
    AUDIO_CODECS, AUDIO_CONTAINERS, ConvertWorker, PostProcessor,
    VIDEO_CONTAINERS, VIDEO_EXTS, AUDIO_EXTS,
    available_video_codec_keys,
)
from ...theme import CAT
from ...local_server import LocalCompanionServer
from ...updater import (
    DownloadUpdateWorker, UpdateCheckWorker, arm_self_replace,
    sha256_metadata_error,
)
from ...utils import (
    DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE,
    default_output_dir as _default_output_dir,
    render_template as _render_template,
    scan_browser_cookies as _scan_browser_cookies,
)
from ..widgets import (
    ask_premium_confirmation,
    ask_premium_text_input,
    make_dialog_section,
    make_field_block,
    make_metric_card,
    make_status_banner,
    show_premium_message,
    update_status_banner,
)


BUILTIN_PRESETS = {
    "Archive Quality": {
        "extract_audio": False, "normalize_loudness": True,
        "reencode_h265": True, "contact_sheet": True,
        "split_by_chapter": False, "remove_silence": False,
        "convert_video": False, "convert_audio": False,
    },
    "Quick Share": {
        "extract_audio": False, "normalize_loudness": False,
        "reencode_h265": False, "contact_sheet": False,
        "split_by_chapter": False, "remove_silence": False,
        "convert_video": True, "convert_video_format": "mp4",
        "convert_video_codec": "h264", "convert_video_scale": "720p",
        "convert_video_fps": "30", "convert_audio": False,
    },
    "Raw — No Processing": {
        "extract_audio": False, "normalize_loudness": False,
        "reencode_h265": False, "contact_sheet": False,
        "split_by_chapter": False, "remove_silence": False,
        "convert_video": False, "convert_audio": False,
    },
}


def _pp_snapshot():
    """Capture the current PostProcessor state as a dict."""
    return {
        "extract_audio": PostProcessor.extract_audio,
        "normalize_loudness": PostProcessor.normalize_loudness,
        "reencode_h265": PostProcessor.reencode_h265,
        "contact_sheet": PostProcessor.contact_sheet,
        "split_by_chapter": PostProcessor.split_by_chapter,
        "remove_silence": PostProcessor.remove_silence,
        "silence_noise_db": PostProcessor.silence_noise_db,
        "silence_min_duration": PostProcessor.silence_min_duration,
        "convert_video": PostProcessor.convert_video,
        "convert_video_format": PostProcessor.convert_video_format,
        "convert_video_codec": PostProcessor.convert_video_codec,
        "convert_video_scale": PostProcessor.convert_video_scale,
        "convert_video_fps": PostProcessor.convert_video_fps,
        "convert_audio": PostProcessor.convert_audio,
        "convert_audio_format": PostProcessor.convert_audio_format,
        "convert_audio_codec": PostProcessor.convert_audio_codec,
        "convert_audio_bitrate": PostProcessor.convert_audio_bitrate,
        "convert_audio_samplerate": PostProcessor.convert_audio_samplerate,
        "convert_delete_source": PostProcessor.convert_delete_source,
    }


def _pp_apply_snapshot(snap, win=None):
    """Apply a preset dict to the PostProcessor class vars and optionally
    refresh the Settings tab widgets."""
    for key, val in snap.items():
        if hasattr(PostProcessor, key):
            setattr(PostProcessor, key, val)
    if win is None:
        return
    # Refresh UI checkboxes/combos to match
    _setc = lambda w, v: (w.blockSignals(True), w.setChecked(bool(v)), w.blockSignals(False))
    if hasattr(win, "pp_audio_check"):
        _setc(win.pp_audio_check, PostProcessor.extract_audio)
    if hasattr(win, "pp_loud_check"):
        _setc(win.pp_loud_check, PostProcessor.normalize_loudness)
    if hasattr(win, "pp_h265_check"):
        _setc(win.pp_h265_check, PostProcessor.reencode_h265)
    if hasattr(win, "pp_contact_check"):
        _setc(win.pp_contact_check, PostProcessor.contact_sheet)
    if hasattr(win, "pp_split_check"):
        _setc(win.pp_split_check, PostProcessor.split_by_chapter)
    if hasattr(win, "pp_silence_check"):
        _setc(win.pp_silence_check, PostProcessor.remove_silence)
    if hasattr(win, "pp_silence_db_spin"):
        win.pp_silence_db_spin.setValue(int(PostProcessor.silence_noise_db or -30))
    if hasattr(win, "pp_silence_dur_spin"):
        win.pp_silence_dur_spin.setValue(int(PostProcessor.silence_min_duration or 3))
    if hasattr(win, "pp_convert_video_check"):
        _setc(win.pp_convert_video_check, PostProcessor.convert_video)
    if hasattr(win, "pp_convert_audio_check"):
        _setc(win.pp_convert_audio_check, PostProcessor.convert_audio)


def _update_webhook_indicator(win, url):
    """Show auto-detected webhook type below the URL input."""
    url = (url or "").strip()
    lbl = getattr(win, "_webhook_type_label", None)
    if not lbl:
        return
    if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
        lbl.setText("\u2714 Discord webhook detected")
    elif "hooks.slack.com" in url:
        lbl.setText("\u2714 Slack incoming webhook detected")
    elif "api.telegram.org/bot" in url:
        if "chat_id=" in url:
            lbl.setText("\u2714 Telegram bot detected (chat_id found)")
        else:
            lbl.setText("\u26A0 Telegram \u2014 add ?chat_id=YOUR_ID to the URL")
    elif "ntfy.sh" in url or "/ntfy/" in url:
        lbl.setText("\u2714 ntfy push notification detected")
    elif url:
        lbl.setText("Generic JSON POST endpoint")
    else:
        lbl.setText("")


def _get_user_presets(win):
    """Return the user-defined presets dict from config."""
    cfg = getattr(win, "_config", {})
    return dict(cfg.get("pp_presets", {}))


def _save_user_presets(win, presets):
    cfg = getattr(win, "_config", {})
    cfg["pp_presets"] = dict(presets)


def _populate_pp_presets(win):
    """Refresh the preset combo box."""
    combo = win.pp_preset_combo
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("(custom)", userData="")
    for name in BUILTIN_PRESETS:
        combo.addItem(f"★ {name}", userData=name)
    for name in _get_user_presets(win):
        combo.addItem(name, userData=name)
    combo.setCurrentIndex(0)
    combo.blockSignals(False)


def _on_pp_preset_selected(win):
    """User picked a preset from the combo — apply it."""
    name = win.pp_preset_combo.currentData()
    if not name:
        return  # "(custom)" selected — no-op
    snap = BUILTIN_PRESETS.get(name) or _get_user_presets(win).get(name)
    if snap:
        _pp_apply_snapshot(snap, win)


def _on_pp_preset_save(win):
    """Save current PP state as a named preset."""
    name, ok = ask_premium_text_input(
        win,
        title="Save post-processing preset",
        body=(
            "Capture the current conversion, cleanup, and archive settings so "
            "you can reuse them later without rebuilding the whole stack."
        ),
        eyebrow="POST-PROCESSING",
        badge_text="Preset",
        tone="info",
        summary_title="Built-in presets stay read-only",
        summary_body="Saved presets capture the current post-processing toggles exactly as shown below.",
        field_label="Preset name",
        field_hint="Use a short label that will still make sense when it appears in the preset picker.",
        placeholder="Weekend archive",
        primary_label="Save preset",
        secondary_label="Cancel",
        validator=lambda value: (bool((value or "").strip()), "Enter a preset name."),
    )
    if not ok:
        return
    if name in BUILTIN_PRESETS:
        show_premium_message(
            win,
            title="Built-in presets are locked",
            body="Pick a different name if you want to save your current adjustments as a reusable custom preset.",
            eyebrow="POST-PROCESSING",
            badge_text="Preset",
            tone="warning",
            summary_title="Archive Quality, Quick Share, and Raw — No Processing stay unchanged.",
            primary_label="Close",
        )
        return
    presets = _get_user_presets(win)
    if name in presets and not ask_premium_confirmation(
        win,
        title="Replace the existing preset?",
        body="Saving again will update this preset with the current post-processing settings.",
        eyebrow="POST-PROCESSING",
        badge_text="Overwrite",
        tone="warning",
        summary_title=f"\"{name}\" already exists.",
        summary_body="Replace it only if these are the settings you want the picker to load next time.",
        primary_label="Replace preset",
        secondary_label="Cancel",
        default_action="secondary",
    ):
        return
    presets[name] = _pp_snapshot()
    _save_user_presets(win, presets)
    _populate_pp_presets(win)
    # Select the newly saved preset
    idx = win.pp_preset_combo.findData(name)
    if idx >= 0:
        win.pp_preset_combo.setCurrentIndex(idx)


def _on_pp_preset_delete(win):
    """Delete the currently selected user preset."""
    name = win.pp_preset_combo.currentData()
    if not name or name in BUILTIN_PRESETS:
        return
    presets = _get_user_presets(win)
    presets.pop(name, None)
    _save_user_presets(win, presets)
    _populate_pp_presets(win)


class SettingsTabMixin:
    """Settings-tab handler methods, mixed into ``StreamKeep``."""

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

    # ── Settings helpers ─────────────────────────────────────────────

    def _settings_browse(self, line_edit):
        d = QFileDialog.getExistingDirectory(self, "Select Folder", line_edit.text())
        if d:
            line_edit.setText(d)

    # ── Browser cookies ──────────────────────────────────────────────

    def _scan_browsers(self):
        """Scan for installed browsers by checking cookie database locations."""
        return _scan_browser_cookies()

    def _scan_browsers_silent(self):
        """Populate combo with scanned browsers without UI feedback."""
        found = self._scan_browsers()
        self.cookies_combo.clear()
        self.cookies_combo.addItem("None")
        seen = set()
        for display, ytdlp_name, path in found:
            label = f"{display} ({ytdlp_name})"
            if label not in seen:
                self.cookies_combo.addItem(label, ytdlp_name)
                seen.add(label)
        # Also add manual entries for common browsers not found
        for name in ["chrome", "chromium", "firefox", "edge", "brave", "opera", "vivaldi", "safari"]:
            manual_label = f"{name} (manual)"
            if not any(name == ytdlp for _, ytdlp, _ in found):
                self.cookies_combo.addItem(manual_label, name)

    def _on_scan_browsers(self):
        found = self._scan_browsers()
        self.cookies_combo.clear()
        self.cookies_combo.addItem("None")
        seen = set()
        for display, ytdlp_name, path in found:
            label = f"{display} ({ytdlp_name})"
            if label not in seen:
                self.cookies_combo.addItem(label, ytdlp_name)
                seen.add(label)
        # Manual fallbacks
        for name in ["chrome", "chromium", "firefox", "edge", "brave"]:
            manual_label = f"{name} (manual)"
            if not any(name == ytdlp for _, ytdlp, _ in found):
                self.cookies_combo.addItem(manual_label, name)

        if found:
            details = "\n".join(f"  {d} -> {p}" for d, _, p in found)
            self.cookies_scan_label.setText(f"Found {len(found)} browser(s):\n{details}")
            self._log(f"[SCAN] Found {len(found)} browser cookie stores:")
            for d, y, p in found:
                self._log(f"  {d} ({y}) -> {p}")
            self._set_status(f"Found {len(found)} browser cookie store(s).", "success")
        else:
            self.cookies_scan_label.setText("No browser cookie stores found.")
            self._set_status("No browser cookie stores were found on this machine.", "warning")

    def _on_browse_cookies_file(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select Cookies File", str(Path.home()),
            "Cookie files (*.txt *.sqlite);;All files (*)"
        )
        if f:
            self.cookies_file_input.setText(f)
            # Also import to StreamKeep's cookies.txt (F47)
            from ...cookies import import_from_file
            ok, msg = import_from_file(f)
            self._update_cookies_status()
            if ok:
                self._set_status(msg, "success")
            else:
                self._set_status(msg, "warning")

    def _on_import_browser_cookies(self):
        """Extract cookies from the selected browser to cookies.txt (F47)."""
        from ...cookies import import_from_browser
        browser_text = self.cookies_combo.currentText()
        browser_data = self.cookies_combo.currentData()
        if browser_text == "None" or not browser_data:
            self._set_status("Select a browser first, then click Import.", "warning")
            return
        ytdlp_name = str(browser_data)
        ok, msg = import_from_browser(ytdlp_name)
        self._update_cookies_status()
        if ok:
            self._set_status(msg, "success")
            self._log(f"[COOKIES] {msg}")
        else:
            self._set_status(msg, "error")
            self._log(f"[COOKIES] Error: {msg}")

    def _on_clear_cookies(self):
        """Delete the cookies.txt file (F47)."""
        from ...cookies import clear_cookies
        ok, msg = clear_cookies()
        self._update_cookies_status()
        self._set_status(msg, "success" if ok else "error")

    def _update_cookies_status(self):
        """Refresh the cookies status label (F47)."""
        from ...cookies import cookies_file_path, cookies_file_age_secs
        label = getattr(self, "cookies_status_label", None)
        if label is None:
            return
        cpath = cookies_file_path()
        if not cpath:
            label.setText("No cookies.txt — authenticated content may fail.")
            return
        age = cookies_file_age_secs()
        if age < 0:
            label.setText("cookies.txt present.")
        elif age < 3600:
            label.setText(f"cookies.txt present (updated {age // 60}m ago).")
        elif age < 86400:
            label.setText(f"cookies.txt present (updated {age // 3600}h ago).")
        else:
            days = age // 86400
            label.setText(f"cookies.txt present ({days}d old — consider refreshing).")

    # ── Platform account tokens ──────────────────────────────────────

    def _on_save_account_tokens(self):
        """Persist platform tokens to encrypted storage (F48)."""
        from ...accounts import set_credential, credential_status
        from ...secrets import SecretStorageError
        inputs = getattr(self, "_account_inputs", {})
        saved = 0
        errors = []
        for plat_key, (inp, status_label) in inputs.items():
            val = inp.text().strip()
            if val:
                try:
                    set_credential(plat_key, val)
                except SecretStorageError as e:
                    errors.append(str(e))
                    status_label.setText("secure store unavailable")
                    continue
                inp.clear()
                saved += 1
            status_label.setText(credential_status(plat_key))
        if errors:
            self._log(f"[ACCOUNTS] Secure credential storage unavailable: {errors[0]}")
            self._set_status("Token save blocked: secure credential storage unavailable.", "error")
            show_premium_message(
                self,
                title="Secure credential storage unavailable",
                body=(
                    "StreamKeep did not save the token because Windows DPAPI or "
                    "keyring storage is unavailable. Install/configure keyring "
                    "or fix the OS credential store, then save again."
                ),
                eyebrow="ACCOUNTS",
                badge_text="Not saved",
                tone="error",
                summary_title="No reversible fallback was written.",
                summary_body="Existing legacy values can still be read, but new tokens require secure storage.",
                primary_label="Close",
                min_width=620,
            )
            return
        if saved:
            self._set_status(f"Saved {saved} token(s).", "success")
            self._log(f"[ACCOUNTS] Saved {saved} platform token(s)")

    def _on_clear_account_tokens(self):
        """Delete all stored platform tokens (F48)."""
        from ...accounts import delete_credential, PLATFORMS, credential_status
        inputs = getattr(self, "_account_inputs", {})
        for plat_key in PLATFORMS:
            delete_credential(plat_key)
            if plat_key in inputs:
                inputs[plat_key][0].clear()
                inputs[plat_key][1].setText(credential_status(plat_key))
        self._set_status("All platform tokens cleared.", "success")

    # ── Proxy pool ───────────────────────────────────────────────────

    def _save_proxy_pool(self):
        """Parse the proxy pool text and persist (F49)."""
        from ...proxy import set_pool
        edit = getattr(self, "proxy_pool_edit", None)
        if edit is None:
            return
        entries = []
        for line in edit.toPlainText().strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            url = parts[0].strip()
            if not url:
                continue
            platforms = [p.strip() for p in (parts[1] if len(parts) > 1 else "").split(",") if p.strip()]
            label = parts[2].strip() if len(parts) > 2 else ""
            entries.append({
                "url": url, "platforms": platforms,
                "label": label, "enabled": True,
            })
        set_pool(entries)
        self._config["proxy_pool"] = entries

    def _on_test_proxies(self):
        """Run health checks on all proxy pool entries (F49)."""
        self._save_proxy_pool()
        from ...proxy import health_check_all
        results = health_check_all(timeout=8)
        if not results:
            self._set_status("No proxies configured to test.", "warning")
            return
        lines = []
        for label, ok, ms in results:
            if ok:
                lines.append(f"  {label}: {ms}ms")
            else:
                lines.append(f"  {label}: FAILED")
        self._log("[PROXY] Health check results:\n" + "\n".join(lines))
        ok_count = sum(1 for _, ok, _ in results if ok)
        self._set_status(
            f"Proxy test: {ok_count}/{len(results)} reachable.",
            "success" if ok_count == len(results) else "warning",
        )

    # ── Save settings ────────────────────────────────────────────────

    def _on_save_settings(self):
        self.output_input.setText(self.settings_output.text())
        # Apply browser cookies setting
        browser_text = self.cookies_combo.currentText()
        browser_data = self.cookies_combo.currentData()
        if browser_text == "None":
            YtDlpExtractor.cookies_browser = ""
            self._config["cookies_browser"] = ""
        else:
            ytdlp_name = browser_data if browser_data else browser_text
            YtDlpExtractor.cookies_browser = ytdlp_name
            self._config["cookies_browser"] = ytdlp_name
        # Apply cookies file
        cookies_file = self.cookies_file_input.text().strip()
        YtDlpExtractor.cookies_file = cookies_file
        self._config["cookies_file"] = cookies_file
        # Apply rate limit
        rate_limit = self.rate_limit_input.text().strip()
        YtDlpExtractor.rate_limit = rate_limit
        self._config["rate_limit"] = rate_limit
        # Apply proxy (also routes native extractor curl calls through it)
        proxy = self.proxy_input.text().strip()
        YtDlpExtractor.proxy = proxy
        self._config["proxy"] = proxy
        set_native_proxy(proxy)
        import streamkeep.ui.main_window as _mw_mod
        _mw_mod.NATIVE_PROXY = proxy
        # Save proxy pool (F49)
        self._save_proxy_pool()
        # Save speed schedule (F51)
        if hasattr(self, "sched_enable_check"):
            sched = {
                "enabled": self.sched_enable_check.isChecked(),
                "day_start": self.sched_day_start.value(),
                "day_end": self.sched_day_end.value(),
                "day_limit": self.sched_day_limit.text().strip(),
                "night_limit": self.sched_night_limit.text().strip(),
                "weekend_limit": self.sched_weekend_limit.text().strip(),
            }
            self._config["speed_schedule"] = sched
            from ...scheduler import configure as _sched_configure
            _sched_configure(sched, rate_limit)
        # Apply parallel connections (affects direct MP4 downloads)
        self._parallel_connections = max(1, min(16, self.parallel_spin.value()))
        # Parallel auto-records + chunked live captures (v4.15.0).
        if hasattr(self, "parallel_autorecords_spin"):
            self._parallel_autorecords = max(1, min(4, self.parallel_autorecords_spin.value()))
            self._config["parallel_autorecords"] = self._parallel_autorecords
        if hasattr(self, "concurrent_queue_spin"):
            self._max_concurrent_downloads = max(1, min(8, self.concurrent_queue_spin.value()))
            self._config["max_concurrent_downloads"] = self._max_concurrent_downloads
        if hasattr(self, "chunk_check"):
            self._chunk_long_captures = self.chunk_check.isChecked()
            self._config["chunk_long_captures"] = self._chunk_long_captures
        if hasattr(self, "chunk_length_spin"):
            self._chunk_length_secs = int(self.chunk_length_spin.value())
            self._config["chunk_length_secs"] = self._chunk_length_secs
        if hasattr(self, "companion_check"):
            prev_enabled = bool(self._config.get("companion_server_enabled", False))
            prev_bind_lan = bool(self._config.get("companion_bind_lan", False))
            new_enabled = bool(self.companion_check.isChecked())
            new_bind_lan = bool(self.companion_lan_check.isChecked())
            self._config["companion_server_enabled"] = new_enabled
            self._config["companion_bind_lan"] = new_bind_lan
            if new_enabled != prev_enabled or new_bind_lan != prev_bind_lan:
                self._maybe_start_companion_server(
                    force_restart=prev_enabled and new_enabled and new_bind_lan != prev_bind_lan
                )
            else:
                self._refresh_companion_ui()
        if hasattr(self, "update_check_check"):
            self._config["check_for_updates"] = bool(self.update_check_check.isChecked())
        if hasattr(self, "capture_chat_check"):
            self._config["capture_live_chat"] = bool(self.capture_chat_check.isChecked())
        if hasattr(self, "render_chat_ass_check"):
            self._config["render_chat_ass"] = bool(self.render_chat_ass_check.isChecked())
        if hasattr(self, "quality_defaults_combos"):
            self._config["quality_defaults"] = {
                plat: (combo.currentData() or "")
                for plat, combo in self.quality_defaults_combos.items()
            }
        if hasattr(self, "whisper_model_combo"):
            self._config["whisper_model"] = str(self.whisper_model_combo.currentData() or "tiny")
        if hasattr(self, "diarize_check"):
            self._config["enable_diarization"] = bool(self.diarize_check.isChecked())
        if hasattr(self, "hf_token_input"):
            self._config["hf_token"] = self.hf_token_input.text().strip()
        # Chat render settings (F22)
        if hasattr(self, "chat_render_width_spin"):
            self._config["chat_render_width"] = self.chat_render_width_spin.value()
            self._config["chat_render_height"] = self.chat_render_height_spin.value()
            self._config["chat_render_font_size"] = self.chat_render_font_spin.value()
            self._config["chat_render_msg_duration"] = self.chat_render_duration_spin.value()
            self._config["chat_render_bg_opacity"] = self.chat_render_opacity_spin.value()
        if hasattr(self, "notif_sound_check"):
            self._config["notif_sound"] = bool(self.notif_sound_check.isChecked())
        # Apply bandwidth schedule rule
        self._bandwidth_rule = {
            "enabled": self.bw_enable_check.isChecked(),
            "start_hour": self.bw_start_spin.value(),
            "end_hour": self.bw_end_spin.value(),
            "limit": self.bw_limit_input.text().strip(),
        }
        self._apply_bandwidth_schedule()
        # Apply YouTube extras
        YtDlpExtractor.download_subs = self.subs_check.isChecked()
        self._config["download_subs"] = YtDlpExtractor.download_subs
        YtDlpExtractor.sponsorblock = self.sponsorblock_check.isChecked()
        self._config["sponsorblock"] = YtDlpExtractor.sponsorblock
        # Apply filename templates
        self._folder_template = self.folder_template_input.text().strip() or DEFAULT_FOLDER_TEMPLATE
        self._file_template = self.file_template_input.text().strip() or DEFAULT_FILE_TEMPLATE
        # Apply webhook
        self._webhook_url = self.webhook_input.text().strip()
        # Apply event hooks (F24)
        if hasattr(self, "hooks_table"):
            hooks = {}
            for i in range(self.hooks_table.rowCount()):
                evt = self.hooks_table.item(i, 0).text()
                cmd = (self.hooks_table.item(i, 1).text() or "").strip()
                if cmd:
                    hooks[evt] = cmd
            self._config["hooks"] = hooks
        # Apply duplicate detection
        self._check_duplicates = self.dup_check.isChecked()
        # Apply lifecycle policies (F32)
        if hasattr(self, "lc_enable_check"):
            self._config["lifecycle"] = {
                "enabled": self.lc_enable_check.isChecked(),
                "max_days": self.lc_max_days_spin.value(),
                "max_total_gb": self.lc_max_gb_spin.value(),
                "delete_watched": self.lc_watched_check.isChecked(),
                "favorites_exempt": self.lc_fav_exempt_check.isChecked(),
            }
        # Apply library/NFO + chat
        from ...extractors.twitch import TwitchExtractor
        self._write_nfo = self.nfo_check.isChecked()
        TwitchExtractor.download_chat_enabled = self.chat_check.isChecked()
        # Apply media server auto-import (F33)
        if hasattr(self, "ms_enable_check"):
            from ...integrations.media_server import SERVER_TYPES
            self._config["media_server"] = {
                "enabled": self.ms_enable_check.isChecked(),
                "server_type": SERVER_TYPES[self.ms_type_combo.currentIndex()],
                "url": self.ms_url_input.text().strip(),
                "token": self.ms_token_input.text().strip(),
                "library_id": self.ms_library_id_input.text().strip(),
                "library_path": self.ms_path_input.text().strip(),
            }
        # Apply post-processing presets
        PostProcessor.extract_audio = self.pp_audio_check.isChecked()
        PostProcessor.normalize_loudness = self.pp_loud_check.isChecked()
        PostProcessor.reencode_h265 = self.pp_h265_check.isChecked()
        PostProcessor.contact_sheet = self.pp_contact_check.isChecked()
        PostProcessor.split_by_chapter = self.pp_split_check.isChecked()
        if hasattr(self, "pp_silence_check"):
            PostProcessor.remove_silence = self.pp_silence_check.isChecked()
            PostProcessor.silence_noise_db = self.pp_silence_db_spin.value()
            PostProcessor.silence_min_duration = float(self.pp_silence_dur_spin.value())
        # Converter settings
        PostProcessor.convert_video = self.pp_convert_video_check.isChecked()
        PostProcessor.convert_video_format = self.pp_convert_video_format.currentText()
        PostProcessor.convert_video_codec = self.pp_convert_video_codec.currentText()
        PostProcessor.convert_video_scale = self.pp_convert_video_scale.currentText()
        PostProcessor.convert_video_fps = self.pp_convert_video_fps.currentText()
        PostProcessor.convert_audio = self.pp_convert_audio_check.isChecked()
        PostProcessor.convert_audio_format = self.pp_convert_audio_format.currentText()
        PostProcessor.convert_audio_codec = self.pp_convert_audio_codec.currentText()
        PostProcessor.convert_audio_bitrate = self.pp_convert_audio_bitrate.currentText()
        PostProcessor.convert_audio_samplerate = self.pp_convert_audio_samplerate.currentText()
        PostProcessor.convert_delete_source = self.pp_convert_delete_check.isChecked()
        if not self._persist_config():
            from ...config import get_last_config_error
            detail = get_last_config_error() or "secure credential storage unavailable"
            self._log(f"[SETTINGS] Settings were not saved: {detail}")
            self._set_status("Settings not saved: secure credential storage unavailable.", "error")
            return
        self._refresh_download_summary()
        self._set_status("Settings saved and applied to future downloads.", "success")

    # ── Theme ────────────────────────────────────────────────────────

    def _on_theme_changed(self, _idx):
        """Apply theme switch instantly (F20)."""
        from ..theme import apply_theme
        from PyQt6.QtWidgets import QApplication
        name = self.theme_combo.currentData() or "dark"
        self._config["theme"] = name
        apply_theme(name, app=QApplication.instance())
        if hasattr(self, "settings_theme_value"):
            theme_display = {"dark": "Dark", "light": "Light", "system": "System"}.get(
                name, "Dark"
            )
            self.settings_theme_value.setText(theme_display)
            self.settings_theme_sub.setText("Catppuccin-based desktop theme")
        if hasattr(self, "_stack"):
            self._switch_tab(self._stack.currentIndex())
        if hasattr(self, "status_label"):
            pill_to_tone = {
                "Standby": "idle",
                "Working": "working",
                "Finalizing": "processing",
                "Ready": "success",
                "Alert": "warning",
                "Error": "error",
            }
            current_tone = pill_to_tone.get(
                self.status_pill.text() if hasattr(self, "status_pill") else "Standby",
                "idle",
            )
            self._set_status(self.status_label.text() or "Theme updated.", current_tone)
        self._refresh_notif_badge()
        self._persist_config()

    def _on_language_changed(self, _idx):
        lang = self.language_combo.currentData() or "en"
        if install_translator(lang):
            self._config["language"] = lang
            self._set_status(
                "Language setting saved. Restart StreamKeep to refresh all labels.",
                "success",
            )
        else:
            self._set_status("Language file could not be loaded.", "warning")
        self._persist_config()

    # ── Browser companion ────────────────────────────────────────────

    def _on_companion_toggled(self, checked):
        """Settings toggle — start or stop the companion server in-place."""
        self._config["companion_server_enabled"] = bool(checked)
        if hasattr(self, "companion_lan_check"):
            self._config["companion_bind_lan"] = bool(self.companion_lan_check.isChecked())
        self._persist_config()
        self._maybe_start_companion_server()
        if checked:
            if self._companion_server is not None:
                self._set_status("Browser companion ready for one-click capture.", "success")
            else:
                self._set_status("Browser companion could not start. Review the Settings panel for details.", "warning")
        else:
            self._set_status("Browser companion disabled.", "idle")

    def _on_companion_scope_toggled(self, checked):
        """Persist LAN scope changes and restart the server if needed."""
        self._config["companion_bind_lan"] = bool(checked)
        self._persist_config()
        if bool(self._config.get("companion_server_enabled", False)):
            self._maybe_start_companion_server(force_restart=self._companion_server is not None)
            if checked:
                self._set_status("Browser companion restarted with LAN access enabled.", "warning")
            else:
                self._set_status("Browser companion returned to local-only access.", "success")
        else:
            self._refresh_companion_ui()
            self._set_status("Browser companion access scope saved.", "success")

    def _copy_text_to_clipboard(self, text, label):
        value = str(text or "").strip()
        if not value:
            self._set_status(f"{label} is not available yet.", "warning")
            return
        try:
            from PyQt6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            if clipboard is None:
                raise RuntimeError("Clipboard unavailable")
            clipboard.setText(value)
            self._set_status(f"{label} copied to clipboard.", "success")
        except Exception as e:
            self._log(f"[CLIPBOARD] Could not copy {label.lower()}: {e}")
            self._set_status(f"Could not copy {label.lower()}.", "error")

    def _companion_local_url(self):
        srv = getattr(self, "_companion_server", None)
        port = int(getattr(srv, "port", 0) or 0) if srv is not None else 0
        if port <= 0:
            return ""
        return getattr(srv, "url", "") or f"http://127.0.0.1:{port}/"

    def _refresh_companion_ui(self):
        """Update the Browser Companion settings panel from live state."""
        enabled = bool(self._config.get("companion_server_enabled", False))
        bind_lan = bool(self._config.get("companion_bind_lan", False))
        srv = getattr(self, "_companion_server", None)
        running = srv is not None and int(getattr(srv, "port", 0) or 0) > 0
        local_url = self._companion_local_url()
        token = str(getattr(srv, "token", "") or "") if running else ""
        error_text = str(getattr(self, "_companion_last_error", "") or "")

        if hasattr(self, "companion_check"):
            self.companion_check.blockSignals(True)
            self.companion_check.setChecked(enabled)
            self.companion_check.blockSignals(False)
        if hasattr(self, "companion_lan_check"):
            self.companion_lan_check.blockSignals(True)
            self.companion_lan_check.setChecked(bind_lan)
            self.companion_lan_check.blockSignals(False)

        if running and bind_lan:
            banner_title = "LAN access is enabled"
            banner_body = (
                "The companion is live on this PC and other devices on your network can connect if they know the token."
            )
            banner_tone = "warning"
        elif running:
            banner_title = "Ready for one-click capture"
            banner_body = (
                "The browser extension can hand URLs to StreamKeep on this PC, and the local web remote is ready."
            )
            banner_tone = "success"
        elif enabled and error_text:
            banner_title = "Companion could not start"
            banner_body = error_text
            banner_tone = "error"
        elif enabled:
            banner_title = "Starting the local receiver"
            banner_body = "StreamKeep is preparing a token-protected local endpoint for the extension and web remote."
            banner_tone = "info"
        else:
            banner_title = "Companion is off"
            banner_body = "Enable it when you want browser handoff or the lightweight local web remote."
            banner_tone = "info"

        if hasattr(self, "companion_status_banner"):
            update_status_banner(
                self.companion_status_banner,
                self.companion_status_title,
                self.companion_status_body,
                title=banner_title,
                body=banner_body,
                tone=banner_tone,
            )

        if hasattr(self, "companion_scope_value"):
            self.companion_scope_value.setText("LAN enabled" if bind_lan else "Local only")
            self.companion_scope_sub.setText(
                "Other trusted devices can reach the server"
                if bind_lan else
                "Only this PC can reach the companion"
            )
        if hasattr(self, "companion_remote_value"):
            if running:
                self.companion_remote_value.setText("Ready")
                self.companion_remote_sub.setText(local_url)
            elif enabled and error_text:
                self.companion_remote_value.setText("Error")
                self.companion_remote_sub.setText(error_text[:72])
            elif enabled:
                self.companion_remote_value.setText("Starting")
                self.companion_remote_sub.setText("Waiting for the local listener")
            else:
                self.companion_remote_value.setText("Off")
                self.companion_remote_sub.setText("Enable the companion to expose a local URL")
        if hasattr(self, "companion_token_value"):
            if token:
                self.companion_token_value.setText("Ready")
                self.companion_token_sub.setText("Rotate on demand or on next launch")
            elif enabled:
                self.companion_token_value.setText("Pending")
                self.companion_token_sub.setText("Generated after the server starts")
            else:
                self.companion_token_value.setText("Waiting")
                self.companion_token_sub.setText("Generated only while the companion is on")

        if hasattr(self, "companion_rotate_token_btn"):
            self.companion_rotate_token_btn.setEnabled(bool(token))

        if hasattr(self, "companion_url_display"):
            self.companion_url_display.setText(local_url)
        if hasattr(self, "companion_open_url_btn"):
            self.companion_open_url_btn.setEnabled(bool(local_url))
        if hasattr(self, "companion_copy_url_btn"):
            self.companion_copy_url_btn.setEnabled(bool(local_url))
        if hasattr(self, "companion_token_display"):
            self.companion_token_display.setText(token)
        if hasattr(self, "companion_copy_token_btn"):
            self.companion_copy_token_btn.setEnabled(bool(token))

    def _on_copy_companion_url(self):
        self._copy_text_to_clipboard(self._companion_local_url(), "Browser companion URL")

    def _on_copy_companion_token(self):
        text = self.companion_token_display.text() if hasattr(self, "companion_token_display") else ""
        self._copy_text_to_clipboard(text, "Pairing token")

    def _on_rotate_companion_token(self):
        srv = getattr(self, "_companion_server", None)
        if srv is None or int(getattr(srv, "port", 0) or 0) <= 0:
            self._set_status("Companion server is not running.", "warning")
            return
        srv.rotate_token()
        self._refresh_companion_ui()
        self._log("[COMPANION] Token rotated — old tokens are now invalid.")
        self._set_status("Pairing token rotated. Re-pair the browser extension.", "success")

    def _on_open_companion_remote(self):
        url = self._companion_local_url()
        if not url:
            self._set_status("Browser companion web remote is not available yet.", "warning")
            return
        QDesktopServices.openUrl(QUrl(url))
        self._set_status("Opened the browser companion web remote.", "success")

    # ── Lifecycle cleanup ────────────────────────────────────────────

    def _on_lifecycle_preview(self):
        """Show a preview of what the lifecycle cleanup would remove."""
        from ...lifecycle import evaluate_cleanup, execute_cleanup, removal_real_paths
        policy = self._config.get("lifecycle", {})
        if not policy.get("enabled"):
            policy = dict(policy, enabled=True)  # preview even if disabled
        removals = evaluate_cleanup(self._history, policy)
        if not removals:
            show_premium_message(
                self,
                title="No recordings match the current cleanup rules",
                body="The current lifecycle policy would not recycle anything right now.",
                eyebrow="LIFECYCLE",
                badge_text="Preview",
                tone="info",
                summary_title="Nothing needs attention.",
                summary_body="Try widening the cleanup rules or revisit this preview after more recordings accumulate.",
                primary_label="Close",
                min_width=560,
            )
            return
        # Build preview text
        total_size = 0
        lines = []
        for h, reason in removals:
            title = getattr(h, "title", "") or "Untitled"
            path = getattr(h, "path", "") or ""
            sz = 0
            if path and os.path.isdir(path):
                for f in os.scandir(path):
                    if f.is_file():
                        try:
                            sz += f.stat().st_size
                        except OSError:
                            pass
            total_size += sz
            sz_mb = sz / (1024 * 1024)
            lines.append(f"  {title[:50]}  ({sz_mb:.1f} MB) — {reason}")
        detail_text = "\n".join(lines[:30])
        if len(lines) > 30:
            detail_text += f"\n  … and {len(lines) - 30} more"
        if ask_premium_confirmation(
            self,
            title="Review lifecycle cleanup",
            body="These recordings match the current cleanup policy and would be moved to the Recycle Bin.",
            eyebrow="LIFECYCLE",
            badge_text="Preview",
            tone="warning",
            summary_title=f"{len(removals)} recording(s) matched, using about {total_size / (1024 ** 3):.2f} GB.",
            summary_body="Files stay recoverable through the Recycle Bin if you need to bring something back.",
            details_title="Matched recordings",
            details_body=detail_text,
            primary_label="Recycle matches",
            secondary_label="Keep everything",
            default_action="secondary",
            min_width=720,
            min_height=520,
            details_monospaced=True,
        ):
            removed = execute_cleanup(removals, log_fn=self._log)
            if removed:
                removed_paths = {
                    real_path for real_path in removal_real_paths(removals)
                    if not os.path.isdir(real_path)
                }
                self._remove_history_for_paths(removed_paths)
            self._log(f"[LIFECYCLE] Recycled {removed} recording(s).")
            self._set_status(f"Lifecycle cleanup: {removed} recording(s) recycled.", "success")

    def _run_lifecycle_cleanup(self):
        """Run lifecycle cleanup silently after a download completes."""
        from ...lifecycle import evaluate_cleanup, execute_cleanup, removal_real_paths
        policy = self._config.get("lifecycle", {})
        if not policy or not policy.get("enabled"):
            return
        removals = evaluate_cleanup(self._history, policy)
        if removals:
            removed = execute_cleanup(removals, log_fn=self._log)
            if removed:
                removed_paths = {
                    real_path for real_path in removal_real_paths(removals)
                    if not os.path.isdir(real_path)
                }
                self._remove_history_for_paths(removed_paths)
                self._log(f"[LIFECYCLE] Auto-cleanup recycled {removed} recording(s).")

    # ── Browser companion local server ───────────────────────────────

    def _maybe_start_companion_server(self, force_restart=False):
        """Start (or stop) the local companion HTTP server based on the
        current Settings toggle. Called at launch and whenever the user
        changes the setting."""
        enabled = bool(self._config.get("companion_server_enabled", False))
        bind_lan = bool(self._config.get("companion_bind_lan", False))
        desired_bind = "0.0.0.0" if bind_lan else "127.0.0.1"
        running = self._companion_server is not None
        if running and getattr(self._companion_server, "_bind_addr", "") != desired_bind:
            force_restart = True
        if force_restart and running:
            try:
                self._companion_server.stop()
            except Exception:
                pass
            self._companion_server = None
            running = False
        if enabled and not running:
            try:
                srv = LocalCompanionServer(bind_lan=bind_lan)
                srv.state_provider = self._api_state_snapshot
                srv.url_received.connect(self._on_companion_url)
                srv.failed_job_retry_requested.connect(self._retry_failed_job)
                srv.failed_job_discard_requested.connect(self._discard_failed_job)
                srv.start()
                self._companion_server = srv
                self._companion_last_error = ""
                host = getattr(srv, "display_host", "127.0.0.1")
                self._log(
                    f"[COMPANION] Listening on {host}:{srv.port} "
                    f"— token in Settings tab."
                )
            except OSError as e:
                self._companion_last_error = str(e)
                self._log(f"[COMPANION] Could not start server: {e}")
        elif enabled and running:
            self._companion_last_error = ""
        elif not enabled and running:
            try:
                self._companion_server.stop()
            except Exception:
                pass
            self._companion_server = None
            self._companion_last_error = ""
            self._log("[COMPANION] Server stopped.")
        elif not enabled:
            self._companion_last_error = ""
        self._refresh_companion_ui()

    def _api_state_snapshot(self):
        """Return a dict snapshot of app state for the REST API (F37).
        Called from the HTTP server thread — must be thread-safe.
        Take list() copies of shared collections to avoid race conditions."""
        downloads = []
        queue_items = []
        try:
            for q in list(getattr(self, "_download_queue", [])):
                queue_items.append({
                    "job_id": q.get("job_id", ""),
                    "url": q.get("url", ""),
                    "title": q.get("title", ""),
                    "platform": q.get("platform", ""),
                    "status": q.get("status", ""),
                    "note": q.get("note", ""),
                    "failure_id": q.get("failure_id", 0),
                })
        except Exception:
            pass
        failures = []
        try:
            for row in _db.load_failed_jobs(limit=25):
                failures.append({
                    "id": row.get("id", 0),
                    "url": row.get("url", ""),
                    "title": row.get("title", ""),
                    "platform": row.get("platform", ""),
                    "stage": row.get("stage", ""),
                    "error": row.get("error", ""),
                    "output_dir": row.get("output_dir", ""),
                    "resume_sidecar": row.get("resume_sidecar", ""),
                    "retry_count": row.get("retry_count", 0),
                    "status": row.get("status", ""),
                    "updated_at": row.get("updated_at", ""),
                })
        except Exception:
            pass
        history = []
        try:
            for h in list(self._history)[-50:]:
                history.append({
                    "title": h.title or "",
                    "platform": h.platform or "",
                    "date": h.date or "",
                    "quality": h.quality or "",
                    "size": h.size or "",
                })
        except Exception:
            pass
        monitor = []
        try:
            for e in list(self.monitor.entries):
                monitor.append({
                    "channel_id": e.channel_id,
                    "platform": e.platform,
                    "status": e.last_status,
                })
        except Exception:
            pass
        live_channels = [m for m in monitor if m.get("status") == "live"]
        active_workers = []
        try:
            for ch_id, w in dict(getattr(self, "_autorecord_workers", {})).items():
                ctx = dict(getattr(self, "_autorecord_contexts", {})).get(ch_id, {})
                active_workers.append({
                    "type": "auto-record",
                    "channel": ch_id,
                    "title": ctx.get("q_name", ch_id),
                    "running": w.isRunning() if w else False,
                })
            if getattr(self, "download_worker", None) and self.download_worker.isRunning():
                active_workers.append({
                    "type": "foreground",
                    "title": str(getattr(self, "_active_stream_info", None) and
                                 getattr(self._active_stream_info, "title", "") or "Download"),
                    "running": True,
                })
        except Exception:
            pass
        resumable = []
        try:
            for rc in list(getattr(self, "_resume_candidates", [])):
                resumable.append({
                    "title": getattr(rc, "title", "") or "",
                    "url": getattr(rc, "url", "") or "",
                    "remaining": getattr(rc, "remaining_count", 0),
                })
        except Exception:
            pass
        return {
            "downloads": downloads,
            "queue": queue_items,
            "failures": failures,
            "history": history,
            "monitor": monitor,
            "live_channels": live_channels,
            "active_workers": active_workers,
            "resumable": resumable,
        }

    # ── Auto-update checker ──────────────────────────────────────────

    def _maybe_check_for_updates(self):
        """Kick off the GitHub release check if the user has opted in.
        Runs once per launch, on a short delay so the UI paints first."""
        if not bool(self._config.get("check_for_updates", False)):
            return
        # Only meaningful in a packaged exe — in a source checkout the
        # updater refuses the self-replace anyway. Skip the network call
        # entirely so source-checkout users don't see a banner.
        if not (getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")):
            return
        if getattr(self, "_update_check_worker", None) is not None:
            return
        worker = UpdateCheckWorker(VERSION)
        worker.result.connect(self._on_update_check_result)
        self._update_check_worker = worker
        worker.start()

    def _on_update_check_result(self, payload):
        worker = getattr(self, "_update_check_worker", None)
        if worker is not None and not worker.isRunning():
            try:
                worker.wait(200)
            except Exception:
                pass
        self._update_check_worker = None
        if not payload or not payload.get("available"):
            return
        tag = payload.get("tag", "")
        if tag == self._config.get("dismissed_update_tag", ""):
            return
        self._latest_update_payload = payload
        if hasattr(self, "update_banner_label"):
            notes = (payload.get("notes") or "").splitlines()
            first_note = next((ln for ln in notes if ln.strip()), "").strip()
            if first_note:
                first_note = first_note[:140] + ("..." if len(first_note) > 140 else "")
            label = f"StreamKeep {tag} is available (you're on v{VERSION})"
            if first_note:
                label = f"{label} — {first_note}"
            hash_error = sha256_metadata_error(payload.get("asset_sha256", ""))
            if hash_error:
                label = f"{label} — install blocked: {hash_error}"
            self.update_banner_label.setText(label)
            self.update_banner.setVisible(True)
        self._notify_center(f"Update available: StreamKeep {tag}", "info")

    def _on_update_install(self):
        payload = getattr(self, "_latest_update_payload", None) or {}
        asset_url = payload.get("asset_url", "")
        if not asset_url:
            self._set_status("Update available but no Windows asset was attached.", "warning")
            return
        hash_error = sha256_metadata_error(payload.get("asset_sha256", ""))
        if hash_error:
            msg = f"Update install blocked: {hash_error}"
            self._log(f"[UPDATE] {msg}")
            self._set_status(msg, "error")
            if hasattr(self, "update_banner_label"):
                self.update_banner_label.setText(msg)
            if hasattr(self, "update_banner_install_btn"):
                self.update_banner_install_btn.setEnabled(True)
                self.update_banner_install_btn.setText("Download & install")
            return
        self.update_banner_install_btn.setEnabled(False)
        self.update_banner_install_btn.setText("Downloading...")
        worker = DownloadUpdateWorker(
            asset_url,
            payload.get("asset_size", 0),
            payload.get("asset_sha256", ""),
        )
        worker.progress.connect(self._on_update_download_progress)
        worker.done.connect(self._on_update_download_done)
        self._update_download_worker = worker
        worker.start()

    def _on_update_download_progress(self, pct, status):
        if hasattr(self, "update_banner_install_btn"):
            self.update_banner_install_btn.setText(
                f"Downloading {status}" if status else f"Downloading {pct}%"
            )

    def _on_update_download_done(self, ok, path_or_err):
        worker = getattr(self, "_update_download_worker", None)
        if worker is not None and not worker.isRunning():
            try:
                worker.wait(200)
            except Exception:
                pass
        self._update_download_worker = None
        if not ok:
            self._log(f"[UPDATE] {path_or_err}")
            self._set_status(f"Update failed: {path_or_err}", "error")
            if hasattr(self, "update_banner_install_btn"):
                self.update_banner_install_btn.setEnabled(True)
                self.update_banner_install_btn.setText("Download & install")
            return
        # Download complete — confirm self-replace + relaunch.
        if not ask_premium_confirmation(
            self,
            title="Install the downloaded update?",
            body="StreamKeep will close, swap in the new version, and relaunch itself automatically.",
            eyebrow="UPDATER",
            badge_text="Restart required",
            tone="warning",
            summary_title="Any active download or recording will be interrupted.",
            summary_body="Install now when you are ready for the app to restart itself.",
            details_title="What happens next",
            details_body=(
                "1. StreamKeep closes.\n"
                "2. The downloaded build replaces the current executable.\n"
                "3. StreamKeep relaunches itself."
            ),
            primary_label="Install and relaunch",
            secondary_label="Not now",
            default_action="secondary",
            min_width=620,
        ):
            self._log("[UPDATE] User cancelled the install step.")
            if hasattr(self, "update_banner_install_btn"):
                self.update_banner_install_btn.setEnabled(True)
                self.update_banner_install_btn.setText("Install now")
            return
        if arm_self_replace(path_or_err):
            self._log("[UPDATE] Armed self-replace, quitting now.")
            from PyQt6.QtWidgets import QApplication
            QApplication.quit()
        else:
            self._set_status("Could not arm the update step. See log.", "error")

    def _on_update_dismiss(self):
        payload = getattr(self, "_latest_update_payload", None) or {}
        self._config["dismissed_update_tag"] = payload.get("tag", "")
        self._persist_config()
        self.update_banner.setVisible(False)


def build_settings_tab(win):
    """Build the Settings tab page. Stashes widget refs on `win.*`."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(14)
    current_theme = str(win._config.get("theme", "dark") or "dark")
    theme_display = {"dark": "Dark", "light": "Light", "system": "System"}.get(
        current_theme, "Dark"
    )

    # ── Hero ────────────────────────────────────────────────────────
    hero = QFrame()
    hero.setObjectName("heroCard")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(18, 18, 18, 18)
    hero_lay.setSpacing(14)

    hero_copy = QVBoxLayout()
    hero_copy.setSpacing(4)
    kicker = QLabel("Settings")
    kicker.setObjectName("eyebrow")
    title = QLabel("Tune storage, authenticated access, and tooling")
    title.setObjectName("heroTitle")
    title.setWordWrap(True)
    body = QLabel(
        "Set default output behavior, attach browser cookies for gated "
        "content, and verify the local toolchain that powers downloads."
    )
    body.setObjectName("heroBody")
    body.setWordWrap(True)
    hero_copy.addWidget(kicker)
    hero_copy.addWidget(title)
    hero_copy.addWidget(body)
    hero_lay.addLayout(hero_copy)

    settings_meta = QLabel(
        f"StreamKeep v{VERSION}\n"
        f"Config file: {CONFIG_FILE}\n"
        f"Supported platforms: {', '.join(Extractor.all_names())}"
    )
    settings_meta.setObjectName("sectionBody")
    settings_meta.setWordWrap(True)
    hero_lay.addWidget(settings_meta)

    settings_metrics = QHBoxLayout()
    settings_metrics.setSpacing(12)
    theme_card, win.settings_theme_value, win.settings_theme_sub = make_metric_card(
        "Appearance", theme_display, "Catppuccin-based desktop theme"
    )
    config_card, _, _ = make_metric_card(
        "Config",
        CONFIG_FILE.name,
        str(CONFIG_FILE.parent),
    )
    secrets_card, _, _ = make_metric_card(
        "Secrets",
        "Protected",
        "Tokens stay local with Windows DPAPI",
    )
    settings_metrics.addWidget(theme_card)
    settings_metrics.addWidget(config_card, 1)
    settings_metrics.addWidget(secrets_card)
    hero_lay.addLayout(settings_metrics)
    lay.addWidget(hero)

    # ── Card body ───────────────────────────────────────────────────
    card = QFrame()
    card.setObjectName("card")
    card_lay = QVBoxLayout(card)
    card_lay.setContentsMargins(18, 18, 18, 18)
    card_lay.setSpacing(14)

    # Theme selector (F20)
    theme_bar = QFrame()
    theme_bar.setObjectName("toolbar")
    theme_row = QHBoxLayout(theme_bar)
    theme_row.setContentsMargins(14, 12, 14, 12)
    theme_row.setSpacing(10)
    theme_copy = QVBoxLayout()
    theme_copy.setSpacing(2)
    theme_title = QLabel("Appearance")
    theme_title.setObjectName("fieldLabel")
    theme_hint = QLabel("Choose the desktop theme StreamKeep should apply instantly.")
    theme_hint.setObjectName("subtleText")
    theme_hint.setWordWrap(True)
    theme_copy.addWidget(theme_title)
    theme_copy.addWidget(theme_hint)
    theme_row.addLayout(theme_copy, 1)
    win.theme_combo = QComboBox()
    win.theme_combo.addItem("Dark (Catppuccin Mocha)", "dark")
    win.theme_combo.addItem("Light (Catppuccin Latte)", "light")
    win.theme_combo.addItem("System", "system")
    idx = max(0, win.theme_combo.findData(current_theme))
    win.theme_combo.setCurrentIndex(idx)
    win.theme_combo.currentIndexChanged.connect(win._on_theme_changed)
    win.theme_combo.setMinimumWidth(210)
    theme_row.addWidget(win.theme_combo)
    language_label = QLabel("Language")
    language_label.setObjectName("fieldLabel")
    theme_row.addWidget(language_label)
    win.language_combo = QComboBox()
    language_labels = {"en": "English", "es": "Spanish"}
    for lang in available_languages():
        win.language_combo.addItem(language_labels.get(lang, lang), lang)
    lang_idx = max(0, win.language_combo.findData(win._config.get("language", "en")))
    win.language_combo.setCurrentIndex(lang_idx)
    win.language_combo.currentIndexChanged.connect(win._on_language_changed)
    win.language_combo.setMinimumWidth(150)
    theme_row.addWidget(win.language_combo)
    card_lay.addWidget(theme_bar)

    # Default Output + Toolchain (side by side)
    sections_top = QHBoxLayout()
    sections_top.setSpacing(12)

    general_block, general_lay = make_field_block(
        "Default Output", "New downloads will default to this folder."
    )
    output_row = QHBoxLayout()
    output_row.setSpacing(8)
    win.settings_output = QLineEdit(str(_default_output_dir()))
    win.settings_output.setClearButtonEnabled(True)
    output_row.addWidget(win.settings_output, 1)
    browse = QPushButton("Browse")
    browse.setObjectName("secondary")
    browse.clicked.connect(lambda: win._settings_browse(win.settings_output))
    output_row.addWidget(browse)
    general_lay.addLayout(output_row)
    sections_top.addWidget(general_block, 1)

    tools_block, tools_lay = make_field_block(
        "Local Toolchain",
        "StreamKeep relies on these binaries for robust downloads.",
    )
    registry = get_runtime_capabilities(refresh=True)
    ffmpeg = registry["ffmpeg"]
    curl = registry["curl"]
    pillow = registry["pillow"]
    yt_status = ytdlp_runtime_status()
    ff_card, _, _ = make_metric_card(
        "FFmpeg",
        ffmpeg["state"].title(),
        f"{ffmpeg.get('version') or 'not found'} · {ffmpeg['provenance']}"[:96],
    )
    yt_card, _, _ = make_metric_card(
        "yt-dlp",
        yt_status.get("summary", "Missing"),
        yt_status.get("detail", "")[:96],
    )
    curl_card, _, _ = make_metric_card(
        "curl",
        curl["state"].title(),
        f"{curl.get('version') or 'not found'} · {curl['provenance']}"[:96],
    )
    pillow_card, _, _ = make_metric_card(
        "Pillow",
        pillow["state"].title(),
        f"{pillow.get('version') or 'not found'} · {pillow['provenance']}"[:96],
    )
    tools_metrics = QVBoxLayout()
    tools_metrics.setSpacing(10)
    tools_row_primary = QHBoxLayout()
    tools_row_primary.setSpacing(10)
    tools_row_primary.addWidget(ff_card)
    tools_row_primary.addWidget(yt_card)
    tools_row_secondary = QHBoxLayout()
    tools_row_secondary.setSpacing(10)
    tools_row_secondary.addWidget(curl_card)
    tools_row_secondary.addWidget(pillow_card)
    tools_metrics.addLayout(tools_row_primary)
    tools_metrics.addLayout(tools_row_secondary)
    tools_lay.addLayout(tools_metrics)
    sections_top.addWidget(tools_block, 1)
    card_lay.addLayout(sections_top)

    # ── Cookies ─────────────────────────────────────────────────────
    cookies_block, cookies_lay = make_field_block(
        "Browser Cookies",
        "Use browser cookies or a cookies.txt file for age-restricted or "
        "authenticated content.",
    )

    row_cookies = QHBoxLayout()
    row_cookies.setSpacing(8)
    win.cookies_combo = QComboBox()
    win.cookies_combo.addItem("None")
    row_cookies.addWidget(win.cookies_combo, 1)
    scan_btn = QPushButton("Scan for Browsers")
    scan_btn.setObjectName("secondary")
    scan_btn.clicked.connect(win._on_scan_browsers)
    row_cookies.addWidget(scan_btn)
    cookies_lay.addLayout(row_cookies)

    row_cookiefile = QHBoxLayout()
    row_cookiefile.setSpacing(8)
    win.cookies_file_input = QLineEdit()
    win.cookies_file_input.setPlaceholderText("Path to cookies.txt (Netscape format)")
    win.cookies_file_input.setClearButtonEnabled(True)
    row_cookiefile.addWidget(win.cookies_file_input, 1)
    browse_cookies = QPushButton("Browse")
    browse_cookies.setObjectName("secondary")
    browse_cookies.clicked.connect(win._on_browse_cookies_file)
    row_cookiefile.addWidget(browse_cookies)
    cookies_lay.addLayout(row_cookiefile)

    # Import cookies to cookies.txt (F47)
    row_import = QHBoxLayout()
    row_import.setSpacing(8)
    win.cookies_import_btn = QPushButton("Import Cookies from Browser")
    win.cookies_import_btn.setObjectName("secondary")
    win.cookies_import_btn.setToolTip(
        "Extract cookies from the selected browser and save as cookies.txt "
        "for authenticated downloads (F47)"
    )
    win.cookies_import_btn.clicked.connect(win._on_import_browser_cookies)
    row_import.addWidget(win.cookies_import_btn)
    win.cookies_clear_btn = QPushButton("Clear")
    win.cookies_clear_btn.setObjectName("secondary")
    win.cookies_clear_btn.setFixedWidth(70)
    win.cookies_clear_btn.clicked.connect(win._on_clear_cookies)
    row_import.addWidget(win.cookies_clear_btn)
    cookies_lay.addLayout(row_import)

    win.cookies_scan_label = QLabel("")
    win.cookies_scan_label.setObjectName("subtleText")
    win.cookies_scan_label.setWordWrap(True)
    cookies_lay.addWidget(win.cookies_scan_label)

    # Show cookies.txt status
    win.cookies_status_label = QLabel("")
    win.cookies_status_label.setObjectName("subtleText")
    cookies_lay.addWidget(win.cookies_status_label)
    card_lay.addWidget(cookies_block)

    saved_browser = win._config.get("cookies_browser", "")
    saved_file = win._config.get("cookies_file", "")
    if saved_file:
        win.cookies_file_input.setText(saved_file)
        YtDlpExtractor.cookies_file = saved_file
    win._scan_browsers_silent()
    if saved_browser:
        idx = win.cookies_combo.findText(saved_browser)
        if idx >= 0:
            win.cookies_combo.setCurrentIndex(idx)
        YtDlpExtractor.cookies_browser = saved_browser
    # Update cookies.txt status indicator (F47)
    if hasattr(win, "_update_cookies_status"):
        win._update_cookies_status()

    # ── Platform Accounts (F48) ───────────────────────────────────
    accounts_block, accounts_lay = make_field_block(
        "Platform Accounts",
        "Store API tokens for authenticated platform access. "
        "Tokens are encrypted with Windows DPAPI.",
    )
    from streamkeep.accounts import PLATFORMS as _ACCT_PLATFORMS, credential_status
    win._account_inputs = {}
    for plat_key, plat_info in _ACCT_PLATFORMS.items():
        arow = QHBoxLayout()
        arow.setSpacing(8)
        alabel = QLabel(f"{plat_info['label']}:")
        alabel.setFixedWidth(100)
        arow.addWidget(alabel)
        ainput = QLineEdit()
        ainput.setPlaceholderText(plat_info["hint"])
        ainput.setEchoMode(QLineEdit.EchoMode.Password)
        arow.addWidget(ainput, 1)
        status = credential_status(plat_key)
        astatus = QLabel(status)
        astatus.setFixedWidth(100)
        astatus.setObjectName("subtleText")
        arow.addWidget(astatus)
        accounts_lay.addLayout(arow)
        win._account_inputs[plat_key] = (ainput, astatus)

    acct_btn_row = QHBoxLayout()
    acct_btn_row.setSpacing(8)
    win.acct_save_btn = QPushButton("Save Tokens")
    win.acct_save_btn.setObjectName("secondary")
    win.acct_save_btn.clicked.connect(win._on_save_account_tokens)
    acct_btn_row.addWidget(win.acct_save_btn)
    win.acct_clear_btn = QPushButton("Clear All")
    win.acct_clear_btn.setObjectName("secondary")
    win.acct_clear_btn.setFixedWidth(80)
    win.acct_clear_btn.clicked.connect(win._on_clear_account_tokens)
    acct_btn_row.addWidget(win.acct_clear_btn)
    acct_btn_row.addStretch(1)
    accounts_lay.addLayout(acct_btn_row)
    card_lay.addWidget(accounts_block)

    # ── Network ────────────────────────────────────────────────────
    network_block, network_lay = make_field_block(
        "Network",
        "Optional bandwidth throttling and proxy for geo-blocked content.",
    )
    rate_row = QHBoxLayout()
    rate_row.setSpacing(8)
    rate_label = QLabel("Rate limit:")
    rate_label.setFixedWidth(100)
    rate_row.addWidget(rate_label)
    win.rate_limit_input = QLineEdit()
    win.rate_limit_input.setPlaceholderText("e.g. 500K or 2M (leave blank for unlimited)")
    win.rate_limit_input.setClearButtonEnabled(True)
    rate_row.addWidget(win.rate_limit_input, 1)
    network_lay.addLayout(rate_row)

    proxy_row = QHBoxLayout()
    proxy_row.setSpacing(8)
    proxy_label = QLabel("Proxy URL:")
    proxy_label.setFixedWidth(100)
    proxy_row.addWidget(proxy_label)
    win.proxy_input = QLineEdit()
    win.proxy_input.setPlaceholderText("e.g. socks5://127.0.0.1:1080 or http://proxy:8080")
    win.proxy_input.setClearButtonEnabled(True)
    proxy_row.addWidget(win.proxy_input, 1)
    network_lay.addLayout(proxy_row)

    # Proxy pool (F49)
    proxy_pool_hint = QLabel(
        "Proxy pool: assign proxies to specific platforms. "
        "Format per line: url|platform1,platform2|label (platforms optional)."
    )
    proxy_pool_hint.setObjectName("subtleText")
    proxy_pool_hint.setWordWrap(True)
    network_lay.addWidget(proxy_pool_hint)
    win.proxy_pool_edit = QPlainTextEdit()
    win.proxy_pool_edit.setMaximumHeight(100)
    win.proxy_pool_edit.setPlaceholderText(
        "socks5://us.proxy:1080|twitch,kick|US proxy\n"
        "http://de.proxy:8080|youtube|DE proxy\n"
        "http://fallback:3128||Global fallback"
    )
    network_lay.addWidget(win.proxy_pool_edit)
    # Load saved pool
    _saved_pool = win._config.get("proxy_pool", [])
    if isinstance(_saved_pool, list) and _saved_pool:
        lines = []
        for pe in _saved_pool:
            if isinstance(pe, dict) and pe.get("url"):
                plats = ",".join(pe.get("platforms", []))
                label = pe.get("label", "")
                lines.append(f"{pe['url']}|{plats}|{label}")
        win.proxy_pool_edit.setPlainText("\n".join(lines))
        from streamkeep.proxy import set_pool
        set_pool(_saved_pool)

    proxy_test_btn = QPushButton("Test Proxies")
    proxy_test_btn.setObjectName("secondary")
    proxy_test_btn.setFixedWidth(110)
    proxy_test_btn.clicked.connect(win._on_test_proxies)
    network_lay.addWidget(proxy_test_btn)

    # Bandwidth schedule
    win.bw_enable_check = QCheckBox(
        "Enable bandwidth schedule (overrides Rate limit within the window)"
    )
    win.bw_enable_check.setChecked(win._bandwidth_rule["enabled"])
    network_lay.addWidget(win.bw_enable_check)
    bw_row = QHBoxLayout()
    bw_row.setSpacing(8)
    bw_row.addWidget(QLabel("Window:"))
    win.bw_start_spin = QSpinBox()
    win.bw_start_spin.setRange(0, 23)
    win.bw_start_spin.setSuffix(":00")
    win.bw_start_spin.setValue(win._bandwidth_rule["start_hour"])
    bw_row.addWidget(win.bw_start_spin)
    bw_row.addWidget(QLabel("to"))
    win.bw_end_spin = QSpinBox()
    win.bw_end_spin.setRange(0, 23)
    win.bw_end_spin.setSuffix(":00")
    win.bw_end_spin.setValue(win._bandwidth_rule["end_hour"])
    bw_row.addWidget(win.bw_end_spin)
    bw_row.addSpacing(12)
    bw_row.addWidget(QLabel("Limit:"))
    win.bw_limit_input = QLineEdit(win._bandwidth_rule["limit"])
    win.bw_limit_input.setPlaceholderText("500K")
    win.bw_limit_input.setClearButtonEnabled(True)
    win.bw_limit_input.setFixedWidth(100)
    bw_row.addWidget(win.bw_limit_input)
    bw_row.addStretch(1)
    network_lay.addLayout(bw_row)

    # Speed schedule (F51) — day/night/weekend tiers
    sched_hint = QLabel(
        "Speed schedule: set different bandwidth limits for day, night, "
        "and weekends. Applied to new downloads only."
    )
    sched_hint.setObjectName("subtleText")
    sched_hint.setWordWrap(True)
    network_lay.addWidget(sched_hint)
    win.sched_enable_check = QCheckBox("Enable speed schedule")
    network_lay.addWidget(win.sched_enable_check)
    sched_row = QHBoxLayout()
    sched_row.setSpacing(8)
    sched_row.addWidget(QLabel("Day:"))
    win.sched_day_start = QSpinBox()
    win.sched_day_start.setRange(0, 23)
    win.sched_day_start.setSuffix(":00")
    win.sched_day_start.setValue(8)
    sched_row.addWidget(win.sched_day_start)
    sched_row.addWidget(QLabel("-"))
    win.sched_day_end = QSpinBox()
    win.sched_day_end.setRange(0, 23)
    win.sched_day_end.setSuffix(":00")
    win.sched_day_end.setValue(23)
    sched_row.addWidget(win.sched_day_end)
    sched_row.addWidget(QLabel("Limit:"))
    win.sched_day_limit = QLineEdit("2M")
    win.sched_day_limit.setFixedWidth(80)
    win.sched_day_limit.setPlaceholderText("2M")
    win.sched_day_limit.setClearButtonEnabled(True)
    sched_row.addWidget(win.sched_day_limit)
    network_lay.addLayout(sched_row)

    sched_row2 = QHBoxLayout()
    sched_row2.setSpacing(8)
    sched_row2.addWidget(QLabel("Night limit:"))
    win.sched_night_limit = QLineEdit("")
    win.sched_night_limit.setFixedWidth(80)
    win.sched_night_limit.setPlaceholderText("(unlimited)")
    win.sched_night_limit.setClearButtonEnabled(True)
    sched_row2.addWidget(win.sched_night_limit)
    sched_row2.addSpacing(12)
    sched_row2.addWidget(QLabel("Weekend limit:"))
    win.sched_weekend_limit = QLineEdit("")
    win.sched_weekend_limit.setFixedWidth(80)
    win.sched_weekend_limit.setPlaceholderText("(unlimited)")
    win.sched_weekend_limit.setClearButtonEnabled(True)
    sched_row2.addWidget(win.sched_weekend_limit)
    sched_row2.addStretch(1)
    network_lay.addLayout(sched_row2)

    # Restore saved speed schedule
    _saved_sched = win._config.get("speed_schedule", {})
    if isinstance(_saved_sched, dict):
        win.sched_enable_check.setChecked(bool(_saved_sched.get("enabled", False)))
        win.sched_day_start.setValue(int(_saved_sched.get("day_start", 8) or 8))
        win.sched_day_end.setValue(int(_saved_sched.get("day_end", 23) or 23))
        win.sched_day_limit.setText(str(_saved_sched.get("day_limit", "2M") or ""))
        win.sched_night_limit.setText(str(_saved_sched.get("night_limit", "") or ""))
        win.sched_weekend_limit.setText(str(_saved_sched.get("weekend_limit", "") or ""))
        from streamkeep.scheduler import configure as _sched_configure
        _sched_configure(_saved_sched, win._config.get("rate_limit", ""))

    # Parallel connections per direct MP4
    par_row = QHBoxLayout()
    par_row.setSpacing(8)
    par_label = QLabel("Parallel connections:")
    par_label.setFixedWidth(140)
    par_row.addWidget(par_label)
    win.parallel_spin = QSpinBox()
    win.parallel_spin.setRange(1, 16)
    win.parallel_spin.setValue(win._parallel_connections)
    win.parallel_spin.setToolTip(
        "Multi-connection HTTP Range splitting for direct MP4 files.\n"
        "Higher values can be 3-5x faster on CDN-hosted content.\n"
        "Set to 1 to disable and always use ffmpeg."
    )
    par_row.addWidget(win.parallel_spin)
    par_hint = QLabel("per direct MP4 (1 = off, default 4)")
    par_hint.setStyleSheet(f"color: {CAT['subtext0']}; font-size: 11px;")
    par_row.addWidget(par_hint)
    par_row.addStretch(1)
    network_lay.addLayout(par_row)

    # Parallel auto-records (v4.15.0)
    par_ar_row = QHBoxLayout()
    par_ar_row.setSpacing(8)
    par_ar_label = QLabel("Parallel auto-records:")
    par_ar_label.setFixedWidth(140)
    par_ar_row.addWidget(par_ar_label)
    win.parallel_autorecords_spin = QSpinBox()
    win.parallel_autorecords_spin.setRange(1, 4)
    win.parallel_autorecords_spin.setValue(int(win._parallel_autorecords or 2))
    win.parallel_autorecords_spin.setToolTip(
        "Maximum simultaneous auto-recordings when multiple monitored "
        "channels go live at the same time. Each recording uses its "
        "own ffmpeg process."
    )
    par_ar_row.addWidget(win.parallel_autorecords_spin)
    par_ar_hint = QLabel("channels captured at once (default 2)")
    par_ar_hint.setStyleSheet(f"color: {CAT['subtext0']}; font-size: 11px;")
    par_ar_row.addWidget(par_ar_hint)
    par_ar_row.addStretch(1)
    network_lay.addLayout(par_ar_row)

    # Concurrent queue downloads (v4.19.0 — F1)
    cq_row = QHBoxLayout()
    cq_row.setSpacing(8)
    cq_label = QLabel("Concurrent queue jobs:")
    cq_label.setFixedWidth(140)
    cq_row.addWidget(cq_label)
    win.concurrent_queue_spin = QSpinBox()
    win.concurrent_queue_spin.setRange(1, 8)
    win.concurrent_queue_spin.setValue(int(getattr(win, "_max_concurrent_downloads", 3)))
    win.concurrent_queue_spin.setToolTip(
        "Maximum queued downloads that run at the same time.\n"
        "Bandwidth is shared evenly across active jobs when a\n"
        "rate limit is set."
    )
    cq_row.addWidget(win.concurrent_queue_spin)
    cq_hint = QLabel("simultaneous queue downloads (default 3)")
    cq_hint.setStyleSheet(f"color: {CAT['subtext0']}; font-size: 11px;")
    cq_row.addWidget(cq_hint)
    cq_row.addStretch(1)
    network_lay.addLayout(cq_row)

    # Chunked live recording (v4.15.0)
    chunk_row = QHBoxLayout()
    chunk_row.setSpacing(8)
    win.chunk_check = QCheckBox("Split long live captures into chunks")
    win.chunk_check.setChecked(bool(win._chunk_long_captures))
    win.chunk_check.setToolTip(
        "When enabled, live captures are written as sequential _part001.mp4, "
        "_part002.mp4, ... files of the configured chunk length. Only applies "
        "to live recordings (not VODs)."
    )
    chunk_row.addWidget(win.chunk_check)
    win.chunk_length_spin = QSpinBox()
    win.chunk_length_spin.setRange(600, 21600)       # 10 min .. 6 h
    win.chunk_length_spin.setSingleStep(600)
    win.chunk_length_spin.setSuffix(" sec")
    win.chunk_length_spin.setValue(int(win._chunk_length_secs or 7200))
    win.chunk_length_spin.setEnabled(bool(win._chunk_long_captures))
    win.chunk_check.toggled.connect(win.chunk_length_spin.setEnabled)
    chunk_row.addWidget(win.chunk_length_spin)
    chunk_hint = QLabel("per chunk (default 2 hours)")
    chunk_hint.setStyleSheet(f"color: {CAT['subtext0']}; font-size: 11px;")
    chunk_row.addWidget(chunk_hint)
    chunk_row.addStretch(1)
    network_lay.addLayout(chunk_row)

    # Per-platform default quality (v4.17.0)
    quality_hdr = QLabel("Default quality per platform")
    quality_hdr.setObjectName("sectionTitle")
    network_lay.addWidget(quality_hdr)
    win.quality_defaults_combos = {}
    quality_opts = [
        ("", "Highest available (default)"),
        ("source", "Source / best"),
        ("1080p", "1080p"),
        ("720p", "720p"),
        ("480p", "480p"),
        ("360p", "360p"),
        ("lowest", "Lowest available"),
    ]
    saved_q = dict(win._config.get("quality_defaults") or {})
    for platform in ("twitch", "kick", "rumble", "youtube", "other"):
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(platform.capitalize() + ":")
        lbl.setFixedWidth(90)
        row.addWidget(lbl)
        combo = QComboBox()
        for val, text in quality_opts:
            combo.addItem(text, userData=val)
        current = saved_q.get(platform, "")
        idx = max(0, combo.findData(current))
        combo.setCurrentIndex(idx)
        row.addWidget(combo)
        row.addStretch(1)
        network_lay.addLayout(row)
        win.quality_defaults_combos[platform] = combo

    # Whisper transcription model (v4.17.0)
    whisper_row = QHBoxLayout()
    whisper_row.setSpacing(8)
    whisper_row.addWidget(QLabel("Whisper model:"))
    win.whisper_model_combo = QComboBox()
    for m in ("tiny", "base", "small", "medium", "large-v3"):
        win.whisper_model_combo.addItem(m, userData=m)
    wm = str(win._config.get("whisper_model", "tiny") or "tiny")
    idx = max(0, win.whisper_model_combo.findData(wm))
    win.whisper_model_combo.setCurrentIndex(idx)
    win.whisper_model_combo.setToolTip(
        "tiny/base are fast on CPU; medium/large-v3 need GPU for sane speeds."
    )
    whisper_row.addWidget(win.whisper_model_combo)
    whisper_row.addWidget(QLabel("(used by Transcribe... in History)"))
    whisper_row.addStretch(1)
    network_lay.addLayout(whisper_row)

    # Speaker diarization (F29)
    diarize_row = QHBoxLayout()
    diarize_row.setSpacing(8)
    win.diarize_check = QCheckBox("Enable speaker diarization")
    win.diarize_check.setChecked(bool(win._config.get("enable_diarization", False)))
    win.diarize_check.setToolTip(
        "Requires WhisperX + pyannote-audio + a HuggingFace token. "
        "Labels transcript segments with speaker IDs (Speaker 1, Speaker 2, ...)."
    )
    diarize_row.addWidget(win.diarize_check)
    diarize_row.addWidget(QLabel("HF token:"))
    win.hf_token_input = QLineEdit()
    win.hf_token_input.setPlaceholderText("hf_...")
    win.hf_token_input.setEchoMode(QLineEdit.EchoMode.Password)
    win.hf_token_input.setText(str(win._config.get("hf_token", "") or ""))
    win.hf_token_input.setMaximumWidth(260)
    win.hf_token_input.setToolTip(
        "Free HuggingFace token for pyannote speaker diarization models."
    )
    diarize_row.addWidget(win.hf_token_input)
    diarize_row.addStretch(1)
    network_lay.addLayout(diarize_row)

    # Chat Render settings (F22)
    cr_header = QLabel("<b>Chat Render</b>")
    network_lay.addWidget(cr_header)
    cr_row1 = QHBoxLayout()
    cr_row1.setSpacing(8)
    cr_row1.addWidget(QLabel("Width:"))
    win.chat_render_width_spin = QSpinBox()
    win.chat_render_width_spin.setRange(200, 1920)
    win.chat_render_width_spin.setValue(int(win._config.get("chat_render_width", 400) or 400))
    cr_row1.addWidget(win.chat_render_width_spin)
    cr_row1.addWidget(QLabel("Height:"))
    win.chat_render_height_spin = QSpinBox()
    win.chat_render_height_spin.setRange(200, 1080)
    win.chat_render_height_spin.setValue(int(win._config.get("chat_render_height", 600) or 600))
    cr_row1.addWidget(win.chat_render_height_spin)
    cr_row1.addWidget(QLabel("Font size:"))
    win.chat_render_font_spin = QSpinBox()
    win.chat_render_font_spin.setRange(8, 36)
    win.chat_render_font_spin.setValue(int(win._config.get("chat_render_font_size", 14) or 14))
    cr_row1.addWidget(win.chat_render_font_spin)
    cr_row1.addStretch(1)
    network_lay.addLayout(cr_row1)
    cr_row2 = QHBoxLayout()
    cr_row2.setSpacing(8)
    cr_row2.addWidget(QLabel("Message duration (s):"))
    win.chat_render_duration_spin = QSpinBox()
    win.chat_render_duration_spin.setRange(2, 30)
    win.chat_render_duration_spin.setValue(int(win._config.get("chat_render_msg_duration", 8) or 8))
    cr_row2.addWidget(win.chat_render_duration_spin)
    cr_row2.addWidget(QLabel("BG opacity:"))
    win.chat_render_opacity_spin = QSpinBox()
    win.chat_render_opacity_spin.setRange(0, 255)
    win.chat_render_opacity_spin.setValue(int(win._config.get("chat_render_bg_opacity", 180) or 180))
    cr_row2.addWidget(win.chat_render_opacity_spin)
    cr_row2.addStretch(1)
    network_lay.addLayout(cr_row2)

    # Live chat capture (v4.16.0)
    chat_row = QHBoxLayout()
    chat_row.setSpacing(8)
    win.capture_chat_check = QCheckBox("Capture Twitch chat during live recordings")
    win.capture_chat_check.setChecked(bool(win._config.get("capture_live_chat", False)))
    win.capture_chat_check.setToolTip(
        "Attach an anonymous Twitch IRC reader to every auto-recorded "
        "Twitch stream. Writes chat.jsonl sidecar in the output folder. "
        "Kick and other platforms are not supported yet."
    )
    chat_row.addWidget(win.capture_chat_check)
    win.render_chat_ass_check = QCheckBox(".ass sidecar for replay sync")
    win.render_chat_ass_check.setChecked(bool(win._config.get("render_chat_ass", True)))
    win.render_chat_ass_check.setToolTip(
        "In addition to chat.jsonl, write chat.ass next to the video. "
        "VLC/mpv will pick it up automatically on replay."
    )
    chat_row.addWidget(win.render_chat_ass_check)
    chat_row.addStretch(1)
    network_lay.addLayout(chat_row)

    # Browser companion local server (v4.16.0)
    companion_panel, companion_panel_lay = make_dialog_section(
        "Browser Companion",
        "Send URLs from the extension with one click, or open the lightweight web remote for queue and status checks. Keep LAN access off unless another device truly needs it.",
    )
    comp_row = QHBoxLayout()
    comp_row.setSpacing(8)
    win.companion_check = QCheckBox("Enable browser-extension companion (local server)")
    win.companion_check.setChecked(bool(win._config.get("companion_server_enabled", False)))
    win.companion_check.setToolTip(
        "Starts a 127.0.0.1-only HTTP server on a random port so the "
        "StreamKeep browser extension can send URLs with one click. The "
        "server requires a bearer token shown below, regenerated each "
        "app launch."
    )
    win.companion_check.toggled.connect(win._on_companion_toggled)
    comp_row.addWidget(win.companion_check)
    win.companion_lan_check = QCheckBox("Allow LAN access")
    win.companion_lan_check.setChecked(bool(win._config.get("companion_bind_lan", False)))
    win.companion_lan_check.setToolTip(
        "Bind to 0.0.0.0 instead of 127.0.0.1 — allows access from other "
        "devices on your network. The Web Remote UI is available at / in a browser."
    )
    win.companion_lan_check.toggled.connect(win._on_companion_scope_toggled)
    comp_row.addWidget(win.companion_lan_check)
    comp_row.addStretch(1)
    companion_panel_lay.addLayout(comp_row)

    win.companion_status_banner, win.companion_status_title, win.companion_status_body = (
        make_status_banner()
    )
    companion_panel_lay.addWidget(win.companion_status_banner)

    companion_metrics = QHBoxLayout()
    companion_metrics.setSpacing(10)
    scope_card, win.companion_scope_value, win.companion_scope_sub = make_metric_card(
        "Access scope",
        "Local only",
        "Recommended default",
    )
    remote_card, win.companion_remote_value, win.companion_remote_sub = make_metric_card(
        "Web remote",
        "Off",
        "Enable the companion to expose a local control page",
    )
    token_card, win.companion_token_value, win.companion_token_sub = make_metric_card(
        "Pairing token",
        "Waiting",
        "Generated fresh on each launch",
    )
    companion_metrics.addWidget(scope_card)
    companion_metrics.addWidget(remote_card)
    companion_metrics.addWidget(token_card)
    companion_panel_lay.addLayout(companion_metrics)

    endpoint_row = QHBoxLayout()
    endpoint_row.setSpacing(8)
    endpoint_row.addWidget(QLabel("Web remote:"))
    win.companion_url_display = QLineEdit("")
    win.companion_url_display.setReadOnly(True)
    win.companion_url_display.setPlaceholderText("Enable the companion server to expose a local URL")
    win.companion_url_display.setToolTip(
        "Open this on the same PC for a lightweight queue and status view."
    )
    endpoint_row.addWidget(win.companion_url_display, 1)
    win.companion_copy_url_btn = QPushButton("Copy")
    win.companion_copy_url_btn.setObjectName("secondary")
    win.companion_copy_url_btn.setFixedWidth(74)
    win.companion_copy_url_btn.clicked.connect(win._on_copy_companion_url)
    endpoint_row.addWidget(win.companion_copy_url_btn)
    win.companion_open_url_btn = QPushButton("Open")
    win.companion_open_url_btn.setObjectName("secondary")
    win.companion_open_url_btn.setFixedWidth(82)
    win.companion_open_url_btn.clicked.connect(win._on_open_companion_remote)
    endpoint_row.addWidget(win.companion_open_url_btn)
    companion_panel_lay.addLayout(endpoint_row)

    comp_token_row = QHBoxLayout()
    comp_token_row.setSpacing(8)
    comp_token_row.addWidget(QLabel("Pairing token:"))
    win.companion_token_display = QLineEdit("")
    win.companion_token_display.setReadOnly(True)
    win.companion_token_display.setPlaceholderText("Enable server to generate a token")
    win.companion_token_display.setToolTip(
        "Paste this into the StreamKeep browser extension's popup. "
        "Regenerated on each launch — never stored on disk."
    )
    comp_token_row.addWidget(win.companion_token_display, 1)
    win.companion_copy_token_btn = QPushButton("Copy token")
    win.companion_copy_token_btn.setObjectName("secondary")
    win.companion_copy_token_btn.setFixedWidth(108)
    win.companion_copy_token_btn.clicked.connect(win._on_copy_companion_token)
    comp_token_row.addWidget(win.companion_copy_token_btn)
    win.companion_rotate_token_btn = QPushButton("Rotate")
    win.companion_rotate_token_btn.setObjectName("secondary")
    win.companion_rotate_token_btn.setFixedWidth(82)
    win.companion_rotate_token_btn.setToolTip(
        "Generate a new token immediately. The old token stops working — "
        "re-pair the browser extension with the new one."
    )
    win.companion_rotate_token_btn.clicked.connect(win._on_rotate_companion_token)
    comp_token_row.addWidget(win.companion_rotate_token_btn)
    companion_panel_lay.addLayout(comp_token_row)

    companion_hint = QLabel(
        "The token rotates every launch and can be rotated on demand. It is never stored on disk. If LAN access is on, share it only with devices you trust."
    )
    companion_hint.setObjectName("subtleText")
    companion_hint.setWordWrap(True)
    companion_panel_lay.addWidget(companion_hint)
    network_lay.addWidget(companion_panel)

    # Notifications sound cue (v4.17.0)
    notif_row = QHBoxLayout()
    notif_row.setSpacing(8)
    win.notif_sound_check = QCheckBox("Audible beep on notification events")
    win.notif_sound_check.setChecked(bool(win._config.get("notif_sound", False)))
    win.notif_sound_check.setToolTip(
        "Play the system beep when a notable event fires (live detected, "
        "download complete, error). The Notifications bell in the header "
        "always updates regardless of this setting."
    )
    notif_row.addWidget(win.notif_sound_check)
    notif_row.addStretch(1)
    network_lay.addLayout(notif_row)

    # Auto-update checker (v4.16.0)
    update_row = QHBoxLayout()
    update_row.setSpacing(8)
    win.update_check_check = QCheckBox("Check for updates on startup")
    win.update_check_check.setChecked(bool(win._config.get("check_for_updates", False)))
    win.update_check_check.setToolTip(
        "Once per launch, asks GitHub whether a newer StreamKeep release "
        "is available. The check is opt-in; downloads and installs still "
        "require explicit confirmation."
    )
    update_row.addWidget(win.update_check_check)
    update_row.addStretch(1)
    network_lay.addLayout(update_row)

    # Load saved network settings
    saved_rate = win._config.get("rate_limit", "")
    saved_proxy = win._config.get("proxy", "")
    if saved_rate:
        win.rate_limit_input.setText(saved_rate)
        YtDlpExtractor.rate_limit = saved_rate
    if saved_proxy:
        win.proxy_input.setText(saved_proxy)
        YtDlpExtractor.proxy = saved_proxy
        set_native_proxy(saved_proxy)
    if hasattr(win, "_refresh_companion_ui"):
        win._refresh_companion_ui()

    card_lay.addWidget(network_block)

    # ── YouTube extras ─────────────────────────────────────────────
    yt_block, yt_lay = make_field_block(
        "YouTube Extras", "Optional yt-dlp features for YouTube videos."
    )
    win.subs_check = QCheckBox("Download subtitles (English) and embed in video")
    win.sponsorblock_check = QCheckBox(
        "Skip SponsorBlock segments (sponsor / self-promo / interaction)"
    )
    yt_lay.addWidget(win.subs_check)
    yt_lay.addWidget(win.sponsorblock_check)

    if win._config.get("download_subs"):
        win.subs_check.setChecked(True)
        YtDlpExtractor.download_subs = True
    if win._config.get("sponsorblock"):
        win.sponsorblock_check.setChecked(True)
        YtDlpExtractor.sponsorblock = True

    card_lay.addWidget(yt_block)

    # ── Filename templates ─────────────────────────────────────────
    tpl_block, tpl_lay = make_field_block(
        "Filename Templates",
        "Variables: {title} {channel} {platform} {date} {year} {month} {day}. "
        "Use / to create subfolders. Each segment is sanitized.",
    )
    folder_row = QHBoxLayout()
    folder_row.setSpacing(8)
    folder_label = QLabel("Folder:")
    folder_label.setFixedWidth(100)
    folder_row.addWidget(folder_label)
    win.folder_template_input = QLineEdit(win._folder_template)
    win.folder_template_input.setPlaceholderText(DEFAULT_FOLDER_TEMPLATE)
    folder_row.addWidget(win.folder_template_input, 1)
    tpl_lay.addLayout(folder_row)
    file_row = QHBoxLayout()
    file_row.setSpacing(8)
    file_label = QLabel("Filename:")
    file_label.setFixedWidth(100)
    file_row.addWidget(file_label)
    win.file_template_input = QLineEdit(win._file_template)
    win.file_template_input.setPlaceholderText(DEFAULT_FILE_TEMPLATE)
    file_row.addWidget(win.file_template_input, 1)
    tpl_lay.addLayout(file_row)

    # ── Live preview (F12) ────────────────────────────────────────
    _sample_ctx = {
        "title": "Just Chatting Marathon",
        "channel": "xQc",
        "platform": "twitch",
        "date": "2026-04-12",
        "year": "2026",
        "month": "04",
        "day": "12",
        "id": "v2098765432",
        "quality": "1080p60",
        "ext": "mp4",
    }
    preview_row = QHBoxLayout()
    preview_row.setSpacing(8)
    plabel = QLabel("Preview:")
    plabel.setFixedWidth(100)
    plabel.setStyleSheet(f"color: {CAT['subtext0']};")
    preview_row.addWidget(plabel)
    win._template_preview = QLabel()
    win._template_preview.setStyleSheet(
        f"color: {CAT['green']}; font-family: monospace; font-size: 12px;"
    )
    win._template_preview.setWordWrap(True)
    preview_row.addWidget(win._template_preview, 1)
    tpl_lay.addLayout(preview_row)

    def _update_template_preview():
        folder_tpl = win.folder_template_input.text().strip() or DEFAULT_FOLDER_TEMPLATE
        file_tpl = win.file_template_input.text().strip() or DEFAULT_FILE_TEMPLATE
        try:
            folder_parts = _render_template(folder_tpl, _sample_ctx)
            file_parts = _render_template(file_tpl, _sample_ctx)
            path = "/".join(folder_parts + file_parts) + ".mp4"
            win._template_preview.setText(path)
            win._template_preview.setStyleSheet(
                f"color: {CAT['green']}; font-family: monospace; font-size: 12px;"
            )
        except Exception:
            win._template_preview.setText("Invalid template")
            win._template_preview.setStyleSheet(
                f"color: {CAT['red']}; font-family: monospace; font-size: 12px;"
            )

    win.folder_template_input.textChanged.connect(lambda: _update_template_preview())
    win.file_template_input.textChanged.connect(lambda: _update_template_preview())
    _update_template_preview()

    card_lay.addWidget(tpl_block)

    # ── Webhook ────────────────────────────────────────────────────
    hook_block, hook_lay = make_field_block(
        "Webhook Notifications",
        "POST a JSON payload when downloads complete. Discord webhook URLs "
        "are auto-detected and formatted as embeds.",
    )
    win.webhook_input = QLineEdit(win._webhook_url)
    win.webhook_input.setPlaceholderText(
        "https://discord.com/api/webhooks/... or any POST endpoint"
    )
    hook_lay.addWidget(win.webhook_input)
    win._webhook_type_label = QLabel("")
    win._webhook_type_label.setStyleSheet(f"color: {CAT['subtext0']};")
    hook_lay.addWidget(win._webhook_type_label)
    win.webhook_input.textChanged.connect(
        lambda text: _update_webhook_indicator(win, text))
    _update_webhook_indicator(win, win._webhook_url)
    card_lay.addWidget(hook_block)

    # ── Event Hooks (F24) ─────────────────────────────────────────
    from ...hooks import HOOK_EVENTS
    evt_block, evt_lay = make_field_block(
        "Event Hooks",
        "Run shell commands on lifecycle events. Context is passed as "
        "environment variables ($SK_TITLE, $SK_CHANNEL, $SK_PLATFORM, "
        "$SK_PATH, $SK_URL, $SK_EVENT).",
    )
    hooks_cfg = win._config.get("hooks", {})
    win.hooks_table = QTableWidget(len(HOOK_EVENTS), 2)
    win.hooks_table.setHorizontalHeaderLabels(["Event", "Command"])
    win.hooks_table.horizontalHeader().setStretchLastSection(True)
    win.hooks_table.setColumnWidth(0, 180)
    win.hooks_table.verticalHeader().setVisible(False)
    for i, evt in enumerate(HOOK_EVENTS):
        name_item = QTableWidgetItem(evt)
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        win.hooks_table.setItem(i, 0, name_item)
        win.hooks_table.setItem(i, 1, QTableWidgetItem(hooks_cfg.get(evt, "")))
    win.hooks_table.setFixedHeight(min(210, 30 * (len(HOOK_EVENTS) + 1)))
    evt_lay.addWidget(win.hooks_table)
    card_lay.addWidget(evt_block)

    # ── Duplicate detection ────────────────────────────────────────
    dup_block, dup_lay = make_field_block(
        "Duplicate Detection",
        "Warn before downloading something already in your history.",
    )
    win.dup_check = QCheckBox("Check history for URL and title matches before download")
    win.dup_check.setChecked(win._check_duplicates)
    dup_lay.addWidget(win.dup_check)
    card_lay.addWidget(dup_block)

    # ── Auto-Cleanup Lifecycle Policies (F32) ─────────────────────
    from ...lifecycle import DEFAULT_POLICY
    lc_block, lc_lay = make_field_block(
        "Auto-Cleanup Lifecycle",
        "Automatically recycle old or watched recordings to reclaim disk space. "
        "Always uses the recycle bin — never permanent delete.",
    )
    lc_cfg = win._config.get("lifecycle", dict(DEFAULT_POLICY))
    win.lc_enable_check = QCheckBox("Enable auto-cleanup after each download")
    win.lc_enable_check.setChecked(bool(lc_cfg.get("enabled")))
    lc_lay.addWidget(win.lc_enable_check)

    lc_days_row = QHBoxLayout()
    lc_days_row.setSpacing(8)
    lc_days_row.addWidget(QLabel("Delete recordings older than"))
    win.lc_max_days_spin = QSpinBox()
    win.lc_max_days_spin.setRange(0, 9999)
    win.lc_max_days_spin.setValue(int(lc_cfg.get("max_days", 0) or 0))
    win.lc_max_days_spin.setSpecialValueText("disabled")
    win.lc_max_days_spin.setFixedWidth(80)
    lc_days_row.addWidget(win.lc_max_days_spin)
    lc_days_row.addWidget(QLabel("days"))
    lc_days_row.addStretch(1)
    lc_lay.addLayout(lc_days_row)

    lc_gb_row = QHBoxLayout()
    lc_gb_row.setSpacing(8)
    lc_gb_row.addWidget(QLabel("Max total storage"))
    win.lc_max_gb_spin = QSpinBox()
    win.lc_max_gb_spin.setRange(0, 99999)
    win.lc_max_gb_spin.setValue(int(lc_cfg.get("max_total_gb", 0) or 0))
    win.lc_max_gb_spin.setSpecialValueText("unlimited")
    win.lc_max_gb_spin.setFixedWidth(80)
    lc_gb_row.addWidget(win.lc_max_gb_spin)
    lc_gb_row.addWidget(QLabel("GB (remove oldest first when exceeded)"))
    lc_gb_row.addStretch(1)
    lc_lay.addLayout(lc_gb_row)

    win.lc_watched_check = QCheckBox("Delete watched recordings automatically")
    win.lc_watched_check.setChecked(bool(lc_cfg.get("delete_watched")))
    lc_lay.addWidget(win.lc_watched_check)
    win.lc_fav_exempt_check = QCheckBox("Favorited recordings are exempt from cleanup")
    win.lc_fav_exempt_check.setChecked(bool(lc_cfg.get("favorites_exempt", True)))
    lc_lay.addWidget(win.lc_fav_exempt_check)

    lc_btn_row = QHBoxLayout()
    lc_btn_row.setSpacing(8)
    win.lc_preview_btn = QPushButton("Preview cleanup…")
    win.lc_preview_btn.setObjectName("secondary")
    win.lc_preview_btn.setFixedWidth(150)
    win.lc_preview_btn.clicked.connect(win._on_lifecycle_preview)
    lc_btn_row.addWidget(win.lc_preview_btn)
    lc_btn_row.addStretch(1)
    lc_lay.addLayout(lc_btn_row)
    card_lay.addWidget(lc_block)

    # ── Media Library ──────────────────────────────────────────────
    lib_block, lib_lay = make_field_block(
        "Media Library",
        "Write Kodi/Jellyfin/Plex-compatible metadata files and chat replays "
        "for archival.",
    )
    win.nfo_check = QCheckBox("Write .nfo file (movie schema) alongside each download")
    win.nfo_check.setChecked(win._write_nfo)
    lib_lay.addWidget(win.nfo_check)
    win.chat_check = QCheckBox("Download Twitch VOD chat replay (JSON + plain text)")
    win.chat_check.setChecked(TwitchExtractor.download_chat_enabled)
    lib_lay.addWidget(win.chat_check)
    card_lay.addWidget(lib_block)

    # ── Media Server Auto-Import (F33) ────────────────────────────
    from ...integrations.media_server import SERVER_TYPES
    ms_block, ms_lay = make_field_block(
        "Media Server Auto-Import",
        "Copy recordings into a Plex/Jellyfin/Emby library folder and trigger "
        "a library scan after each download.",
    )
    ms_cfg = win._config.get("media_server", {})
    win.ms_enable_check = QCheckBox("Enable auto-import after download")
    win.ms_enable_check.setChecked(bool(ms_cfg.get("enabled")))
    ms_lay.addWidget(win.ms_enable_check)

    ms_type_row = QHBoxLayout()
    ms_type_row.setSpacing(8)
    ms_type_row.addWidget(QLabel("Server type:"))
    win.ms_type_combo = QComboBox()
    win.ms_type_combo.addItems([t.title() for t in SERVER_TYPES])
    cur_type = (ms_cfg.get("server_type") or "plex").lower()
    idx = SERVER_TYPES.index(cur_type) if cur_type in SERVER_TYPES else 0
    win.ms_type_combo.setCurrentIndex(idx)
    ms_type_row.addWidget(win.ms_type_combo)
    ms_type_row.addStretch(1)
    ms_lay.addLayout(ms_type_row)

    ms_url_row = QHBoxLayout()
    ms_url_row.setSpacing(8)
    ms_url_row.addWidget(QLabel("Server URL:"))
    win.ms_url_input = QLineEdit(ms_cfg.get("url", ""))
    win.ms_url_input.setPlaceholderText("http://localhost:32400")
    ms_url_row.addWidget(win.ms_url_input)
    ms_lay.addLayout(ms_url_row)

    ms_token_row = QHBoxLayout()
    ms_token_row.setSpacing(8)
    ms_token_row.addWidget(QLabel("API token:"))
    win.ms_token_input = QLineEdit(ms_cfg.get("token", ""))
    win.ms_token_input.setEchoMode(QLineEdit.EchoMode.Password)
    win.ms_token_input.setPlaceholderText("Plex token / Jellyfin API key")
    ms_token_row.addWidget(win.ms_token_input)
    ms_lay.addLayout(ms_token_row)

    ms_lib_row = QHBoxLayout()
    ms_lib_row.setSpacing(8)
    ms_lib_row.addWidget(QLabel("Library ID:"))
    win.ms_library_id_input = QLineEdit(ms_cfg.get("library_id", "1"))
    win.ms_library_id_input.setFixedWidth(60)
    win.ms_library_id_input.setToolTip("Plex library section ID (e.g. 1). Ignored for Jellyfin/Emby.")
    ms_lib_row.addWidget(win.ms_library_id_input)
    ms_lib_row.addStretch(1)
    ms_lay.addLayout(ms_lib_row)

    ms_path_row = QHBoxLayout()
    ms_path_row.setSpacing(8)
    ms_path_row.addWidget(QLabel("Library path:"))
    win.ms_path_input = QLineEdit(ms_cfg.get("library_path", ""))
    win.ms_path_input.setPlaceholderText("/path/to/media/library")
    ms_path_row.addWidget(win.ms_path_input)
    ms_lay.addLayout(ms_path_row)

    card_lay.addWidget(ms_block)

    # ── Post-Processing ────────────────────────────────────────────
    pp_block, pp_lay = make_field_block(
        "Post-Processing",
        "Automatic ffmpeg operations on each downloaded file. Originals are preserved.",
    )
    # Preset selector (v4.20.0 — F7)
    preset_row = QHBoxLayout()
    preset_row.setSpacing(8)
    preset_row.addWidget(QLabel("Preset:"))
    win.pp_preset_combo = QComboBox()
    win.pp_preset_combo.setMinimumWidth(180)
    win.pp_preset_combo.setToolTip("Load a saved post-processing profile")
    _populate_pp_presets(win)
    win.pp_preset_combo.currentIndexChanged.connect(
        lambda _idx, w=win: _on_pp_preset_selected(w)
    )
    preset_row.addWidget(win.pp_preset_combo)
    win.pp_preset_save_btn = QPushButton("Save As…")
    win.pp_preset_save_btn.setObjectName("ghost")
    win.pp_preset_save_btn.setFixedWidth(80)
    win.pp_preset_save_btn.clicked.connect(lambda _c=False, w=win: _on_pp_preset_save(w))
    preset_row.addWidget(win.pp_preset_save_btn)
    win.pp_preset_del_btn = QPushButton("Delete")
    win.pp_preset_del_btn.setObjectName("ghost")
    win.pp_preset_del_btn.setFixedWidth(60)
    win.pp_preset_del_btn.clicked.connect(lambda _c=False, w=win: _on_pp_preset_delete(w))
    preset_row.addWidget(win.pp_preset_del_btn)
    preset_row.addStretch(1)
    pp_lay.addLayout(preset_row)

    win.pp_audio_check = QCheckBox("Extract audio as MP3 (libmp3lame, VBR quality 2)")
    win.pp_audio_check.setChecked(PostProcessor.extract_audio)
    pp_lay.addWidget(win.pp_audio_check)
    win.pp_loud_check = QCheckBox("Normalize loudness (EBU R128: I=-16, TP=-1.5, LRA=11)")
    win.pp_loud_check.setChecked(PostProcessor.normalize_loudness)
    pp_lay.addWidget(win.pp_loud_check)
    win.pp_h265_check = QCheckBox("Re-encode video to H.265/HEVC (libx265, CRF 23 — slow)")
    win.pp_h265_check.setChecked(PostProcessor.reencode_h265)
    pp_lay.addWidget(win.pp_h265_check)
    win.pp_contact_check = QCheckBox("Generate contact sheet (3x3 thumbnail grid .jpg)")
    win.pp_contact_check.setChecked(PostProcessor.contact_sheet)
    pp_lay.addWidget(win.pp_contact_check)
    win.pp_split_check = QCheckBox(
        "Split by chapters into per-chapter files (for videos with chapters)"
    )
    win.pp_split_check.setChecked(PostProcessor.split_by_chapter)
    pp_lay.addWidget(win.pp_split_check)

    # Silence removal (v4.20.0 — F26)
    silence_row = QHBoxLayout()
    silence_row.setSpacing(8)
    win.pp_silence_check = QCheckBox("Remove silence / dead air")
    win.pp_silence_check.setChecked(PostProcessor.remove_silence)
    win.pp_silence_check.setToolTip(
        "Detect silent segments with ffmpeg silencedetect and cut them out.\n"
        "Produces a .nosilence copy — the original is preserved."
    )
    silence_row.addWidget(win.pp_silence_check)
    silence_row.addWidget(QLabel("Threshold:"))
    win.pp_silence_db_spin = QSpinBox()
    win.pp_silence_db_spin.setRange(-60, -10)
    win.pp_silence_db_spin.setSuffix(" dB")
    win.pp_silence_db_spin.setValue(int(PostProcessor.silence_noise_db or -30))
    win.pp_silence_db_spin.setToolTip("Noise floor — lower values are more aggressive")
    win.pp_silence_db_spin.setFixedWidth(90)
    silence_row.addWidget(win.pp_silence_db_spin)
    silence_row.addWidget(QLabel("Min:"))
    win.pp_silence_dur_spin = QSpinBox()
    win.pp_silence_dur_spin.setRange(1, 60)
    win.pp_silence_dur_spin.setSuffix("s")
    win.pp_silence_dur_spin.setValue(int(PostProcessor.silence_min_duration or 3))
    win.pp_silence_dur_spin.setToolTip("Minimum consecutive silence before cutting")
    win.pp_silence_dur_spin.setFixedWidth(80)
    silence_row.addWidget(win.pp_silence_dur_spin)
    silence_row.addStretch(1)
    pp_lay.addLayout(silence_row)

    # Video converter row
    win.pp_convert_video_check = QCheckBox("Convert video to:")
    win.pp_convert_video_check.setChecked(PostProcessor.convert_video)
    win.pp_convert_video_format = QComboBox()
    win.pp_convert_video_format.addItems(VIDEO_CONTAINERS)
    idx = (
        VIDEO_CONTAINERS.index(PostProcessor.convert_video_format)
        if PostProcessor.convert_video_format in VIDEO_CONTAINERS else 0
    )
    win.pp_convert_video_format.setCurrentIndex(idx)
    win.pp_convert_video_format.setFixedWidth(80)
    win.pp_convert_video_codec = QComboBox()
    vc_keys = available_video_codec_keys()
    win.pp_convert_video_codec.addItems(vc_keys)
    saved_vc = PostProcessor.convert_video_codec
    if saved_vc in vc_keys:
        win.pp_convert_video_codec.setCurrentIndex(vc_keys.index(saved_vc))
    elif "h264" in vc_keys:
        win.pp_convert_video_codec.setCurrentIndex(vc_keys.index("h264"))
    win.pp_convert_video_codec.setFixedWidth(140)
    hw_count = sum(1 for k in vc_keys if "(" in k)
    hw_note = (
        f" ({hw_count} GPU encoder{'s' if hw_count != 1 else ''} detected)"
        if hw_count else ""
    )
    win.pp_convert_video_codec.setToolTip(
        "copy = fast remux (no re-encode)\n"
        "h264/h265/vp9/av1/mpeg4 = software encoders\n"
        "(NVENC) = NVIDIA GPU (5-20x faster)\n"
        "(QSV) = Intel Quick Sync\n"
        "(AMF) = AMD GPU\n"
        "(VT) = Apple VideoToolbox\n"
        + hw_note
    )
    # Scale target
    scale_items = ["original", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
    win.pp_convert_video_scale = QComboBox()
    win.pp_convert_video_scale.addItems(scale_items)
    idx = (
        scale_items.index(PostProcessor.convert_video_scale)
        if PostProcessor.convert_video_scale in scale_items else 0
    )
    win.pp_convert_video_scale.setCurrentIndex(idx)
    win.pp_convert_video_scale.setFixedWidth(90)
    win.pp_convert_video_scale.setToolTip(
        "Downscale target height. Aspect ratio is preserved.\n"
        "Forces a re-encode when not 'original' (copy codec ignored)."
    )
    # FPS cap
    fps_items = ["original", "60", "30", "24"]
    win.pp_convert_video_fps = QComboBox()
    win.pp_convert_video_fps.addItems(fps_items)
    idx = (
        fps_items.index(PostProcessor.convert_video_fps)
        if PostProcessor.convert_video_fps in fps_items else 0
    )
    win.pp_convert_video_fps.setCurrentIndex(idx)
    win.pp_convert_video_fps.setFixedWidth(80)
    win.pp_convert_video_fps.setToolTip(
        "Frame rate cap. Forces a re-encode when not 'original'."
    )

    vconv_row = QHBoxLayout()
    vconv_row.setSpacing(6)
    vconv_row.addWidget(win.pp_convert_video_check)
    vconv_row.addSpacing(4)
    vconv_row.addWidget(QLabel("Container:"))
    vconv_row.addWidget(win.pp_convert_video_format)
    vconv_row.addSpacing(8)
    vconv_row.addWidget(QLabel("Codec:"))
    vconv_row.addWidget(win.pp_convert_video_codec)
    vconv_row.addSpacing(8)
    vconv_row.addWidget(QLabel("Scale:"))
    vconv_row.addWidget(win.pp_convert_video_scale)
    vconv_row.addSpacing(8)
    vconv_row.addWidget(QLabel("FPS:"))
    vconv_row.addWidget(win.pp_convert_video_fps)
    vconv_row.addStretch(1)
    pp_lay.addLayout(vconv_row)

    # Audio converter row
    win.pp_convert_audio_check = QCheckBox("Convert audio to:")
    win.pp_convert_audio_check.setChecked(PostProcessor.convert_audio)
    win.pp_convert_audio_format = QComboBox()
    win.pp_convert_audio_format.addItems(AUDIO_CONTAINERS)
    idx = (
        AUDIO_CONTAINERS.index(PostProcessor.convert_audio_format)
        if PostProcessor.convert_audio_format in AUDIO_CONTAINERS else 0
    )
    win.pp_convert_audio_format.setCurrentIndex(idx)
    win.pp_convert_audio_format.setFixedWidth(80)
    win.pp_convert_audio_codec = QComboBox()
    win.pp_convert_audio_codec.addItems(list(AUDIO_CODECS.keys()))
    ac_keys = list(AUDIO_CODECS.keys())
    idx = (
        ac_keys.index(PostProcessor.convert_audio_codec)
        if PostProcessor.convert_audio_codec in ac_keys else 1
    )
    win.pp_convert_audio_codec.setCurrentIndex(idx)
    win.pp_convert_audio_codec.setFixedWidth(90)
    win.pp_convert_audio_codec.setToolTip(
        "copy = remux only\n"
        "mp3 = libmp3lame (universal)\n"
        "aac = AAC-LC (Apple-friendly)\n"
        "opus = low-bitrate champion\n"
        "vorbis = open-source lossy\n"
        "flac/pcm = lossless"
    )
    win.pp_convert_audio_bitrate = QComboBox()
    win.pp_convert_audio_bitrate.addItems(["96k", "128k", "192k", "256k", "320k"])
    br_items = ["96k", "128k", "192k", "256k", "320k"]
    idx = (
        br_items.index(PostProcessor.convert_audio_bitrate)
        if PostProcessor.convert_audio_bitrate in br_items else 2
    )
    win.pp_convert_audio_bitrate.setCurrentIndex(idx)
    win.pp_convert_audio_bitrate.setFixedWidth(80)
    win.pp_convert_audio_bitrate.setToolTip("Bitrate (ignored for flac/pcm)")
    # Sample rate
    sr_items = ["original", "48000", "44100", "22050"]
    win.pp_convert_audio_samplerate = QComboBox()
    win.pp_convert_audio_samplerate.addItems(sr_items)
    idx = (
        sr_items.index(PostProcessor.convert_audio_samplerate)
        if PostProcessor.convert_audio_samplerate in sr_items else 0
    )
    win.pp_convert_audio_samplerate.setCurrentIndex(idx)
    win.pp_convert_audio_samplerate.setFixedWidth(90)
    win.pp_convert_audio_samplerate.setToolTip(
        "Sample rate (Hz). Forces a re-encode when not 'original'."
    )

    aconv_row = QHBoxLayout()
    aconv_row.setSpacing(6)
    aconv_row.addWidget(win.pp_convert_audio_check)
    aconv_row.addSpacing(4)
    aconv_row.addWidget(QLabel("Container:"))
    aconv_row.addWidget(win.pp_convert_audio_format)
    aconv_row.addSpacing(8)
    aconv_row.addWidget(QLabel("Codec:"))
    aconv_row.addWidget(win.pp_convert_audio_codec)
    aconv_row.addSpacing(8)
    aconv_row.addWidget(QLabel("Bitrate:"))
    aconv_row.addWidget(win.pp_convert_audio_bitrate)
    aconv_row.addSpacing(8)
    aconv_row.addWidget(QLabel("Rate:"))
    aconv_row.addWidget(win.pp_convert_audio_samplerate)
    aconv_row.addStretch(1)
    pp_lay.addLayout(aconv_row)

    win.pp_convert_delete_check = QCheckBox(
        "Delete original source file after successful conversion"
    )
    win.pp_convert_delete_check.setChecked(PostProcessor.convert_delete_source)
    pp_lay.addWidget(win.pp_convert_delete_check)

    # Standalone manual converter
    manual_row = QHBoxLayout()
    manual_row.setSpacing(8)
    win.convert_files_btn = QPushButton("Convert Files...")
    win.convert_files_btn.setObjectName("secondary")
    win.convert_files_btn.setToolTip(
        "Pick individual media files and convert them with the current settings.\n"
        "Saves your settings first."
    )
    win.convert_files_btn.clicked.connect(win._on_convert_files_clicked)
    manual_row.addWidget(win.convert_files_btn)

    win.convert_folder_btn = QPushButton("Convert Folder...")
    win.convert_folder_btn.setObjectName("secondary")
    win.convert_folder_btn.setToolTip(
        "Pick a folder; every video/audio file in it gets converted."
    )
    win.convert_folder_btn.clicked.connect(win._on_convert_folder_clicked)
    manual_row.addWidget(win.convert_folder_btn)

    win.convert_cancel_btn = QPushButton("Cancel")
    win.convert_cancel_btn.setObjectName("secondary")
    win.convert_cancel_btn.setVisible(False)
    win.convert_cancel_btn.clicked.connect(win._on_convert_cancel)
    manual_row.addWidget(win.convert_cancel_btn)
    manual_row.addStretch(1)
    pp_lay.addLayout(manual_row)

    card_lay.addWidget(pp_block)

    # Save / Import / Export row
    save_row = QHBoxLayout()
    import_btn = QPushButton("Import Config")
    import_btn.setObjectName("secondary")
    import_btn.setToolTip("Replace current settings with a backup file")
    import_btn.clicked.connect(win._on_import_config)
    save_row.addWidget(import_btn)
    export_btn = QPushButton("Export Config")
    export_btn.setObjectName("secondary")
    export_btn.setToolTip("Write current settings to a backup file")
    export_btn.clicked.connect(win._on_export_config)
    save_row.addWidget(export_btn)
    save_row.addStretch()
    save_btn = QPushButton("Save Settings")
    save_btn.setObjectName("primary")
    save_btn.clicked.connect(win._on_save_settings)
    save_row.addWidget(save_btn)
    card_lay.addLayout(save_row)

    lay.addWidget(card, 1)
    return page
