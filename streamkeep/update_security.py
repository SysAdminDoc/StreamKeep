"""Publisher authentication and anti-rollback rules for application updates.

The installed executable is the trust anchor: its valid Authenticode signer
certificate verifies both the detached release manifest and every installable
asset.  This keeps update verification offline and prevents a compromised
release feed from substituting a different, merely "validly signed" binary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa


MANIFEST_NAME = "StreamKeep-update.json"
SIGNATURE_NAME = "StreamKeep-update.json.sig"
REPOSITORY_PATH = "SysAdminDoc/StreamKeep"
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ASSET_NAMES = {
    "portable-exe": "StreamKeep.exe",
    "msix": "StreamKeep.msix",
}


class UpdateSecurityError(ValueError):
    """Raised when update provenance or monotonicity cannot be proven."""


def canonical_json_bytes(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def certificate_sha256(certificate) -> str:
    der = certificate.public_bytes(serialization.Encoding.DER)
    return sha256_bytes(der)


def _certificate_from_info(signer_info):
    encoded = str((signer_info or {}).get("certificate_der", "") or "")
    try:
        der = base64.b64decode(encoded, validate=True)
        certificate = x509.load_der_x509_certificate(der)
    except (ValueError, TypeError) as exc:
        raise UpdateSecurityError(
            "Installed publisher certificate could not be decoded."
        ) from exc
    expected = str((signer_info or {}).get("certificate_sha256", "") or "").lower()
    if not _SHA256_RE.fullmatch(expected) or certificate_sha256(certificate) != expected:
        raise UpdateSecurityError("Installed publisher certificate identity did not match.")
    return certificate


def _powershell_path():
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = [
        Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(system_root) / "Sysnative" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "powershell.exe"


def get_authenticode_info(path):
    """Return the Windows trust verdict and signer certificate for *path*.

    PowerShell delegates the actual PE/MSIX validation to Windows' trust
    provider.  The process is always non-interactive and console-hidden.
    """
    path = Path(path).resolve()
    if sys.platform != "win32":
        raise UpdateSecurityError("Authenticode verification requires Windows.")
    if not path.is_file():
        raise UpdateSecurityError("Signed update file was not found.")
    encoded_path = base64.b64encode(str(path).encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop';"
        "Import-Module Microsoft.PowerShell.Security -Force;"
        f"$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_path}'));"
        "$s=Get-AuthenticodeSignature -LiteralPath $p;"
        "if($null -eq $s.SignerCertificate){throw 'No signer certificate'};"
        "$o=[ordered]@{status=[string]$s.Status;"
        "status_message=[string]$s.StatusMessage;"
        "thumbprint=[string]$s.SignerCertificate.Thumbprint;"
        "subject=[string]$s.SignerCertificate.Subject;"
        "certificate_der=[Convert]::ToBase64String($s.SignerCertificate.RawData)};"
        "$o|ConvertTo-Json -Compress"
    )
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    environment = os.environ.copy()
    # PowerShell 7 adds its module roots to PSModulePath.  Windows PowerShell
    # can then pick incompatible type data and fail before WinTrust runs, so
    # constrain this child to the inbox Windows PowerShell module tree.
    environment["PSModulePath"] = str(
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"
    )
    try:
        completed = subprocess.run(
            [
                _powershell_path(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded_script,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=creationflags,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateSecurityError(f"Windows signature verification failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise UpdateSecurityError(f"Windows signature verification failed: {detail}")
    try:
        result = json.loads(completed.stdout)
        der = base64.b64decode(result["certificate_der"], validate=True)
        certificate = x509.load_der_x509_certificate(der)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateSecurityError("Windows returned invalid signature metadata.") from exc
    result["valid"] = str(result.get("status", "")) == "Valid"
    result["thumbprint"] = re.sub(
        r"[^0-9A-F]", "", str(result.get("thumbprint", "")).upper()
    )
    result["certificate_sha256"] = certificate_sha256(certificate)
    return result


def require_authenticode(path, *, expected_certificate_sha256="", asset_format=""):
    path = Path(path)
    expected_suffix = {"portable-exe": ".exe", "msix": ".msix"}.get(asset_format)
    if expected_suffix and path.suffix.lower() != expected_suffix:
        raise UpdateSecurityError("Update asset format did not match its signed manifest.")
    info = get_authenticode_info(path)
    if not info.get("valid"):
        status = str(info.get("status", "Unknown") or "Unknown")
        raise UpdateSecurityError(f"Windows rejected the publisher signature ({status}).")
    expected = str(expected_certificate_sha256 or "").lower()
    if expected and info.get("certificate_sha256", "").lower() != expected:
        raise UpdateSecurityError("Update asset was signed by a different publisher certificate.")
    return info


def sign_manifest_bytes(manifest_bytes: bytes, private_key, certificate):
    """Create the detached signature document used by release tooling."""
    if isinstance(private_key, rsa.RSAPrivateKey):
        algorithm = "rsa-pss-sha256"
        signature = private_key.sign(
            manifest_bytes,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
    elif isinstance(private_key, ec.EllipticCurvePrivateKey):
        algorithm = "ecdsa-sha256"
        signature = private_key.sign(manifest_bytes, ec.ECDSA(hashes.SHA256()))
    elif isinstance(private_key, ed25519.Ed25519PrivateKey):
        algorithm = "ed25519"
        signature = private_key.sign(manifest_bytes)
    else:
        raise UpdateSecurityError("Unsupported release signing key type.")
    return {
        "schema_version": 1,
        "algorithm": algorithm,
        "certificate_sha256": certificate_sha256(certificate),
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def verify_manifest_signature(manifest_bytes: bytes, signature_bytes: bytes, signer_info):
    if not (signer_info or {}).get("valid"):
        raise UpdateSecurityError("The installed application has no valid publisher signature.")
    certificate = _certificate_from_info(signer_info)
    try:
        document = json.loads(signature_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateSecurityError("Release manifest signature document is invalid.") from exc
    required = {"schema_version", "algorithm", "certificate_sha256", "signature"}
    if not isinstance(document, dict) or set(document) != required:
        raise UpdateSecurityError("Release manifest signature schema is invalid.")
    if document.get("schema_version") != 1:
        raise UpdateSecurityError("Release manifest signature schema is unsupported.")
    cert_digest = str(document.get("certificate_sha256", "") or "").lower()
    if cert_digest != signer_info.get("certificate_sha256", "").lower():
        raise UpdateSecurityError("Release manifest was signed by a different publisher certificate.")
    try:
        signature = base64.b64decode(document.get("signature", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise UpdateSecurityError("Release manifest signature encoding is invalid.") from exc
    public_key = certificate.public_key()
    algorithm = document.get("algorithm")
    try:
        if algorithm == "rsa-pss-sha256" and isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(
                signature,
                manifest_bytes,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
                hashes.SHA256(),
            )
        elif algorithm == "ecdsa-sha256" and isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature, manifest_bytes, ec.ECDSA(hashes.SHA256()))
        elif algorithm == "ed25519" and isinstance(public_key, ed25519.Ed25519PublicKey):
            public_key.verify(signature, manifest_bytes)
        else:
            raise UpdateSecurityError("Release manifest signature algorithm is unsupported.")
    except UpdateSecurityError:
        raise
    except Exception as exc:
        raise UpdateSecurityError("Release manifest publisher signature is invalid.") from exc
    return document


def parse_version(value):
    match = _SEMVER_RE.fullmatch(str(value or ""))
    if not match:
        raise UpdateSecurityError("Release version is not a stable semantic version.")
    return tuple(int(group) for group in match.groups())


def validate_manifest(manifest_bytes: bytes, signer_info):
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateSecurityError("Release manifest is invalid JSON.") from exc
    if canonical_json_bytes(manifest) != manifest_bytes:
        raise UpdateSecurityError("Release manifest is not canonical JSON.")
    required = {"schema_version", "sequence", "version", "tag", "assets"}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise UpdateSecurityError("Release manifest schema is invalid.")
    if manifest.get("schema_version") != 1:
        raise UpdateSecurityError("Release manifest schema is unsupported.")
    sequence = manifest.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise UpdateSecurityError("Release sequence must be a positive integer.")
    version = str(manifest.get("version", "") or "")
    parse_version(version)
    if manifest.get("tag") != f"v{version}":
        raise UpdateSecurityError("Release tag and version do not match.")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets or len(assets) > len(_ASSET_NAMES):
        raise UpdateSecurityError("Release manifest has no supported update assets.")
    expected_signer = str(signer_info.get("certificate_sha256", "") or "").lower()
    seen_names = set()
    normalized_assets = []
    asset_fields = {"name", "format", "size", "sha256", "signer_sha256"}
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != asset_fields:
            raise UpdateSecurityError("Release asset schema is invalid.")
        asset_format = str(asset.get("format", "") or "")
        name = str(asset.get("name", "") or "")
        if _ASSET_NAMES.get(asset_format) != name or name in seen_names:
            raise UpdateSecurityError("Release manifest contains an unsupported or duplicate asset.")
        size = asset.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise UpdateSecurityError("Release asset size is invalid.")
        digest = str(asset.get("sha256", "") or "").lower()
        if not _SHA256_RE.fullmatch(digest):
            raise UpdateSecurityError("Release asset SHA-256 is invalid.")
        asset_signer = str(asset.get("signer_sha256", "") or "").lower()
        if asset_signer != expected_signer:
            raise UpdateSecurityError("Release asset publisher does not match the installed application.")
        seen_names.add(name)
        normalized_assets.append({
            "name": name,
            "format": asset_format,
            "size": size,
            "sha256": digest,
            "signer_sha256": asset_signer,
        })
    return {**manifest, "assets": normalized_assets}


def validate_release_asset_url(url, tag, name):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", str(tag or "")):
        raise UpdateSecurityError("Release tag is invalid.")
    if name not in {MANIFEST_NAME, SIGNATURE_NAME, *_ASSET_NAMES.values()}:
        raise UpdateSecurityError("Release asset name is not allowed.")
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as exc:
        raise UpdateSecurityError("Release asset URL has an invalid port.") from exc
    expected_path = (
        f"/{REPOSITORY_PATH}/releases/download/"
        f"{quote(tag, safe='')}/{quote(name, safe='')}"
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != expected_path
    ):
        raise UpdateSecurityError("Release asset URL failed repository path validation.")
    return str(url)


def enforce_release_progress(manifest, manifest_digest, current_version, state):
    remote = parse_version(manifest.get("version"))
    current = parse_version(current_version)
    if remote <= current:
        raise UpdateSecurityError("Release is a replay or downgrade of the installed version.")
    sequence = int(manifest.get("sequence", 0))
    highest_sequence = int((state or {}).get("highest_sequence", 0) or 0)
    highest_version_text = str((state or {}).get("highest_version", "0.0.0") or "0.0.0")
    try:
        highest_version = parse_version(highest_version_text)
    except UpdateSecurityError:
        highest_version = (0, 0, 0)
    previous_digest = str((state or {}).get("manifest_sha256", "") or "").lower()
    if sequence < highest_sequence:
        raise UpdateSecurityError("Release sequence was replayed or rolled back.")
    if sequence == highest_sequence:
        if not previous_digest or manifest_digest != previous_digest:
            raise UpdateSecurityError("Release sequence conflicts with a previously verified manifest.")
        if remote != highest_version:
            raise UpdateSecurityError("Release version conflicts with a previously verified manifest.")
        return
    if remote <= highest_version:
        raise UpdateSecurityError("Release version was replayed or downgraded.")
