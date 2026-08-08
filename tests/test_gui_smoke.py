import threading
import tempfile
from unittest import mock
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QFrame,
    QLabel, QLineEdit, QPlainTextEdit, QSlider, QSplitter, QTextEdit,
)

from streamkeep.models import HistoryEntry, MediaTrackInfo, MonitorEntry, QualityInfo
from streamkeep import db


def _ready_ytdlp_status():
    return {
        "state": "ready",
        "summary": "Ready",
        "detail": "yt-dlp 2026.07.04 with yt-dlp-ejs and deno 2.3.0.",
        "yt_dlp_version": "2026.07.04",
        "ejs_available": True,
        "js_runtime": {"name": "deno", "version": "2.3.0", "supported": True},
        "problems": [],
    }


def _ready_runtime_registry(*, refresh=False, config=None):
    del refresh, config

    def record(name, version, path):
        return {
            "name": name,
            "display_name": name,
            "path": path,
            "version": version,
            "minimum": version,
            "provenance": "test-fixture",
            "available": True,
            "supported": True,
            "capabilities": [],
            "command": [path],
            "repair": "",
            "detail": f"{name} {version} at {path}",
            "state": "ready",
        }

    return {
        "sqlite": record("SQLite", "3.53.3", r"C:\Python\sqlite3.dll"),
        "ffmpeg": record("FFmpeg", "8.1.2", r"C:\Tools\ffmpeg.exe"),
        "curl": record("curl", "8.21.0", r"C:\Tools\curl.exe"),
        "pillow": record("Pillow", "12.3.0", r"C:\Python\PIL\__init__.py"),
    }


def test_onboarding_exposes_high_contrast_and_applies_choice(qt_application):
    import streamkeep.ui.onboarding as onboarding
    from streamkeep.theme import CAT, apply_theme

    config = {"existing": True}
    with mock.patch.object(
        onboarding,
        "get_runtime_capabilities",
        return_value=_ready_runtime_registry(),
    ), mock.patch.object(
        onboarding,
        "ytdlp_runtime_status",
        return_value={"state": "ready", "detail": "test runtime"},
    ):
        wizard = onboarding.OnboardingWizard(config=config)
        try:
            wizard._high_contrast_radio.setChecked(True)
            assert wizard.chosen_theme == "high_contrast"
            assert "security-ready" not in wizard._ffmpeg_title.text().lower()
            wizard._finish()
            assert config["theme"] == "high_contrast"
            assert config["first_run_complete"] is True
            assert CAT["base"] == "#000000"
        finally:
            wizard.close()
            apply_theme("dark", app=qt_application)


