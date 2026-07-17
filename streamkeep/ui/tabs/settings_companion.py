"""Browser companion, lifecycle, server, and update Settings handlers."""

import os
import sys

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices

from ... import VERSION
from ... import db as _db
from ...models import HistoryEntry
from ...local_server import (
    LocalCompanionServer,
    generate_bearer_token,
    valid_bearer_token,
)
from ...updater import DownloadUpdateWorker, UpdateCheckWorker, arm_self_replace
from ..widgets import (
    ask_premium_confirmation,
    show_premium_message,
    update_status_banner,
)


class SettingsCompanionMixin:
    """Companion trust boundary, cleanup, and release-update orchestration."""

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
        """Persist reverse-proxy scope changes and restart if needed."""
        self._config["companion_bind_lan"] = bool(checked)
        if hasattr(self, "companion_proxy_origin_input"):
            self._config["companion_proxy_origin"] = (
                self.companion_proxy_origin_input.text().strip()
            )
        self._persist_config()
        if bool(self._config.get("companion_server_enabled", False)):
            self._maybe_start_companion_server(force_restart=self._companion_server is not None)
            if self._companion_server is None:
                self._set_status(
                    "Browser companion could not start. Review the HTTPS origin and secure storage status.",
                    "warning",
                )
            elif checked:
                self._set_status(
                    "Browser companion restarted behind the trusted HTTPS proxy.",
                    "warning",
                )
            else:
                self._set_status("Browser companion returned to local-only access.", "success")
        else:
            self._refresh_companion_ui()
            self._set_status("Browser companion access scope saved.", "success")

    def _on_companion_proxy_origin_changed(self):
        origin = self.companion_proxy_origin_input.text().strip()
        changed = origin != str(self._config.get("companion_proxy_origin", "") or "")
        self._config["companion_proxy_origin"] = origin
        self._persist_config()
        if changed and bool(self._config.get("companion_bind_lan", False)):
            self._maybe_start_companion_server(
                force_restart=self._companion_server is not None
            )
            if self._companion_server is None:
                self._set_status(
                    "HTTPS remote origin is invalid or the companion could not restart.",
                    "warning",
                )
            else:
                self._set_status("HTTPS remote origin applied.", "success")

    def _ensure_companion_master_token(self):
        token = str(self._config.get("companion_token", "") or "")
        if not valid_bearer_token(token):
            token = generate_bearer_token()
        self._config["companion_token"] = token
        if not self._persist_config():
            self._config.pop("companion_token", None)
            raise ValueError(
                "Secure credential storage is unavailable; companion access stayed off."
            )
        return token

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
        pairing_code = str(getattr(self, "_companion_pairing_code", "") or "")
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
                "The listener remains local. Other devices can connect only through the configured HTTPS reverse proxy and a one-time pairing code."
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
            scope_detail = (
                "Trusted devices can connect through the HTTPS reverse proxy."
                if bind_lan else
                "Only this PC can reach the companion."
            )
            self.companion_scope_sub.setText("HTTPS proxy" if bind_lan else "This PC")
            self.companion_scope_sub.setToolTip(scope_detail)
        if hasattr(self, "companion_remote_value"):
            if running:
                self.companion_remote_value.setText("Ready")
                self.companion_remote_sub.setText(f"Port {srv.port}")
                self.companion_remote_sub.setToolTip(local_url)
            elif enabled and error_text:
                self.companion_remote_value.setText("Error")
                self.companion_remote_sub.setText("Needs attention")
                self.companion_remote_sub.setToolTip(error_text)
            elif enabled:
                self.companion_remote_value.setText("Starting")
                self.companion_remote_sub.setText("Local listener")
                self.companion_remote_sub.setToolTip("")
            else:
                self.companion_remote_value.setText("Off")
                self.companion_remote_sub.setText("Not running")
                self.companion_remote_sub.setToolTip("")
        if hasattr(self, "companion_token_value"):
            if pairing_code:
                self.companion_token_value.setText("Ready")
                self.companion_token_sub.setText("Expires in 5 min")
            elif running:
                self.companion_token_value.setText("Generate")
                self.companion_token_sub.setText("One-time code")
            else:
                self.companion_token_value.setText("Waiting")
                self.companion_token_sub.setText("Not running")

        if hasattr(self, "companion_rotate_token_btn"):
            self.companion_rotate_token_btn.setEnabled(running)
        if hasattr(self, "companion_revoke_tokens_btn"):
            self.companion_revoke_tokens_btn.setEnabled(running)

        if hasattr(self, "companion_url_display"):
            self.companion_url_display.setText(local_url)
        if hasattr(self, "companion_open_url_btn"):
            self.companion_open_url_btn.setEnabled(bool(local_url))
        if hasattr(self, "companion_copy_url_btn"):
            self.companion_copy_url_btn.setEnabled(bool(local_url))
        if hasattr(self, "companion_token_display"):
            self.companion_token_display.setText(pairing_code)
        if hasattr(self, "companion_copy_token_btn"):
            self.companion_copy_token_btn.setEnabled(bool(pairing_code))

    def _on_copy_companion_url(self):
        self._copy_text_to_clipboard(self._companion_local_url(), "Browser companion URL")

    def _on_copy_companion_token(self):
        text = self.companion_token_display.text() if hasattr(self, "companion_token_display") else ""
        self._copy_text_to_clipboard(text, "One-time pairing code")

    def _on_rotate_companion_token(self):
        srv = getattr(self, "_companion_server", None)
        if srv is None or int(getattr(srv, "port", 0) or 0) <= 0:
            self._set_status("Companion server is not running.", "warning")
            return
        self._config["companion_token"] = srv.rotate_token()
        if not self._persist_config():
            srv.stop()
            self._companion_server = None
            self._companion_pairing_code = ""
            self._config.pop("companion_token", None)
            self._set_status(
                "Access was revoked, but secure storage failed; companion stopped.",
                "error",
            )
            self._refresh_companion_ui()
            return
        self._publish_companion_pairing_code(srv.create_pairing_code())
        self._log("[COMPANION] All client access revoked and master token rotated.")
        self._set_status("All clients revoked. A fresh one-time pairing code is ready.", "success")

    def _on_generate_companion_pairing_code(self):
        srv = getattr(self, "_companion_server", None)
        if srv is None or int(getattr(srv, "port", 0) or 0) <= 0:
            self._set_status("Companion server is not running.", "warning")
            return
        self._publish_companion_pairing_code(srv.create_pairing_code())
        self._set_status("One-time pairing code generated for five minutes.", "success")

    def _publish_companion_pairing_code(self, code):
        self._companion_pairing_code = code
        self._refresh_companion_ui()
        QTimer.singleShot(
            300_000,
            lambda issued=code: self._expire_companion_pairing_code(issued),
        )

    def _expire_companion_pairing_code(self, issued):
        if getattr(self, "_companion_pairing_code", "") == issued:
            self._companion_pairing_code = ""
            self._refresh_companion_ui()

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
        history = (
            HistoryEntry.from_dict(row)
            for row in _db.iter_history(page_size=500)
        )
        removals = evaluate_cleanup(history, policy)
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
        history = (
            HistoryEntry.from_dict(row)
            for row in _db.iter_history(page_size=500)
        )
        removals = evaluate_cleanup(history, policy)
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
        proxy_origin = str(self._config.get("companion_proxy_origin", "") or "").strip()
        desired_bind = "127.0.0.1"
        running = self._companion_server is not None
        if running and (
            getattr(self._companion_server, "_bind_addr", "") != desired_bind
            or getattr(self._companion_server, "external_origin", "")
            != (proxy_origin if bind_lan else "")
        ):
            force_restart = True
        if force_restart and running:
            try:
                self._companion_server.stop()
            except Exception:
                pass
            self._companion_server = None
            self._companion_pairing_code = ""
            running = False
        if enabled and not running:
            try:
                master_token = self._ensure_companion_master_token()
                srv = LocalCompanionServer(
                    bind_lan=bind_lan,
                    external_origin=proxy_origin,
                    master_token=master_token,
                )
                srv.state_provider = self._api_state_snapshot
                srv.url_received.connect(self._on_companion_url)
                srv.clip_received.connect(self._on_companion_clip)
                srv.failed_job_retry_requested.connect(self._retry_failed_job)
                srv.failed_job_discard_requested.connect(self._discard_failed_job)
                srv.start()
                self._companion_server = srv
                self._companion_last_error = ""
                self._log(
                    f"[COMPANION] Loopback listener ready on port {srv.port} "
                    "— pairing is explicit and nonce-protected."
                )
            except (OSError, ValueError) as e:
                self._companion_pairing_code = ""
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
            self._companion_pairing_code = ""
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
            for row in reversed(_db.query_history_page(limit=50)):
                h = HistoryEntry.from_dict(row)
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
        error = str((payload or {}).get("error", "") or "")
        if error:
            self._latest_update_payload = None
            self._log(f"[UPDATE] {error}")
            self._set_status(error, "error")
            if hasattr(self, "update_banner_label"):
                self.update_banner_label.setText(error)
                self.update_banner.setVisible(True)
                self.update_banner_install_btn.setEnabled(False)
                self.update_banner_install_btn.setText("Install blocked")
            return
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
            signer = str(payload.get("signer_subject", "") or "")
            if signer:
                label = f"{label} — publisher verified"
            self.update_banner_label.setText(label)
            self.update_banner.setVisible(True)
            self.update_banner_install_btn.setEnabled(True)
            self.update_banner_install_btn.setText("Download & install")
        self._notify_center(f"Update available: StreamKeep {tag}", "info")

    def _on_update_install(self):
        payload = getattr(self, "_latest_update_payload", None) or {}
        if not payload.get("asset"):
            self._set_status("Update available but no authenticated Windows asset was attached.", "warning")
            return
        self.update_banner_install_btn.setEnabled(False)
        self.update_banner_install_btn.setText("Downloading...")
        worker = DownloadUpdateWorker(payload)
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
            body="StreamKeep will close, install the publisher-authenticated build, and relaunch automatically.",
            eyebrow="UPDATER",
            badge_text="Restart required",
            tone="warning",
            summary_title="Any active download or recording will be interrupted.",
            summary_body="Install now when you are ready for the app to restart itself.",
            details_title="What happens next",
            details_body=(
                "1. StreamKeep closes.\n"
                "2. Migration-sensitive state is snapshotted.\n"
                "3. The signed build replaces the current executable.\n"
                "4. If startup health confirmation fails, the previous build and state are restored."
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
        if arm_self_replace(path_or_err, self._latest_update_payload or {}):
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
