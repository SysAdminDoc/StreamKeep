# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH).resolve()


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
