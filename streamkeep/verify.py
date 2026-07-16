"""Download Integrity Verification - ffprobe and archive checksum checks.

Verifies downloaded media containers with ffprobe and records SHA-256
manifests for completed recording folders.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from .capabilities import CapabilityUnavailableError, resolve_tool_command
from .paths import _CREATE_NO_WINDOW

# Status constants
STATUS_OK = "verified"
STATUS_WARN = "warning"
STATUS_FAIL = "failed"

MANIFEST_FILENAME = ".streamkeep_manifest.json"
MANIFEST_VERSION = 1
HASH_CHUNK_SIZE = 1024 * 1024

MEDIA_EXTS = {".mp4", ".mkv", ".ts", ".webm", ".flv", ".mov", ".avi"}
MANIFEST_SKIP_NAMES = {
    MANIFEST_FILENAME,
    ".streamkeep_resume.json",
}
MANIFEST_SKIP_SUFFIXES = {
    ".tmp",
    ".part",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_numeric_probe_value(value, cast):
    try:
        parsed = cast(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed


def verify_media(media_path, expected_duration=0):
    """Verify a media file's integrity via ffprobe.

    Returns ``(status, details)`` where status is one of
    ``STATUS_OK``, ``STATUS_WARN``, ``STATUS_FAIL``.
    """
    if not media_path or not os.path.isfile(media_path):
        return STATUS_FAIL, "File not found"

    file_size = os.path.getsize(media_path)
    if file_size == 0:
        return STATUS_FAIL, "File is empty (0 bytes)"

    try:
        cmd = [
            resolve_tool_command("ffprobe"), "-v", "error",
            "-show_entries", "format=duration,size,nb_streams",
            "-of", "json", media_path,
        ]
        r = subprocess.run(
            cmd, capture_output=True, timeout=30,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (CapabilityUnavailableError, subprocess.TimeoutExpired, OSError) as e:
        return STATUS_FAIL, f"ffprobe error: {e}"

    if r.returncode != 0:
        stderr = r.stderr.decode("utf-8", errors="replace")[:200]
        return STATUS_FAIL, f"ffprobe failed: {stderr}"

    try:
        data = json.loads(r.stdout.decode("utf-8", errors="replace"))
        fmt = data.get("format", {})
    except (json.JSONDecodeError, ValueError):
        return STATUS_FAIL, "ffprobe returned invalid JSON"

    actual_duration = _parse_numeric_probe_value(fmt.get("duration", 0), float)
    nb_streams = _parse_numeric_probe_value(fmt.get("nb_streams", 0), int)
    if actual_duration is None or nb_streams is None:
        return STATUS_FAIL, "ffprobe returned invalid numeric metadata"

    if actual_duration <= 0:
        return STATUS_FAIL, "Duration is 0 (corrupted or incomplete)"

    if nb_streams < 1:
        return STATUS_FAIL, "No media streams found"

    if expected_duration and expected_duration > 0:
        ratio = actual_duration / expected_duration
        if ratio < 0.5:
            return STATUS_FAIL, (
                f"Duration {actual_duration:.0f}s is <50% of expected "
                f"{expected_duration:.0f}s (truncated)"
            )
        if ratio < 0.95:
            return STATUS_WARN, (
                f"Duration {actual_duration:.0f}s is {ratio*100:.0f}% of "
                f"expected {expected_duration:.0f}s (minor discrepancy)"
            )
        if ratio > 1.05:
            return STATUS_WARN, (
                f"Duration {actual_duration:.0f}s exceeds expected "
                f"{expected_duration:.0f}s by {(ratio-1)*100:.0f}%"
            )

    return STATUS_OK, (
        f"Valid ({actual_duration:.0f}s, {nb_streams} stream(s), "
        f"{file_size / 1024 / 1024:.1f} MB)"
    )


def verify_recording_dir(recording_dir, expected_duration=0):
    """Verify the first media file in a recording directory.

    Returns ``(status, details, media_path)``.
    """
    if not recording_dir or not os.path.isdir(recording_dir):
        return STATUS_FAIL, "Directory not found", ""

    media = ""
    for fn in sorted(os.listdir(recording_dir)):
        if fn.lower().endswith(tuple(MEDIA_EXTS)) and not fn.startswith("."):
            media = os.path.join(recording_dir, fn)
            break

    if not media:
        return STATUS_FAIL, "No media file found", ""

    status, details = verify_media(media, expected_duration)
    return status, details, media


def _classify_manifest_file(path: Path) -> str:
    return "media" if path.suffix.lower() in MEDIA_EXTS else "metadata"


def _iter_manifest_files(recording_dir):
    root = Path(recording_dir)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if not name.startswith(".") and name != "__pycache__"
        ]
        for filename in sorted(filenames):
            if filename in MANIFEST_SKIP_NAMES:
                continue
            path = Path(dirpath) / filename
            if filename.startswith(".") or path.suffix.lower() in MANIFEST_SKIP_SUFFIXES:
                continue
            if path.is_file():
                yield path


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_sidecar_path(recording_dir) -> Path:
    return Path(recording_dir) / MANIFEST_FILENAME


def create_archive_manifest(recording_dir, *, write_sidecar=True):
    """Create a SHA-256 archive manifest for a recording directory."""
    root = Path(recording_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Recording directory not found: {recording_dir}")

    files = []
    for path in _iter_manifest_files(root):
        try:
            st = path.stat()
            rel = path.relative_to(root).as_posix()
            files.append({
                "path": rel,
                "role": _classify_manifest_file(path),
                "size": int(st.st_size),
                "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
                "sha256": _sha256_file(path),
            })
        except (OSError, ValueError):
            continue

    manifest = {
        "version": MANIFEST_VERSION,
        "algorithm": "sha256",
        "created_at": _utc_now_iso(),
        "root": str(root),
        "files": files,
    }
    if write_sidecar:
        write_archive_manifest_sidecar(root, manifest)
    return manifest


def write_archive_manifest_sidecar(recording_dir, manifest):
    """Write *manifest* beside a recording atomically."""
    root = Path(recording_dir)
    root.mkdir(parents=True, exist_ok=True)
    sidecar = _manifest_sidecar_path(root)
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    data = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        try:
            f.flush()
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass
    os.replace(tmp, sidecar)
    return str(sidecar)


def load_archive_manifest_sidecar(recording_dir):
    """Load a recording-folder manifest sidecar, or return None."""
    sidecar = _manifest_sidecar_path(recording_dir)
    if not sidecar.is_file():
        return None
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return None


def _safe_manifest_target(root: Path, rel_path: str):
    if not rel_path or os.path.isabs(rel_path):
        return None
    candidate = root / rel_path
    try:
        resolved_root = root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    return candidate


def verify_archive_manifest(recording_dir, manifest=None):
    """Verify a recording directory against a SHA-256 archive manifest.

    Returns ``(status, details, report)``. The report contains `missing`,
    `changed`, `checked`, and `expected` entries so callers can present a
    repair/rescan workflow without parsing human-readable text.
    """
    root = Path(recording_dir)
    if not root.is_dir():
        report = {"status": STATUS_FAIL, "missing": [], "changed": [], "checked": 0, "expected": 0}
        return STATUS_FAIL, "Directory not found", report

    if manifest is None:
        manifest = load_archive_manifest_sidecar(root)
    if not isinstance(manifest, dict):
        report = {"status": STATUS_FAIL, "missing": [], "changed": [], "checked": 0, "expected": 0}
        return STATUS_FAIL, "No integrity manifest found", report

    entries = manifest.get("files", [])
    if not isinstance(entries, list) or not entries:
        report = {"status": STATUS_WARN, "missing": [], "changed": [], "checked": 0, "expected": 0}
        return STATUS_WARN, "Integrity manifest contains no files", report

    missing = []
    changed = []
    checked = 0
    for entry in entries:
        if not isinstance(entry, dict):
            changed.append({"path": "", "reason": "invalid manifest entry"})
            continue
        rel = str(entry.get("path", "") or entry.get("relative_path", "") or "")
        target = _safe_manifest_target(root, rel)
        if target is None:
            changed.append({"path": rel, "reason": "unsafe manifest path"})
            continue
        if not target.is_file():
            missing.append({"path": rel, "role": entry.get("role", "")})
            continue
        try:
            st = target.stat()
            expected_size = int(entry.get("size", -1))
            if expected_size >= 0 and st.st_size != expected_size:
                changed.append({
                    "path": rel,
                    "reason": f"size {st.st_size} != {expected_size}",
                })
                continue
            expected_hash = str(entry.get("sha256", "") or "")
            actual_hash = _sha256_file(target)
            checked += 1
            if expected_hash and actual_hash.lower() != expected_hash.lower():
                changed.append({"path": rel, "reason": "sha256 mismatch"})
        except OSError as e:
            changed.append({"path": rel, "reason": str(e)})

    status = STATUS_OK if not missing and not changed else STATUS_FAIL
    report = {
        "status": status,
        "missing": missing,
        "changed": changed,
        "checked": checked,
        "expected": len(entries),
        "created_at": manifest.get("created_at", ""),
    }
    if status == STATUS_OK:
        return status, f"Integrity verified: {checked}/{len(entries)} file(s) match", report
    parts = []
    if missing:
        parts.append(f"{len(missing)} missing")
    if changed:
        parts.append(f"{len(changed)} changed")
    return status, "Integrity drift detected: " + ", ".join(parts), report


def rescan_archive_manifest(recording_dir, *, write_sidecar=True):
    """Intentionally replace the archive manifest with current file state."""
    return create_archive_manifest(recording_dir, write_sidecar=write_sidecar)


class VerifyWorker(QThread):
    """Run integrity verification in background."""

    verified = pyqtSignal(str, str, str)  # path, status, details

    def __init__(self, media_path, expected_duration=0):
        super().__init__()
        self._path = media_path
        self._expected = expected_duration

    def run(self):
        status, details = verify_media(self._path, self._expected)
        self.verified.emit(self._path, status, details)
