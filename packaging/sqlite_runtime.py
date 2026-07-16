"""Acquire and verify the pinned fixed SQLite DLL for Windows releases."""

from __future__ import annotations

import ctypes
import hashlib
import os
import urllib.request
import zipfile
from pathlib import Path


SQLITE_VERSION = "3.53.3"
SQLITE_ARCHIVE = "sqlite-dll-win-x64-3530300.zip"
SQLITE_URL = f"https://sqlite.org/2026/{SQLITE_ARCHIVE}"
SQLITE_SHA3_256 = "3a494861ce24d1f330efbc6c3fb58ce4972f2cf8df4e43122246ed987109dc8a"
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024


def _sha3(path):
    digest = hashlib.sha3_256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_library(path):
    """Return the version exported by a SQLite library, or raise."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite runtime not found: {path}")
    loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL
    library = loader(str(path))
    library.sqlite3_libversion.restype = ctypes.c_char_p
    value = library.sqlite3_libversion()
    version = value.decode("ascii", "strict") if value else ""
    if version != SQLITE_VERSION:
        raise RuntimeError(
            f"SQLite runtime {path} reports {version or 'unknown'}, "
            f"expected {SQLITE_VERSION}"
        )
    return version


def acquire(output_dir):
    """Download, SHA3-verify, and extract the pinned official SQLite DLL."""
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / SQLITE_ARCHIVE
    target = output_dir / "sqlite3.dll"
    if target.is_file():
        verify_library(target)
        return target

    if not archive.is_file() or _sha3(archive) != SQLITE_SHA3_256:
        temporary = archive.with_suffix(archive.suffix + ".download")
        request = urllib.request.Request(
            SQLITE_URL,
            headers={"User-Agent": "StreamKeep release builder"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response, \
                    open(temporary, "wb") as handle:
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise RuntimeError("SQLite archive exceeds the size limit")
                    handle.write(chunk)
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)

    digest = _sha3(archive)
    if digest != SQLITE_SHA3_256:
        raise RuntimeError(
            f"SQLite archive SHA3-256 mismatch: got {digest}, "
            f"expected {SQLITE_SHA3_256}"
        )

    with zipfile.ZipFile(archive) as package:
        matches = [
            name for name in package.namelist()
            if Path(name).name.lower() == "sqlite3.dll"
        ]
        if len(matches) != 1:
            raise RuntimeError("Official SQLite archive has no unique sqlite3.dll")
        temporary = target.with_suffix(".dll.extract")
        with package.open(matches[0]) as source, open(temporary, "wb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        os.replace(temporary, target)
    verify_library(target)
    return target
