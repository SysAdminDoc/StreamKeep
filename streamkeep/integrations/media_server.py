"""Media-server library layouts, playlists, and watched-state import.

The filesystem side of the integration is deliberately deterministic and
testable without a running Plex/Jellyfin/Emby instance.  Network operations
are small, token-redacting adapters layered on top of those pure plans.

Config keys (under ``config["media_server"]``):
    enabled, server_type (plex|jellyfin|emby|kodi), url, token,
    library_path, library_id, layout_mode (seasoned|flat), portable_m3u,
    native_playlist, playlist_name, sidecar_profile, upload_profile_id,
    upload_after_import, watched_user_id, watched_user_name.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException


# Supported server types. Kodi is file-based: NFO files and M3U playlists are
# native to a Kodi library, while the other three types also support a remote
# playlist API.
SERVER_TYPES = ["plex", "jellyfin", "emby", "kodi"]
LAYOUT_MODES = ["seasoned", "flat"]
DEFAULT_LAYOUT_MODE = "seasoned"
DEFAULT_PLAYLIST_NAME = "StreamKeep"
SIDECAR_PROFILES = ["jellyfin", "plex", "archive", "full", "none"]
_MEDIA_EXTS = (
    ".mp4", ".mkv", ".ts", ".webm", ".flv", ".mov", ".avi", ".m4v",
    ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac",
)


def _safe_name(value: object, max_len: int = 80) -> str:
    """Sanitize a value for use as one filesystem path component."""
    if not value:
        return "Unknown"
    text = str(value).strip()
    bad = '<>:"/\\|?*'
    out = "".join(
        "_" if char in bad or ord(char) < 32 else char for char in text
    )
    out = out[:max_len].rstrip(". ")
    return out or "Unknown"


def _safe_playlist_name(value: object) -> str:
    """Return a single safe playlist filename with an ``.m3u`` suffix."""
    name = _safe_name(value, 120)
    if name.lower().endswith(".m3u"):
        return name
    return f"{name}.m3u"


def normalize_media_server_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize media-server settings while preserving unknown app fields.

    This helper is used at runtime, where old config files are expected.  The
    import validator remains stricter and rejects unknown fields.
    """
    raw = dict(config or {})
    server_type = str(raw.get("server_type", "plex") or "plex").lower()
    if server_type not in SERVER_TYPES:
        server_type = "plex"
    layout_mode = str(raw.get("layout_mode", DEFAULT_LAYOUT_MODE) or DEFAULT_LAYOUT_MODE).lower()
    if layout_mode not in LAYOUT_MODES:
        layout_mode = DEFAULT_LAYOUT_MODE
    result = dict(raw)
    result.update({
        "enabled": bool(raw.get("enabled", False)),
        "server_type": server_type,
        "url": str(raw.get("url", "") or "").strip(),
        "token": str(raw.get("token", "") or "").strip(),
        "library_id": str(raw.get("library_id", "") or "").strip(),
        "library_path": str(raw.get("library_path", "") or "").strip(),
        "layout_mode": layout_mode,
        "portable_m3u": bool(raw.get("portable_m3u", False)),
        "native_playlist": bool(raw.get("native_playlist", False)),
        "sidecar_profile": str(raw.get("sidecar_profile", "") or "").strip().lower(),
        "upload_profile_id": str(raw.get("upload_profile_id", "") or "").strip(),
        "upload_after_import": bool(raw.get("upload_after_import", False)),
        "playlist_name": str(
            raw.get("playlist_name", DEFAULT_PLAYLIST_NAME) or DEFAULT_PLAYLIST_NAME
        ).strip() or DEFAULT_PLAYLIST_NAME,
        "watched_user_id": str(raw.get("watched_user_id", "") or "").strip(),
        "watched_user_name": str(raw.get("watched_user_name", "") or "").strip(),
    })
    return result


def _year_from_info(info: object | None) -> str:
    start_time = str(getattr(info, "start_time", "") or "") if info else ""
    match = re.match(r"(\d{4})", start_time)
    return match.group(1) if match else datetime.now().strftime("%Y")


def _next_episode(season_dir: str | os.PathLike[str]) -> int:
    """Scan *season_dir* and return the next S/E episode number."""
    path = os.fspath(season_dir)
    if not os.path.isdir(path):
        return 1
    existing = []
    for entry in os.scandir(path):
        if not entry.is_file():
            continue
        match = re.search(r"S\d+E(\d+)", entry.name, re.IGNORECASE)
        if match:
            existing.append(int(match.group(1)))
    return max(existing, default=0) + 1


