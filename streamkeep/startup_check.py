"""Offscreen startup contract for source and frozen release artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time


_FIXTURE_COUNTS = {
    "empty": {"history": 0, "monitor_channels": 0, "download_queue": 0},
    "migrated": {"history": 1, "monitor_channels": 1, "download_queue": 1},
    "populated": {"history": 1, "monitor_channels": 1, "download_queue": 1},
}


def _write_result(path, payload):
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)


def _fixture_rows(config_dir, fixture):
    recording_dir = config_dir / "fixture-recording"
    recording_dir.mkdir(parents=True, exist_ok=True)
    media_path = recording_dir / "capture.mp4"
    media_path.write_bytes(b"StreamKeep packaged startup fixture")

    from PyQt6.QtGui import QColor, QImage
    from .postprocess.thumb_worker import single_thumb_path

    cache_path = Path(single_thumb_path(str(media_path)))
    image = QImage(16, 9, QImage.Format.Format_RGB32)
    image.fill(QColor("#89b4fa"))
    if not image.save(str(cache_path), "JPG"):
        raise RuntimeError("could not create the startup thumbnail fixture")

    history = {
        "date": "2026-07-16 00:00",
        "platform": "yt-dlp",
        "title": f"{fixture.title()} startup fixture",
        "channel": "artifact-check",
        "quality": "source",
        "size": "fixture",
        "path": str(recording_dir),
        "url": "https://example.com/artifact-check",
    }
    monitor = {
        "url": "https://example.com/channel/artifact-check",
        "platform": "Example",
        "channel_id": "artifact-check",
        "interval_secs": 3600,
        "auto_record": False,
        "subscribe_vods": False,
        "archive_ids": [],
    }
    queue = {
        "url": "https://example.com/queued-artifact-check",
        "title": "Paused startup fixture",
        "platform": "Example",
        "status": "paused",
    }
    return history, monitor, queue


def _prepare_fixture(config_dir, fixture):
    config_path = config_dir / "config.json"
    database_path = config_dir / "library.db"
    if config_path.exists() or database_path.exists():
        raise RuntimeError("startup-check requires a fresh isolated config directory")

    config_dir.mkdir(parents=True, exist_ok=True)
    output_dir = config_dir / "downloads"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "output_dir": str(output_dir),
        "check_for_updates": False,
        "companion_server_enabled": False,
        "first_run_complete": True,
    }

    if fixture == "empty":
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return

    history, monitor, queue = _fixture_rows(config_dir, fixture)
    if fixture == "migrated":
        config.update({
            "history": [history],
            "monitor_channels": [monitor],
            "download_queue": [queue],
        })
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return

    config_path.write_text(json.dumps(config), encoding="utf-8")
    from . import db
    db.init_db()
    db.save_history_entry(history)
    db.save_all_monitor_channels([monitor])
    db.save_queue([queue])


def run_startup_check(*, ready_file, fixture="empty"):
    """Run the artifact startup check and atomically write its result."""
    started = time.monotonic()
    payload = {
        "schema_version": 1,
        "ready": False,
        "fixture": fixture,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    window = None
    try:
        if fixture not in _FIXTURE_COUNTS:
            raise ValueError(f"unknown startup fixture: {fixture}")

        # This command is never allowed to touch the user's visible desktop,
        # even when an inherited environment requests another Qt platform.
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from .paths import CONFIG_DIR
        config_dir = Path(CONFIG_DIR).resolve()
        _prepare_fixture(config_dir, fixture)

        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication, QMainWindow
        app = QApplication.instance() or QApplication(["StreamKeep", "startup-check"])

        from . import db
        from .capabilities import (
            MINIMUM_VERSIONS,
            get_runtime_capabilities,
            version_at_least,
        )
        from .extractors.ytdlp import ytdlp_command
        from .ui.main_window import StreamKeep
        from yt_dlp.version import __version__ as ytdlp_version

        window = StreamKeep(startup_check=True)
        for _ in range(3):
            app.processEvents()

        diagnostics = db.db_diagnostics()
        counts = diagnostics.get("row_counts", {})
        expected = _FIXTURE_COUNTS[fixture]
        top_levels = list(app.topLevelWidgets())
        application_windows = [
            widget for widget in top_levels if isinstance(widget, QMainWindow)
        ]
        visible_top_levels = [widget for widget in top_levels if widget.isVisible()]
        thumbnail_pixmap = (
            window.history_model.data(
                window.history_model.index(0, 0),
                Qt.ItemDataRole.DecorationRole,
            )
            if window.history_model.rowCount() else None
        )
        thumbnail_rendered = bool(
            thumbnail_pixmap is not None and not thumbnail_pixmap.isNull()
        )
        config_after = json.loads(
            (config_dir / "config.json").read_text(encoding="utf-8")
        )
        legacy_keys_removed = not any(
            key in config_after
            for key in ("history", "monitor_channels", "download_queue")
        )
        command = ytdlp_command()
        runtime_registry = get_runtime_capabilities(refresh=True)
        ytdlp_record = runtime_registry["yt_dlp"]
        ejs_record = runtime_registry["yt_dlp_ejs"]
        sqlite_record = runtime_registry["sqlite"]

        checks = {
            "database_path_bound": Path(diagnostics.get("path", "")).resolve()
            == config_dir / "library.db",
            "database_counts": all(
                int(counts.get(name, -1)) == value
                for name, value in expected.items()
            ),
            "history_loaded": window.history_model.total_count == expected["history"],
            "history_table_initialized": window.history_model.rowCount()
            == expected["history"],
            "thumbnail_loader_initialized": all(hasattr(window, name) for name in (
                "_history_thumb_loader", "_storage_thumb_loader", "_preview_loader"
            )),
            "thumbnail_rendered": thumbnail_rendered if expected["history"] else True,
            "legacy_config_migrated": legacy_keys_removed,
            "embedded_ytdlp_available": bool(str(ytdlp_version or "").strip()),
            "embedded_ytdlp_supported": (
                ytdlp_record.get("supported") is True
                and version_at_least(
                    ytdlp_version, MINIMUM_VERSIONS["yt_dlp"]
                )
            ),
            "embedded_ytdlp_ejs_compatible": ejs_record.get("supported") is True,
            "sqlite_runtime_safe": sqlite_record.get("supported") is True,
            "frozen_sqlite_wal_reset_fixed": (
                sqlite_record.get("wal_reset_fixed") is True
                if getattr(sys, "frozen", False) else True
            ),
            "embedded_ytdlp_runner": (
                "--internal-ytdlp" in command
                if getattr(sys, "frozen", False)
                else "-m" in command
            ),
            "single_qapplication": QApplication.instance() is app,
            "single_application_window": len(application_windows) == 1,
            "no_visible_windows": not visible_top_levels,
        }
        payload.update({
            "checks": checks,
            "ready": all(checks.values()),
            "config_dir": str(config_dir),
            "database": diagnostics,
            "history_loaded": window.history_model.total_count,
            "history_table_rows": window.history_model.rowCount(),
            "thumbnail_rendered": thumbnail_rendered,
            "qt_application_instances": 1 if QApplication.instance() is app else 0,
            "application_windows": len(application_windows),
            "top_level_widgets": len(top_levels),
            "visible_top_level_widgets": len(visible_top_levels),
            "ytdlp_version": str(ytdlp_version),
            "ytdlp_minimum_version": MINIMUM_VERSIONS["yt_dlp"],
            "ytdlp_ejs_version": ejs_record.get("version", ""),
            "ytdlp_ejs_requirement": ejs_record.get("required_by_ytdlp", ""),
            "ytdlp_command": command,
        })
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if window is not None:
            try:
                window.close()
                from PyQt6.QtWidgets import QApplication
                app = QApplication.instance()
                if app is not None:
                    app.processEvents()
            except Exception as exc:
                payload["close_error"] = f"{type(exc).__name__}: {exc}"
                payload["ready"] = False
        payload["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        _write_result(ready_file, payload)
    return payload
