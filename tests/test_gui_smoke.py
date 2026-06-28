from types import SimpleNamespace
from unittest import mock

from PyQt6.QtCore import Qt

from streamkeep.models import HistoryEntry, MonitorEntry


def _ready_ytdlp_status():
    return {
        "state": "ready",
        "summary": "Ready",
        "detail": "yt-dlp 2026.01.01 with yt-dlp-ejs and deno 2.3.0.",
        "yt_dlp_version": "2026.01.01",
        "ejs_available": True,
        "js_runtime": {"name": "deno", "version": "2.3.0", "supported": True},
        "problems": [],
    }


def test_main_window_tabs_dialogs_and_language_smoke(tmp_path, qt_application):
    from streamkeep import accounts, notifications
    from streamkeep.i18n import install_translator
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
    }
    recording_dir = tmp_path / "recording"
    recording_dir.mkdir()

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
            mock.patch("streamkeep.search.index_all_async", lambda *args, **kwargs: None), \
            mock.patch(
                "streamkeep.ui.onboarding.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="ffmpeg version test\n"),
            ), \
            mock.patch(
                "streamkeep.ui.tabs.settings.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="2026.01.01\n"),
            ), \
            mock.patch("streamkeep.ui.tabs.settings.ytdlp_runtime_status", _ready_ytdlp_status), \
            mock.patch("streamkeep.ui.onboarding.ytdlp_runtime_status", _ready_ytdlp_status):
        main_window._db.init_db()

        window = main_window.StreamKeep()
        try:
            assert window._stack.count() == len(window._tab_names) == 6
            assert [button.text() for button in window._tab_btns] == window._tab_names

            for index, name in enumerate(window._tab_names):
                window._switch_tab(index)
                qt_application.processEvents()
                assert window._stack.currentIndex() == index
                assert window._tab_btns[index].objectName() == "tabActive", name

            assert install_translator("en", qt_application) is True

            dialogs = [
                NotificationLogDialog(window, window._notifications),
                MonitorEntryDialog(
                    window,
                    MonitorEntry(
                        url="https://example.com/channel",
                        platform="Example",
                        channel_id="example-channel",
                    ),
                    globals_preview=config,
                ),
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
