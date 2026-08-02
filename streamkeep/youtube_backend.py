"""Optional plugin-backed YouTube cipher and PO-token backend.

The backend contract is deliberately small: a trusted ``youtube_backend``
adapter receives a YouTube URL and returns already-formed yt-dlp
``--extractor-args`` pairs.  This lets a plugin call a remote cipher/token
service without making StreamKeep depend on that service or importing its
client library.  The host validates the returned argv before it reaches a
yt-dlp process and never forwards cookies, headers, or filesystem paths.

Backend use is opt-in.  ``off`` is the default, and ``auto``/``required`` both
fail open when the plugin or its endpoint is unavailable so existing local
yt-dlp behavior remains unchanged.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from typing import Any, Callable

from .plugins import execute_plugin_adapter, load_all_plugins, registered_adapters

REMOTE_BACKEND_URL_KEY = "youtube_remote_backend_url"
REMOTE_BACKEND_MODE_KEY = "youtube_remote_backend_mode"
REMOTE_BACKEND_ID_KEY = "youtube_remote_backend_id"
REMOTE_BACKEND_MODES = frozenset({"off", "auto", "required"})
REMOTE_BACKEND_ADAPTER_TYPE = "youtube_backend"
MAX_BACKEND_URL_CHARS = 512
MAX_BACKEND_ARG_CHARS = 1024
MAX_BACKEND_ARGS = 8
BACKEND_HEALTH_TIMEOUT_SECONDS = 8.0
BACKEND_SOLVE_TIMEOUT_SECONDS = 15.0
_SAFE_DETAIL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)

_autoload_lock = threading.Lock()
_autoload_attempted = False
_solve_cache_lock = threading.Lock()
_solve_cache: dict[tuple[str, str, str, str, str], tuple[float, tuple[str, ...]]] = {}
_SOLVE_CACHE_SECONDS = 30.0
_MAX_SOLVE_CACHE_ENTRIES = 64


def normalize_backend_mode(value: Any) -> str:
    """Return a supported backend mode, defaulting safely to ``off``."""
    mode = str(value or "").strip().lower()
    return mode if mode in REMOTE_BACKEND_MODES else "off"


def normalize_backend_url(value: Any) -> str:
    """Validate an explicit HTTP(S) backend URL without retaining credentials."""
    raw = str(value or "").strip()
    if not raw or len(raw) > MAX_BACKEND_URL_CHARS:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
        hostname = parsed.hostname
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return ""
    if parsed.username or parsed.password or parsed.fragment:
        return ""
    return raw.rstrip("/")


def _display_url(value: str) -> str:
    """Return an endpoint origin suitable for CLI/UI status output."""
    normalized = normalize_backend_url(value)
    if not normalized:
        return ""
    try:
        parsed = urllib.parse.urlsplit(normalized)
        return f"{parsed.scheme.lower()}://{parsed.netloc}"
    except ValueError:
        return ""


def _safe_detail(value: Any, fallback: str = "") -> str:
    detail = str(value or fallback).replace("\r", " ").replace("\n", " ").strip()
    detail = _SAFE_DETAIL_RE.sub("<remote endpoint>", detail)
    return detail[:240]


def _is_youtube_url(value: Any) -> bool:
    try:
        host = urllib.parse.urlsplit(str(value or "").strip()).netloc.lower()
    except ValueError:
        return False
    return (
        host == "youtu.be"
        or host.endswith(".youtu.be")
        or host == "youtube.com"
        or host.endswith(".youtube.com")
    )


def _config_value(config: dict[str, Any] | None, key: str) -> Any:
    if config is not None:
        return config.get(key)
    try:
        from .config import load_config
        return load_config().get(key)
    except Exception:
        return None


def _backend_config(config: dict[str, Any] | None) -> tuple[str, str, str]:
    mode = normalize_backend_mode(_config_value(config, REMOTE_BACKEND_MODE_KEY))
    raw_url = _config_value(config, REMOTE_BACKEND_URL_KEY)
    url = normalize_backend_url(raw_url)
    plugin_id = str(_config_value(config, REMOTE_BACKEND_ID_KEY) or "").strip()
    return mode, url, plugin_id


def _backend_handles(plugin_id: str = ""):
    handles = [
        handle for handle in registered_adapters(plugin_id=plugin_id)
        if handle.spec.adapter_type == REMOTE_BACKEND_ADAPTER_TYPE
    ]
    if handles:
        return handles

    global _autoload_attempted
    if not _autoload_attempted:
        with _autoload_lock:
            if not _autoload_attempted:
                _autoload_attempted = True
                try:
                    load_all_plugins()
                except Exception:
                    pass
        handles = [
            handle for handle in registered_adapters(plugin_id=plugin_id)
            if handle.spec.adapter_type == REMOTE_BACKEND_ADAPTER_TYPE
        ]
    return handles


def _select_backend(plugin_id: str):
    handles = _backend_handles(plugin_id)
    if not handles:
        return None
    return sorted(
        handles,
        key=lambda handle: (handle.spec.plugin_id, handle.spec.entrypoint),
    )[0]


def _validated_extractor_args(value: Any) -> tuple[str, ...] | None:
    """Validate a plugin result before it is appended to a subprocess argv."""
    if not isinstance(value, dict):
        return None
    raw_args = value.get("extractor_args", [])
    if not isinstance(raw_args, list) or len(raw_args) > MAX_BACKEND_ARGS:
        return None
    if len(raw_args) % 2:
        return None
    args: list[str] = []
    for index in range(0, len(raw_args), 2):
        flag, payload = raw_args[index:index + 2]
        if flag != "--extractor-args" or not isinstance(payload, str):
            return None
        if not payload.startswith("youtube:") or len(payload) > MAX_BACKEND_ARG_CHARS:
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in payload):
            return None
        args.extend((flag, payload))
    return tuple(args)


def _request(url: str, mode: str, backend_url: str, reason: str, player_client: str):
    # Keep this request intentionally free of auth material. The plugin can
    # use its own configured remote service, but the host never supplies
    # cookies, request headers, local paths, or arbitrary config.
    return {
        "url": str(url)[:2048],
        "mode": mode,
        "backend_url": backend_url,
        "reason": str(reason or "download")[:96],
        "player_client": str(player_client or "")[:96],
    }


def _log_failure(log_fn: Callable[[str], None] | None, message: str) -> None:
    if log_fn is not None:
        try:
            log_fn(message)
        except Exception:
            pass


def resolve_extractor_args(
    url: str,
    *,
    reason: str = "download",
    config: dict[str, Any] | None = None,
    player_client: str = "",
    log_fn: Callable[[str], None] | None = None,
) -> list[str]:
    """Ask the configured backend for bounded YouTube extractor arguments."""
    mode, backend_url, plugin_id = _backend_config(config)
    if mode == "off" or not _is_youtube_url(url) or not backend_url:
        return []
    handle = _select_backend(plugin_id)
    if handle is None:
        _log_failure(log_fn, "YouTube remote backend is configured but no trusted plugin is loaded.")
        return []

    cache_key = (
        handle.spec.plugin_id, backend_url, str(url), mode,
        str(player_client or "")[:96],
    )
    now = time.monotonic()
    with _solve_cache_lock:
        cached = _solve_cache.get(cache_key)
        if cached and now - cached[0] < _SOLVE_CACHE_SECONDS:
            return list(cached[1])
        _solve_cache.pop(cache_key, None)

    outcome = execute_plugin_adapter(
        handle,
        _request(url, mode, backend_url, reason, player_client),
        operation="solve",
        required_permissions=("network",),
        timeout_seconds=BACKEND_SOLVE_TIMEOUT_SECONDS,
    )
    if not outcome.ok:
        _log_failure(
            log_fn,
            f"YouTube remote backend unavailable ({outcome.code}); continuing without it.",
        )
        return []
    args = _validated_extractor_args(outcome.value)
    if args is None:
        _log_failure(
            log_fn,
            "YouTube remote backend returned an invalid extractor-argument contract; continuing without it.",
        )
        return []
    with _solve_cache_lock:
        if len(_solve_cache) >= _MAX_SOLVE_CACHE_ENTRIES:
            oldest = min(_solve_cache, key=lambda key: _solve_cache[key][0])
            _solve_cache.pop(oldest, None)
        _solve_cache[cache_key] = (now, args)
    return list(args)


def backend_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return redacted backend configuration, plugin, and reachability state."""
    mode, backend_url, plugin_id = _backend_config(config)
    configured = mode != "off"
    result: dict[str, Any] = {
        "configured": configured,
        "mode": mode,
        "backend_url": _display_url(backend_url),
        "plugin_id": "",
        "available": False,
        "reachable": False,
        "usable": False,
        "capabilities": [],
        "detail": "Remote backend is disabled.",
    }
    if mode == "off":
        return result
    if not backend_url:
        result["detail"] = "Remote backend mode is enabled but its URL is invalid or missing."
        return result
    handle = _select_backend(plugin_id)
    if handle is None:
        result["detail"] = "No trusted youtube_backend plugin is loaded."
        return result

    result["available"] = True
    result["plugin_id"] = handle.spec.plugin_id
    outcome = execute_plugin_adapter(
        handle,
        _request("", mode, backend_url, "health", ""),
        operation="health",
        required_permissions=("network",),
        timeout_seconds=BACKEND_HEALTH_TIMEOUT_SECONDS,
    )
    if not outcome.ok or not isinstance(outcome.value, dict):
        result["detail"] = (
            "Remote backend health probe failed; downloads continue without the backend."
        )
        return result
    value = outcome.value
    reachable = value.get("reachable") is True
    result["reachable"] = reachable
    result["usable"] = reachable
    capabilities = value.get("capabilities", [])
    if isinstance(capabilities, list):
        result["capabilities"] = [
            str(capability)[:64]
            for capability in capabilities[:16]
            if isinstance(capability, str) and capability.strip()
        ]
    result["detail"] = _safe_detail(
        value.get("detail"),
        "Remote backend is reachable." if reachable else "Remote backend is not reachable.",
    )
    return result


def reset_backend_state() -> None:
    """Clear bounded process-local state after plugin/config changes."""
    global _autoload_attempted
    with _autoload_lock:
        _autoload_attempted = False
    with _solve_cache_lock:
        _solve_cache.clear()