def _find_media(out_dir: str | os.PathLike[str]) -> str | None:
    """Return the largest audio/video file in *out_dir*."""
    best: str | None = None
    best_size = 0
    path = os.fspath(out_dir)
    if not os.path.isdir(path):
        return None
    for entry in os.scandir(path):
        if not entry.is_file() or not entry.name.lower().endswith(_MEDIA_EXTS):
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        if size > best_size:
            best, best_size = entry.path, size
    return best


def _find_video(out_dir: str | os.PathLike[str]) -> str | None:
    """Backward-compatible alias for older integrations/tests."""
    return _find_media(out_dir)


@dataclass(frozen=True)
class MediaImportPlan:
    """A collision-free, server-independent import plan."""

    media_path: str
    destination: str
    nfo_path: str
    library_path: str
    channel: str
    title: str
    year: str
    episode: int
    layout_mode: str

    @property
    def relative_path(self) -> str:
        return os.path.relpath(self.destination, self.library_path).replace(os.sep, "/")


def plan_media_import(
    config: dict[str, Any],
    out_dir: str | os.PathLike[str],
    info: object | None = None,
) -> MediaImportPlan | None:
    """Plan a safe per-channel library path without touching the filesystem."""
    media = _find_media(out_dir)
    library_path = str(config.get("library_path", "") or "").strip()
    if not media or not library_path:
        return None

    channel = _safe_name(getattr(info, "channel", "") if info else "")
    title = _safe_name(
        (getattr(info, "title", "") if info else "") or os.path.basename(os.fspath(out_dir))
    )
    year = _year_from_info(info)
    layout_mode = str(config.get("layout_mode", DEFAULT_LAYOUT_MODE) or DEFAULT_LAYOUT_MODE).lower()
    if layout_mode not in LAYOUT_MODES:
        layout_mode = DEFAULT_LAYOUT_MODE

    channel_dir = os.path.join(library_path, channel)
    if layout_mode == "seasoned":
        target_dir = os.path.join(channel_dir, f"Season {year}")
    else:
        target_dir = channel_dir

    episode = _next_episode(target_dir)
    extension = os.path.splitext(media)[1].lower()
    while True:
        filename = f"{channel} - S{year}E{episode:02d} - {title}{extension}"
        destination = os.path.join(target_dir, filename)
        if not os.path.exists(destination):
            break
        episode += 1
    nfo_path = os.path.join(target_dir, f"{os.path.splitext(filename)[0]}.nfo")
    return MediaImportPlan(
        media_path=os.path.abspath(media),
        destination=os.path.abspath(destination),
        nfo_path=os.path.abspath(nfo_path),
        library_path=os.path.abspath(library_path),
        channel=channel,
        title=title,
        year=year,
        episode=episode,
        layout_mode=layout_mode,
    )


def _sidecar_profile(config: dict[str, Any]) -> str:
    """Choose a deterministic sidecar profile for a media-server export."""
    requested = str(config.get("sidecar_profile", "") or "").strip().lower()
    if requested in SIDECAR_PROFILES:
        return requested
    server_type = str(config.get("server_type", "plex") or "plex").lower()
    return {
        "jellyfin": "jellyfin",
        "emby": "jellyfin",
        "plex": "plex",
        "kodi": "full",
    }.get(server_type, "full")


