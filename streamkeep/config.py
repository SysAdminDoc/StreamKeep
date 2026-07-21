"""Config persistence, bounded config interchange, and logging bridges."""

import copy
import json
import logging
import math
import os
import threading
from dataclasses import dataclass
from datetime import datetime

from .paths import CONFIG_DIR, CONFIG_FILE, LOG_FILE, LOG_FILE_BACKUP, LOG_FILE_MAX_BYTES

# Serializes config writes and log rotation across threads (QTimer polling,
# clipboard monitor, download workers) so two writers can't corrupt the file.
_SAVE_LOCK = threading.Lock()
_LOG_LOCK = threading.Lock()
_LAST_CONFIG_ERROR = ""

CONFIG_EXPORT_FORMAT = "streamkeep-config"
CONFIG_EXPORT_SCHEMA_VERSION = 1
MAX_CONFIG_IMPORT_BYTES = 1024 * 1024
MAX_CONFIG_IMPORT_DEPTH = 8
MAX_CONFIG_IMPORT_NODES = 5000
MAX_CONFIG_IMPORT_CONTAINER_ITEMS = 512
MAX_CONFIG_IMPORT_STRING_CHARS = 16384


class ConfigImportError(ValueError):
    """Raised when an interchange file violates the config import contract."""


@dataclass(frozen=True)
class ConfigImportPreview:
    """Validated import with risky capabilities disabled by default."""

    quarantined_config: dict
    capability_values: dict
    diff_lines: tuple[str, ...]

    @property
    def capabilities(self):
        return tuple(self.capability_values)


_IMPORT_CAPABILITY_INFO = {
    "hooks": (
        "event hooks",
        "Runs the imported shell commands after matching lifecycle events.",
    ),
    "webhook": (
        "webhook notifications",
        "Sends download metadata to the imported remote endpoint.",
    ),
    "proxies": (
        "proxy routing",
        "Routes network traffic through the imported proxy configuration.",
    ),
    "cookie_sources": (
        "automatic cookie access",
        "Allows downloads to read cookies from the imported browser or file path.",
    ),
    "media_server_auto_import": (
        "media-server auto-import",
        "Copies completed media and contacts the imported Plex/Jellyfin/Emby server.",
    ),
    "companion_server": (
        "companion control server",
        "Starts the imported local or LAN browser-control endpoint.",
    ),
    "lifecycle_cleanup": (
        "automatic lifecycle cleanup",
        "Allows the imported retention policy to recycle local media automatically.",
    ),
}

