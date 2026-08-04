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
from .storage import MEDIA_EXTS, import_folders, scan_storage
from .utils import (
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    TemplateRenderError,
    render_template_strict,
)


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


def plan_library_adoption(
    root, archive_paths=(), *, archive_source_url="", db_module=db,
    cancel_fn=None,
):
    """Return the preview-first external-library adoption plan."""
    from .importer import preview_adoption
    return preview_adoption(
        root,
        archive_paths,
        archive_source_url=archive_source_url,
        db_module=db_module,
        cancel_fn=cancel_fn,
    )


def apply_library_adoption(
    plan, *, db_module=db, backup_fn=None, cancel_fn=None,
):
    """Apply an unchanged external-library plan through the maintenance API."""
    from .importer import apply_adoption
    return apply_adoption(
        plan,
        db_module=db_module,
        backup_fn=backup_fn,
        cancel_fn=cancel_fn,
    )


def plan_library_rebuild(root, *, db_module=db, tags_module=None, cancel_fn=None):
    """Return a sidecar-only preview for rebuilding the local indexes."""
    from . import tags as default_tags
    from .rebuild import plan_library_rebuild as build_plan
    return build_plan(
        root,
        db_module=db_module,
        tags_module=tags_module or default_tags,
        cancel_fn=cancel_fn,
    )


def apply_library_rebuild(
    plan, *, db_module=db, tags_module=None, backup_fn=None, cancel_fn=None,
):
    """Apply a sidecar-only rebuild after backup and staged validation."""
    from . import tags as default_tags
    from .rebuild import apply_library_rebuild as apply_plan
    return apply_plan(
        plan,
        db_module=db_module,
        tags_module=tags_module or default_tags,
        backup_fn=backup_fn,
        cancel_fn=cancel_fn,
    )


def _path_is_within(path, root):
    try:
        return os.path.commonpath((_normal_path(path), _normal_path(root))) == _normal_path(root)
    except (OSError, ValueError):
        return False


def _history_template_context(row, recording_path):
    """Build a complete context from durable history and its sidecar."""
    from .metadata import load_metadata_sidecar

    path = Path(recording_path)
    metadata = load_metadata_sidecar(path / "metadata.json")
    provenance = metadata.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    title = str(row.get("title") or metadata.get("title") or path.name or "")
    channel = str(
        row.get("channel") or metadata.get("channel")
        or metadata.get("vod_channel") or "unknown"
    )
    platform = str(row.get("platform") or metadata.get("platform") or "unknown")
    source_id = str(
        row.get("source_id") or provenance.get("source_id")
        or row.get("id") or ""
    )
    quality = str(row.get("quality") or metadata.get("quality") or "")
    raw_date = str(
        row.get("date") or metadata.get("downloaded_at")
        or metadata.get("start_time") or metadata.get("vod_date") or ""
    )
    date_value = raw_date[:10] if raw_date else ""
    if not date_value or len(date_value) != 10 or date_value[4] != "-" or date_value[7] != "-":
        try:
            date_value = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        except OSError:
            date_value = ""
    media = sorted(
        entry for entry in path.iterdir()
        if entry.is_file() and not entry.name.startswith(".")
        and entry.suffix.lower() in MEDIA_EXTS
    ) if path.is_dir() else []
    extension = media[0].suffix.lstrip(".").lower() if media else ""
    year, month, day = (
        date_value.split("-") if len(date_value) == 10 and "-" in date_value
        else ("", "", "")
    )
    return {
        "title": title,
        "channel": channel,
        "platform": platform,
        "date": date_value,
        "year": year,
        "month": month,
        "day": day,
        "id": source_id,
        "quality": quality,
        "ext": extension,
    }


