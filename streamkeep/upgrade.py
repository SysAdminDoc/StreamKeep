"""Quality-upgrade decisions and crash-safe version activation.

The monitor and both queue execution lanes use this module as the single
policy boundary.  It deliberately contains no Qt or database code so the
decision can be tested before a downloader is allowed to write bytes.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import safe_filename


DEFAULT_UPGRADE_LADDER = (
    "360p",
    "480p",
    "720p",
    "1080p",
    "1440p",
    "2160p",
    "source",
)
DEFAULT_VERSION_KEEP = 3
MAX_VERSION_KEEP = 10
_MAX_LADDER_ITEMS = 32
_MAX_MATCHERS = 64


class UpgradeSafetyError(RuntimeError):
    """Raised when an upgrade cannot be staged or activated safely."""


class UpgradeProfileError(ValueError):
    """Raised when an upgrade profile is not an explicit safe policy."""


@dataclass(frozen=True)
class UpgradeDecision:
    """One deterministic policy result suitable for durable audit logging."""

    decision: str
    reason_code: str
    reason: str
    current_quality: str = ""
    candidate_quality: str = ""
    current_rank: int = -1
    candidate_rank: int = -1
    score: float = 0.0
    matcher_name: str = ""
    platform: str = ""
    source_id: str = ""

    @property
    def accepted(self) -> bool:
        return self.decision == "accepted"

    @property
    def deferred(self) -> bool:
        return self.decision == "deferred"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "current_quality": self.current_quality,
            "candidate_quality": self.candidate_quality,
            "current_rank": self.current_rank,
            "candidate_rank": self.candidate_rank,
            "score": self.score,
            "matcher_name": self.matcher_name,
            "platform": self.platform,
            "source_id": self.source_id,
        }


@dataclass(frozen=True)
class UpgradePaths:
    existing: Path
    staging: Path
    final: Path


def default_upgrade_profile(minimum_quality: str = "") -> dict[str, Any]:
    """Return the explicit profile used by legacy monitor settings."""
    profile: dict[str, Any] = {
        "ladder": list(DEFAULT_UPGRADE_LADDER),
        "cutoff": "source",
        "matchers": [],
        "minimum_score": 0,
        "version_keep": DEFAULT_VERSION_KEEP,
    }
    if str(minimum_quality or "").strip():
        profile["minimum_quality"] = str(minimum_quality).strip()
    return profile


def _text(value: Any, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def _get(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_upgrade_profile(
    profile: Mapping[str, Any] | None = None,
    *,
    minimum_quality: str = "",
    allow_legacy_default: bool = True,
) -> dict[str, Any]:
    """Validate and normalize a profile without preserving arbitrary input.

    ``ladder`` is ordered from lowest to highest quality.  ``cutoff`` is
    intentionally mandatory for caller-supplied profiles; an empty legacy
    profile is expanded to the explicit shipped default so old settings keep
    working without silently becoming a broader policy.
    """
    if profile is None:
        raw = {}
    elif isinstance(profile, Mapping):
        raw = dict(profile)
    else:
        raise UpgradeProfileError("upgrade profile must be an object")
    if not raw and allow_legacy_default:
        raw = default_upgrade_profile(minimum_quality)
    ladder_raw = raw.get("ladder", raw.get("format_ladder", raw.get("qualities")))
    if not isinstance(ladder_raw, (list, tuple)):
        raise UpgradeProfileError("upgrade profile ladder must be an ordered list")
    ladder: list[str] = []
    for value in ladder_raw[:_MAX_LADDER_ITEMS]:
        if isinstance(value, Mapping):
            value = value.get("name", value.get("format", ""))
        label = _text(value, 64)
        if label and label.casefold() not in {item.casefold() for item in ladder}:
            ladder.append(label)
    if len(ladder) < 2:
        raise UpgradeProfileError("upgrade profile ladder needs at least two formats")
    cutoff = _text(
        raw.get("cutoff", raw.get("upgrade_until", raw.get("max_quality", ""))),
        64,
    )
    if not cutoff:
        raise UpgradeProfileError("upgrade profile requires an explicit cutoff")
    cutoff_index = next(
        (index for index, value in enumerate(ladder)
         if value.casefold() == cutoff.casefold()),
        -1,
    )
    if cutoff_index < 0:
        raise UpgradeProfileError("upgrade profile cutoff must be in the ladder")

    matchers_raw = raw.get("matchers", raw.get("scored_matchers", []))
    if matchers_raw is None:
        matchers_raw = []
    if not isinstance(matchers_raw, (list, tuple)):
        raise UpgradeProfileError("upgrade profile matchers must be a list")
    matchers: list[dict[str, Any]] = []
    for index, item in enumerate(matchers_raw[:_MAX_MATCHERS]):
        if not isinstance(item, Mapping):
            raise UpgradeProfileError(f"matcher {index + 1} must be an object")
        field = _text(item.get("field", "title"), 32).casefold()
        operator = _text(
            item.get("operator", item.get("match", "contains")), 24,
        ).casefold()
        pattern = _text(item.get("pattern", item.get("value", "")), 160)
        if field not in {
            "title", "channel", "quality", "platform", "source_id",
            "format", "container",
        }:
            raise UpgradeProfileError(f"matcher {index + 1} uses an invalid field")
        if operator not in {"contains", "equals", "startswith", "endswith", "regex"}:
            raise UpgradeProfileError(f"matcher {index + 1} uses an invalid operator")
        if not pattern:
            raise UpgradeProfileError(f"matcher {index + 1} has no pattern")
        if operator == "regex":
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as error:
                raise UpgradeProfileError(
                    f"matcher {index + 1} has invalid regex: {error}"
                ) from error
        score = _number(item.get("score", 0), 0.0)
        if not math.isfinite(score):
            raise UpgradeProfileError(f"matcher {index + 1} score is invalid")
        name = _text(item.get("name", ""), 64) or f"matcher-{index + 1}"
        matchers.append({
            "name": name,
            "field": field,
            "operator": operator,
            "pattern": pattern,
            "score": score,
        })
    try:
        minimum_score = float(raw.get("minimum_score", raw.get("min_score", 0)) or 0)
    except (TypeError, ValueError) as error:
        raise UpgradeProfileError("upgrade profile minimum score is invalid") from error
    if not math.isfinite(minimum_score):
        raise UpgradeProfileError("upgrade profile minimum score is invalid")
    try:
        version_keep = max(
            1,
            min(MAX_VERSION_KEEP, int(raw.get("version_keep", DEFAULT_VERSION_KEEP) or 1)),
        )
    except (TypeError, ValueError) as error:
        raise UpgradeProfileError("upgrade profile version retention is invalid") from error
    minimum = _text(raw.get("minimum_quality", minimum_quality), 64)
    if minimum:
        minimum_rank = _format_rank(minimum, ladder)
        if minimum_rank < 0:
            raise UpgradeProfileError(
                "upgrade profile minimum quality must be in the ladder"
            )
        minimum = ladder[minimum_rank]
    return {
        "ladder": ladder,
        "cutoff": ladder[cutoff_index],
        "matchers": matchers,
        "minimum_score": minimum_score,
        "minimum_quality": minimum,
        "version_keep": version_keep,
    }


def validate_upgrade_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a user profile and require its explicit policy fields."""
    return normalize_upgrade_profile(profile, allow_legacy_default=False)


def _matcher_matches(matcher: Mapping[str, Any], candidate: Any) -> bool:
    value = _text(_get(candidate, str(matcher["field"]), ""), 400).casefold()
    pattern = _text(matcher.get("pattern", ""), 160).casefold()
    operator = str(matcher.get("operator", "contains"))
    if operator == "equals":
        return value == pattern
    if operator == "startswith":
        return value.startswith(pattern)
    if operator == "endswith":
        return value.endswith(pattern)
    if operator == "regex":
        return re.search(str(matcher.get("pattern", "")), value, re.IGNORECASE) is not None
    return pattern in value


def _format_rank(quality: str, ladder: tuple[str, ...] | list[str]) -> int:
    label = _text(quality, 64).casefold()
    if not label:
        return -1
    for index, item in enumerate(ladder):
        if label == item.casefold():
            return index
    numeric = quality_rank(quality)
    if numeric <= 0:
        return -1
    numeric_ladder = [quality_rank(item) for item in ladder]
    matching = [
        index for index, value in enumerate(numeric_ladder)
        if value == numeric and value > 0
    ]
    if matching:
        return matching[-1]
    lower = [index for index, value in enumerate(numeric_ladder) if 0 < value <= numeric]
    return lower[-1] if lower else -1


def evaluate_upgrade(
    current: Any,
    candidate: Any,
    profile: Mapping[str, Any] | None = None,
    *,
    enabled: bool = True,
    defer_unknown_quality: bool = False,
    expected_platform: str = "",
    expected_source_id: str = "",
) -> UpgradeDecision:
    """Evaluate one candidate and return a named, auditable decision."""
    platform = _text(_get(candidate, "platform", ""), 64)
    source_id = _text(_get(candidate, "source_id", ""), 160)
    if not enabled:
        return UpgradeDecision(
            "rejected", "upgrades_disabled", "Automatic upgrades are disabled",
            platform=platform, source_id=source_id,
        )
    if expected_platform and platform.casefold() != _text(expected_platform, 64).casefold():
        return UpgradeDecision(
            "rejected", "identity_mismatch", "Candidate platform does not match the queued identity",
            platform=platform, source_id=source_id,
        )
    if expected_source_id and source_id != _text(expected_source_id, 160):
        return UpgradeDecision(
            "rejected", "identity_mismatch", "Candidate source identity does not match the queued identity",
            platform=platform, source_id=source_id,
        )
    if not platform or not source_id:
        return UpgradeDecision(
            "rejected", "missing_identity", "A canonical platform and source identity are required",
            platform=platform, source_id=source_id,
        )
    try:
        normalized = normalize_upgrade_profile(profile)
    except (UpgradeProfileError, TypeError, ValueError) as error:
        return UpgradeDecision(
            "rejected", "invalid_profile", str(error),
            platform=platform, source_id=source_id,
        )
    current_quality = _text(_get(current, "quality", ""), 64)
    candidate_quality = _text(_get(candidate, "quality", ""), 64)
    ladder = normalized["ladder"]
    if not current_quality:
        return UpgradeDecision(
            "rejected", "current_quality_unknown", "The known-good recording has no quality label",
            candidate_quality=candidate_quality, platform=platform, source_id=source_id,
        )
    if not candidate_quality:
        if defer_unknown_quality:
            return UpgradeDecision(
                "deferred", "candidate_quality_deferred", "Candidate quality will be resolved before download",
                current_quality=current_quality,
                candidate_quality=candidate_quality,
                current_rank=_format_rank(current_quality, ladder),
                platform=platform, source_id=source_id,
            )
        return UpgradeDecision(
            "rejected", "candidate_quality_unknown", "Candidate quality could not be resolved",
            current_quality=current_quality,
            candidate_quality=candidate_quality,
            current_rank=_format_rank(current_quality, ladder),
            platform=platform, source_id=source_id,
        )
    current_rank = _format_rank(current_quality, ladder)
    candidate_rank = _format_rank(candidate_quality, ladder)
    if current_rank < 0:
        return UpgradeDecision(
            "rejected", "current_format_unknown", "The known-good quality is not in the upgrade ladder",
            current_quality=current_quality, candidate_quality=candidate_quality,
            candidate_rank=candidate_rank, platform=platform, source_id=source_id,
        )
    if candidate_rank < 0:
        return UpgradeDecision(
            "rejected", "candidate_format_unknown", "Candidate quality is not in the upgrade ladder",
            current_quality=current_quality, candidate_quality=candidate_quality,
            current_rank=current_rank, platform=platform, source_id=source_id,
        )
    cutoff_rank = _format_rank(normalized["cutoff"], ladder)
    if candidate_rank > cutoff_rank:
        return UpgradeDecision(
            "rejected", "above_upgrade_cutoff", "Candidate is above the configured Upgrade Until cutoff",
            current_quality=current_quality, candidate_quality=candidate_quality,
            current_rank=current_rank, candidate_rank=candidate_rank,
            platform=platform, source_id=source_id,
        )
    if candidate_rank <= current_rank:
        return UpgradeDecision(
            "rejected", "not_an_upgrade", "Candidate is not higher than the known-good recording",
            current_quality=current_quality, candidate_quality=candidate_quality,
            current_rank=current_rank, candidate_rank=candidate_rank,
            platform=platform, source_id=source_id,
        )
    minimum = _text(normalized.get("minimum_quality", ""), 64)
    if minimum and candidate_rank < _format_rank(minimum, ladder):
        return UpgradeDecision(
            "rejected", "below_minimum_quality", f"Candidate is below the {minimum} minimum",
            current_quality=current_quality, candidate_quality=candidate_quality,
            current_rank=current_rank, candidate_rank=candidate_rank,
            platform=platform, source_id=source_id,
        )

    score = 0.0
    for matcher in normalized["matchers"]:
        if not _matcher_matches(matcher, candidate):
            continue
        matcher_score = float(matcher["score"])
        if matcher_score < 0:
            name = str(matcher["name"])
            return UpgradeDecision(
                "rejected", "matcher_veto", f"Candidate rejected by hard-veto matcher '{name}'",
                current_quality=current_quality, candidate_quality=candidate_quality,
                current_rank=current_rank, candidate_rank=candidate_rank,
                score=score + matcher_score, matcher_name=name,
                platform=platform, source_id=source_id,
            )
        score += matcher_score
    if score < float(normalized["minimum_score"]):
        return UpgradeDecision(
            "rejected", "matcher_score_below_minimum", "Candidate score is below the profile minimum",
            current_quality=current_quality, candidate_quality=candidate_quality,
            current_rank=current_rank, candidate_rank=candidate_rank, score=score,
            platform=platform, source_id=source_id,
        )
    return UpgradeDecision(
        "accepted", "quality_upgrade_eligible", "Candidate is a higher same-identity quality within the cutoff",
        current_quality=current_quality, candidate_quality=candidate_quality,
        current_rank=current_rank, candidate_rank=candidate_rank, score=score,
        platform=platform, source_id=source_id,
    )


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


