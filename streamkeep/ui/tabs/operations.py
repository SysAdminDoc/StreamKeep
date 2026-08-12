"""Operations tab — one paged view of queue, monitor, and failure state."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...operations import (
    OperationsFilters,
    discard_failure_ids,
    query_operations,
    restore_discarded_failure_ids,
    retry_failure_ids,
    write_operations_report,
)
from ...i18n import tr
from ...theme import CAT
from ...utils import fmt_size
from ..widgets import make_empty_state, make_metric_card, set_accessible, style_table


def _format_duration(seconds):
    seconds = max(0.0, float(seconds or 0))
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    return f"{minutes / 60:.1f}h"


def _current_filters(win):
    return OperationsFilters.from_mapping(
        state=win.operations_state_filter.currentData() or "",
        source=win.operations_source_filter.text(),
        stage=win.operations_stage_filter.text(),
        kind=win.operations_kind_filter.currentData() or "",
        search=win.operations_search.text(),
        page=getattr(win, "_operations_page", 0),
        page_size=50,
    )


def _selected_failure_ids(win):
    selected = set()
    for index in win.operations_table.selectionModel().selectedRows():
        item = win.operations_table.item(index.row(), 1)
        if item is None:
            continue
        value = item.data(Qt.ItemDataRole.UserRole)
        if value:
            selected.add(int(value))
    return sorted(selected)


def _update_operation_actions(win):
    has_failures = bool(_selected_failure_ids(win))
    win.operations_retry_btn.setEnabled(has_failures)
    win.operations_discard_btn.setEnabled(has_failures)


def _refresh_operations(win):
    """Refresh one bounded page from the durable operations query."""
    try:
        result = query_operations(_current_filters(win))
    except Exception as error:
        win.operations_table.setRowCount(0)
        win.operations_table.setVisible(False)
        win.operations_empty_title.setText("Operations are unavailable")
        win.operations_empty_body.setText(
            "Check the local data directory, then press Refresh to try again."
        )
        win.operations_empty_state.setVisible(True)
        win.operations_summary.setText("Operations state is temporarily unavailable.")
        win.operations_page_label.setText("Page unavailable")
        win.operations_status.setText(f"Could not read operations: {error}")
        _update_operation_actions(win)
        return

    summary = result.summary
    win.operations_total_value.setText(str(summary.total_count))
    win.operations_active_value.setText(str(summary.active_count))
    win.operations_failure_value.setText(str(summary.failure_count))
    win.operations_monitor_value.setText(str(summary.monitor_count))
    details = (
        f"Estimate {fmt_size(summary.estimated_size_bytes)} • "
        f"{_format_duration(summary.estimated_duration_seconds)} total duration • "
        f"last success {summary.last_success_at or '—'} • "
        f"next run {summary.next_run_at or '—'}"
    )
    if summary.retry_reason:
        details += f" • latest retry: {summary.retry_reason}"
    win.operations_summary.setText(details)

    table = win.operations_table
    table.setSortingEnabled(False)
    table.clearContents()
    table.setRowCount(len(result.rows))
    for row_index, row in enumerate(result.rows):
        remediation = row.remediation if row.kind == "failure" else {}
        remediation_text = (
            tr(remediation["message"], context="FailureRemediation")
            if remediation.get("message") else "—"
        )
        if remediation.get("action"):
            remediation_text += " (" + tr(
                remediation["action"], context="FailureRemediation"
            ) + ")"
        values = (
            row.kind,
            row.item_id,
            row.title or "—",
            row.source or "Unknown",
            row.state or "—",
            row.stage or "—",
            row.retry_reason or "—",
            remediation_text,
            row.next_run_at or "—",
            (
                f"{fmt_size(row.size_bytes)} / {_format_duration(row.duration_seconds)}"
                if row.size_bytes or row.duration_seconds else "—"
            ),
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if column == 1 and row.kind == "failure":
                item.setData(Qt.ItemDataRole.UserRole, row.item_id)
            if row.kind == "failure":
                failure_tint = QColor(CAT["red"])
                failure_tint.setAlpha(20)
                item.setBackground(QBrush(failure_tint))
            if column == 0:
                item.setForeground(QBrush(QColor(
                    CAT["red"] if row.kind == "failure" else CAT["accent"]
                )))
            elif column == 4:
                state = str(row.state or "").casefold()
                if state in {"active", "downloading", "running", "live"}:
                    state_color = CAT["green"]
                elif state in {"failed", "error"}:
                    state_color = CAT["red"]
                elif state in {"queued", "pending", "configured"}:
                    state_color = CAT["yellow"]
                else:
                    state_color = CAT["subtext1"]
                item.setForeground(QBrush(QColor(state_color)))
            table.setItem(row_index, column, item)
    has_rows = bool(result.rows)
    table.setVisible(has_rows)
    if has_rows:
        win.operations_empty_state.setVisible(False)
    else:
        filters = result.filters
        has_filters = any((
            filters.state, filters.source, filters.stage, filters.kind,
            filters.search,
        ))
        win.operations_empty_title.setText(
            "No operations match these filters"
            if has_filters else "No durable operations yet"
        )
        win.operations_empty_body.setText(
            "Clear or change a filter, then press Refresh."
            if has_filters else
            "Resolve a source on Download or add a channel on Monitor; queued, "
            "scheduled, and retryable work will appear here."
        )
        win.operations_empty_state.setVisible(True)
    table.setSortingEnabled(False)
    table.resizeRowsToContents()
    page_number = result.filters.page + 1
    page_count = max(1, (result.total_count + result.filters.page_size - 1) // result.filters.page_size)
    win.operations_page_label.setText(
        f"Page {page_number} of {page_count} • {result.total_count} item(s)"
    )
    win.operations_previous_btn.setEnabled(result.filters.page > 0)
    win.operations_next_btn.setEnabled(result.to_dict()["has_next"])
    win.operations_status.setText(
        "Select failed rows to retry or discard them. Export contains no URLs or paths."
    )
    _update_operation_actions(win)


def _schedule_operations_refresh(win):
    win._operations_filter_timer.start()


def _change_operations_page(win, delta):
    current = getattr(win, "_operations_page", 0)
    win._operations_page = max(0, current + delta)
    _refresh_operations(win)


def _run_operation_action(win, action):
    failure_ids = _selected_failure_ids(win)
    if not failure_ids:
        return
    if action == "retry":
        results = retry_failure_ids(failure_ids)
    else:
        results = discard_failure_ids(failure_ids)
    succeeded = sum(1 for result in results if result.get("ok"))
    _refresh_operations(win)
    if action == "discard":
        restored_ids = [
            result["failure_id"] for result in results if result.get("ok")
        ]
        message = (
            f"Discarded {succeeded} of {len(failure_ids)} selected failure(s)."
        )
        if restored_ids:
            win._discarded_operation_failure_ids = restored_ids
            message += " Use Undo discarded failures in Notifications to restore them."
            win._notify_center(message, "success")
    else:
        message = (
            f"Retried {succeeded} of {len(failure_ids)} selected failure(s)."
        )
    win.operations_status.setText(message)


def undo_discarded_operations(win):
    """Restore the last desktop discard batch for a notification-menu undo."""
    failure_ids = list(getattr(win, "_discarded_operation_failure_ids", ()))
    if not failure_ids:
        return 0, 0
    results = restore_discarded_failure_ids(failure_ids)
    restored = sum(1 for result in results if result.get("ok"))
    failed_ids = [
        result["failure_id"] for result in results if not result.get("ok")
    ]
    win._discarded_operation_failure_ids = []
    _refresh_operations(win)
    return restored, len(failed_ids)


def _export_operations(win):
    path, _ = QFileDialog.getSaveFileName(
        win,
        "Export operations report",
        "operations-report.json",
        "JSON report (*.json);;CSV report (*.csv)",
    )
    if not path:
        return
    try:
        report = write_operations_report(path, _current_filters(win))
    except (OSError, ValueError) as error:
        message = f"Could not export operations: {error}"
        win.operations_status.setText(message)
        # operations_status sits at the bottom of a long scroll page and is
        # very likely off-screen when the export fails (V196).
        win._report_failure(message)
        return
    win.operations_status.setText(
        f"Exported {report['row_count']} row(s) to {path}"
        + (" (truncated at 10,000 rows)." if report["truncated"] else ".")
    )


def build_operations_tab(win):
    """Build the Operations tab and attach its durable view controls to *win*."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)

    hero = QFrame()
    hero.setObjectName("heroCard")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(2, 2, 2, 4)
    hero_lay.setSpacing(4)
    title = QLabel("Operations view")
    title.setObjectName("heroTitle")
    body = QLabel("One durable, filterable view of queue, monitor, and failure state.")
    body.setObjectName("heroBody")
    body.setWordWrap(True)
    hero_lay.addWidget(title)
    hero_lay.addWidget(body)
    metrics = QHBoxLayout()
    metrics.setSpacing(12)
    total_card, win.operations_total_value, _ = make_metric_card("Total", "0", "items")
    active_card, win.operations_active_value, _ = make_metric_card("Active", "0", "running")
    failure_card, win.operations_failure_value, _ = make_metric_card("Failures", "0", "actionable")
    monitor_card, win.operations_monitor_value, _ = make_metric_card("Monitors", "0", "configured")
    for card in (total_card, active_card, failure_card, monitor_card):
        metrics.addWidget(card, 1)
    hero_lay.addLayout(metrics)
    lay.addWidget(hero)

    filter_card = QFrame()
    filter_card.setObjectName("toolbar")
    filter_lay = QVBoxLayout(filter_card)
    filter_lay.setContentsMargins(14, 12, 14, 12)
    filter_lay.setSpacing(6)
    filter_row = QHBoxLayout()
    filter_row.setSpacing(8)

    win.operations_state_filter = QComboBox()
    win.operations_state_filter.addItem("All states", "")
    for label, value in (
        ("Active", "active"),
        ("Failed", "failed"),
        ("Queued", "queued"),
        ("Downloading", "downloading"),
        ("Running", "running"),
        ("Configured", "configured"),
    ):
        win.operations_state_filter.addItem(label, value)
    set_accessible(win.operations_state_filter, "Operations state filter", "Filter by operation state")
    filter_row.addWidget(win.operations_state_filter)

    win.operations_kind_filter = QComboBox()
    win.operations_kind_filter.addItem("All kinds", "")
    for label, value in (("Queue", "queue"), ("Failure", "failure"), ("Monitor", "monitor")):
        win.operations_kind_filter.addItem(label, value)
    set_accessible(win.operations_kind_filter, "Operations kind filter", "Filter queue, failure, or monitor records")
    filter_row.addWidget(win.operations_kind_filter)

    win.operations_source_filter = QLineEdit()
    win.operations_source_filter.setPlaceholderText("Source / platform")
    win.operations_source_filter.setClearButtonEnabled(True)
    set_accessible(win.operations_source_filter, "Operations source filter", "Filter by source or platform")
    filter_row.addWidget(win.operations_source_filter, 1)

    win.operations_stage_filter = QLineEdit()
    win.operations_stage_filter.setPlaceholderText("Stage")
    win.operations_stage_filter.setClearButtonEnabled(True)
    set_accessible(win.operations_stage_filter, "Operations stage filter", "Filter by pipeline stage")
    filter_row.addWidget(win.operations_stage_filter, 1)

    win.operations_search = QLineEdit()
    win.operations_search.setPlaceholderText("Search title, source, or retry reason")
    win.operations_search.setClearButtonEnabled(True)
    set_accessible(win.operations_search, "Search operations", "Search durable operation text")
    filter_row.addWidget(win.operations_search, 2)
    filter_lay.addLayout(filter_row)

    action_row = QHBoxLayout()
    action_row.setSpacing(8)
    win.operations_refresh_btn = QPushButton("Refresh")
    win.operations_refresh_btn.setObjectName("secondary")
    set_accessible(win.operations_refresh_btn, "Refresh operations", "Reload the durable operations page")
    win.operations_refresh_btn.clicked.connect(lambda: _refresh_operations(win))
    action_row.addWidget(win.operations_refresh_btn)

    win.operations_export_btn = QPushButton("Export redacted report")
    win.operations_export_btn.setObjectName("secondary")
    set_accessible(win.operations_export_btn, "Export operations report", "Save a URL-free operations report")
    win.operations_export_btn.clicked.connect(lambda: _export_operations(win))
    action_row.addWidget(win.operations_export_btn)
    action_row.addStretch(1)

    win.operations_retry_btn = QPushButton("Retry selected")
    win.operations_retry_btn.setObjectName("primary")
    win.operations_retry_btn.setEnabled(False)
    set_accessible(win.operations_retry_btn, "Retry selected failures", "Retry selected failed operations")
    win.operations_retry_btn.clicked.connect(lambda: _run_operation_action(win, "retry"))
    action_row.addWidget(win.operations_retry_btn)

    win.operations_discard_btn = QPushButton("Discard selected")
    win.operations_discard_btn.setObjectName("danger")
    win.operations_discard_btn.setEnabled(False)
    set_accessible(win.operations_discard_btn, "Discard selected failures", "Discard selected failed operations")
    win.operations_discard_btn.clicked.connect(lambda: _run_operation_action(win, "discard"))
    action_row.addWidget(win.operations_discard_btn)
    filter_lay.addLayout(action_row)
    lay.addWidget(filter_card)

    table_card = QFrame()
    table_card.setObjectName("dataPane")
    table_lay = QVBoxLayout(table_card)
    table_lay.setContentsMargins(16, 14, 16, 12)
    table_lay.setSpacing(8)
    table_title = QLabel("Durable operations")
    table_title.setObjectName("sectionTitle")
    table_lay.addWidget(table_title)

    win.operations_summary = QLabel("Loading durable operations state…")
    win.operations_summary.setObjectName("sectionBody")
    win.operations_summary.setWordWrap(True)
    table_lay.addWidget(win.operations_summary)

    win.operations_table = QTableWidget(0, 10)
    win.operations_table.setHorizontalHeaderLabels(
        [
            "Kind", "ID", "Title", "Source", "State", "Stage", "Retry reason",
            "What to do", "Next run", "Estimate",
        ]
    )
    win.operations_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    win.operations_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    win.operations_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    win.operations_table.setAlternatingRowColors(True)
    win.operations_table.setMinimumHeight(230)
    win.operations_table.verticalHeader().setVisible(False)
    header = win.operations_table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)
    style_table(
        win.operations_table,
        42,
        accessible_name="Operations table",
        accessible_description="Paged queue, failure, and monitor state; select failed rows for actions",
    )
    win.operations_table.itemSelectionChanged.connect(lambda: _update_operation_actions(win))
    table_lay.addWidget(win.operations_table, 1)

    (
        win.operations_empty_state,
        win.operations_empty_title,
        win.operations_empty_body,
    ) = make_empty_state("No durable operations yet")
    win.operations_empty_state.setMinimumHeight(230)
    win.operations_empty_state.setVisible(False)
    table_lay.addWidget(win.operations_empty_state, 1)

    footer = QHBoxLayout()
    win.operations_page_label = QLabel("Page 1")
    win.operations_page_label.setObjectName("subtleText")
    footer.addWidget(win.operations_page_label)
    footer.addStretch(1)
    win.operations_previous_btn = QPushButton("Previous")
    win.operations_previous_btn.setObjectName("ghost")
    win.operations_previous_btn.setEnabled(False)
    set_accessible(win.operations_previous_btn, "Previous operations page")
    win.operations_previous_btn.clicked.connect(lambda: _change_operations_page(win, -1))
    footer.addWidget(win.operations_previous_btn)
    win.operations_next_btn = QPushButton("Next")
    win.operations_next_btn.setObjectName("ghost")
    win.operations_next_btn.setEnabled(False)
    set_accessible(win.operations_next_btn, "Next operations page")
    win.operations_next_btn.clicked.connect(lambda: _change_operations_page(win, 1))
    footer.addWidget(win.operations_next_btn)
    table_lay.addLayout(footer)

    win.operations_status = QLabel("Select failed rows to retry or discard them.")
    win.operations_status.setObjectName("subtleText")
    win.operations_status.setWordWrap(True)
    table_lay.addWidget(win.operations_status)
    lay.addWidget(table_card, 1)

    win._operations_page = 0
    win._operations_filter_timer = QTimer(win)
    win._operations_filter_timer.setSingleShot(True)
    win._operations_filter_timer.setInterval(220)
    win._operations_filter_timer.timeout.connect(lambda: _refresh_operations(win))
    win.operations_state_filter.currentIndexChanged.connect(lambda: _refresh_operations(win))
    win.operations_kind_filter.currentIndexChanged.connect(lambda: _refresh_operations(win))
    for field in (win.operations_source_filter, win.operations_stage_filter, win.operations_search):
        field.textChanged.connect(lambda _text: _schedule_operations_refresh(win))

    _refresh_operations(win)
    return page
