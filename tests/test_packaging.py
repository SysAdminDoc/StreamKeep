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
        # Hand-authored, because the stamper must not touch the release list:
        # rewriting the newest entry's version leaves the description that
        # belonged to it and erases the release it described.
        "packaging/flatpak/com.github.SysAdminDoc.StreamKeep.metainfo.xml": (
            '<releases>\n'
            '  <release version="5.2.1" date="2026-01-02">'
            '<description><p>The new one.</p></description></release>\n'
            '  <release version="0.0.0" date="2026-01-01">'
            '<description><p>The old one.</p></description></release>\n'
            '</releases>\n'
        ),
        "ROADMAP.md": "- Current package version: v0.0.0.\n",
        "packaging/winget/SysAdminDoc.StreamKeep.yaml": (
            "PackageVersion: 0.0.0\n"
            "Installers:\n"
            "  - InstallerUrl: https://github.com/SysAdminDoc/StreamKeep/"
            "releases/download/v0.0.0/StreamKeep-0.0.0-setup.exe\n"
        ),
    }
    for relative, source in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    assert len(stamp_versions(tmp_path)) == 3
    assert version_drift(tmp_path) == []
    assert "version-5.2.1-blue" in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "v5.2.1." in (tmp_path / "ROADMAP.md").read_text(encoding="utf-8")
    # Stamping left the release list exactly as authored, older entry included.
    metainfo = (
        tmp_path / "packaging/flatpak/com.github.SysAdminDoc.StreamKeep.metainfo.xml"
    ).read_text(encoding="utf-8")
    assert '<release version="5.2.1"' in metainfo
    assert '<release version="0.0.0"' in metainfo, "history must survive a bump"
    assert "The old one." in metainfo
    winget = (
        tmp_path / "packaging/winget/SysAdminDoc.StreamKeep.yaml"
    ).read_text(encoding="utf-8")
    assert "PackageVersion: 5.2.1" in winget
    assert "/v5.2.1/StreamKeep-5.2.1-setup.exe" in winget
    assert stamp_versions(tmp_path) == []

    # Drift must *report* a release list that no longer leads with VERSION,
    # rather than silently rewriting it into agreement.
    metainfo_path = (
        tmp_path / "packaging/flatpak/com.github.SysAdminDoc.StreamKeep.metainfo.xml"
    )
    metainfo_path.write_text(
        metainfo.replace('version="5.2.1"', 'version="5.2.0"', 1), encoding="utf-8",
    )
    assert any("metainfo" in item for item in version_drift(tmp_path))


def test_a_bump_that_erases_the_previous_release_entry_is_reported(tmp_path):
    """The defect this guard exists for, reproduced.

    Stamping the release list with a regex used to rewrite the newest
    ``<release>`` version in place. The description that belonged to it stayed,
    so the entry began claiming the previous release's work and that release
    vanished from the AppStream history. A drift check that performed the same
    rewrite called the result "in sync", so nothing ever reported it — v4.51.0
    was lost exactly this way.
    """
    from versioning import metainfo_release_problem

    (tmp_path / "streamkeep").mkdir()
    (tmp_path / "streamkeep/__init__.py").write_text(
        'VERSION = "5.2.1"\n', encoding="utf-8",
    )
    metainfo = tmp_path / "packaging/flatpak/com.github.SysAdminDoc.StreamKeep.metainfo.xml"
    metainfo.parent.mkdir(parents=True)

    # The newest entry still names the previous release: the bump was applied
    # everywhere else but no release block was authored.
    metainfo.write_text(
        '<releases>\n  <release version="5.2.0" date="2026-01-01">'
        '<description><p>Older.</p></description></release>\n</releases>\n',
        encoding="utf-8",
    )
    problem = metainfo_release_problem(tmp_path)
    assert "newest <release> is 5.2.0" in problem

    # Two entries claiming one version is the in-place rewrite's other outcome.
    metainfo.write_text(
        '<releases>\n  <release version="5.2.1" date="2026-01-02"/>\n'
        '  <release version="5.2.1" date="2026-01-01"/>\n</releases>\n',
        encoding="utf-8",
    )
    assert "duplicate" in metainfo_release_problem(tmp_path)

    # And a correctly authored list is accepted.
    metainfo.write_text(
        '<releases>\n  <release version="5.2.1" date="2026-01-02">'
        '<description><p>New.</p></description></release>\n'
        '  <release version="5.2.0" date="2026-01-01">'
        '<description><p>Older.</p></description></release>\n</releases>\n',
        encoding="utf-8",
    )
    assert metainfo_release_problem(tmp_path) == ""