_STRING_CONFIG_KEYS = frozenset({
    "output_dir", "folder_template", "file_template", "webhook_url",
    "rate_limit", "proxy", "cookies_browser", "cookies_file", "theme",
    "visual_density", "visual_accent",
    "language", "whisper_model", "hf_token", "dismissed_update_tag",
    "companion_proxy_origin", "subtitle_languages", "subtitle_convert",
    "sponsorblock_mark", "sponsorblock_remove", "sponsorblock_api",
    "ytdlp_retries", "ytdlp_fragment_retries", "ytdlp_retry_sleep",
    "ytdlp_unavailable_fragments", "ytdlp_throttled_rate",
    "ytdlp_wait_for_video",
    "ytdlp_external_downloader", "ytdlp_aria2c_min_split_size",
    "queue_complete_action",
    "pp_convert_video_format", "pp_convert_video_codec",
    "pp_convert_video_scale", "pp_convert_video_fps",
    "pp_convert_audio_format", "pp_convert_audio_codec",
    "pp_convert_audio_bitrate", "pp_convert_audio_samplerate",
    "pp_bilingual_primary_lang", "pp_bilingual_secondary_lang",
    "pp_bilingual_format", "pp_lrc_lang",
})
_BOOL_CONFIG_KEYS = frozenset({
    "check_duplicates", "write_nfo", "download_twitch_chat",
    "chunk_long_captures", "companion_server_enabled", "companion_bind_lan",
    "companion_allow_private_network",
    "check_for_updates", "capture_live_chat", "render_chat_ass",
    "enable_diarization", "notif_sound", "native_notifications",
    "download_subs", "subtitle_auto",
    "subtitle_embed", "sponsorblock", "capture_youtube_chat",
    "ytdlp_live_from_start", "ytdlp_embed_chapters",
    "ytdlp_embed_metadata", "ytdlp_embed_thumbnail",
    "pp_extract_audio", "pp_normalize_loudness", "pp_reencode_h265",
    "pp_contact_sheet", "pp_split_by_chapter", "pp_remove_silence",
    "pp_convert_video", "pp_convert_audio", "pp_convert_delete_source",
    "pp_bilingual_subs", "pp_lrc_export",
    "disk_monitor_enabled", "disk_auto_pause",
    "first_run_complete",
})
_INT_CONFIG_KEYS = frozenset({
    "segment_idx", "parallel_connections", "parallel_autorecords",
    "max_concurrent_downloads", "chunk_length_secs", "chat_render_width",
    "ytdlp_concurrent_fragments",
    "ytdlp_aria2c_connections", "ytdlp_aria2c_splits",
    "chat_render_height", "chat_render_font_size", "chat_render_msg_duration",
    "chat_render_bg_opacity", "pp_silence_noise_db",
    "disk_warning_gb", "disk_critical_gb", "sponsorblock_delay_hours",
})
_DICT_CONFIG_KEYS = frozenset({
    "bandwidth_rule", "speed_schedule", "quality_defaults", "pp_presets",
    "lifecycle", "media_server", "schedules", "storage_snapshots", "hooks",
    "ytdlp_arg_templates",
})
_LIST_CONFIG_KEYS = frozenset({"recent_urls", "proxy_pool"})
_FORBIDDEN_IMPORT_KEYS = frozenset({
    "history", "monitor_channels", "download_queue", "accounts", "cookies",
})


def load_config():
    """Load the config JSON.

    Falls back to the last-known-good ``config.json.bak`` if the primary
    file is missing or corrupted. Returns ``{}`` on any unrecoverable error.
    """
    for candidate in (
        CONFIG_FILE,
        CONFIG_FILE.with_suffix(".json.bak"),
    ):
        try:
            if not candidate.exists():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                from .secrets import (
                    SecretStorageError,
                    prepare_config_for_storage,
                    resolve_config_secrets,
                )
                runtime = resolve_config_secrets(data)
                try:
                    stored, migrated = prepare_config_for_storage(data)
                except SecretStorageError:
                    return runtime
                if migrated:
                    try:
                        with _SAVE_LOCK:
                            _write_config_payload(stored, rotate_backup=False)
                            try:
                                CONFIG_FILE.with_suffix(".json.bak").unlink(missing_ok=True)
                            except OSError:
                                pass
                    except OSError:
                        pass
                return runtime
        except Exception:
            continue
    return {}


def save_config(cfg):
    """Persist the config dict atomically. Silent on error.

    Writes to `config.json.tmp` then renames into place so a mid-write
    crash leaves the previous config intact. Also rotates a last-known-good
    sibling `config.json.bak` before each successful replace.
    """
    global _LAST_CONFIG_ERROR
    from .secrets import prepare_config_for_storage
    with _SAVE_LOCK:
        try:
            stored, _changed = prepare_config_for_storage(cfg)
            _write_config_payload(stored, rotate_backup=True)
            _LAST_CONFIG_ERROR = ""
            return True
        except Exception as error:
            _LAST_CONFIG_ERROR = str(error)
            # Best-effort cleanup of stale tmp on failure.
            try:
                tmp = CONFIG_FILE.with_suffix(".json.tmp")
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            return False


def get_last_config_error():
    return _LAST_CONFIG_ERROR


def export_config(cfg):
    """Return a versioned config-only envelope with auth state removed."""
    from . import VERSION
    from .secrets import secret_free_config
    exported = secret_free_config(cfg)
    for key in _FORBIDDEN_IMPORT_KEYS:
        exported.pop(key, None)
    return {
        "format": CONFIG_EXPORT_FORMAT,
        "schema_version": CONFIG_EXPORT_SCHEMA_VERSION,
        "exported_by": VERSION,
        "config": exported,
    }


