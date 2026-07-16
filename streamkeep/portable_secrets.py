"""Explicit password-protected credential transfer backups."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

FORMAT_NAME = "streamkeep-portable-secrets"
FORMAT_VERSION = 1
AAD = b"StreamKeep portable secrets v1"
MAX_BACKUP_BYTES = 16 * 1024 * 1024
KDF_DEFAULTS = {
    "name": "argon2id",
    "time_cost": 3,
    "memory_cost_kib": 65536,
    "parallelism": 4,
    "hash_len": 32,
}


def create_portable_secret_backup(output_path, password):
    """Encrypt config/account/cookie auth state with Argon2id + AES-GCM."""
    if not output_path:
        return False, "No output path specified"
    if len(str(password or "")) < 10:
        return False, "Password must be at least 10 characters."
    try:
        from .accounts import get_credential, list_platforms
        from .config import load_config
        from .cookies import export_cookie_text
        from .secrets import collect_config_secrets

        payload = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "config_secrets": collect_config_secrets(load_config()),
            "accounts": {
                platform: credential
                for platform in list_platforms()
                if (credential := get_credential(platform))
            },
            "cookies": export_cookie_text(),
        }
        plaintext = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = _derive_key(str(password), salt, KDF_DEFAULTS)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, AAD)
        envelope = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "kdf": {
                **KDF_DEFAULTS,
                "salt": base64.b64encode(salt).decode("ascii"),
            },
            "cipher": {
                "name": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode("ascii"),
            },
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        encoded = json.dumps(
            envelope, indent=2, sort_keys=True
        ).encode("utf-8")
        if len(encoded) > MAX_BACKUP_BYTES:
            return False, "Portable-secret backup exceeds 16 MB."
        _write_atomic(Path(output_path), encoded)
        return True, "Password-protected portable-secret backup created."
    except Exception as error:
        return False, f"Portable-secret backup failed: {error}"


def restore_portable_secret_backup(backup_path, password):
    """Authenticate and restore an explicit portable-secret backup."""
    path = Path(backup_path)
    if not path.is_file():
        return False, "Portable-secret backup not found."
    if len(str(password or "")) < 1:
        return False, "Password is required."
    try:
        if path.stat().st_size > MAX_BACKUP_BYTES:
            return False, "Portable-secret backup exceeds 16 MB."
        envelope = json.loads(path.read_text(encoding="utf-8"))
        _validate_envelope(envelope)
        kdf = envelope["kdf"]
        salt = _decode_b64(kdf["salt"], expected=16)
        nonce = _decode_b64(envelope["cipher"]["nonce"], expected=12)
        ciphertext = _decode_b64(envelope["ciphertext"], maximum=MAX_BACKUP_BYTES)
        key = _derive_key(str(password), salt, kdf)
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, AAD)
        except InvalidTag:
            return False, "Wrong password or portable-secret backup was modified."
        payload = json.loads(plaintext.decode("utf-8"))
        _validate_payload(payload)

        from .accounts import set_credential
        from .config import get_last_config_error, load_config, save_config
        from .cookies import restore_cookie_text
        from .secrets import apply_config_secrets

        config = apply_config_secrets(load_config(), payload["config_secrets"])
        if not save_config(config):
            return False, (
                "Could not restore config secrets: "
                + (get_last_config_error() or "secure storage unavailable")
            )
        for platform, credential in payload["accounts"].items():
            set_credential(platform, credential)
        ok, message = restore_cookie_text(payload["cookies"])
        if not ok:
            return False, message
        count = len(payload["config_secrets"]) + len(payload["accounts"])
        return True, f"Restored {count} credential value(s); {message}"
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return False, f"Invalid portable-secret backup: {error}"
    except Exception as error:
        return False, f"Portable-secret restore failed: {error}"


def _derive_key(password: str, salt: bytes, kdf: dict[str, Any]) -> bytes:
    if kdf.get("name") != "argon2id":
        raise ValueError("unsupported KDF")
    time_cost = int(kdf.get("time_cost", 0))
    memory_cost = int(kdf.get("memory_cost_kib", 0))
    parallelism = int(kdf.get("parallelism", 0))
    hash_len = int(kdf.get("hash_len", 0))
    if not (1 <= time_cost <= 10):
        raise ValueError("invalid Argon2 time cost")
    if not (8192 <= memory_cost <= 262144):
        raise ValueError("invalid Argon2 memory cost")
    if not (1 <= parallelism <= 16 and hash_len == 32):
        raise ValueError("invalid Argon2 parameters")
    from argon2.low_level import ARGON2_VERSION, Type, hash_secret_raw
    return hash_secret_raw(
        password.encode("utf-8"), salt,
        time_cost=time_cost, memory_cost=memory_cost,
        parallelism=parallelism, hash_len=hash_len,
        type=Type.ID, version=ARGON2_VERSION,
    )


def _validate_envelope(value):
    if not isinstance(value, dict):
        raise ValueError("envelope is not an object")
    if value.get("format") != FORMAT_NAME or value.get("version") != FORMAT_VERSION:
        raise ValueError("unsupported format")
    if value.get("cipher", {}).get("name") != "AES-256-GCM":
        raise ValueError("unsupported cipher")
    if not isinstance(value.get("kdf"), dict):
        raise ValueError("missing KDF")


def _validate_payload(value):
    if not isinstance(value, dict):
        raise ValueError("payload is not an object")
    if value.get("format") != FORMAT_NAME or value.get("version") != FORMAT_VERSION:
        raise ValueError("payload format mismatch")
    if not isinstance(value.get("config_secrets"), dict):
        raise ValueError("invalid config secrets")
    accounts = value.get("accounts")
    if not isinstance(accounts, dict) or not all(
        isinstance(key, str) and isinstance(secret, str)
        for key, secret in accounts.items()
    ):
        raise ValueError("invalid accounts")
    cookies = value.get("cookies")
    if not isinstance(cookies, str) or len(cookies.encode("utf-8")) > 10 * 1024 * 1024:
        raise ValueError("invalid cookies")


def _decode_b64(value, *, expected=None, maximum=None):
    try:
        decoded = base64.b64decode(str(value), validate=True)
    except Exception as error:
        raise ValueError("invalid base64") from error
    if expected is not None and len(decoded) != expected:
        raise ValueError("invalid field length")
    if maximum is not None and len(decoded) > maximum:
        raise ValueError("field too large")
    return decoded


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    if os.name != "nt":
        os.chmod(path, 0o600)
