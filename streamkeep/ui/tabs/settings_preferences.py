"""Cookie, account, proxy, theme, language, and save handlers."""

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QFileDialog

from ...extractors.ytdlp import YtDlpExtractor
from ...http import set_native_proxy
from ...i18n import install_translator
from ...postprocess import PostProcessor
from ...utils import (
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    scan_browser_cookies as _scan_browser_cookies,
)
from ..widgets import ask_premium_confirmation, show_premium_message


class _CredentialProbeWorker(QThread):
    """Run non-downloading credential probes off the UI thread.

    Emits ``result_ready(ProbeResult)`` per platform and ``finished_all()``
    when done. Cancellable so a slow network never blocks Settings.
    """

    result_ready = pyqtSignal(object)
    finished_all = pyqtSignal()

    def __init__(self, platforms, timeout=12, parent=None):
        super().__init__(parent)
        self._platforms = list(platforms)
        self._timeout = timeout
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        from ...credential_check import (
            NETWORK_ERROR, ProbeResult, probe_platform,
        )
        for platform in self._platforms:
            if self._cancel:
                break
            try:
                res = probe_platform(
                    platform, timeout=self._timeout,
                    cancel_check=lambda: self._cancel,
                )
            except Exception as exc:  # a probe must never crash the worker
                res = ProbeResult(platform, NETWORK_ERROR, str(exc))
            if not self._cancel:
                self.result_ready.emit(res)
        self.finished_all.emit()


