"""Regression coverage for the Monitor schedule refresh worker."""

import time
from types import SimpleNamespace
from unittest import mock

from PyQt6.QtCore import QObject

from streamkeep.models import MonitorEntry
from streamkeep.ui.tabs.monitor import MonitorTabMixin


class _Calendar:
    def __init__(self):
        self.refreshing = False
        self.cache = None
        self.error = ""

    def set_refreshing(self, value):
        self.refreshing = bool(value)

    def set_cache(self, cache):
        self.cache = cache

    def set_refresh_error(self, message):
        self.error = str(message or "")
        self.refreshing = False


class _MonitorHost(QObject, MonitorTabMixin):
    def __init__(self):
        super().__init__()
        self._config = {"schedules": {"old": {"segments": []}}}
        self.monitor = SimpleNamespace(entries=[
            MonitorEntry(platform="Twitch", channel_id="channel"),
        ])
        self.schedule_calendar = _Calendar()
        self.log_messages = []
        self.status_messages = []
        self.persist_count = 0

    def _log(self, message):
        self.log_messages.append(message)

    def _set_status(self, message, level):
        self.status_messages.append((message, level))

    def _schedule_persist_config(self):
        self.persist_count += 1


def _wait_for(app, predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


def test_schedule_refresh_applies_cache_and_forwards_logs(qt_application):
    host = _MonitorHost()
    refreshed = {"channel": {"segments": [{"title": "fixture"}]}}

    def fake_refresh(entries, cache, log_fn=None):
        assert len(entries) == 1
        assert cache == {"old": {"segments": []}}
        if log_fn:
            log_fn("[SCHEDULE] fixture fetch complete")
        return refreshed

    with mock.patch("streamkeep.schedule.refresh_schedules", side_effect=fake_refresh):
        host._on_refresh_schedules()
        _wait_for(qt_application, lambda: not host.schedule_calendar.refreshing)

    assert host._config["schedules"] == refreshed
    assert host.schedule_calendar.cache == refreshed
    assert host.schedule_calendar.error == ""
    assert "[SCHEDULE] fixture fetch complete" in host.log_messages
    assert host.status_messages[-1] == ("Schedule cache refreshed.", "success")
    assert host.persist_count == 1


def test_schedule_refresh_surfaces_worker_failure(qt_application):
    host = _MonitorHost()

    with mock.patch(
        "streamkeep.schedule.refresh_schedules",
        side_effect=RuntimeError("fixture failure"),
    ):
        host._on_refresh_schedules()
        _wait_for(qt_application, lambda: not host.schedule_calendar.refreshing)

    assert host.schedule_calendar.error == "fixture failure"
    assert any("fixture failure" in message for message in host.log_messages)
    assert host.status_messages[-1] == (
        "Schedule refresh failed. See log for details.",
        "error",
    )