def prepare_config_import(raw_data, current_config):
    """Validate an interchange payload and quarantine executable behavior.

    This function has no persistence or runtime side effects. Callers can show
    ``diff_lines`` and collect explicit capability approvals before passing the
    preview to :func:`finalize_config_import` and saving the returned config.
    """
    envelope = _parse_config_import_envelope(raw_data)
    imported = copy.deepcopy(envelope["config"])
    _validate_config_schema(imported)
    quarantined, capability_values = _quarantine_import_capabilities(imported)
    diff_lines = tuple(_build_config_diff(current_config, quarantined))
    return ConfigImportPreview(
        quarantined_config=quarantined,
        capability_values=capability_values,
        diff_lines=diff_lines,
    )


def finalize_config_import(preview, enabled_capabilities=()):
    """Return an import candidate with only explicitly approved risks enabled."""
    if not isinstance(preview, ConfigImportPreview):
        raise TypeError("preview must be a ConfigImportPreview")
    requested = {str(value) for value in enabled_capabilities}
    available = set(preview.capability_values)
    unknown = requested - available
    if unknown:
        raise ConfigImportError(
            "unknown import capability: " + ", ".join(sorted(unknown))
        )
    result = copy.deepcopy(preview.quarantined_config)
    for capability in preview.capabilities:
        if capability not in requested:
            continue
        for path, value in preview.capability_values[capability]:
            _set_import_path(result, path, copy.deepcopy(value))
    return result


def get_import_capability_info(capability):
    """Return the user-facing label and consequence for one capability."""
    return _IMPORT_CAPABILITY_INFO.get(
        str(capability), (str(capability), "Enables imported automation.")
    )


def _parse_config_import_envelope(raw_data):
    if isinstance(raw_data, str):
        encoded = raw_data.encode("utf-8")
    elif isinstance(raw_data, (bytes, bytearray)):
        encoded = bytes(raw_data)
    else:
        raise ConfigImportError("config import must be UTF-8 JSON data")
    if len(encoded) > MAX_CONFIG_IMPORT_BYTES:
        raise ConfigImportError("config import exceeds the 1 MB limit")
    try:
        text = encoded.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ConfigImportError("config import is not valid UTF-8") from error

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ConfigImportError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ConfigImportError(f"non-finite JSON number: {value}")

    try:
        envelope = json.loads(
            text, object_pairs_hook=unique_object, parse_constant=reject_constant,
        )
    except ConfigImportError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ConfigImportError(f"invalid config JSON: {error}") from error
    if not isinstance(envelope, dict):
        raise ConfigImportError("config import root is not an object")
    if envelope.get("format") != CONFIG_EXPORT_FORMAT:
        raise ConfigImportError("not a StreamKeep config export")
    version = envelope.get("schema_version")
    if isinstance(version, bool) or version != CONFIG_EXPORT_SCHEMA_VERSION:
        raise ConfigImportError(f"unsupported config schema version: {version!r}")
    allowed_envelope_keys = {"format", "schema_version", "exported_by", "config"}
    unknown = set(envelope) - allowed_envelope_keys
    if unknown:
        raise ConfigImportError(
            "unsupported envelope field(s): " + ", ".join(sorted(unknown))
        )
    if not isinstance(envelope.get("config"), dict):
        raise ConfigImportError("config field is not an object")
    exported_by = envelope.get("exported_by", "")
    if not isinstance(exported_by, str) or len(exported_by) > 64:
        raise ConfigImportError("exported_by must be a short string")
    counter = [0]
    _validate_json_tree(envelope, path=(), depth=0, counter=counter)
    return envelope


