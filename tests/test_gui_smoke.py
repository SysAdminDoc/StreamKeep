from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLineEdit, QSplitter

from streamkeep.models import HistoryEntry, MediaTrackInfo, MonitorEntry, QualityInfo


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


def _ready_runtime_registry(*, refresh=False):
    del refresh

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
        "ffmpeg": record("FFmpeg", "8.1.2", r"C:\Tools\ffmpeg.exe"),
        "curl": record("curl", "8.21.0", r"C:\Tools\curl.exe"),
        "pillow": record("Pillow", "12.3.0", r"C:\Python\PIL\__init__.py"),
    }


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
        main_window._db.save_history_entry(
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
            thumb_request.assert_any_call(
                (str(recording_dir), "Existing YouTube download"),
                str(recording_media),
            )
            assert window._stack.count() == len(window._tab_names) == 6
            assert [button.text() for button in window._tab_btns] == window._tab_names

            # The compact visual system keeps navigation and the primary
            # capture controls above the queue/activity working surface.
            assert window.findChild(QFrame, "appHeader") is not None
            assert window.findChild(QFrame, "appNav") is not None
            assert window.findChild(QFrame, "composerCard") is not None
            work_surface = window.findChild(QSplitter, "workSurface")
            assert work_surface is not None
            assert work_surface.orientation() == Qt.Orientation.Horizontal
            assert window.download_hero_title.text() == "New download"
            assert window.scan_lan_check.text() == "Allow LAN for this scan"
            assert not window.scan_lan_check.isChecked()
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
            window._track_checks[1][0].setChecked(True)
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
                assert window._tab_btns[index].objectName() == "tabActive", name

            assert install_translator("en", qt_application) is True
            assert "es" in available_languages()
            es_idx = window.language_combo.findData("es")
            assert es_idx >= 0
            window.language_combo.setCurrentIndex(es_idx)
            qt_application.processEvents()
            assert window._config["language"] == "es"
            assert current_language() == "es"
            assert install_translator("en", qt_application) is True

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