def preview_media_import(
    config: dict[str, Any],
    out_dir: str | os.PathLike[str],
    info: object | None = None,
) -> dict[str, Any]:
    """Return a side-effect-free media-server layout preview."""
    cfg = normalize_media_server_config(config)
    plan = plan_media_import(cfg, out_dir, info)
    if plan is None:
        return {
            "ok": False,
            "server_type": cfg["server_type"],
            "layout_mode": cfg["layout_mode"],
            "error": "No media file or library path is available",
            "files": [],
        }
    from .sidecar_profiles import BUILTIN_PROFILES

    profile = _sidecar_profile(cfg)
    profile_cfg = BUILTIN_PROFILES[profile]
    stem = os.path.splitext(os.path.basename(plan.destination))[0]
    target_dir = os.path.dirname(plan.destination)
    files = [{
        "kind": "media",
        "path": plan.destination,
        "relative_path": plan.relative_path,
        "bytes": os.path.getsize(plan.media_path),
    }]
    if profile_cfg.get("nfo"):
        files.append({
            "kind": "nfo",
            "path": os.path.join(target_dir, f"{stem}.nfo"),
            "relative_path": os.path.relpath(
                os.path.join(target_dir, f"{stem}.nfo"), plan.library_path,
            ).replace(os.sep, "/"),
            "bytes": 0,
        })
    if profile_cfg.get("metadata_json"):
        files.append({
            "kind": "metadata",
            "path": os.path.join(target_dir, "metadata.json"),
            "relative_path": os.path.relpath(
                os.path.join(target_dir, "metadata.json"), plan.library_path,
            ).replace(os.sep, "/"),
            "bytes": 0,
        })
    if profile_cfg.get("thumbnail"):
        files.append({
            "kind": "thumbnail",
            "path": os.path.join(target_dir, "thumbnail.jpg"),
            "relative_path": os.path.relpath(
                os.path.join(target_dir, "thumbnail.jpg"), plan.library_path,
            ).replace(os.sep, "/"),
            "bytes": 0,
        })
    if cfg.get("portable_m3u") or (
        cfg["server_type"] == "kodi" and cfg.get("native_playlist")
    ):
        playlist = os.path.join(
            plan.library_path, _safe_playlist_name(cfg["playlist_name"]),
        )
        files.append({
            "kind": "playlist",
            "path": playlist,
            "relative_path": os.path.basename(playlist),
            "bytes": 0,
        })
    return {
        "ok": True,
        "server_type": cfg["server_type"],
        "layout_mode": plan.layout_mode,
        "sidecar_profile": profile,
        "library_path": plan.library_path,
        "destination": plan.destination,
        "relative_media_path": plan.relative_path,
        "files": files,
        "total_bytes": sum(int(item["bytes"] or 0) for item in files),
        "plan": plan,
    }


def materialize_media_import(
    config: dict[str, Any],
    out_dir: str | os.PathLike[str],
    info: object | None = None,
    *,
    log_fn=None,
) -> dict[str, Any]:
    """Commit a previously previewable layout and return generated files."""
    preview = preview_media_import(config, out_dir, info)
    if not preview.get("ok"):
        return preview
    plan = preview["plan"]
    if os.path.lexists(plan.destination):
        raise FileExistsError(
            f"Media-server destination already exists: {plan.relative_path}"
        )
    os.makedirs(os.path.dirname(plan.destination), exist_ok=True)
    try:
        os.link(plan.media_path, plan.destination)
        action = "Hardlinked"
    except OSError:
        shutil.copy2(plan.media_path, plan.destination)
        action = "Copied"
    if log_fn:
        log_fn(f"[MEDIA-SERVER] {action} → {plan.destination}")

    from .sidecar_profiles import generate_sidecars

    cfg = normalize_media_server_config(config)
    sidecar_results = generate_sidecars(
        os.path.dirname(plan.destination), info,
        profile=_sidecar_profile(cfg),
        file_base=os.path.splitext(os.path.basename(plan.destination))[0],
        log_fn=log_fn,
    ) if info else {}
    if cfg.get("portable_m3u") or (
        cfg["server_type"] == "kodi" and cfg.get("native_playlist")
    ):
        playlist_path = write_portable_m3u(
            cfg["library_path"], cfg["playlist_name"],
        )
        sidecar_results["playlist"] = playlist_path
        if log_fn:
            log_fn(f"[MEDIA-SERVER] Portable playlist updated → {playlist_path}")

    files = []
    for item in preview["files"]:
        candidate = item["path"]
        if os.path.isfile(candidate):
            files.append({
                **item,
                "bytes": os.path.getsize(candidate),
            })
    for key, value in sidecar_results.items():
        if value and os.path.isfile(value) and not any(
            item["path"] == os.path.abspath(value) for item in files
        ):
            path = os.path.abspath(value)
            files.append({
                "kind": key,
                "path": path,
                "relative_path": os.path.relpath(
                    path, plan.library_path,
                ).replace(os.sep, "/"),
                "bytes": os.path.getsize(path),
            })
    return {
        **preview,
        "plan": plan,
        "files": files,
        "total_bytes": sum(int(item["bytes"] or 0) for item in files),
        "sidecars": sidecar_results,
    }


