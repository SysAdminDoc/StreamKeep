"""Explicit, hash-verified acquisition of the optional Deno runtime.

The capability registry may inspect a managed runtime, but this module never
downloads or installs anything during import, startup, or capability probing.
Callers must invoke :func:`install_managed_deno` from an explicit user action.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import paths
from .paths import _CREATE_NO_WINDOW


DENO_VERSION = "2.3.1"
DENO_MINIMUM_VERSION = "2.3.0"
DENO_RELEASE_URL = (
    "https://github.com/denoland/deno/releases/download/"
    f"v{DENO_VERSION}"
)
DENO_RUNTIME_SCHEMA_VERSION = 1
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXTRACTED_BYTES = 128 * 1024 * 1024

# These are the SHA-256 values published beside the official Deno v2.3.1
# release assets. The v2.3.0 tag itself was published with an incorrect
# version and directs users to v2.3.1, so v2.3.1 is the smallest valid pin that
# satisfies StreamKeep's existing Deno 2.3+ floor.
_ASSETS = {
    "x86_64-pc-windows-msvc": {
        "asset": "deno-x86_64-pc-windows-msvc.zip",
        "sha256": "1b968541d115420ba04f7a5fbb5d0f8d620d9d87d492b66da5c97ca07e269b9b",
        "executable": "deno.exe",
    },
    "x86_64-unknown-linux-gnu": {
        "asset": "deno-x86_64-unknown-linux-gnu.zip",
        "sha256": "b2920265e633215959b09a32b67f46c93362842bbfd27c96e8acc2d24b66f563",
        "executable": "deno",
    },
    "aarch64-unknown-linux-gnu": {
        "asset": "deno-aarch64-unknown-linux-gnu.zip",
        "sha256": "3771ede34037694591846166f6211e7a8ab5cd77a1e7143e637d4457e8708dc7",
        "executable": "deno",
    },
    "x86_64-apple-darwin": {
        "asset": "deno-x86_64-apple-darwin.zip",
        "sha256": "ba34eb6ec164a0f89f5431fc1989a31f7896f76d074415f64ea70509de39fc56",
        "executable": "deno",
    },
    "aarch64-apple-darwin": {
        "asset": "deno-aarch64-apple-darwin.zip",
        "sha256": "e3d3d7b21ce89105d96c316e9370b1f05aa6e87687f40faf37a39a613a477014",
        "executable": "deno",
    },
}


class DenoRuntimeError(RuntimeError):
    """Raised when a pinned Deno archive cannot be safely installed."""


def parse_deno_version(value):
    """Return a numeric Deno version tuple from ``deno --version`` output."""
    match = re.search(r"\bdeno\s+v?(\d+(?:\.\d+){1,3})\b", str(value or ""), re.I)
    if not match:
        match = re.search(r"\bv?(\d+(?:\.\d+){1,3})\b", str(value or ""))
    if not match:
        return ()
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return ()


def _version_at_least(version, minimum):
    current = parse_deno_version(version) if isinstance(version, str) else tuple(version)
    required = parse_deno_version(minimum) if isinstance(minimum, str) else tuple(minimum)
    if not current or not required:
        return False
    length = max(len(current), len(required))
    return current + (0,) * (length - len(current)) >= required + (0,) * (
        length - len(required)
    )


def host_target(system=None, machine=None):
    """Return the pinned Deno target for the current host."""
    system = str(system or platform.system()).lower()
    machine = str(machine or platform.machine()).lower()
    machine = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
    }.get(machine, machine)
    if system == "windows" and machine == "x86_64":
        return "x86_64-pc-windows-msvc"
    if system == "linux" and machine in {"x86_64", "aarch64"}:
        return f"{machine}-unknown-linux-gnu"
    if system == "darwin" and machine in {"x86_64", "aarch64"}:
        return f"{machine}-apple-darwin"
    raise DenoRuntimeError(
        f"No pinned Deno {DENO_VERSION} asset is available for {system}/{machine}."
    )


def pinned_asset(system=None, machine=None):
    """Return the immutable asset descriptor for a host."""
    target = host_target(system, machine)
    descriptor = dict(_ASSETS[target])
    descriptor.update({
        "target": target,
        "version": DENO_VERSION,
        "url": f"{DENO_RELEASE_URL}/{descriptor['asset']}",
    })
    return descriptor


def runtime_root(config_dir=None):
    """Return the managed-runtime root, without creating it."""
    root = Path(config_dir) if config_dir is not None else paths.CONFIG_DIR
    return root.expanduser().resolve() / "runtimes" / "deno"


def runtime_directory(config_dir=None, *, target=None):
    """Return the versioned managed-runtime directory for the host."""
    target = target or host_target()
    return runtime_root(config_dir) / DENO_VERSION / target


def _executable_name(target=None):
    target = target or host_target()
    return _ASSETS[target]["executable"]


def managed_executable_path(config_dir=None, *, target=None):
    """Return the expected managed executable path."""
    target = target or host_target()
    return runtime_directory(config_dir, target=target) / _executable_name(target)


def read_runtime_preference(config=None):
    """Return ``path`` or ``managed`` without importing config persistence."""
    if isinstance(config, dict):
        value = config.get("javascript_runtime_preference", "path")
    else:
        value = "path"
        try:
            raw = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                value = raw.get("javascript_runtime_preference", value)
        except (OSError, ValueError, TypeError):
            pass
    return "managed" if str(value or "").strip().lower() == "managed" else "path"


def _metadata_path(config_dir=None, *, target=None):
    return runtime_directory(config_dir, target=target) / "runtime.json"


def _read_metadata(config_dir=None, *, target=None):
    path = _metadata_path(config_dir, target=target)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def get_managed_deno_info(config_dir=None, *, target=None):
    """Return a redacted, local-only description of the managed Deno."""
    target = target or host_target()
    executable = managed_executable_path(config_dir, target=target)
    metadata = _read_metadata(config_dir, target=target)
    valid = (
        metadata.get("schema_version") == DENO_RUNTIME_SCHEMA_VERSION
        and metadata.get("runtime") == "deno"
        and metadata.get("version") == DENO_VERSION
        and metadata.get("target") == target
        and executable.is_file()
    )
    if not valid:
        return {
            "available": False,
            "path": str(executable) if executable.exists() else "",
            "version": "",
            "target": target,
            "provenance": "",
            "source": "",
            "asset": str(metadata.get("asset") or ""),
            "sha256": str(metadata.get("archive_sha256") or ""),
            "detail": "No verified managed Deno runtime is installed.",
        }
    return {
        "available": True,
        "path": str(executable),
        "version": DENO_VERSION,
        "target": target,
        "provenance": str(metadata.get("provenance") or "managed"),
        "source": str(metadata.get("source") or metadata.get("provenance") or "managed"),
        "asset": str(metadata.get("asset") or ""),
        "sha256": str(metadata.get("archive_sha256") or ""),
        "detail": (
            f"Managed Deno {DENO_VERSION} at {executable} "
            f"({metadata.get('source') or 'managed'})."
        ),
    }


def bundled_deno_path():
    """Return a frozen-build Deno path when the distribution contains one."""
    if not getattr(sys, "frozen", False):
        return ""
    root = Path(sys.executable).resolve().parent
    target = host_target()
    executable = _executable_name(target)
    for candidate in (root / "runtime" / executable, root / "deno" / executable):
        if candidate.is_file():
            return str(candidate)
    return ""


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_archive(url, destination, *, timeout=180):
    """Download only the fixed official URL through the approved curl path."""
    try:
        from .capabilities import resolve_tool_command
        from .http import run_capture_interruptible

        curl = resolve_tool_command("curl")
    except Exception as error:
        raise DenoRuntimeError(f"Cannot acquire curl for the managed runtime: {error}") from error
    command = [
        curl,
        "--silent",
        "--show-error",
        "--location",
        "--fail",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--max-redirs",
        "5",
        "--connect-timeout",
        "15",
        "--max-time",
        str(max(30, int(timeout))),
        "--max-filesize",
        str(MAX_ARCHIVE_BYTES),
        "--retry",
        "2",
        "--retry-delay",
        "1",
        "--output",
        str(destination),
        url,
    ]
    result = run_capture_interruptible(command, timeout=max(40, int(timeout) + 15))
    if result.returncode != 0 or result.timed_out or result.interrupted:
        detail = (result.stderr or result.stdout or "curl failed").strip()[:240]
        raise DenoRuntimeError(f"Deno download failed: {detail}")
    try:
        if destination.stat().st_size > MAX_ARCHIVE_BYTES:
            raise DenoRuntimeError("Deno archive exceeds the safety size limit.")
    except OSError as error:
        raise DenoRuntimeError(f"Deno download did not produce an archive: {error}") from error


def _safe_archive_member(info, executable):
    name = str(info.filename or "").replace("\\", "/")
    parts = PurePosixPath(name).parts
    mode = (int(info.external_attr) >> 16) & 0o170000
    if (
        not name
        or name.startswith("/")
        or ":" in name.split("/", 1)[0]
        or ".." in parts
        or mode == 0o120000
        or info.is_dir()
        or name != executable
    ):
        return False
    return True


def _extract_executable(archive, destination, executable):
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            infos = bundle.infolist()
            if len(infos) > 16:
                raise DenoRuntimeError("Deno archive contains too many entries.")
            match = next(
                (info for info in infos if _safe_archive_member(info, executable)),
                None,
            )
            if match is None:
                raise DenoRuntimeError(
                    f"Deno archive does not contain the expected {executable} entry."
                )
            if match.file_size <= 0 or match.file_size > MAX_EXTRACTED_BYTES:
                raise DenoRuntimeError("Deno executable exceeds the safety size limit.")
            with bundle.open(match, "r") as source, Path(destination).open("wb") as target:
                total = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_EXTRACTED_BYTES:
                        raise DenoRuntimeError("Deno executable exceeds the safety size limit.")
                    target.write(chunk)
    except zipfile.BadZipFile as error:
        raise DenoRuntimeError("Deno archive is not a valid ZIP file.") from error
    if os.name != "nt":
        try:
            Path(destination).chmod(0o755)
        except OSError as error:
            raise DenoRuntimeError(f"Cannot mark managed Deno executable: {error}") from error


def _probe_executable(path):
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DenoRuntimeError(f"Managed Deno could not be executed: {error}") from error
    output = "\n".join(
        part.strip() for part in (result.stdout, result.stderr) if part.strip()
    )
    version = parse_deno_version(output)
    if result.returncode != 0 or not version:
        raise DenoRuntimeError("Managed Deno did not report a valid version.")
    version_text = ".".join(str(part) for part in version)
    if version_text != DENO_VERSION:
        raise DenoRuntimeError(
            f"Managed Deno reports {version_text}; expected pinned {DENO_VERSION}."
        )
    return version_text


def _write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        temporary_path.write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def install_managed_deno(archive_path=None, *, config_dir=None, timeout=180):
    """Install the pinned Deno asset from the network or a local archive.

    ``archive_path`` is the offline path: the supplied ZIP must match the
    exact published SHA-256 for this host before it is opened or extracted.
    """
    descriptor = pinned_asset()
    root = runtime_root(config_dir)
    root.mkdir(parents=True, exist_ok=True)
    temporary_archive = None
    archive = None
    source = "offline-archive" if archive_path else "managed-download"
    try:
        if archive_path:
            archive = Path(archive_path).expanduser().resolve()
            if not archive.is_file():
                raise DenoRuntimeError(f"Deno archive was not found: {archive}")
            if archive.stat().st_size > MAX_ARCHIVE_BYTES:
                raise DenoRuntimeError("Deno archive exceeds the safety size limit.")
        else:
            fd, temporary_archive = tempfile.mkstemp(
                prefix="deno-", suffix=".zip", dir=str(root)
            )
            os.close(fd)
            archive = Path(temporary_archive)
            _download_archive(descriptor["url"], archive, timeout=timeout)

        digest = _sha256_file(archive)
        if digest.lower() != descriptor["sha256"].lower():
            raise DenoRuntimeError(
                "Deno archive SHA-256 does not match the pinned official asset."
            )

        parent = root / DENO_VERSION
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=str(parent)))
        target = parent / descriptor["target"]
        backup = parent / f".backup-{uuid.uuid4().hex}"
        try:
            staged_executable = staging / descriptor["executable"]
            _extract_executable(archive, staged_executable, descriptor["executable"])
            version = _probe_executable(staged_executable)
            metadata = {
                "schema_version": DENO_RUNTIME_SCHEMA_VERSION,
                "runtime": "deno",
                "version": DENO_VERSION,
                "target": descriptor["target"],
                "asset": descriptor["asset"],
                "archive_sha256": descriptor["sha256"],
                "source": source,
                "provenance": source,
                "url": descriptor["url"] if source == "managed-download" else "",
                "path": str(target / descriptor["executable"]),
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_json_atomic(staging / "runtime.json", metadata)
            if target.exists():
                os.replace(target, backup)
            os.replace(staging, target)
            shutil.rmtree(backup, ignore_errors=True)
            return {
                "available": True,
                "path": str(target / descriptor["executable"]),
                "version": version,
                "target": descriptor["target"],
                "provenance": source,
                "source": source,
                "asset": descriptor["asset"],
                "sha256": descriptor["sha256"],
                "detail": f"Installed Deno {version} from {source}.",
            }
        except Exception:
            if target.exists() and not backup.exists():
                shutil.rmtree(target, ignore_errors=True)
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(backup, ignore_errors=True)
    except DenoRuntimeError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise DenoRuntimeError(f"Deno installation failed: {error}") from error
    finally:
        if temporary_archive:
            Path(temporary_archive).unlink(missing_ok=True)


def remove_managed_deno(*, config_dir=None, target=None):
    """Remove only the verified, versioned managed-runtime directory."""
    target = target or host_target()
    directory = runtime_directory(config_dir, target=target)
    root = runtime_root(config_dir)
    try:
        directory.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise DenoRuntimeError("Managed Deno path escaped its runtime root.") from error
    if not directory.exists():
        return False
    shutil.rmtree(directory)
    return True


__all__ = [
    "DENO_MINIMUM_VERSION",
    "DENO_RELEASE_URL",
    "DENO_RUNTIME_SCHEMA_VERSION",
    "DENO_VERSION",
    "DenoRuntimeError",
    "bundled_deno_path",
    "get_managed_deno_info",
    "host_target",
    "install_managed_deno",
    "managed_executable_path",
    "parse_deno_version",
    "pinned_asset",
    "read_runtime_preference",
    "remove_managed_deno",
    "runtime_directory",
    "runtime_root",
]