def _validate_json_tree(value, *, path, depth, counter):
    if depth > MAX_CONFIG_IMPORT_DEPTH:
        raise ConfigImportError(
            f"config nesting exceeds {MAX_CONFIG_IMPORT_DEPTH} levels at "
            + (_format_import_path(path) or "root")
        )
    counter[0] += 1
    if counter[0] > MAX_CONFIG_IMPORT_NODES:
        raise ConfigImportError(
            f"config contains more than {MAX_CONFIG_IMPORT_NODES} values"
        )
    if isinstance(value, dict):
        if len(value) > MAX_CONFIG_IMPORT_CONTAINER_ITEMS:
            raise ConfigImportError("config object contains too many fields")
        for key, child in value.items():
            if not isinstance(key, str):
                raise ConfigImportError("config object keys must be strings")
            if not key or len(key) > 128 or any(ord(char) < 32 for char in key):
                raise ConfigImportError("config contains an invalid object key")
            _validate_json_tree(
                child, path=(*path, key), depth=depth + 1, counter=counter,
            )
        return
    if isinstance(value, list):
        if len(value) > MAX_CONFIG_IMPORT_CONTAINER_ITEMS:
            raise ConfigImportError("config list contains too many items")
        for index, child in enumerate(value):
            _validate_json_tree(
                child, path=(*path, str(index)), depth=depth + 1,
                counter=counter,
            )
        return
    if isinstance(value, str):
        if len(value) > MAX_CONFIG_IMPORT_STRING_CHARS:
            raise ConfigImportError(
                f"config string is too long at {_format_import_path(path)}"
            )
        if "\x00" in value:
            raise ConfigImportError(
                f"config string contains NUL at {_format_import_path(path)}"
            )
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigImportError("config contains a non-finite number")
    if value is not None and not isinstance(value, (bool, int, float)):
        raise ConfigImportError(
            f"unsupported config value at {_format_import_path(path)}"
        )


def _validate_config_schema(config):
    forbidden = _FORBIDDEN_IMPORT_KEYS.intersection(config)
    if forbidden:
        raise ConfigImportError(
            "config imports cannot contain library state: "
            + ", ".join(sorted(forbidden))
        )
    for key in _STRING_CONFIG_KEYS.intersection(config):
        if not isinstance(config[key], str):
            raise ConfigImportError(f"config.{key} must be a string")
    for key in _BOOL_CONFIG_KEYS.intersection(config):
        if not isinstance(config[key], bool):
            raise ConfigImportError(f"config.{key} must be true or false")
    for key in _INT_CONFIG_KEYS.intersection(config):
        if isinstance(config[key], bool) or not isinstance(config[key], int):
            raise ConfigImportError(f"config.{key} must be an integer")
    for key in _DICT_CONFIG_KEYS.intersection(config):
        if not isinstance(config[key], dict):
            raise ConfigImportError(f"config.{key} must be an object")
    for key in _LIST_CONFIG_KEYS.intersection(config):
        if not isinstance(config[key], list):
            raise ConfigImportError(f"config.{key} must be a list")
    _validate_hooks_schema(config.get("hooks", {}))
    _validate_proxy_pool_schema(config.get("proxy_pool", []))
    _validate_media_server_schema(config.get("media_server", {}))
    _validate_lifecycle_schema(config.get("lifecycle", {}))
    _validate_ytdlp_templates_schema(config.get("ytdlp_arg_templates", {}))
    _reject_imported_secret_handles(config)


def _reject_imported_secret_handles(config):
    from .secrets import _iter_sensitive_values

    def contains_handle(value):
        if isinstance(value, str):
            return value.startswith(("secretref:", "dpapi:", "kr:", "b64:"))
        if isinstance(value, dict):
            return any(contains_handle(child) for child in value.values())
        if isinstance(value, list):
            return any(contains_handle(child) for child in value)
        return False

    for path, value in _iter_sensitive_values(config):
        if contains_handle(value):
            raise ConfigImportError(
                "config imports cannot contain local secret handles at "
                + _format_import_path(path)
            )


