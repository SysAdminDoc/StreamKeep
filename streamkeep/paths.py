"""Shared paths and platform-specific constants.

Separated from `config` so modules that just need the config directory
don't have to import the whole persistence layer.

Portable mode (F43): if ``portable.txt`` exists next to the main script
or frozen exe, all config/data goes into a ``data/`` subdirectory alongside
the executable instead of ``%APPDATA%\\StreamKeep``.
"""

import os
import sys
import subprocess
from pathlib import Path

# Windows-only: hide console windows that subprocess would otherwise spawn
_CREATE_NO_WINDOW = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)

# ffmpeg safety flags — restrict protocols and prevent stdin reads.
FFMPEG_SAFETY = [
    "-nostdin",
    "-protocol_whitelist", "file,pipe,http,https,tcp,tls,crypto",
]

# ── Portable mode detection (F43) ──────────────────────────────────
# Check for a ``portable.txt`` marker next to the exe/script.
_exe_dir = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.argv[0] or "."))).resolve()
# For PyInstaller one-file builds, _MEIPASS is a temp dir — use the exe's
# actual location instead.
if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).resolve().parent
PORTABLE = (_exe_dir / "portable.txt").is_file()

def _default_config_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "StreamKeep"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "StreamKeep"
    # Linux/BSD — XDG Base Directory spec
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    xdg_dir = base / "StreamKeep"
    # Auto-migrate legacy ~/StreamKeep/ if the XDG path doesn't exist yet
    legacy = Path.home() / "StreamKeep"
    if not xdg_dir.exists() and legacy.is_dir():
        try:
            legacy.rename(xdg_dir)
        except OSError:
            return legacy
    return xdg_dir


if PORTABLE:
    CONFIG_DIR = _exe_dir / "data"
else:
    CONFIG_DIR = _default_config_dir()

CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "streamkeep.log"
LOG_FILE_BACKUP = CONFIG_DIR / "streamkeep.log.1"
LOG_FILE_MAX_BYTES = 2_000_000  # rotate at ~2 MB
CRASH_LOG = CONFIG_DIR / "crash.log"


def bind_config_dir(path):
    """Bind every config-derived path before stateful modules are imported.

    CLI entry points call this immediately after argument parsing.  Modules
    such as :mod:`streamkeep.config` and :mod:`streamkeep.db` intentionally
    capture these values at import time, so rebinding must happen before those
    imports rather than mutating only ``CONFIG_DIR`` later.
    """
    global CONFIG_DIR, CONFIG_FILE, LOG_FILE, LOG_FILE_BACKUP, CRASH_LOG

    config_dir = Path(path).expanduser().resolve()
    CONFIG_DIR = config_dir
    CONFIG_FILE = config_dir / "config.json"
    LOG_FILE = config_dir / "streamkeep.log"
    LOG_FILE_BACKUP = config_dir / "streamkeep.log.1"
    CRASH_LOG = config_dir / "crash.log"
    return config_dir
