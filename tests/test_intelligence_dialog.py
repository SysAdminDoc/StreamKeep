from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QDialog, QDialogButtonBox

from streamkeep.intelligence import runtime as intelligence_runtime
from streamkeep.ui.intelligence_dialog import SummaryConsentDialog


def _preview(**overrides):
    value = {
        "provider_label": "Ollama (local)",
        "model": "llama3.2",
        "payload_chars": 18,
        "payload_sha256": "a" * 64,
        "redaction_applied": False,
        "capability": {"detail": "ready"},
        "payload": "local transcript text",
        "requires_consent": False,
        "consent_token": "",
    }
    value.update(overrides)
    return value


def test_local_summary_dialog_renders_and_accepts(qt_application, monkeypatch):
    previews = []

    class Runtime:
        def preview(self, recording_dir, **request):
            previews.append((recording_dir, request))
            return _preview(model=request.get("model") or "llama3.2")

    monkeypatch.setattr(intelligence_runtime, "get_runtime", lambda: Runtime())
    monkeypatch.setattr(intelligence_runtime, "list_profiles", lambda: [])
    dialog = SummaryConsentDialog(None, "C:/archive/recording")
    try:
        generate = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert generate.isEnabled()
        assert "Ollama (local)" in dialog.boundary_label.text()
        assert dialog.payload_edit.toPlainText() == "local transcript text"
        assert dialog.consent_check.isHidden()

        dialog._accept_if_valid()

        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.request() == {
            "provider": "ollama",
            "model": "llama3.2",
            "redact": False,
            "consent_token": "",
        }
        assert previews[-1][0] == "C:/archive/recording"
    finally:
        dialog.close()


def test_cloud_summary_requires_consent_and_returns_review_token(
    qt_application, monkeypatch,
):
    class Runtime:
        def preview(self, _recording_dir, **request):
            if request.get("profile_id"):
                return _preview(
                    provider_label="OpenAI",
                    model="gpt-5-mini",
                    redaction_applied=bool(request.get("redact")),
                    requires_consent=True,
                    consent_token="review-token",
                )
            return _preview()

    monkeypatch.setattr(intelligence_runtime, "get_runtime", lambda: Runtime())
    monkeypatch.setattr(
        intelligence_runtime,
        "list_profiles",
        lambda: [{
            "provider_label": "OpenAI",
            "label": "Cloud review",
            "profile_id": "cloud-profile",
        }],
    )
    dialog = SummaryConsentDialog(None, "C:/archive/recording")
    try:
        dialog.profile_combo.setCurrentIndex(1)
        generate = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert not dialog.consent_check.isHidden()
        assert not generate.isEnabled()

        dialog.redact_check.setChecked(True)
        dialog.consent_check.setChecked(True)

        assert generate.isEnabled()
        assert dialog.request() == {
            "profile_id": "cloud-profile",
            "model": "gpt-5-mini",
            "redact": True,
            "consent_token": "review-token",
        }
    finally:
        dialog.close()


def test_preview_failure_is_visible_and_dialog_owns_no_worker(
    qt_application, monkeypatch,
):
    class Runtime:
        def preview(self, *_args, **_kwargs):
            raise RuntimeError("transcript index unavailable")

    runtime = Runtime()
    monkeypatch.setattr(intelligence_runtime, "get_runtime", lambda: runtime)
    monkeypatch.setattr(intelligence_runtime, "list_profiles", lambda: [])
    dialog = SummaryConsentDialog(None, "C:/archive/recording")
    try:
        generate = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert dialog.boundary_label.text() == "Transcript preview unavailable."
        assert "index unavailable" in dialog.error_label.text()
        assert dialog.payload_edit.toPlainText() == ""
        assert not generate.isEnabled()
        assert dialog.request() == {}
        assert not dialog.findChildren(QThread)

        dialog._accept_if_valid()
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.close()
