from types import SimpleNamespace
from unittest import mock

from streamkeep import db, local_server
from streamkeep.db import history, queue, schema
from streamkeep.postprocess import PostProcessor
from streamkeep.server import routes, static_assets
from streamkeep.ui.tabs import settings_presets


def test_database_facade_and_domain_modules_keep_the_legacy_surface():
    assert history.load_history is db.load_history
    assert queue.enqueue_queue_job is db.enqueue_queue_job
    assert schema.migrate_database is not db.init_db

    original_connect = db._connect
    with mock.patch.object(db, "_connect") as patched:
        assert db._connect is patched
        assert db._implementation._connect is patched
    assert db._connect is original_connect
    assert db._implementation._connect is original_connect


def test_server_facade_exposes_route_table_and_external_web_ui():
    assert local_server.PRODUCT_REST_PATHS == set(routes.ROUTE_TABLE)
    html = static_assets.load_web_ui()
    assert html == local_server._WEB_UI_HTML
    assert "{{web_i18n}}" in html


def test_web_remote_template_is_mobile_accessible_and_has_error_states():
    html = static_assets.load_web_ui()
    assert 'role="tablist"' in html
    assert 'role="tabpanel"' in html
    assert 'aria-selected="true"' in html
    assert 'aria-live="assertive"' in html
    assert 'aria-busy="true"' in html
    assert "button:focus-visible" in html
    assert "@media (max-width:600px)" in html
    assert 'autocomplete="one-time-code"' in html
    assert "message.className='status-region error'" in html
    assert 'type="button"' in html


class _PresetCombo:
    def __init__(self, current=""):
        self.items = []
        self.current = current

    def blockSignals(self, _blocked):
        return None

    def clear(self):
        self.items.clear()

    def addItem(self, label, userData=""):
        self.items.append((label, userData))

    def setCurrentIndex(self, index):
        self.current = self.items[index][1]

    def currentData(self):
        return self.current

    def findData(self, value):
        for index, (_label, data) in enumerate(self.items):
            if data == value:
                return index
        return -1


def test_settings_presets_are_owned_by_the_extracted_module():
    original = settings_presets._pp_snapshot()
    window = SimpleNamespace(
        _config={"pp_presets": {"Custom": {"extract_audio": True}}},
        pp_preset_combo=_PresetCombo(),
    )
    try:
        settings_presets._populate_pp_presets(window)
        assert [item[1] for item in window.pp_preset_combo.items] == [
            "",
            *settings_presets.BUILTIN_PRESETS,
            "Custom",
        ]

        window.pp_preset_combo.current = "Custom"
        settings_presets._on_pp_preset_selected(window)
        assert PostProcessor.extract_audio is True

        settings_presets._save_user_presets(
            window, {"Saved": {"convert_video": True}},
        )
        assert settings_presets._get_user_presets(window) == {
            "Saved": {"convert_video": True},
        }
    finally:
        settings_presets._pp_apply_snapshot(original)
