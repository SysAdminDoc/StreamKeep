#!/usr/bin/env python3
"""StreamKeep — multi-platform stream/VOD downloader.

This file is a thin launcher. All code lives in the `streamkeep/` package:
- streamkeep.ui.main_window  — the StreamKeep QMainWindow class
- streamkeep.extractors      — platform-specific extractors
- streamkeep.workers         — async QThread workers
- streamkeep.postprocess     — ffmpeg converter + post-processing
- streamkeep.http            — curl-based HTTP helpers
- streamkeep.config          — JSON config persistence
- streamkeep.theme           — Catppuccin QSS stylesheet
- streamkeep.models          — StreamInfo / VODInfo / QualityInfo / etc.
- streamkeep.utils           — fmt/safe_filename/default_output_dir helpers
- streamkeep.monitor         — channel live-detection monitor
- streamkeep.clipboard       — clipboard URL watcher
- streamkeep.metadata        — metadata.json + NFO writer
- streamkeep.scrape          — direct URL detection + page scrape
"""

import multiprocessing
# MUST be called before any code that could spawn a child process in a
# frozen exe. Without this, a PyInstaller build that ever touches
# `multiprocessing` will re-execute the entire program in each child,
# which — combined with any subprocess([sys.executable, ...]) call —
# becomes an uncontrollable process explosion.
multiprocessing.freeze_support()

import sys
import os
from pathlib import Path

from streamkeep.bootstrap import bootstrap


def _launcher_has_cli_args():
    """Detect CLI mode without importing PyQt-backed CLI modules yet."""
    if len(sys.argv) <= 1:
        return False
    cli_triggers = {
        "download", "dl", "server", "extractors", "db", "snapshot", "backup",
        "startup-check",
        "--url", "--server", "--list-extractors", "--version", "--help", "-h",
        "--internal-ytdlp",
        "--internal-update-helper", "--update-transaction",
    }
    return any(arg in cli_triggers for arg in sys.argv[1:])


bootstrap(include_optional=not _launcher_has_cli_args())


def _branding_icon_path() -> Path:
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "icon.png")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "icon.png")
    current = Path(__file__).resolve()
    candidates.extend([current.parent / "icon.png", current.parent.parent / "icon.png", current.parent.parent.parent / "icon.png"])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path("icon.png")


from streamkeep import VERSION as _VERSION; _VERSION  # version grep anchor


def _restore_internal_cli_streams():
    """Restore redirected pipes for the windowed frozen yt-dlp helper.

    PyInstaller's ``console=False`` bootloader deliberately sets the Python
    standard streams to ``None``.  The internal yt-dlp subprocess is launched
    with stdout/stderr pipes, however, so reconnect those inherited Windows
    handles before yt-dlp starts writing progress and JSON output.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        import io
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        invalid_handle = ctypes.c_void_p(-1).value
        for stream_name, handle_id in (("stdout", -11), ("stderr", -12)):
            stream = getattr(sys, stream_name, None)
            if stream is not None and callable(getattr(stream, "write", None)):
                continue
            handle = kernel32.GetStdHandle(ctypes.c_ulong(handle_id).value)
            if not handle or handle == invalid_handle:
                continue
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
            raw = os.fdopen(fd, "wb", closefd=False)
            setattr(
                sys,
                stream_name,
                io.TextIOWrapper(
                    raw,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=True,
                    write_through=True,
                ),
            )
    except (AttributeError, OSError, ValueError):
        # The caller will receive yt-dlp's exit status even if a host blocks
        # access to the inherited handles.
        pass


def _run_internal_ytdlp():
    """Run the bundled yt-dlp module when the frozen app re-enters itself."""
    flag = "--internal-ytdlp"
    if flag not in sys.argv[1:]:
        return False
    flag_index = sys.argv.index(flag)
    _restore_internal_cli_streams()
    from yt_dlp import main as ytdlp_main
    ytdlp_main(sys.argv[flag_index + 1:])
    return True


def _pop_flag_value(flag):
    """Remove an internal flag and its value before Qt/CLI parse argv."""
    if flag not in sys.argv[1:]:
        return ""
    index = sys.argv.index(flag)
    if index + 1 >= len(sys.argv):
        del sys.argv[index]
        return ""
    value = sys.argv[index + 1]
    del sys.argv[index:index + 2]
    return value


def _run_internal_update_helper():
    transaction = _pop_flag_value("--internal-update-helper")
    if not transaction:
        return False
    from streamkeep.update_runtime import run_update_watchdog
    raise SystemExit(run_update_watchdog(transaction))


def main():
    if _run_internal_update_helper():
        return
    if _run_internal_ytdlp():
        return

    update_transaction = _pop_flag_value("--update-transaction")

    # CLI / headless mode (F42): detect subcommands before creating the GUI
    from streamkeep.cli import has_cli_args, run_cli
    if has_cli_args():
        run_cli()
        return

    from streamkeep.crash_log import setup_crash_logging
    setup_crash_logging()

    from PyQt6.QtGui import QIcon
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from streamkeep.theme import apply_theme
    from streamkeep.ui.main_window import StreamKeep

    app = QApplication(sys.argv)
    branding_icon = QIcon(str(_branding_icon_path()))
    app.setWindowIcon(branding_icon)
    app.setStyle("Fusion")
    # Apply saved theme (F20) — defaults to dark/Mocha
    try:
        import json
        from streamkeep.paths import CONFIG_DIR
        cfg_file = CONFIG_DIR / "config.json"
        if cfg_file.exists():
            with open(cfg_file, "r", encoding="utf-8") as _f:
                saved_theme = json.load(_f).get("theme", "dark")
        else:
            saved_theme = "dark"
    except Exception:
        saved_theme = "dark"
    apply_theme(saved_theme, app=app)

    win = StreamKeep()
    win.show()

    def _finish_startup():
        from streamkeep.paths import CONFIG_DIR
        from streamkeep.update_runtime import (
            cleanup_update_helper,
            consume_recovery_notice,
            mark_transaction_healthy,
        )
        if update_transaction:
            try:
                mark_transaction_healthy(update_transaction, _VERSION)
                win._log(f"[UPDATE] StreamKeep v{_VERSION} completed startup and is healthy.")
            except (OSError, ValueError) as exc:
                win._log(f"[UPDATE] Startup health confirmation failed: {exc}")
                return
        notice = consume_recovery_notice(CONFIG_DIR)
        if notice:
            message = str(notice.get("message", "Update rollback completed.") or "")
            log_path = str(notice.get("log_path", "") or "")
            win._log(f"[UPDATE RECOVERY] {message} Recovery log: {log_path}")
            win._set_status(message, "warning")
            win._notify_center(message, "warning")
        QTimer.singleShot(2500, lambda: cleanup_update_helper(sys.executable))

    # A build becomes healthy only after the full window initialization and
    # the first event-loop turn have both completed.
    QTimer.singleShot(0, _finish_startup)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
