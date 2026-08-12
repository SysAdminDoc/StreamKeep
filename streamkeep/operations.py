"""Durable, paged operations view over queue, monitor, and failure state.

The operations surface deliberately queries SQLite instead of process-local
lists.  Desktop, CLI, and REST callers therefore see the same rows after a
restart, and a large queue never has to be materialized just to render page 1.
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import db
from .diagnostics import redact_text
from .retry import failure_remediation, sanitize_failure_reason

MAX_PAGE_SIZE = 200
MAX_EXPORT_ROWS = 10_000
ACTIVE_STATES = frozenset({
    "fetching", "downloading", "finalizing", "running", "cancelling",
})
FAILURE_STATES = frozenset({
    "failed", "retryable", "retrying", "intervention",
})
_URL_RE = re.compile(r"(?i)\b(?:https?|rtmps?|rtsp|srt|ftp)://[^\s<>'\"]+")
_WINDOWS_PATH_RE = re.compile(r"(?i)(?<![\w])(?:[a-z]:[\\/]|\\\\)[^<>\r\n\"']+")
_POSIX_PATH_RE = re.compile(
    r"(?<![\w])/(?:Users|home|tmp|var|mnt|opt|private)/[^<>\r\n\"']+",
    re.I,
)


@dataclass(frozen=True)
class OperationsFilters:
    """Normalized filters shared by the desktop, CLI, REST, and export paths."""

    state: str = ""
    source: str = ""
    stage: str = ""
    kind: str = ""
    search: str = ""
    page: int = 0
    page_size: int = 50

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None = None, **overrides):
        raw = dict(value or {})
        raw.update(overrides)
        try:
            page = max(0, int(raw.get("page", 0) or 0))
        except (TypeError, ValueError):
            page = 0
        try:
            page_size = max(1, min(MAX_PAGE_SIZE, int(raw.get("page_size", 50) or 50)))
        except (TypeError, ValueError):
            page_size = 50
        return cls(
            state=str(raw.get("state", "") or "").strip().lower()[:64],
            source=str(raw.get("source", "") or "").strip()[:120],
            stage=str(raw.get("stage", "") or "").strip().lower()[:64],
            kind=str(raw.get("kind", "") or "").strip().lower()[:32],
            search=str(raw.get("search", "") or "").strip()[:160],
            page=page,
            page_size=page_size,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperationRow:
    """One normalized queue, failure, or monitor record."""

    kind: str
    item_id: str
    title: str
    source: str
    state: str
    stage: str
    failure_category: str
    retry_reason: str
    remediation: dict[str, str]
    next_run_at: str
    updated_at: str
    created_at: str
    retry_count: int
    retryable: bool
    size_bytes: int
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.item_id,
            "title": self.title,
            "source": self.source,
            "state": self.state,
            "stage": self.stage,
            "category": self.failure_category,
            "retry_reason": self.retry_reason,
            "remediation": dict(self.remediation),
            "next_run_at": self.next_run_at,
            "updated_at": self.updated_at,
            "created_at": self.created_at,
            "retry_count": self.retry_count,
            "retryable": self.retryable,
            "size_bytes": self.size_bytes,
            "duration_seconds": self.duration_seconds,
        }

    def to_redacted_dict(self) -> dict[str, Any]:
        """Return the deliberately URL/path-free export representation."""
        return {
            "kind": self.kind,
            "id": self.item_id,
            "title": self.title,
            "source": self.source,
            "state": self.state,
            "stage": self.stage,
            "category": self.failure_category,
            "retry_reason": self.retry_reason,
            "remediation": dict(self.remediation),
            "next_run_at": self.next_run_at,
            "updated_at": self.updated_at,
            "retry_count": self.retry_count,
            "retryable": self.retryable,
            "size_bytes": self.size_bytes,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class OperationsSummary:
    """Aggregate metrics for the current filter set."""

    total_count: int
    active_count: int
    failure_count: int
    monitor_count: int
    estimated_size_bytes: int
    estimated_duration_seconds: float
    last_success_at: str
    next_run_at: str
    retry_reason: str
    source_health: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_count": self.total_count,
            "batch_count": self.total_count,
            "active_count": self.active_count,
            "failure_count": self.failure_count,
            "monitor_count": self.monitor_count,
            "estimated_size_bytes": self.estimated_size_bytes,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "last_success_at": self.last_success_at,
            "next_run_at": self.next_run_at,
            "retry_reason": self.retry_reason,
            "source_health": [dict(item) for item in self.source_health],
        }


@dataclass(frozen=True)
class OperationsPage:
    """One stable page plus totals and navigation metadata."""

    filters: OperationsFilters
    rows: tuple[OperationRow, ...]
    total_count: int
    summary: OperationsSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "filters": self.filters.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
            "total_count": self.total_count,
            "page": self.filters.page,
            "page_size": self.filters.page_size,
            "has_previous": self.filters.page > 0,
            "has_next": (self.filters.page + 1) * self.filters.page_size < self.total_count,
            "summary": self.summary.to_dict(),
        }


_OPERATIONS_CTE = """
WITH operation_rows AS (
    SELECT
        'queue' AS kind,
        q.job_id AS item_id,
        q.title AS title,
        q.platform AS source,
        q.status AS state,
        '' AS stage,
        '' AS failure_category,
        '' AS retry_reason,
        CASE WHEN json_valid(q.data)
             THEN COALESCE(json_extract(q.data, '$.start_at'), '')
             ELSE '' END AS next_run_at,
        q.updated_at AS updated_at,
        q.created_at AS created_at,
        0 AS retry_count,
        0 AS retryable,
        CASE WHEN json_valid(q.data)
             THEN CAST(COALESCE(json_extract(q.data, '$.size_bytes'), 0) AS INTEGER)
             ELSE 0 END AS size_bytes,
        CASE WHEN json_valid(q.data)
             THEN CAST(COALESCE(
                 json_extract(q.data, '$.duration_seconds'),
                 json_extract(q.data, '$.duration_secs'), 0
             ) AS REAL)
             ELSE 0 END AS duration_seconds
    FROM download_queue q
    UNION ALL
    SELECT
        'failure' AS kind,
        CAST(f.id AS TEXT) AS item_id,
        f.title AS title,
        COALESCE(NULLIF(f.source_label, ''), f.platform) AS source,
        f.status AS state,
        f.stage AS stage,
        f.category AS failure_category,
        COALESCE(NULLIF(f.last_reason, ''), f.error) AS retry_reason,
        f.next_attempt_at AS next_run_at,
        f.updated_at AS updated_at,
        f.created_at AS created_at,
        f.retry_count AS retry_count,
        f.retryable AS retryable,
        0 AS size_bytes,
        0 AS duration_seconds
    FROM failed_jobs f
    UNION ALL
    SELECT
        'monitor' AS kind,
        CAST(m.id AS TEXT) AS item_id,
        m.channel_id AS title,
        m.platform AS source,
        'configured' AS state,
        'monitor' AS stage,
        '' AS failure_category,
        '' AS retry_reason,
        '' AS next_run_at,
        '' AS updated_at,
        '' AS created_at,
        0 AS retry_count,
        0 AS retryable,
        0 AS size_bytes,
        0 AS duration_seconds
    FROM monitor_channels m
)
"""


def _where_clause(filters: OperationsFilters):
    clauses = ["1=1"]
    params: list[Any] = []
    state = filters.state
    if state == "failed":
        clauses.append(
            "((kind = 'failure' AND lower(state) NOT IN ('resolved', 'discarded')) "
            "OR lower(state) IN "
            "('failed', 'retryable', 'retrying', 'intervention'))"
        )
    elif state == "active":
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        clauses.append(f"lower(state) IN ({placeholders})")
        params.extend(sorted(ACTIVE_STATES))
    elif state:
        clauses.append("lower(state) = ?")
        params.append(state)
    if filters.kind:
        clauses.append("lower(kind) = ?")
        params.append(filters.kind)
    if filters.source:
        clauses.append("lower(source) LIKE ?")
        params.append(f"%{filters.source.lower()}%")
    if filters.stage:
        clauses.append("lower(stage) LIKE ?")
        params.append(f"%{filters.stage.lower()}%")
    if filters.search:
        clauses.append(
            "(lower(title) LIKE ? OR lower(source) LIKE ? OR "
            "lower(retry_reason) LIKE ?)"
        )
        query = f"%{filters.search.lower()}%"
        params.extend([query, query, query])
    return " WHERE " + " AND ".join(clauses), params


def _safe_text(value: Any, limit: int = 300) -> str:
    text = redact_text(str(value or ""))
    text = _URL_RE.sub("[URL removed]", text)
    text = _WINDOWS_PATH_RE.sub("[path removed]", text)
    text = _POSIX_PATH_RE.sub("[path removed]", text)
    return text.replace("\r", " ").replace("\n", " ")[:limit]


def _row_from_sql(row) -> OperationRow:
    return OperationRow(
        kind=_safe_text(row["kind"], 32),
        item_id=_safe_text(row["item_id"], 128),
        title=_safe_text(row["title"]),
        source=_safe_text(row["source"], 120),
        state=_safe_text(row["state"], 64),
        stage=_safe_text(row["stage"], 64),
        failure_category=_safe_text(row["failure_category"], 64),
        retry_reason=sanitize_failure_reason(row["retry_reason"], limit=500),
        remediation=failure_remediation(
            row["failure_category"],
            reason=row["retry_reason"],
        ),
        next_run_at=_safe_text(row["next_run_at"], 80),
        updated_at=_safe_text(row["updated_at"], 80),
        created_at=_safe_text(row["created_at"], 80),
        retry_count=int(row["retry_count"] or 0),
        retryable=bool(row["retryable"]),
        size_bytes=max(0, int(row["size_bytes"] or 0)),
        duration_seconds=max(0.0, float(row["duration_seconds"] or 0)),
    )


def _summary(conn, where: str, params: list[Any]) -> OperationsSummary:
    row = conn.execute(
        _OPERATIONS_CTE
        + """
        SELECT COUNT(*) AS total_count,
               SUM(CASE WHEN lower(state) IN
                   ('fetching','downloading','finalizing','running','cancelling')
                   THEN 1 ELSE 0 END) AS active_count,
               SUM(CASE WHEN (kind='failure' AND lower(state) NOT IN
                   ('resolved','discarded')) OR lower(state) IN
                   ('failed','retryable','retrying','intervention')
                   THEN 1 ELSE 0 END) AS failure_count,
               SUM(CASE WHEN kind='monitor' THEN 1 ELSE 0 END) AS monitor_count,
               COALESCE(SUM(size_bytes), 0) AS estimated_size_bytes,
               COALESCE(SUM(duration_seconds), 0) AS estimated_duration_seconds,
               MIN(NULLIF(next_run_at, '')) AS next_run_at
          FROM operation_rows
        """
        + where,
        params,
    ).fetchone()
    health_rows = conn.execute(
        _OPERATIONS_CTE
        + """
        SELECT COALESCE(NULLIF(source, ''), 'Unknown') AS source,
               COUNT(*) AS total_count,
               SUM(CASE WHEN (kind='failure' AND lower(state) NOT IN
                   ('resolved','discarded')) OR lower(state) IN
                   ('failed','retryable','retrying','intervention')
                   THEN 1 ELSE 0 END) AS failure_count,
               SUM(CASE WHEN lower(state) IN
                   ('fetching','downloading','finalizing','running','cancelling')
                   THEN 1 ELSE 0 END) AS active_count
          FROM operation_rows
        """
        + where
        + " GROUP BY source ORDER BY total_count DESC, source ASC LIMIT 50",
        params,
    ).fetchall()
    try:
        last_success = conn.execute(
            "SELECT MAX(date) FROM history WHERE COALESCE(date, '') <> ''"
        ).fetchone()[0] or ""
    except Exception:
        last_success = ""
    reason_row = conn.execute(
        _OPERATIONS_CTE
        + """
        SELECT retry_reason FROM operation_rows
         """
        + where
        + " AND kind='failure' AND lower(state) NOT IN ('resolved','discarded') "
        + "AND retry_reason<>''"
        + " ORDER BY updated_at DESC, item_id DESC LIMIT 1",
        params,
    ).fetchone()
    return OperationsSummary(
        total_count=int(row["total_count"] or 0),
        active_count=int(row["active_count"] or 0),
        failure_count=int(row["failure_count"] or 0),
        monitor_count=int(row["monitor_count"] or 0),
        estimated_size_bytes=max(0, int(row["estimated_size_bytes"] or 0)),
        estimated_duration_seconds=max(0.0, float(row["estimated_duration_seconds"] or 0)),
        last_success_at=_safe_text(last_success, 80),
        next_run_at=_safe_text(row["next_run_at"], 80),
        retry_reason=sanitize_failure_reason(
            reason_row["retry_reason"] if reason_row else "", limit=500,
        ),
        source_health=tuple({
            "source": _safe_text(health["source"], 120),
            "total_count": int(health["total_count"] or 0),
            "failure_count": int(health["failure_count"] or 0),
            "active_count": int(health["active_count"] or 0),
        } for health in health_rows),
    )


def query_operations(filters: OperationsFilters | dict[str, Any] | None = None) -> OperationsPage:
    """Return one bounded, snapshot-stable page of normalized operations."""
    normalized = (
        filters if isinstance(filters, OperationsFilters)
        else OperationsFilters.from_mapping(filters)
    )
    where, params = _where_clause(normalized)
    conn = db._connect(readonly=True)
    try:
        total = int(conn.execute(
            _OPERATIONS_CTE + " SELECT COUNT(*) FROM operation_rows" + where,
            params,
        ).fetchone()[0] or 0)
        rows = conn.execute(
            _OPERATIONS_CTE
            + """
            SELECT * FROM operation_rows
            """
            + where
            + """
                ORDER BY CASE kind WHEN 'failure' THEN 0 WHEN 'queue' THEN 1 ELSE 2 END,
                         updated_at DESC, created_at DESC, item_id ASC
                LIMIT ? OFFSET ?
            """,
            [*params, normalized.page_size, normalized.page * normalized.page_size],
        ).fetchall()
        summary = _summary(conn, where, params)
    finally:
        conn.close()
    return OperationsPage(
        filters=normalized,
        rows=tuple(_row_from_sql(row) for row in rows),
        total_count=total,
        summary=summary,
    )


def _iter_pages(filters: OperationsFilters, max_rows: int):
    page = 0
    emitted = 0
    while emitted < max_rows:
        current = OperationsFilters.from_mapping(
            filters.to_dict(), page=page, page_size=min(MAX_PAGE_SIZE, max_rows - emitted),
        )
        result = query_operations(current)
        if not result.rows:
            break
        for row in result.rows:
            yield row
            emitted += 1
            if emitted >= max_rows:
                break
        if not result.to_dict()["has_next"]:
            break
        page += 1


def export_operations_report(
    filters: OperationsFilters | dict[str, Any] | None = None,
    *,
    max_rows: int = MAX_EXPORT_ROWS,
) -> dict[str, Any]:
    """Build a redacted report with no URLs, host paths, or raw error text."""
    normalized = (
        filters if isinstance(filters, OperationsFilters)
        else OperationsFilters.from_mapping(filters)
    )
    bounded = max(1, min(MAX_EXPORT_ROWS, int(max_rows or MAX_EXPORT_ROWS)))
    first_page = query_operations(OperationsFilters.from_mapping(
        normalized.to_dict(), page=0, page_size=MAX_PAGE_SIZE,
    ))
    rows = list(_iter_pages(normalized, bounded))
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filters": normalized.to_dict(),
        "summary": first_page.summary.to_dict(),
        "row_count": len(rows),
        "truncated": len(rows) < first_page.total_count,
        "rows": [row.to_redacted_dict() for row in rows],
    }


def write_operations_report(
    output_path: str | os.PathLike[str],
    filters: OperationsFilters | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write JSON or CSV based on the requested extension and return the report."""
    path = Path(output_path).expanduser()
    if not str(path).strip():
        raise ValueError("output path is required")
    report = export_operations_report(filters)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        fields = [
            "kind", "id", "title", "source", "state", "stage", "category",
            "retry_reason", "remediation_message", "remediation_action",
            "remediation_target",
            "next_run_at", "updated_at", "retry_count", "retryable",
            "size_bytes", "duration_seconds",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(
                {
                    **{
                        key: value for key, value in row.items()
                        if key != "remediation"
                    },
                    "remediation_message": row["remediation"]["message"],
                    "remediation_action": row["remediation"]["action"],
                    "remediation_target": row["remediation"]["target"],
                }
                for row in report["rows"]
            )
    else:
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def retry_failure_ids(failure_ids: Iterable[int | str]) -> list[dict[str, Any]]:
    """Promote selected failure rows into durable queued jobs."""
    results = []
    for raw_id in failure_ids:
        try:
            failure_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if failure_id <= 0:
            continue
        job = db.promote_failed_job_retry(failure_id)
        results.append({
            "failure_id": failure_id,
            "ok": bool(job),
            "job_id": str(job.get("job_id", "")) if job else "",
        })
    return results


def discard_failure_ids(failure_ids: Iterable[int | str]) -> list[dict[str, Any]]:
    """Mark selected failure rows discarded while retaining audit history."""
    results = []
    for raw_id in failure_ids:
        try:
            failure_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if failure_id <= 0:
            continue
        found = db.load_failed_job(failure_id) is not None
        if found:
            db.mark_failed_job_discarded(failure_id)
        results.append({"failure_id": failure_id, "ok": found})
    return results


def restore_discarded_failure_ids(
    failure_ids: Iterable[int | str],
) -> list[dict[str, Any]]:
    """Restore selected discarded failures to manual intervention."""
    results = []
    for raw_id in failure_ids:
        try:
            failure_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if failure_id <= 0:
            continue
        results.append({
            "failure_id": failure_id,
            "ok": db.restore_discarded_failed_job(failure_id),
        })
    return results


def run_failure_action(
    action: str, failure_ids: Iterable[int | str],
) -> list[dict[str, Any]]:
    """Dispatch one bounded failure action shared by REST callers."""
    handlers = {
        "retry": retry_failure_ids,
        "discard": discard_failure_ids,
        "restore": restore_discarded_failure_ids,
    }
    handler = handlers.get(str(action or "").strip().lower())
    if handler is None:
        raise ValueError("action must be retry, discard, or restore")
    return handler(failure_ids)