def test_pyinstaller_spec_includes_release_assets():
    spec = (ROOT / "StreamKeep.spec").read_text(encoding="utf-8")
    compact = spec.replace(" ", "")
    assert "datas=datas" in compact
    assert "collect_submodules('paramiko')" in spec
    for required in (
        "assets",
        "browser-extension",
        "streamkeep/i18n",
        "packaging",
        "runtime_hook_mp.py",
    ):
        assert required in spec


def test_flatpak_manifest_uses_locked_linux_modules_and_current_base():
    manifest = (
        ROOT / "packaging" / "flatpak" / "com.github.SysAdminDoc.StreamKeep.yml"
    ).read_text(encoding="utf-8")
    assert "PLACEHOLDER" not in manifest
    assert re.search(r"sha256:\s+[0-9a-f]{64}", manifest)
    assert "install -Dm644 icon.png /app/bin/icon.png" in manifest
    assert "hicolor/512x512/apps/com.github.SysAdminDoc.StreamKeep.png" in manifest
    assert "runtime-version: '6.10'" in manifest
    desktop = (
        ROOT / "packaging" / "flatpak" /
        "com.github.SysAdminDoc.StreamKeep.desktop"
    ).read_text(encoding="utf-8")
    assert "Exec=streamkeep %u" in desktop
    assert "MimeType=x-scheme-handler/streamkeep;" in desktop
    assert "base-version: '6.10'" in manifest
    assert "--filesystem=home" not in manifest
    assert "--talk-name=org.freedesktop.portal.Desktop" in manifest
    assert "xdg-desktop-portal >= 1.22.1" in manifest
    assert "python3-requirements.json" in manifest
    assert "pip3 install --no-index --find-links=." not in manifest
    assert "commit: 0480cb05fa188d37ae87e8f4fd8f1aea3711f7ee" in manifest
    assert "commit: f21b135c3414ade4e22305d008bf07d23d0595fb" in manifest

    from locked_requirements import locked_packages, validate_hashed_lock
    linux_lock = ROOT / "packaging" / "flatpak" / "requirements.lock"
    packages = dict(locked_packages(linux_lock))
    assert validate_hashed_lock(linux_lock) == []
    assert "secretstorage" in packages
    assert "pywin32-ctypes" not in packages
    sources = json.loads((
        ROOT / "packaging" / "flatpak" / "python3-requirements.json"
    ).read_text(encoding="utf-8"))
    assert sources["name"] == "python3-requirements"
    assert len(sources["modules"]) == len(packages)


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
        "--sequence",
    ):
        assert required in script


def test_msix_lane_is_not_shipped():
    assert not (ROOT / "packaging" / "msix").exists()
    for path in (
        ROOT / "packaging" / "versioning.py",
        ROOT / "packaging" / "update_manifest.py",
        ROOT / "streamkeep" / "updater.py",
        ROOT / "streamkeep" / "update_security.py",
    ):
        assert "msix" not in path.read_text(encoding="utf-8").casefold()


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

    assert manifest["minimum_chrome_version"] == "144"
    assert set(manifest["host_permissions"]) == {"http://127.0.0.1/*"}
    assert "<all_urls>" not in manifest["host_permissions"]
    assert "tabs" not in manifest["permissions"]
    assert "webRequest" in manifest["permissions"]
    assert "127.0.0.1" not in popup
    assert "fetch(" not in popup
    assert 'scopes: ["status", "queue"]' in popup
    for source in (popup, background):
        assert "chrome.storage.session.get" in source
    assert "X-StreamKeep-Timestamp" in background
    assert "X-StreamKeep-Nonce" in background
    assert "onBeforeSendHeaders" in background
    assert "activeOriginPattern" in background
    assert '{ urls: ["<all_urls>"]' not in background
    assert "fetch(" in background
    assert "request_headers" in popup


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


def test_release_locks_are_exact_hashed_and_build_lock_is_runtime_superset():
    from locked_requirements import locked_packages, validate_hashed_lock

    runtime_lock = ROOT / "requirements.lock"
    build_lock = ROOT / "requirements-build.lock"
    assert validate_hashed_lock(runtime_lock) == []
    assert validate_hashed_lock(build_lock) == []
    runtime = dict(locked_packages(runtime_lock))
    build = dict(locked_packages(build_lock))
    assert len(runtime) >= 20
    assert all(build.get(name) == version for name, version in runtime.items())
    assert build["pyinstaller"] == "6.21.0"


