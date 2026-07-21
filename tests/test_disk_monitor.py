"""Dedicated coverage for the storage-health monitor (F67).

Exercises the threshold state machine, status formatting, and color mapping
without a display server or a running Qt event loop — signals are captured via
direct-connected Python slots and ``shutil.disk_usage`` is stubbed.
"""

import os
from collections import namedtuple
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication  # noqa: E402

from streamkeep.disk_monitor import DiskMonitor  # noqa: E402

_Usage = namedtuple("_Usage", "total used free")
_GB = 1024 ** 3


@pytest.fixture(scope="module")
def _app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def _monitor(_app):
    mon = DiskMonitor()
    mon.set_paths(["/data"])
    mon.configure(warning_gb=20, critical_gb=5, auto_pause=True)
    return mon


def _poll_with_free(mon, free_gb):
    usage = _Usage(total=1000 * _GB, used=0, free=int(free_gb * _GB))
    with mock.patch("streamkeep.disk_monitor.shutil.disk_usage", return_value=usage):
        mon._poll()


def test_healthy_space_emits_no_alert(_app):
    mon = _monitor(_app)
    warnings, criticals, changes = [], [], []
    mon.space_warning.connect(lambda p, f: warnings.append((p, f)))
    mon.space_critical.connect(lambda p, f: criticals.append((p, f)))
    mon.space_changed.connect(lambda p, f, t: changes.append((p, f, t)))
    _poll_with_free(mon, 100)
    assert warnings == []
    assert criticals == []
    assert changes and changes[0][0] == "/data"
    assert mon.get_color() == "#a6e3a1"


def test_warning_then_critical_transitions_fire_once(_app):
    mon = _monitor(_app)
    warnings, criticals = [], []
    mon.space_warning.connect(lambda p, f: warnings.append(f))
    mon.space_critical.connect(lambda p, f: criticals.append(f))

    _poll_with_free(mon, 12)          # below 20 GB warning
    _poll_with_free(mon, 11)          # still warning — must NOT re-emit
    assert len(warnings) == 1
    assert mon.get_color() == "#f9e2af"

    _poll_with_free(mon, 3)           # below 5 GB critical
    _poll_with_free(mon, 2)           # still critical — must NOT re-emit
    assert len(criticals) == 1
    assert mon.get_color() == "#f38ba8"


def test_recovery_re_arms_alerts(_app):
    mon = _monitor(_app)
    warnings, criticals = [], []
    mon.space_warning.connect(lambda p, f: warnings.append(f))
    mon.space_critical.connect(lambda p, f: criticals.append(f))

    _poll_with_free(mon, 2)           # critical
    _poll_with_free(mon, 100)         # recover to ok
    _poll_with_free(mon, 2)           # critical again -> should re-emit
    assert len(criticals) == 2


def test_format_status_and_unreadable_path(_app):
    mon = _monitor(_app)
    usage = _Usage(total=1000 * _GB, used=0, free=42 * _GB)
    with mock.patch("streamkeep.disk_monitor.shutil.disk_usage", return_value=usage):
        assert "42.0 GB free" in mon.format_status()
    with mock.patch("streamkeep.disk_monitor.shutil.disk_usage", side_effect=OSError):
        assert mon.format_status() == "N/A"


def test_configure_reads_auto_pause(_app):
    mon = DiskMonitor()
    mon.configure(warning_gb=10, critical_gb=2, auto_pause=False)
    assert mon.auto_pause is False
    mon.configure(warning_gb=10, critical_gb=2, auto_pause=True)
    assert mon.auto_pause is True


def test_config_keys_are_import_validated():
    from streamkeep.config import _BOOL_CONFIG_KEYS, _INT_CONFIG_KEYS

    assert {"disk_monitor_enabled", "disk_auto_pause"} <= _BOOL_CONFIG_KEYS
    assert {"disk_warning_gb", "disk_critical_gb"} <= _INT_CONFIG_KEYS