def queue_media_server_export(
    config: dict[str, Any],
    out_dir: str | os.PathLike[str],
    info: object | None,
    profile_id: str,
    *,
    log_fn=None,
) -> dict[str, Any]:
    """Materialize a layout and enqueue each generated file for upload."""
    from ..upload.runtime import get_runtime, public_job, resolve_profile

    runtime = get_runtime()
    if resolve_profile(profile_id) is None:
        raise ValueError("Upload profile was not found")
    exported = materialize_media_import(config, out_dir, info, log_fn=log_fn)
    if not exported.get("ok"):
        return exported
    jobs = []
    for item in exported.get("files", []):
        relative = str(item.get("relative_path", "") or "")
        remote_dir = os.path.dirname(relative).replace(os.sep, "/")
        job = runtime.enqueue(
            profile_id, item["path"],
            metadata={
                "kind": item.get("kind", ""),
                "title": getattr(info, "title", "") if info else "",
                "remote_dir": remote_dir,
            },
        )
        jobs.append(public_job(job))
    return {
        **exported,
        "upload_profile_id": str(profile_id or ""),
        "upload_jobs": jobs,
    }


def _within_directory(path: str | os.PathLike[str], directory: str | os.PathLike[str]) -> bool:
    try:
        return os.path.commonpath((os.path.abspath(os.fspath(path)), os.path.abspath(os.fspath(directory)))) == os.path.abspath(os.fspath(directory))
    except ValueError:
        return False


def collect_library_media(library_path: str | os.PathLike[str]) -> list[str]:
    """Return media files below a library root in deterministic order."""
    root = os.path.abspath(os.fspath(library_path))
    if not os.path.isdir(root):
        return []
    paths: list[str] = []
    for current, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(_MEDIA_EXTS):
                paths.append(os.path.abspath(os.path.join(current, name)))
    return sorted(paths, key=lambda value: value.casefold())


def _playlist_label(path: str, entry: object | None = None) -> str:
    title = str(getattr(entry, "title", "") or "") if entry else ""
    return title.replace("\r", " ").replace("\n", " ").strip() or os.path.basename(path)


def build_m3u_text(
    entries: Iterable[str | dict[str, Any]],
    *,
    playlist_dir: str | os.PathLike[str],
) -> str:
    """Build a portable UTF-8 M3U with relative paths and safe labels."""
    base = os.path.abspath(os.fspath(playlist_dir))
    lines = ["#EXTM3U"]
    for item in entries:
        if isinstance(item, dict):
            path = str(item.get("path", "") or "")
            label = str(item.get("title", "") or "")
            duration = int(item.get("duration", -1) or -1)
        else:
            path = os.fspath(item)
            label = ""
            duration = -1
        if not path or not _within_directory(path, base):
            continue
        relative = os.path.relpath(os.path.abspath(path), base).replace(os.sep, "/")
        if relative.startswith("../") or relative == "..":
            continue
        lines.append(f"#EXTINF:{duration},{_playlist_label(path) if not label else label}")
        lines.append(relative)
    return "\n".join(lines) + "\n"


