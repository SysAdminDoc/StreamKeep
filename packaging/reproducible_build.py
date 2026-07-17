"""Build, compare, inventory, and smoke-test an isolated Windows release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

from locked_requirements import locked_packages, validate_hashed_lock
from sqlite_runtime import SQLITE_SHA3_256, SQLITE_VERSION, acquire
from versioning import read_version, stamp_versions, version_drift


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK = ROOT / "work" / "reproducible-build"
RUNTIME_LOCK = ROOT / "requirements.lock"
BUILD_LOCK = ROOT / "requirements-build.lock"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command, *, env=None, capture=False):
    return subprocess.run(
        [str(value) for value in command],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
        creationflags=CREATE_NO_WINDOW,
    )


def _safe_clean(path):
    path = Path(path).resolve()
    work_root = (ROOT / "work").resolve()
    if path == work_root or work_root not in path.parents:
        raise RuntimeError(f"Refusing to clean non-build path: {path}")
    if path.exists():
        shutil.rmtree(path)


def _venv_python(environment_root):
    folder = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return environment_root / folder / executable


def _source_date_epoch():
    configured = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if configured:
        return configured
    result = _run(["git", "show", "-s", "--format=%ct", "HEAD"], capture=True)
    return result.stdout.strip()


def _validate_inputs():
    errors = []
    for lock in (RUNTIME_LOCK, BUILD_LOCK):
        if not lock.is_file():
            errors.append(f"missing {lock.name}")
            continue
        errors.extend(f"{lock.name}: {error}" for error in validate_hashed_lock(lock))
    if not errors:
        runtime = dict(locked_packages(RUNTIME_LOCK))
        build = dict(locked_packages(BUILD_LOCK))
        for name, version in runtime.items():
            if build.get(name) != version:
                errors.append(f"build lock does not preserve runtime pin {name}=={version}")
        if build.get("pyinstaller") != "6.21.0":
            errors.append("build lock must pin pyinstaller==6.21.0")
    errors.extend(version_drift(ROOT))
    if errors:
        raise RuntimeError("Release input validation failed:\n- " + "\n- ".join(errors))


def _create_environment(environment_root):
    venv.EnvBuilder(with_pip=True, clear=True).create(environment_root)
    python = _venv_python(environment_root)
    _run([
        python, "-m", "pip", "install",
        "--require-hashes", "--only-binary=:all:", "-r", BUILD_LOCK,
    ])
    return python


def _build(python, label, work_root, environment, sqlite_dll):
    dist_path = work_root / label / "dist"
    work_path = work_root / label / "pyinstaller"
    _run([
        python, ROOT / "packaging" / "build.py", "--clean", "--noconfirm",
        "--sqlite-dll", sqlite_dll,
        "--dist-path", dist_path,
        "--work-path", work_path,
    ], env=environment)
    artifact = dist_path / "StreamKeep.exe"
    if not artifact.is_file():
        raise RuntimeError(f"PyInstaller did not produce {artifact}")
    return artifact


def _smoke(artifact, work_root):
    result = _run([
        sys.executable, ROOT / "packaging" / "artifact_smoke.py",
        "--executable", artifact,
        "--work-dir", work_root / "artifact-smoke",
    ], capture=True)
    data = json.loads(result.stdout)
    return {
        "passed": data["passed"],
        "fixtures": [
            {"fixture": row["fixture"], "passed": row["passed"]}
            for row in data["cases"]
        ],
    }


def _write_release_manifest(output_dir, artifact, epoch, smoke, sqlite_dll):
    browser_manifest = ROOT / "browser-extension" / "manifest.json"
    browser = json.loads(browser_manifest.read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT))
    from streamkeep.capabilities import get_product_capability_claims

    manifest = {
        "schema_version": 1,
        "application": {"name": "StreamKeep", "version": read_version(ROOT)},
        "artifact": {
            "file": artifact.name,
            "sha256": sha256(artifact),
            "size": artifact.stat().st_size,
        },
        "build": {
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "source_date_epoch": int(epoch),
            "runtime_lock": {"file": RUNTIME_LOCK.name, "sha256": sha256(RUNTIME_LOCK)},
            "build_lock": {"file": BUILD_LOCK.name, "sha256": sha256(BUILD_LOCK)},
            "sqlite": {
                "version": SQLITE_VERSION,
                "archive_sha3_256": SQLITE_SHA3_256,
                "library_sha256": sha256(sqlite_dll),
            },
            "browser_companion": {
                "version": browser["version"],
                "manifest_sha256": sha256(browser_manifest),
            },
        },
        "capabilities": [
            {"id": claim.id, "status": claim.status}
            for claim in get_product_capability_claims()
        ],
        "artifact_smoke": smoke,
    }
    path = output_dir / "release-manifest.json"
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--verify-reproducible", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args(argv)

    if sys.version_info[:2] != (3, 12):
        parser.error("requirements locks are generated and supported with Python 3.12")
    _validate_inputs()
    stamp_versions(ROOT)

    work_root = args.work_dir.resolve()
    _safe_clean(work_root)
    work_root.mkdir(parents=True)
    environment_root = work_root / "environment"
    python = _create_environment(environment_root)
    sqlite_dll = acquire(work_root / "sqlite-runtime" / SQLITE_VERSION)
    epoch = _source_date_epoch()
    environment = os.environ.copy()
    environment.update({
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": epoch,
    })

    artifact_a = _build(python, "build-a", work_root, environment, sqlite_dll)
    if args.verify_reproducible:
        artifact_b = _build(python, "build-b", work_root, environment, sqlite_dll)
        digest_a, digest_b = sha256(artifact_a), sha256(artifact_b)
        if digest_a != digest_b:
            raise RuntimeError(
                "Reproducibility check failed: "
                f"build-a={digest_a}, build-b={digest_b}"
            )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / "StreamKeep.exe"
    shutil.copy2(artifact_a, artifact)
    _run([
        python, ROOT / "packaging" / "sbom.py",
        "--lock", RUNTIME_LOCK,
        "--output", output_dir / "sbom.cdx.json",
        "--licenses", output_dir / "third-party-licenses.json",
    ], env=environment)
    smoke = {"passed": False, "fixtures": []}
    if not args.skip_smoke:
        smoke = _smoke(artifact, work_root)
    manifest = _write_release_manifest(output_dir, artifact, epoch, smoke, sqlite_dll)
    print(f"Artifact: {artifact}")
    print(f"SHA-256: {sha256(artifact)}")
    print(f"Manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
