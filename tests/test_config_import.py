import copy
import json
from unittest import mock

import pytest

from streamkeep import config
from streamkeep.ui.tabs import settings as settings_module


def _envelope(payload, *, version=config.CONFIG_EXPORT_SCHEMA_VERSION):
    return json.dumps({
        "format": config.CONFIG_EXPORT_FORMAT,
        "schema_version": version,
        "exported_by": "test",
        "config": payload,
    }).encode("utf-8")


def test_export_config_is_versioned_and_secret_free():
    exported = config.export_config({
        "theme": "dark",
        "hf_token": "hf-private",
        "webhook_url": "https://hooks.example/private",
        "monitor_channels": [{"url": "https://example.invalid/channel"}],
    })

    assert exported["format"] == config.CONFIG_EXPORT_FORMAT
    assert exported["schema_version"] == config.CONFIG_EXPORT_SCHEMA_VERSION
    assert exported["config"]["theme"] == "dark"
    assert exported["config"]["hf_token"] == ""
    assert exported["config"]["webhook_url"] == ""
    assert "monitor_channels" not in exported["config"]


@pytest.mark.parametrize("payload, message", [
    (json.dumps({"theme": "dark"}).encode(), "not a StreamKeep"),
    (_envelope({"theme": "dark"}, version=99), "unsupported config schema"),
    (
        b'{"format":"streamkeep-config","format":"other",'
        b'"schema_version":1,"config":{}}',
        "duplicate JSON key",
    ),
    (_envelope({"output_dir": []}), "config.output_dir must be a string"),
    (_envelope({"history": []}), "cannot contain library state"),
    (_envelope({"hooks": {"unknown_event": "echo no"}}), "unsupported hook event"),
    (
        _envelope({"ytdlp_arg_templates": {"Unsafe": ["--exec", "calc"]}}),
        "not allowed",
    ),
    (_envelope({"hf_token": "secretref:config:webhook_url"}), "local secret handles"),
])
def test_import_rejects_unversioned_or_invalid_schema(payload, message):
    with pytest.raises(config.ConfigImportError, match=message):
        config.prepare_config_import(payload, {})


def test_import_enforces_byte_depth_count_and_string_limits():
    with pytest.raises(config.ConfigImportError, match="1 MB"):
        config.prepare_config_import(
            b" " * (config.MAX_CONFIG_IMPORT_BYTES + 1), {}
        )

    nested = "leaf"
    for _ in range(config.MAX_CONFIG_IMPORT_DEPTH + 2):
        nested = {"next": nested}
    with pytest.raises(config.ConfigImportError, match="nesting exceeds"):
        config.prepare_config_import(_envelope({"nested": nested}), {})

    many_values = {f"bucket_{index}": [0] * 500 for index in range(11)}
    with pytest.raises(config.ConfigImportError, match="more than 5000 values"):
        config.prepare_config_import(_envelope(many_values), {})

    long_string = "x" * (config.MAX_CONFIG_IMPORT_STRING_CHARS + 1)
    with pytest.raises(config.ConfigImportError, match="string is too long"):
        config.prepare_config_import(_envelope({"custom": long_string}), {})


def test_import_quarantines_each_risky_capability_until_approved():
    imported = {
        "theme": "light",
        "hooks": {"download_complete": "echo imported-hook-secret"},
        "webhook_url": "https://hooks.example/imported-webhook-secret",
        "proxy": "http://user:imported-proxy-secret@proxy.example",
        "proxy_pool": [{
            "url": "socks5://proxy.example:1080",
            "platforms": ["twitch"],
            "enabled": True,
            "label": "Imported",
        }],
        "cookies_browser": "firefox",
        "cookies_file": "C:/private/cookies.txt",
        "media_server": {
            "enabled": True,
            "server_type": "plex",
            "url": "http://plex.local",
            "token": "",
            "library_id": "1",
            "library_path": "D:/Media",
        },
        "companion_server_enabled": True,
        "companion_bind_lan": True,
        "lifecycle": {
            "enabled": True,
            "max_days": 30,
            "max_total_gb": 500,
            "delete_watched": True,
            "favorites_exempt": True,
        },
    }
    preview = config.prepare_config_import(_envelope(imported), {"theme": "dark"})

    assert set(preview.capabilities) == {
        "hooks", "webhook", "proxies", "cookie_sources",
        "media_server_auto_import", "companion_server", "lifecycle_cleanup",
    }
    held = preview.quarantined_config
    assert held["hooks"] == {}
    assert held["webhook_url"] == ""
    assert held["proxy"] == ""
    assert held["proxy_pool"] == []
    assert held["cookies_browser"] == ""
    assert held["cookies_file"] == ""
    assert held["media_server"]["enabled"] is False
    assert held["companion_server_enabled"] is False
    assert held["companion_bind_lan"] is False
    assert held["lifecycle"]["enabled"] is False

    activated = config.finalize_config_import(
        preview, {"hooks", "media_server_auto_import"}
    )
    assert activated["hooks"] == imported["hooks"]
    assert activated["media_server"]["enabled"] is True
    assert activated["webhook_url"] == ""
    assert activated["proxy_pool"] == []
    assert activated["lifecycle"]["enabled"] is False

    diff = "\n".join(preview.diff_lines)
    assert "imported-hook-secret" not in diff
    assert "imported-webhook-secret" not in diff
    assert "imported-proxy-secret" not in diff
    assert "theme" in diff


def test_validation_failure_does_not_mutate_current_config():
    current = {
        "theme": "dark",
        "hooks": {"download_complete": "echo trusted"},
    }
    original = copy.deepcopy(current)

    with pytest.raises(config.ConfigImportError):
        config.prepare_config_import(_envelope({"proxy_pool": ["invalid"]}), current)

    assert current == original


def test_finalize_rejects_capabilities_not_present_in_preview():
    preview = config.prepare_config_import(_envelope({"theme": "light"}), {})
    with pytest.raises(config.ConfigImportError, match="unknown import capability"):
        config.finalize_config_import(preview, {"hooks"})


class _FakeSettingsWindow:
    def __init__(self):
        self._config = {"theme": "dark"}
        self.logs = []
        self.statuses = []

    def _log(self, value):
        self.logs.append(value)

    def _set_status(self, message, tone):
        self.statuses.append((message, tone))


def test_ui_validation_failure_never_saves_or_mutates_config(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"theme":"light"}', encoding="utf-8")
    window = _FakeSettingsWindow()
    original = copy.deepcopy(window._config)

    with mock.patch.object(
        settings_module.QFileDialog, "getOpenFileName", return_value=(str(path), "")
    ), mock.patch.object(settings_module, "_save_config") as save:
        settings_module.SettingsTabMixin._on_import_config(window)

    save.assert_not_called()
    assert window._config == original
    assert window.statuses[-1][1] == "error"


def test_ui_shows_diff_before_save_and_cancel_keeps_config(tmp_path):
    path = tmp_path / "valid.json"
    path.write_text(
        json.dumps(config.export_config({"theme": "light"})), encoding="utf-8"
    )
    window = _FakeSettingsWindow()

    with mock.patch.object(
        settings_module.QFileDialog, "getOpenFileName", return_value=(str(path), "")
    ), mock.patch.object(
        settings_module, "ask_premium_confirmation", return_value=False
    ) as confirm, mock.patch.object(settings_module, "_save_config") as save:
        settings_module.SettingsTabMixin._on_import_config(window)

    save.assert_not_called()
    assert window._config == {"theme": "dark"}
    assert "theme" in confirm.call_args.kwargs["summary_body"]
    assert "no changes were applied" in window.statuses[-1][0]
