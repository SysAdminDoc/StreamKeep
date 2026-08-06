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
    # Deno publishes a Windows arm64 build from v2.9.0 onward.
    assert runtime.host_target("Windows", "ARM64") == "aarch64-pc-windows-msvc"
    assert runtime.host_target("Linux", "aarch64") == "aarch64-unknown-linux-gnu"
    assert runtime.host_target("Darwin", "arm64") == "aarch64-apple-darwin"
    with pytest.raises(runtime.DenoRuntimeError):
        runtime.host_target("Linux", "riscv64")
    with pytest.raises(runtime.DenoRuntimeError):
        runtime.host_target("FreeBSD", "x86_64")


def test_every_pinned_target_is_reachable_from_a_host_pair():
    """A target nobody can resolve is a pin that will never be verified."""
    resolved = {
        runtime.host_target("Windows", "AMD64"),
        runtime.host_target("Windows", "ARM64"),
        runtime.host_target("Linux", "x86_64"),
        runtime.host_target("Linux", "aarch64"),
        runtime.host_target("Darwin", "x86_64"),
        runtime.host_target("Darwin", "arm64"),
    }
    assert resolved == set(runtime._ASSETS)


def test_pinned_digests_are_lowercase_sha256_and_distinct():
    digests = [asset["sha256"] for asset in runtime._ASSETS.values()]
    for digest in digests:
        assert len(digest) == 64, digest
        assert digest == digest.lower(), digest
        int(digest, 16)
    assert len(set(digests)) == len(digests)


def test_minimum_version_rejects_the_published_advisory_range():
    """2.8.1 is the highest fixed version across the advisories for 2.3.1."""
    assert runtime.parse_deno_version(runtime.DENO_MINIMUM_VERSION) >= (2, 8, 1)
    assert runtime.parse_deno_version(runtime.DENO_VERSION) >= runtime.parse_deno_version(
        runtime.DENO_MINIMUM_VERSION
    )


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


def _install(tmp_path, monkeypatch, config_dir, payload=b"deno"):
    archive = _archive(tmp_path, name=f"deno-{payload.decode()}.zip", payload=payload)
    _pin_archive(monkeypatch, archive)
    monkeypatch.setattr(runtime, "_probe_executable", lambda _path: runtime.DENO_VERSION)
    return runtime.install_managed_deno(archive, config_dir=config_dir)


def test_failed_extraction_leaves_the_previous_install_intact(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    first = _install(tmp_path, monkeypatch, config_dir, payload=b"original")
    original = Path(first["path"])
    assert original.read_bytes() == b"original"

    archive = _archive(tmp_path, name="replacement.zip", payload=b"replacement")
    _pin_archive(monkeypatch, archive)

    def _boom(*_args, **_kwargs):
        raise OSError("extraction failed")

    monkeypatch.setattr(runtime, "_extract_executable", _boom)

    with pytest.raises(runtime.DenoRuntimeError):
        runtime.install_managed_deno(archive, config_dir=config_dir)

    assert original.is_file()
    assert original.read_bytes() == b"original"
    info = runtime.get_managed_deno_info(config_dir=config_dir)
    assert info["available"] is True


def test_failed_version_probe_leaves_the_previous_install_intact(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    first = _install(tmp_path, monkeypatch, config_dir, payload=b"original")
    original = Path(first["path"])

    archive = _archive(tmp_path, name="probe-fail.zip", payload=b"replacement")
    _pin_archive(monkeypatch, archive)

    def _timeout(*_args, **_kwargs):
        raise runtime.DenoRuntimeError("probe timed out")

    monkeypatch.setattr(runtime, "_probe_executable", _timeout)

    with pytest.raises(runtime.DenoRuntimeError, match="probe timed out"):
        runtime.install_managed_deno(archive, config_dir=config_dir)

    assert original.is_file()
    assert original.read_bytes() == b"original"
    assert runtime.get_managed_deno_info(config_dir=config_dir)["available"] is True


def test_failed_first_install_leaves_no_partial_runtime(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    archive = _archive(tmp_path, name="first.zip", payload=b"first")
    _pin_archive(monkeypatch, archive)
    monkeypatch.setattr(
        runtime, "_probe_executable",
        lambda _path: (_ for _ in ()).throw(runtime.DenoRuntimeError("bad binary")),
    )

    with pytest.raises(runtime.DenoRuntimeError, match="bad binary"):
        runtime.install_managed_deno(archive, config_dir=config_dir)

    assert runtime.get_managed_deno_info(config_dir=config_dir)["available"] is False
