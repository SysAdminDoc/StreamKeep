import json
import time
from types import SimpleNamespace
from unittest import mock

from PyQt6.QtWidgets import QWidget

from streamkeep import db, operations


def _seed_small_database(db_path):
    with mock.patch.object(db, "DB_PATH", db_path):
        db.init_db()
        db.enqueue_queue_job({
            "job_id": "queued-1", "title": "Queued title", "platform": "Twitch",
            "url": "https://example.test/queued", "status": "queued",
            "size_bytes": 2048, "duration_seconds": 120,
        })
        db.enqueue_queue_job({
            "job_id": "running-1", "title": "Running title", "platform": "YouTube",
            "url": "https://example.test/running", "status": "downloading",
        })
        failure_id = db.save_failed_job(
            url="https://example.test/failure",
            platform="Twitch",
            title="Failed title",
            stage="finalize",
            error="token=secret-value retry later",
            output_dir=str(db_path.parent / "private-output"),
            queue_data={"url": "https://example.test/failure", "title": "Failed title"},
            auto_retry=False,
            status="intervention",
        )
        db.save_monitor_channel({
            "url": "https://example.test/channel",
            "platform": "Twitch",
            "channel_id": "channel-one",
        })
        return failure_id


def test_operations_query_is_paged_filterable_and_reports_summary(tmp_path):
    db_path = tmp_path / "library.db"
    with mock.patch.object(db, "DB_PATH", db_path), mock.patch.object(
        operations.db, "DB_PATH", db_path,
    ):
        failure_id = _seed_small_database(db_path)
        page = operations.query_operations({"page_size": 2})
        assert page.total_count == 4
        assert len(page.rows) == 2
        assert page.summary.total_count == 4
        assert page.summary.estimated_size_bytes == 2048
        assert page.summary.monitor_count == 1

        failures = operations.query_operations({"state": "failed", "page_size": 20})
        assert [row.item_id for row in failures.rows] == [str(failure_id)]
        assert failures.rows[0].stage == "finalize"
        assert "secret-value" not in failures.rows[0].retry_reason
        assert failures.rows[0].failure_category == "unknown"
        assert failures.rows[0].remediation["message"]

        report = operations.export_operations_report({"source": "twitch"})
        report_text = json.dumps(report)
        assert report["row_count"] == 3
        assert "https://example.test" not in report_text
        assert "private-output" not in report_text
        assert "secret-value" not in report_text
        assert report["rows"][1]["remediation"]["message"]

        csv_path = db_path.parent / "operations.csv"
        operations.write_operations_report(csv_path, {"state": "failed"})
        csv_text = csv_path.read_text(encoding="utf-8")
        assert "remediation_message" in csv_text
        assert "https://" not in csv_text
        assert "private-output" not in csv_text

        path_title = db.save_failed_job(
            url="https://example.test/path-title",
            platform="Twitch",
            title=str(db_path.parent / "secret-recording"),
            stage="fetch",
            error="path failure",
            output_dir=str(db_path.parent / "secret-recording"),
            queue_data={},
            auto_retry=False,
            status="intervention",
        )
        path_report = operations.export_operations_report({"kind": "failure"})
        assert path_title
        assert "secret-recording" not in json.dumps(path_report)


def test_operations_actions_persist_and_reappear_after_query(tmp_path):
    db_path = tmp_path / "library.db"
    with mock.patch.object(db, "DB_PATH", db_path), mock.patch.object(
        operations.db, "DB_PATH", db_path,
    ):
        failure_id = _seed_small_database(db_path)
        retried = operations.retry_failure_ids([failure_id])
        assert retried == [{
            "failure_id": failure_id,
            "ok": True,
            "job_id": retried[0]["job_id"],
        }]
        assert operations.query_operations({"kind": "queue"}).total_count == 3

        discarded_id = db.save_failed_job(
            url="https://example.test/second-failure",
            platform="Reddit",
            title="Second failure",
            stage="resolve",
            error="bad request",
            auto_retry=False,
            status="intervention",
        )
        result = operations.discard_failure_ids([discarded_id])
        assert result == [{"failure_id": discarded_id, "ok": True}]
        failed_rows = operations.query_operations({"state": "failed"}).rows
        assert all(row.item_id != str(discarded_id) for row in failed_rows)

        restored = operations.restore_discarded_failure_ids([discarded_id])
        assert restored == [{"failure_id": discarded_id, "ok": True}]
        restored_failure = db.load_failed_job(discarded_id)
        assert restored_failure["status"] == "intervention"


