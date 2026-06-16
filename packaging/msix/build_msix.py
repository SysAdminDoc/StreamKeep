#!/usr/bin/env python3
"""Build an MSIX package from the PyInstaller output.

Usage:
    1. Build with PyInstaller first:  pyinstaller --onedir --name StreamKeep ...
    2. Run: python packaging/msix/build_msix.py dist/StreamKeep

Produces: dist/StreamKeep.msix

Requires:
    - Windows 10 SDK (makeappx.exe in PATH or at default install location)
    - Pillow (for icon generation)
    - signtool.exe for signing (optional)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
MANIFEST = SCRIPT_DIR / "AppxManifest.xml"
ICON_SIZES = {
    "icon-44.png": 44,
    "icon-71.png": 71,
    "icon-150.png": 150,
    "icon-310x150.png": (310, 150),
}


def _find_makeappx():
    for candidate in ("makeappx", "makeappx.exe"):
        if shutil.which(candidate):
            return candidate
    sdk_paths = [
        r"C:\Program Files (x86)\Windows Kits\10\bin",
        r"C:\Program Files\Windows Kits\10\bin",
    ]
    for sdk in sdk_paths:
        if not os.path.isdir(sdk):
            continue
        for ver in sorted(os.listdir(sdk), reverse=True):
            candidate = os.path.join(sdk, ver, "x64", "makeappx.exe")
            if os.path.isfile(candidate):
                return candidate
    return None


def _generate_icons(assets_dir, source_icon):
    from PIL import Image
    src = Image.open(source_icon)
    for name, size in ICON_SIZES.items():
        if isinstance(size, tuple):
            w, h = size
        else:
            w, h = size, size
        resized = src.resize((w, h), Image.LANCZOS)
        resized.save(assets_dir / name, "PNG")
        print(f"  Generated {name} ({w}x{h})")


def main():
    if len(sys.argv) < 2:
        print("Usage: python build_msix.py <pyinstaller-dist-dir>")
        sys.exit(1)

    dist_dir = Path(sys.argv[1]).resolve()
    if not dist_dir.is_dir():
        print(f"ERROR: {dist_dir} is not a directory")
        sys.exit(1)

    makeappx = _find_makeappx()
    if not makeappx:
        print("ERROR: makeappx.exe not found. Install the Windows 10 SDK.")
        sys.exit(1)

    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    source_icon = dist_dir.parent.parent / "assets" / "icon.ico"
    if not source_icon.exists():
        source_icon = dist_dir.parent.parent / "assets" / "icon.png"
    if source_icon.exists():
        _generate_icons(assets_dir, source_icon)
    else:
        print("WARNING: No source icon found — MSIX will lack icons")

    shutil.copy2(MANIFEST, dist_dir / "AppxManifest.xml")
    print(f"  Copied AppxManifest.xml")

    output = dist_dir.parent / "StreamKeep.msix"
    cmd = [makeappx, "pack", "/d", str(dist_dir), "/p", str(output), "/o"]
    print(f"  Running: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        print(f"  SUCCESS: {output}")
        print(f"  Sign with: signtool sign /a /fd SHA256 {output}")
    else:
        print(f"  FAILED: {r.stderr}")
        sys.exit(1)


if __name__ == "__main__":
    main()