def write_portable_m3u(
    library_path: str | os.PathLike[str],
    playlist_name: str,
    entries: Iterable[str | dict[str, Any]] | None = None,
) -> str:
    """Atomically write a portable playlist inside the library root."""
    root = os.path.abspath(os.fspath(library_path))
    os.makedirs(root, exist_ok=True)
    filename = _safe_playlist_name(playlist_name or DEFAULT_PLAYLIST_NAME)
    destination = os.path.join(root, filename)
    if entries is None:
        entries = collect_library_media(root)
    payload = build_m3u_text(entries, playlist_dir=root)
    temporary = os.path.join(root, f".{filename}.tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    return destination


def _request_bytes(
    url: str,
    *,
    token: str = "",
    server_type: str = "",
    method: str = "GET",
    payload: object | None = None,
    timeout: int = 15,
) -> bytes:
    data = None
    headers = {"Accept": "application/json, application/xml, text/xml"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        if server_type == "plex":
            headers["X-Plex-Token"] = token
        else:
            headers["X-Emby-Token"] = token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _is_xml_element(value: object) -> bool:
    return (
        hasattr(value, "tag")
        and hasattr(value, "attrib")
        and callable(getattr(value, "findall", None))
    )


def _json_or_xml(payload: bytes) -> object:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            return ET.fromstring(payload)
        except (ET.ParseError, DefusedXmlException):
            return None


def fetch_media_server_users(config: dict[str, Any]) -> list[dict[str, str]]:
    """Fetch selectable users from one configured server."""
    cfg = normalize_media_server_config(config)
    if not cfg["url"] or not cfg["token"]:
        raise ValueError("server URL and API token are required")
    server_type = cfg["server_type"]
    if server_type == "plex":
        payload = _json_or_xml(
            _request_bytes(f"{cfg['url'].rstrip('/')}/users", token=cfg["token"], server_type=server_type)
        )
        if not _is_xml_element(payload):
            return []
        return [
            {"id": str(node.attrib.get("id", "") or node.attrib.get("key", "")),
             "name": str(node.attrib.get("title", "") or node.attrib.get("username", ""))}
            for node in payload.findall("User")
            if node.attrib.get("id") or node.attrib.get("key")
        ]
    payload = _json_or_xml(
        _request_bytes(f"{cfg['url'].rstrip('/')}/Users", token=cfg["token"], server_type=server_type)
    )
    if not isinstance(payload, list):
        return []
    return [
        {"id": str(item.get("Id", "") or ""), "name": str(item.get("Name", "") or "")}
        for item in payload
        if isinstance(item, dict) and item.get("Id")
    ]


def _plex_items(payload: Any) -> list[dict[str, Any]]:
    items = []
    for node in payload:
        rating_key = str(node.attrib.get("ratingKey", "") or "")
        if not rating_key:
            continue
        watched = int(node.attrib.get("viewCount", "0") or 0) > 0
        try:
            position = max(0.0, float(node.attrib.get("viewOffset", "0") or 0) / 1000.0)
        except (TypeError, ValueError):
            position = 0.0
        provider_ids = {}
        guid = str(node.attrib.get("guid", "") or "")
        if guid:
            provider_ids["guid"] = guid
        items.append({
            "server_key": rating_key,
            "source_id": guid,
            "provider_ids": provider_ids,
            "title": str(node.attrib.get("title", "") or ""),
            "channel": str(node.attrib.get("grandparentTitle", "") or ""),
            "year": str(node.attrib.get("year", "") or ""),
            "path": str(node.attrib.get("file", "") or ""),
            "watched": watched,
            "watch_position_secs": position,
            "played": watched or position > 0,
        })
    return items


def _jellyfin_items(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    result = []
    for item in payload.get("Items", []) or []:
        if not isinstance(item, dict) or not item.get("Id"):
            continue
        user_data = item.get("UserData") if isinstance(item.get("UserData"), dict) else {}
        ticks = float(user_data.get("PlaybackPositionTicks", 0) or 0)
        position = max(0.0, ticks / 10_000_000.0)
        provider_ids = item.get("ProviderIds") if isinstance(item.get("ProviderIds"), dict) else {}
        source_id = next((str(value) for value in provider_ids.values() if value), "")
        played = bool(user_data.get("Played", False)) or position > 0
        result.append({
            "server_key": str(item.get("Id", "") or ""),
            "source_id": source_id,
            "provider_ids": {str(key): str(value) for key, value in provider_ids.items() if value},
            "title": str(item.get("Name", "") or ""),
            "channel": str(item.get("SeriesName", "") or item.get("Album", "") or ""),
            "year": str(item.get("ProductionYear", "") or ""),
            "path": str(item.get("Path", "") or ""),
            "watched": bool(user_data.get("Played", False)),
            "watch_position_secs": position,
            "played": played,
        })
    return result


def fetch_watched_items(config: dict[str, Any], user_id: str) -> list[dict[str, Any]]:
    """Fetch watched/progress metadata for one explicitly selected user."""
    cfg = normalize_media_server_config(config)
    selected_user = str(user_id or "").strip()
    if not selected_user:
        raise ValueError("select a media-server user before importing watched state")
    if not cfg["url"] or not cfg["token"]:
        raise ValueError("server URL and API token are required")
    base = cfg["url"].rstrip("/")
    if cfg["server_type"] == "plex":
        section = cfg["library_id"] or "1"
        url = f"{base}/library/sections/{urllib.parse.quote(section, safe='')}/all?type=1"
        payload = _json_or_xml(_request_bytes(url, token=cfg["token"], server_type="plex"))
        return _plex_items(payload) if _is_xml_element(payload) else []
    query = urllib.parse.urlencode({
        "UserId": selected_user,
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Episode,Video",
        "Fields": "Path,ProviderIds,UserData,MediaSources",
        "EnableUserData": "true",
        "Limit": "10000",
    })
    payload = _json_or_xml(
        _request_bytes(f"{base}/Users/{urllib.parse.quote(selected_user, safe='')}/Items?{query}",
                       token=cfg["token"], server_type=cfg["server_type"])
    )
    return _jellyfin_items(payload)


def _identity_values(item: dict[str, Any]) -> set[str]:
    values = set()
    for key in ("source_id", "guid", "url"):
        value = str(item.get(key, "") or "").strip().casefold()
        if value:
            values.add(value)
    provider_ids = item.get("provider_ids")
    if isinstance(provider_ids, dict):
        values.update(
            str(value).strip().casefold()
            for value in provider_ids.values()
            if str(value or "").strip()
        )
    return values


def _path_key(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text)).casefold()


def _title_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("title", "") or "").strip().casefold(),
        str(item.get("channel", "") or "").strip().casefold(),
        str(item.get("year", "") or "").strip()[:4],
    )