def _file_rename_preview(recording_path, new_base):
    """Return safe direct-file renames for one recording directory."""
    root = Path(recording_path)
    media = sorted(
        (
            entry for entry in root.iterdir()
            if entry.is_file() and not entry.name.startswith(".")
            and entry.suffix.lower() in MEDIA_EXTS
        ),
        key=lambda entry: os.path.normcase(entry.name),
    )
    if not media:
        raise TemplateRenderError(
            "missing_media", "Recording directory contains no media file"
        )
    specs = []
    for index, entry in enumerate(media):
        suffix = entry.suffix
        stem = str(new_base)
        if len(media) > 1 and index:
            stem += f"_{index + 1:03d}"
        specs.append((entry.name, entry.stem, f"{stem}{suffix}"))

    renames = {
        old_name: new_name
        for old_name, _old_stem, new_name in specs
        if old_name != new_name
    }
    # Keep yt-dlp/NFO/chapter/subtitle/chat siblings attached to the media
    # basename. Generic sidecars (metadata, notes, thumbnail, manifest) stay
    # named as-is and still move with the directory.
    all_names = sorted(
        (entry.name for entry in root.iterdir() if entry.is_file()),
        key=os.path.normcase,
    )
    for name in all_names:
        if name in renames or name.startswith("."):
            continue
        for _old_name, old_stem, new_name in sorted(specs, key=lambda item: len(item[1]), reverse=True):
            if name.startswith(old_stem + ".") or name.startswith(old_stem + "_"):
                candidate = new_name.rsplit(".", 1)[0] + name[len(old_stem):]
                if name != candidate:
                    renames[name] = candidate
                break
    old_names = {os.path.normcase(name) for name in all_names}
    rename_sources = {os.path.normcase(name) for name in renames}
    destinations = list(renames.values())
    if len({os.path.normcase(name) for name in destinations}) != len(destinations):
        raise TemplateRenderError(
            "filename_collision", "Rendered media sidecars would collide"
        )
    for destination in destinations:
        destination_key = os.path.normcase(destination)
        if destination_key in old_names and destination_key not in rename_sources:
            raise TemplateRenderError(
                "filename_collision", f"Rendered filename already exists: {destination}"
            )
    return _order_file_renames([
        {"old": old_name, "new": new_name}
        for old_name, new_name in renames.items()
    ])


def _order_file_renames(file_renames):
    """Order overlapping renames without writing over an unmoved source."""
    source_by_key = {}
    destination_by_key = {}
    for pair in file_renames:
        old_name = str(pair["old"])
        new_name = str(pair["new"])
        old_key = os.path.normcase(old_name)
        new_key = os.path.normcase(new_name)
        if old_key in source_by_key:
            raise TemplateRenderError(
                "filename_collision", f"Duplicate source filename: {old_name}"
            )
        if new_key in destination_by_key:
            raise TemplateRenderError(
                "filename_collision", f"Rendered filename already exists: {new_name}"
            )
        source_by_key[old_key] = {"old": old_name, "new": new_name}
        destination_by_key[new_key] = old_name

    dependencies = {old_key: set() for old_key in source_by_key}
    for old_key, pair in source_by_key.items():
        destination_key = os.path.normcase(pair["new"])
        if destination_key in source_by_key and destination_key != old_key:
            # The file currently at the destination must move first.
            dependencies[old_key].add(destination_key)

    remaining = set(source_by_key)
    ordered = []
    while remaining:
        ready = sorted(
            (key for key in remaining if not dependencies[key] & remaining),
            key=os.path.normcase,
        )
        if not ready:
            cycle = ", ".join(
                source_by_key[key]["old"] for key in sorted(remaining, key=os.path.normcase)
            )
            raise TemplateRenderError(
                "filename_cycle", f"File rename cycle cannot be ordered: {cycle}"
            )
        for key in ready:
            ordered.append(source_by_key[key])
            remaining.remove(key)
    return ordered


