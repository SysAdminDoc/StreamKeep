"""Browser companion, lifecycle, server, and update Settings handlers."""

import os

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices

from ... import VERSION
from ... import db as _db
from ...config import save_config as _save_config
from ...har import normalize_replay_headers
from ...models import HistoryEntry
from ...local_server import (
    LocalCompanionServer,
    generate_bearer_token,
    valid_bearer_token,
)
from ...preflight import (
    PreflightError,
    ProbeCache,
    build_picker_response,
    collect_probe_result,
    serialize_stream_picker,
    serialize_vod_picker,
    validate_probe_request,
)
from ...updater import UpdateCheckWorker
from ..widgets import (
    ask_premium_confirmation,
    show_premium_message,
    update_status_banner,
)


class SettingsCompanionMixin:
    """Companion trust boundary, cleanup, and release-update orchestration."""

    def _companion_probe(self, data):
        """Resolve a companion probe without exposing delivery credentials."""
        item = validate_probe_request(data)
        request_headers = normalize_replay_headers(
            item.get("request_headers")
        )
        url = item["url"]
        from ...workers import FetchWorker

        def worker_factory():
            return FetchWorker(
                url,
                vod_source=item.get("vod_source") or None,
                vod_platform=item.get("vod_platform") or None,
                vod_title=item.get("vod_title") or None,
                vod_channel=item.get("vod_channel") or None,
                source_id=item.get("source_id") or None,
                webpage_url=item.get("webpage_url") or None,
                request_headers=request_headers,
            )

        kind, value = collect_probe_result(
            worker_factory,
            timeout_seconds=45.0,
        )
        picker = (
            serialize_vod_picker(value, url)
            if kind == "vods"
            else serialize_stream_picker(value, url)
        )
        if not picker.get("media_items"):
            raise PreflightError("probe returned no playable media")
        cache = getattr(self, "_companion_probe_cache", None)
        if cache is None:
            cache = ProbeCache()
            self._companion_probe_cache = cache
        validation_id, expires_at = cache.put(url, picker)
        return build_picker_response(url, picker, validation_id, expires_at)

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

    def _on_companion_extension_origin_pinned(self, origin):
        """Persist the device-local browser extension trust pin."""
        normalized = str(origin or "").strip()
        if normalized:
            self._config["companion_extension_origin"] = normalized
            self._log(f"[COMPANION] Pinned browser extension origin {normalized}.")
        else:
            self._config.pop("companion_extension_origin", None)
            self._log("[COMPANION] Browser extension origin pin cleared.")
        if not _save_config(self._config):
            self._log("[COMPANION] Could not persist the browser extension origin pin.")

    def _on_companion_security_event(self, event):
        """Surface a rate-limited local-server rejection in Notifications."""
        center = getattr(self, "_notifications", None)
        if center is not None:
            center.push_security_event(event)

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
        self._refresh_companion_tokens()

    def _refresh_companion_tokens(self):
        """Refresh the redacted scoped-token inventory in Settings."""
        table = getattr(self, "companion_tokens_table", None)
        if table is None:
            return
        from PyQt6.QtWidgets import QTableWidgetItem, QPushButton

        table.setRowCount(0)
        srv = getattr(self, "_companion_server", None)
        if srv is None or int(getattr(srv, "port", 0) or 0) <= 0:
            return
        for metadata in srv.list_scoped_tokens():
            row = table.rowCount()
            table.insertRow(row)
            values = (
                metadata.get("label", ""),
                ", ".join(metadata.get("scopes", [])),
                metadata.get("origin", "") or "Any origin",
                metadata.get("created_at", "") or "",
                metadata.get("last_used") or "Never",
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))
            revoke = QPushButton("Revoke")
            revoke.setObjectName("secondary")
            revoke.clicked.connect(
                lambda _checked=False, token_id=metadata.get("id", ""):
                self._on_revoke_companion_token(token_id)
            )
            table.setCellWidget(row, 5, revoke)

    def _on_revoke_companion_token(self, token_id):
        """Revoke one scoped token selected from the metadata inventory."""
        srv = getattr(self, "_companion_server", None)
        if srv is None or not str(token_id or "").strip():
            self._set_status("That scoped token is no longer active.", "warning")
            return
        if not srv.revoke_token_by_id(token_id):
            self._set_status("That scoped token is no longer active.", "warning")
            self._refresh_companion_tokens()
            return
        self._log(f"[COMPANION] Revoked scoped token {token_id}.")
        self._set_status("Scoped token revoked immediately.", "success")
        self._refresh_companion_tokens()

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
        from ...lifecycle import (
            evaluate_cleanup, execute_cleanup, keep_last_map_from_monitor,
            removal_real_paths,
        )
        policy = self._config.get("lifecycle", {})
        if not policy.get("enabled"):
            policy = dict(policy, enabled=True)  # preview even if disabled
        history = (
            HistoryEntry.from_dict(row)
            for row in _db.iter_history(page_size=500)
        )
        keep_map = keep_last_map_from_monitor(
            getattr(getattr(self, "monitor", None), "entries", []))
        removals = evaluate_cleanup(history, policy, keep_last_map=keep_map)
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
                self._remove_history_for_paths(removed_paths, reason="lifecycle")
            self._log(f"[LIFECYCLE] Recycled {removed} recording(s).")
            self._set_status(f"Lifecycle cleanup: {removed} recording(s) recycled.", "success")

    def _run_lifecycle_cleanup(self):
        """Run lifecycle cleanup silently after a download completes."""
        from ...lifecycle import (
            evaluate_cleanup, execute_cleanup, keep_last_map_from_monitor,
            removal_real_paths,
        )
        policy = self._config.get("lifecycle", {})
        if not policy or not policy.get("enabled"):
            return
        history = (
            HistoryEntry.from_dict(row)
            for row in _db.iter_history(page_size=500)
        )
        keep_map = keep_last_map_from_monitor(
            getattr(getattr(self, "monitor", None), "entries", []))
        removals = evaluate_cleanup(history, policy, keep_last_map=keep_map)
        if removals:
            removed = execute_cleanup(removals, log_fn=self._log)
            if removed:
                removed_paths = {
                    real_path for real_path in removal_real_paths(removals)
                    if not os.path.isdir(real_path)
                }
                self._remove_history_for_paths(removed_paths, reason="lifecycle")
                self._log(f"[LIFECYCLE] Auto-cleanup recycled {removed} recording(s).")

    def _on_tombstone_manager(self):
        """List deletion markers and let the user re-enable one identity."""
        from PyQt6.QtWidgets import (
            QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget,
            QTableWidgetItem, QVBoxLayout,
        )
        from PyQt6.QtWidgets import QAbstractItemView, QHeaderView

        dialog = QDialog(self)
        dialog.setWindowTitle("Removed media")
        dialog.setMinimumSize(760, 420)
        layout = QVBoxLayout(dialog)
        description = QLabel(
            "Deliberately removed media stays out of monitor, playlist, and queue "
            "dispatch until its tombstone is cleared. Retention and lifecycle "
            "markers are listed for audit but do not block re-fetching."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        summary = QLabel()
        layout.addWidget(summary)
        table = QTableWidget(0, 5, dialog)
        table.setHorizontalHeaderLabels(
            ("Platform", "Identity", "Deleted", "Reason", "Action")
        )
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(table, 1)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)

        def refresh():
            rows = _db.list_tombstones(limit=500)
            table.setRowCount(0)
            for marker in rows:
                row = table.rowCount()
                table.insertRow(row)
                identity = (
                    marker.get("source_id")
                    or marker.get("webpage_url")
                    or "unknown identity"
                )
                values = (
                    marker.get("platform", ""), identity,
                    marker.get("deleted_at", ""),
                    str(marker.get("reason", "user")).capitalize(),
                )
                for column, value in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(str(value)))
                action = QPushButton("Clear")
                action.setObjectName("secondary")
                action.clicked.connect(
                    lambda _checked=False, marker_id=marker.get("id", 0):
                    clear_one(marker_id)
                )
                table.setCellWidget(row, 4, action)
            summary.setText(
                f"{len(rows)} removed-media marker(s)"
                if rows else "No removed-media markers."
            )

        def clear_one(marker_id):
            if not _db.clear_tombstone(marker_id):
                return
            self._log(f"[TOMBSTONE] Cleared removed-media marker {marker_id}.")
            self._set_status(
                "Removed-media marker cleared; the identity may be downloaded again.",
                "success",
            )
            refresh()

        refresh()
        dialog.exec()

    # ── Browser companion local server ───────────────────────────────

    def _maybe_start_companion_server(self, force_restart=False):
        """Start (or stop) the local companion HTTP server based on the
        current Settings toggle. Called at launch and whenever the user
        changes the setting."""
        enabled = bool(self._config.get("companion_server_enabled", False))
        bind_lan = bool(self._config.get("companion_bind_lan", False))
        allow_private = bool(self._config.get("companion_allow_private_network", False))
        proxy_origin = str(self._config.get("companion_proxy_origin", "") or "").strip()
        extension_origin = str(
            self._config.get("companion_extension_origin", "") or ""
        ).strip()
        desired_bind = "127.0.0.1"
        running = self._companion_server is not None
        if running and (
            getattr(self._companion_server, "_bind_addr", "") != desired_bind
            or getattr(self._companion_server, "external_origin", "")
            != (proxy_origin if bind_lan else "")
            or bool(getattr(self._companion_server, "allow_private_network", False))
            != allow_private
            or str(getattr(self._companion_server, "extension_origin", "") or "")
            != extension_origin
        ):
            force_restart = True
        if force_restart and running:
            try:
                self._companion_server.stop()
            except Exception:
                pass  # safe: best-effort fallback; preserve the primary operation
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
                    allow_private_network=allow_private,
                    extension_origin=extension_origin,
                )
                srv.state_provider = self._api_state_snapshot
                srv.probe_submitter = self._companion_probe
                srv.handoff_received.connect(self._on_companion_handoff)
                srv.url_received.connect(self._on_companion_url)
                srv.clip_received.connect(self._on_companion_clip)
                srv.extension_origin_pinned.connect(
                    self._on_companion_extension_origin_pinned
                )
                srv.security_event.connect(self._on_companion_security_event)
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
                pass  # safe: best-effort fallback; preserve the primary operation
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
            pass  # safe: best-effort fallback; preserve the primary operation
        failures = []
        try:
            for row in _db.load_failed_jobs(limit=25):
                failures.append(_db.failed_job_public_view(row))
        except Exception:
            pass  # safe: best-effort fallback; preserve the primary operation
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
            pass  # safe: best-effort fallback; preserve the primary operation
        monitor = []
        try:
            for e in list(self.monitor.entries):
                monitor.append({
                    "channel_id": e.channel_id,
                    "platform": e.platform,
                    "status": e.last_status,
                })
        except Exception:
            pass  # safe: best-effort fallback; preserve the primary operation
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
            pass  # safe: best-effort fallback; preserve the primary operation
        resumable = []
        try:
            for rc in list(getattr(self, "_resume_candidates", [])):
                resumable.append({
                    "title": getattr(rc, "title", "") or "",
                    "url": getattr(rc, "url", "") or "",
                    "remaining": getattr(rc, "remaining_count", 0),
                })
        except Exception:
            pass  # safe: best-effort fallback; preserve the primary operation
        return {
            "downloads": downloads,
            "queue": queue_items,
            "failures": failures,
            "retry_circuits": _db.load_retry_circuits(),
            "backup": _db.backup_state_public_view(_db.load_backup_state()),
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
                pass  # safe: best-effort fallback; preserve the primary operation
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
                self.update_banner_install_btn.setText("Open release page")
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
            digest = str(
                payload.get("published_sha256")
                or (payload.get("asset") or {}).get("sha256", "")
                or ""
            )
            if digest:
                label = (
                    f"{label} — download manually and verify the published "
                    f"SHA-256: {digest}. Use your package manager when applicable."
                )
            self.update_banner_label.setText(label)
            self.update_banner.setVisible(True)
            self.update_banner_install_btn.setEnabled(True)
            self.update_banner_install_btn.setText("Open release page")
        self._notify_center(f"Update available: StreamKeep {tag}", "info")

    def _on_update_manual_download(self):
        """Open the fixed release page; public metadata is never installed here."""
        payload = getattr(self, "_latest_update_payload", None) or {}
        url = str(payload.get("release_url", "") or "")
        if not url:
            url = str((payload.get("asset") or {}).get("url", "") or "")
        if not url.startswith("https://github.com/SysAdminDoc/StreamKeep/releases/"):
            self._set_status("The release link was not valid.", "error")
            return
        if QDesktopServices.openUrl(QUrl(url)):
            self._set_status("Opened the release page for manual verification.", "success")
        else:
            self._set_status("Could not open the release page.", "error")

    def _on_update_dismiss(self):
        payload = getattr(self, "_latest_update_payload", None) or {}
        self._config["dismissed_update_tag"] = payload.get("tag", "")
        self._persist_config()
        self.update_banner.setVisible(False)
