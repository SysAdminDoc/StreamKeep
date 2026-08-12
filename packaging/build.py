"""Build the Windows release (onedir by default) with a fixed SQLite runtime.

Onedir is the shipped shape (V35): the legacy one-file executable
re-extracted its full ~500 MB payload to a temp directory on every launch,
which made cold start slow, maximised the unsigned-binary AV surface, and
let two simultaneous launches race the same ``_MEIxxxx`` directory.

``--installer`` additionally produces an **unsigned** Inno Setup installer.
No signing step exists anywhere in this path by policy.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlite_runtime import acquire
from versioning import stamp_versions


ROOT = Path(__file__).resolve().parents[1]
RELEASE_PYTHON = (3, 14)
MIN_RELEASE_PYTHON = (3, 14, 6)


def _wal_reset_is_fixed(version):
    version = tuple(version)
    return (
        version >= (3, 51, 3)
        or version >= (3, 50, 7) and version[:2] == (3, 50)
        or version >= (3, 44, 6) and version[:2] == (3, 44)
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--noconfirm", action="store_true")
    parser.add_argument("--sqlite-dll", type=Path)
    parser.add_argument("--dist-path", type=Path)
    parser.add_argument("--work-path", type=Path)
    parser.add_argument(
        "--onefile", action="store_true",
        help="Build the legacy single-file executable instead of a onedir tree",
    )
    parser.add_argument(
        "--installer", action="store_true",
        help="Also build an unsigned Inno Setup installer from the onedir tree",
    )
    args = parser.parse_args(argv)

    current_python = tuple(sys.version_info[:3])
    if current_python[:2] != RELEASE_PYTHON or current_python < MIN_RELEASE_PYTHON:
        parser.error(
            "release artifacts require Python 3.14.6 or newer within the 3.14 line; "
            f"running Python {'.'.join(str(part) for part in current_python)}"
        )

    stamp_versions(ROOT)

    translation_code = subprocess.call(
        [
            sys.executable,
            "-m",
            "streamkeep.i18n.compile_translations",
            "--check",
        ],
        cwd=ROOT,
    )
    if translation_code != 0:
        return translation_code

    environment = os.environ.copy()
    sqlite_dll = args.sqlite_dll
    if sqlite_dll is None and not _wal_reset_is_fixed(sqlite3.sqlite_version_info):
        sqlite_dll = acquire(ROOT / "work" / "sqlite-runtime" / "3.53.3")
    if sqlite_dll is not None:
        environment["STREAMKEEP_SQLITE_DLL"] = str(sqlite_dll.resolve())
    if args.onefile:
        environment["STREAMKEEP_ONEFILE"] = "1"
    else:
        environment.pop("STREAMKEEP_ONEFILE", None)

    command = [sys.executable, "-m", "PyInstaller"]
    if args.clean:
        command.append("--clean")
    if args.noconfirm:
        command.append("--noconfirm")
    if args.dist_path:
        command.extend(["--distpath", str(args.dist_path.resolve())])
    if args.work_path:
        command.extend(["--workpath", str(args.work_path.resolve())])
    command.append("StreamKeep.spec")
    code = subprocess.call(command, cwd=ROOT, env=environment)
    if code != 0 or not args.installer:
        return code
    if args.onefile:
        print("--installer requires the onedir build; skipping.")
        return 1
    dist_root = (args.dist_path or ROOT / "dist").resolve()
    return build_installer(dist_root / "StreamKeep", dist_root)


def find_iscc():
    """Locate the Inno Setup compiler, or return None."""
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    for candidate in (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    ):
        for version in ("6", "5"):
            probe = candidate / f"Inno Setup {version}" / "ISCC.exe"
            if probe.is_file():
                return probe
    return None


def build_installer(source_dir, output_dir):
    """Compile the UNSIGNED installer from a built onedir tree.

    There is no SignTool invocation here and there must never be one: releases
    ship unsigned and are verified by published hash.
    """
    source_dir = Path(source_dir).resolve()
    if not (source_dir / "StreamKeep.exe").is_file():
        print(f"No onedir build found at {source_dir}")
        return 1
    iscc = find_iscc()
    if iscc is None:
        print(
            "Inno Setup is not installed; skipping the installer. Install it "
            "with 'winget install JRSoftware.InnoSetup' to produce one."
        )
        return 1

    sys.path.insert(0, str(ROOT))
    from streamkeep import VERSION

    script = ROOT / "packaging" / "installer" / "streamkeep.iss"
    command = [
        str(iscc),
        f"/DAppVersion={VERSION}",
        f"/DSourceDir={source_dir}",
        f"/DOutputDir={Path(output_dir).resolve()}",
        str(script),
    ]
    code = subprocess.call(command, cwd=ROOT)
    if code == 0:
        artifact = Path(output_dir) / f"StreamKeep-{VERSION}-setup.exe"
        print(f"Unsigned installer: {artifact}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