def test_operations_page_stays_bounded_for_one_hundred_thousand_jobs(tmp_path):
    db_path = tmp_path / "library.db"
    with mock.patch.object(db, "DB_PATH", db_path), mock.patch.object(
        operations.db, "DB_PATH", db_path,
    ):
        db.init_db()
        conn = db._connect()
        try:
            rows = [(
                f"job-{index:06d}", index, f"https://example.test/{index}",
                f"Job {index}", "Synthetic", "", "queued", "", 0,
                "2026-08-02T00:00:00Z", "2026-08-02T00:00:00Z", "{}",
            ) for index in range(100_000)]
            conn.executemany(
                "INSERT INTO download_queue "
                "(job_id, position, url, title, platform, quality, status, "
                "recurrence, failure_id, created_at, updated_at, data) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()

        started = time.monotonic()
        page = operations.query_operations({"page": 1_000, "page_size": 50})
        elapsed = time.monotonic() - started
        assert page.total_count == 100_000
        assert len(page.rows) == 50
        assert elapsed < 5.0


def test_operations_tab_refresh_passes_the_window_filters(qt_application):
    from streamkeep.ui.tabs import operations as operations_tab

    filters = operations.OperationsFilters()
    summary = operations.OperationsSummary(
        total_count=0,
        active_count=0,
        failure_count=0,
        monitor_count=0,
        estimated_size_bytes=0,
        estimated_duration_seconds=0.0,
        last_success_at="",
        next_run_at="",
        retry_reason="",
        source_health=(),
    )
    page = operations.OperationsPage(
        filters=filters,
        rows=(),
        total_count=0,
        summary=summary,
    )
    window = QWidget()
    window._report_failure = lambda _message: None
    with mock.patch.object(operations_tab, "query_operations", return_value=page) as query:
        tab = operations_tab.build_operations_tab(window)
        try:
            requested_filters = query.call_args.args[0]
            assert isinstance(requested_filters, operations.OperationsFilters)
            assert requested_filters.page == 0
            assert window.operations_summary.text() != (
                "Operations state is temporarily unavailable."
            )
            assert window.operations_table.isHidden()
            assert not window.operations_empty_state.isHidden()
            assert window.operations_empty_title.text() == "No durable operations yet"
            assert "Download" in window.operations_empty_body.text()
        finally:
            tab.close()
            window.close()


def test_operations_discard_reports_and_exposes_undo(qt_application):
    from PyQt6.QtWidgets import QLabel
    from streamkeep.ui.tabs import operations as operations_tab

    notifications = []
    window = SimpleNamespace(
        operations_status=QLabel(),
        _notify_center=lambda message, tone: notifications.append((message, tone)),
    )
    with mock.patch.object(
        operations_tab, "_selected_failure_ids", return_value=[7, 8],
    ), mock.patch.object(
        operations_tab,
        "discard_failure_ids",
        return_value=[
            {"failure_id": 7, "ok": True},
            {"failure_id": 8, "ok": True},
        ],
    ), mock.patch.object(operations_tab, "_refresh_operations"):
        operations_tab._run_operation_action(window, "discard")

    assert window._discarded_operation_failure_ids == [7, 8]
    assert "Undo discarded failures" in window.operations_status.text()
    assert notifications[-1][1] == "success"