def preview_watched_import(
    server_items: Iterable[dict[str, Any]],
    history_entries: Iterable[dict[str, Any]],
    *,
    user_id: str = "",
) -> dict[str, Any]:
    """Build a safe preview; ambiguous or weak matches are never applied."""
    history = [dict(row) for row in history_entries if isinstance(row, dict)]
    matches: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    used_history: set[int] = set()

    for raw_item in server_items:
        item = dict(raw_item or {})
        played = bool(item.get("played", item.get("watched", False)))
        try:
            position = max(
                0.0,
                float(item.get("watch_position_secs", item.get("playback_position_secs", 0)) or 0),
            )
        except (TypeError, ValueError):
            position = 0.0
        if not played and position <= 0:
            skipped.append({"server_key": str(item.get("server_key", "") or ""), "reason": "not watched"})
            continue

        server_ids = _identity_values(item)
        server_path = _path_key(item.get("path", ""))
        server_title = _title_key(item)
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in history:
            row_id = int(row.get("id", row.get("db_id", 0)) or 0)
            if not row_id or row_id in used_history:
                continue
            row_ids = _identity_values(row)
            row_path = _path_key(row.get("path", ""))
            row_title = _title_key(row)
            score = 0
            if server_ids and row_ids and server_ids.intersection(row_ids):
                score = 300
            elif server_path and row_path and server_path == row_path:
                score = 200
            elif server_title != ("", "", "") and server_title == row_title:
                score = 100
            if score:
                scored.append((score, row))
        if not scored:
            skipped.append({
                "server_key": str(item.get("server_key", "") or ""),
                "title": str(item.get("title", "") or ""),
                "reason": "no unambiguous local history match",
            })
            continue
        best_score = max(score for score, _row in scored)
        candidates = [row for score, row in scored if score == best_score]
        if len(candidates) != 1:
            ambiguous.append({
                "server_key": str(item.get("server_key", "") or ""),
                "title": str(item.get("title", "") or ""),
                "candidate_history_ids": [int(row.get("id", 0) or 0) for row in candidates],
                "reason": "multiple local history matches",
            })
            continue
        row = candidates[0]
        row_id = int(row.get("id", row.get("db_id", 0)) or 0)
        used_history.add(row_id)
        matches.append({
            "history_id": row_id,
            "server_key": str(item.get("server_key", "") or ""),
            "title": str(item.get("title", "") or row.get("title", "")),
            "watched": True,
            "watch_position_secs": position,
            "match_strength": best_score,
        })
    return {
        "user_id": str(user_id or ""),
        "matches": matches,
        "skipped": skipped,
        "ambiguous": ambiguous,
        "lifecycle_delete_requested": False,
    }


def apply_watched_import(
    preview: dict[str, Any],
    update_fn: Callable[[int, dict[str, Any]], None],
    *,
    allow_lifecycle_delete: bool = False,
) -> int:
    """Apply only previewed watched fields; never delete local media."""
    if allow_lifecycle_delete:
        raise ValueError("watched-state import cannot enable lifecycle deletion")
    applied = 0
    for match in preview.get("matches", []) if isinstance(preview, dict) else []:
        if not isinstance(match, dict):
            continue
        history_id = int(match.get("history_id", 0) or 0)
        if history_id <= 0:
            continue
        update_fn(history_id, {
            "watched": bool(match.get("watched", True)),
            "watch_position_secs": max(0.0, float(match.get("watch_position_secs", 0) or 0)),
        })
        applied += 1
    return applied


