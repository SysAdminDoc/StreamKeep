import json
import re
import tempfile
import zipfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_consistent_across_claimed_metadata():
    from versioning import read_version, version_drift

    assert re.fullmatch(r"\d+\.\d+\.\d+", read_version(ROOT))
    assert version_drift(ROOT) == []


def test_version_stamper_derives_all_metadata_from_package_version(tmp_path):
    from versioning import stamp_versions, version_drift

    files = {
        "streamkeep/__init__.py": 'VERSION = "5.2.1"\n',
        "README.md": "![Version](https://img.shields.io/badge/version-0.0.0-blue)\n",
        "packaging/msix/AppxManifest.xml": '<Identity Name="StreamKeep" Version="0.0.0.0" />\n',
        "packaging/flatpak/com.github.SysAdminDoc.StreamKeep.metainfo.xml": (
            '<releases><release version="0.0.0" date="2026-01-01" /></releases>\n'
        ),
        "ROADMAP.md": "- Current package version: v0.0.0.\n",
    }
    for relative, source in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    assert len(stamp_versions(tmp_path)) == 4
    assert version_drift(tmp_path) == []
    assert "version-5.2.1-blue" in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert 'Version="5.2.1.0"' in (
        tmp_path / "packaging/msix/AppxManifest.xml"
    ).read_text(encoding="utf-8")
    assert '<release version="5.2.1"' in (
        tmp_path / "packaging/flatpak/com.github.SysAdminDoc.StreamKeep.metainfo.xml"
    ).read_text(encoding="utf-8")
    assert "v5.2.1." in (tmp_path / "ROADMAP.md").read_text(encoding="utf-8")
    assert stamp_versions(tmp_path) == []


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
        "packaged_exe",
        '_sign_windows_artifact(packaged_exe, "packaged executable")',
    ):
        assert required in script


def test_update_manifest_binds_assets_and_metadata_to_one_publisher_key():
    script = (ROOT / "packaging" / "update_manifest.py").read_text(
        encoding="utf-8"
    )
    for required in (
        "STREAMKEEP_SIGN_PFX",
        "require_authenticode",
        "sign_manifest_bytes",
        "certificate_sha256",
        "StreamKeep.exe",
        "StreamKeep.msix",
        "--sequence",
    ):
        assert required in script


def test_launcher_marks_update_healthy_only_after_full_window_initialization():
    launcher = (ROOT / "StreamKeep.py").read_text(encoding="utf-8")
    construct = launcher.index("win = StreamKeep()")
    show = launcher.index("win.show()", construct)
    mark = launcher.index("mark_transaction_healthy", show)
    event_loop = launcher.index("QTimer.singleShot(0, _finish_startup)", mark)
    assert construct < show < mark < event_loop


def test_browser_extension_validates_mv3_manifest():
    from browser_extension import validate_extension
    ok, errors = validate_extension(ROOT / "browser-extension")
    assert ok, f"Extension validation failed: {errors}"


def test_browser_extension_keeps_access_session_only_and_replay_protected():
    extension = ROOT / "browser-extension"
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    popup = (extension / "popup.js").read_text(encoding="utf-8")
    background = (extension / "background.js").read_text(encoding="utf-8")

    assert manifest["host_permissions"] == ["http://127.0.0.1/*"]
    assert 'chrome.storage.local.set({ port })' in popup
    assert 'chrome.storage.session.set({ token: result.token })' in popup
    assert 'chrome.storage.local.set({ port, token' not in popup
    assert 'scopes: ["status", "queue"]' in popup
    for source in (popup, background):
        assert "chrome.storage.session.get" in source
        assert "X-StreamKeep-Timestamp" in source
        assert "X-StreamKeep-Nonce" in source


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


def test_sbom_generates_cyclonedx_with_components():
    from sbom import generate_sbom
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "sbom.cdx.json"
        ok, result = generate_sbom(out)
        assert ok, f"SBOM generation failed: {result}"
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["bomFormat"] == "CycloneDX"
        assert data["specVersion"] == "1.5"
        assert len(data["components"]) > 0
        purls = [c["purl"] for c in data["components"]]
        assert any("pyqt6" in p for p in purls)
