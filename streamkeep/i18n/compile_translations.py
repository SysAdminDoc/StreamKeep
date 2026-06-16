#!/usr/bin/env python3
"""Compile .ts translation source files into .qm binary files.

Usage:
    python -m streamkeep.i18n.compile_translations

Requires PyQt6 (uses lrelease from the Qt tools bundled with PyQt6).
Falls back to system lrelease6 / lrelease if available.
"""

import os
import subprocess
import sys
from pathlib import Path

_DIR = Path(__file__).parent


def _find_lrelease():
    """Find the lrelease tool — try PyQt6 bundled, then system."""
    try:
        from PyQt6.QtCore import QLibraryInfo
        qt_bin = QLibraryInfo.path(QLibraryInfo.LibraryPath.BinariesPath)
        for name in ("lrelease", "lrelease6"):
            candidate = os.path.join(qt_bin, name)
            if os.path.isfile(candidate):
                return candidate
            if sys.platform == "win32" and os.path.isfile(candidate + ".exe"):
                return candidate + ".exe"
    except Exception:
        pass
    for name in ("lrelease6", "lrelease"):
        try:
            subprocess.run([name, "-version"], capture_output=True, timeout=5)
            return name
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def compile_all():
    lrelease = _find_lrelease()
    if not lrelease:
        print("ERROR: lrelease not found. Install PyQt6-tools or Qt Linguist.")
        return False
    ts_files = sorted(_DIR.glob("*.ts"))
    if not ts_files:
        print("No .ts files found.")
        return True
    ok = True
    for ts in ts_files:
        qm = ts.with_suffix(".qm")
        print(f"  {ts.name} → {qm.name}")
        r = subprocess.run(
            [lrelease, str(ts), "-qm", str(qm)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            print(f"    FAILED: {r.stderr.strip()}")
            ok = False
    return ok


if __name__ == "__main__":
    sys.exit(0 if compile_all() else 1)
