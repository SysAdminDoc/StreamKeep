import json
import re
import tempfile
import zipfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

ROOT = Path(__file__).resolve().parents[1]


def test_pyinstaller_spec_includes_release_assets():
    spec = (ROOT / "StreamKeep.spec").read_text(encoding="utf-8")
    compact = spec.replace(" ", "")
    assert "datas=datas" in compact
    for required in (
        "assets",
        "browser-extension",
        "streamkeep/i18n",
        "packaging",
        "runtime_hook_mp.py",
    ):
        assert required in spec


def test_flatpak_manifest_uses_real_ffmpeg_hash_and_png_icon():
    manifest = (
        ROOT / "packaging" / "flatpak" / "com.github.SysAdminDoc.StreamKeep.yml"
    ).read_text(encoding="utf-8")
    assert "PLACEHOLDER" not in manifest
    assert re.search(r"sha256:\s+[0-9a-f]{64}", manifest)
    assert "install -Dm644 icon.png" in manifest


def test_msix_builder_supports_configured_signing():
    script = (ROOT / "packaging" / "msix" / "build_msix.py").read_text(
        encoding="utf-8"
    )
    for required in (
        "STREAMKEEP_SIGNTOOL",
        "STREAMKEEP_SIGN_PFX",
        "STREAMKEEP_SIGN_CERT_SUBJECT",
        "STREAMKEEP_SIGN=1",
        "signtool.exe",
    ):
        assert required in script


def test_browser_extension_validates_mv3_manifest():
    from browser_extension import validate_extension
    ok, errors = validate_extension(ROOT / "browser-extension")
    assert ok, f"Extension validation failed: {errors}"


def test_browser_extension_packages_deterministic_zip():
    from browser_extension import package_extension
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "companion.zip"
        ok, result = package_extension(out, ROOT / "browser-extension")
        assert ok, f"Packaging failed: {result}"
        assert out.is_file()
        with zipfile.ZipFile(out, "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            assert "popup.html" in names
            assert "popup.js" in names
            assert "background.js" in names
            assert "icons/128.png" in names
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["manifest_version"] == 3


def test_browser_extension_rejects_missing_asset():
    from browser_extension import validate_extension
    with tempfile.TemporaryDirectory() as tmpdir:
        ext = Path(tmpdir)
        (ext / "manifest.json").write_text(json.dumps({
            "manifest_version": 3, "version": "1.0.0",
            "permissions": ["activeTab", "storage", "contextMenus"],
            "host_permissions": ["http://127.0.0.1/*"],
        }), encoding="utf-8")
        ok, errors = validate_extension(ext)
        assert not ok
        assert any("Missing file" in e for e in errors)
