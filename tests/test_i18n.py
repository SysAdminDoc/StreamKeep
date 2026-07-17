from pathlib import Path

from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from streamkeep import i18n
from streamkeep.i18n.extract_translations import extract_messages, update_catalogs


def test_compiled_spanish_translation_is_available(qt_application):
    assert "es" in i18n.available_languages()
    assert i18n.install_translator("es", qt_application)
    assert i18n.current_language() == "es"
    assert i18n.install_translator("en", qt_application)
    assert i18n.current_language() == "en"


def test_catalogs_cover_hand_authored_ui_and_match_frozen_assets():
    messages, _locations = extract_messages()
    assert len(messages) >= 1_000
    assert {"StreamKeep", "Status", "History", "Accessibility"} <= {
        message.context for message in messages
    }
    assert update_catalogs(check=True)

    i18n_dir = Path(i18n.__file__).parent
    assert {path.stem for path in i18n_dir.glob("streamkeep_*.ts")} == {
        path.stem for path in i18n_dir.glob("streamkeep_*.qm")
    }


def test_live_shell_and_dialog_retranslation_with_plural_context(qt_application):
    shell = QWidget()
    layout = QVBoxLayout(shell)
    download = QLabel("Download")
    settings = QPushButton("Settings")
    layout.addWidget(download)
    layout.addWidget(settings)
    i18n.translate_widget_tree(shell)

    dialog = QDialog()
    dialog.setWindowTitle("Notification Log")
    dialog_layout = QVBoxLayout(dialog)
    close = QPushButton("Close")
    dialog_layout.addWidget(close)
    i18n.translate_widget_tree(dialog)

    try:
        assert i18n.install_translator("es", qt_application)
        assert (download.text(), settings.text()) == ("Descargar", "Configuración")
        assert (dialog.windowTitle(), close.text()) == (
            "Registro de notificaciones", "Cerrar",
        )
        assert i18n.tr_n("%n saved download(s)", 1, context="History") == (
            "1 descarga guardada"
        )
        assert i18n.tr_n("%n saved download(s)", 3, context="History") == (
            "3 descargas guardadas"
        )

        late_dialog = i18n.TranslatableDialog()
        late_dialog.setWindowTitle("Notification Log")
        late_layout = QVBoxLayout(late_dialog)
        late_close = QPushButton("Close")
        late_layout.addWidget(late_close)
        late_dialog.show()
        qt_application.processEvents()
        assert (late_dialog.windowTitle(), late_close.text()) == (
            "Registro de notificaciones", "Cerrar",
        )
        late_dialog.close()

        assert i18n.install_translator("en", qt_application)
        assert (download.text(), settings.text()) == ("Download", "Settings")
        assert (dialog.windowTitle(), close.text()) == ("Notification Log", "Close")
    finally:
        i18n.install_translator("en", qt_application)
        shell.close()
        dialog.close()


def test_pseudo_locale_expands_text_and_clipping_audit_detects_constraints(
    qt_application,
):
    root = QWidget()
    layout = QVBoxLayout(root)
    constrained = QPushButton("Download Selected")
    constrained.setObjectName("constrainedAction")
    constrained.setFixedWidth(40)
    layout.addWidget(constrained)
    i18n.translate_widget_tree(root)
    try:
        assert i18n.install_translator("qps-ploc", qt_application)
        assert constrained.text().startswith("⟦")
        assert "constrainedAction" in i18n.find_clipped_text_widgets(root)
    finally:
        i18n.install_translator("en", qt_application)
        root.close()