def test_main_window_tabs_dialogs_and_language_smoke(tmp_path, qt_application):
    from streamkeep import accounts, notifications
    from streamkeep.i18n import available_languages, current_language, install_translator
    import streamkeep.ui.main_window as main_window
    from streamkeep.ui.monitor_entry_dialog import MonitorEntryDialog
    from streamkeep.ui.notification_log_dialog import NotificationLogDialog
    from streamkeep.ui.onboarding import OnboardingWizard
    from streamkeep.ui.rename_dialog import RenameDialog
    import streamkeep.ui.tabs.settings as settings_tab

    config = {
        "output_dir": str(tmp_path),
        "check_for_updates": False,
        "companion_server_enabled": False,
        "ytdlp_arg_templates": {
            "Archive headers": [
                "--add-header", "Referer: https://example.com/",
            ],
        },
    }
    recording_dir = tmp_path / "recording"
    recording_dir.mkdir()
    recording_media = recording_dir / "capture.mp4"
    recording_media.write_bytes(b"thumbnail fixture")

    with mock.patch.object(main_window, "_load_config", return_value=dict(config)), \
            mock.patch.object(main_window, "_save_config"), \
            mock.patch.object(main_window, "_write_log_line"), \
            mock.patch.object(main_window._db, "CONFIG_DIR", tmp_path), \
            mock.patch.object(main_window._db, "DB_PATH", tmp_path / "library.db"), \
            mock.patch.object(accounts, "CONFIG_DIR", tmp_path), \
            mock.patch.object(accounts, "DB_PATH", tmp_path / "accounts.db"), \
            mock.patch.object(notifications, "NOTIF_LOG", tmp_path / "notifications.jsonl"), \
            mock.patch.object(main_window, "_available_video_codec_keys", return_value=["h264"]), \
            mock.patch.object(settings_tab, "available_video_codec_keys", return_value=["h264"]), \
            mock.patch.object(main_window.QSystemTrayIcon, "isSystemTrayAvailable", return_value=False), \
            mock.patch.object(main_window.QTimer, "singleShot", lambda *args, **kwargs: None), \
            mock.patch("streamkeep.ui.thumb_loader.ThumbLoader.request") as thumb_request, \
            mock.patch("streamkeep.search.index_all_async", lambda *args, **kwargs: None), \
            mock.patch(
                "streamkeep.ui.onboarding.get_runtime_capabilities",
                side_effect=_ready_runtime_registry,
            ), \
            mock.patch(
                "streamkeep.ui.tabs.settings.get_runtime_capabilities",
                side_effect=_ready_runtime_registry,
            ), \
            mock.patch("streamkeep.ui.tabs.settings.ytdlp_runtime_status", _ready_ytdlp_status), \
            mock.patch("streamkeep.ui.onboarding.ytdlp_runtime_status", _ready_ytdlp_status):
        main_window._db.init_db()
        history_id = main_window._db.save_history_entry(
            HistoryEntry(
                date="2026-07-15 19:00",
                platform="yt-dlp",
                title="Existing YouTube download",
                quality="1080p",
                size="1.0 GB",
                path=str(recording_dir),
                url="https://www.youtube.com/watch?v=fixture",
            ).to_dict()
        )

        window = main_window.StreamKeep()
        try:
            window._schedule_visible_history_thumbnails()
            thumb_request.assert_any_call(
                history_id,
                str(recording_media),
            )
            assert window._stack.count() == len(window._tab_names) == 7
            assert [button.text() for button in window._tab_btns] == window._tab_names
            assert all(button.isCheckable() for button in window._tab_btns)
            assert [button.isChecked() for button in window._tab_btns] == [
                True, False, False, False, False, False, False,
            ]
            assert window._global_search.accessibleName() == "Search StreamKeep"
            assert window.url_input.accessibleName() == "Source URL"
            for table, expected_name in (
                (window.table, "Available stream segments"),
                (window.queue_table, "Download queue"),
                (window.monitor_table, "Monitored channels"),
                (window.history_table, "Download history"),
                (window.storage_table, "Archive storage"),
                (window.operations_table, "Operations table"),
            ):
                assert table.focusPolicy() == Qt.FocusPolicy.StrongFocus
                assert table.accessibleName() == expected_name
                assert table.property("accessibilityConfigured") is True
            assert window.storage_scrub_btn.text() == "Run integrity scrub"
            assert window.storage_integrity_tree.accessibleName() == (
                "Archive integrity issues"
            )
            assert window.integrity_fraction_spin.value() == 10
            interactive_types = (
                QAbstractButton, QAbstractItemView, QAbstractSpinBox,
                QComboBox, QLineEdit, QPlainTextEdit, QSlider, QTextEdit,
            )
            unnamed = []
            for attr_name, control in vars(window).items():
                if not isinstance(control, interactive_types):
                    continue
                if control.accessibleName().strip():
                    continue
                unnamed.append(f"{attr_name}:{type(control).__name__}")
            assert unnamed == []

            # The compact visual system keeps navigation and the primary
            # capture controls above the queue/activity working surface.
            assert window.findChild(QFrame, "appHeader") is not None
            assert window.findChild(QFrame, "appNav") is not None
            assert window.findChild(QFrame, "navRail") is window.nav_rail
            assert window.nav_rail.width() == 220
            assert window.shell_page_title.text() == "Download"
            assert window.system_status_btn.text() == "Systems ready"
            assert window.findChild(QFrame, "composerCard") is not None
            assert window.findChild(QFrame, "queuePane") is not None
            assert window.findChild(QFrame, "activityPane") is not None
            assert window.findChild(QFrame, "archiveHealthPane") is not None
            assert len(window.findChildren(QFrame, "dataPane")) == 6
            assert len(window.findChildren(QFrame, "analyticsPanel")) == 3
            work_surface = window.findChild(QSplitter, "workSurface")
            assert work_surface is not None
            assert work_surface.orientation() == Qt.Orientation.Horizontal
            assert not window.queue_empty_state.isHidden()
            assert window.queue_table.isHidden() is False
            assert not window.activity_empty_state.isHidden()
            assert window.log_text.isHidden()
            assert not window.monitor_empty_state.isHidden()
            assert window.monitor_table.isHidden()
            assert window.history_empty_state.isHidden()
            assert not window.history_table.isHidden()
            window.resize(1120, 900)
            window.show()
            window._switch_tab(2)
            qt_application.processEvents()
            history_scroll = window._stack.widget(2)
            assert history_scroll.horizontalScrollBar().maximum() == 0
            assert window.history_metrics_grid._compact is True
            window.hide()
            window._switch_tab(0)
            assert window.download_hero_title.text() == "New download"
            assert window.scan_lan_check.text() == "Allow LAN for this scan"
            assert not window.scan_lan_check.isChecked()
            assert window.download_settings_action.isCheckable()
            assert window.batch_import_btn.objectName() == "secondary"
            assert window.download_advanced_btn.objectName() == "secondary"
            assert window.queue_table.columnCount() == 7
            window._download_queue = [
                    {
                        "status": "queued",
                        "platform": "Direct",
                        "title": "First item",
                        "added": "now",
                        "url": "https://example.com/first",
                    },
                {
                    "status": "queued",
                        "platform": "Direct",
                        "title": "Second item",
                        "added": "now",
                        "url": "https://example.com/second",
                },
            ]
            window._refresh_queue_table()
            assert window.queue_selected_label.text() == "2 selected"
            assert window.queue_table.cellWidget(0, 0).findChild(QAbstractButton).isChecked()
            assert window.queue_table.cellWidget(0, 2) is not None
            assert window.queue_table.cellWidget(0, 3) is not None
            window.queue_table.cellWidget(1, 0).findChild(QAbstractButton).setChecked(False)
            assert window.queue_selected_label.text() == "1 selected"
            window._on_queue_header_clicked(0)
            assert window.queue_selected_label.text() == "2 selected"
            window._on_queue_pause_selected()
            assert [item["status"] for item in window._download_queue] == [
                "paused", "paused",
            ]
            assert window.queue_start_btn.isEnabled()
            window._on_queue_item_progress(
                window._download_queue[0],
                62,
                "1.2 GB | 18.7 MB/s | ETA 00:01:24",
            )
            assert window._download_queue[0]["progress"] == 62
            assert window._download_queue[0]["speed"] == "18.7 MB/s"
            assert window._download_queue[0]["eta"] == "00:01:24"
            assert window._queue_progress_bars[id(window._download_queue[0])].value() == 62
            failed_id = main_window._db.save_failed_job(
                url="https://example.com/failed",
                platform="Example",
                title="Failed item",
                stage="fetch",
                error="No space left on device",
                output_dir=str(tmp_path / "recordings"),
                queue_data={"url": "https://example.com/failed"},
                auto_retry=False,
                status="intervention",
            )
            window._download_queue = [{
                "status": "failed",
                "platform": "Example",
                "title": "Failed item",
                "added": "now",
                "url": "https://example.com/failed",
                "failure_id": failed_id,
            }]
            window._refresh_queue_table()
            remediation_hint = window.queue_table.cellWidget(0, 2).findChild(
                QLabel, "queueFailureRemediation"
            )
            assert remediation_hint is not None
            assert "Free space" in remediation_hint.text()
            window._download_queue = []
            window._refresh_queue_table()
            assert window._format_activity_message(
                "[QUEUE] Starting: Example",
                when=main_window.datetime(2026, 7, 17, 14, 32, 10),
            ) == "14:32:10  Starting: Example"
            worker = threading.Thread(
                target=lambda: window._log("[SEARCH] Indexed from worker thread")
            )
            worker.start()
            worker.join(timeout=2)
            assert not worker.is_alive()
            qt_application.processEvents()
            assert "Indexed from worker thread" in window.log_text.toPlainText()
            assert [button.text() for button in window.settings_nav_buttons] == [
                "General", "Access", "Downloads", "Companion",
                "Automation", "Library", "Processing",
            ]
            settings_page = window._stack.widget(5).widget()
            assert settings_page.property("responsiveLayout") is True
            assert [
                window.companion_scope_sub.text(),
                window.companion_remote_sub.text(),
                window.companion_token_sub.text(),
            ] == ["This PC", "Not running", "Not running"]
            assert window.theme_combo.itemText(0) == "Dark"
            assert all(
                button.objectName() == "commandGhost"
                for button in window.settings_nav_buttons
            )
            assert window.time_range_action.isCheckable()
            assert window.adv_overrides_action.isCheckable()
            assert window.download_settings_panel.isHidden()
            assert window.adv_frame.isHidden()
            window.download_settings_action.setChecked(True)
            assert not window.download_settings_panel.isHidden()
            window.download_settings_action.setChecked(False)
            assert window.download_settings_panel.isHidden()
            window.time_range_action.setChecked(True)
            assert window.download_settings_action.isChecked()
            assert not window.time_range_panel.isHidden()
            window.time_range_action.setChecked(False)
            window.download_settings_action.setChecked(False)
            assert window.adv_ytdlp_template_combo.findData(
                "Archive headers"
            ) >= 0
            assert window.ytdlp_template_editor_combo.findData(
                "Archive headers"
            ) >= 0
            assert window.copy_command_btn.isEnabled() is False
            assert window.adv_hls_key_input.echoMode() == QLineEdit.EchoMode.Password
            assert window.adv_hls_iv_input.echoMode() == QLineEdit.EchoMode.Password
            assert window.track_table.columnCount() == 5
            assert window.track_section.isVisible() is False
            from streamkeep.ui.tabs.download import _populate_track_table
            selectable = QualityInfo(
                name="1080p", url="https://cdn.example.com/main.mpd",
                format_type="dash", primary_track_id="v0",
                tracks=[
                    MediaTrackInfo(
                        id="v0", kind="video", label="1080p",
                        url="https://cdn.example.com/main.mpd", default=True,
                    ),
                    MediaTrackInfo(
                        id="v1", kind="video", label="720p",
                        url="https://cdn.example.com/main.mpd",
                        stream_index=1,
                    ),
                    MediaTrackInfo(
                        id="a0", kind="audio", label="English",
                        language="en", url="https://cdn.example.com/main.mpd",
                        default=True,
                    ),
                ],
            )
            window.quality_combo.clear()
            window.quality_combo.addItem("1080p", selectable)
            _populate_track_table(window)
            assert window.track_table.rowCount() == 3
            assert not window.track_section.isHidden()
            assert [check.isChecked() for check, _track in window._track_checks] == [
                True, False, True,
            ]
            window.track_table.cellActivated.emit(1, 1)
            assert [check.isChecked() for check, track in window._track_checks
                    if track.kind == "video"] == [False, True]
            assert "border-radius: 999px" not in window.status_pill.styleSheet()
            metric_labels = [
                getattr(window, f"download_{key}_{suffix}")
                for key in ("platform", "duration", "selection", "output", "finalize", "speed", "eta")
                for suffix in ("value", "sub")
            ]
            assert all(
                label.parentWidget() is window._download_metric_state
                for label in metric_labels
            )
            assert not window._download_metric_state.isVisible()

            # Archive maintenance exposes an explicit dry-run/approval surface.
            assert window.maintenance_summary.isHidden()
            from streamkeep.maintenance import plan_maintenance
            orphan_dir = tmp_path / "orphan-recording"
            orphan_dir.mkdir()
            (orphan_dir / "video.mp4").write_bytes(b"orphan fixture")
            maintenance_plan = plan_maintenance(
                tmp_path,
                config={"archive_backup_dir": str(tmp_path / "backups")},
                db_module=main_window._db,
            )
            window._on_maintenance_preview_done(maintenance_plan)
            assert not window.maintenance_summary.isHidden()
            assert window.maintenance_tree.accessibleName() == (
                "Archive maintenance preview"
            )
            assert window.maintenance_tree.topLevelItemCount() >= 2
            assert window.maintenance_apply_btn.isEnabled()
            assert "orphaned on disk" in window.maintenance_summary.text()

            # A browser clip handoff prefills the crop range before the fetch
            # that follows reads it (V-clip-handoff).
            window.crop_start_input.clear()
            window.crop_end_input.clear()
            window._on_companion_clip("https://example.com/clip", 30.0, 300.0)
            qt_application.processEvents()
            assert window.url_input.text() == "https://example.com/clip"
            assert window.crop_start_input.text() == "0:00:30"
            assert window.crop_end_input.text() == "0:05:00"

            # Structured event hooks: author one through the editor and confirm
            # it persists as an executable + argument array (no shell).
            hook_event = window.hooks_event_combo.itemData(0)
            window.hooks_event_combo.setCurrentIndex(0)
            window.hook_executable_input.setText("/usr/bin/notify")
            window.hook_args_edit.setPlainText("--title\n%SK_TITLE%")
            window.hook_enabled_check.setChecked(True)
            window._on_hook_save()
            qt_application.processEvents()
            saved_hook = window._config["hooks"][hook_event]
            assert saved_hook == {
                "executable": "/usr/bin/notify",
                "args": ["--title", "%SK_TITLE%"],
                "enabled": True,
            }
            # A legacy shell string surfaces as disabled and blanks the fields.
            window._config["hooks"][hook_event] = "echo legacy"
            window._refresh_hook_editor(hook_event)
            qt_application.processEvents()
            assert not window.hook_executable_input.isEnabled()
            assert "disabled" in window.hook_status_label.text().lower()

            qt_application.processEvents()
            leaked_windows = [
                widget for widget in qt_application.topLevelWidgets()
                if widget is not window and widget.isVisible()
            ]
            assert leaked_windows == []

            for index, name in enumerate(window._tab_names):
                window._switch_tab(index)
                qt_application.processEvents()
                assert window._stack.currentIndex() == index
                assert window.shell_page_title.text() == name
                assert window._tab_btns[index].objectName() == "tabActive", name
                assert window._tab_btns[index].isChecked(), name
                assert sum(button.isChecked() for button in window._tab_btns) == 1

            previous_revision = window.status_label.property(
                "accessibleStatusRevision"
            )
            window._set_status("The download could not be started.", "error")
            assert window.status_label.accessibleName() == (
                "Application status: The download could not be started."
            )
            assert window.status_label.accessibleDescription() == (
                "error status update"
            )
            assert window.status_label.property("accessibleStatusRevision") > (
                previous_revision
            )

            assert install_translator("en", qt_application) is True
            assert "es" in available_languages()
            hc_idx = window.theme_combo.findData("high_contrast")
            compact_idx = window.density_combo.findData("compact")
            blue_idx = window.accent_combo.findText("Blue")
            assert min(hc_idx, compact_idx, blue_idx) >= 0
            window.theme_combo.setCurrentIndex(hc_idx)
            window.density_combo.setCurrentIndex(compact_idx)
            window.accent_combo.setCurrentIndex(blue_idx)
            qt_application.processEvents()
            assert window._config["theme"] == "high_contrast"
            assert window._config["visual_density"] == "compact"
            assert window._config["visual_accent"] == "#89b4fa"
            assert "#000000" in qt_application.styleSheet().lower()
            window.theme_combo.setCurrentIndex(window.theme_combo.findData("dark"))
            window.density_combo.setCurrentIndex(window.density_combo.findData("cozy"))
            window.accent_combo.setCurrentIndex(window.accent_combo.findData(""))
            es_idx = window.language_combo.findData("es")
            assert es_idx >= 0
            window.language_combo.setCurrentIndex(es_idx)
            qt_application.processEvents()
            assert window._config["language"] == "es"
            assert current_language() == "es"
            assert window._tab_btns[0].text() == "Descargar"
            assert window._tab_btns[2].text() == "Historial"
            assert window.download_hero_title.text() == "Origen detectado"
            assert window.fetch_btn.text() == "Resolver origen"
            assert window.history_search.placeholderText().startswith("Buscar título")
            assert window.status_label.text() == "El idioma se actualizó en StreamKeep."
            # Stable-value combos that still consume currentText() do not have
            # their semantic values translated under the user-facing locale.
            assert window.storage_platform_filter.currentText() == "All"
            assert window.theme_combo.currentText().startswith("Oscuro")
            assert install_translator("en", qt_application) is True
            assert window._tab_btns[0].text() == "Download"
            assert window.download_hero_title.text() == "Source detected"

            # Queue-complete power action (V24): the control exists, defaults
            # to the safe "none", and its selection round-trips into config.
            assert window.queue_complete_action_combo.currentData() == "none"
            _lock_idx = window.queue_complete_action_combo.findData("lock")
            assert _lock_idx >= 0
            window.queue_complete_action_combo.setCurrentIndex(_lock_idx)
            window._on_save_settings()
            assert window._config["queue_complete_action"] == "lock"

            # Bilingual-subtitle + LRC post-processing controls (P3): the
            # controls exist, drive the PostProcessor, and round-trip config.
            from streamkeep.postprocess import PostProcessor
            window.pp_bilingual_check.setChecked(True)
            window.pp_bilingual_primary.setText("en")
            window.pp_bilingual_secondary.setText("es")
            _ass_idx = window.pp_bilingual_format.findText("ass")
            window.pp_bilingual_format.setCurrentIndex(_ass_idx)
            window.pp_lrc_check.setChecked(True)
            window.pp_lrc_lang.setText("ja")
            window._on_save_settings()
            assert window._config["pp_bilingual_subs"] is True
            assert window._config["pp_bilingual_secondary_lang"] == "es"
            assert window._config["pp_bilingual_format"] == "ass"
            assert window._config["pp_lrc_export"] is True
            assert window._config["pp_lrc_lang"] == "ja"
            assert PostProcessor.bilingual_secondary_lang == "es"

            # YouTube live-chat replay opt-in (P3): toggle exists and persists.
            from streamkeep.extractors.ytdlp import YtDlpExtractor
            assert window.capture_youtube_chat_check.isChecked() is False
            window.capture_youtube_chat_check.setChecked(True)
            window._on_save_settings()
            assert window._config["capture_youtube_chat"] is True
            assert YtDlpExtractor.capture_youtube_chat is True

            monitor_dialog = MonitorEntryDialog(
                window,
                MonitorEntry(
                    url="https://example.com/channel",
                    platform="Example",
                    channel_id="example-channel",
                    ytdlp_template_name="Archive headers",
                ),
                globals_preview=config,
            )
            assert monitor_dialog.ytdlp_template_combo.currentData() == (
                "Archive headers"
            )
            from streamkeep.ui.recover_dialog import RecoverDialog
            import streamkeep.ui.clip_dialog as clip_dialog
            recover_dialog = RecoverDialog(window)
            with mock.patch.object(clip_dialog, "probe_duration", return_value=0.0):
                trim_dialog = clip_dialog.ClipDialog(window, str(recording_media))
            assert recover_dialog.channel_input.accessibleName() == "Twitch channel"
            assert recover_dialog.table.accessibleName() == "Recoverable VODs"
            assert recover_dialog.channel_input.focusPolicy() != Qt.FocusPolicy.NoFocus
            assert trim_dialog.start_input.accessibleName() == "Clip start time"
            assert trim_dialog.start_input.focusPolicy() != Qt.FocusPolicy.NoFocus
            assert trim_dialog.scrubber.accessibleName() == "Clip timeline handles"
            dialogs = [
                NotificationLogDialog(window, window._notifications),
                monitor_dialog,
                RenameDialog(
                    window,
                    [
                        HistoryEntry(
                            title="Example Capture",
                            channel="Example",
                            platform="Example",
                            path=str(recording_dir),
                        )
                    ],
                ),
                OnboardingWizard(window, config=config),
                recover_dialog,
                trim_dialog,
            ]
            for dialog in dialogs:
                assert dialog.windowTitle()
                dialog.close()
        finally:
            window.close()
            qt_application.processEvents()


