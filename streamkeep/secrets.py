"""Encrypted Config Storage — DPAPI on Windows, keyring on macOS/Linux (F79).

Encrypts sensitive config fields (tokens, webhook URLs, proxy creds)
transparently. Non-sensitive fields remain plaintext.

Priority chain:
  1. Windows DPAPI via ctypes (machine-bound encryption)
  2. ``keyring`` library (macOS Keychain, Linux SecretService/kwallet)

Usage::

    from streamkeep.secrets import protect, unprotect
    blob = protect("my_secret_token")    # "dpapi:..." or "kr:<field>"
    plain = unprotect(blob)               # "my_secret_token"
"""

import base64
import copy
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


class SecretStorageError(RuntimeError):
    """Raised when no secure storage backend can protect a new secret."""


# Fields in config.json that must be stored outside config.json.
SENSITIVE_FIELDS = frozenset({
    "webhook_url",
    "proxy",
    "proxy_pool",
    "hf_token",
    "companion_token",
    "media_server_token",
    "media_server_url",
    "token",
    "password",
    "secret",
    "api_key",
    "access_key",
    "secret_key",
    "oauth_token",
    "access_token",
    "refresh_token",
    "ytdlp_arg_templates",
})

SECRET_REF_PREFIX = "secretref:"
_SECRET_FILE_NAME = "credentials.json"
_STORE_LOCK = threading.Lock()
_SECRET_CACHE: dict[str, str] = {}


