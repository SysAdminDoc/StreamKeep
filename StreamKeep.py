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

from streamkeep.bootstrap import bootstrap
bootstrap()

import sys
import subprocess

from PyQt6.QtWidgets import QApplication, QMessageBox

from streamkeep import VERSION  # noqa: F401 — kept for `python StreamKeep.py --version` greps
from streamkeep.paths import _CREATE_NO_WINDOW
from streamkeep.theme import STYLESHEET
from streamkeep.crash_log import setup_crash_logging
from streamkeep.ui.main_window import StreamKeep


def main():
    setup_crash_logging()

    # QApplication must exist before any QWidget (e.g. a QMessageBox in the
    # ffmpeg error path). Create it first so every failure branch is safe.
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    ffmpeg_ok = False
    try:
        r = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        ffmpeg_ok = r.returncode == 0
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        ffmpeg_ok = False
    if not ffmpeg_ok:
        QMessageBox.critical(
            None, "StreamKeep",
            "ffmpeg not found (or failed to run) in PATH.\n"
            "Install ffmpeg and try again.",
        )
        sys.exit(1)

    win = StreamKeep()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