def test_playlist_expand_worker_emits_success_and_failure(qt_application):
    from streamkeep.workers.playlist import PlaylistExpandWorker

    successes = []
    errors = []
    worker = PlaylistExpandWorker("https://example.com/playlist")
    worker.finished.connect(
        successes.append,
        type=Qt.ConnectionType.DirectConnection,
    )

    with mock.patch(
        "streamkeep.workers.playlist.YtDlpExtractor.list_playlist_entries",
        return_value=[{"url": "https://example.com/video", "title": "Video"}],
    ):
        worker.run()

    qt_application.processEvents()
    assert successes == [[{"url": "https://example.com/video", "title": "Video"}]]

    failing_worker = PlaylistExpandWorker("https://example.com/broken")
    failing_worker.error.connect(errors.append, type=Qt.ConnectionType.DirectConnection)

    with mock.patch(
        "streamkeep.workers.playlist.YtDlpExtractor.list_playlist_entries",
        side_effect=RuntimeError("playlist probe failed"),
    ):
        failing_worker.run()

    qt_application.processEvents()
    assert errors == ["playlist probe failed"]


def test_playlist_expand_worker_suppresses_signals_after_interruption(qt_application):
    from streamkeep.workers.playlist import PlaylistExpandWorker

    emitted = []
    worker = PlaylistExpandWorker("https://example.com/playlist")
    worker.finished.connect(emitted.append, type=Qt.ConnectionType.DirectConnection)
    worker.error.connect(emitted.append, type=Qt.ConnectionType.DirectConnection)

    with mock.patch(
        "streamkeep.workers.playlist.YtDlpExtractor.list_playlist_entries",
        return_value=[{"url": "https://example.com/video"}],
    ) as playlist_probe, mock.patch.object(worker, "isInterruptionRequested", return_value=True):
        worker.run()

    qt_application.processEvents()
    playlist_probe.assert_not_called()
    assert emitted == []


