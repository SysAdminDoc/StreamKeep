"""Shared paths and platform-specific constants.

Separated from `config` so modules that just need the config directory
don't have to import the whole persistence layer.
"""

import os
import sys
import subprocess
from pathlib import Path

# Windows-only: hide console windows that subprocess would otherwise spawn
_CREATE_NO_WINDOW = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "StreamKeep"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "streamkeep.log"
LOG_FILE_BACKUP = CONFIG_DIR / "streamkeep.log.1"
LOG_FILE_MAX_BYTES = 2_000_000  # rotate at ~2 MB
CRASH_LOG = CONFIG_DIR / "crash.log"
