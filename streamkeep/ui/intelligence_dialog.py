"""Consent and data-boundary dialogs for local-first intelligence actions."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)


class SummaryConsentDialog(QDialog):
    """Show the exact transcript boundary before a summary can run."""

    def __init__(self, parent, recording_dir: str):
        super().__init__(parent)
        self.setWindowTitle("Generate summary")
        self.setModal(True)
        self._recording_dir = str(recording_dir or "")
        self._preview = None

        root = QVBoxLayout(self)
        intro = QLabel(
            "StreamKeep processes transcripts locally by default. Review the "
            "exact payload below before any cloud provider is used."
        )
        intro.setWordWrap(True)
        intro.setObjectName("sectionBody")
        root.addWidget(intro)

        form = QFormLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.setAccessibleName("Summary provider profile")
        self.profile_combo.addItem("Ollama (local)", "")
        try:
            from ..intelligence.runtime import list_profiles

            for profile in list_profiles():
                self.profile_combo.addItem(
                    f"{profile['provider_label']} — "
                    f"{profile['label'] or profile['profile_id']}",
                    profile["profile_id"],
                )
        except Exception:
            pass  # safe: best-effort fallback; preserve the primary operation
        self.profile_combo.currentIndexChanged.connect(self._refresh_preview)
        form.addRow("Provider", self.profile_combo)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("Default model for the selected provider")
        self.model_edit.setAccessibleName("Summary model")
        self.model_edit.editingFinished.connect(self._refresh_preview)
        form.addRow("Model", self.model_edit)
        root.addLayout(form)

        self.boundary_label = QLabel("Preparing transcript preview…")
        self.boundary_label.setWordWrap(True)
        self.boundary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(self.boundary_label)

        self.redact_check = QCheckBox(
            "Redact URLs, email addresses, and recognizable bearer tokens"
        )
        self.redact_check.setAccessibleName("Redact transcript before cloud request")
        self.redact_check.toggled.connect(self._refresh_preview)
        root.addWidget(self.redact_check)

        self.payload_edit = QPlainTextEdit()
        self.payload_edit.setReadOnly(True)
        self.payload_edit.setAccessibleName("Exact transcript payload")
        self.payload_edit.setPlaceholderText("No transcript payload available.")
        self.payload_edit.setMinimumHeight(220)
        root.addWidget(self.payload_edit, 1)

        self.consent_check = QCheckBox(
            "I explicitly consent to send the displayed transcript to this cloud provider."
        )
        self.consent_check.setAccessibleName("Cloud transcript consent")
        self.consent_check.setVisible(False)
        self.consent_check.toggled.connect(self._update_buttons)
        root.addWidget(self.consent_check)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("errorText")
        root.addWidget(self.error_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Generate")
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self._accept_if_valid)
        root.addWidget(self.buttons)
        self._refresh_preview()

    def _request(self):
        profile_id = str(self.profile_combo.currentData() or "")
        if profile_id:
            return {"profile_id": profile_id}
        return {
            "provider": "ollama",
            "model": self.model_edit.text().strip(),
        }

    def _refresh_preview(self):
        try:
            from ..intelligence.runtime import get_runtime

            request = self._request()
            request["redact"] = self.redact_check.isChecked()
            self._preview = get_runtime().preview(self._recording_dir, **request)
            preview = self._preview
            self.boundary_label.setText(
                f"Provider: {preview['provider_label']}  |  Model: {preview['model']}\n"
                f"Payload: {preview['payload_chars']} characters  |  "
                f"SHA-256: {preview['payload_sha256']}\n"
                f"Redaction: {'applied' if preview['redaction_applied'] else 'off'}\n"
                f"Capability: {(preview.get('capability') or {}).get('detail', 'ready')}"
            )
            self.payload_edit.setPlainText(preview["payload"])
            self.consent_check.setVisible(bool(preview["requires_consent"]))
            self.error_label.clear()
        except Exception as error:
            self._preview = None
            self.boundary_label.setText("Transcript preview unavailable.")
            self.payload_edit.clear()
            self.consent_check.setVisible(False)
            self.error_label.setText(str(error))
        self._update_buttons()

    def _update_buttons(self):
        allowed = self._preview is not None
        if allowed and self._preview.get("requires_consent"):
            allowed = self.consent_check.isChecked()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(allowed)

    def _accept_if_valid(self):
        self._refresh_preview()
        if self._preview is None:
            return
        if self._preview.get("requires_consent") and not self.consent_check.isChecked():
            return
        self.accept()

    def request(self) -> dict:
        """Return the reviewed provider settings and one-use consent token."""
        if self._preview is None:
            return {}
        request = self._request()
        request.update({
            "model": self._preview["model"],
            "redact": bool(self._preview.get("redaction_applied")),
            "consent_token": self._preview.get("consent_token", ""),
        })
        return request