def _template_path_context(row, root, folder_template, file_template):
    old_path = Path(str(row.get("path") or "")).expanduser()
    context = _history_template_context(row, old_path)
    folder_parts = render_template_strict(folder_template, context, max_component=80)
    file_parts = render_template_strict(file_template, context, max_component=60)
    if not file_parts:
        raise TemplateRenderError(
            "unresolvable_field", "Filename template rendered no filename"
        )
    relative_parts = list(folder_parts) + list(file_parts[:-1])
    new_path = Path(root).joinpath(*relative_parts)
    if not _path_is_within(new_path, root) or _normal_path(new_path) == _normal_path(root):
        raise TemplateRenderError(
            "invalid_destination", "Rendered destination escapes the archive root"
        )
    if len(str(new_path)) > 240:
        raise TemplateRenderError(
            "path_too_long", "Rendered destination exceeds the safe Windows path length"
        )
    renames = _file_rename_preview(old_path, file_parts[-1])
    for pair in renames:
        if len(str(new_path / pair["new"])) > 240:
            raise TemplateRenderError(
                "path_too_long", "Rendered media path exceeds the safe Windows path length"
            )
    return old_path, new_path, renames


def _retemplate_diagnostics(
    root, *, config, history, ready, conflicts, unchanged, db_module=db,
):
    try:
        usage = shutil.disk_usage(root)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
    except OSError:
        free_gb = total_gb = -1.0
    backup_dir = config.get("archive_backup_dir") or str(CONFIG_DIR / "backups")
    warning_gb = float(config.get("archive_disk_warning_gb", 20) or 20)
    critical_gb = float(config.get("archive_disk_critical_gb", 5) or 5)
    disk_status = (
        "unknown" if free_gb < 0 else
        "critical" if free_gb <= critical_gb else
        "warning" if free_gb <= warning_gb else "healthy"
    )
    return {
        "kind": "retemplate",
        "retemplate": {
            "rows": len(history), "ready": ready, "conflicts": conflicts,
            "unchanged": unchanged,
        },
        "library": {
            "rows": len(history), "missing": conflicts, "untracked": 0,
            "moved": ready,
        },
        "database": db_module.db_diagnostics(),
        "backup": _latest_backup(backup_dir),
        "backup_dir": str(backup_dir),
        "disk": {
            "free_gb": round(free_gb, 2), "total_gb": round(total_gb, 2),
            "warning_gb": warning_gb, "critical_gb": critical_gb,
            "status": disk_status,
        },
    }


def plan_retemplate(
    root,
    folder_template="",
    file_template="",
    *,
    config=None,
    db_module=db,
    history_ids=None,
    cancel_fn=None,
):
    """Preview a strict, archive-wide output-template migration."""
    root = str(Path(root).expanduser().resolve())
    if not os.path.isdir(root):
        raise ValueError(f"Archive root is not a directory: {root}")
    config = dict(config or {})
    folder_template = str(folder_template or config.get("folder_template", "") or DEFAULT_FOLDER_TEMPLATE)
    file_template = str(file_template or config.get("file_template", "") or DEFAULT_FILE_TEMPLATE)
    snapshot_id = db_module.history_snapshot_id()
    history = list(db_module.iter_history(page_size=500))
    wanted = {int(value) for value in history_ids} if history_ids else None
    rows = [row for row in history if wanted is None or int(row.get("id", 0)) in wanted]
    actions = []
    destinations = {}
    ready = conflicts = unchanged = 0
    for row in rows:
        if cancel_fn and cancel_fn():
            raise InterruptedError("re-template preview cancelled")
        payload = {"history_id": int(row["id"]), "old_path": str(row.get("path") or "")}
        try:
            old_path, new_path, renames = _template_path_context(
                row, root, folder_template, file_template
            )
            payload.update({
                "new_path": str(new_path), "file_renames": renames,
                "status": "ready",
            })
            if not _path_is_within(old_path, root):
                raise TemplateRenderError(
                    "outside_root", "Current recording is outside the selected archive root"
                )
            if not old_path.is_dir() or old_path.is_symlink():
                raise TemplateRenderError(
                    "missing_source", "Current recording directory is unavailable"
                )
            if _normal_path(new_path) != _normal_path(old_path):
                if os.path.exists(new_path):
                    raise TemplateRenderError(
                        "collision", "Rendered destination already exists"
                    )
                if _path_is_within(new_path, old_path):
                    raise TemplateRenderError(
                        "invalid_destination", "Rendered destination is inside the source directory"
                    )
            destination_key = _normal_path(new_path)
            if destination_key in destinations:
                raise TemplateRenderError(
                    "collision", "Another recording renders to the same destination"
                )
            destinations[destination_key] = int(row["id"])
            if _normal_path(new_path) == _normal_path(old_path) and not renames:
                payload["status"] = "unchanged"
                unchanged += 1
            else:
                ready += 1
            actions.append(_action(
                "retemplate", "Re-template recording",
                f"{old_path} → {new_path}"
                + (f" ({len(renames)} file rename(s))" if renames else ""),
                payload,
            ))
        except (OSError, TemplateRenderError, ValueError) as exc:
            if isinstance(exc, TemplateRenderError):
                code = exc.code
                message = str(exc)
                if exc.field:
                    message = f"{message} ({exc.field})"
            else:
                code = "unresolvable_field"
                message = str(exc)
            payload.update({"new_path": "", "file_renames": [], "status": "conflict",
                            "reason_code": code, "reason": message})
            conflicts += 1
            actions.append(_action(
                "retemplate_conflict", "Re-template conflict",
                f"{payload['old_path']} — {message}", payload,
            ))
    diagnostics = _retemplate_diagnostics(
        root, config=config, history=rows, ready=ready,
        conflicts=conflicts, unchanged=unchanged, db_module=db_module,
    )
    diagnostics["templates"] = {
        "folder": folder_template, "file": file_template,
    }
    return MaintenancePlan(
        str(uuid.uuid4()), _utc_now(), root, snapshot_id,
        _history_fingerprint(history), actions, diagnostics,
    )