def _scan_plex(url: str, token: str, library_id: str, log_fn=None):
    """Plex: GET /library/sections/{id}/refresh with token in header."""
    section = library_id or "1"
    scan_url = f"{url}/library/sections/{urllib.parse.quote(section, safe='')}/refresh"
    _request_bytes(scan_url, token=token, server_type="plex")
    if log_fn:
        log_fn(f"[MEDIA-SERVER] Plex library scan triggered (section {section}).")


def _scan_jellyfin(url: str, token: str, log_fn=None):
    """Jellyfin/Emby: POST /Library/Refresh with API key header."""
    _request_bytes(f"{url}/Library/Refresh", token=token, server_type="jellyfin", method="POST")
    if log_fn:
        log_fn("[MEDIA-SERVER] Jellyfin/Emby library refresh triggered.")


def _server_search_item(config: dict[str, Any], plan: MediaImportPlan) -> str:
    """Return a remote item id/rating key matching one imported path."""
    cfg = normalize_media_server_config(config)
    server_type = cfg["server_type"]
    base = cfg["url"].rstrip("/")
    if server_type == "plex":
        section = cfg["library_id"] or "1"
        query = urllib.parse.urlencode({"type": "1", "title": plan.title})
        payload = _json_or_xml(_request_bytes(
            f"{base}/library/sections/{urllib.parse.quote(section, safe='')}/all?{query}",
            token=cfg["token"], server_type=server_type,
        ))
        if not _is_xml_element(payload):
            return ""
        target = _path_key(plan.destination)
        for node in payload:
            if _path_key(node.attrib.get("file", "")) == target:
                return str(node.attrib.get("ratingKey", "") or "")
        return ""
    query = urllib.parse.urlencode({
        "Recursive": "true", "SearchTerm": plan.title,
        "Fields": "Path,ProviderIds,MediaSources",
    })
    payload = _json_or_xml(_request_bytes(
        f"{base}/Items?{query}", token=cfg["token"], server_type=server_type,
    ))
    if not isinstance(payload, dict):
        return ""
    target = _path_key(plan.destination)
    for item in payload.get("Items", []) or []:
        if not isinstance(item, dict):
            continue
        if _path_key(item.get("Path", "")) == target:
            return str(item.get("Id", "") or "")
        for source in item.get("MediaSources", []) or []:
            if isinstance(source, dict) and _path_key(source.get("Path", "")) == target:
                return str(item.get("Id", "") or "")
    return ""


def sync_native_playlist(
    config: dict[str, Any],
    plan: MediaImportPlan,
    *,
    log_fn=None,
) -> bool:
    """Add an imported item to a named native playlist when the server allows it."""
    cfg = normalize_media_server_config(config)
    if not cfg.get("native_playlist"):
        return False
    if cfg["server_type"] == "kodi":
        # Kodi's native file playlist is the portable M3U written by the same
        # import.  The caller writes it even when no remote API exists.
        return bool(cfg.get("portable_m3u"))
    if not cfg["url"] or not cfg["token"]:
        if log_fn:
            log_fn("[MEDIA-SERVER] Native playlist skipped — URL/token not configured.")
        return False
    try:
        item_id = _server_search_item(cfg, plan)
        if not item_id:
            if log_fn:
                log_fn("[MEDIA-SERVER] Native playlist waiting for server library scan.")
            return False
        base = cfg["url"].rstrip("/")
        name = cfg["playlist_name"]
        if cfg["server_type"] == "plex":
            uri = f"server://{urllib.parse.urlparse(base).netloc}/com.plexapp.plugins.library/library/metadata/{item_id}"
            playlists = _json_or_xml(_request_bytes(
                f"{base}/playlists", token=cfg["token"], server_type="plex"
            ))
            playlist_id = ""
            if _is_xml_element(playlists):
                for playlist in playlists:
                    if str(playlist.attrib.get("title", "")) == name:
                        playlist_id = str(playlist.attrib.get("ratingKey", "") or "")
                        break
            if playlist_id:
                query = urllib.parse.urlencode({"uri": uri})
                _request_bytes(
                    f"{base}/playlists/{urllib.parse.quote(playlist_id, safe='')}/items?{query}",
                    token=cfg["token"], server_type="plex", method="POST",
                )
            else:
                query = urllib.parse.urlencode({
                    "type": "video", "title": name, "smart": "0", "uri": uri,
                })
                _request_bytes(
                    f"{base}/playlists?{query}",
                    token=cfg["token"], server_type="plex", method="POST",
                )
        else:
            # Jellyfin and Emby share the playlist shape and endpoint.
            existing = _json_or_xml(_request_bytes(
                f"{base}/Playlists", token=cfg["token"], server_type=cfg["server_type"]
            ))
            playlist_id = ""
            if isinstance(existing, dict):
                for playlist in existing.get("Items", []) or []:
                    if isinstance(playlist, dict) and str(playlist.get("Name", "")) == name:
                        playlist_id = str(playlist.get("Id", "") or "")
                        break
            if not playlist_id:
                created = _json_or_xml(_request_bytes(
                    f"{base}/Playlists",
                    token=cfg["token"], server_type=cfg["server_type"], method="POST",
                    payload={"Name": name, "MediaType": "Video"},
                ))
                if isinstance(created, dict):
                    playlist_id = str(created.get("Id", "") or created.get("id", "") or "")
            if not playlist_id:
                return False
            query = urllib.parse.urlencode({"Ids": item_id})
            _request_bytes(
                f"{base}/Playlists/{urllib.parse.quote(playlist_id, safe='')}/Items?{query}",
                token=cfg["token"], server_type=cfg["server_type"], method="POST",
            )
        if log_fn:
            log_fn(f"[MEDIA-SERVER] Added '{plan.title}' to native playlist '{name}'.")
        return True
    except Exception as error:
        if log_fn:
            log_fn(f"[MEDIA-SERVER] Native playlist sync failed: {error}")
        return False