class SettingsPreferencesMixin:
    """Persisted preferences plus credential and network control handlers."""

    def _settings_browse(self, line_edit):
        d = QFileDialog.getExistingDirectory(self, "Select Folder", line_edit.text())
        if d:
            line_edit.setText(d)

    def _on_test_youtube_capability(self):
        """Run the local YouTube capability report (V26) and present the
        result plus concrete remediation for SABR/PO-token gating."""
        from ...extractors.ytdlp import youtube_health_report
        preset = str(self._config.get("youtube_player_client", "") or "")
        report = youtube_health_report(player_client=preset)
        lines = [
            f"Status: {report['summary'] or report['state'] or 'unknown'}",
            f"yt-dlp: {report['yt_dlp_version'] or 'unknown'}",
            f"JS runtime: {(report.get('js_runtime') or {}).get('name') or 'none'}",
            f"player_client: {report['player_client']}",
            "PO-token provider: "
            + ("detected" if report['pot_provider']['available'] else "not detected"),
        ]
        pot_setup = report.get("pot_setup") or {}
        if not pot_setup.get("provider_present", True):
            lines.append("")
            lines.extend(pot_setup.get("steps", []))
        for warning in report.get("warnings", []):
            lines.append(f"⚠ {warning}")
        show_premium_message(
            self,
            title="YouTube capability",
            body="\n".join(lines),
            eyebrow="YT-DLP",
            tone="success" if report.get("healthy") else "warning",
        )

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
        from ...cookies import clear_cookies, cookies_file_path
        if cookies_file_path() and not ask_premium_confirmation(
            self,
            title="Delete imported cookies?",
            body=(
                "Remove the imported cookies.txt. Authenticated or age-restricted "
                "content may fail until you import cookies again."
            ),
            eyebrow="COOKIES",
            badge_text="Cannot be undone",
            tone="warning",
            primary_label="Delete cookies",
            secondary_label="Cancel",
            default_action="secondary",
        ):
            return
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
        if not ask_premium_confirmation(
            self,
            title="Delete all saved platform tokens?",
            body=(
                "Remove every stored platform credential. You will need to "
                "re-enter them to download authenticated content again."
            ),
            eyebrow="ACCOUNTS",
            badge_text="Cannot be undone",
            tone="warning",
            primary_label="Delete tokens",
            secondary_label="Cancel",
            default_action="secondary",
        ):
            return
        inputs = getattr(self, "_account_inputs", {})
        for plat_key in PLATFORMS:
            delete_credential(plat_key)
            if plat_key in inputs:
                inputs[plat_key][0].clear()
                inputs[plat_key][1].setText(credential_status(plat_key))
        self._set_status("All platform tokens cleared.", "success")

    def _on_check_account_tokens(self):
        """Validate stored platform credentials with a live, cancellable probe."""
        # Persist anything freshly typed so we validate what will actually be used.
        self._on_save_account_tokens()
        self._start_credential_probe(["twitch", "youtube", "kick"])

    def _start_credential_probe(self, platforms):
        worker = getattr(self, "_cred_probe_worker", None)
        if worker is not None and worker.isRunning():
            return  # a check is already in flight
        inputs = getattr(self, "_account_inputs", {})
        for plat in platforms:
            entry = inputs.get(plat)
            if entry:
                entry[1].setText("Checking…")
                entry[1].setToolTip("")
        btn = getattr(self, "acct_check_btn", None)
        if btn is not None:
            btn.setEnabled(False)
        self._cred_probe_worker = _CredentialProbeWorker(platforms, parent=self)
        self._cred_probe_worker.result_ready.connect(self._on_credential_result)
        self._cred_probe_worker.finished_all.connect(self._on_credential_probe_done)
        self._cred_probe_worker.start()
        self._set_status("Checking platform credentials…", "info")

    def _on_credential_result(self, result):
        entry = getattr(self, "_account_inputs", {}).get(result.platform)
        if entry:
            entry[1].setText(result.label)
            entry[1].setToolTip(result.detail or "")
        self._log(
            f"[ACCOUNTS] {result.platform}: {result.status}"
            + (f" ({result.detail})" if result.detail else "")
        )

    def _on_credential_probe_done(self):
        btn = getattr(self, "acct_check_btn", None)
        if btn is not None:
            btn.setEnabled(True)
        self._set_status("Credential check complete.", "success")

    def _on_check_cookies(self):
        """Validate the imported cookie profile locally (no request)."""
        from ...credential_check import probe_cookies
        result = probe_cookies()
        label = getattr(self, "cookies_status_label", None)
        if label is not None:
            text = f"{result.label}: {result.detail}" if result.detail else result.label
            label.setText(text)
        self._set_status(f"Cookies: {result.label}", result.tone)

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
        from ...download_options import validate_ytdlp_transfer_options
        try:
            transfer_options = validate_ytdlp_transfer_options(
                concurrent_fragments=self.ytdlp_fragments_spin.value(),
                retries=self.ytdlp_retries_input.text(),
                fragment_retries=self.ytdlp_fragment_retries_input.text(),
                retry_sleep=self.ytdlp_retry_sleep_input.text(),
                unavailable_fragments=(
                    self.ytdlp_unavailable_combo.currentData() or ""
                ),
                throttled_rate=self.ytdlp_throttled_input.text(),
                live_from_start=self.ytdlp_live_from_start_check.isChecked(),
                wait_for_video=self.ytdlp_wait_for_video_input.text(),
                embed_chapters=self.ytdlp_embed_chapters_combo.currentData(),
                embed_metadata=self.ytdlp_embed_metadata_combo.currentData(),
                embed_thumbnail=self.ytdlp_embed_thumbnail_combo.currentData(),
            )
        except ValueError as error:
            self._set_status(str(error), "warning")
            return
        for name, value in transfer_options.items():
            key = f"ytdlp_{name}"
            setattr(YtDlpExtractor, key, value)
            if value is None:
                self._config.pop(key, None)
            else:
                self._config[key] = value
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
            prev_proxy_origin = str(
                self._config.get("companion_proxy_origin", "") or ""
            )
            new_enabled = bool(self.companion_check.isChecked())
            new_bind_lan = bool(self.companion_lan_check.isChecked())
            new_proxy_origin = self.companion_proxy_origin_input.text().strip()
            self._config["companion_server_enabled"] = new_enabled
            self._config["companion_bind_lan"] = new_bind_lan
            self._config["companion_proxy_origin"] = new_proxy_origin
            if (
                new_enabled != prev_enabled
                or new_bind_lan != prev_bind_lan
                or new_proxy_origin != prev_proxy_origin
            ):
                self._maybe_start_companion_server(
                    force_restart=prev_enabled and new_enabled
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
        if hasattr(self, "native_notif_check"):
            self._config["native_notifications"] = bool(self.native_notif_check.isChecked())
        if hasattr(self, "disk_monitor_check"):
            self._config["disk_monitor_enabled"] = bool(self.disk_monitor_check.isChecked())
            self._config["disk_warning_gb"] = int(self.disk_warning_spin.value())
            self._config["disk_critical_gb"] = int(self.disk_critical_spin.value())
            self._config["disk_auto_pause"] = bool(self.disk_auto_pause_check.isChecked())
            self._apply_disk_monitor_settings()
        if hasattr(self, "queue_complete_action_combo"):
            from ...power import normalize_power_action
            self._config["queue_complete_action"] = normalize_power_action(
                self.queue_complete_action_combo.currentData()
            )
        # Apply bandwidth schedule rule
        self._bandwidth_rule = {
            "enabled": self.bw_enable_check.isChecked(),
            "start_hour": self.bw_start_spin.value(),
            "end_hour": self.bw_end_spin.value(),
            "limit": self.bw_limit_input.text().strip(),
        }
        self._apply_bandwidth_schedule()
        # Apply YouTube extras
        from ...download_options import validate_subtitle_options
        try:
            subtitle_options = validate_subtitle_options(
                enabled=self.subs_check.isChecked(),
                languages=self.subs_languages_input.text(),
                automatic=self.subs_auto_check.isChecked(),
                convert=self.subs_convert_combo.currentData() or "",
                embed=self.subs_delivery_combo.currentData() == "embed",
            )
        except ValueError as error:
            self._set_status(f"Subtitle settings: {error}", "warning")
            return
        YtDlpExtractor.download_subs = subtitle_options["enabled"]
        YtDlpExtractor.subtitle_languages = subtitle_options["languages"]
        YtDlpExtractor.subtitle_auto = subtitle_options["automatic"]
        YtDlpExtractor.subtitle_convert = subtitle_options["convert"]
        YtDlpExtractor.subtitle_embed = subtitle_options["embed"]
        if hasattr(self, "capture_youtube_chat_check"):
            YtDlpExtractor.capture_youtube_chat = (
                self.capture_youtube_chat_check.isChecked()
            )
            self._config["capture_youtube_chat"] = (
                self.capture_youtube_chat_check.isChecked()
            )
        if hasattr(self, "youtube_client_combo"):
            preset = str(self.youtube_client_combo.currentData() or "")
            YtDlpExtractor.youtube_player_client = preset
            self._config["youtube_player_client"] = preset
        self._config["download_subs"] = subtitle_options["enabled"]
        self._config["subtitle_languages"] = subtitle_options["languages"]
        self._config["subtitle_auto"] = subtitle_options["automatic"]
        self._config["subtitle_convert"] = subtitle_options["convert"]
        self._config["subtitle_embed"] = subtitle_options["embed"]
        from ...download_options import validate_sponsorblock_options
        sponsorblock_mark = ",".join(
            category for category, combo in self.sponsorblock_action_combos.items()
            if combo.currentData() == "mark"
        )
        sponsorblock_remove = ",".join(
            category for category, combo in self.sponsorblock_action_combos.items()
            if combo.currentData() == "remove"
        )
        try:
            sponsorblock_options = validate_sponsorblock_options(
                enabled=self.sponsorblock_check.isChecked(),
                mark=sponsorblock_mark,
                remove=sponsorblock_remove,
                api_url=self.sponsorblock_api_input.text(),
            )
        except ValueError as error:
            self._set_status(f"SponsorBlock settings: {error}", "warning")
            return
        YtDlpExtractor.sponsorblock = sponsorblock_options["enabled"]
        YtDlpExtractor.sponsorblock_mark = sponsorblock_options["mark"]
        YtDlpExtractor.sponsorblock_remove = sponsorblock_options["remove"]
        YtDlpExtractor.sponsorblock_api = sponsorblock_options["api_url"]
        self._config["sponsorblock"] = sponsorblock_options["enabled"]
        self._config["sponsorblock_mark"] = sponsorblock_options["mark"]
        self._config["sponsorblock_remove"] = sponsorblock_options["remove"]
        self._config["sponsorblock_api"] = sponsorblock_options["api_url"]
        # Apply filename templates
        self._folder_template = self.folder_template_input.text().strip() or DEFAULT_FOLDER_TEMPLATE
        self._file_template = self.file_template_input.text().strip() or DEFAULT_FILE_TEMPLATE
        # Apply webhook
        self._webhook_url = self.webhook_input.text().strip()
        # Event hooks (F24) are structured actions persisted immediately by the
        # per-event editor (_on_hook_save); nothing to collect on general save.
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
                "keep_last_per_source": self.lc_keep_last_spin.value(),
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
        if hasattr(self, "pp_bilingual_check"):
            PostProcessor.bilingual_subs = self.pp_bilingual_check.isChecked()
            PostProcessor.bilingual_primary_lang = (
                self.pp_bilingual_primary.text().strip() or "en"
            )
            PostProcessor.bilingual_secondary_lang = (
                self.pp_bilingual_secondary.text().strip()
            )
            PostProcessor.bilingual_format = (
                self.pp_bilingual_format.currentText() or "srt"
            )
            PostProcessor.lrc_export = self.pp_lrc_check.isChecked()
            PostProcessor.lrc_lang = self.pp_lrc_lang.text().strip() or "en"
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
        """Apply the complete visual preference set instantly."""
        self._apply_visual_preferences()

    def _on_visual_settings_changed(self, _idx):
        self._apply_visual_preferences()

    def _apply_visual_preferences(self):
        from ...theme import apply_visual_system
        from PyQt6.QtWidgets import QApplication
        name = self.theme_combo.currentData() or "dark"
        density = self.density_combo.currentData() or "cozy"
        accent = self.accent_combo.currentData() or ""
        self._config["theme"] = name
        self._config["visual_density"] = density
        self._config["visual_accent"] = accent
        apply_visual_system(
            name, density, accent, app=QApplication.instance()
        )
        if hasattr(self, "settings_theme_value"):
            theme_display = {
                "dark": "Dark", "light": "Light", "system": "System",
                "high_contrast": "High Contrast",
            }.get(name, "Dark")
            self.settings_theme_value.setText(theme_display)
            accent_name = self.accent_combo.currentText()
            self.settings_theme_sub.setText(
                f"{density.title()} density • {accent_name} accent"
            )
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
            pill_source = "Standby"
            if hasattr(self, "status_pill"):
                pill_source = getattr(
                    self.status_pill, "_streamkeep_i18n_source", {}
                ).get("text", self.status_pill.text())
            current_tone = pill_to_tone.get(pill_source, "idle")
            self._set_status(self.status_label.text() or "Theme updated.", current_tone)
        self._refresh_notif_badge()
        self._persist_config()

    def _on_language_changed(self, _idx):
        lang = self.language_combo.currentData() or "en"
        if install_translator(lang):
            self._config["language"] = lang
            self._set_status(
                "Language updated across StreamKeep.",
                "success",
            )
        else:
            self._set_status("Language file could not be loaded.", "warning")
        self._persist_config()