def test_source_requirement_floors_match_the_runtime_lock():
    from locked_requirements import source_requirement_floors, validate_source_floors

    requirements = ROOT / "requirements.txt"
    lock = ROOT / "requirements.lock"
    floors = source_requirement_floors(requirements)
    assert validate_source_floors(requirements, lock) == []
    assert floors["cryptography"] == "50.0.0"
    assert floors["paramiko"] == "5.0.0"
    assert floors["pyqt6-qt6"] == "6.11.1"
    assert floors["urllib3"] == "2.7.0"


def test_source_requirement_floor_checker_rejects_lock_drift(tmp_path):
    from locked_requirements import validate_source_floors

    requirements = tmp_path / "requirements.txt"
    lock = tmp_path / "requirements.lock"
    requirements.write_text("urllib3>=2.8.0\n", encoding="utf-8")
    lock.write_text(
        "urllib3==2.7.0 \\\n    --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )

    errors = validate_source_floors(requirements, lock)
    assert errors == ["urllib3 source floor 2.8.0 exceeds locked version 2.7.0"]


def test_lock_driven_sbom_and_license_inventory_are_deterministic(
        tmp_path, monkeypatch):
    from sbom import generate_sbom

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1704067200")
    lock = tmp_path / "runtime.lock"
    lock.write_text(
        "Pillow==12.3.0 \\\n+    --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    sbom = tmp_path / "sbom.json"
    licenses = tmp_path / "licenses.json"
    ok, _ = generate_sbom(sbom, lock_path=lock, license_output=licenses)
    assert ok
    first_sbom = sbom.read_bytes()
    first_licenses = licenses.read_bytes()
    ok, _ = generate_sbom(sbom, lock_path=lock, license_output=licenses)
    assert ok
    assert sbom.read_bytes() == first_sbom
    assert licenses.read_bytes() == first_licenses
    data = json.loads(first_sbom)
    assert data["metadata"]["timestamp"] == "2024-01-01T00:00:00+00:00"
    components = data["components"]
    assert {
        "name": "pillow",
        "purl": "pkg:pypi/pillow@12.3.0",
        "type": "library",
        "version": "12.3.0",
    } in components
    optional = {
        component["name"]: component
        for component in components
        if component.get("scope") == "optional"
    }
    assert set(optional) == {"boto3", "libmpv", "python-mpv"}
    for component in optional.values():
        properties = {row["name"]: row["value"] for row in component["properties"]}
        assert properties["streamkeep:lock-status"] == "out-of-lock"
        assert properties["streamkeep:requirement"].startswith(component["name"] + ">=")
    inventory = json.loads(first_licenses)
    assert inventory["generated_from"] == "runtime.lock"
    assert inventory["packages"][0]["version"] == "12.3.0"


def test_reproducible_builder_gates_double_build_inventory_and_smoke():
    source = (ROOT / "packaging" / "reproducible_build.py").read_text(
        encoding="utf-8"
    )
    for required in (
        "--require-hashes",
        "--verify-reproducible",
        "digest_a != digest_b",
        "sbom.cdx.json",
        "third-party-licenses.json",
        "artifact_smoke.py",
        "release-manifest.json",
        "SOURCE_DATE_EPOCH",
    ):
        assert required in source


def test_release_build_inputs_name_the_shipped_python_target():
    build_source = (ROOT / "packaging" / "build.py").read_text(encoding="utf-8")
    reproducible_source = (
        ROOT / "packaging" / "reproducible_build.py"
    ).read_text(encoding="utf-8")
    spec_source = (ROOT / "StreamKeep.spec").read_text(encoding="utf-8")
    # Derived from the gate's declared floor so a bump does not need this test
    # edited, and so a half-landed bump fails instead of passing (V193).
    import sys as _sys
    if str(ROOT / "packaging") not in _sys.path:
        _sys.path.insert(0, str(ROOT / "packaging"))
    from release_gate import MIN_RELEASE_PYTHON

    tuple_literal = ", ".join(str(part) for part in MIN_RELEASE_PYTHON)
    dotted = ".".join(str(part) for part in MIN_RELEASE_PYTHON)
    for source in (build_source, reproducible_source, spec_source):
        assert tuple_literal in source
        assert dotted in source
