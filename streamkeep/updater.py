"""GitHub release discovery plus the operator-only authenticated updater.

The startup check reads stable release metadata and can report a newer public
release with GitHub's published SHA-256 for manual verification.  Automatic
staging and self-replacement remain an operator-only path: a canonical
manifest signed by the installed publisher certificate, a matching signed
asset, and the exact repository release path are still required.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

from PyQt6.QtCore import QThread, pyqtSignal

from .update_runtime import prepare_update_transaction
from .update_security import (
    REPOSITORY_PATH,
    UpdateSecurityError,
    enforce_release_progress,
    parse_published_sha256,
    parse_version,
    require_authenticode,
    sha256_bytes,
    sha256_file,
    validate_manifest,
    validate_release_asset_url,
    verify_manifest_signature,
)


RELEASES_URL = "https://api.github.com/repos/SysAdminDoc/StreamKeep/releases/latest"
USER_AGENT = "StreamKeep-updater"
MAX_RELEASE_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_SIGNATURE_BYTES = 16 * 1024


def _parse_semver(value):
    try:
        from .update_security import parse_version
        return parse_version(str(value or "").lstrip("v"))
    except UpdateSecurityError:
        return (0, 0, 0)


def is_newer(remote, local):
    return _parse_semver(remote) > _parse_semver(local)


def _empty_payload(error=""):
    return {
        "available": False,
        "error": str(error or ""),
        "tag": "",
        "version": "",
        "notes": "",
        "sequence": 0,
        "manifest_sha256": "",
        "current_version": "",
        "signer_subject": "",
        "asset": {},
        "manual_only": False,
        "self_replace_allowed": False,
        "published_sha256": "",
        "release_url": "",
    }


def _read_limited(response, maximum):
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise UpdateSecurityError("Update metadata exceeded its size limit.")
    return data


def _fetch_bytes(url, maximum, *, timeout=15):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return _read_limited(response, maximum)


def _find_release_asset(release, name):
    matches = [
        asset for asset in (release.get("assets") or [])
        if isinstance(asset, dict) and asset.get("name") == name
    ]
    if len(matches) != 1:
        raise UpdateSecurityError(f"Release must contain exactly one {name} asset.")
    return matches[0]


def _release_asset_url(release, name):
    asset = _find_release_asset(release, name)
    tag = str(release.get("tag_name", "") or "")
    url = str(asset.get("browser_download_url", "") or "")
    validate_release_asset_url(url, tag, name)
    return url


def _public_release_payload(release, current_version):
    """Return manual-install metadata without invoking publisher trust code."""
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise UpdateSecurityError("Update feed did not return a stable published release.")
    tag = str(release.get("tag_name", "") or "").strip()
    if not tag.startswith("v"):
        raise UpdateSecurityError("Update feed tag is not a versioned release.")
    version = tag[1:]
    remote = parse_version(version)
    if tag != "v" + ".".join(str(part) for part in remote):
        raise UpdateSecurityError("Update feed tag is not a canonical semantic version.")
    current = parse_version(str(current_version or "").lstrip("v"))
    payload = _empty_payload()
    payload.update({
        "tag": tag,
        "version": version,
        "notes": str(release.get("body", "") or "").strip(),
        "current_version": str(current_version or ""),
    })
    if remote <= current:
        return payload

    selected = None
    for name, asset_format in (
        ("StreamKeep.exe", "portable-exe"),
        ("StreamKeep.msix", "msix"),
    ):
        if any(
            isinstance(row, dict) and row.get("name") == name
            for row in (release.get("assets") or [])
        ):
            selected = (_find_release_asset(release, name), asset_format)
            break
    if selected is None:
        raise UpdateSecurityError("Release has no supported downloadable Windows asset.")
    api_asset, asset_format = selected
    size = api_asset.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise UpdateSecurityError("Release asset size is invalid.")
    name = str(api_asset.get("name", "") or "")
    url = str(api_asset.get("browser_download_url", "") or "")
    validate_release_asset_url(url, tag, name)
    digest = parse_published_sha256(api_asset.get("digest"))
    release_url = (
        f"https://github.com/{REPOSITORY_PATH}/releases/tag/"
        f"{quote(tag, safe='')}"
    )
    payload.update({
        "available": True,
        "manual_only": True,
        "self_replace_allowed": False,
        "published_sha256": digest,
        "release_url": release_url,
        "asset": {
            "name": name,
            "format": asset_format,
            "size": size,
            "sha256": digest,
            "url": url,
        },
    })
    return payload


def load_update_state(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateSecurityError("Local update rollback state is unreadable.") from exc
    if not isinstance(state, dict):
        raise UpdateSecurityError("Local update rollback state is invalid.")
    sequence = state.get("highest_sequence", 0)
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise UpdateSecurityError("Local update rollback sequence is invalid.")
    return state


def _write_update_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(temporary, path)


def record_verified_manifest(state_path, payload):
    state = load_update_state(state_path)
    sequence = int(payload["sequence"])
    if sequence >= int(state.get("highest_sequence", 0) or 0):
        state.update({
            "highest_sequence": sequence,
            "highest_version": payload["version"],
            "manifest_sha256": payload["manifest_sha256"],
        })
        _write_update_state(state_path, state)


def verify_release_document(
    release,
    manifest_bytes,
    signature_bytes,
    signer_info,
    current_version,
    state,
):
    """Verify operator-signed metadata and return a self-update payload."""
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise UpdateSecurityError("Update feed did not return a stable published release.")
    verify_manifest_signature(manifest_bytes, signature_bytes, signer_info)
    manifest = validate_manifest(manifest_bytes, signer_info)
    digest = sha256_bytes(manifest_bytes)
    if release.get("tag_name") != manifest["tag"]:
        raise UpdateSecurityError("Release feed tag did not match the signed manifest.")
    enforce_release_progress(manifest, digest, current_version, state)

    api_assets = {}
    for row in release.get("assets") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "") or "")
        if name in api_assets:
            raise UpdateSecurityError("Release feed contains duplicate asset names.")
        api_assets[name] = row
    mapped_assets = []
    for signed_asset in manifest["assets"]:
        name = signed_asset["name"]
        api_asset = api_assets.get(name)
        if not api_asset:
            raise UpdateSecurityError(f"Signed release asset {name} is missing.")
        size = api_asset.get("size")
        if isinstance(size, bool):
            size = -1
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = -1
        if size != signed_asset["size"]:
            raise UpdateSecurityError("Release feed asset size did not match the signed manifest.")
        url = str(api_asset.get("browser_download_url", "") or "")
        validate_release_asset_url(url, manifest["tag"], name)
        mapped_assets.append({**signed_asset, "url": url})
    selected = next(
        (asset for asset in mapped_assets if asset["format"] == "portable-exe"),
        None,
    )
    if selected is None:
        raise UpdateSecurityError("Signed release does not contain the portable Windows executable.")
    return {
        "available": True,
        "error": "",
        "tag": manifest["tag"],
        "version": manifest["version"],
        "notes": str(release.get("body", "") or "").strip(),
        "sequence": manifest["sequence"],
        "manifest_sha256": digest,
        "current_version": current_version,
        "signer_subject": str(signer_info.get("subject", "") or ""),
        "asset": selected,
        "manual_only": False,
        "self_replace_allowed": True,
        "published_sha256": selected["sha256"],
        "release_url": (
            f"https://github.com/{REPOSITORY_PATH}/releases/tag/"
            f"{quote(manifest['tag'], safe='')}"
        ),
    }


def validate_download_payload(payload, state):
    if not isinstance(payload, dict) or not payload.get("available"):
        raise UpdateSecurityError("Verified update metadata is missing.")
    if payload.get("manual_only"):
        raise UpdateSecurityError("Public update metadata is manual-only.")
    sequence = payload.get("sequence")
    if sequence != state.get("highest_sequence"):
        raise UpdateSecurityError("Update metadata is not the newest verified release.")
    if payload.get("version") != state.get("highest_version"):
        raise UpdateSecurityError("Update version does not match local rollback state.")
    if payload.get("manifest_sha256") != state.get("manifest_sha256"):
        raise UpdateSecurityError("Update manifest identity does not match local rollback state.")
    asset = payload.get("asset")
    if not isinstance(asset, dict):
        raise UpdateSecurityError("Verified update asset metadata is missing.")
    required = {"name", "format", "size", "sha256", "signer_sha256", "url"}
    if set(asset) != required:
        raise UpdateSecurityError("Verified update asset schema is invalid.")
    validate_release_asset_url(asset["url"], payload.get("tag"), asset["name"])
    if asset["format"] != "portable-exe" or asset["name"] != "StreamKeep.exe":
        raise UpdateSecurityError("Self-update requires the signed portable executable.")
    return asset


class UpdateCheckWorker(QThread):
    """Discover a public release without blocking the UI.

    This worker never authenticates the running executable and never writes
    rollback state.  Its result is display/download-page metadata only;
    ``DownloadUpdateWorker`` and ``arm_self_replace`` enforce the separate
    operator-authenticated path.
    """

    result = pyqtSignal(dict)

    def __init__(self, current_version):
        super().__init__()
        self.current_version = current_version

    def _current_path(self):
        return sys.executable

    def run(self):
        payload = _empty_payload()
        try:
            release = json.loads(_fetch_bytes(RELEASES_URL, MAX_RELEASE_BYTES).decode("utf-8"))
            payload = _public_release_payload(release, self.current_version)
        except UpdateSecurityError as exc:
            payload = _empty_payload(f"Update check unavailable: {exc}")
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            payload = _empty_payload()
        self.result.emit(payload)


class DownloadUpdateWorker(QThread):
    """Download an exact asset from an operator-verified manifest only."""

    progress = pyqtSignal(int, str)
    done = pyqtSignal(bool, str)

    def __init__(self, release_payload):
        super().__init__()
        self.release_payload = dict(release_payload or {})
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _target_path(self):
        return sys.executable

    def _remove_staged(self, path):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    def run(self):
        target = Path(self._target_path()).resolve()
        staged = Path(f"{target}.new")
        frozen = bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))
        if not frozen:
            self.done.emit(False, "Auto-update only supports the packaged executable.")
            return
        try:
            from .paths import CONFIG_DIR
            state = load_update_state(CONFIG_DIR / "update-state.json")
            asset = validate_download_payload(self.release_payload, state)
            require_authenticode(
                target,
                expected_certificate_sha256=asset["signer_sha256"],
                asset_format="portable-exe",
            )
        except (OSError, UpdateSecurityError) as exc:
            self.done.emit(False, f"Update blocked: {exc}")
            return
        cancelled = False
        try:
            request = urllib.request.Request(asset["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=30) as response, open(staged, "wb") as output:
                written = 0
                expected_size = asset["size"]
                while True:
                    if self._cancel:
                        cancelled = True
                        break
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    written += len(chunk)
                    if written > expected_size:
                        raise UpdateSecurityError(
                            "Download exceeded the size in the signed manifest."
                        )
                    percent = min(99, int((written / expected_size) * 100))
                    self.progress.emit(
                        percent,
                        f"{written // 1024} KB / {expected_size // 1024} KB",
                    )
        except UpdateSecurityError as exc:
            self._remove_staged(staged)
            self.done.emit(False, f"Update blocked: {exc}")
            return
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            OSError,
        ) as exc:
            self._remove_staged(staged)
            self.done.emit(False, f"Download failed: {exc}")
            return
        if cancelled:
            self._remove_staged(staged)
            self.done.emit(False, "Download cancelled.")
            return
        try:
            if staged.stat().st_size != asset["size"]:
                raise UpdateSecurityError("Downloaded file size did not match the signed manifest.")
            self.progress.emit(99, "Verifying publisher signature...")
            if sha256_file(staged) != asset["sha256"]:
                raise UpdateSecurityError("Downloaded file SHA-256 did not match the signed manifest.")
            require_authenticode(
                staged,
                expected_certificate_sha256=asset["signer_sha256"],
                asset_format=asset["format"],
            )
        except (OSError, UpdateSecurityError) as exc:
            self._remove_staged(staged)
            self.done.emit(False, f"Update blocked: {exc}")
            return
        self.progress.emit(100, "Authenticated")
        self.done.emit(True, str(staged))


def is_directory_install(executable=None):
    """Return whether this build is a onedir/package-managed installation.

    A onedir install is a *tree* — the executable next to an ``_internal``
    directory — so swapping one file would leave the runtime it was built
    against behind and produce a half-updated install. Those builds are
    updated by reinstalling or through a package manager instead (V35).
    """
    target = Path(executable or sys.executable).resolve()
    try:
        return (target.parent / "_internal").is_dir()
    except OSError:
        return False


def self_replace_unavailable_reason(executable=None):
    """Explain why in-app self-replacement is not offered, or '' when it is."""
    if not bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")):
        return "Self-update applies to frozen builds only."
    if is_directory_install(executable):
        return (
            "This is a directory install. Update by downloading the new "
            "release and verifying its published SHA-256 hash, or through the "
            "package manager that installed StreamKeep."
        )
    return ""


def arm_self_replace(new_exe_path, release_payload):
    """Start a detached last-known-good watchdog and return immediately.

    Refuses outright for a onedir/package-managed install: replacing the single
    executable inside a tree cannot produce a consistent installation, and
    unsigned public releases have no publisher key to authorize one anyway.
    """
    if bool((release_payload or {}).get("manual_only")):
        return False
    target = Path(sys.executable).resolve()
    staged = Path(new_exe_path).resolve()
    helper = Path(f"{target}.update-helper.exe")
    frozen = bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))
    if not frozen or staged != Path(f"{target}.new") or not staged.is_file():
        return False
    if is_directory_install(target):
        return False
    transaction = None
    try:
        from .paths import CONFIG_DIR
        state = load_update_state(CONFIG_DIR / "update-state.json")
        asset = validate_download_payload(release_payload, state)
        require_authenticode(
            target,
            expected_certificate_sha256=asset["signer_sha256"],
            asset_format="portable-exe",
        )
        require_authenticode(
            staged,
            expected_certificate_sha256=asset["signer_sha256"],
            asset_format="portable-exe",
        )
        shutil.copy2(target, helper)
        transaction = prepare_update_transaction(
            current_path=target,
            staged_path=staged,
            helper_path=helper,
            config_dir=CONFIG_DIR,
            release_payload=release_payload,
        )
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        subprocess.Popen(
            [str(helper), "--internal-update-helper", str(transaction)],
            close_fds=True,
            creationflags=flags,
        )
        return True
    except (OSError, ValueError, UpdateSecurityError, subprocess.SubprocessError):
        if transaction is not None:
            shutil.rmtree(Path(transaction).parent, ignore_errors=True)
        try:
            helper.unlink(missing_ok=True)
        except OSError:
            pass
        return False