def import_to_media_server(config, out_dir, info=None, log_fn=None, monitor_entry=None):
    """Import a recording asynchronously into the configured media library."""
    if not config or not config.get("enabled"):
        return
    effective = normalize_media_server_config(config)
    if monitor_entry is not None:
        override = str(getattr(monitor_entry, "media_server_layout", "") or "").strip().lower()
        if override in LAYOUT_MODES:
            effective["layout_mode"] = override
    library_path = effective["library_path"]
    if not library_path or not os.path.isdir(library_path):
        if log_fn:
            log_fn("[MEDIA-SERVER] Library path does not exist — skipping import.")
        return

    def _run():
        try:
            _do_import(effective, out_dir, info, log_fn)
        except Exception as error:
            if log_fn:
                log_fn(f"[MEDIA-SERVER] Import error: {error}")

    threading.Thread(target=_run, daemon=True).start()


def _do_import(config, out_dir, info, log_fn):
    exported = materialize_media_import(config, out_dir, info, log_fn=log_fn)
    if not exported.get("ok"):
        if log_fn:
            log_fn(
                "[MEDIA-SERVER] "
                f"{exported.get('error', 'No media file or library path found')}"
                " — skipping import."
            )
        return
    cfg = normalize_media_server_config(config)
    plan = exported["plan"]
    if cfg.get("upload_after_import") and cfg.get("upload_profile_id"):
        try:
            from ..upload.runtime import get_runtime

            runtime = get_runtime()
            for item in exported.get("files", []):
                relative = str(item.get("relative_path", "") or "")
                runtime.enqueue(
                    cfg["upload_profile_id"], item["path"],
                    metadata={
                        "kind": item.get("kind", ""),
                        "title": getattr(info, "title", "") if info else "",
                        "remote_dir": os.path.dirname(relative).replace(os.sep, "/"),
                    },
                )
            if log_fn:
                log_fn(
                    f"[UPLOAD] Queued {len(exported.get('files', []))} "
                    "media-server file(s)."
                )
        except Exception as error:
            if log_fn:
                log_fn(f"[UPLOAD] Media-server export queue failed: {error}")

    url = cfg["url"]
    token = cfg["token"]
    server_type = cfg["server_type"]
    if url and token and server_type != "kodi":
        try:
            if server_type == "plex":
                _scan_plex(url, token, cfg["library_id"], log_fn)
            elif server_type in ("jellyfin", "emby"):
                _scan_jellyfin(url, token, log_fn)
            sync_native_playlist(cfg, plan, log_fn=log_fn)
        except Exception as error:
            if log_fn:
                log_fn(f"[MEDIA-SERVER] Scan trigger failed: {error}")
    elif server_type == "kodi" and log_fn:
        log_fn("[MEDIA-SERVER] Kodi uses the on-disk NFO/playlist layout; no remote scan was requested.")
    elif log_fn:
        log_fn("[MEDIA-SERVER] No server URL/token — skipping library scan.")
