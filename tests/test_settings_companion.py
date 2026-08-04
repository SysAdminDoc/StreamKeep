from types import SimpleNamespace
from unittest import mock

import pytest
from PyQt6.QtWidgets import QTableWidget

from streamkeep.ui.tabs.settings_companion import SettingsCompanionMixin


def test_companion_master_token_is_generated_only_with_persisted_config():
    window = SimpleNamespace(_config={}, _persist_config=mock.Mock(return_value=True))
    token = SettingsCompanionMixin._ensure_companion_master_token(window)
    assert token
    assert window._config["companion_token"] == token
    window._persist_config.assert_called_once_with()

    failing = SimpleNamespace(_config={}, _persist_config=mock.Mock(return_value=False))
    with pytest.raises(ValueError, match="[Ss]ecure credential storage"):
        SettingsCompanionMixin._ensure_companion_master_token(failing)
    assert "companion_token" not in failing._config


def test_companion_local_url_and_scoped_token_inventory_are_redacted_ui_data(
    qt_application,
):
    class _Server:
        port = 4567
        url = "http://127.0.0.1:4567/"

        def list_scoped_tokens(self):
            return [{
                "id": "token-id",
                "label": "Phone",
                "scopes": ["queue", "status"],
                "origin": "https://remote.example",
                "created_at": "2026-08-04T00:00:00Z",
                "last_used": None,
            }]

    window = SimpleNamespace(
        _companion_server=_Server(),
        companion_tokens_table=QTableWidget(0, 6),
    )
    window._on_revoke_companion_token = mock.Mock()

    assert SettingsCompanionMixin._companion_local_url(window) == (
        "http://127.0.0.1:4567/"
    )
    SettingsCompanionMixin._refresh_companion_tokens(window)
    table = window.companion_tokens_table
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "Phone"
    assert table.item(0, 1).text() == "queue, status"
    assert table.item(0, 2).text() == "https://remote.example"
    assert table.item(0, 4).text() == "Never"
    assert table.cellWidget(0, 5).text() == "Revoke"

    table.cellWidget(0, 5).click()
    qt_application.processEvents()
    window._on_revoke_companion_token.assert_called_once_with("token-id")


def test_companion_pairing_expiration_does_not_clear_a_newer_code():
    refreshed = []
    window = SimpleNamespace(
        _companion_pairing_code="new-code",
        _refresh_companion_ui=lambda: refreshed.append(True),
    )
    SettingsCompanionMixin._expire_companion_pairing_code(window, "old-code")
    assert window._companion_pairing_code == "new-code"
    assert refreshed == []

    SettingsCompanionMixin._expire_companion_pairing_code(window, "new-code")
    assert window._companion_pairing_code == ""
    assert refreshed == [True]


def test_companion_origin_pin_persists_only_the_normalized_value():
    window = SimpleNamespace(_config={}, _log=mock.Mock())
    with mock.patch(
        "streamkeep.ui.tabs.settings_companion._save_config", return_value=True,
    ) as save:
        SettingsCompanionMixin._on_companion_extension_origin_pinned(
            window, "  chrome-extension://example  ",
        )
        assert window._config["companion_extension_origin"] == (
            "chrome-extension://example"
        )
        SettingsCompanionMixin._on_companion_extension_origin_pinned(window, "")

    assert "companion_extension_origin" not in window._config
    assert save.call_count == 2
