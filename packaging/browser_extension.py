"""Package the browser companion extension as a deterministic ZIP artifact.

Usage:
    python packaging/browser_extension.py [--output path.zip]

Produces a ZIP with sorted entries, no timestamps, containing all required
MV3 files: manifest.json, popup.html, popup.js, background.js, and icons.
"""

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT_DIR = ROOT / "browser-extension"

REQUIRED_FILES = [
    "manifest.json",
    "popup.html",
    "popup.js",
    "background.js",
]
ICON_SIZES = [16, 32, 48, 128]
REQUIRED_PERMISSIONS = {"activeTab", "storage", "contextMenus"}
MAX_HOST_PERMISSIONS = {"http://127.0.0.1/*"}


def validate_extension(ext_dir=None):
    """Validate the extension source. Returns (ok, errors)."""
    ext_dir = Path(ext_dir or EXT_DIR)
    errors = []

    manifest_path = ext_dir / "manifest.json"
    if not manifest_path.is_file():
        return False, ["manifest.json not found"]

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return False, [f"Invalid manifest.json: {e}"]

    if manifest.get("manifest_version") != 3:
        errors.append(f"Expected MV3, got manifest_version={manifest.get('manifest_version')}")

    version = manifest.get("version", "")
    if not version or not all(p.isdigit() for p in version.split(".")):
        errors.append(f"Invalid version: {version!r}")

    perms = set(manifest.get("permissions", []))
    missing = REQUIRED_PERMISSIONS - perms
    if missing:
        errors.append(f"Missing permissions: {missing}")

    host_perms = set(manifest.get("host_permissions", []))
    extra = host_perms - MAX_HOST_PERMISSIONS
    if extra:
        errors.append(f"Excessive host_permissions: {extra}")

    for fname in REQUIRED_FILES:
        if not (ext_dir / fname).is_file():
            errors.append(f"Missing file: {fname}")

    for size in ICON_SIZES:
        icon_path = ext_dir / "icons" / f"{size}.png"
        if not icon_path.is_file():
            errors.append(f"Missing icon: icons/{size}.png")

    return len(errors) == 0, errors


def package_extension(output_path=None, ext_dir=None):
    """Build a deterministic ZIP. Returns (ok, path_or_error)."""
    ext_dir = Path(ext_dir or EXT_DIR)
    ok, errors = validate_extension(ext_dir)
    if not ok:
        return False, "; ".join(errors)

    if not output_path:
        output_path = ROOT / "dist" / "streamkeep-companion.zip"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    files = []
    for root, dirs, fnames in os.walk(str(ext_dir)):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for fname in sorted(fnames):
            if fname.startswith(".") or fname == "README.md":
                continue
            full = Path(root) / fname
            rel = full.relative_to(ext_dir)
            files.append((str(rel).replace("\\", "/"), full))

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, full_path in sorted(files):
            info = zipfile.ZipInfo(arcname, date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(full_path, "rb") as f:
                zf.writestr(info, f.read())

    return True, str(output_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Package StreamKeep browser companion")
    parser.add_argument("-o", "--output", default="", help="Output ZIP path")
    args = parser.parse_args()
    ok, result = package_extension(args.output or None)
    if ok:
        print(f"OK: {result}")
        size_kb = os.path.getsize(result) / 1024
        print(f"Size: {size_kb:.0f} KB")
    else:
        print(f"FAILED: {result}", file=sys.stderr)
        sys.exit(1)
