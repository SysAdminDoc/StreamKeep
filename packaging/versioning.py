"""Read and propagate StreamKeep's single application version source."""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_version(root: Path = ROOT) -> str:
    """Return VERSION from streamkeep/__init__.py without importing the app."""
    source_path = root / "streamkeep" / "__init__.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            version = node.value.value
            if re.fullmatch(r"\d+\.\d+\.\d+", version):
                return version
            raise ValueError(f"VERSION must be X.Y.Z, got {version!r}")
    raise ValueError(f"VERSION assignment not found in {source_path}")


def _targets(version: str, root: Path) -> tuple[tuple[Path, re.Pattern[str], str], ...]:
    return (
        (
            root / "README.md",
            re.compile(r"(?m)(!\[Version\]\(https://img\.shields\.io/badge/version-)[0-9.]+(-blue\))"),
            rf"\g<1>{version}\g<2>",
        ),
        (
            root / "packaging" / "msix" / "AppxManifest.xml",
            re.compile(r"(?s)(<Identity\b.*?\bVersion=\")[0-9.]+(\")"),
            rf"\g<1>{version}.0\g<2>",
        ),
        (
            root / "packaging" / "flatpak" / "com.github.SysAdminDoc.StreamKeep.metainfo.xml",
            re.compile(r"(<release\s+version=\")[0-9.]+(\")"),
            rf"\g<1>{version}\g<2>",
        ),
        (
            root / "ROADMAP.md",
            re.compile(r"(?m)(^- Current package version: v)[0-9]+(?:\.[0-9]+){2}(\.$)"),
            rf"\g<1>{version}\g<2>",
        ),
    )


def version_drift(root: Path = ROOT) -> list[str]:
    """Return target files whose embedded version differs from VERSION."""
    version = read_version(root)
    drift: list[str] = []
    for path, pattern, replacement in _targets(version, root):
        source = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(replacement, source, count=1)
        if count != 1:
            drift.append(f"{path.relative_to(root)}: version marker missing or ambiguous")
        elif updated != source:
            drift.append(f"{path.relative_to(root)}: does not match {version}")
    return drift


def stamp_versions(root: Path = ROOT) -> list[Path]:
    """Stamp build/package metadata from VERSION and return changed paths."""
    version = read_version(root)
    changed: list[Path] = []
    for path, pattern, replacement in _targets(version, root):
        source = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(replacement, source, count=1)
        if count != 1:
            raise ValueError(f"Expected one version marker in {path}, found {count}")
        if updated != source:
            path.write_text(updated, encoding="utf-8", newline="\n")
            changed.append(path)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stamp", action="store_true", help="update derived version strings")
    args = parser.parse_args(argv)

    if args.stamp:
        changed = stamp_versions()
        for path in changed:
            print(f"Stamped {path.relative_to(ROOT)}")

    drift = version_drift()
    if drift:
        print("Version drift detected:")
        for problem in drift:
            print(f"  - {problem}")
        return 1
    print(f"Version metadata matches {read_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
