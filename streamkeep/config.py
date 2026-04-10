"""Config persistence + rotating log file.

Free-form JSON dict for now. A typed dataclass wrapper lands in Phase 3b.
"""

import json
from datetime import datetime

from .paths import CONFIG_DIR, CONFIG_FILE, LOG_FILE, LOG_FILE_BACKUP, LOG_FILE_MAX_BYTES


def load_config():
    """Load the config JSON. Returns {} on any error."""
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
    except Exception:
        pass
    return {}


def save_config(cfg):
    """Persist the config dict. Silent on error."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


def write_log_line(msg):
    """Append a timestamped line to the rotating log file.
    Rotates streamkeep.log -> streamkeep.log.1 when it exceeds the cap."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_FILE_MAX_BYTES:
            try:
                if LOG_FILE_BACKUP.exists():
                    LOG_FILE_BACKUP.unlink()
                LOG_FILE.rename(LOG_FILE_BACKUP)
            except Exception:
                pass
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass
