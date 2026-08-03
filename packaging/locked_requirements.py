"""Parse and validate pip-compile lock files without third-party imports."""

from __future__ import annotations

import re
from pathlib import Path


_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")
_SOURCE_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*>=\s*([0-9][0-9A-Za-z.\-]*)"
)
_HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}(?:\s*\\)?$")
_VERSION = re.compile(r"[0-9]+(?:[.\-][0-9]+)*")


def canonical_name(name):
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def locked_packages(path):
    """Return sorted ``(canonical_name, version)`` pairs from a lock file."""
    path = Path(path)
    rows = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _REQUIREMENT.match(line)
        if match is None:
            continue
        name = canonical_name(match.group(1))
        if name in seen:
            raise ValueError(f"Duplicate locked requirement: {name}")
        seen.add(name)
        rows.append((name, match.group(2)))
    if not rows:
        raise ValueError(f"No locked requirements in {path}")
    return sorted(rows)


def validate_hashed_lock(path):
    """Return errors when a pinned requirement has no SHA-256 artifact hash."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    errors = []
    found = 0
    for index, line in enumerate(lines):
        match = _REQUIREMENT.match(line)
        if match is None:
            continue
        found += 1
        package = canonical_name(match.group(1))
        cursor = index + 1
        has_hash = False
        while cursor < len(lines) and _REQUIREMENT.match(lines[cursor]) is None:
            if _HASH.search(lines[cursor].strip()):
                has_hash = True
            cursor += 1
        if not has_hash:
            errors.append(f"{package} has no SHA-256 hash")
    if not found:
        errors.append("lock contains no exact requirements")
    return errors


def source_requirement_floors(path):
    """Return direct ``requirements.txt`` package floors by canonical name."""
    path = Path(path)
    floors = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = _SOURCE_REQUIREMENT.match(line)
        if match is None:
            continue
        name = canonical_name(match.group(1))
        version = match.group(2)
        previous = floors.get(name)
        if previous is not None and previous != version:
            raise ValueError(
                f"Duplicate source requirement with different floors: {name}"
            )
        floors[name] = version
    return dict(sorted(floors.items()))


def _version_tuple(value):
    match = _VERSION.fullmatch(str(value or "").replace("-", "."))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def validate_source_floors(requirements_path, lock_path):
    """Return errors when direct source floors exceed or evade the lock."""
    locked = dict(locked_packages(lock_path))
    errors = []
    for name, floor in source_requirement_floors(requirements_path).items():
        pinned = locked.get(name)
        if pinned is None:
            errors.append(f"{name} has a source floor but is not pinned in the lock")
            continue
        if not _version_tuple(pinned) >= _version_tuple(floor):
            errors.append(
                f"{name} source floor {floor} exceeds locked version {pinned}"
            )
    return errors
