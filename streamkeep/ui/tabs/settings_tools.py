"""Template, hook, converter, and config-transfer Settings handlers."""

import json
import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QTableWidgetItem

from ...config import save_config as _save_config
from ...postprocess import AUDIO_EXTS, VIDEO_EXTS, ConvertWorker
from ...utils import default_output_dir as _default_output_dir
from ..widgets import ask_premium_confirmation


class _DenoInstallWorker(QThread):
    """Run the explicit Deno archive/download action off the GUI thread."""

    result = pyqtSignal(bool, str)

    def __init__(self, archive_path="", parent=None):
        super().__init__(parent)
        self.archive_path = str(archive_path or "")

    def run(self):
        try:
            from ...javascript_runtime import install_managed_deno

            info = install_managed_deno(self.archive_path or None)
            self.result.emit(
                True,
                f"Deno {info.get('version', '')} installed from "
                f"{info.get('source', 'managed')}.",
            )
        except Exception as error:
            self.result.emit(False, str(error))


class SettingsToolsMixin:
    """Advanced templates/hooks plus manual conversion and config transfer."""

    @staticmethod
    def _plugin_contract_text(report):
        permissions = report.get("permissions") or []
        permission_text = ", ".join(str(item) for item in permissions) or "none"
        dependencies = report.get("dependencies") or []
        dependency_text = []
        for dependency in dependencies:
            label = str(dependency.get("name", ""))
            minimum = str(dependency.get("minimum_version", "") or "")
            if minimum:
                label += f" >= {minimum}"
            dependency_text.append(label)
        dependency_text = ", ".join(dependency_text) or "none"
        compatibility = report.get("compatibility") or {}
        compatibility_text = str(
            compatibility.get("range", "Any StreamKeep version")
        )
        entrypoints = report.get("entrypoints") or []
        entrypoint_text = ", ".join(
            f"{item.get('type', '?')}:{item.get('entrypoint', '?')}"
            for item in entrypoints
        ) or "none"
        return {
            "permissions": permission_text,
            "dependencies": dependency_text,
            "compatibility": compatibility_text,
            "entrypoints": entrypoint_text,
        }

    def _plugin_reports(self):
        from ... import plugins

        reports = []
        for plugin in plugins.discover_plugins():
            report = plugins.diagnose_plugin(plugin)
            report.update({
                "enabled": bool(plugin.get("enabled", False)),
                "trusted": bool(plugin.get("trusted", False)),
                "path": plugin.get("path", ""),
                "error": plugin.get("error", ""),
            })
            reports.append(report)
        return reports

    def _source_adapter_reports(self):
        """Return hot-reloaded YAML adapter diagnostics for Settings tooling."""
        from ... import plugins

        return plugins.declarative_adapter_diagnostics(
            config=getattr(self, "_config", None),
        )

    def _settings_browse_file(self, line_edit, title="Select file"):
        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            line_edit.text(),
            "Whisper model (*.bin *.gguf);;All files (*)",
        )
        if path:
            line_edit.setText(path)

    def _refresh_transcription_runtime_controls(self):
        """Refresh the visible FFmpeg whisper capability diagnostic."""
        label = getattr(self, "whisper_ffmpeg_status", None)
        if label is None:
            return
        from ...capabilities import get_runtime_capabilities

        registry = get_runtime_capabilities(
            refresh=True, config=getattr(self, "_config", None),
        )
        self._runtime_registry_snapshot = registry
        record = registry.get("ffmpeg_whisper", {})
        if record.get("supported"):
            label.setText(
                f"Ready: FFmpeg {record.get('version') or 'unknown'} exposes "
                f"whisper and will use {record.get('model_path')}."
            )
        else:
            label.setText(
                str(record.get("detail") or "FFmpeg whisper backend unavailable.")
            )
        label.setToolTip(str(record.get("repair") or ""))

    @staticmethod
    def _plugin_state_label(report):
        if report.get("error") or not report.get("compatible"):
            return "Needs attention"
        if report.get("enabled"):
            if report.get("trust_reviewed"):
                return "Enabled"
            return "Review required"
        if report.get("trust_reviewed"):
            return "Disabled"
        return "Review required"

    def _refresh_plugin_trust_ui(self):
        self._refresh_source_adapter_ui()
        if not hasattr(self, "plugin_trust_table"):
            return
        reports = self._plugin_reports()
        self._plugin_trust_snapshot = reports
        table = self.plugin_trust_table
        table.blockSignals(True)
        table.clearContents()
        table.setRowCount(len(reports))
        for row, report in enumerate(reports):
            contract = self._plugin_contract_text(report)
            values = (
                f"{report.get('name') or report.get('id') or 'Unknown'} "
                f"v{report.get('version', '?')}",
                contract["permissions"],
                contract["dependencies"],
                contract["compatibility"],
                contract["entrypoints"],
                self._plugin_state_label(report),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, report.get("id", ""))
                table.setItem(row, column, item)
        table.blockSignals(False)
        table.setVisible(bool(reports))
        empty_state = getattr(self, "plugin_trust_empty_state", None)
        if empty_state is not None:
            empty_state.setVisible(not reports)
        if reports:
            table.selectRow(0)
        else:
            self._on_plugin_selection_changed()

    def _selected_plugin_report(self):
        table = getattr(self, "plugin_trust_table", None)
        if table is None or table.currentRow() < 0:
            return None
        item = table.item(table.currentRow(), 0)
        plugin_id = item.data(Qt.ItemDataRole.UserRole) if item else ""
        return next(
            (
                report for report in getattr(self, "_plugin_trust_snapshot", [])
                if report.get("id") == plugin_id
            ),
            None,
        )

    def _on_plugin_selection_changed(self):
        report = self._selected_plugin_report()
        if report is None:
            self.plugin_enable_btn.setEnabled(False)
            self.plugin_disable_btn.setEnabled(False)
            self.plugin_trust_status.setText("No plugin selected.")
            return
        contract = self._plugin_contract_text(report)
        review_state = (
            "Current contract approved."
            if report.get("trust_reviewed")
            else "Explicit review is required before this plugin can run."
        )
        self.plugin_trust_status.setText(
            f"{report.get('name') or report.get('id')}: "
            f"{review_state} Permissions: {contract['permissions']}. "
            f"Entry points: {contract['entrypoints']}."
        )
        self.plugin_enable_btn.setEnabled(bool(report.get("compatible")))
        self.plugin_disable_btn.setEnabled(bool(report.get("enabled")))

    def _on_plugin_refresh_clicked(self):
        self._refresh_plugin_trust_ui()
        adapter_report = self._source_adapter_snapshot
        adapter_count = len(adapter_report.get("adapters", []))
        pending_count = len(adapter_report.get("pending_review", []))
        error_count = len(adapter_report.get("errors", []))
        suffix = (
            f" Declarative source adapters: {adapter_count} active, "
            f"{pending_count} awaiting review, {error_count} error(s)."
        )
        self._set_status(
            "Plugin and source-adapter diagnostics refreshed." + suffix,
            "warning" if (error_count or pending_count) else "info",
        )

    def _on_plugin_enable_clicked(self):
        from ... import plugins

        selected = self._selected_plugin_report()
        if selected is None:
            self._set_status("Select a plugin to review first.", "warning")
            return
        # Re-read the manifest immediately before the confirmation so the
        # approval is for the contract on disk, not a stale table row.
        fresh = next(
            (report for report in self._plugin_reports()
             if report.get("id") == selected.get("id")),
            None,
        )
        if fresh is None:
            self._refresh_plugin_trust_ui()
            self._set_status("Plugin is no longer available.", "error")
            return
        if not fresh.get("compatible"):
            detail = "; ".join(fresh.get("errors") or []) or "incompatible contract"
            self._set_status(f"Cannot enable plugin: {detail}", "error")
            return
        contract = self._plugin_contract_text(fresh)
        if not ask_premium_confirmation(
            self,
            title=f"Review {fresh.get('name') or fresh.get('id')} plugin",
            body=(
                "This plugin is requesting the contract below. StreamKeep will "
                "remember this exact contract and ask again if it changes.\n\n"
                f"Permissions: {contract['permissions']}\n"
                f"Dependencies: {contract['dependencies']}\n"
                f"Compatibility: {contract['compatibility']}\n"
                f"Entry points: {contract['entrypoints']}"
            ),
            eyebrow="PLUGIN TRUST",
            badge_text="Explicit review",
            tone="warning" if fresh.get("permissions") else "info",
            summary_title="Review before enabling",
            summary_body=(
                "Only the declared adapter contract is approved. A later "
                "permission or contract change requires this review again."
            ),
            primary_label="Enable plugin",
            secondary_label="Keep disabled",
            default_action="secondary",
            min_width=650,
        ):
            self._set_status("Plugin review cancelled; it remains disabled.", "idle")
            return
        plugin_id = str(fresh.get("id", ""))
        if not plugins.mark_trusted(plugin_id, True):
            self._set_status("Plugin review could not be saved.", "error")
            return
        if not plugins.set_plugin_enabled(plugin_id, True):
            self._set_status("Plugin trust saved, but enabling it failed.", "error")
            self._refresh_plugin_trust_ui()
            return
        self._log(
            f"[PLUGIN] Enabled after explicit contract review: {plugin_id} "
            f"({fresh.get('contract_fingerprint', '')[:12]})"
        )
        self._refresh_plugin_trust_ui()
        self._set_status(
            f"{fresh.get('name') or plugin_id} enabled after contract review.",
            "success",
        )

    def _on_plugin_disable_clicked(self):
        from ... import plugins

        report = self._selected_plugin_report()
        if report is None:
            self._set_status("Select a plugin to disable first.", "warning")
            return
        plugin_id = str(report.get("id", ""))
        if not plugins.set_plugin_enabled(plugin_id, False):
            self._set_status("Plugin could not be disabled.", "error")
            return
        self._log(f"[PLUGIN] Disabled: {plugin_id}")
        self._refresh_plugin_trust_ui()
        self._set_status(f"{report.get('name') or plugin_id} disabled.", "success")

    # ── Declarative source adapter review (V147) ────────────────────

    @staticmethod
    def _source_adapter_request_summary(adapter):
        """One-line "what it would call" digest for the table cell."""
        operations = adapter.get("operations") or []
        if not operations:
            return "no requests declared"
        return "; ".join(
            f"{operation.get('operation', '?')}: "
            f"{operation.get('method', 'GET')} {operation.get('url', '')}"
            for operation in operations
        )

    @staticmethod
    def _source_adapter_contract_text(adapter):
        """Spell the contract out the way the operator has to read it."""
        hosts = ", ".join(adapter.get("hosts") or []) or "(none declared)"
        lines = [f"Hosts it may contact: {hosts}", "", "Requests it would issue:"]
        operations = adapter.get("operations") or []
        if not operations:
            lines.append("    (none declared)")
        for operation in operations:
            lines.append(
                f"    {operation.get('operation', '?')} — "
                f"{operation.get('method', 'GET')} {operation.get('url', '')}"
            )
            for header in operation.get("headers") or []:
                lines.append(f"        header: {header}")
            params = operation.get("params") or []
            if params:
                lines.append(f"        query parameters: {', '.join(params)}")
        source = str(adapter.get("source") or "")
        if source:
            lines.extend(["", f"Definition: {source}"])
        return "\n".join(lines)

    def _source_adapter_rows(self):
        """Merge pending and approved adapters into one review list."""
        report = self._source_adapter_reports()
        rows = [
            {**adapter, "reviewed": False}
            for adapter in report.get("pending_review", [])
        ]
        rows.extend(
            {**adapter, "reviewed": True} for adapter in report.get("adapters", [])
        )
        return report, rows

    # ── Adaptive rate governance (V162) ─────────────────────────────

    def _refresh_rate_governor_ui(self):
        from ...governor import public_view

        status = getattr(self, "rate_governor_status", None)
        if status is None:
            return
        view = public_view()
        if not view["enabled"]:
            status.setText(
                "Automatic backoff is off; a throttling host will keep being "
                "asked at the configured pace."
            )
            return
        hosts = view["hosts"]
        if not hosts:
            status.setText("No host is being throttled.")
            return
        lines = [
            f"{entry['host']}: {entry['concurrency']} at once"
            + (
                f", {entry['delay_seconds']:g}s between requests"
                if entry["delay_seconds"] else ""
            )
            + (
                f" ({entry['classification']})"
                if entry.get("classification") else ""
            )
            for entry in hosts[:5]
        ]
        if len(hosts) > 5:
            lines.append(f"and {len(hosts) - 5} more")
        status.setText("Backing off — " + "; ".join(lines))

    def _on_rate_governor_toggled(self, checked):
        from ...governor import configure

        enabled = bool(checked)
        self._config["rate_governor_enabled"] = enabled
        configure(
            enabled=enabled,
            default_concurrency=int(self._config.get("max_concurrent", 4) or 4),
        )
        if not _save_config(self._config):
            self._set_status(
                "Could not save the rate governance setting.", "error",
            )
            return
        self._refresh_rate_governor_ui()
        self._set_status(
            "Automatic backoff is on." if enabled
            else "Automatic backoff is off.",
            "info",
        )

    # ── Per-source download engine (V165) ───────────────────────────

    def _source_engine_rows(self):
        """Platforms worth offering a choice for, worst first.

        Every platform with a recorded failure circuit is listed, plus any
        platform that already carries an override, so a choice can always be
        undone even after its failures have aged out of the ledger.
        """
        from ...capabilities import load_source_engine_overrides

        overrides = load_source_engine_overrides(self._config)
        rows = {}
        try:
            from ... import db
            circuits = db.load_retry_circuits() or []
        except Exception:  # a ledger read must not empty the chooser
            circuits = []
        for circuit in circuits:
            if not isinstance(circuit, dict):
                continue
            label = str(circuit.get("source_label") or "").strip()
            if not label:
                continue
            key = label.casefold()
            try:
                failures = int(circuit.get("failure_count", 0) or 0)
            except (TypeError, ValueError):
                failures = 0
            existing = rows.get(key)
            if existing is None or failures > existing["failures"]:
                rows[key] = {
                    "key": key,
                    "label": label,
                    "failures": failures,
                    "engine": str(circuit.get("engine") or ""),
                }
        for key, engine in overrides.items():
            rows.setdefault(key, {
                "key": key, "label": key, "failures": 0, "engine": "",
            })
        ordered = sorted(
            rows.values(), key=lambda row: (-row["failures"], row["label"])
        )
        for row in ordered:
            row["override"] = overrides.get(row["key"], "")
        return ordered

    def _refresh_source_engine_ui(self):
        from PyQt6.QtWidgets import QComboBox

        from ...capabilities import available_engines

        table = getattr(self, "source_engine_table", None)
        if table is None:
            return
        rows = self._source_engine_rows()
        engines = available_engines()
        table.blockSignals(True)
        table.clearContents()
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            failing = row.get("engine") or ""
            summary = str(row["failures"])
            if failing:
                summary += f" ({failing})"
            for column, value in enumerate((row["label"], summary)):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(index, column, item)
            combo = QComboBox()
            for engine_id, label in engines.items():
                combo.addItem(label, userData=engine_id)
            current = combo.findData(row.get("override", ""))
            combo.setCurrentIndex(current if current >= 0 else 0)
            combo.currentIndexChanged.connect(
                lambda _, platform=row["key"], box=combo:
                self._on_source_engine_changed(platform, box)
            )
            table.setCellWidget(index, 2, combo)
        table.blockSignals(False)
        table.setVisible(bool(rows))
        empty_state = getattr(self, "source_engine_empty_state", None)
        if empty_state is not None:
            empty_state.setVisible(not rows)
        self._update_source_engine_status()

    def _update_source_engine_status(self):
        from ...capabilities import (
            DOWNLOAD_ENGINES, load_source_engine_overrides,
        )

        status = getattr(self, "source_engine_status", None)
        if status is None:
            return
        overrides = load_source_engine_overrides(self._config)
        if not overrides:
            status.setText(
                "No source engine overrides; every platform uses the global "
                "engine settings."
            )
            return
        named = ", ".join(
            f"{platform} to {DOWNLOAD_ENGINES.get(engine, engine)}"
            for platform, engine in sorted(overrides.items())
        )
        status.setText(f"Overridden: {named}.")

    def _on_source_engine_changed(self, platform, combo):
        from ...capabilities import DOWNLOAD_ENGINES, set_source_engine

        engine = combo.currentData() or ""
        try:
            set_source_engine(self._config, platform, engine)
        except ValueError as error:
            self._set_status(str(error), "error")
            return
        if not _save_config(self._config):
            self._set_status(
                "Could not save the source engine override.", "error",
            )
            return
        self._update_source_engine_status()
        label = DOWNLOAD_ENGINES.get(engine, engine)
        self._set_status(
            f"{platform} now uses {label}." if engine
            else f"{platform} returned to the global engine settings.",
            "info",
        )

    def _on_source_engine_refresh_clicked(self):
        self._refresh_source_engine_ui()
        count = len(self._source_engine_rows())
        self._set_status(
            f"Source engines rescanned: {count} platform(s) with recorded "
            "failures or an override.",
            "info",
        )

    def _refresh_source_adapter_ui(self):
        report, rows = self._source_adapter_rows()
        self._source_adapter_snapshot = report
        self._source_adapter_rows_snapshot = rows
        table = getattr(self, "source_adapter_table", None)
        if table is None:
            return
        table.blockSignals(True)
        table.clearContents()
        table.setRowCount(len(rows))
        for row, adapter in enumerate(rows):
            values = (
                f"{adapter.get('name') or adapter.get('id') or 'Unknown'} "
                f"v{adapter.get('version', '?')}",
                ", ".join(adapter.get("hosts") or []) or "(none)",
                self._source_adapter_request_summary(adapter),
                str(adapter.get("source") or "config"),
                "Approved" if adapter.get("reviewed") else "Review required",
            )
            # The request and definition columns are wider than any sane column
            # width, so the full contract rides along as the row's tooltip.
            tooltip = self._source_adapter_contract_text(adapter)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(tooltip)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, adapter.get("id", ""))
                table.setItem(row, column, item)
        table.blockSignals(False)
        table.setVisible(bool(rows))
        empty_state = getattr(self, "source_adapter_empty_state", None)
        if empty_state is not None:
            empty_state.setVisible(not rows)
        if rows:
            table.selectRow(0)
        else:
            self._on_source_adapter_selection_changed()

    def _selected_source_adapter(self):
        table = getattr(self, "source_adapter_table", None)
        if table is None or table.currentRow() < 0:
            return None
        item = table.item(table.currentRow(), 0)
        adapter_id = item.data(Qt.ItemDataRole.UserRole) if item else ""
        return next(
            (
                adapter
                for adapter in getattr(self, "_source_adapter_rows_snapshot", [])
                if adapter.get("id") == adapter_id
            ),
            None,
        )

    def _on_source_adapter_selection_changed(self):
        adapter = self._selected_source_adapter()
        approve_btn = getattr(self, "source_adapter_approve_btn", None)
        revoke_btn = getattr(self, "source_adapter_revoke_btn", None)
        status = getattr(self, "source_adapter_status", None)
        if adapter is None:
            if approve_btn is not None:
                approve_btn.setEnabled(False)
            if revoke_btn is not None:
                revoke_btn.setEnabled(False)
            if status is not None:
                errors = len(
                    getattr(self, "_source_adapter_snapshot", {}).get("errors", [])
                )
                status.setText(
                    f"No declarative source adapters found. {errors} definition(s) "
                    "failed to load." if errors
                    else "No declarative source adapters found."
                )
            return
        reviewed = bool(adapter.get("reviewed"))
        if approve_btn is not None:
            approve_btn.setEnabled(not reviewed)
        if revoke_btn is not None:
            revoke_btn.setEnabled(reviewed)
        if status is not None:
            state = (
                "Current contract approved; this adapter is active."
                if reviewed
                else "Inert until reviewed — it will not issue any request."
            )
            status.setText(
                f"{adapter.get('name') or adapter.get('id')}: {state} "
                + self._source_adapter_request_summary(adapter)
            )

    def _on_source_adapter_refresh_clicked(self):
        from ... import declarative

        declarative.invalidate_registry_cache()
        self._refresh_source_adapter_ui()
        report = self._source_adapter_snapshot
        pending = len(report.get("pending_review", []))
        active = len(report.get("adapters", []))
        errors = len(report.get("errors", []))
        self._set_status(
            f"Source adapters rescanned: {active} active, {pending} awaiting "
            f"review, {errors} error(s).",
            "warning" if (errors or pending) else "info",
        )

    def _on_source_adapter_approve_clicked(self):
        from ... import declarative

        selected = self._selected_source_adapter()
        if selected is None:
            self._set_status("Select a source adapter to review first.", "warning")
            return
        # Re-read from disk immediately before the confirmation so the approval
        # is for the contract that exists now, not the one the table was built
        # from — the file may have changed while the panel sat open.
        declarative.invalidate_registry_cache()
        _report, rows = self._source_adapter_rows()
        fresh = next(
            (adapter for adapter in rows
             if adapter.get("id") == selected.get("id")),
            None,
        )
        if fresh is None:
            self._refresh_source_adapter_ui()
            self._set_status("Source adapter is no longer available.", "error")
            return
        if fresh.get("reviewed"):
            self._refresh_source_adapter_ui()
            self._set_status("That contract is already approved.", "info")
            return
        if not ask_premium_confirmation(
            self,
            title=(
                f"Review {fresh.get('name') or fresh.get('id')} source adapter"
            ),
            body=(
                "This definition describes the outbound requests below. "
                "StreamKeep will remember this exact contract and make the "
                "adapter inert again if it changes.\n\n"
                + self._source_adapter_contract_text(fresh)
            ),
            eyebrow="SOURCE ADAPTER REVIEW",
            badge_text="Explicit review",
            tone="warning",
            summary_title="Review before activating",
            summary_body=(
                "Only the requests shown are approved. Any change to the hosts, "
                "method, URL, headers, or query parameters requires this review "
                "again."
            ),
            primary_label="Approve adapter",
            secondary_label="Keep inert",
            default_action="secondary",
            min_width=650,
        ):
            self._set_status(
                "Source adapter review cancelled; it remains inert.", "idle",
            )
            return
        adapter_id = str(fresh.get("id", ""))
        fingerprint = str(fresh.get("contract_fingerprint", ""))
        self._config = declarative.approve_source_adapter(
            adapter_id, fingerprint, self._config,
        )
        if not _save_config(self._config):
            self._set_status("Source adapter approval could not be saved.", "error")
            return
        self._log(
            f"[ADAPTER] Approved after explicit contract review: {adapter_id} "
            f"({fingerprint[:12]})"
        )
        self._refresh_source_adapter_ui()
        self._set_status(
            f"{fresh.get('name') or adapter_id} approved and now active.",
            "success",
        )

    def _on_source_adapter_revoke_clicked(self):
        from ... import declarative

        adapter = self._selected_source_adapter()
        if adapter is None:
            self._set_status("Select a source adapter to revoke first.", "warning")
            return
        adapter_id = str(adapter.get("id", ""))
        self._config = declarative.revoke_source_adapter(adapter_id, self._config)
        if not _save_config(self._config):
            self._set_status("Source adapter revocation could not be saved.", "error")
            return
        self._log(f"[ADAPTER] Approval revoked; now inert: {adapter_id}")
        self._refresh_source_adapter_ui()
        self._set_status(
            f"{adapter.get('name') or adapter_id} is inert until reviewed again.",
            "warning",
        )

    def _on_javascript_runtime_preference_changed(self, index):
        value = str(self.deno_preference_combo.itemData(index) or "path")
        self._config["javascript_runtime_preference"] = (
            "managed" if value == "managed" else "path"
        )
        self._persist_config()
        from ...capabilities import invalidate_runtime_capabilities_cache

        invalidate_runtime_capabilities_cache()
        self._refresh_deno_runtime_controls()
        self._set_status(
            "JavaScript runtime preference saved; managed Deno is preferred."
            if value == "managed"
            else "JavaScript runtime preference saved; PATH is preferred.",
            "success",
        )

    def _on_browse_ytdlp_executable(self):
        from PyQt6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "Select a yt-dlp executable", "",
            "Executables (*.exe *.cmd *.bat);;All files (*)",
        )
        if path:
            self.ytdlp_external_input.setText(path)
            self._on_ytdlp_channel_changed()

    def _on_ytdlp_channel_changed(self, _index=None):
        """Save the channel and re-probe, so the switch is visible immediately."""
        from ...capabilities import (
            invalidate_runtime_capabilities_cache,
            normalize_ytdlp_channel,
        )

        combo = getattr(self, "ytdlp_channel_combo", None)
        if combo is None:
            return
        channel = normalize_ytdlp_channel(combo.itemData(combo.currentIndex()))
        command = str(self.ytdlp_external_input.text() or "").strip()
        if (self._config.get("ytdlp_channel") == channel
                and str(self._config.get("ytdlp_external_command", "") or "") == command):
            return
        self._config["ytdlp_channel"] = channel
        self._config["ytdlp_external_command"] = command
        self._persist_config()
        invalidate_runtime_capabilities_cache()
        self._refresh_ytdlp_channel_controls()

    def _refresh_ytdlp_channel_controls(self):
        """Report the channel in use, which is not always the one requested."""
        label = getattr(self, "ytdlp_channel_status", None)
        if label is None:
            return
        from ...extractors.ytdlp import ytdlp_runtime_status

        status = ytdlp_runtime_status(self._config, check_updates=True)
        self._ytdlp_status_snapshot = status
        channel = status.get("yt_dlp_channel", "bundled")
        requested = status.get("yt_dlp_channel_requested", channel)
        version = status.get("yt_dlp_version") or "not available"
        text = f"Using the {channel} yt-dlp {version}."
        update = status.get("yt_dlp_update") or {}
        update_summary = str(update.get("summary") or "unknown")
        text += f" Update: {update_summary}."
        detail = str(status.get("yt_dlp_channel_detail") or "").strip()
        if detail:
            text += f" {detail}"
        label.setText(text)
        fell_back = requested != channel
        stale = update.get("state") == "stale"
        label.setObjectName("warningText" if fell_back or stale else "subtleText")
        label.setToolTip(" ".join(filter(None, (
            str(status.get("yt_dlp_external_problem") or ""),
            str(update.get("warning") or ""),
        ))))
        # Re-polish: an object-name change does not restyle an existing widget.
        label.style().unpolish(label)
        label.style().polish(label)
        if fell_back:
            self._set_status(
                "The external yt-dlp could not be used; the bundled build is "
                "still active. " + str(status.get("yt_dlp_external_problem") or ""),
                "warning",
            )
        elif stale:
            self._set_status(str(update.get("warning") or ""), "warning")
        enabled = channel == "external" or requested == "external"
        for widget_name in ("ytdlp_external_input", "ytdlp_external_browse"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _refresh_deno_runtime_controls(self):
        if not hasattr(self, "deno_runtime_status"):
            return
        from ...capabilities import get_runtime_capabilities
        from ...javascript_runtime import get_managed_deno_info

        registry = get_runtime_capabilities(refresh=True, config=self._config)
        self._runtime_registry_snapshot = registry
        runtime = registry.get("javascript", {})
        source = runtime.get("runtime_source") or runtime.get("provenance") or "none"
        try:
            managed = get_managed_deno_info()
        except Exception as error:
            managed = {
                "available": False,
                "path": "",
                "version": "",
                "source": "",
                "detail": f"Managed Deno status unavailable: {error}",
            }
        managed_text = "Managed Deno: not installed."
        if managed.get("available"):
            managed_source = managed.get("source") or managed.get("provenance") or "managed"
            managed_text = (
                f"Managed Deno {managed.get('version') or 'unknown'} "
                f"({managed_source}) at {managed.get('path')}."
            )
        self.deno_runtime_status.setText(
            f"Selected {runtime.get('display_name') or 'JavaScript runtime'} "
            f"{runtime.get('version') or 'not available'} — source: {source}. "
            f"{runtime.get('path') or runtime.get('detail') or ''} "
            f"{managed_text}"
        )
        self.deno_remove_btn.setEnabled(bool(managed.get("available")))
        preference = str(
            self._config.get("javascript_runtime_preference", "path") or "path"
        )
        index = self.deno_preference_combo.findData(
            "managed" if preference == "managed" else "path"
        )
        if index >= 0 and self.deno_preference_combo.currentIndex() != index:
            self.deno_preference_combo.blockSignals(True)
            self.deno_preference_combo.setCurrentIndex(index)
            self.deno_preference_combo.blockSignals(False)

    def _start_deno_install(self, archive_path=""):
        worker = getattr(self, "_deno_worker", None)
        if worker is not None and worker.isRunning():
            self._set_status("A Deno installation is already running.", "warning")
            return
        self._deno_worker = _DenoInstallWorker(archive_path, self)
        self._deno_worker.result.connect(self._on_deno_install_result)
        busy_done = self._begin_background_activity("Installing Deno runtime…")
        self._deno_worker.finished.connect(busy_done)
        self._deno_worker.finished.connect(self._clear_deno_worker)
        self._deno_worker.finished.connect(self._deno_worker.deleteLater)
        self.deno_install_btn.setEnabled(False)
        self.deno_archive_btn.setEnabled(False)
        self._set_status("Installing the pinned Deno runtime...", "working")
        self._deno_worker.start()

    def _clear_deno_worker(self):
        self._deno_worker = None

    def _on_deno_install_result(self, ok, message):
        self.deno_install_btn.setEnabled(True)
        self.deno_archive_btn.setEnabled(True)
        if ok:
            from ...capabilities import invalidate_runtime_capabilities_cache

            invalidate_runtime_capabilities_cache()
            self._refresh_deno_runtime_controls()
            self._set_status(message, "success")
            self._log(f"[RUNTIME] {message}")
        else:
            self._set_status(f"Deno installation failed: {message}", "error")
            self._log(f"[RUNTIME] Deno installation failed: {message}")

    def _on_install_deno_clicked(self):
        self._start_deno_install()

    def _on_install_deno_archive_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select pinned Deno archive",
            str(Path.home()),
            "Deno ZIP archive (*.zip);;All files (*)",
        )
        if path:
            self._start_deno_install(path)

    def _on_remove_deno_clicked(self):
        try:
            from ...javascript_runtime import remove_managed_deno

            removed = remove_managed_deno()
        except Exception as error:
            self._set_status(f"Deno removal failed: {error}", "error")
            return
        from ...capabilities import invalidate_runtime_capabilities_cache

        invalidate_runtime_capabilities_cache()
        self._refresh_deno_runtime_controls()
        self._set_status(
            "Managed Deno removed." if removed else "No managed Deno was installed.",
            "success" if removed else "warning",
        )

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

    # ── Smart Mode URL profiles (V16) ───────────────────────────────

    def _refresh_smart_profiles(self, selected=""):
        from ...smart_mode import load_profiles

        combo = getattr(self, "smart_profile_combo", None)
        if combo is None:
            return
        profiles = load_profiles(self._config)
        self._config["smart_profiles"] = profiles
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("New profile", userData="")
        for profile in profiles:
            combo.addItem(profile["name"], userData=profile["name"])
        index = combo.findData(selected)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)
        self._on_smart_profile_selected()
        self._refresh_smart_profile_hint(
            self.url_input.text().strip() if hasattr(self, "url_input") else ""
        )

    def _on_smart_profile_selected(self):
        combo = getattr(self, "smart_profile_combo", None)
        if combo is None:
            return
        selected = str(combo.currentData() or "")
        profile = next(
            (
                value for value in self._config.get("smart_profiles", [])
                if isinstance(value, dict) and value.get("name") == selected
            ),
            None,
        )
        fields = (
            "smart_profile_name_input", "smart_profile_patterns_edit",
            "smart_profile_output_input", "smart_profile_quality_input",
            "smart_profile_folder_input", "smart_profile_file_input",
            "smart_profile_template_input", "smart_profile_auth_input",
            "smart_profile_proxy_input",
        )
        if profile is None:
            self.smart_profile_name_input.clear()
            self.smart_profile_patterns_edit.clear()
            self.smart_profile_enabled_check.setChecked(True)
            for name in fields[2:]:
                getattr(self, name).clear()
            self.smart_profile_delete_btn.setEnabled(False)
            self.smart_profile_status.setText(
                "New profile. Save at least one URL pattern."
            )
            return
        overrides = profile.get("overrides", {})
        self.smart_profile_enabled_check.setChecked(bool(profile.get("enabled", True)))
        self.smart_profile_name_input.setText(str(profile.get("name", "")))
        self.smart_profile_patterns_edit.setPlainText(
            "\n".join(str(pattern) for pattern in profile.get("patterns", []))
        )
        self.smart_profile_output_input.setText(str(overrides.get("output_dir", "")))
        self.smart_profile_quality_input.setText(str(overrides.get("quality", "")))
        self.smart_profile_folder_input.setText(str(overrides.get("folder_template", "")))
        self.smart_profile_file_input.setText(str(overrides.get("file_template", "")))
        self.smart_profile_template_input.setText(
            str(overrides.get("ytdlp_template_name", ""))
        )
        self.smart_profile_auth_input.setText(str(overrides.get("auth_profile_id", "")))
        self.smart_profile_proxy_input.setText(str(overrides.get("proxy", "")))
        self.smart_profile_delete_btn.setEnabled(True)
        state = "enabled" if profile.get("enabled", True) else "disabled"
        self.smart_profile_status.setText(
            f"{state.capitalize()} profile. First matching profile wins."
        )

    def _smart_profile_from_editor(self):
        from ...smart_mode import normalize_profile

        candidate = {
            "name": self.smart_profile_name_input.text().strip(),
            "enabled": self.smart_profile_enabled_check.isChecked(),
            "patterns": self.smart_profile_patterns_edit.toPlainText().splitlines(),
            "overrides": {
                "output_dir": self.smart_profile_output_input.text().strip(),
                "quality": self.smart_profile_quality_input.text().strip(),
                "folder_template": self.smart_profile_folder_input.text().strip(),
                "file_template": self.smart_profile_file_input.text().strip(),
                "ytdlp_template_name": self.smart_profile_template_input.text().strip(),
                "auth_profile_id": self.smart_profile_auth_input.text().strip(),
                "proxy": self.smart_profile_proxy_input.text().strip(),
            },
        }
        return normalize_profile(candidate)

    def _on_smart_profile_save(self):
        from ...download_options import resolve_ytdlp_arg_template

        profile = self._smart_profile_from_editor()
        if profile is None:
            self.smart_profile_status.setText(
                "Enter a name and at least one valid URL pattern."
            )
            self._set_status("Smart Mode profile is incomplete.", "warning")
            return
        template_name = profile["overrides"].get("ytdlp_template_name", "")
        if template_name:
            try:
                resolve_ytdlp_arg_template(
                    self._config.get("ytdlp_arg_templates", {}), template_name
                )
            except ValueError as error:
                self.smart_profile_status.setText(str(error))
                self._set_status(str(error), "warning")
                return
        profiles = list(self._config.get("smart_profiles", []))
        selected = str(self.smart_profile_combo.currentData() or "")
        replaced = False
        updated = []
        for existing in profiles:
            if not isinstance(existing, dict):
                continue
            if existing.get("name") in {selected, profile["name"]}:
                if not replaced:
                    updated.append(profile)
                    replaced = True
                continue
            updated.append(existing)
        if not replaced:
            updated.append(profile)
        self._config["smart_profiles"] = updated
        self._refresh_smart_profiles(profile["name"])
        self._persist_config()
        self._set_status(
            f'Saved Smart Mode profile "{profile["name"]}".', "success"
        )

    def _on_smart_profile_delete(self):
        combo = getattr(self, "smart_profile_combo", None)
        if combo is None:
            return
        selected = str(combo.currentData() or "")
        if not selected:
            return
        self._config["smart_profiles"] = [
            profile for profile in self._config.get("smart_profiles", [])
            if not isinstance(profile, dict) or profile.get("name") != selected
        ]
        self._refresh_smart_profiles()
        self._persist_config()
        self._set_status(f'Deleted Smart Mode profile "{selected}".', "success")

    def _on_smart_mode_toggled(self, checked, source=""):
        del source
        enabled = bool(checked)
        self._config["smart_mode"] = enabled
        for name in ("smart_mode_download_check", "smart_mode_settings_check"):
            widget = getattr(self, name, None)
            if widget is not None and widget.isChecked() != enabled:
                widget.blockSignals(True)
                widget.setChecked(enabled)
                widget.blockSignals(False)
        self._refresh_smart_profile_hint(
            self.url_input.text().strip() if hasattr(self, "url_input") else ""
        )
        self._persist_config()

    def _refresh_smart_profile_hint(self, url=""):
        label = getattr(self, "smart_profile_hint", None)
        if label is None:
            return
        if not bool(self._config.get("smart_mode", False)):
            label.setText("Smart Mode off")
            return
        from ...smart_mode import resolve_profile
        profile = resolve_profile(url, self._config) if url else None
        if profile is None:
            label.setText("Smart Mode on — no profile matches this URL")
        else:
            label.setText(f"Smart profile: {profile['name']}")

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

    # ── Automatic backups (V51) ───────────────────────────────────

    def _on_browse_auto_backup_dir(self):
        """Pick the rotation directory for automatic profile backups."""
        current = self.auto_backup_dir_input.text().strip()
        path = QFileDialog.getExistingDirectory(
            self, "Choose Backup Folder", current or str(Path.home()),
        )
        if path:
            self.auto_backup_dir_input.setText(path)

    def _refresh_backup_status(self):
        """Show last success, size, next run, and any failure reason."""
        from ... import db as _db
        from ...backup import backup_settings
        from ...utils import fmt_size

        label = getattr(self, "auto_backup_status_label", None)
        if label is None:
            return
        settings = backup_settings(self._config)
        view = _db.backup_state_public_view(_db.load_backup_state())
        if not settings["enabled"]:
            label.setText("Automatic backups are off.")
            return
        parts = []
        if view["last_success_at"]:
            size = fmt_size(view["last_size"]) if view["last_size"] else "unknown size"
            parts.append(
                f"Last backup {view['last_success_at']} "
                f"({view['last_name'] or 'archive'}, {size})"
            )
        else:
            parts.append("No successful backup yet")
        if view["running"]:
            parts.append("a backup is running now")
        elif view["next_run_at"]:
            parts.append(f"next run {view['next_run_at']}")
        if view["last_error"]:
            parts.append(
                f"last failure {view['last_failure_at']}: {view['last_error']}"
            )
        label.setText(". ".join(parts) + ".")

    def _on_backup_now(self):
        """Force the next scheduled backup to be due immediately."""
        from ... import db as _db
        from ...backup import backup_settings

        self._persist_config()
        settings = backup_settings(self._config)
        if not settings["enabled"]:
            self._set_status(
                "Enable automatic backups and save settings first.", "warning",
            )
            return
        _db.request_backup_now(cadence_seconds=settings["cadence_seconds"])
        self._tick_scheduled_backup()
        self._set_status("Backup requested; it runs on the next tick.", "info")
        self._refresh_backup_status()

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
