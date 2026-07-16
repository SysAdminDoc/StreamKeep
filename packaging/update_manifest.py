#!/usr/bin/env python3
"""Sign Windows release assets and emit the authenticated update manifest.

The PFX used for Authenticode is also used to create the detached manifest
signature, binding the update feed to the installed application's publisher.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.serialization import pkcs12


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamkeep.update_security import (  # noqa: E402
    MANIFEST_NAME,
    SIGNATURE_NAME,
    canonical_json_bytes,
    certificate_sha256,
    require_authenticode,
    sha256_file,
    sign_manifest_bytes,
    parse_version,
)


def _find_signtool():
    configured = os.environ.get("STREAMKEEP_SIGNTOOL", "").strip()
    if configured and Path(configured).is_file():
        return configured
    for candidate in ("signtool.exe", "signtool"):
        found = shutil.which(candidate)
        if found:
            return found
    for sdk_root in (
        Path(r"C:\Program Files (x86)\Windows Kits\10\bin"),
        Path(r"C:\Program Files\Windows Kits\10\bin"),
    ):
        if not sdk_root.is_dir():
            continue
        for version in sorted(sdk_root.iterdir(), reverse=True):
            candidate = version / "x64" / "signtool.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _load_pfx():
    configured = os.environ.get("STREAMKEEP_SIGN_PFX", "").strip()
    if not configured:
        raise RuntimeError("STREAMKEEP_SIGN_PFX is required for release manifests.")
    path = Path(configured).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"Publisher PFX was not found: {path}")
    password = os.environ.get("STREAMKEEP_SIGN_PASSWORD", "")
    try:
        private_key, certificate, _chain = pkcs12.load_key_and_certificates(
            path.read_bytes(),
            password.encode("utf-8") if password else None,
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError("Publisher PFX could not be opened.") from exc
    if private_key is None or certificate is None:
        raise RuntimeError("Publisher PFX does not contain a certificate and private key.")
    return path, password, private_key, certificate


def _sign_asset(path, pfx_path, password):
    signtool = _find_signtool()
    if not signtool:
        raise RuntimeError("signtool.exe was not found in the Windows SDK.")
    timestamp = os.environ.get(
        "STREAMKEEP_TIMESTAMP_URL", "http://timestamp.digicert.com"
    ).strip()
    command = [
        signtool,
        "sign",
        "/fd", "SHA256",
        "/td", "SHA256",
        "/tr", timestamp,
        "/f", str(pfx_path),
    ]
    if password:
        command.extend(["/p", password])
    command.append(str(path))
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown signing error").strip()
        raise RuntimeError(f"Authenticode signing failed for {path.name}: {detail}")


def build_release_documents(version, sequence, assets, *, sign_assets=True, output_dir=None):
    parse_version(version)
    if isinstance(sequence, bool) or int(sequence) < 1:
        raise RuntimeError("Release sequence must be a positive integer.")
    pfx_path, password, private_key, certificate = _load_pfx()
    certificate_digest = certificate_sha256(certificate)
    rows = []
    seen_formats = set()
    for raw_path in assets:
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise RuntimeError(f"Release asset was not found: {path}")
        if path.name == "StreamKeep.exe":
            asset_format = "portable-exe"
        elif path.name == "StreamKeep.msix":
            asset_format = "msix"
        else:
            raise RuntimeError(f"Unsupported release asset name: {path.name}")
        if asset_format in seen_formats:
            raise RuntimeError(f"Duplicate {asset_format} release asset.")
        if sign_assets:
            _sign_asset(path, pfx_path, password)
        signer = require_authenticode(
            path,
            expected_certificate_sha256=certificate_digest,
            asset_format=asset_format,
        )
        if signer["certificate_sha256"] != certificate_digest:
            raise RuntimeError(f"Publisher mismatch for {path.name}.")
        rows.append({
            "name": path.name,
            "format": asset_format,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "signer_sha256": certificate_digest,
        })
        seen_formats.add(asset_format)
    if "portable-exe" not in seen_formats:
        raise RuntimeError("The updater release must include StreamKeep.exe.")
    rows.sort(key=lambda row: row["name"])
    manifest = {
        "schema_version": 1,
        "sequence": int(sequence),
        "version": str(version),
        "tag": f"v{version}",
        "assets": rows,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    signature = sign_manifest_bytes(manifest_bytes, private_key, certificate)
    signature_bytes = canonical_json_bytes(signature)
    destination = Path(output_dir).resolve() if output_dir else Path(assets[0]).resolve().parent
    _atomic_write(destination / MANIFEST_NAME, manifest_bytes)
    _atomic_write(destination / SIGNATURE_NAME, signature_bytes)
    return destination / MANIFEST_NAME, destination / SIGNATURE_NAME


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Sign StreamKeep update assets and emit authenticated release metadata."
    )
    parser.add_argument("--version", required=True, help="Stable semantic version, for example 4.32.0")
    parser.add_argument("--sequence", required=True, type=int, help="Strictly increasing release sequence")
    parser.add_argument("--asset", action="append", required=True, help="StreamKeep.exe or StreamKeep.msix")
    parser.add_argument("--output-dir", help="Metadata output directory (defaults to first asset directory)")
    parser.add_argument(
        "--already-signed",
        action="store_true",
        help="Verify existing Authenticode signatures instead of signing assets again",
    )
    args = parser.parse_args(argv)
    try:
        manifest, signature = build_release_documents(
            args.version,
            args.sequence,
            args.asset,
            sign_assets=not args.already_signed,
            output_dir=args.output_dir,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(f"Authenticated manifest: {manifest}")
    print(f"Detached signature: {signature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
