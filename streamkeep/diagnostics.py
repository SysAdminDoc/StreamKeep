"""Privacy-redacted support snapshot export.

Produces a ZIP containing app/version info, dependency checks, redacted
config, DB integrity summary, recent log/crash tails, and packaging/server
state — without leaking tokens, cookies, API keys, passwords, or
credential-store payloads.
"""

import json
import os
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from . import VERSION
from .paths import CONFIG_DIR, LOG_FILE, CRASH_LOG, PORTABLE

_REDACT_PATTERNS = [
    re.compile(r"(bearer\s+)\S+", re.I),
    re.compile(r"(token[\"'\s:=]+)\S+", re.I),
    re.compile(r"(password[\"'\s:=]+)\S+", re.I),
    re.compile(r"(api[_-]?key[\"'\s:=]+)\S+", re.I),
    re.compile(r"(secret[\"'\s:=]+)\S+", re.I),
    re.compile(r"(cookie[\"'\s:=]+)\S+", re.I),
    re.compile(r"(dpapi:)\S+", re.I),
    re.compile(r"(kr:)\S+", re.I),
    re.compile(
        r"([?&](?:token|sig|signature|key|api[_-]?key|auth|oauth)[^=&#\s]*=)"
        r"[^&#\s]+",
        re.I,
    ),
    re.compile(r"(://[^/\s:@]+:)[^@/\s]+@", re.I),
    re.compile(
        r"(https://(?:discord(?:app)?\.com)/api/webhooks/\d+/)[^\s/?]+",
        re.I,
    ),
    re.compile(r"(https://hooks\.slack\.com/services/)[^\s]+", re.I),
]

_SENSITIVE_CONFIG_KEYS = frozenset({
    "webhook_url", "proxy", "proxy_pool", "hf_token", "companion_token",
    "media_server_token", "media_server_url",
    "youtube_api_key", "twitch_oauth_token",
    "token", "api_key", "secret", "password",
    "oauth_token", "access_token", "refresh_token",
    "access_key", "secret_key",
})


def redact_text(text):
    """Redact known secret patterns from a text string."""
    result = text
    for pat in _REDACT_PATTERNS:
        result = pat.sub(lambda m: m.group(1) + "***REDACTED***", result)
    return result


def redact_config(cfg):
    """Return a deep copy of the config dict with sensitive values masked."""
    out = {}
    for key, value in cfg.items():
        if key in _SENSITIVE_CONFIG_KEYS:
            out[key] = "***REDACTED***" if value else ""
        elif isinstance(value, dict):
            out[key] = redact_config(value)
        elif isinstance(value, list):
            out[key] = [
                redact_config(item) if isinstance(item, dict)
                else redact_text(item) if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            out[key] = redact_text(value) if isinstance(value, str) else value
    return out


def _tail_file(path, max_lines=100, max_bytes=32768):
    """Read the last N lines of a file, capped by byte size."""
    try:
        p = Path(path)
        if not p.is_file():
            return ""
        size = p.stat().st_size
        read_bytes = min(size, max_bytes)
        with open(p, "rb") as f:
            if read_bytes < size:
                f.seek(size - read_bytes)
            raw = f.read(read_bytes)
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()[-max_lines:]
        return "\n".join(lines)
    except OSError:
        return ""


def _runtime_info():
    info = {
        "streamkeep_version": VERSION,
        "python_version": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "frozen": getattr(sys, "frozen", False),
        "portable": PORTABLE,
        "config_dir": str(CONFIG_DIR),
    }
    try:
        import subprocess
        from .paths import _CREATE_NO_WINDOW
        r = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        first_line = r.stdout.decode("utf-8", errors="replace").split("\n")[0]
        info["ffmpeg"] = first_line.strip() if r.returncode == 0 else "not found"
    except Exception:
        info["ffmpeg"] = "not available"
    try:
        import yt_dlp.version
        info["yt_dlp_version"] = getattr(yt_dlp.version, "__version__", "unknown")
    except (ImportError, AttributeError):
        info["yt_dlp_version"] = "not installed"
    return info


def create_diagnostic_snapshot(output_path):
    """Create a redacted diagnostic ZIP at *output_path*.

    Returns (ok, message).
    """
    if not output_path:
        return False, "No output path specified"
    try:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            zf.writestr("runtime.json", json.dumps(
                _runtime_info(), indent=2, ensure_ascii=False
            ))

            try:
                from .config import load_config
                cfg = load_config()
                zf.writestr("config_redacted.json", json.dumps(
                    redact_config(cfg), indent=2, ensure_ascii=False
                ))
            except Exception as e:
                zf.writestr("config_redacted.json", json.dumps(
                    {"error": str(e)}, indent=2
                ))

            try:
                from . import db
                diag = db.db_diagnostics()
                zf.writestr("db_diagnostics.json", json.dumps(
                    diag, indent=2, ensure_ascii=False
                ))
            except Exception as e:
                zf.writestr("db_diagnostics.json", json.dumps(
                    {"error": str(e)}, indent=2
                ))

            log_tail = redact_text(_tail_file(LOG_FILE))
            if log_tail:
                zf.writestr("streamkeep_log_tail.txt", log_tail)

            crash_tail = redact_text(_tail_file(CRASH_LOG))
            if crash_tail:
                zf.writestr("crash_log_tail.txt", crash_tail)

            zf.writestr("_snapshot_meta.json", json.dumps({
                "version": VERSION,
                "created": datetime.now().isoformat(timespec="seconds"),
            }, indent=2))

        size_kb = os.path.getsize(output_path) / 1024
        return True, f"Diagnostic snapshot: {size_kb:.0f} KB"
    except (OSError, zipfile.BadZipFile) as e:
        return False, f"Snapshot failed: {e}"
