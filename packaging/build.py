"""Build the one-file Windows release with a fixed SQLite runtime."""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlite_runtime import acquire


ROOT = Path(__file__).resolve().parents[1]


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
    args = parser.parse_args(argv)

    environment = os.environ.copy()
    sqlite_dll = args.sqlite_dll
    if sqlite_dll is None and not _wal_reset_is_fixed(sqlite3.sqlite_version_info):
        sqlite_dll = acquire(ROOT / "work" / "sqlite-runtime" / "3.53.3")
    if sqlite_dll is not None:
        environment["STREAMKEEP_SQLITE_DLL"] = str(sqlite_dll.resolve())

    command = [sys.executable, "-m", "PyInstaller"]
    if args.clean:
        command.append("--clean")
    if args.noconfirm:
        command.append("--noconfirm")
    command.append("StreamKeep.spec")
    return subprocess.call(command, cwd=ROOT, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
