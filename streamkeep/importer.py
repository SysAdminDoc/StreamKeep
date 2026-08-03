"""Preview-first adoption of existing media into the StreamKeep library.

The importer never changes media files. It classifies recording directories from
bounded public sidecars, persists only an approved preview, and applies history
rows plus monitor archive seeds in one SQLite transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import backup
from . import db as _db
from .metadata import (
    build_archival_provenance,
    load_metadata_sidecar,
    load_nfo_sidecar,
    load_ytdlp_info_sidecar,
)
from .models import StreamInfo
from .storage import MEDIA_EXTS, _fmt_size, iter_media_directories

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_LINES = 100_000
MAX_ARCHIVE_LINE_BYTES = 4_096
MAX_ARCHIVE_TARGETS = 500


@dataclass
class AdoptionPlan:
    plan_id: str
    created_at: str
    root: str
    archive_paths: list[str]
    archive_source_url: str
    history_snapshot_id: int
    history_fingerprint: str
    monitor_fingerprint: str
    items: list[dict] = field(default_factory=list)
    archive_entries: list[dict] = field(default_factory=list)
    archive_issues: list[dict] = field(default_factory=list)
    archive_files: list[dict] = field(default_factory=list)
    monitor_archive_seeds: dict[str, list[str]] = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(
            plan_id=str(value["plan_id"]),
            created_at=str(value["created_at"]),
            root=str(value["root"]),
            archive_paths=[str(item) for item in value.get("archive_paths", [])],
            archive_source_url=str(value.get("archive_source_url", "") or ""),
            history_snapshot_id=int(value.get("history_snapshot_id", 0) or 0),
            history_fingerprint=str(value.get("history_fingerprint", "")),
            monitor_fingerprint=str(value.get("monitor_fingerprint", "")),
            items=[dict(item) for item in value.get("items", [])],
            archive_entries=[dict(item) for item in value.get("archive_entries", [])],
            archive_issues=[dict(item) for item in value.get("archive_issues", [])],
            archive_files=[dict(item) for item in value.get("archive_files", [])],
            monitor_archive_seeds={
                str(key): [str(item) for item in values]
                for key, values in value.get("monitor_archive_seeds", {}).items()
            },
            diagnostics=dict(value.get("diagnostics", {})),
        )


@dataclass
class AdoptionResult:
    status: str
    adopted: int = 0
    skipped: int = 0
    conflicts: int = 0
    archive_files: int = 0
    backup_path: str = ""
    history_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _utc_now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _normal_path(path):
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path or ""))))


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value):
    return _sha256_bytes(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode(
            "utf-8"
        )
    )


def _history_fingerprint(rows):
    return _sha256_json([
        {
            key: row.get(key)
            for key in (
                "id", "date", "platform", "source_id", "webpage_url",
                "title", "channel", "quality", "size", "path", "url",
            )
        }
        for row in rows
    ])


def _monitor_fingerprint(rows):
    return _sha256_json([
        {
            key: row.get(key)
            for key in ("url", "platform", "channel_id", "archive_ids")
        }
        for row in rows
    ])


def _tree_fingerprint(directory, sidecars=None):
    directory = Path(directory)
    paths = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() in MEDIA_EXTS or path.name in set(sidecars or ()):
            try:
                stat = path.stat()
            except OSError:
                continue
            paths.append((path.name, int(stat.st_size), int(stat.st_mtime_ns)))
    return _sha256_json(sorted(paths))


def _read_text_bounded(path, maximum=MAX_ARCHIVE_BYTES):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError(f"file is missing or larger than {maximum} bytes")
    return path.read_text(encoding="utf-8", errors="replace")


def _read_archive(path):
    """Return valid yt-dlp archive lines and explicit malformed-line issues."""
    path = Path(path).expanduser().resolve()
    text = _read_text_bounded(path)
    entries = []
    issues = []
    seen = set()
    for number, raw_line in enumerate(text.splitlines(), 1):
        if number > MAX_ARCHIVE_LINES:
            issues.append({
                "path": str(path), "line": number,
                "reason": "line limit exceeded",
            })
            break
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if len(line.encode("utf-8")) > MAX_ARCHIVE_LINE_BYTES:
            issues.append({
                "path": str(path), "line": number,
                "reason": "line is too long",
            })
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not all(parts):
            issues.append({
                "path": str(path), "line": number,
                "reason": "expected extractor and id",
            })
            continue
        extractor, source_id = (str(value).strip() for value in parts)
        if any(ord(char) < 32 for char in extractor + source_id):
            issues.append({
                "path": str(path), "line": number,
                "reason": "control character",
            })
            continue
        key = (extractor.casefold(), source_id.casefold())
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            "path": str(path),
            "line": number,
            "extractor": extractor,
            "source_id": source_id,
            "archive_key": f"{extractor.casefold()}::{source_id}",
            "text": line,
        })
    return entries, issues


def _sidecar_candidates(directory, media_paths):
    directory = Path(directory)
    candidates = {directory / "metadata.json"}
    for media in media_paths:
        media = Path(media)
        candidates.update({
            directory / f"{media.name}.info.json",
            directory / f"{media.stem}.info.json",
            directory / f"{media.stem}.nfo",
        })
    candidates.update(directory.glob("*.info.json"))
    candidates.update(directory.glob("*.nfo"))
    return sorted(
        (path for path in candidates if path.is_file()),
        key=lambda path: path.name.casefold(),
    )


def _load_sidecars(directory, media_paths):
    paths = _sidecar_candidates(directory, media_paths)
    metadata = {}
    infos = []
    nfos = []
    unreadable = []
    metadata_path = Path(directory) / "metadata.json"
    if metadata_path in paths:
        metadata = load_metadata_sidecar(metadata_path)
        if not metadata:
            unreadable.append(metadata_path.name)
    for path in paths:
        if path.name == "metadata.json":
            continue
        if path.name.casefold().endswith(".info.json"):
            value = load_ytdlp_info_sidecar(path)
            if value:
                infos.append((path, value))
            else:
                unreadable.append(path.name)
        elif path.suffix.casefold() == ".nfo":
            value = load_nfo_sidecar(path)
            if value:
                nfos.append((path, value))
            else:
                unreadable.append(path.name)
    return metadata, infos, nfos, unreadable, [path.name for path in paths]


def _as_text(value):
    return str(value or "").strip()


def _date_value(*values):
    for value in values:
        text = _as_text(value)
        if not text:
            continue
        if len(text) == 8 and text.isdigit():
            text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
        return text
    return ""


def _platform_from_info(info):
    return _as_text(info.get("extractor_key") or info.get("extractor") or "yt-dlp")


def _source_from_payload(payload, kind):
    if kind == "metadata":
        provenance = payload.get("provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        platform = _as_text(provenance.get("platform") or payload.get("platform"))
        source_id = _as_text(provenance.get("source_id") or payload.get("source_id"))
        webpage_url = _as_text(
            provenance.get("webpage_url")
            or payload.get("webpage_url")
            or payload.get("url")
        )
        title = _as_text(payload.get("title"))
        channel = _as_text(payload.get("channel") or payload.get("vod_channel"))
        date = _date_value(payload.get("downloaded_at"), payload.get("vod_date"))
        quality = _as_text(payload.get("quality"))
    elif kind == "info":
        platform = _platform_from_info(payload)
        source_id = _as_text(payload.get("id") or payload.get("display_id"))
        webpage_url = _as_text(
            payload.get("webpage_url") or payload.get("original_url")
        )
        title = _as_text(payload.get("title"))
        channel = _as_text(
            payload.get("channel") or payload.get("uploader")
            or payload.get("channel_id") or payload.get("uploader_id")
        )
        date = _date_value(payload.get("upload_date"), payload.get("timestamp"))
        quality = _as_text(payload.get("format_note") or payload.get("resolution"))
    else:
        platform = _as_text(payload.get("studio") or payload.get("uniqueid_type"))
        source_id = _as_text(payload.get("source_id"))
        webpage_url = _as_text(payload.get("url"))
        title = _as_text(payload.get("title"))
        channel = _as_text(payload.get("director") or payload.get("credits"))
        date = _date_value(payload.get("premiered"))
        quality = ""
    if not platform:
        platform = "Unknown"
    provenance = build_archival_provenance(
        StreamInfo(
            platform=platform,
            channel=channel,
            source_id=source_id,
            webpage_url=webpage_url,
        ),
        source_url=webpage_url,
    )
    return {
        "platform": provenance.platform or platform,
        "source_id": provenance.source_id,
        "webpage_url": provenance.webpage_url,
        "title": title,
        "channel": channel,
        "date": date,
        "quality": quality,
    }


def _identity_key(record):
    source_id = _as_text(record.get("source_id"))
    platform = _as_text(record.get("platform")).casefold()
    if source_id:
        return ("id", platform, source_id.casefold())
    webpage_url = _as_text(record.get("webpage_url"))
    if webpage_url:
        return ("url", webpage_url.casefold())
    return ()


def _archive_id_matches(source_id, archive_id):
    left = _as_text(source_id).casefold()
    right = _as_text(archive_id).casefold()
    if not left or not right:
        return False
    if left == right:
        return True
    return left.rsplit(":", 1)[-1] == right.rsplit(":", 1)[-1]


def _platform_matches(platform, extractor):
    left = _as_text(platform).casefold()
    right = _as_text(extractor).casefold()
    if not left or not right:
        return False
    aliases = {
        "youtube": {"youtube", "yt-dlp", "youtubedl"},
        "yt-dlp": {"youtube", "yt-dlp", "youtubedl"},
    }
    if right in aliases.get(left, set()) or left in aliases.get(right, set()):
        return True
    return left == right or left in right or right in left


def _archive_line_for(record):
    source_id = _as_text(record.get("source_id"))
    platform = _as_text(record.get("platform"))
    if not source_id or not platform:
        return ""
    return f"{platform.casefold()} {source_id}"


def _archive_path_for(source_url, db_module):
    import hashlib as _hashlib

    from .metadata import canonical_webpage_url

    canonical = canonical_webpage_url(source_url)
    identity = _as_text(canonical or source_url).encode("utf-8")
    digest = _hashlib.sha256(identity).hexdigest()
    return str(Path(db_module.DB_PATH).parent / "download-archives" / f"{digest}.txt")


def _read_existing_archive(path):
    path = Path(path)
    if not path.is_file():
        return [], _sha256_bytes(b"")
    raw = path.read_bytes()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError(f"archive is larger than {MAX_ARCHIVE_BYTES} bytes")
    lines = [
        line.strip()
        for line in raw.decode("utf-8", errors="replace").splitlines()
    ]
    lines = [line for line in lines if line]
    return lines, _sha256_bytes(raw)


def _monitor_targets(monitors, record=None, extractor=""):
    candidates = [
        row for row in monitors
        if _platform_matches(record.get("platform", "") if record else extractor,
                             row.get("platform", ""))
    ] if record else [
        row for row in monitors
        if _platform_matches(row.get("platform", ""), extractor)
    ]
    if record and record.get("channel"):
        channel = _as_text(record.get("channel")).casefold()
        exact = [
            row for row in candidates
            if _as_text(row.get("channel_id")).casefold() == channel
        ]
        if exact:
            return exact
    return candidates


def _classify_directory(
    directory, db_module, existing_paths, existing_ids, existing_urls,
    media_paths=None,
):
    directory = Path(directory)
    media_paths = sorted(
        (Path(path) for path in (media_paths or ())),
        key=lambda path: path.name.casefold(),
    )
    metadata, infos, nfos, unreadable, sidecars = _load_sidecars(
        directory, media_paths,
    )
    payloads = []
    if metadata:
        payloads.append(("metadata", metadata, "metadata.json"))
    payloads.extend(("info", value, path.name) for path, value in infos)
    payloads.extend(("nfo", value, path.name) for path, value in nfos)
    sources = [_source_from_payload(value, kind) for kind, value, _ in payloads]
    identity_keys = {
        _identity_key(source)
        for source in sources if _identity_key(source)
    }
    if len(identity_keys) > 1:
        return {
            "path": str(directory), "action": "conflict",
            "reason": "sidecars disagree on the canonical identity",
            "media_files": [str(path) for path in media_paths],
            "sidecars": sidecars,
            "file_fingerprint": _tree_fingerprint(directory, sidecars),
        }
    source = dict(sources[0]) if sources else {}
    for candidate in sources[1:]:
        for key in (
            "platform", "source_id", "webpage_url", "title", "channel",
            "date", "quality",
        ):
            if not source.get(key) and candidate.get(key):
                source[key] = candidate[key]
    if not source:
        reason = (
            "sidecar is unreadable: " + ", ".join(unreadable)
            if unreadable else "no supported metadata sidecar"
        )
        return {
            "path": str(directory), "action": "conflict", "reason": reason,
            "media_files": [str(path) for path in media_paths],
            "sidecars": sidecars,
            "file_fingerprint": _tree_fingerprint(directory, sidecars),
        }
    if not source.get("source_id") and not source.get("webpage_url"):
        return {
            "path": str(directory), "action": "conflict",
            "reason": "sidecar has no recoverable canonical identity",
            "media_files": [str(path) for path in media_paths],
            "sidecars": sidecars,
            "file_fingerprint": _tree_fingerprint(directory, sidecars),
        }
    try:
        newest = max(path.stat().st_mtime for path in media_paths)
    except (OSError, ValueError):
        newest = 0
    record = {
        "date": source.get("date") or (
            datetime.fromtimestamp(newest, tz=UTC).isoformat(timespec="seconds")
            if newest else ""
        ),
        "platform": source.get("platform", "Unknown"),
        "source_id": source.get("source_id", ""),
        "webpage_url": source.get("webpage_url", ""),
        "title": source.get("title") or directory.name,
        "channel": source.get("channel", ""),
        "quality": source.get("quality", ""),
        "size": _fmt_size(sum(path.stat().st_size for path in media_paths)),
        "path": str(directory),
        "url": source.get("webpage_url", ""),
    }
    record = db_module._canonical_history_entry(record)
    identity = _identity_key(record)
    path_key = _normal_path(directory)
    if path_key in existing_paths:
        action, reason = "skip", "path is already indexed"
    elif identity and identity in existing_ids:
        action, reason = "skip", "canonical identity is already indexed"
    elif record.get("webpage_url", "").casefold() in existing_urls:
        action, reason = "skip", "canonical page URL is already indexed"
    else:
        action, reason = "adopt", "canonical identity recovered from sidecar"
    return {
        "path": str(directory), "action": action, "reason": reason,
        "record": record, "identity_key": list(identity),
        "media_files": [str(path) for path in media_paths],
        "sidecars": sidecars,
        "file_fingerprint": _tree_fingerprint(directory, sidecars),
    }


def preview_adoption(
    root,
    archive_paths=(),
    *,
    archive_source_url="",
    db_module=_db,
    cancel_fn=None,
) -> AdoptionPlan:
    """Build a read-only adoption plan for a directory tree and archives."""
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError(f"adoption root is not a directory: {root}")
    db_module.init_db()
    history = list(db_module.iter_history(page_size=1000))
    monitors = db_module.load_monitor_channels()
    existing_paths = {
        _normal_path(row.get("path"))
        for row in history if row.get("path")
    }
    existing_ids = {
        (
            "id", _as_text(row.get("platform")).casefold(),
            _as_text(row.get("source_id")).casefold(),
        )
        for row in history if row.get("source_id")
    }
    existing_urls = {
        _as_text(row.get("webpage_url") or row.get("url")).casefold()
        for row in history if row.get("webpage_url") or row.get("url")
    }
    items = []
    directories = {}
    for directory, media_paths in iter_media_directories(
        root_path, cancel_fn=cancel_fn,
    ):
        if cancel_fn and cancel_fn():
            raise InterruptedError("adoption preview cancelled")
        directories[directory] = media_paths
    for directory in sorted(directories, key=str.casefold):
        if cancel_fn and cancel_fn():
            raise InterruptedError("adoption preview cancelled")
        items.append(_classify_directory(
            directory, db_module, existing_paths, existing_ids, existing_urls,
            directories[directory],
        ))

    identity_counts = {}
    for item in items:
        if item.get("action") == "adopt":
            identity_counts[tuple(item.get("identity_key", []))] = (
                identity_counts.get(tuple(item.get("identity_key", [])), 0) + 1
            )
    for item in items:
        identity = tuple(item.get("identity_key", []))
        if item.get("action") == "adopt" and identity_counts.get(identity, 0) > 1:
            item["action"] = "conflict"
            item["reason"] = "canonical identity appears in multiple folders"

    archive_entries = []
    archive_issues = []
    archive_paths = [
        str(Path(path).expanduser().resolve())
        for path in archive_paths if str(path).strip()
    ]
    for path in archive_paths:
        entries, issues = _read_archive(path)
        archive_entries.extend(entries)
        archive_issues.extend(issues)
    archive_entries = list({
        (entry["extractor"].casefold(), entry["source_id"].casefold()): entry
        for entry in archive_entries
    }.values())

    adopted = [item["record"] for item in items if item.get("action") == "adopt"]
    generated_lines = [
        line for record in adopted if (line := _archive_line_for(record))
    ]
    all_archive_entries = list(archive_entries)
    seen_archive = {
        (entry["extractor"].casefold(), entry["source_id"].casefold())
        for entry in all_archive_entries
    }
    for line in generated_lines:
        extractor, source_id = line.split(None, 1)
        key = (extractor.casefold(), source_id.casefold())
        if key not in seen_archive:
            all_archive_entries.append({
                "extractor": extractor, "source_id": source_id,
                "archive_key": f"{extractor.casefold()}::{source_id}",
                "text": line, "path": "generated", "line": 0,
            })
            seen_archive.add(key)

    monitor_archive_seeds = {}
    archive_lines_by_url = {}
    for record in adopted:
        line = _archive_line_for(record)
        targets = _monitor_targets(monitors, record=record)
        if not targets and record.get("webpage_url"):
            if line:
                archive_lines_by_url.setdefault(
                    _as_text(record["webpage_url"]), []
                ).append(line)
        if not line:
            continue
        key = (
            f"{_as_text(record.get('platform')).casefold()}::"
            f"{_as_text(record.get('source_id'))}"
        )
        for target in targets:
            url = _as_text(target.get("url"))
            if not url:
                continue
            monitor_archive_seeds.setdefault(url, []).append(key)
            archive_lines_by_url.setdefault(url, []).append(line)
    for entry in all_archive_entries:
        targets = _monitor_targets(monitors, extractor=entry["extractor"])
        if not targets and archive_source_url:
            archive_lines_by_url.setdefault(
                _as_text(archive_source_url), []
            ).append(entry["text"])
        elif not targets and entry.get("path") != "generated":
            archive_issues.append({
                "path": entry.get("path", ""),
                "line": entry.get("line", 0),
                "reason": "no matching monitor; pass --archive-source-url to seed it",
            })
        for target in targets:
            url = _as_text(target.get("url"))
            if not url:
                continue
            monitor_archive_seeds.setdefault(url, []).append(entry["archive_key"])
            archive_lines_by_url.setdefault(url, []).append(entry["text"])

    archive_files = []
    for source_url, additions in sorted(archive_lines_by_url.items()):
        if len(archive_files) >= MAX_ARCHIVE_TARGETS:
            archive_issues.append({
                "path": source_url, "line": 0,
                "reason": "archive target limit exceeded",
            })
            break
        target = _archive_path_for(source_url, db_module)
        existing, digest = _read_existing_archive(target)
        final_lines = list(dict.fromkeys(existing + additions))
        archive_files.append({
            "source_url": source_url,
            "target": target,
            "existing_sha256": digest,
            "lines": final_lines,
            "added": max(0, len(final_lines) - len(existing)),
        })

    for key, values in list(monitor_archive_seeds.items()):
        monitor_archive_seeds[key] = list(dict.fromkeys(values))
    diagnostics = {
        "media_directories": len(items),
        "adopt": sum(item.get("action") == "adopt" for item in items),
        "skip": sum(item.get("action") == "skip" for item in items),
        "conflict": sum(item.get("action") == "conflict" for item in items),
        "archive_entries": len(archive_entries),
        "archive_issues": len(archive_issues),
        "archive_targets": len(archive_files),
        "backup_dir": str(Path(db_module.DB_PATH).parent / "backups"),
    }
    return AdoptionPlan(
        plan_id=uuid.uuid4().hex,
        created_at=_utc_now(),
        root=str(root_path),
        archive_paths=archive_paths,
        archive_source_url=_as_text(archive_source_url),
        history_snapshot_id=db_module.history_snapshot_id(),
        history_fingerprint=_history_fingerprint(history),
        monitor_fingerprint=_monitor_fingerprint(monitors),
        items=items,
        archive_entries=all_archive_entries,
        archive_issues=archive_issues,
        archive_files=archive_files,
        monitor_archive_seeds=monitor_archive_seeds,
        diagnostics=diagnostics,
    )


def save_adoption_plan(plan, path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(plan.to_dict(), indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def load_adoption_plan(path):
    path = Path(path).expanduser()
    return AdoptionPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _restore_archive_targets(replaced):
    for target, prior in reversed(replaced):
        try:
            if prior is None:
                Path(target).unlink(missing_ok=True)
            else:
                Path(target).write_bytes(prior)
        except OSError:
            pass


def apply_adoption(
    plan,
    *,
    db_module=_db,
    backup_fn=None,
    cancel_fn=None,
) -> AdoptionResult:
    """Apply an unchanged plan without moving or rewriting media files."""
    result = AdoptionResult(
        status="completed",
        skipped=sum(item.get("action") == "skip" for item in plan.items),
        conflicts=sum(item.get("action") == "conflict" for item in plan.items)
        + len(plan.archive_issues),
    )
    if cancel_fn and cancel_fn():
        result.status = "cancelled"
        return result
    current_history = list(db_module.iter_history(page_size=1000))
    current_monitors = db_module.load_monitor_channels()
    if (
        db_module.history_snapshot_id() != plan.history_snapshot_id
        or _history_fingerprint(current_history) != plan.history_fingerprint
        or _monitor_fingerprint(current_monitors) != plan.monitor_fingerprint
    ):
        result.status = "stale"
        result.errors.append("Library or monitor state changed after preview.")
        return result
    for item in plan.items:
        if item.get("action") != "adopt":
            continue
        directory = Path(item.get("path", ""))
        if (
            not directory.is_dir()
            or _tree_fingerprint(directory, item.get("sidecars"))
            != item.get("file_fingerprint")
        ):
            result.status = "stale"
            result.errors.append(f"Recording changed after preview: {directory}")
            return result
    mutating = bool(
        any(item.get("action") == "adopt" for item in plan.items)
        or plan.archive_files
        or plan.monitor_archive_seeds
    )
    if not mutating:
        return result
    backup_dir = Path(
        plan.diagnostics.get("backup_dir")
        or Path(db_module.DB_PATH).parent / "backups"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"adoption-{plan.plan_id}.skbackup"
    create = backup_fn or backup.create_backup
    ok, detail = create(str(backup_path))
    if not ok:
        result.status = "backup_failed"
        result.errors.append(str(detail))
        return result
    result.backup_path = str(backup_path)
    if cancel_fn and cancel_fn():
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass
        result.backup_path = ""
        result.status = "cancelled"
        return result

    staged = []
    replaced = []
    try:
        for archive in plan.archive_files:
            target = Path(archive["target"]).expanduser().resolve()
            archive_root = (
                Path(db_module.DB_PATH).parent / "download-archives"
            ).resolve()
            inside_archive = (
                os.path.commonpath((str(target), str(archive_root)))
                == str(archive_root)
            )
            if not inside_archive:
                raise ValueError(
                    "archive target is outside the configured archive directory"
                )
            existing, digest = _read_existing_archive(target)
            if digest != archive.get("existing_sha256", ""):
                raise RuntimeError(f"archive changed after preview: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + f".{plan.plan_id}.tmp")
            temporary.write_text(
                "\n".join(archive.get("lines", [])) + "\n",
                encoding="utf-8",
            )
            staged.append(temporary)

        if cancel_fn and cancel_fn():
            result.status = "cancelled"
            return result
        for archive, temporary in zip(plan.archive_files, staged, strict=False):
            target = Path(archive["target"])
            prior = target.read_bytes() if target.is_file() else None
            os.replace(temporary, target)
            replaced.append((target, prior))

        entries = [
            item["record"] for item in plan.items if item.get("action") == "adopt"
        ]
        result.history_ids = db_module.adopt_history_records(
            entries,
            monitor_archive_seeds=plan.monitor_archive_seeds,
        )
        result.adopted = len(result.history_ids)
        result.archive_files = sum(
            int(archive.get("added", 0) or 0) > 0 for archive in plan.archive_files
        )
        return result
    except Exception as error:
        _restore_archive_targets(replaced)
        result.status = "failed"
        result.errors.append(str(error))
        return result
    finally:
        for temporary in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