def _is_sensitive_path(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    key = path[-1].strip().lower().replace("-", "_")
    if key in SENSITIVE_FIELDS:
        return True
    if key.endswith((
        "_token", "_password", "_secret", "_api_key", "_access_key",
        "_secret_key", "_oauth_token", "_refresh_token",
    )):
        return True
    return len(path) >= 2 and path[-2:] == ("media_server", "url")


def _iter_sensitive_values(
    value: Any, path: tuple[str, ...] = ()
):
    if not isinstance(value, dict):
        return
    for raw_key, child in value.items():
        key = str(raw_key)
        child_path = (*path, key)
        if _is_sensitive_path(child_path):
            yield child_path, child
        elif isinstance(child, dict):
            yield from _iter_sensitive_values(child, child_path)


def _get_path(value: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _set_path(value: dict[str, Any], path: tuple[str, ...], replacement: Any) -> None:
    current: Any = value
    for key in path[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(key)
    if isinstance(current, dict):
        current[path[-1]] = replacement


def _secret_id(path: tuple[str, ...]) -> str:
    return "config:" + ".".join(path)


def _encode_secret_value(value: Any) -> str:
    return json.dumps(
        {"version": 1, "value": value}, ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    )


def _decode_secret_value(payload: str) -> Any:
    try:
        decoded = json.loads(payload)
        if isinstance(decoded, dict) and decoded.get("version") == 1:
            return decoded.get("value")
    except (json.JSONDecodeError, TypeError):
        pass
    return payload


def _local_store_path() -> Path:
    from .paths import CONFIG_DIR
    return CONFIG_DIR / _SECRET_FILE_NAME


def _read_local_store() -> dict[str, str]:
    path = _local_store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                str(key): str(value)
                for key, value in data.items()
                if isinstance(value, str)
            }
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {}


def _write_local_store(data: dict[str, str]) -> None:
    path = _local_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def set_secret_value(secret_id: str, value: Any) -> str:
    """Store one typed secret outside config.json and return its reference."""
    secret_id = str(secret_id or "").strip()
    if not secret_id:
        raise ValueError("secret_id is required")
    payload = _encode_secret_value(value)
    with _STORE_LOCK:
        if _SECRET_CACHE.get(secret_id) == payload:
            return SECRET_REF_PREFIX + secret_id
        if _keyring_set(secret_id, payload):
            local = _read_local_store()
            if secret_id in local:
                local.pop(secret_id, None)
                _write_local_store(local)
            _SECRET_CACHE[secret_id] = payload
            return SECRET_REF_PREFIX + secret_id
        if sys.platform == "win32":
            protected = _dpapi_protect(payload)
            if protected:
                local = _read_local_store()
                local[secret_id] = protected
                _write_local_store(local)
                _SECRET_CACHE[secret_id] = payload
                return SECRET_REF_PREFIX + secret_id
    raise SecretStorageError(
        "Secure credential storage is unavailable. Install/configure keyring "
        "or use Windows DPAPI before saving sensitive values."
    )


def get_secret_value(secret_id: str) -> Any:
    """Resolve a typed secret reference from keyring or the DPAPI store."""
    secret_id = str(secret_id or "").strip()
    if not secret_id:
        return None
    payload = _SECRET_CACHE.get(secret_id)
    if payload is None:
        payload = _keyring_get(secret_id)
    if payload is None:
        stored = _read_local_store().get(secret_id, "")
        if stored:
            payload = unprotect(stored)
    if payload is None or payload == "":
        return None
    _SECRET_CACHE[secret_id] = payload
    return _decode_secret_value(payload)


def delete_secret_value(secret_id: str) -> None:
    secret_id = str(secret_id or "").strip()
    if not secret_id:
        return
    with _STORE_LOCK:
        if secret_id not in _SECRET_CACHE and secret_id not in _read_local_store():
            return
        _keyring_delete(secret_id)
        local = _read_local_store()
        if secret_id in local:
            local.pop(secret_id, None)
            _write_local_store(local)
        _SECRET_CACHE.pop(secret_id, None)


def prepare_config_for_storage(
    cfg: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Return a deep config copy with sensitive values replaced by refs."""
    stored = copy.deepcopy(cfg)
    changed = False
    for path, original in list(_iter_sensitive_values(stored)):
        current = _get_path(stored, path)
        secret_id = _secret_id(path)
        if isinstance(current, str) and current.startswith(SECRET_REF_PREFIX):
            continue
        if current in (None, "", [], {}):
            delete_secret_value(secret_id)
            continue
        if isinstance(current, str) and is_protected(current):
            current = unprotect(current)
        reference = set_secret_value(secret_id, current)
        _set_path(stored, path, reference)
        changed = True
    return stored, changed


def resolve_config_secrets(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a deep runtime copy with secret references resolved."""
    resolved = copy.deepcopy(cfg)
    for path, original in list(_iter_sensitive_values(resolved)):
        current = _get_path(resolved, path)
        if isinstance(current, str) and current.startswith(SECRET_REF_PREFIX):
            value = get_secret_value(current[len(SECRET_REF_PREFIX):])
            _set_path(resolved, path, value if value is not None else "")
        elif isinstance(current, str) and is_protected(current):
            _set_path(resolved, path, unprotect(current))
    return resolved


def collect_config_secrets(cfg: dict[str, Any]) -> dict[str, Any]:
    """Collect non-empty runtime config secrets for portable export."""
    collected: dict[str, Any] = {}
    for path, original in _iter_sensitive_values(cfg):
        value = original
        if isinstance(value, str) and value.startswith(SECRET_REF_PREFIX):
            value = get_secret_value(value[len(SECRET_REF_PREFIX):])
        elif isinstance(value, str) and is_protected(value):
            value = unprotect(value)
        if value not in (None, "", [], {}):
            collected[_secret_id(path)] = value
    return collected


def apply_config_secrets(
    cfg: dict[str, Any], secret_values: dict[str, Any]
) -> dict[str, Any]:
    """Apply validated portable secret values to a runtime config copy."""
    result = copy.deepcopy(cfg)
    known_paths = {
        _secret_id(path): path for path, _value in _iter_sensitive_values(result)
    }
    for secret_id, value in secret_values.items():
        path = known_paths.get(str(secret_id))
        if path is None and str(secret_id).startswith("config:"):
            candidate = tuple(str(secret_id)[7:].split("."))
            if candidate and _is_sensitive_path(candidate):
                path = candidate
                current: Any = result
                for key in path[:-1]:
                    if not isinstance(current, dict):
                        current = None
                        break
                    current = current.setdefault(key, {})
        if path:
            _set_path(result, path, value)
    return result


def secret_free_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a normal-export copy with auth state removed."""
    exported = copy.deepcopy(cfg)
    for path, original in list(_iter_sensitive_values(exported)):
        empty: Any = [] if isinstance(original, list) else {}
        if not isinstance(original, (list, dict)):
            empty = ""
        _set_path(exported, path, empty)
    return _sanitize_export_strings(exported)


def reference_only_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Keep existing secure refs while blanking any legacy plaintext."""
    exported = copy.deepcopy(cfg)
    for path, original in list(_iter_sensitive_values(exported)):
        if isinstance(original, str) and original.startswith(SECRET_REF_PREFIX):
            continue
        empty: Any = [] if isinstance(original, list) else {}
        if not isinstance(original, (list, dict)):
            empty = ""
        _set_path(exported, path, empty)
    return exported


def _sanitize_export_strings(value: Any) -> Any:
    from .diagnostics import redact_text
    if isinstance(value, dict):
        return {
            key: _sanitize_export_strings(child) for key, child in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_export_strings(child) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def protect(plaintext, field_name=""):
    """Encrypt *plaintext* using OS-level encryption.

    Returns a prefixed string: ``"dpapi:..."`` on Windows,
    ``"kr:<field_name>"`` via keyring on macOS/Linux. Empty input returns
    empty string. This function always fails closed: if no secure backend is
    available it raises rather than writing a reversible value. The insecure
    ``"b64:..."`` encoding is never produced here — it is only recognized on
    read (``unprotect``) so pre-existing legacy configs can be migrated.
    """
    if not plaintext:
        return ""

    if sys.platform == "win32":
        result = _dpapi_protect(plaintext)
        if result:
            return result

    if field_name and _keyring_set(field_name, plaintext):
        return f"kr:{field_name}"

    raise SecretStorageError(
        "Secure credential storage is unavailable. Install/configure keyring "
        "or use Windows DPAPI before saving sensitive values."
    )


def unprotect(stored):
    """Decrypt a value produced by ``protect()``.

    Handles ``"dpapi:..."``, ``"kr:..."``, and legacy ``"b64:..."`` prefixes.
    Unprefixed values are returned as-is (legacy plaintext).
    """
    if not stored:
        return ""

    if stored.startswith("dpapi:") and sys.platform == "win32":
        result = _dpapi_unprotect(stored[6:])
        if result is not None:
            return result

    if stored.startswith("kr:"):
        result = _keyring_get(stored[3:])
        if result is not None:
            return result

    if stored.startswith("b64:"):
        try:
            return base64.b64decode(stored[4:]).decode("utf-8")
        except Exception:
            return stored

    # Legacy plaintext — return as-is
    return stored


def is_protected(value):
    """Return True if *value* is an encrypted blob (not plaintext)."""
    if not value:
        return False
    return (value.startswith("dpapi:") or value.startswith("kr:")
            or value.startswith("b64:"))


def protect_config_fields(cfg):
    """Replace sensitive config fields in-place with secure references."""
    stored, _changed = prepare_config_for_storage(cfg)
    cfg.clear()
    cfg.update(stored)


def unprotect_config_fields(cfg):
    """Resolve secure config references in-place for runtime use."""
    resolved = resolve_config_secrets(cfg)
    cfg.clear()
    cfg.update(resolved)


# ── DPAPI helpers (Windows only) ────────────────────────────────────

def _dpapi_protect(plaintext):
    """Encrypt using Windows DPAPI. Returns 'dpapi:...' or None on failure."""
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        inp = plaintext.encode("utf-8")
        blob_in = DATA_BLOB(len(inp), ctypes.create_string_buffer(inp, len(inp)))
        blob_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out),
        ):
            enc = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            return "dpapi:" + base64.b64encode(enc).decode("ascii")
    except Exception:
        pass
    return None


_KEYRING_SERVICE = "StreamKeep"


def _keyring_set(field_name, plaintext):
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, field_name, plaintext)
        return True
    except Exception:
        return False


def _keyring_get(field_name):
    try:
        import keyring
        return keyring.get_password(_KEYRING_SERVICE, field_name)
    except Exception:
        return None


def _keyring_delete(field_name):
    try:
        import keyring
        try:
            keyring.delete_password(_KEYRING_SERVICE, field_name)
        except keyring.errors.PasswordDeleteError:
            pass
        return True
    except Exception:
        return False


def _dpapi_unprotect(b64_blob):
    """Decrypt a DPAPI blob. Returns plaintext or None on failure."""
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        raw = base64.b64decode(b64_blob)
        blob_in = DATA_BLOB(len(raw), ctypes.create_string_buffer(raw, len(raw)))
        blob_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out),
        ):
            dec = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            return dec.decode("utf-8")
    except Exception:
        pass
    return None
