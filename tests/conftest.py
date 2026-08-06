import atexit
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# ── Config-directory isolation ──────────────────────────────────────
# Stateful modules capture their paths from ``streamkeep.paths`` at import
# time (``db.DB_PATH``, ``notifications.SECURITY_EVENT_LOG``, ``search.DB_PATH``
# and friends), so the bind has to happen before any of them is imported.
# conftest is imported ahead of every test module, which makes this the only
# place the rebind is reliable. Without it the suite reads and writes the real
# user profile — creating ``library.db``, appending security events, and
# routing the crash handler's output into the operator's own crash log.
_ISOLATED_CONFIG_DIR = Path(tempfile.mkdtemp(prefix="streamkeep-tests-"))
_SUITE_STARTED_AT = time.time()

from streamkeep import paths as _paths  # noqa: E402

_REAL_CONFIG_DIR = _paths.CONFIG_DIR
_paths.bind_config_dir(_ISOLATED_CONFIG_DIR)
_ISOLATED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)


@atexit.register
def _remove_isolated_config_dir():
    shutil.rmtree(_ISOLATED_CONFIG_DIR, ignore_errors=True)


def isolated_config_dir() -> Path:
    """Return the temporary config directory the suite is bound to."""
    return _paths.CONFIG_DIR


def real_config_dir() -> Path:
    """Return the platform config directory the suite must never touch."""
    return _REAL_CONFIG_DIR


def suite_started_at() -> float:
    """Return the epoch seconds captured before any StreamKeep import."""
    return _SUITE_STARTED_AT


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    app = QApplication.instance() or QApplication([])
    yield app
