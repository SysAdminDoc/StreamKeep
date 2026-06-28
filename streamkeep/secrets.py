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
import sys


class SecretStorageError(RuntimeError):
    """Raised when no secure storage backend can protect a new secret."""


# Fields in config.json that should be encrypted when saved
SENSITIVE_FIELDS = frozenset({
    "webhook_url",
    "proxy",
    "companion_token",
    "media_server_token",
    "media_server_url",
})


def protect(plaintext, field_name="", *, allow_insecure_fallback=False):
    """Encrypt *plaintext* using OS-level encryption.

    Returns a prefixed string: ``"dpapi:..."`` on Windows,
    ``"kr:<field_name>"`` via keyring on macOS/Linux. Empty input
    returns empty string. New ``"b64:..."`` values are only written when
    ``allow_insecure_fallback`` is explicitly set.
    """
    if not plaintext:
        return ""

    if sys.platform == "win32":
        result = _dpapi_protect(plaintext)
        if result:
            return result

    if field_name and _keyring_set(field_name, plaintext):
        return f"kr:{field_name}"

    if allow_insecure_fallback:
        return "b64:" + base64.b64encode(plaintext.encode("utf-8")).decode("ascii")

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
    """Encrypt sensitive fields in a config dict in-place.

    Only encrypts fields that are not already protected.
    """
    for key in SENSITIVE_FIELDS:
        val = cfg.get(key, "")
        if val and not is_protected(val):
            cfg[key] = protect(val, field_name=key)


def unprotect_config_fields(cfg):
    """Decrypt sensitive fields in a config dict in-place."""
    for key in SENSITIVE_FIELDS:
        val = cfg.get(key, "")
        if val and is_protected(val):
            cfg[key] = unprotect(val)


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