def test_playlist_expand_worker_skips_user_tombstoned_entries(qt_application):
    from streamkeep.workers.playlist import PlaylistExpandWorker

    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(db, "DB_PATH", Path(tmpdir) / "library.db"):
            db.init_db()
            db.record_tombstone(
                platform="yt-dlp",
                source_id="playlist-blocked",
                webpage_url="https://www.youtube.com/watch?v=playlist-blocked",
            )
            emitted = []
            worker = PlaylistExpandWorker("https://example.com/playlist")
            worker.finished.connect(
                emitted.append, type=Qt.ConnectionType.DirectConnection,
            )
            with mock.patch(
                "streamkeep.workers.playlist.YtDlpExtractor.list_playlist_entries",
                return_value=[{
                    "id": "playlist-blocked",
                    "url": "https://www.youtube.com/watch?v=playlist-blocked",
                    "title": "Removed video",
                }],
            ):
                worker.run()

    qt_application.processEvents()
    assert emitted == [[]]


def test_close_waits_for_maintenance_worker_before_teardown(qt_application):
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QMainWindow
    import streamkeep.ui.main_window as main_window

    class StubMaintenanceWorker(QThread):
        def run(self):
            while not self.isInterruptionRequested():
                self.msleep(5)

    class TimerStub:
        def stop(self):
            return None

    window = main_window.StreamKeep.__new__(main_window.StreamKeep)
    QMainWindow.__init__(window)
    window._maintenance_worker = StubMaintenanceWorker()
    window._queue_contexts = {}
    window._queue_execution_enabled = False
    window._persist_config = lambda: None
    window.monitor = type("MonitorStub", (), {"_timer": TimerStub()})()
    window._scheduler_timer = TimerStub()
    window._config_save_timer = TimerStub()
    window._executor_lease_timer = TimerStub()
    window.clipboard_monitor = TimerStub()
    window._tray_icon = None
    window._disk_monitor = None
    window._backup_worker = None

    worker = window._maintenance_worker
    worker.start()
    try:
        assert window.close()
        qt_application.processEvents()
        assert not worker.isRunning()
        assert not window.isVisible()
    finally:
        if worker.isRunning():
            worker.requestInterruption()
            worker.wait(1500)
        window.deleteLater()
        qt_application.processEvents()


