from types import SimpleNamespace
from unittest import mock

from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QTableWidget,
    QWidget,
)

from streamkeep.storage import StorageScan
from streamkeep.ui.main_window import StreamKeep
from streamkeep.ui.storage_model import StorageFilterProxyModel, StorageTableModel
from streamkeep.ui.tabs import analytics
from streamkeep.ui.tabs.download_vod import DownloadVodMixin
from streamkeep.ui.tabs.settings_tools import SettingsToolsMixin
from streamkeep.ui.tabs.storage import populate_storage_table


def test_analytics_explains_an_empty_archive_and_selected_range(qt_application):
    window = QWidget()
    page = analytics.build_analytics_tab(window)
    empty_stats = {
        "total": 0,
        "size_gb": 0.0,
        "platforms": [],
        "channels": [],
        "daily": [],
    }
    try:
        with mock.patch.object(analytics._db, "history_analytics", return_value=empty_stats):
            analytics._refresh_analytics(window)
            assert not window.analytics_empty_state.isHidden()
            assert window.analytics_charts.isHidden()
            assert window.analytics_empty_title.text() == "No archive analytics yet"
            assert "History entry" in window.analytics_empty_body.text()

            window.analytics_range.setCurrentIndex(1)
            assert window.analytics_empty_title.text() == (
                "No archive activity in this range"
            )
            assert "All Time" in window.analytics_empty_body.text()
    finally:
        page.close()
        window.close()


def test_storage_uses_the_shared_empty_state_for_an_empty_scan(qt_application):
    window = SimpleNamespace(
        _config={},
        storage_platform_filter=QComboBox(),
        storage_channel_filter=QComboBox(),
        storage_total_value=QLabel(),
        storage_total_sub=QLabel(),
        storage_files_value=QLabel(),
        storage_platforms_value=QLabel(),
        storage_platforms_sub=QLabel(),
        storage_channels_value=QLabel(),
        storage_filter_summary=QLabel(),
        storage_table=QTableView(),
        storage_empty_state=QFrame(),
        storage_delete_btn=QPushButton(),
        _schedule_visible_storage_thumbnails=lambda: None,
    )
    window.storage_model = StorageTableModel()
    window.storage_proxy_model = StorageFilterProxyModel()
    window.storage_proxy_model.setSourceModel(window.storage_model)
    window.storage_table.setModel(window.storage_proxy_model)

    populate_storage_table(window, StorageScan())

    assert window.storage_table.isHidden()
    assert not window.storage_empty_state.isHidden()


class _VodHost(DownloadVodMixin):
    def __init__(self):
        self._queue_autostart = False
        self._queue_active_item = None
        self.url_input = QLineEdit("https://example.test/channel")
        self.vod_table = QTableWidget(0, 7)
        self.vod_empty_state = QFrame()
        self.vod_select_all_cb = QPushButton()
        self.vod_widget = QFrame()
        self.vod_summary_label = QLabel()
        self.vod_load_more_btn = QPushButton()
        self.vod_load_btn = QPushButton()
        self.vod_queue_btn = QPushButton()
        self.vod_dl_all_btn = QPushButton()
        self.fetch_btn = QPushButton()
        self.statuses = []

    def _update_badge(self, _platform):
        pass

    def _set_status(self, message, tone):
        self.statuses.append((message, tone))


def test_channel_resolution_shows_a_next_step_when_no_vods_exist(qt_application):
    window = _VodHost()

    window._on_vods_found([], "Example")

    assert window.vod_table.isHidden()
    assert not window.vod_empty_state.isHidden()
    assert window.vod_select_all_cb.isHidden()
    assert not window.vod_load_btn.isEnabled()
    assert window.statuses[-1][1] == "info"


def test_settings_data_tables_swap_blank_grids_for_empty_states(qt_application):
    plugin = SimpleNamespace(
        plugin_trust_table=QTableWidget(0, 6),
        plugin_trust_empty_state=QFrame(),
        _refresh_source_adapter_ui=lambda: None,
        _plugin_reports=lambda: [],
        _on_plugin_selection_changed=lambda: None,
    )
    SettingsToolsMixin._refresh_plugin_trust_ui(plugin)
    assert plugin.plugin_trust_table.isHidden()
    assert not plugin.plugin_trust_empty_state.isHidden()

    adapter = SimpleNamespace(
        source_adapter_table=QTableWidget(0, 5),
        source_adapter_empty_state=QFrame(),
        _source_adapter_rows=lambda: ({}, []),
        _on_source_adapter_selection_changed=lambda: None,
    )
    SettingsToolsMixin._refresh_source_adapter_ui(adapter)
    assert adapter.source_adapter_table.isHidden()
    assert not adapter.source_adapter_empty_state.isHidden()

    engine = SimpleNamespace(
        source_engine_table=QTableWidget(0, 3),
        source_engine_empty_state=QFrame(),
        _source_engine_rows=lambda: [],
        _update_source_engine_status=lambda: None,
    )
    SettingsToolsMixin._refresh_source_engine_ui(engine)
    assert engine.source_engine_table.isHidden()
    assert not engine.source_engine_empty_state.isHidden()


def test_health_panel_does_not_fake_an_empty_table_row(qt_application):
    window = SimpleNamespace(
        health_table=QTableWidget(0, 4),
        health_empty_state=QFrame(),
        health_status_label=QLabel(),
        health_updated_label=QLabel(),
    )

    StreamKeep._refresh_health_panel(window, {
        "status": "healthy",
        "checked_at": "2026-08-12T12:00:00Z",
        "summary": {"active": 0},
        "conditions": [],
    })

    assert window.health_table.rowCount() == 0
    assert window.health_table.isHidden()
    assert not window.health_empty_state.isHidden()
