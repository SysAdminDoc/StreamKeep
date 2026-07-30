"""Crash-safe planning and activation for quality-upgrade versions."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .utils import safe_filename


class UpgradeSafetyError(RuntimeError):
    """Raised when an upgrade cannot be staged or activated safely."""


@dataclass(frozen=True)
class UpgradePaths:
    existing: Path
    staging: Path
    final: Path


def identity_matches(
    expected_platform: str,
    expected_source_id: str,
    actual_platform: str,
    actual_source_id: str,
) -> bool:
    """Return true only for one exact, non-empty platform-scoped identity."""
    expected_platform = str(expected_platform or "").strip().casefold()
    actual_platform = str(actual_platform or "").strip().casefold()
    expected_source_id = str(expected_source_id or "").strip()
    actual_source_id = str(actual_source_id or "").strip()
    return bool(
        expected_platform
        and expected_source_id
        and expected_platform == actual_platform
        and expected_source_id == actual_source_id
    )


def quality_rank(quality: str) -> int:
    """Normalize common labels and WxH resolutions to a comparable height."""
    text = str(quality or "").strip().lower()
    if text in {"source", "best", "highest", "best available"}:
        return 9999
    resolution = re.search(r"\b\d{2,5}x(\d{2,5})\b", text)
    if resolution:
        return int(resolution.group(1))
    progressive = re.search(r"\b(\d{3,4})p(?:\d+)?\b", text)
    if progressive:
        return int(progressive.group(1))
    digits = re.search(r"\d+", text)
    return int(digits.group(0)) if digits else 0


def plan_upgrade_paths(
    existing_path: str | os.PathLike[str],
    job_id: str,
    quality: str = "",
) -> UpgradePaths:
    """Plan deterministic sibling paths without touching the known-good set."""
    existing = Path(existing_path).expanduser().resolve(strict=False)
    if not existing.name or existing.parent == existing:
        raise UpgradeSafetyError("Existing recording path is not a safe directory")
    token = re.sub(r"[^A-Za-z0-9]", "", str(job_id or ""))[:16]
    if len(token) < 8:
        raise UpgradeSafetyError("Upgrade job ID is missing or invalid")
    base = safe_filename(existing.name)[:80] or "recording"
    quality_label = safe_filename(str(quality or "higher"))[:24] or "higher"
    staging = existing.parent / f".{base}.streamkeep-upgrade-{token}"
    final = existing.parent / f"{base} [upgrade {quality_label} {token[:8]}]"
    _validate_siblings(existing, staging, final)
    return UpgradePaths(existing=existing, staging=staging, final=final)


def prepare_upgrade_staging(paths: UpgradePaths) -> Path:
    """Create or reuse this job's isolated stage, never replacing a target."""
    _validate_siblings(paths.existing, paths.staging, paths.final)
    if not paths.existing.is_dir():
        raise UpgradeSafetyError("Known-good recording directory is missing")
    if paths.final.exists():
        raise UpgradeSafetyError("Upgrade version target already exists")
    if paths.staging.exists() and not paths.staging.is_dir():
        raise UpgradeSafetyError("Upgrade staging path is not a directory")
    paths.staging.mkdir(parents=False, exist_ok=True)
    return paths.staging


def activate_upgrade_version(paths: UpgradePaths) -> Path:
    """Atomically publish a validated staged version beside the original."""
    _validate_siblings(paths.existing, paths.staging, paths.final)
    if not paths.existing.is_dir():
        raise UpgradeSafetyError("Known-good recording directory is missing")
    if not paths.staging.is_dir():
        raise UpgradeSafetyError("Validated upgrade staging directory is missing")
    if paths.final.exists():
        raise UpgradeSafetyError("Upgrade version target already exists")
    os.replace(paths.staging, paths.final)
    return paths.final


def _validate_siblings(existing: Path, staging: Path, final: Path) -> None:
    parent = existing.parent.resolve(strict=False)
    resolved = [
        path.resolve(strict=False)
        for path in (existing, staging, final)
    ]
    if any(path.parent != parent for path in resolved):
        raise UpgradeSafetyError("Upgrade paths must be siblings")
    if len(set(resolved)) != 3:
        raise UpgradeSafetyError("Upgrade paths must be distinct")
    if not staging.name.startswith(".") or ".streamkeep-upgrade-" not in staging.name:
        raise UpgradeSafetyError("Upgrade staging path is not isolated")
