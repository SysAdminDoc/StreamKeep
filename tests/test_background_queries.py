"""Background archive queries keep the Qt event thread responsive."""

import threading
from types import SimpleNamespace
from unittest import mock

from PyQt6.QtWidgets import QLineEdit, QListWidget, QWidget

from streamkeep.ui.main_window import StreamKeep
from streamkeep.ui.tabs import analytics
from streamkeep.ui.worker_teardown import iter_owned_workers


def _process_worker_result(application, worker):
    assert worker.wait(5000)
    for _ in range(3):
        application.processEvents()


def _analytics_stats(total):
    return {
        "total": total,
        "size_gb": float(total),
        "platforms": [("youtube", total)] if total else [],
        "channels": [("channel", total)] if total else [],
        "daily": [("2026-08-12", total)] if total else [],
    }


def test_analytics_discards_a_superseded_query_and_tracks_busy_state(
    qt_application,
):
    window = QWidget()
    page = analytics.build_analytics_tab(window)
    first_started = threading.Event()
    release_first = threading.Event()
    busy_events = []

    def begin_busy(message):
        busy_events.append(("begin", message))
        return lambda: busy_events.append(("done", message))

    def query(_cutoff):
        if not first_started.is_set():
            first_started.set()
            assert release_first.wait(5)
            return _analytics_stats(99)
        return _analytics_stats(2)

    window._begin_background_activity = begin_busy
    workers = []
    try:
        with mock.patch.object(analytics._db, "history_analytics", side_effect=query):
            workers.append(analytics._refresh_analytics(window))
            assert first_started.wait(5)
            assert any(
                label.startswith("analytics_workers[")
                for label, _worker in iter_owned_workers(window)
            )

            window.analytics_range.setCurrentIndex(1)
            current = window._analytics_workers[window._analytics_generation]
            workers.append(current)
            _process_worker_result(qt_application, current)
            assert window.analytics_total_val.text() == "2"

            release_first.set()
            _process_worker_result(qt_application, workers[0])
            assert window.analytics_total_val.text() == "2"

        assert [event for event, _message in busy_events].count("begin") == 2
        assert [event for event, _message in busy_events].count("done") == 2
    finally:
        release_first.set()
        for worker in workers:
            worker.cancel()
            worker.wait(5000)
        page.close()
        window.close()


def _search_host(query):
    host = SimpleNamespace(
        _global_search=QLineEdit(query),
        _global_results=QListWidget(),
        _download_queue=[],
        monitor=SimpleNamespace(entries=[]),
        _global_search_workers={},
        _global_search_generation=0,
        busy_events=[],
        log_messages=[],
        toast_messages=[],
    )
    host._begin_background_activity = lambda message: _begin_search_busy(
        host, message,
    )
    host._apply_global_search_results = lambda generation, payload: (
        StreamKeep._apply_global_search_results(host, generation, payload)
    )
    host._show_global_search_error = lambda generation, error: (
        StreamKeep._show_global_search_error(host, generation, error)
    )
    host._log = host.log_messages.append
    host._toast = lambda message, tone: host.toast_messages.append((message, tone))
    return host


def _begin_search_busy(host, message):
    host.busy_events.append(("begin", message))
    return lambda: host.busy_events.append(("done", message))


def test_global_search_discards_superseded_results_and_exposes_its_worker(
    qt_application,
):
    host = _search_host("first")
    first_started = threading.Event()
    release_first = threading.Event()
    workers = []

    def search(query, _cap, _monitor_entries, _queue_items):
        if query == "first":
            first_started.set()
            assert release_first.wait(5)
        return {
            "items": [("history", {"query": query}, f"result:{query}")],
            "errors": [],
        }

    try:
        with mock.patch(
            "streamkeep.ui.main_window._run_global_search_query",
            side_effect=search,
        ):
            workers.append(StreamKeep._on_global_search(host))
            assert first_started.wait(5)
            assert any(
                label.startswith("global_search_workers[")
                for label, _worker in iter_owned_workers(host)
            )

            host._global_search.setText("second")
            workers.append(StreamKeep._on_global_search(host))
            _process_worker_result(qt_application, workers[1])
            assert host._global_results.item(0).text() == "result:second"

            release_first.set()
            _process_worker_result(qt_application, workers[0])
            assert host._global_results.item(0).text() == "result:second"

        assert [event for event, _message in host.busy_events].count("begin") == 2
        assert [event for event, _message in host.busy_events].count("done") == 2
    finally:
        release_first.set()
        for worker in workers:
            worker.cancel()
            worker.wait(5000)
        host._global_results.close()
        host._global_search.close()
