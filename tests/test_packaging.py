import re
from pathlib import Path


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
