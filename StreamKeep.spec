# -*- mode: python ; coding: utf-8 -*-
import ctypes
import os
import sqlite3
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


ROOT = Path(SPECPATH).resolve()


def wal_reset_is_fixed(version):
    version = tuple(version)
    return (
        version >= (3, 51, 3)
        or version >= (3, 50, 7) and version[:2] == (3, 50)
        or version >= (3, 44, 6) and version[:2] == (3, 44)
    )


sqlite_override = os.environ.get('STREAMKEEP_SQLITE_DLL', '').strip()
if not wal_reset_is_fixed(sqlite3.sqlite_version_info) and not sqlite_override:
    raise SystemExit(
        'Frozen builds require fixed SQLite. Run python packaging/build.py '
        '--clean --noconfirm or set STREAMKEEP_SQLITE_DLL.'
    )
if sqlite_override:
    sqlite_override = str(Path(sqlite_override).resolve())
    library = ctypes.WinDLL(sqlite_override)
    library.sqlite3_libversion.restype = ctypes.c_char_p
    sqlite_override_version = library.sqlite3_libversion().decode('ascii')
    if not wal_reset_is_fixed(tuple(int(part) for part in sqlite_override_version.split('.'))):
        raise SystemExit(
            f'STREAMKEEP_SQLITE_DLL {sqlite_override_version} lacks the WAL-reset fix.'
        )


def collect_tree(relative, dest):
    root = ROOT / relative
    if not root.exists():
        return []
    rows = []
    for path in root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        target_dir = Path(dest) / path.relative_to(root).parent
        rows.append((str(path), str(target_dir)))
    return rows


hiddenimports = []
hiddenimports += collect_submodules('streamkeep')
hiddenimports += collect_submodules('yt_dlp')
hiddenimports += collect_submodules('yt_dlp_ejs')

datas = []
for top_level in ('LICENSE', 'README.md', 'requirements.txt', 'icon.ico', 'icon.png'):
    path = ROOT / top_level
    if path.is_file():
        datas.append((str(path), '.'))
datas += collect_tree('assets', 'assets')
datas += collect_tree('browser-extension', 'browser-extension')
datas += collect_tree('packaging', 'packaging')
datas += collect_tree('streamkeep/i18n', 'streamkeep/i18n')
datas += collect_data_files('yt_dlp_ejs')
datas += copy_metadata('yt-dlp-ejs')


a = Analysis(
    ['StreamKeep.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / 'packaging' / 'pyinstaller' / 'runtime_hook_mp.py')],
    excludes=[],
    noarchive=False,
    optimize=0,
)
if sqlite_override:
    a.binaries = [
        entry for entry in a.binaries
        if Path(entry[0]).name.lower() != 'sqlite3.dll'
    ]
    a.binaries.append(('sqlite3.dll', sqlite_override, 'BINARY'))
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='StreamKeep',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\icon.ico'],
)
