"""Rolling archive-integrity scrub orchestration.

Storage scans use the cheap structure check from :mod:`streamkeep.verify`.
This module schedules a bounded fraction of database-backed manifests for
full hashing, persists coverage checkpoints, and reports drift without ever
repairing or deleting a recording.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import time

from PyQt6.QtCore import QThread, pyqtSignal

from . import db, verify


def sha384_sri_from_digest(digest):
    """Return a standards-shaped SRI token for one SHA-384 digest."""
    raw = bytes(digest or b"")
    if len(raw) != 48:
        raise ValueError("SHA-384 digests must contain 48 bytes")
    return "sha384-" + base64.b64encode(raw).decode("ascii")


@dataclass
class IntegrityScrubResult:
    status: str = "completed"
    due: bool = True
    checked: int = 0
    mismatches: int = 0
    skipped: int = 0
    offline: int = 0
    bytes_hashed: int = 0
    issues: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


class IntegrityScrubWorker(QThread):
    """Qt worker shared by the desktop and headless schedulers."""

    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, root, config=None, *, notify_fn=None, parent=None):
        super().__init__(parent)
        self.root = str(root or "")
        self.config = dict(config or {})
        self.notify_fn = notify_fn

    def run(self):
        try:
            result = run_rolling_integrity_scrub(
                self.root,
                config=self.config,
                cancel_fn=self.isInterruptionRequested,
                notify_fn=self.notify_fn,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


def _utc_now(now=None):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _iso(value):
    return _utc_now(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _number(config, key, default, *, minimum=0, maximum=None, cast=float):
    try:
        value = cast(config.get(key, default))
    except (TypeError, ValueError, OverflowError):
        value = cast(default)
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _within(path, root):
    try:
        return os.path.commonpath((
            os.path.normcase(os.path.abspath(os.path.normpath(str(path)))),
            os.path.normcase(os.path.abspath(os.path.normpath(str(root)))),
        )) == os.path.normcase(os.path.abspath(os.path.normpath(str(root))))
    except (OSError, ValueError):
        return False


def _volume_online(path):
    """Distinguish an unavailable volume from an online missing recording."""
    path = str(path or "")
    drive, _tail = os.path.splitdrive(path)
    if drive:
        return os.path.exists(drive + os.sep)
    anchor = Path(path).anchor
    return bool(anchor and os.path.exists(anchor))


def _last_full(value):
    raw = str(value or "")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _manifest_bytes(manifest):
    total = 0
    for entry in (manifest or {}).get("files", []) or []:
        if isinstance(entry, dict):
            try:
                total += max(0, int(entry.get("size", 0) or 0))
            except (TypeError, ValueError):
                continue
    return total


def _notify(notify_fn, text, level="error"):
    if not callable(notify_fn):
        return
    try:
        notify_fn(str(text), level)
    except TypeError:
        try:
            notify_fn(str(text))
        except Exception:
            return
    except Exception:
        return


def _issue(history_id, path, status, details, report):
    affected = []
    for item in [
        *(report.get("missing", []) or []),
        *(report.get("changed", []) or []),
    ]:
        if not isinstance(item, dict):
            continue
        affected.append({
            "path": str(item.get("path", "") or ""),
            "reason": str(item.get("reason", "") or "missing"),
        })
    return {
        "history_id": int(history_id), "recording_path": str(path),
        "status": str(status), "details": str(details), "files": affected,
    }


def run_rolling_integrity_scrub(
    root="",
    *,
    config=None,
    db_module=db,
    verify_module=verify,
    cancel_fn=None,
    notify_fn=None,
    now=None,
):
    """Run one due, bounded rolling integrity pass.

    The fraction is selected oldest-first by the per-recording ``last_full``
    checkpoint. A path on an unavailable volume is skipped and remains due;
    it is never recorded as a failed hash.
    """
    config = dict(config or {})
    result = IntegrityScrubResult()
    result.started_at = _iso(now)
    if not bool(config.get("integrity_scrub_enabled", True)):
        result.status = "disabled"
        result.due = False
        return result
    interval_seconds = int(_number(
        config, "integrity_scrub_interval_hours", 24,
        minimum=1, maximum=24 * 30, cast=float,
    ) * 3600)
    current = _utc_now(now)
    if not db_module.integrity_scrub_is_due(interval_seconds, now=current):
        result.status = "not_due"
        result.due = False
        return result

    period_days = _number(
        config, "integrity_scrub_period_days", 30,
        minimum=1, maximum=3650, cast=float,
    )
    fraction = _number(
        config, "integrity_scrub_fraction", 0.10,
        minimum=0.01, maximum=1.0, cast=float,
    )
    max_bytes = int(_number(
        config, "integrity_scrub_max_bytes", 512 * 1024 * 1024,
        minimum=1, maximum=1024 * 1024 * 1024 * 1024, cast=float,
    ))
    rate_mbps = _number(
        config, "integrity_scrub_rate_mbps", 32,
        minimum=0, maximum=1024, cast=float,
    )
    rate_bytes = rate_mbps * 1024 * 1024
    max_items = int(_number(
        config, "integrity_scrub_max_items", 100,
        minimum=1, maximum=10000, cast=float,
    ))
    deadline = current - timedelta(days=period_days)

    db_module.record_integrity_scrub_run(
        started_at=result.started_at, status="running", details="",
        checked=0, mismatches=0, skipped=0,
    )
    try:
        records = list(db_module.list_archive_manifest_records())
        states = {
            int(item.get("history_id", 0)): item
            for item in db_module.list_integrity_scrub_states()
        }
        candidates = []
        for record in records:
            try:
                history_id = int(record.get("history_id", 0) or 0)
            except (TypeError, ValueError):
                continue
            if history_id <= 0:
                continue
            path = str(record.get("recording_path") or record.get("path") or "")
            if root and not _within(path, root):
                continue
            state = states.get(history_id, {})
            last_full = _last_full(state.get("last_full_at"))
            # A path change invalidates coverage even if an old row has a
            # recent checkpoint.
            path_changed = bool(
                state.get("recording_path")
                and os.path.normcase(os.path.normpath(str(state["recording_path"])))
                != os.path.normcase(os.path.normpath(path))
            )
            if path_changed or last_full is None or last_full <= deadline:
                candidates.append((
                    last_full or datetime.min.replace(tzinfo=timezone.utc),
                    history_id, path, record.get("manifest") or {},
                ))
        candidates.sort(key=lambda item: (item[0], item[1]))
        # The fraction is of the whole manifest library, not just the overdue
        # subset. Otherwise the target shrinks after every pass and a library
        # can never receive the promised coverage within the configured period.
        target_count = min(max_items, max(1, int(math.ceil(len(records) * fraction)))) if candidates else 0
        selected = candidates[:target_count]
        consumed = 0
        for _last, history_id, path, manifest in selected:
            if cancel_fn is not None and cancel_fn():
                raise InterruptedError("archive integrity scrub cancelled")
            estimated = _manifest_bytes(manifest)
            if consumed + estimated > max_bytes:
                result.skipped += 1
                db_module.record_integrity_scrub(
                    history_id, recording_path=path, cheap_at=_iso(current),
                    status="deferred", details="Deferred by the per-run byte budget",
                )
                continue
            if not _volume_online(path):
                result.skipped += 1
                result.offline += 1
                db_module.record_integrity_scrub(
                    history_id, recording_path=path, cheap_at=_iso(current),
                    status="offline", details="Volume is unavailable; retry remains due",
                )
                continue
            started = time.monotonic()
            try:
                status, details, report = verify_module.verify_archive_manifest(
                    path, manifest, cancel_fn=cancel_fn,
                    rate_bytes_per_sec=rate_bytes,
                )
            except InterruptedError:
                raise
            except OSError as exc:
                result.skipped += 1
                result.errors.append(f"{path}: {exc}")
                db_module.record_integrity_scrub(
                    history_id, recording_path=path, cheap_at=_iso(current),
                    status="unavailable", details=str(exc),
                )
                continue
            duration_ms = int((time.monotonic() - started) * 1000)
            result.checked += 1
            consumed += estimated
            result.bytes_hashed += estimated
            full_at = _iso(current)
            mismatch = status != verify_module.STATUS_OK
            if mismatch:
                result.mismatches += 1
                item = _issue(history_id, path, status, details, report)
                result.issues.append(item)
                files = ", ".join(
                    str(row.get("path") or "unknown")
                    for row in item["files"][:5]
                ) or "manifest"
                _notify(
                    notify_fn,
                    f"Archive integrity drift: {path} ({files})",
                    "error",
                )
            db_module.record_integrity_scrub(
                history_id, recording_path=path, cheap_at=_iso(current),
                full_at=full_at,
                status="failed" if mismatch else "verified",
                details=details, full_bytes=estimated, duration_ms=duration_ms,
            )
        result.finished_at = _iso(current)
        result.status = "completed_with_mismatches" if result.mismatches else "completed"
        details = (
            f"Checked {result.checked} recording(s); {result.mismatches} mismatch(es); "
            f"{result.skipped} skipped ({result.offline} offline)."
        )
        db_module.record_integrity_scrub_run(
            finished_at=result.finished_at, status=result.status, details=details,
            checked=result.checked, mismatches=result.mismatches,
            skipped=result.skipped,
        )
        return result
    except InterruptedError:
        result.finished_at = _iso(current)
        result.status = "cancelled"
        db_module.record_integrity_scrub_run(
            finished_at=result.finished_at, status="cancelled",
            details="Stopped between recording hashes; no repair was attempted.",
            checked=result.checked, mismatches=result.mismatches,
            skipped=result.skipped,
        )
        return result
    except Exception as exc:
        result.finished_at = _iso(current)
        result.status = "failed"
        result.errors.append(str(exc))
        db_module.record_integrity_scrub_run(
            finished_at=result.finished_at, status="failed", details=str(exc),
            checked=result.checked, mismatches=result.mismatches,
            skipped=result.skipped,
        )
        return result
