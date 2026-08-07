import atexit
import gc
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, QThread
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


# ── Qt lifetime ─────────────────────────────────────────────────────
# pytest finalises session-scoped fixtures inside the *last* item's teardown,
# which is why the access violation always surfaced with `runtestprotocol` as
# the innermost Python frame and no failing test. When this fixture returned,
# its local was the only reference to the QApplication, so the application was
# destroyed first and Qt then tore down every still-live widget underneath a
# dying application — a native crash with nothing to see at the Python level.
#
# The module-level reference keeps the application alive, and the retirement
# pass below closes the widgets and stops the threads while it still is. Both
# halves are load-bearing: measured over ten runs of
# `tests/test_gui_smoke.py tests/test_subtitle_ui.py -p no:randomly`, the
# unpatched suite crashed 7 times, the reference alone dropped that to 2, and
# the two together to 0.
_QT_APP = None


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    yield _QT_APP
    _retire_qt_objects(_QT_APP)


def _retire_qt_objects(app):
    """Destroy leftover Qt objects while the ``QApplication`` is still alive."""
    gc.collect()
    for obj in gc.get_objects():
        if not isinstance(obj, QThread):
            continue
        try:
            if obj.isRunning():
                obj.quit()
                obj.wait(5000)
        except RuntimeError:  # C++ side already gone
            pass

    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    for _ in range(5):
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


@pytest.fixture(autouse=True)
def _isolate_rate_governor():
    """Give every test a governor with no memory of the last one.

    The per-host rate governor keeps module-level state on purpose — in
    production a host's backoff has to outlive the job that earned it. In a
    suite that makes it shared mutable state, so a test that records a
    throttle could otherwise hold back an unrelated test's queue.
    """
    from streamkeep import governor

    governor.reset()
    governor.configure(
        enabled=True, default_concurrency=governor.DEFAULT_CONCURRENCY,
    )
    yield
    governor.reset()