def test_a_queue_that_cannot_resume_says_so(qt_application, tmp_path, monkeypatch):
    """Resuming the queue must not fail silently.

    Four callers resume the queue after a power, disk or Settings change. Each
    swallowed the exception, so a failure left the queue permanently stalled
    with nothing on screen -- and two of them had already announced "resuming
    queue" first (V185).
    """
    from streamkeep.ui.main_window import StreamKeep

    window = StreamKeep(startup_check=True)
    try:
        statuses = []
        logged = []
        notified = []
        monkeypatch.setattr(
            window, "_set_status",
            lambda text, tone="info": statuses.append((text, tone)),
        )
        monkeypatch.setattr(window, "_log", logged.append)
        monkeypatch.setattr(
            window, "_notify_center",
            lambda text, level="info": notified.append((text, level)),
        )
        monkeypatch.setattr(
            window, "_advance_queue",
            lambda: (_ for _ in ()).throw(RuntimeError("lease is held")),
        )

        assert window._resume_queue_or_report() is False

        assert any("lease is held" in line for line in logged), logged
        assert statuses and statuses[-1][1] == "error", statuses
        assert "could not be resumed" in statuses[-1][0]
        assert notified and notified[-1][1] == "error", notified
    finally:
        window.close()


def test_a_queue_that_resumes_reports_nothing(qt_application, monkeypatch):
    from streamkeep.ui.main_window import StreamKeep

    window = StreamKeep(startup_check=True)
    try:
        statuses = []
        monkeypatch.setattr(
            window, "_set_status",
            lambda text, tone="info": statuses.append((text, tone)),
        )
        monkeypatch.setattr(window, "_advance_queue", lambda: None)

        assert window._resume_queue_or_report() is True
        assert not statuses, "a successful resume must stay quiet"
    finally:
        window.close()
