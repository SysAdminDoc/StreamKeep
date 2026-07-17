"""Dry-run-first archive maintenance planning and application.

The coordinator keeps discovery read-only, persists the exact preview, requires
explicit action IDs for application, and writes an append-only JSONL ledger.
Each approved action is committed independently so cancellation or a process
restart cannot leave an unreported half-transaction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from . import backup, db
from .paths import CONFIG_DIR
from .storage import import_folders, scan_storage


@dataclass
class MaintenanceAction:
    action_id: str
    kind: str
    label: str
    detail: str
    payload: dict = field(default_factory=dict)


@dataclass
class MaintenancePlan:
    plan_id: str
    created_at: str
    root: str
    history_snapshot_id: int
    history_fingerprint: str
    actions: list[MaintenanceAction] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(
            plan_id=str(value["plan_id"]),
            created_at=str(value["created_at"]),
            root=str(value["root"]),
            history_snapshot_id=int(value["history_snapshot_id"]),
            history_fingerprint=str(value["history_fingerprint"]),
            actions=[MaintenanceAction(**item) for item in value.get("actions", [])],
            diagnostics=dict(value.get("diagnostics", {})),
        )


@dataclass
class MaintenanceResult:
    status: str
    applied: int = 0
    failed: int = 0
    skipped: int = 0
    backup_path: str = ""
    errors: list[str] = field(default_factory=list)


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normal_path(path):
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path or ""))))


def _recording_identity(value):
    return tuple(
        str(value or "").strip().casefold()
        for value in (value.get("platform"), value.get("channel"), value.get("title"))
    )


def _history_fingerprint(rows):
    digest = hashlib.sha256()
    for row in rows:
        payload = {
            key: row.get(key) for key in (
                "id", "date", "platform", "title", "channel", "quality", "size",
                "path", "url", "favorite", "watched", "watch_position_secs",
                "bookmarks",
            )
        }
        digest.update(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _action(kind, label, detail, payload):
    canonical = json.dumps(
        {"kind": kind, "payload": payload}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return MaintenanceAction(
        hashlib.sha256(canonical).hexdigest()[:16], kind, label, detail, payload
    )


def _latest_backup(backup_dir):
    directory = Path(backup_dir)
    try:
        candidates = sorted(
            directory.glob("*.skbackup"), key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        candidates = []
    if not candidates:
        return {"path": "", "modified_at": "", "status": "missing"}
    latest = candidates[0]
    return {
        "path": str(latest),
        "modified_at": datetime.fromtimestamp(
            latest.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds"),
        "status": "available",
    }


def plan_maintenance(root, *, config=None, db_module=db, cancel_fn=None):
    """Create a side-effect-free maintenance preview for *root*."""
    config = dict(config or {})
    root = str(Path(root).expanduser().resolve())
    snapshot_id = db_module.history_snapshot_id()
    history = list(db_module.iter_history(page_size=500))
    history_fingerprint = _history_fingerprint(history)
    if cancel_fn and cancel_fn():
        raise InterruptedError("maintenance preview cancelled")
    scan = scan_storage(root, cancel_fn=cancel_fn)
    if cancel_fn and cancel_fn():
        raise InterruptedError("maintenance preview cancelled")

    existing = {_normal_path(row.get("path")): row for row in history if row.get("path")}
    untracked = [group for group in scan.groups if _normal_path(group.dir_path) not in existing]
    missing = [
        row for row in history
        if row.get("path") and not os.path.exists(str(row.get("path")))
    ]
    by_identity = {}
    for group in untracked:
        identity = _recording_identity({
            "platform": group.platform, "channel": group.channel, "title": group.title,
        })
        by_identity.setdefault(identity, []).append(group)

    actions = []
    moved_group_paths = set()
    moved_history_ids = set()
    for row in missing:
        candidates = by_identity.get(_recording_identity(row), [])
        if len(candidates) != 1:
            continue
        group = candidates[0]
        moved_group_paths.add(_normal_path(group.dir_path))
        moved_history_ids.add(int(row["id"]))
        actions.append(_action(
            "move", "Relink moved recording", f"{row['path']} → {group.dir_path}",
            {"history_id": int(row["id"]), "old_path": str(row["path"]),
             "new_path": group.dir_path},
        ))

    for group in untracked:
        if _normal_path(group.dir_path) in moved_group_paths:
            continue
        actions.append(_action(
            "import", "Import disk recording", group.dir_path,
            {"path": group.dir_path, "has_metadata": (Path(group.dir_path) / "metadata.json").is_file()},
        ))
    for row in missing:
        if int(row["id"]) in moved_history_ids:
            continue
        actions.append(_action(
            "remove_missing", "Remove missing library row", str(row["path"]),
            {"history_id": int(row["id"]), "path": str(row["path"])},
        ))
    actions.append(_action(
        "rebuild", "Rebuild search indexes and planner statistics",
        f"Rebuild History FTS and analyze {len(history)} library row(s).",
        {"history_rows": len(history)},
    ))

    try:
        usage = shutil.disk_usage(root)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
    except OSError:
        free_gb = total_gb = -1.0
    warning_gb = float(config.get("archive_disk_warning_gb", 20) or 20)
    critical_gb = float(config.get("archive_disk_critical_gb", 5) or 5)
    disk_status = (
        "unknown" if free_gb < 0 else
        "critical" if free_gb <= critical_gb else
        "warning" if free_gb <= warning_gb else "healthy"
    )
    backup_dir = config.get("archive_backup_dir") or str(CONFIG_DIR / "backups")
    notes_count = sum(
        1 for group in scan.groups if (Path(group.dir_path) / ".notes.md").is_file()
    )
    diagnostics = {
        "scan": {"groups": len(scan.groups), "files": scan.total_files,
                 "bytes": scan.total_size, "note_sidecars": notes_count},
        "library": {"rows": len(history), "missing": len(missing),
                    "untracked": len(untracked), "moved": len(moved_group_paths)},
        "database": db_module.db_diagnostics(),
        "backup": _latest_backup(backup_dir),
        "backup_dir": str(backup_dir),
        "disk": {"free_gb": round(free_gb, 2), "total_gb": round(total_gb, 2),
                 "warning_gb": warning_gb, "critical_gb": critical_gb,
                 "status": disk_status},
    }
    return MaintenancePlan(
        str(uuid.uuid4()), _utc_now(), root, snapshot_id, history_fingerprint,
        actions, diagnostics,
    )


def pending_plan_path(config_dir=None):
    return Path(config_dir or CONFIG_DIR) / "maintenance" / "pending.json"


def audit_path(config_dir=None):
    return Path(config_dir or CONFIG_DIR) / "maintenance" / "audit.jsonl"


def save_pending_plan(plan, *, config_dir=None):
    path = pending_plan_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(plan.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, path)
    return path


def load_pending_plan(*, config_dir=None):
    path = pending_plan_path(config_dir)
    if not path.is_file():
        return None
    return MaintenancePlan.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _audit(record, *, ledger_path):
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def apply_maintenance(
    plan, approved_action_ids, *, db_module=db, cancel_fn=None,
    ledger_path=None, backup_fn=None, config_dir=None,
):
    """Apply only explicitly approved actions from an unchanged preview."""
    approved = set(approved_action_ids or ())
    selected = [action for action in plan.actions if action.action_id in approved]
    result = MaintenanceResult("completed", skipped=len(plan.actions) - len(selected))
    ledger = Path(ledger_path or audit_path(config_dir))
    _audit({"event": "apply_started", "at": _utc_now(), "plan_id": plan.plan_id,
            "approved": sorted(approved)}, ledger_path=ledger)
    current_history = list(db_module.iter_history(page_size=500))
    if (db_module.history_snapshot_id() != plan.history_snapshot_id or
            _history_fingerprint(current_history) != plan.history_fingerprint):
        result.status = "stale"
        result.errors.append("Library changed after preview; create a fresh plan.")
        _audit({"event": "apply_stale", "at": _utc_now(), "plan_id": plan.plan_id},
               ledger_path=ledger)
        return result
    mutating = any(action.kind in {"move", "import", "remove_missing", "rebuild"}
                   for action in selected)
    if mutating:
        backup_dir = Path(plan.diagnostics.get("backup_dir") or
                          Path(config_dir or CONFIG_DIR) / "backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / f"maintenance-{plan.plan_id}.skbackup"
        create = backup_fn or backup.create_backup
        ok, detail = create(str(backup_file))
        if not ok:
            result.status = "backup_failed"
            result.errors.append(str(detail))
            _audit({"event": "backup_failed", "at": _utc_now(),
                    "plan_id": plan.plan_id, "detail": str(detail)}, ledger_path=ledger)
            return result
        result.backup_path = str(backup_file)

    groups = {_normal_path(group.dir_path): group for group in scan_storage(plan.root).groups}
    for action in selected:
        if cancel_fn and cancel_fn():
            result.status = "cancelled"
            break
        try:
            if action.kind == "move":
                if os.path.exists(action.payload["old_path"]):
                    raise RuntimeError("old recording path exists again; preview is stale")
                if not os.path.isdir(action.payload["new_path"]):
                    raise RuntimeError("new recording path is no longer available")
                db_module.update_history_entry(
                    int(action.payload["history_id"]), {"path": action.payload["new_path"]}
                )
            elif action.kind == "import":
                group = groups.get(_normal_path(action.payload["path"]))
                if group is None:
                    raise RuntimeError("recording folder is no longer available")
                imported, errors = import_folders([group], db_module=db_module)
                if imported != 1 or errors:
                    raise RuntimeError("; ".join(errors) or "recording was not imported")
            elif action.kind == "remove_missing":
                if os.path.exists(action.payload["path"]):
                    raise RuntimeError("recording path exists again; row was preserved")
                db_module.delete_history_entries([int(action.payload["history_id"])])
            elif action.kind == "rebuild":
                ok, detail = db_module.rebuild_history_indexes()
                if not ok:
                    raise RuntimeError(detail)
            else:
                raise RuntimeError(f"unsupported maintenance action: {action.kind}")
            result.applied += 1
            _audit({"event": "action_applied", "at": _utc_now(),
                    "plan_id": plan.plan_id, "action_id": action.action_id,
                    "kind": action.kind, "detail": action.detail}, ledger_path=ledger)
        except Exception as exc:
            result.failed += 1
            result.errors.append(f"{action.label}: {exc}")
            _audit({"event": "action_failed", "at": _utc_now(),
                    "plan_id": plan.plan_id, "action_id": action.action_id,
                    "kind": action.kind, "error": str(exc)}, ledger_path=ledger)
    _audit({"event": "apply_finished", "at": _utc_now(), "plan_id": plan.plan_id,
            "status": result.status, "applied": result.applied,
            "failed": result.failed, "skipped": result.skipped}, ledger_path=ledger)
    pending = pending_plan_path(config_dir)
    if pending.is_file():
        pending.unlink()
    return result
