"""Persistent scheduled health checks for the desktop and local API.

The evaluator is deliberately independent of Qt.  It aggregates the existing
runtime, credential, archive, retry-circuit, and disk probes into one bounded
JSON snapshot so the desktop, CLI, and authenticated companion API expose the
same standing conditions.  Only active conditions are retained; transitions
emit stable health hook events and resolved conditions disappear on the next
successful run.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import paths

HEALTH_SCHEMA_VERSION = 1
HEALTH_FILE_NAME = "health.json"
DEFAULT_INTERVAL_MINUTES = 15
DEFAULT_FAILURE_THRESHOLD = 3

SEVERITY_ORDER = ("critical", "error", "warning", "info")
_SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITY_ORDER)}

HEALTH_EVENT_BY_CATEGORY = {
    "runtime": "health_runtime_degraded",
    "credentials": "health_credentials_expired",
    "archive": "health_archive_offline",
    "extractor": "health_extractor_failures",
    "disk": "health_disk_pressure",
}

_REQUIRED_RUNTIME = frozenset({
    "sqlite", "curl", "ffmpeg", "ffprobe", "yt_dlp", "youtube",
})
_HARD_CREDENTIAL_STATUSES = frozenset({
    "invalid", "expired", "insufficient_scope",
})
_LOCK = threading.RLock()


def health_path(path=None) -> Path:
    """Return the persistent health path, resolving the current config root."""
    return Path(path) if path else paths.CONFIG_DIR / HEALTH_FILE_NAME


def _empty_snapshot() -> dict:
    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "checked_at": "",
        "status": "healthy",
        "summary": {
            "active": 0,
            "critical": 0,
            "error": 0,
            "warning": 0,
            "info": 0,
        },
        "conditions": [],
        "events": [],
    }


def _safe_text(value, limit=500) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = "".join(char for char in text if ord(char) >= 32 or char in "\t\n")
    return text[:max(1, int(limit))]


def _now_iso(now=None) -> str:
    if now is None:
        current = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        current = now.astimezone(timezone.utc)
    else:
        current = datetime.fromtimestamp(float(now), timezone.utc)
    return current.isoformat(timespec="seconds")


def _now_epoch(now=None) -> float:
    if now is None:
        return datetime.now(timezone.utc).timestamp()
    if isinstance(now, datetime):
        return now.timestamp()
    return float(now)


def _severity(value, default="warning") -> str:
    value = str(value or default).strip().lower()
    return value if value in _SEVERITY_RANK else default


def _condition(
    condition_id,
    category,
    severity,
    title,
    detail,
    repair,
    *,
    target="",
    target_path="",
    event="",
    now="",
) -> dict:
    category = _safe_text(category, 64) or "general"
    return {
        "id": _safe_text(condition_id, 160),
        "category": category,
        "event": event or HEALTH_EVENT_BY_CATEGORY.get(category, ""),
        "severity": _severity(severity),
        "title": _safe_text(title, 160),
        "detail": _safe_text(detail),
        "repair": _safe_text(repair, 500),
        "target": _safe_text(target, 120),
        "target_path": _safe_text(target_path, 1024),
        "updated_at": now,
    }


def _read_snapshot(path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _empty_snapshot()
    if not isinstance(payload, dict):
        return _empty_snapshot()
    result = _empty_snapshot()
    result.update({
        key: payload.get(key, result[key])
        for key in ("schema_version", "checked_at", "status", "summary", "conditions")
    })
    if not isinstance(result["conditions"], list):
        result["conditions"] = []
    result["events"] = []
    result["conditions"] = [
        dict(item) for item in result["conditions"]
        if isinstance(item, dict) and item.get("id")
    ]
    return result


def load_health_snapshot(path=None) -> dict:
    """Load the last persisted snapshot without running any probes."""
    with _LOCK:
        return _read_snapshot(health_path(path))


def _write_snapshot(snapshot, path=None) -> None:
    target = health_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    payload = copy.deepcopy(snapshot)
    payload["events"] = []
    encoded = json.dumps(payload, indent=2, ensure_ascii=False)
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def _runtime_conditions(runtime, now) -> list[dict]:
    conditions = []
    if not isinstance(runtime, dict):
        return conditions
    from .capabilities import format_capability_problem

    for name in sorted(_REQUIRED_RUNTIME):
        record = runtime.get(name)
        if not isinstance(record, dict) or record.get("supported") is True:
            continue
        display_name = _safe_text(record.get("display_name") or name, 100)
        detail = format_capability_problem(record) if record else (
            f"{display_name} did not return a readiness record."
        )
        severity = "critical" if name == "sqlite" else "error"
        conditions.append(_condition(
            f"runtime:{name}", "runtime", severity,
            f"{display_name} needs attention", detail,
            record.get("repair") or "Install a supported dependency set.",
            target=name, event=HEALTH_EVENT_BY_CATEGORY["runtime"], now=now,
        ))
    return conditions


def _credential_conditions(results, now) -> list[dict]:
    conditions = []
    for result in results or ():
        if isinstance(result, dict):
            platform = result.get("platform", "unknown")
            status = str(result.get("status", ""))
            detail = result.get("detail", "")
            label = result.get("label", status)
        else:
            platform = getattr(result, "platform", "unknown")
            status = str(getattr(result, "status", ""))
            detail = getattr(result, "detail", "")
            label = getattr(result, "label", status)
        if status not in _HARD_CREDENTIAL_STATUSES:
            continue
        platform = _safe_text(platform, 64).lower() or "unknown"
        label = _safe_text(label, 100) or "Credential failure"
        conditions.append(_condition(
            f"credential:{platform}", "credentials", "error",
            f"{platform.title()} credential: {label}",
            detail or "The stored credential did not pass its validation probe.",
            "Open Settings → Access and replace or re-authorize the credential.",
            target=platform, event=HEALTH_EVENT_BY_CATEGORY["credentials"], now=now,
        ))
    return conditions


def _configured_roots(config, archive_roots) -> list[tuple[str, str]]:
    if archive_roots is None:
        roots = []
        output = str(config.get("output_dir", "") or "").strip()
        if output:
            roots.append(("output_dir", output))
        configured = config.get("archive_roots", [])
        if isinstance(configured, dict):
            configured = list(configured.items())
        if isinstance(configured, (list, tuple)):
            archive_roots = configured
        else:
            archive_roots = []
    else:
        roots = []

    for index, value in enumerate(archive_roots or ()):
        label = f"archive_{index + 1}"
        path = ""
        if isinstance(value, dict):
            label = value.get("label") or value.get("name") or label
            path = value.get("path") or value.get("root") or ""
        elif isinstance(value, (list, tuple)) and len(value) >= 2:
            label, path = value[0], value[1]
        else:
            path = value
        path = _safe_text(path, 1024)
        if path:
            roots.append((_safe_text(label, 120) or label, path))
    unique = []
    seen = set()
    for label, path in roots:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, path))
    return unique


def _archive_conditions(config, archive_roots, now) -> tuple[list[dict], list[tuple[str, str]]]:
    roots = _configured_roots(config, archive_roots)
    conditions = []
    for label, path in roots:
        candidate = Path(path).expanduser()
        try:
            reachable = candidate.is_dir() and os.access(candidate, os.R_OK | os.W_OK)
        except OSError:
            reachable = False
        if reachable:
            continue
        conditions.append(_condition(
            f"archive:{label}", "archive", "error",
            f"Archive root unavailable: {label}",
            "The configured archive root is missing or not accessible.",
            "Open Settings → General and choose a reachable archive folder.",
            target=label, target_path=str(candidate),
            event=HEALTH_EVENT_BY_CATEGORY["archive"], now=now,
        ))
    return conditions, roots


def _disk_conditions(config, roots, disk_usage, now) -> list[dict]:
    if disk_usage is None:
        disk_usage = {}
    warning_gb = max(1, int(config.get("disk_warning_gb", 20) or 20))
    critical_gb = max(1, int(config.get("disk_critical_gb", 5) or 5))
    warning_bytes = warning_gb * 1024 ** 3
    critical_bytes = critical_gb * 1024 ** 3
    conditions = []
    for label, path in roots:
        candidate = Path(path).expanduser()
        try:
            if isinstance(disk_usage, dict) and path in disk_usage:
                usage = disk_usage[path]
            elif isinstance(disk_usage, dict) and label in disk_usage:
                usage = disk_usage[label]
            else:
                usage = shutil.disk_usage(candidate)
            if hasattr(usage, "free"):
                free = int(usage.free)
            else:
                free = int(usage[2])
        except (OSError, IndexError, KeyError, TypeError, ValueError):
            continue
        if free >= warning_bytes:
            continue
        severity = "critical" if free < critical_bytes else "warning"
        free_gb = free / 1024 ** 3
        threshold_gb = critical_gb if severity == "critical" else warning_gb
        conditions.append(_condition(
            f"disk:{label}", "disk", severity,
            f"Disk space pressure: {label}",
            f"Only {free_gb:.1f} GB remains on the archive volume "
            f"({threshold_gb} GB {severity} threshold).",
            "Free space or move the archive root before starting more captures.",
            target=label, target_path=str(candidate),
            event=HEALTH_EVENT_BY_CATEGORY["disk"], now=now,
        ))
    return conditions


def _extractor_conditions(config, retry_circuits, now, now_epoch) -> list[dict]:
    if retry_circuits is None:
        try:
            from . import db
            retry_circuits = db.load_retry_circuits()
        except Exception:
            retry_circuits = []
    threshold = max(1, int(
        config.get("health_failure_threshold", DEFAULT_FAILURE_THRESHOLD)
        or DEFAULT_FAILURE_THRESHOLD
    ))
    conditions = []
    for row in retry_circuits or ():
        if not isinstance(row, dict):
            continue
        try:
            count = int(row.get("failure_count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        if count < threshold:
            continue
        key = _safe_text(row.get("source_key") or row.get("source_label") or "unknown", 120)
        label = _safe_text(row.get("source_label") or key, 120)
        try:
            opened_until = float(row.get("opened_until", 0) or 0)
        except (TypeError, ValueError):
            opened_until = 0
        severity = "error" if opened_until > now_epoch else "warning"
        reason = _safe_text(row.get("last_reason") or row.get("last_category"), 240)
        detail = f"{count} consecutive extractor failures"
        if reason:
            detail += f": {reason}"
        conditions.append(_condition(
            f"extractor:{key}", "extractor", severity,
            f"Repeated extractor failures: {label}", detail,
            "Open Operations, inspect the source, and retry after the service recovers.",
            target=label, event=HEALTH_EVENT_BY_CATEGORY["extractor"], now=now,
        ))
    return conditions


def _source_adapter_conditions(now, diagnostics=None) -> list[dict]:
    """Raise one standing condition per source adapter that will not load.

    A malformed adapter otherwise falls through to the yt-dlp catch-all with
    no visible sign, so the site simply stops behaving as the operator
    configured it. The condition is derived from the current on-disk state
    every run, so fixing the file clears it (V151).
    """
    if diagnostics is None:
        try:
            from .declarative import declarative_adapter_diagnostics
            diagnostics = declarative_adapter_diagnostics()
        except Exception:
            # Adapters are optional; an unavailable registry is not a health
            # finding of its own.
            return []
    errors = (diagnostics or {}).get("errors") or []
    conditions = []
    for entry in errors:
        if not isinstance(entry, dict):
            continue
        source = _safe_text(entry.get("source") or "source adapter", 240)
        label = Path(source).name or source
        conditions.append(_condition(
            f"source_adapter:{source}", "extractor", "warning",
            f"Source adapter could not be loaded: {label}",
            _safe_text(entry.get("error") or "the definition is invalid", 240),
            "Fix or remove the definition in the source_adapters folder; "
            "until then this site falls back to the yt-dlp extractor.",
            target=label, target_path=source,
            event=HEALTH_EVENT_BY_CATEGORY["extractor"], now=now,
        ))
    return conditions


def _snapshot_status(conditions) -> tuple[str, dict]:
    summary = {name: 0 for name in SEVERITY_ORDER}
    for condition in conditions:
        severity = _severity(condition.get("severity"))
        summary[severity] += 1
    summary["active"] = len(conditions)
    status = "healthy"
    for severity in SEVERITY_ORDER:
        if summary[severity]:
            status = severity
            break
    return status, summary


def _transition_events(previous, current, now) -> list[dict]:
    previous_map = {
        item.get("id"): item for item in previous if item.get("id")
    }
    current_map = {
        item.get("id"): item for item in current if item.get("id")
    }
    events = []
    for condition in current:
        old = previous_map.get(condition["id"])
        if old is None:
            state = "opened"
        elif old.get("severity") != condition.get("severity"):
            state = "changed"
        else:
            continue
        events.append({
            "event": condition["event"],
            "condition": condition["id"],
            "state": state,
            "severity": condition["severity"],
            "title": condition["title"],
            "detail": condition["detail"],
            "repair": condition["repair"],
            "target": condition.get("target", ""),
            "timestamp": now,
        })
    for condition_id, old in previous_map.items():
        if condition_id in current_map:
            continue
        events.append({
            "event": old.get("event", ""),
            "condition": condition_id,
            "state": "resolved",
            "severity": "info",
            "title": old.get("title", condition_id),
            "detail": "The condition cleared on the latest health run.",
            "repair": "",
            "target": old.get("target", ""),
            "timestamp": now,
        })
    return events


def dispatch_health_events(events, config=None, *, event_sink=None, log_fn=None) -> None:
    """Dispatch stable hook events and optionally notify a caller-owned surface."""
    config = config if isinstance(config, dict) else {}
    hooks_config = config.get("hooks", {})
    from .hooks import fire_hook

    for event in events or ():
        hook_name = str(event.get("event") or "").strip()
        if hook_name:
            fire_hook(
                hook_name,
                {
                    "title": event.get("title", ""),
                    "condition": event.get("condition", ""),
                    "state": event.get("state", ""),
                    "severity": event.get("severity", ""),
                    "detail": event.get("detail", ""),
                    "repair": event.get("repair", ""),
                },
                hooks_config,
                log_fn=log_fn,
            )
        if event_sink is not None:
            try:
                event_sink(dict(event))
            except Exception as error:
                if log_fn:
                    log_fn(f"[HEALTH] Event sink failed: {error}")


def run_health_check(
    config=None,
    *,
    runtime=None,
    credential_results=None,
    archive_roots=None,
    retry_circuits=None,
    disk_usage=None,
    adapter_diagnostics=None,
    now=None,
    credential_timeout=8,
    storage_path=None,
    dispatch_events=True,
    event_sink=None,
    log_fn=None,
) -> dict:
    """Run all bounded health probes and persist the resulting snapshot."""
    config = dict(config or {})
    now_iso = _now_iso(now)
    now_epoch = _now_epoch(now)
    if runtime is None:
        try:
            from .capabilities import get_runtime_capabilities
            runtime = get_runtime_capabilities(refresh=True, config=config)
        except Exception as error:
            runtime = {}
            if log_fn:
                log_fn(f"[HEALTH] Runtime probe failed: {error}")
    if credential_results is None:
        try:
            from .credential_check import probe_all
            credential_results = probe_all(timeout=max(1, int(credential_timeout or 8)))
        except Exception as error:
            credential_results = []
            if log_fn:
                log_fn(f"[HEALTH] Credential probe failed: {error}")

    conditions = _runtime_conditions(runtime, now_iso)
    conditions.extend(_credential_conditions(credential_results, now_iso))
    archive, roots = _archive_conditions(config, archive_roots, now_iso)
    conditions.extend(archive)
    conditions.extend(_disk_conditions(config, roots, disk_usage, now_iso))
    conditions.extend(_extractor_conditions(config, retry_circuits, now_iso, now_epoch))
    conditions.extend(_source_adapter_conditions(now_iso, adapter_diagnostics))
    conditions.sort(key=lambda item: (
        _SEVERITY_RANK.get(item.get("severity"), len(SEVERITY_ORDER)),
        item.get("title", ""),
        item.get("id", ""),
    ))

    with _LOCK:
        previous = _read_snapshot(health_path(storage_path))
        events = _transition_events(previous.get("conditions", []), conditions, now_iso)
        status, summary = _snapshot_status(conditions)
        snapshot = {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "checked_at": now_iso,
            "status": status,
            "summary": summary,
            "conditions": conditions,
            "events": events,
        }
        try:
            _write_snapshot(snapshot, storage_path)
        except OSError as error:
            if log_fn:
                log_fn(f"[HEALTH] Could not persist health snapshot: {error}")

    if dispatch_events:
        dispatch_health_events(
            events, config, event_sink=event_sink, log_fn=log_fn,
        )
    return snapshot


def public_snapshot(snapshot=None) -> dict:
    """Return a copy safe for the authenticated API and web remote."""
    result = copy.deepcopy(snapshot or _empty_snapshot())
    result["conditions"] = [
        {
            key: value for key, value in condition.items()
            if key != "target_path"
        }
        for condition in result.get("conditions", [])
        if isinstance(condition, dict)
    ]
    result["events"] = [
        {
            key: value for key, value in event.items()
            if key != "target_path"
        }
        for event in result.get("events", [])
        if isinstance(event, dict)
    ]
    return result


__all__ = [
    "DEFAULT_FAILURE_THRESHOLD", "DEFAULT_INTERVAL_MINUTES",
    "HEALTH_EVENT_BY_CATEGORY", "HEALTH_FILE_NAME", "HEALTH_SCHEMA_VERSION",
    "SEVERITY_ORDER", "dispatch_health_events", "health_path",
    "load_health_snapshot", "public_snapshot", "run_health_check",
]