def _validate_hooks_schema(hooks):
    if not hooks:
        return
    from .hooks import HOOK_EVENTS, MAX_LEGACY_HOOK_CHARS, normalize_hook
    allowed = set(HOOK_EVENTS)
    for event, value in hooks.items():
        if event not in allowed:
            raise ConfigImportError(f"unsupported hook event: {event}")
        kind, data = normalize_hook(value)
        if kind == "invalid":
            raise ConfigImportError(f"hook {event} is invalid: {data}")
        if kind == "legacy" and len(str(value)) > MAX_LEGACY_HOOK_CHARS:
            raise ConfigImportError(
                f"hook {event} exceeds {MAX_LEGACY_HOOK_CHARS} characters"
            )


def _validate_proxy_pool_schema(entries):
    if len(entries) > 64:
        raise ConfigImportError("proxy_pool contains more than 64 entries")
    allowed = {"url", "platforms", "enabled", "label"}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigImportError(f"proxy_pool[{index}] must be an object")
        unknown = set(entry) - allowed
        if unknown:
            raise ConfigImportError(
                f"proxy_pool[{index}] has unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        if not isinstance(entry.get("url", ""), str):
            raise ConfigImportError(f"proxy_pool[{index}].url must be a string")
        if not isinstance(entry.get("enabled", True), bool):
            raise ConfigImportError(f"proxy_pool[{index}].enabled must be boolean")
        platforms = entry.get("platforms", [])
        if not isinstance(platforms, list) or len(platforms) > 32 or not all(
            isinstance(platform, str) for platform in platforms
        ):
            raise ConfigImportError(
                f"proxy_pool[{index}].platforms must be a short string list"
            )
        if not isinstance(entry.get("label", ""), str):
            raise ConfigImportError(f"proxy_pool[{index}].label must be a string")


def _validate_media_server_schema(value):
    if not value:
        return
    allowed = {
        "enabled", "server_type", "url", "token", "library_id", "library_path",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ConfigImportError(
            "media_server has unsupported fields: " + ", ".join(sorted(unknown))
        )
    if not isinstance(value.get("enabled", False), bool):
        raise ConfigImportError("media_server.enabled must be boolean")
    for key in allowed - {"enabled"}:
        if key in value and not isinstance(value[key], str):
            raise ConfigImportError(f"media_server.{key} must be a string")


def _validate_lifecycle_schema(value):
    if not value:
        return
    allowed = {
        "enabled", "max_days", "max_total_gb", "delete_watched",
        "favorites_exempt", "keep_last_per_source",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ConfigImportError(
            "lifecycle has unsupported fields: " + ", ".join(sorted(unknown))
        )
    for key in ("enabled", "delete_watched", "favorites_exempt"):
        if key in value and not isinstance(value[key], bool):
            raise ConfigImportError(f"lifecycle.{key} must be boolean")
    for key in ("max_days", "max_total_gb", "keep_last_per_source"):
        if key in value and (
            isinstance(value[key], bool) or not isinstance(value[key], (int, float))
        ):
            raise ConfigImportError(f"lifecycle.{key} must be numeric")


def _validate_ytdlp_templates_schema(value):
    if not value:
        return
    from .download_options import normalize_ytdlp_arg_templates
    try:
        normalize_ytdlp_arg_templates(value)
    except ValueError as error:
        raise ConfigImportError(str(error)) from error


def _quarantine_import_capabilities(config):
    result = copy.deepcopy(config)
    held = {}

    def hold(capability, paths, *, active):
        if not active:
            return
        held[capability] = [
            (path, copy.deepcopy(_get_import_path(config, path))) for path in paths
        ]

    hooks = config.get("hooks", {})
    hold("hooks", [("hooks",)], active=bool(hooks))
    if hooks:
        result["hooks"] = {}

    webhook = config.get("webhook_url", "")
    hold("webhook", [("webhook_url",)], active=bool(str(webhook).strip()))
    if webhook:
        result["webhook_url"] = ""

    proxy_paths = [
        (key,) for key in ("proxy", "proxy_pool") if key in config
    ]
    proxy_active = bool(config.get("proxy") or config.get("proxy_pool"))
    hold("proxies", proxy_paths, active=proxy_active)
    if proxy_active:
        result["proxy"] = ""
        result["proxy_pool"] = []

    cookie_paths = [
        (key,) for key in ("cookies_browser", "cookies_file") if key in config
    ]
    cookie_active = bool(config.get("cookies_browser") or config.get("cookies_file"))
    hold("cookie_sources", cookie_paths, active=cookie_active)
    if cookie_active:
        result["cookies_browser"] = ""
        result["cookies_file"] = ""

    media_server = config.get("media_server", {})
    media_active = bool(media_server.get("enabled")) if isinstance(media_server, dict) else False
    media_path = ("media_server", "enabled")
    hold("media_server_auto_import", [media_path], active=media_active)
    if media_active:
        result.setdefault("media_server", {})["enabled"] = False

    companion_paths = [
        (key,) for key in (
            "companion_server_enabled", "companion_bind_lan", "companion_proxy_origin"
        )
        if key in config
    ]
    companion_active = bool(
        config.get("companion_server_enabled") or config.get("companion_bind_lan")
    )
    hold("companion_server", companion_paths, active=companion_active)
    if companion_active:
        result["companion_server_enabled"] = False
        result["companion_bind_lan"] = False

    lifecycle = config.get("lifecycle", {})
    lifecycle_active = bool(lifecycle.get("enabled")) if isinstance(lifecycle, dict) else False
    lifecycle_path = ("lifecycle", "enabled")
    hold("lifecycle_cleanup", [lifecycle_path], active=lifecycle_active)
    if lifecycle_active:
        result.setdefault("lifecycle", {})["enabled"] = False

    return result, held


def _get_import_path(value, path):
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _set_import_path(value, path, replacement):
    current = value
    for key in path[:-1]:
        if not isinstance(current, dict):
            raise ConfigImportError(
                f"cannot activate {_format_import_path(path)}"
            )
        current = current.setdefault(key, {})
    current[path[-1]] = replacement


def _build_config_diff(before, after, *, limit=100):
    lines = []

    def walk(old, new, path):
        if len(lines) >= limit or old == new:
            return
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(set(old) | set(new), key=str):
                if len(lines) >= limit:
                    return
                child_path = (*path, str(key))
                if key not in old:
                    lines.append(
                        f"+ {_format_import_path(child_path)}: "
                        f"{_summarize_import_value(new[key], child_path)}"
                    )
                elif key not in new:
                    lines.append(f"- {_format_import_path(child_path)}")
                else:
                    walk(old[key], new[key], child_path)
            return
        lines.append(
            f"~ {_format_import_path(path)}: "
            f"{_summarize_import_value(old, path)} -> "
            f"{_summarize_import_value(new, path)}"
        )

    walk(before if isinstance(before, dict) else {}, after, ())
    if len(lines) >= limit:
        lines.append(f"... additional changes omitted after {limit} entries")
    return lines


def _summarize_import_value(value, path):
    sensitive_roots = {
        "hooks", "webhook_url", "proxy", "proxy_pool", "cookies_browser",
        "cookies_file", "hf_token", "media_server",
    }
    if path and path[0] in sensitive_roots:
        if value in (None, "", [], {}):
            return "disabled"
        return "<configured>"
    if isinstance(value, dict):
        return f"object ({len(value)} fields)"
    if isinstance(value, list):
        return f"list ({len(value)} items)"
    if isinstance(value, str):
        from .diagnostics import redact_text
        safe = redact_text(value).replace("\n", " ").replace("\r", " ")
        if len(safe) > 80:
            safe = safe[:77] + "..."
        return json.dumps(safe, ensure_ascii=False)
    return repr(value)


def _format_import_path(path):
    return ".".join(path)


def _write_config_payload(stored, *, rotate_backup):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    payload = json.dumps(stored, indent=2, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    if rotate_backup and CONFIG_FILE.exists():
        bak = CONFIG_FILE.with_suffix(".json.bak")
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                from .secrets import reference_only_config
                safe_existing = reference_only_config(existing)
                bak_tmp = bak.with_suffix(".bak.tmp")
                with open(bak_tmp, "w", encoding="utf-8") as handle:
                    json.dump(safe_existing, handle, indent=2, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(bak_tmp, bak)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    os.replace(tmp, CONFIG_FILE)


def write_log_line(msg):
    """Append a timestamped line to the rotating log file.
    Rotates streamkeep.log -> streamkeep.log.1 when it exceeds the cap."""
    with _LOG_LOCK:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            try:
                if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_FILE_MAX_BYTES:
                    if LOG_FILE_BACKUP.exists():
                        try:
                            LOG_FILE_BACKUP.unlink()
                        except OSError:
                            pass
                    try:
                        LOG_FILE.rename(LOG_FILE_BACKUP)
                    except (FileNotFoundError, OSError):
                        pass
            except OSError:
                pass
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            from .diagnostics import redact_text
            safe_message = redact_text(str(msg or ""))
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {safe_message}\n")
        except Exception:
            pass


# ── Structured logging bridge ──────────────────────────────────────

_LEVEL_LABELS = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARN",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRIT",
}


class GuiLogHandler(logging.Handler):
    """Forwards logging records to a callable (e.g. main window ``_log``).

    Install via ``install_gui_logging(callback)`` — attaches to the
    ``streamkeep`` root logger and writes through to the rotating log
    file as well.  Duplicate suppression: if a record with the same
    message was emitted within the last second, it is counted but not
    forwarded until the burst ends.

    The callback is invoked via QTimer.singleShot(0, ...) so it runs on
    the Qt main thread regardless of which thread emitted the log record.
    """

    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        self._last_msg = ""
        self._last_time = 0.0
        self._suppress_count = 0

    def _invoke_on_main_thread(self, text):
        try:
            from PyQt6.QtCore import QCoreApplication, QThread, QTimer
            app = QCoreApplication.instance()
            if app is None or QThread.currentThread() is app.thread():
                self._callback(text)
            else:
                QTimer.singleShot(0, lambda: self._callback(text))
        except Exception:
            self._callback(text)

    def emit(self, record):
        try:
            module = record.name.rsplit(".", 1)[-1] if record.name else ""
            level = _LEVEL_LABELS.get(record.levelno, str(record.levelno))
            msg = self.format(record) if self.formatter else record.getMessage()

            now = record.created
            if msg == self._last_msg and (now - self._last_time) < 1.0:
                self._suppress_count += 1
                self._last_time = now
                return
            if self._suppress_count > 0:
                self._invoke_on_main_thread(
                    f"[{module}] {level}: (repeated {self._suppress_count}x)"
                )
                self._suppress_count = 0

            self._last_msg = msg
            self._last_time = now
            formatted = f"[{module}] {level}: {msg}"
            self._invoke_on_main_thread(formatted)
            write_log_line(formatted)
        except Exception:
            pass


class FileLogHandler(logging.Handler):
    """Writes logging records to the rotating log file only."""

    def emit(self, record):
        try:
            msg = self.format(record) if self.formatter else record.getMessage()
            module = record.name.rsplit(".", 1)[-1] if record.name else ""
            level = _LEVEL_LABELS.get(record.levelno, str(record.levelno))
            write_log_line(f"[{module}] {level}: {msg}")
        except Exception:
            pass


def install_gui_logging(callback, *, level=logging.INFO):
    """Attach a ``GuiLogHandler`` to the ``streamkeep`` root logger.

    Call once at app startup. Returns the handler so it can be removed
    later if needed.
    """
    root = logging.getLogger("streamkeep")
    root.setLevel(level)
    for h in list(root.handlers):
        if isinstance(h, (GuiLogHandler, FileLogHandler)):
            root.removeHandler(h)
    handler = GuiLogHandler(callback)
    root.addHandler(handler)
    return handler


def install_file_logging(*, level=logging.WARNING):
    """Attach a ``FileLogHandler`` to the ``streamkeep`` root logger.

    Used in CLI/headless mode where there is no GUI log panel.
    """
    root = logging.getLogger("streamkeep")
    root.setLevel(level)
    for h in list(root.handlers):
        if isinstance(h, FileLogHandler):
            root.removeHandler(h)
    handler = FileLogHandler()
    root.addHandler(handler)
    return handler