def _save_plan_file(plan, path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(plan.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
    return path


def save_retemplate_plan(plan, path):
    return _save_plan_file(plan, path)


def load_retemplate_plan(path):
    path = Path(path).expanduser()
    return MaintenancePlan.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _restore_retemplate_stage(temp_path, old_path, new_path, file_renames, finalized):
    stage = Path(temp_path)
    destination = Path(old_path)
    final_path = Path(new_path)
    if finalized and final_path.exists():
        os.replace(final_path, stage)
    for pair in reversed(file_renames):
        source = stage / pair["new"]
        target = stage / pair["old"]
        if source.exists() and not target.exists():
            os.replace(source, target)
    if stage.exists():
        os.replace(stage, destination)


def _apply_retemplate_action(
    action, *, db_module, tags_module, ledger_path=None, plan_id=""
):
    payload = action.payload
    old_path = Path(str(payload.get("old_path") or ""))
    new_path = Path(str(payload.get("new_path") or ""))
    history_id = int(payload.get("history_id") or 0)
    file_renames = list(payload.get("file_renames") or [])
    if payload.get("status") != "ready":
        raise RuntimeError(str(payload.get("reason") or "action is not ready"))
    if not old_path.is_dir() or old_path.is_symlink():
        raise RuntimeError("source recording directory is no longer available")
    if new_path.exists() and _normal_path(new_path) != _normal_path(old_path):
        raise RuntimeError("destination exists; preview is stale")
    if os.path.splitdrive(str(old_path))[0].casefold() != os.path.splitdrive(str(new_path))[0].casefold():
        raise RuntimeError("source and destination are on different volumes")
    for pair in file_renames:
        if not isinstance(pair, dict) or not pair.get("old") or not pair.get("new"):
            raise RuntimeError("invalid file rename in preview")
        if not (old_path / pair["old"]).is_file():
            raise RuntimeError(f"sidecar changed after preview: {pair['old']}")
    try:
        file_renames = _order_file_renames(file_renames)
    except TemplateRenderError as exc:
        raise RuntimeError(str(exc)) from exc
    rename_sources = {os.path.normcase(pair["old"]) for pair in file_renames}
    for pair in file_renames:
        target = old_path / pair["new"]
        if target.exists() and os.path.normcase(target.name) not in rename_sources:
            raise RuntimeError(f"file destination exists: {target.name}")

    old_manifest_row = None
    if hasattr(db_module, "load_archive_manifest"):
        old_manifest_row = db_module.load_archive_manifest(history_id)
    old_manifest = (
        dict(old_manifest_row.get("manifest") or {})
        if isinstance(old_manifest_row, dict) else None
    )
    sidecar = old_path / ".streamkeep_manifest.json"
    old_sidecar_bytes = sidecar.read_bytes() if sidecar.is_file() else None
    temporary = old_path.parent / f".streamkeep-retemplate-{uuid.uuid4().hex}"
    new_parent = new_path.parent
    created_parents = []
    probe = new_parent
    while not probe.exists() and probe != probe.parent:
        created_parents.append(probe)
        probe = probe.parent
    new_parent.mkdir(parents=True, exist_ok=True)
    finalized = False
    tags_committed = False
    db_committed = False
    manifest = None
    try:
        if ledger_path is not None:
            _audit({
                "event": "retemplate_swap_started",
                "at": _utc_now(),
                "plan_id": str(plan_id or ""),
                "action_id": action.action_id,
                "history_id": history_id,
                "old_path": str(old_path),
                "new_path": str(new_path),
                "temporary": str(temporary),
                "file_renames": file_renames,
                "had_manifest": old_sidecar_bytes is not None,
            }, ledger_path=ledger_path)
        os.replace(old_path, temporary)
        for pair in file_renames:
            os.replace(temporary / pair["old"], temporary / pair["new"])
        os.replace(temporary, new_path)
        finalized = True
        if old_sidecar_bytes is not None:
            from .verify import create_archive_manifest
            manifest = create_archive_manifest(new_path, write_sidecar=True)
        elif old_manifest is not None:
            manifest = dict(old_manifest)
            manifest["root"] = str(new_path)
        if hasattr(tags_module, "relocate_recording_tags"):
            tags_module.relocate_recording_tags(str(old_path), str(new_path))
            tags_committed = True
        if hasattr(db_module, "relocate_history_recording"):
            db_module.relocate_history_recording(
                history_id, str(old_path), str(new_path),
                manifest=manifest if old_manifest_row is not None else None,
            )
        else:
            db_module.update_history_entry(history_id, {"path": str(new_path)})
        db_committed = True
        if ledger_path is not None:
            _audit({
                "event": "retemplate_swap_finished",
                "at": _utc_now(),
                "plan_id": str(plan_id or ""),
                "action_id": action.action_id,
                "history_id": history_id,
                "old_path": str(old_path),
                "new_path": str(new_path),
            }, ledger_path=ledger_path)
        return {
            "old_path": str(old_path), "new_path": str(new_path),
            "file_renames": file_renames,
        }
    except Exception:
        rollback_errors = []
        if db_committed and hasattr(db_module, "relocate_history_recording"):
            try:
                db_module.relocate_history_recording(
                    history_id, str(new_path), str(old_path), manifest=old_manifest,
                )
            except Exception as exc:
                rollback_errors.append(f"database rollback: {exc}")
        if tags_committed and hasattr(tags_module, "relocate_recording_tags"):
            try:
                tags_module.relocate_recording_tags(str(new_path), str(old_path))
            except Exception as exc:
                rollback_errors.append(f"tag rollback: {exc}")
        try:
            _restore_retemplate_stage(
                temporary, old_path, new_path, file_renames, finalized
            )
        except Exception as exc:
            rollback_errors.append(f"filesystem rollback: {exc}")
        if old_sidecar_bytes is not None and old_path.is_dir():
            try:
                (old_path / ".streamkeep_manifest.json").write_bytes(old_sidecar_bytes)
            except Exception as exc:
                rollback_errors.append(f"manifest rollback: {exc}")
        if rollback_errors:
            raise RuntimeError(
                f"relocation failed and rollback was incomplete: {'; '.join(rollback_errors)}"
            )
        raise
    finally:
        for directory in sorted(created_parents, key=lambda item: len(str(item)), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass


def apply_retemplate(
    plan,
    approved_action_ids,
    *,
    db_module=db,
    tags_module=None,
    cancel_fn=None,
    ledger_path=None,
    backup_fn=None,
    config_dir=None,
):
    """Apply approved re-template moves as independently rollback-safe units."""
    from . import tags as default_tags

    approved = set(approved_action_ids or ())
    selected = [
        action for action in plan.actions
        if action.action_id in approved and action.kind == "retemplate"
        and action.payload.get("status") == "ready"
    ]
    result = MaintenanceResult("completed", skipped=len(plan.actions) - len(selected))
    ledger = Path(ledger_path or audit_path(config_dir))
    _audit({"event": "apply_started", "at": _utc_now(), "plan_id": plan.plan_id,
            "kind": "retemplate", "approved": sorted(approved)}, ledger_path=ledger)
    current_history = list(db_module.iter_history(page_size=500))
    if (db_module.history_snapshot_id() != plan.history_snapshot_id or
            _history_fingerprint(current_history) != plan.history_fingerprint):
        result.status = "stale"
        result.errors.append("Library changed after preview; create a fresh plan.")
        _audit({"event": "apply_stale", "at": _utc_now(), "plan_id": plan.plan_id,
                "kind": "retemplate"}, ledger_path=ledger)
        return result
    if selected:
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
                    "plan_id": plan.plan_id, "kind": "retemplate",
                    "detail": str(detail)}, ledger_path=ledger)
            return result
        result.backup_path = str(backup_file)

    tags_module = tags_module or default_tags
    for action in selected:
        if cancel_fn and cancel_fn():
            result.status = "cancelled"
            break
        try:
            _apply_retemplate_action(
                action, db_module=db_module, tags_module=tags_module,
                ledger_path=ledger, plan_id=plan.plan_id,
            )
            result.applied += 1
            _audit({"event": "action_applied", "at": _utc_now(),
                    "plan_id": plan.plan_id, "action_id": action.action_id,
                    "kind": action.kind, "detail": action.detail}, ledger_path=ledger)
        except Exception as exc:
            result.failed += 1
            result.errors.append(f"{action.label}: {exc}")
            _audit({"event": "action_failed", "at": _utc_now(),
                    "plan_id": plan.plan_id, "action_id": action.action_id,
                    "kind": action.kind, "error": str(exc),
                    "rollback": "attempted"}, ledger_path=ledger)
    _audit({"event": "apply_finished", "at": _utc_now(), "plan_id": plan.plan_id,
            "kind": "retemplate", "status": result.status,
            "applied": result.applied, "failed": result.failed,
            "skipped": result.skipped}, ledger_path=ledger)
    return result


# Explicit aliases keep the public maintenance vocabulary consistent with
# adoption and rebuild while allowing callers to use the shorter API names.
plan_library_retemplate = plan_retemplate
apply_library_retemplate = apply_retemplate


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


def _retemplate_journal_key(record):
    plan_id = str(record.get("plan_id") or "")
    action_id = str(record.get("action_id") or "")
    return (plan_id, action_id) if plan_id and action_id else None


def _retemplate_history_paths(db_module):
    try:
        return {
            int(row.get("id")): str(row.get("path") or "")
            for row in db_module.load_history()
            if row.get("id") is not None
        }
    except Exception:
        return {}


def _cleanup_retemplate_parents(path, stop):
    current = Path(path)
    stop = Path(stop)
    try:
        common = Path(os.path.commonpath((str(current), str(stop))))
    except (OSError, ValueError):
        common = stop
    while current != common and current != current.parent:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _restore_retemplate_manifest(path, had_manifest):
    manifest = Path(path) / ".streamkeep_manifest.json"
    if had_manifest:
        from .verify import create_archive_manifest
        create_archive_manifest(path, write_sidecar=True)
    else:
        manifest.unlink(missing_ok=True)


def _recover_retemplate_record(record, history_paths, *, db_module, tags_module=None):
    old_path = Path(str(record.get("old_path") or ""))
    new_path = Path(str(record.get("new_path") or ""))
    temporary = Path(str(record.get("temporary") or ""))
    if not old_path or not new_path or not temporary:
        raise RuntimeError("re-template journal is missing a path")
    if not temporary.name.startswith(".streamkeep-retemplate-"):
        raise RuntimeError("re-template journal has an unsafe staging path")
    if _normal_path(temporary.parent) != _normal_path(old_path.parent):
        raise RuntimeError("re-template journal has an unsafe staging parent")
    file_renames = list(record.get("file_renames") or ())
    history_id = int(record.get("history_id") or 0)
    history_path = str(history_paths.get(history_id) or "")
    old_key = _normal_path(old_path)
    new_key = _normal_path(new_path)
    if old_path.is_dir() and not temporary.exists() and not new_path.exists():
        return {"status": "completed", "decision": "no_swap"}
    if old_path.exists() and (temporary.exists() or new_path.exists()):
        raise RuntimeError("re-template recovery found duplicate recording paths")
    if temporary.exists() and new_path.exists():
        raise RuntimeError("re-template recovery found staging and destination")
    if history_path and _normal_path(history_path) == new_key:
        if not new_path.is_dir() or temporary.exists():
            raise RuntimeError("history points to an incomplete re-template")
        return {"status": "completed", "decision": "kept_destination"}
    if history_path and _normal_path(history_path) != old_key:
        raise RuntimeError("history points outside the re-template pair")

    if new_path.is_dir() and not temporary.exists():
        if tags_module is None:
            from . import tags as tags_module
        tags_module.relocate_recording_tags(str(new_path), str(old_path))
        _restore_retemplate_stage(
            temporary, old_path, new_path, file_renames, True,
        )
        _restore_retemplate_manifest(
            old_path, bool(record.get("had_manifest", False)),
        )
        _cleanup_retemplate_parents(new_path.parent, old_path.parent)
        return {"status": "completed", "decision": "reversed_destination"}
    if temporary.is_dir():
        _restore_retemplate_stage(
            temporary, old_path, new_path, file_renames, False,
        )
        _cleanup_retemplate_parents(new_path.parent, old_path.parent)
        return {"status": "completed", "decision": "reversed_staging"}
    raise RuntimeError("re-template journal has no recoverable path")


def finalize_interrupted_retemplates(
    *, config_dir=None, db_module=db, tags_module=None,
):
    """Recover journaled re-template swaps left by an interrupted process.

    The append-only start record is written before the first directory swap.
    A destination is retained only when the history row already points to it;
    otherwise the staged or finalized directory is restored to its old path.
    """
    ledger = audit_path(config_dir)
    if not ledger.is_file():
        return False
    states = {}
    try:
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except (TypeError, ValueError):
                continue
            key = _retemplate_journal_key(record)
            if key is None:
                continue
            if record.get("event") in {
                "retemplate_swap_started", "retemplate_swap_finished",
                "action_applied", "action_failed", "retemplate_recovered",
            }:
                states[key] = record
    except OSError:
        return False

    pending = [
        record for record in states.values()
        if record.get("event") == "retemplate_swap_started"
    ]
    if not pending:
        return False
    history_paths = _retemplate_history_paths(db_module)
    recovered = False
    for record in pending:
        try:
            result = _recover_retemplate_record(
                record, history_paths, db_module=db_module,
                tags_module=tags_module,
            )
        except (OSError, RuntimeError, ValueError):
            continue
        _audit({
            "event": "retemplate_recovered",
            "at": _utc_now(),
            "plan_id": record.get("plan_id", ""),
            "action_id": record.get("action_id", ""),
            "history_id": record.get("history_id", 0),
            **result,
        }, ledger_path=ledger)
        recovered = True
    return recovered


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
