import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from streamkeep import javascript_runtime as runtime


def _archive(tmp_path, name="deno.zip", member="deno.exe", payload=b"deno"):
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(member, payload)
    return archive


def _pin_archive(monkeypatch, archive, target="x86_64-pc-windows-msvc"):
    monkeypatch.setattr(runtime, "host_target", lambda *_args: target)
    monkeypatch.setitem(runtime._ASSETS[target], "sha256", hashlib.sha256(
        archive.read_bytes()
    ).hexdigest())


def test_host_target_normalizes_supported_platforms():
    assert runtime.host_target("Windows", "AMD64") == "x86_64-pc-windows-msvc"
    assert runtime.host_target("Linux", "aarch64") == "aarch64-unknown-linux-gnu"
    with pytest.raises(runtime.DenoRuntimeError):
        runtime.host_target("Windows", "arm64")


def test_runtime_preference_is_strict_and_does_not_import_config(tmp_path):
    assert runtime.read_runtime_preference({}) == "path"
    assert runtime.read_runtime_preference({"javascript_runtime_preference": "managed"}) == "managed"
    assert runtime.read_runtime_preference({"javascript_runtime_preference": "anything-else"}) == "path"

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"javascript_runtime_preference": "managed"}), encoding="utf-8")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runtime.paths, "CONFIG_FILE", config)
        assert runtime.read_runtime_preference() == "managed"


def test_offline_install_verifies_hash_before_extracting(tmp_path, monkeypatch):
    archive = _archive(tmp_path)
    _pin_archive(monkeypatch, archive)
    monkeypatch.setitem(runtime._ASSETS["x86_64-pc-windows-msvc"], "sha256", "0" * 64)
    config_dir = tmp_path / "config"

    extracted = []
    monkeypatch.setattr(runtime, "_extract_executable", lambda *args: extracted.append(args))
    with pytest.raises(runtime.DenoRuntimeError, match="SHA-256"):
        runtime.install_managed_deno(archive, config_dir=config_dir)

    assert extracted == []
    assert not (config_dir / "runtimes" / runtime.DENO_VERSION).exists()


def test_offline_install_writes_verified_metadata_and_can_be_removed(tmp_path, monkeypatch):
    archive = _archive(tmp_path)
    _pin_archive(monkeypatch, archive)
    monkeypatch.setattr(runtime, "_probe_executable", lambda _path: runtime.DENO_VERSION)
    config_dir = tmp_path / "config"

    installed = runtime.install_managed_deno(archive, config_dir=config_dir)

    assert installed["source"] == "offline-archive"
    assert installed["provenance"] == "offline-archive"
    assert Path(installed["path"]).is_file()
    info = runtime.get_managed_deno_info(config_dir=config_dir)
    assert info["available"] is True
    assert info["version"] == runtime.DENO_VERSION
    metadata_path = Path(installed["path"]).with_name("runtime.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["archive_sha256"] == installed["sha256"]
    assert metadata["url"] == ""

    assert runtime.remove_managed_deno(config_dir=config_dir) is True
    assert runtime.remove_managed_deno(config_dir=config_dir) is False


def test_offline_install_rejects_zip_traversal(tmp_path, monkeypatch):
    archive = _archive(tmp_path, member="../deno.exe")
    _pin_archive(monkeypatch, archive)

    with pytest.raises(runtime.DenoRuntimeError, match="expected deno.exe"):
        runtime.install_managed_deno(archive, config_dir=tmp_path / "config")
