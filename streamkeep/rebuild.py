"""Preview and apply a SQLite index rebuild from public library sidecars.

The filesystem remains the source of truth for this workflow.  A preview
only reads recording folders and writes a small, portable plan.  Apply first
creates the normal secret-free backup, builds replacement SQLite files away
from the live database, and swaps them only after both replacements validate.
Media and sidecars are never written by this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import db as _db
from . import tags as _tags
from .backup import create_backup
from .metadata import (
    METADATA_SCHEMA_VERSION,
    MAX_IMPORT_SIDECAR_BYTES,
    archive_key_for_provenance,
    build_archival_provenance,
    load_nfo_sidecar,
    load_ytdlp_info_sidecar,
    normalize_metadata_payload,
)
from .models import StreamInfo
from .storage import MEDIA_EXTS, _fmt_size
from .sqlite_runtime import connect as sqlite_connect
from .verify import MANIFEST_FILENAME, MANIFEST_VERSION


REBUILD_PLAN_SCHEMA = 1
MAX_PLAN_BYTES = 64 * 1024 * 1024
REBUILD_SWAP_MARKER = ".streamkeep-rebuild-swap.json"
_REBUILD_STAGE_PREFIX = ".streamkeep-rebuild-"
_MEDIA_EXTS = set(MEDIA_EXTS) | {".m4v"}
_SIDECAR_SUFFIXES = (".info.json", ".nfo")
_HISTORY_FINGERPRINT_FIELDS = (
    "id", "date", "platform", "source_id", "webpage_url", "title",
    "channel", "quality", "size", "path", "url", "favorite", "watched",
    "watch_position_secs", "bookmarks",
)


def _utc_now():
    return datetime.now(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _text(value):
    return str(value or "").strip()


def _is_within(path, root):
    try:
        Path(path).resolve(strict=False).relative_to(
            Path(root).resolve(strict=False)
        )
        return True
    except (OSError, ValueError):
        return False


def _media_files_under(root):
    root = Path(root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".")
        )
        for name in sorted(filenames, key=str.casefold):
            path = Path(dirpath) / name
            if name.startswith(".") or path.suffix.lower() not in _MEDIA_EXTS:
                continue
            if path.is_file():
                found.append(path)
    return found


def _sidecar_paths(directory):
    directory = Path(directory)
    paths = []
    for name in ("metadata.json", MANIFEST_FILENAME):
        path = directory / name
        if path.is_file():
            paths.append(path)
    for path in directory.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        lowered = path.name.casefold()
        if lowered.endswith(_SIDECAR_SUFFIXES):
            paths.append(path)
    return sorted(set(paths), key=lambda path: path.name.casefold())


def _raw_json(path):
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size > MAX_IMPORT_SIDECAR_BYTES:
            return None, "missing or oversized sidecar"
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        return None, str(error)
    if not isinstance(value, dict):
        return None, "sidecar root is not an object"
    return value, ""


def _schema_version(value, default=1):
    try:
        version = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return version if version > 0 else default


def _safe_manifest_path(root, relative):
    relative = _text(relative)
    if not relative or os.path.isabs(relative):
        return None
    candidate = (Path(root) / relative).resolve(strict=False)
    if not _is_within(candidate, root):
        return None
    return candidate


def _normalize_manifest(root, raw):
    """Return a migrated manifest and explicit validation issues."""
    if not isinstance(raw, dict):
        return None, ["manifest root is not an object"]
    version = _schema_version(raw.get("version"), 1)
    if version > MANIFEST_VERSION:
        return None, [f"manifest schema {version} is newer than {MANIFEST_VERSION}"]
    manifest = dict(raw)
    manifest["version"] = MANIFEST_VERSION
    manifest.setdefault("algorithm", "sha256")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        return None, ["manifest files is not a list"]
    issues = []
    clean_entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            issues.append("manifest contains a non-object file entry")
            continue
        relative = _text(entry.get("path") or entry.get("relative_path"))
        if _safe_manifest_path(root, relative) is None:
            issues.append(f"manifest contains unsafe path: {relative}")
            continue
        clean = dict(entry)
        clean["path"] = relative
        clean_entries.append(clean)
        if not (Path(root) / relative).is_file():
            issues.append(f"manifest file is missing: {relative}")
    manifest["files"] = clean_entries
    return manifest, issues


def _date_value(*values):
    for value in values:
        text = _text(value)
        if not text:
            continue
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
        return text
    return ""


def _duration_value(*values):
    for value in values:
        try:
            parsed = float(value or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if parsed >= 0:
            return parsed
    return 0.0


def _system_tags(payload, *, platform, channel, total_secs, is_live):
    tags = []
    if platform:
        tags.append({"name": f"platform:{platform}", "kind": "system"})
    if channel:
        tags.append({"name": f"channel:{channel}", "kind": "system"})
    qualities = payload.get("qualities", [])
    if isinstance(qualities, list):
        resolutions = [
            _text(row.get("resolution"))
            for row in qualities if isinstance(row, dict)
        ]
        for resolution, label in (
            ("1080", "res:1080p"),
            ("720", "res:720p"),
            ("480", "res:480p"),
        ):
            if any(resolution in value for value in resolutions):
                tags.append({"name": label, "kind": "system"})
                break
    if total_secs > 0:
        for threshold, label in (
            (3600, "short (<1h)"),
            (7200, "medium (1-2h)"),
            (14400, "long (2-4h)"),
            (999999, "marathon (4h+)"),
        ):
            if total_secs < threshold:
                tags.append({
                    "name": f"duration:{label}", "kind": "system",
                })
                break
    tags.append({
        "name": f"type:{'live' if is_live else 'vod'}", "kind": "system",
    })
    return tags


def _dedupe_tags(rows):
    result = []
    seen = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = _text(row.get("name"))[:256]
        kind = _text(row.get("kind", "user")).lower()
        if not name or kind not in {"system", "user"}:
            continue
        key = (name.casefold(), kind)
        if key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "kind": kind})
    return result


def _info_payloads(directory):
    values = []
    for path in _sidecar_paths(directory):
        if not path.name.casefold().endswith(".info.json"):
            continue
        value = load_ytdlp_info_sidecar(path)
        if value:
            values.append((path, value))
    return values


def _nfo_payloads(directory, issue_fn=None):
    values = []
    for path in _sidecar_paths(directory):
        if path.suffix.casefold() != ".nfo":
            continue
        value = load_nfo_sidecar(path, issue_fn=issue_fn)
        if value:
            values.append((path, value))
    return values


def _record_from_directory(directory, media_paths):
    directory = Path(directory).resolve()
    issues = []
    sidecars = _sidecar_paths(directory)
    metadata_path = directory / "metadata.json"
    metadata_raw = {}
    metadata = {}
    if metadata_path.is_file():
        raw, error = _raw_json(metadata_path)
        if raw is None:
            issues.append({
                "path": str(metadata_path),
                "kind": "metadata",
                "reason": f"metadata sidecar is unreadable: {error}",
            })
        else:
            version = _schema_version(raw.get("schema_version"), 1)
            metadata_raw = raw
            if version > METADATA_SCHEMA_VERSION:
                issues.append({
                    "path": str(metadata_path),
                    "kind": "metadata",
                    "reason": (
                        f"metadata schema {version} is newer than "
                        f"{METADATA_SCHEMA_VERSION}"
                    ),
                })
            else:
                metadata = normalize_metadata_payload(raw)
    info_values = _info_payloads(directory)
    nfo_values = _nfo_payloads(directory, issue_fn=issues.append)
    valid_info_paths = {path for path, _value in info_values}
    valid_nfo_paths = {path for path, _value in nfo_values}
    for path in sidecars:
        lowered = path.name.casefold()
        if lowered.endswith(".info.json") and path not in valid_info_paths:
            issues.append({
                "path": str(path),
                "kind": "sidecar",
                "reason": "yt-dlp info sidecar is unreadable or unsupported",
            })
        elif (
            path.suffix.casefold() == ".nfo"
            and path not in valid_nfo_paths
            and not any(issue.get("path") == str(path) for issue in issues)
        ):
            issues.append({
                "path": str(path),
                "kind": "sidecar",
                "reason": "NFO sidecar is unreadable or unsupported",
            })
    if sidecars and not metadata and not info_values and not nfo_values:
        issues.append({
            "path": str(directory),
            "kind": "sidecar",
            "reason": "no readable supported metadata sidecar",
        })

    provenance = metadata.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    platform = _text(
        metadata.get("platform") or provenance.get("platform")
    )
    channel = _text(metadata.get("channel") or metadata.get("vod_channel"))
    title = _text(metadata.get("title"))
    source_id = _text(
        metadata.get("source_id") or provenance.get("source_id")
    )
    webpage_url = _text(
        metadata.get("webpage_url") or provenance.get("webpage_url")
    )
    info_value = info_values[0][1] if info_values else {}
    nfo_value = nfo_values[0][1] if nfo_values else {}
    if not platform:
        platform = _text(
            info_value.get("extractor_key")
            or info_value.get("extractor")
            or nfo_value.get("uniqueid_type")
        )
    if not title:
        title = _text(info_value.get("title") or nfo_value.get("title"))
    if not channel:
        channel = _text(
            info_value.get("channel")
            or info_value.get("uploader")
            or nfo_value.get("director")
        )
    if not source_id:
        source_id = _text(
            info_value.get("id")
            or info_value.get("display_id")
            or nfo_value.get("source_id")
        )
    if not webpage_url:
        webpage_url = _text(
            info_value.get("webpage_url")
            or info_value.get("original_url")
            or nfo_value.get("url")
        )
    provenance = build_archival_provenance(
        StreamInfo(
            platform=platform,
            channel=channel,
            source_id=source_id,
            webpage_url=webpage_url,
        ),
        source_url=webpage_url,
    )
    platform = provenance.platform or platform
    source_id = provenance.source_id
    webpage_url = provenance.webpage_url
    total_secs = _duration_value(
        metadata.get("total_secs"), metadata.get("duration"),
        info_value.get("duration"),
    )
    is_live = bool(metadata.get("is_live", False))
    if not metadata and nfo_value and not is_live:
        is_live = False
    quality = _text(metadata.get("quality"))
    if not quality:
        quality = _text(
            info_value.get("format_note") or info_value.get("resolution")
        )
    newest_mtime = 0.0
    total_size = 0
    for media_path in media_paths:
        try:
            stat = Path(media_path).stat()
        except OSError:
            continue
        newest_mtime = max(newest_mtime, stat.st_mtime)
        total_size += int(stat.st_size)
    date = _date_value(
        metadata.get("downloaded_at"), metadata.get("start_time"),
        info_value.get("upload_date"),
    )
    if not date and newest_mtime:
        date = datetime.fromtimestamp(newest_mtime, tz=UTC).isoformat(
            timespec="seconds"
        )
    tags = _dedupe_tags(metadata.get("tags", []))
    if not tags:
        tags = _system_tags(
            metadata,
            platform=platform,
            channel=channel,
            total_secs=total_secs,
            is_live=is_live,
        )
        if metadata_path.is_file():
            issues.append({
                "path": str(metadata_path),
                "kind": "tags",
                "reason": "user tags were not stored in the sidecar; system tags regenerated",
            })
    archive_key = _text(metadata.get("archive_key"))
    if not archive_key:
        archive_key = archive_key_for_provenance(provenance)
    identity_valid = bool(platform and source_id and webpage_url)
    manifest = None
    manifest_path = directory / MANIFEST_FILENAME
    if manifest_path.is_file():
        raw_manifest, error = _raw_json(manifest_path)
        if raw_manifest is None:
            issues.append({
                "path": str(manifest_path),
                "kind": "manifest",
                "reason": f"manifest is unreadable: {error}",
            })
        else:
            manifest, manifest_issues = _normalize_manifest(
                directory, raw_manifest
            )
            issues.extend({
                "path": str(manifest_path),
                "kind": "manifest",
                "reason": reason,
            } for reason in manifest_issues)
    else:
        issues.append({
            "path": str(directory),
            "kind": "manifest",
            "reason": "integrity manifest sidecar is not present",
        })
    if not metadata_raw:
        issues.append({
            "path": str(directory),
            "kind": "history",
            "reason": "favorite, watched, bookmarks, and playback position are not reconstructible",
        })
    else:
        missing_state = [
            name for name in (
                "favorite", "watched", "watch_position_secs", "bookmarks",
            ) if name not in metadata_raw
        ]
        if missing_state:
            issues.append({
                "path": str(metadata_path),
                "kind": "history",
                "reason": (
                    "history fields not present in sidecar: "
                    + ", ".join(missing_state)
                ),
            })
    if not identity_valid:
        issues.append({
            "path": str(directory),
            "kind": "identity",
            "reason": "no recoverable canonical identity",
        })
    record = {
        "date": date,
        "platform": platform,
        "source_id": source_id,
        "webpage_url": webpage_url,
        "url": webpage_url,
        "title": title or directory.name,
        "channel": channel,
        "quality": quality,
        "size": _fmt_size(total_size),
        "path": str(directory),
        "favorite": bool(metadata_raw.get("favorite", False)),
        "watched": bool(metadata_raw.get("watched", False)),
        "watch_position_secs": float(
            metadata_raw.get("watch_position_secs", 0) or 0
        ),
        "bookmarks": list(metadata_raw.get("bookmarks", []) or []),
    }
    fingerprint = _directory_fingerprint(directory, sidecars, media_paths)
    item = {
        "path": str(directory),
        "action": "rebuild" if identity_valid else "skip",
        "reason": (
            "canonical history and sidecar state recovered"
            if identity_valid else "no recoverable canonical identity"
        ),
        "record": record,
        "tags": tags,
        "archive_key": archive_key,
        "manifest": manifest,
        "sidecars": [path.name for path in sidecars],
        "file_fingerprint": fingerprint,
        "unreconstructible": [issue["reason"] for issue in issues],
    }
    return item, issues


def _directory_fingerprint(directory, sidecars, media_paths):
    rows = []
    for path in [*sidecars, *media_paths]:
        path = Path(path)
        try:
            stat = path.stat()
        except OSError:
            rows.append({"path": str(path), "missing": True})
            continue
        row = {
            "path": str(path),
            "size": int(stat.st_size),
            "mtime_ns": int(
                getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))
            ),
        }
        if path in {Path(value) for value in sidecars}:
            try:
                row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                row["sha256"] = ""
        rows.append(row)
    payload = json.dumps(
        sorted(rows, key=lambda row: row["path"]),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _collect_candidates(root, cancel_fn=None):
    root = Path(root)
    sidecar_roots = set()
    media_directories = {}
    sidecarless = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if cancel_fn and cancel_fn():
            raise InterruptedError("rebuild preview cancelled")
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".")
        )
        directory = Path(dirpath)
        sidecars = _sidecar_paths(directory)
        if sidecars:
            sidecar_roots.add(directory.resolve())
        direct_media = [
            directory / name for name in filenames
            if not name.startswith(".")
            and Path(name).suffix.lower() in _MEDIA_EXTS
            and (directory / name).is_file()
        ]
        if direct_media:
            media_directories[directory.resolve()] = sorted(
                direct_media, key=lambda path: path.name.casefold()
            )
    candidates = {}
    for directory in sorted(
        sidecar_roots, key=lambda path: str(path).casefold()
    ):
        media_paths = _media_files_under(directory)
        if media_paths:
            candidates[directory] = media_paths
        else:
            sidecarless.append({
                "path": str(directory),
                "kind": "media",
                "reason": "sidecar directory contains no media file",
            })
    for directory, media_paths in media_directories.items():
        if any(
            directory != sidecar_root
            and _is_within(directory, sidecar_root)
            for sidecar_root in sidecar_roots
        ):
            continue
        candidates.setdefault(directory, media_paths)
    return candidates, sidecarless


def _json_fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _history_content(db_module):
    path = Path(db_module.DB_PATH)
    if not path.is_file():
        return {"missing": True}
    rows = db_module.load_history()
    return {
        "rows": [
            {
                field: row.get(field)
                for field in _HISTORY_FINGERPRINT_FIELDS
            }
            for row in rows
        ],
    }


def _tag_content(tags_module):
    path = Path(tags_module.DB_PATH)
    if not path.is_file():
        return {"missing": True}
    database = path.expanduser().resolve(strict=False)
    connection = sqlite_connect(
        f"{database.as_uri()}?mode=ro",
        uri=True,
        readonly=True,
        configure_journal=False,
    )
    try:
        rows = connection.execute("""
            SELECT t.name, t.kind, rt.recording_path
            FROM tags t
            LEFT JOIN recording_tags rt ON rt.tag_id = t.id
            ORDER BY t.kind, t.name, rt.recording_path
        """).fetchall()
        return {
            "rows": [
                [value if value is not None else "" for value in row]
                for row in rows
            ],
        }
    finally:
        connection.close()


def _database_fingerprint(db_module=_db, tags_module=_tags):
    content = [
        {"path": str(Path(db_module.DB_PATH)), "history": _history_content(db_module)},
        {"path": str(Path(tags_module.DB_PATH)), "tags": _tag_content(tags_module)},
    ]
    return _json_fingerprint(content)


@dataclass
class RebuildPlan:
    plan_id: str
    created_at: str
    root: str
    database_fingerprint: str
    items: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    schema_version: int = REBUILD_PLAN_SCHEMA

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "root": self.root,
            "database_fingerprint": self.database_fingerprint,
            "items": self.items,
            "issues": self.issues,
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict):
            raise ValueError("rebuild plan must be an object")
        version = _schema_version(payload.get("schema_version"), 0)
        if version != REBUILD_PLAN_SCHEMA:
            raise ValueError(
                f"unsupported rebuild plan schema {version}"
            )
        return cls(
            plan_id=_text(payload.get("plan_id")) or uuid.uuid4().hex,
            created_at=_text(payload.get("created_at")),
            root=_text(payload.get("root")),
            database_fingerprint=_text(payload.get("database_fingerprint")),
            items=list(payload.get("items", []) or []),
            issues=list(payload.get("issues", []) or []),
            diagnostics=dict(payload.get("diagnostics", {}) or {}),
            schema_version=version,
        )


@dataclass
class RebuildResult:
    status: str = "completed"
    rebuilt: int = 0
    skipped: int = 0
    conflicts: int = 0
    issues: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    backup_path: str = ""


def plan_library_rebuild(
    root,
    *,
    db_module=_db,
    tags_module=_tags,
    cancel_fn=None,
):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"library root not found: {root}")
    candidates, issues = _collect_candidates(root, cancel_fn=cancel_fn)
    items = []
    for directory, media_paths in sorted(candidates.items(), key=lambda pair: str(pair[0]).casefold()):
        if cancel_fn and cancel_fn():
            raise InterruptedError("rebuild preview cancelled")
        item, item_issues = _record_from_directory(directory, media_paths)
        items.append(item)
        issues.extend(item_issues)

    identities = {}
    for item in items:
        if item.get("action") != "rebuild":
            continue
        record = item.get("record", {})
        key = (
            _text(record.get("platform")).casefold(),
            _text(record.get("source_id")).casefold(),
        )
        identities.setdefault(key, []).append(item)
    for key, duplicate_items in identities.items():
        if len(duplicate_items) < 2 or not key[0] or not key[1]:
            continue
        paths = [str(row.get("path", "")) for row in duplicate_items]
        reason = "duplicate canonical identity; review before rebuilding"
        for item in duplicate_items:
            item["action"] = "conflict"
            item["reason"] = reason
            item.setdefault("unreconstructible", []).append(reason)
            issues.append({
                "path": item.get("path", ""),
                "kind": "identity",
                "reason": f"{reason}: {', '.join(paths)}",
            })

    rebuild_count = sum(item.get("action") == "rebuild" for item in items)
    skip_count = sum(item.get("action") == "skip" for item in items)
    conflict_count = sum(item.get("action") == "conflict" for item in items)
    archive_count = sum(
        bool(item.get("archive_key")) and item.get("action") == "rebuild"
        for item in items
    )
    diagnostics = {
        "rebuild": rebuild_count,
        "skip": skip_count,
        "conflict": conflict_count,
        "issues": len(issues),
        "archive_keys": archive_count,
        "manifest_count": sum(bool(item.get("manifest")) for item in items),
    }
    return RebuildPlan(
        plan_id=uuid.uuid4().hex,
        created_at=_utc_now(),
        root=str(root),
        database_fingerprint=_database_fingerprint(db_module, tags_module),
        items=items,
        issues=issues,
        diagnostics=diagnostics,
    )


def save_rebuild_plan(plan, path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def load_rebuild_plan(path):
    path = Path(path).expanduser()
    if path.stat().st_size > MAX_PLAN_BYTES:
        raise ValueError("rebuild plan is too large")
    return RebuildPlan.from_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _check_plan_items(plan):
    root = Path(plan.root).expanduser().resolve()
    if not root.is_dir():
        return [f"library root not found: {root}"]
    errors = []
    for item in plan.items:
        if item.get("action") != "rebuild":
            continue
        path = Path(item.get("path", "")).expanduser().resolve()
        if not _is_within(path, root):
            errors.append(f"recording path is outside the planned root: {path}")
            continue
        media_paths = _media_files_under(path)
        sidecars = _sidecar_paths(path)
        if _directory_fingerprint(path, sidecars, media_paths) != item.get(
            "file_fingerprint", ""
        ):
            errors.append(f"recording changed after preview: {path}")
    return errors


def _swap_marker_path(pairs):
    if not pairs:
        raise ValueError("at least one database pair is required")
    return Path(pairs[0][0]).parent / REBUILD_SWAP_MARKER


def _swap_record(target, staged, plan_id):
    target = Path(target)
    staged = Path(staged)
    return {
        "target": str(target),
        "staged": str(staged),
        "previous": str(
            target.with_name(target.name + f".pre-rebuild-{plan_id}")
        ),
        "existed": target.is_file(),
    }


def _write_swap_marker(marker, plan_id, records):
    marker = Path(marker)
    if marker.exists():
        raise RuntimeError(
            f"interrupted rebuild swap marker already exists: {marker}"
        )
    temporary = marker.with_name(marker.name + ".tmp")
    payload = {
        "schema": 1,
        "plan_id": str(plan_id),
        "pairs": records,
    }
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def _clear_swap_marker(marker):
    marker = Path(marker)
    marker.unlink(missing_ok=True)
    marker.with_name(marker.name + ".tmp").unlink(missing_ok=True)


def _unlink_sqlite_file(path):
    path = Path(path)
    path.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _validate_swap_marker(marker, payload):
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ValueError("unsupported rebuild swap marker")
    plan_id = _text(payload.get("plan_id"))
    records = payload.get("pairs")
    if not plan_id or not isinstance(records, list) or not records:
        raise ValueError("invalid rebuild swap marker")
    marker_parent = Path(marker).parent.resolve(strict=False)
    expected_stage = marker_parent / f"{_REBUILD_STAGE_PREFIX}{plan_id}"
    validated = []
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("invalid rebuild swap pair")
        target = Path(raw.get("target", ""))
        staged = Path(raw.get("staged", ""))
        previous = Path(raw.get("previous", ""))
        if target.parent.resolve(strict=False) != marker_parent:
            raise ValueError("rebuild target is outside the config directory")
        if staged.parent.resolve(strict=False) != expected_stage:
            raise ValueError("rebuild stage is outside the generated stage")
        expected_previous = target.with_name(
            target.name + f".pre-rebuild-{plan_id}"
        )
        if previous != expected_previous:
            raise ValueError("invalid rebuild previous path")
        if not isinstance(raw.get("existed"), bool):
            raise ValueError("invalid rebuild existence flag")
        validated.append({
            "target": target,
            "staged": staged,
            "previous": previous,
            "existed": raw["existed"],
        })
    if expected_stage not in {record["staged"].parent for record in validated}:
        raise ValueError("missing rebuild stage")
    return validated, expected_stage


def finalize_interrupted_rebuild(*, config_dir=None, db_module=_db):
    """Roll back a rebuild whose marker survived an interrupted swap.

    The marker is deliberately resolved before the database is opened.  All
    paths are validated against the generated config/stage layout so a
    malformed marker cannot turn startup recovery into an arbitrary delete.
    """
    marker_parent = Path(
        config_dir or Path(db_module.DB_PATH).parent
    ).expanduser().resolve(strict=False)
    marker = marker_parent / REBUILD_SWAP_MARKER
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        records, stage_dir = _validate_swap_marker(marker, payload)
        for record in records:
            target = record["target"]
            previous = record["previous"]
            if previous.exists():
                _unlink_sqlite_file(target)
                if record["existed"]:
                    os.replace(previous, target)
                for suffix in ("-wal", "-shm"):
                    previous_sidecar = Path(f"{previous}{suffix}")
                    target_sidecar = Path(f"{target}{suffix}")
                    if previous_sidecar.exists():
                        os.replace(previous_sidecar, target_sidecar)
            elif not record["existed"] and target.exists():
                _unlink_sqlite_file(target)
            elif record["existed"] and not target.exists():
                raise OSError(
                    f"rebuild target disappeared without a backup: {target}"
                )
            _unlink_sqlite_file(record["staged"])
        shutil.rmtree(stage_dir, ignore_errors=True)
        if stage_dir.exists():
            raise OSError(f"could not remove rebuild stage: {stage_dir}")
        _clear_swap_marker(marker)
        return True
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _swap_databases(pairs, plan_id):
    marker = _swap_marker_path(pairs)
    records = [_swap_record(target, staged, plan_id) for target, staged in pairs]
    _write_swap_marker(marker, plan_id, records)
    moved = []
    try:
        for record in records:
            target = record["target"]
            staged = record["staged"]
            previous = record["previous"]
            target = Path(target)
            staged = Path(staged)
            previous = Path(previous)
            previous.unlink(missing_ok=True)
            sidecars = []
            if target.is_file():
                os.replace(target, previous)
                moved.append((target, previous, True, sidecars))
                for suffix in ("-wal", "-shm"):
                    live_sidecar = Path(f"{target}{suffix}")
                    previous_sidecar = Path(f"{previous}{suffix}")
                    if live_sidecar.exists():
                        os.replace(live_sidecar, previous_sidecar)
                        sidecars.append((live_sidecar, previous_sidecar))
            else:
                moved.append((target, previous, False, sidecars))
            os.replace(staged, target)
            for suffix in ("-wal", "-shm"):
                staged_sidecar = Path(f"{staged}{suffix}")
                target_sidecar = Path(f"{target}{suffix}")
                if staged_sidecar.exists():
                    os.replace(staged_sidecar, target_sidecar)
    except Exception:
        rollback_ok = True
        for target, previous, existed, sidecars in reversed(moved):
            try:
                _unlink_sqlite_file(target)
            except OSError:
                rollback_ok = False
            for live_sidecar, previous_sidecar in reversed(sidecars):
                try:
                    if previous_sidecar.exists():
                        os.replace(previous_sidecar, live_sidecar)
                except OSError:
                    rollback_ok = False
            if existed and previous.exists():
                try:
                    os.replace(previous, target)
                except OSError:
                    rollback_ok = False
        if rollback_ok:
            try:
                _clear_swap_marker(marker)
            except OSError:
                pass
        raise
    _clear_swap_marker(marker)
    for _target, previous, _existed, sidecars in moved:
        previous.unlink(missing_ok=True)
        for _live_sidecar, previous_sidecar in sidecars:
            previous_sidecar.unlink(missing_ok=True)


def apply_library_rebuild(
    plan,
    *,
    db_module=_db,
    tags_module=_tags,
    backup_fn=None,
    cancel_fn=None,
):
    result = RebuildResult(
        skipped=sum(item.get("action") == "skip" for item in plan.items),
        conflicts=sum(item.get("action") == "conflict" for item in plan.items),
        issues=list(plan.issues),
    )
    if cancel_fn and cancel_fn():
        result.status = "cancelled"
        return result
    if _database_fingerprint(db_module, tags_module) != plan.database_fingerprint:
        result.status = "stale"
        result.errors.append("Library or tag database changed after preview.")
        return result
    item_errors = _check_plan_items(plan)
    if item_errors:
        result.status = "stale"
        result.errors.extend(item_errors)
        return result

    config_dir = Path(db_module.DB_PATH).parent
    backup_dir = config_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"rebuild-{plan.plan_id}.skbackup"
    create = backup_fn or create_backup
    try:
        ok, detail = create(str(backup_path))
    except Exception as error:
        ok, detail = False, str(error)
    if not ok:
        result.status = "backup_failed"
        result.errors.append(str(detail))
        return result
    result.backup_path = str(backup_path)
    if cancel_fn and cancel_fn():
        result.status = "cancelled"
        return result

    valid_items = [
        item for item in plan.items if item.get("action") == "rebuild"
    ]
    entries = [item.get("record", {}) for item in valid_items]
    manifests = {
        _text(item.get("path")): item.get("manifest")
        for item in valid_items if isinstance(item.get("manifest"), dict)
    }
    tag_records = [
        {"path": item.get("path", ""), "tags": item.get("tags", [])}
        for item in valid_items
    ]
    stage_dir = config_dir / f"{_REBUILD_STAGE_PREFIX}{plan.plan_id}"
    stage_library = stage_dir / "library.db"
    stage_tags = stage_dir / "tags.db"
    try:
        stage_dir.mkdir(parents=True, exist_ok=False)
        db_module.build_rebuilt_library_database(
            stage_library, entries, manifests
        )
        tags_module.build_rebuilt_tags_database(stage_tags, tag_records)
        if cancel_fn and cancel_fn():
            result.status = "cancelled"
            return result
        # The configured profile connection is intentionally cached for hot
        # paths, but the live database must be released before the atomic
        # swap below can rename it on Windows.
        close_connections = getattr(db_module, "close_connections", None)
        if callable(close_connections):
            close_connections()
        _swap_databases(
            [
                (Path(db_module.DB_PATH), stage_library),
                (Path(tags_module.DB_PATH), stage_tags),
            ],
            plan.plan_id,
        )
        result.rebuilt = len(entries)
        return result
    except Exception as error:
        result.status = "failed"
        result.errors.append(str(error))
        return result
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