def _version_root_name(name: str) -> str:
    root = str(name or "")
    pattern = re.compile(r"\s+\[upgrade [^\]]+ [A-Za-z0-9]{8}\]$")
    while pattern.search(root):
        root = pattern.sub("", root)
    return root


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
    base = safe_filename(_version_root_name(existing.name))[:80] or "recording"
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


def list_upgrade_versions(existing_path: str | os.PathLike[str]) -> list[Path]:
    """List published sibling versions for the same archive item."""
    existing = Path(existing_path).expanduser().resolve(strict=False)
    root = _version_root_name(existing.name)
    prefix = f"{root} [upgrade "
    try:
        entries = [
            path for path in existing.parent.iterdir()
            if path.is_dir() and path.name.startswith(prefix)
        ]
    except OSError:
        return []
    return sorted(entries, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)


def prune_upgrade_versions(
    existing_path: str | os.PathLike[str],
    *,
    keep: int = DEFAULT_VERSION_KEEP,
    active: str | os.PathLike[str] | None = None,
    log_fn=None,
) -> list[Path]:
    """Recycle old published versions and remove their history rows."""
    keep = max(1, min(MAX_VERSION_KEEP, int(keep or DEFAULT_VERSION_KEEP)))
    existing = Path(existing_path).expanduser().resolve(strict=False)
    versions = list_upgrade_versions(existing)
    active_path = Path(active).expanduser().resolve(strict=False) if active else None
    retained = set(versions[:keep])
    if active_path is not None:
        retained.add(active_path)
    try:
        from send2trash import send2trash as _send2trash
    except ImportError:
        if log_fn:
            log_fn(
                "[UPGRADE] send2trash not installed — refusing to delete. "
                "Install with: pip install send2trash"
            )
        return []

    removed: list[Path] = []
    for path in versions:
        if path in retained or path == existing:
            continue
        # The prefix was derived from the exact item's sibling root above;
        # keep the final check local and explicit before any recursive delete.
        if path.parent != existing.parent or not path.name.startswith(
            f"{_version_root_name(existing.name)} [upgrade "
        ):
            continue
        try:
            _send2trash(str(path))
        except Exception as error:
            if log_fn:
                log_fn(f"[UPGRADE] Failed to recycle {path}: {error}")
            continue
        try:
            from . import db as _db
            _db.delete_history_for_paths([path], reason="retention")
        except Exception as error:
            # Recycling remains successful even if a legacy or externally
            # supplied database cannot accept the tombstone transaction.
            if log_fn:
                log_fn(
                    f"[UPGRADE] Could not record tombstone for {path}: {error}"
                )
        removed.append(path)
        if log_fn:
            log_fn(f"[UPGRADE] Recycled: {path.name}")
    return removed


def activate_upgrade_version(
    paths: UpgradePaths,
    *,
    version_keep: int = DEFAULT_VERSION_KEEP,
    log_fn=None,
) -> Path:
    """Atomically publish a validated staged version beside the original."""
    _validate_siblings(paths.existing, paths.staging, paths.final)
    if not paths.existing.is_dir():
        raise UpgradeSafetyError("Known-good recording directory is missing")
    if not paths.staging.is_dir():
        raise UpgradeSafetyError("Validated upgrade staging directory is missing")
    if paths.final.exists():
        raise UpgradeSafetyError("Upgrade version target already exists")
    os.replace(paths.staging, paths.final)
    prune_upgrade_versions(
        paths.existing, keep=version_keep, active=paths.final, log_fn=log_fn,
    )
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
