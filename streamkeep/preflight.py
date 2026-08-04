"""Shared pre-queue validation and picker contracts.

The fetch workers still own source resolution and the download workers still
own transfer policy. This module only defines the bounded, serializable
boundary between a client that wants to queue media and those workers. In
particular, picker responses never contain short-lived delivery URLs; a
validation id refers to a private, expiring server-side result instead.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from .job_spec import split_remote_queue_fields


MEDIA_ITEM_TYPES = ("video", "audio", "photo", "gif")
MAX_PICKER_ITEMS = 100
MAX_URL_LENGTH = 8192
MAX_TEXT_LENGTH = 512
MAX_ID_LENGTH = 128
PROBE_TTL_SECONDS = 5 * 60
_SELECTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")


class PreflightError(ValueError):
    """Raised when a probe or queue selection cannot be trusted."""


def _bounded_text(value: Any, field: str, limit: int = MAX_TEXT_LENGTH) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise PreflightError(f"{field} must be text")
    text = str(value).strip()
    if len(text) > limit:
        raise PreflightError(f"{field} is too long")
    if any(ord(char) < 32 and char not in "\t" for char in text):
        raise PreflightError(f"{field} contains control characters")
    return text


def _selection_id(value: Any, field: str) -> str:
    text = _bounded_text(value, field, MAX_ID_LENGTH)
    if text and not _SELECTION_ID_RE.fullmatch(text):
        raise PreflightError(f"{field} is not a valid picker id")
    return text


def _http_url(value: Any, *, allow_legacy_vod_id: bool = False) -> str:
    url = _bounded_text(value, "url", MAX_URL_LENGTH)
    if allow_legacy_vod_id and url.isdigit() and 1 <= len(url) <= 32:
        return url
    parsed = urlsplit(url)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or any(char.isspace() for char in url)
    ):
        raise PreflightError("invalid url")
    return url


def _media_type(value: Any, default: str = "video") -> str:
    text = str(value or default).strip().lower()
    aliases = {"image": "photo", "picture": "photo", "still": "photo"}
    text = aliases.get(text, text)
    return text if text in MEDIA_ITEM_TYPES else default


def validate_queue_payload(payload: Any) -> dict[str, Any]:
    """Return a normalized, bounded queue payload.

    Unknown fields are retained for trusted local queue consumers, while all
    fields that can influence picker selection are type-checked and bounded
    here. The headless remote boundary calls
    :func:`filter_remote_queue_payload` before persisting a job.
    GUI VOD rows may retain a legacy numeric Twitch VOD id as url when the
    real source is present in vod_source.
    """
    if not isinstance(payload, dict):
        raise PreflightError("queue payload must be an object")
    normalized = dict(payload)
    vod_source = _bounded_text(
        payload.get("vod_source"), "vod_source", MAX_URL_LENGTH
    )
    vod_platform = _bounded_text(payload.get("vod_platform"), "vod_platform")
    url = _http_url(
        payload.get("url"),
        allow_legacy_vod_id=bool(
            vod_source
            and str(payload.get("url") or "").strip().isdigit()
            and vod_platform.casefold() == "twitch"
        ),
    )
    normalized["url"] = url
    if vod_source:
        normalized["vod_source"] = vod_source
    if vod_platform:
        normalized["vod_platform"] = vod_platform

    for field in (
        "validation_id",
        "media_item_id",
        "background_audio_id",
        "media_item_type",
    ):
        if field in payload and payload.get(field) not in (None, ""):
            if field == "media_item_type":
                value = _bounded_text(
                    payload.get(field), field, MAX_ID_LENGTH
                ).lower()
                if value not in MEDIA_ITEM_TYPES:
                    raise PreflightError("media_item_type is invalid")
            else:
                value = _selection_id(payload.get(field), field)
            normalized[field] = value

    if "media_item_ids" in payload and payload.get("media_item_ids") not in (
        None,
        "",
    ):
        values = payload.get("media_item_ids")
        if not isinstance(values, list) or not values:
            raise PreflightError("media_item_ids must be a non-empty array")
        if len(values) > MAX_PICKER_ITEMS:
            raise PreflightError("too many media items selected")
        normalized["media_item_ids"] = [
            _selection_id(value, "media_item_id") for value in values
        ]

    for field in (
        "quality",
        "title",
        "platform",
        "source_id",
        "webpage_url",
        "vod_title",
        "vod_channel",
    ):
        if field in payload and payload.get(field) not in (None, ""):
            normalized[field] = _bounded_text(payload.get(field), field)
    return normalized


def _path_is_within_root(path: Any, root: Any) -> bool:
    """Return whether real *path* stays within the configured real *root*."""
    try:
        root_real = os.path.normcase(os.path.realpath(os.fspath(root)))
        path_real = os.path.normcase(os.path.realpath(os.fspath(path)))
        return bool(root_real) and os.path.commonpath(
            (root_real, path_real)
        ) == root_real
    except (OSError, TypeError, ValueError):
        return False


def filter_remote_queue_payload(
    payload: Any,
    *,
    output_root: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Filter a remote queue request before it can become a durable job.

    The returned field names are rejected rather than raised as validation
    errors so clients can continue to queue a URL when they send a stale or
    speculative executor option. ``output_dir`` is the sole path-bearing
    exception and is accepted only below the service's configured output root.
    """
    normalized = validate_queue_payload(payload)
    filtered, rejected = split_remote_queue_fields(normalized)
    rejected_set = set(rejected)
    if "output_dir" in filtered:
        raw_output_dir = filtered["output_dir"]
        if raw_output_dir in (None, ""):
            filtered.pop("output_dir", None)
        elif not isinstance(raw_output_dir, str):
            filtered.pop("output_dir", None)
            rejected_set.add("output_dir")
        else:
            try:
                output_dir = _bounded_text(
                    raw_output_dir, "output_dir", MAX_URL_LENGTH
                )
            except PreflightError:
                filtered.pop("output_dir", None)
                rejected_set.add("output_dir")
            else:
                if not _path_is_within_root(output_dir, output_root):
                    filtered.pop("output_dir", None)
                    rejected_set.add("output_dir")
                else:
                    filtered["output_dir"] = output_dir
    return filtered, tuple(sorted(rejected_set))


def validate_probe_request(payload: Any) -> dict[str, Any]:
    """Validate a probe request using the same selection fields as queueing."""
    normalized = validate_queue_payload(payload)
    normalized["action"] = _bounded_text(
        payload.get("action") or "probe", "action", 32
    ).lower()
    if normalized["action"] not in {"probe", "fetch", "queue"}:
        raise PreflightError("invalid probe action")
    return normalized


def _value(obj: Any, key: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _safe_metadata(value: Any, field: str, limit: int = MAX_TEXT_LENGTH) -> str:
    try:
        return _bounded_text(value, field, limit)
    except PreflightError:
        return ""


def _safe_int(value: Any, *, maximum: int = 2**63 - 1) -> int:
    try:
        return max(0, min(maximum, int(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _stable_id(prefix: str, source_url: str, index: int, stable: Any = "") -> str:
    digest = hashlib.sha256(
        f"{prefix}\0{source_url}\0{stable}\0{index}".encode(
            "utf-8", "replace"
        )
    ).hexdigest()[:20]
    return f"{prefix}:{digest}"


def _audio_picker_entries(
    values: Any,
    *,
    prefix: str,
    source_url: str,
    seed: str = "",
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    public: list[dict[str, Any]] = []
    private: dict[str, dict[str, Any]] = {}
    if not isinstance(values, (list, tuple)):
        return public, private
    for index, value in enumerate(values[:MAX_PICKER_ITEMS]):
        if isinstance(value, dict):
            label = value.get("label") or value.get("name") or "Audio"
            language = value.get("language") or value.get("lang") or ""
            codec = value.get("codec") or ""
            track_id = value.get("id") or value.get("track_id") or ""
            selected = bool(value.get("selected", value.get("default", False)))
        else:
            label = _value(value, "label") or _value(value, "name") or "Audio"
            language = _value(value, "language") or _value(value, "lang") or ""
            codec = _value(value, "codec") or ""
            track_id = _value(value, "id") or ""
            selected = bool(
                _value(value, "selected", _value(value, "default", False))
            )
        item_id = _stable_id(prefix, source_url, index, f"{seed}:{track_id}")
        public_item = {
            "id": item_id,
            "type": "audio",
            "label": _safe_metadata(label, "audio label", 160) or "Audio",
            "language": _safe_metadata(language, "audio language", 64),
            "codec": _safe_metadata(codec, "audio codec", 96),
            "selected": selected,
        }
        public.append(public_item)
        private[item_id] = {
            "background_audio_id": item_id,
            "background_audio_track_id": _safe_metadata(
                track_id, "track id", 160
            ),
            "background_audio_type": "audio",
        }
    return public, private


def serialize_vod_picker(vods: Any, source_url: str = "") -> dict[str, Any]:
    """Serialize a VOD listing into a safe multi-media picker result."""
    source_url = (
        _http_url(source_url) if source_url else "https://streamkeep.invalid/"
    )
    public_items: list[dict[str, Any]] = []
    private_items: dict[str, dict[str, Any]] = {}
    background_public: list[dict[str, Any]] = []
    background_private: dict[str, dict[str, Any]] = {}
    values = list(vods or []) if isinstance(vods, (list, tuple)) else []
    for index, vod in enumerate(values[:MAX_PICKER_ITEMS]):
        title = _safe_metadata(
            _value(vod, "title"), "title"
        ) or f"VOD {index + 1}"
        platform = _safe_metadata(_value(vod, "platform"), "platform", 120)
        source = _safe_metadata(
            _value(vod, "source"), "vod source", MAX_URL_LENGTH
        )
        source_id = _safe_metadata(_value(vod, "source_id"), "source id", 160)
        webpage_url = _safe_metadata(
            _value(vod, "webpage_url"), "webpage_url", MAX_URL_LENGTH
        )
        item_id = _stable_id(
            "vod", source_url, index, source_id or webpage_url or source or title
        )
        media_type = _media_type(_value(vod, "media_type", "video"))
        public_items.append({
            "id": item_id,
            "type": media_type,
            "title": title,
            "label": title,
            "platform": platform,
            "date": _safe_metadata(_value(vod, "date"), "date", 80),
            "duration": _safe_metadata(
                _value(vod, "duration"), "duration", 64
            ),
            "duration_ms": _safe_int(_value(vod, "duration_ms", 0)),
            "selected": index == 0,
        })
        private_items[item_id] = {
            "media_item_id": item_id,
            "media_item_type": media_type,
            "vod_source": source,
            "vod_platform": platform,
            "vod_title": title,
            "vod_channel": _safe_metadata(
                _value(vod, "channel"), "channel", 160
            ),
            "feed_url": _safe_metadata(
                _value(vod, "feed_url"), "feed_url", MAX_URL_LENGTH
            ),
            "source_id": source_id,
            "webpage_url": webpage_url,
            "title": title,
            "platform": platform,
        }
        audio_public, audio_private = _audio_picker_entries(
            _value(vod, "background_audio", []),
            prefix=f"background-audio:{index}",
            source_url=source_url,
            seed=item_id,
        )
        background_public.extend(audio_public)
        background_private.update(audio_private)

    first = public_items[0] if public_items else {}
    return {
        "kind": "vods",
        "title": first.get("title", ""),
        "platform": first.get("platform", ""),
        "duration": first.get("duration", ""),
        "media_items": public_items,
        "background_audio": background_public[:MAX_PICKER_ITEMS],
        "_media_items": private_items,
        "_background_audio": background_private,
        "truncated": len(values) > MAX_PICKER_ITEMS,
    }


def _quality_media_type(quality: Any) -> str:
    explicit = _value(quality, "media_type", "")
    if explicit:
        return _media_type(explicit)
    tracks = list(_value(quality, "tracks", []) or [])
    kinds = {str(_value(track, "kind", "")).lower() for track in tracks}
    if kinds and "video" not in kinds and "audio" in kinds:
        return "audio"
    fmt = str(_value(quality, "format_type", "") or "").lower()
    if fmt in {"gif", "image/gif"}:
        return "gif"
    if fmt in {"photo", "image", "jpeg", "jpg", "png", "webp"}:
        return "photo"
    return "video"


def serialize_stream_picker(info: Any, source_url: str = "") -> dict[str, Any]:
    """Serialize resolved qualities and selectable audio tracks safely."""
    source_url = (
        _http_url(source_url) if source_url else "https://streamkeep.invalid/"
    )
    qualities = list(_value(info, "qualities", []) or [])
    public_items: list[dict[str, Any]] = []
    private_items: dict[str, dict[str, Any]] = {}
    background_public: list[dict[str, Any]] = []
    background_private: dict[str, dict[str, Any]] = {}
    for index, quality in enumerate(qualities[:MAX_PICKER_ITEMS]):
        item_id = f"quality:{index}"
        name = _safe_metadata(_value(quality, "name"), "quality", 160)
        resolution = _safe_metadata(
            _value(quality, "resolution"), "resolution", 64
        )
        format_type = _safe_metadata(
            _value(quality, "format_type", "hls"), "format", 64
        )
        media_type = _quality_media_type(quality)
        label = name or resolution or format_type or f"Media {index + 1}"
        public_items.append({
            "id": item_id,
            "type": media_type,
            "title": _safe_metadata(_value(info, "title"), "title"),
            "label": label,
            "quality": name,
            "resolution": resolution,
            "format": format_type,
            "bandwidth": _safe_int(_value(quality, "bandwidth", 0)),
            "selected": index == 0,
        })
        private_items[item_id] = {
            "media_item_id": item_id,
            "media_item_type": media_type,
            "quality": name or resolution,
            "title": _safe_metadata(_value(info, "title"), "title"),
            "platform": _safe_metadata(
                _value(info, "platform"), "platform", 120
            ),
        }
        tracks = [
            track for track in list(_value(quality, "tracks", []) or [])
            if str(_value(track, "kind", "")).lower() == "audio"
        ]
        if not tracks and _value(quality, "audio_url", ""):
            tracks = [{"label": "Audio", "default": True, "id": "companion"}]
        for track_index, track in enumerate(tracks):
            track_id = _value(track, "id", "")
            audio_id = f"background-audio:{index}:{track_index}"
            label = _safe_metadata(
                _value(track, "label") or _value(track, "language") or "Audio",
                "audio label",
                160,
            ) or "Audio"
            language = _safe_metadata(
                _value(track, "language", ""), "audio language", 64
            )
            public_audio = {
                "id": audio_id,
                "type": "audio",
                "label": label,
                "language": language,
                "codec": _safe_metadata(
                    _value(track, "codec", ""), "audio codec", 96
                ),
                "media_item_id": item_id,
                "selected": bool(
                    _value(track, "default", False)
                    or _value(track, "autoselect", False)
                ),
            }
            background_public.append(public_audio)
            background_private[audio_id] = {
                "background_audio_id": audio_id,
                "background_audio_track_id": _safe_metadata(
                    track_id, "track id", 160
                ),
                "media_item_id": item_id,
                "background_audio_type": "audio",
            }

    if not public_items and _value(info, "url", ""):
        public_items.append({
            "id": "quality:direct",
            "type": "video",
            "title": _safe_metadata(_value(info, "title"), "title"),
            "label": "Direct media",
            "quality": "",
            "resolution": "",
            "format": "direct",
            "selected": True,
        })
        private_items["quality:direct"] = {
            "media_item_id": "quality:direct",
            "media_item_type": "video",
            "quality": "",
            "title": _safe_metadata(_value(info, "title"), "title"),
            "platform": _safe_metadata(
                _value(info, "platform"), "platform", 120
            ),
        }
    return {
        "kind": "stream",
        "title": _safe_metadata(_value(info, "title"), "title"),
        "platform": _safe_metadata(
            _value(info, "platform"), "platform", 120
        ),
        "channel": _safe_metadata(_value(info, "channel"), "channel", 160),
        "duration": _safe_metadata(
            _value(info, "duration_str") or _value(info, "total_secs", ""),
            "duration",
            64,
        ),
        "media_items": public_items,
        "background_audio": background_public[:MAX_PICKER_ITEMS],
        "_media_items": private_items,
        "_background_audio": background_private,
        "truncated": len(qualities) > MAX_PICKER_ITEMS,
    }


def build_picker_response(
    url: str,
    picker: dict[str, Any],
    validation_id: str,
    expires_at: float,
) -> dict[str, Any]:
    """Build the public REST/GUI response from a private picker result."""
    media_items = [
        dict(item)
        for item in picker.get("media_items", [])
        if isinstance(item, dict)
    ]
    background_audio = [
        dict(item)
        for item in picker.get("background_audio", [])
        if isinstance(item, dict)
    ]
    selected_media = next(
        (item for item in media_items if item.get("selected")),
        media_items[0] if media_items else {},
    )
    selected_audio = next(
        (item for item in background_audio if item.get("selected")), {}
    )
    return {
        "ok": True,
        "validated": True,
        "status": "picker" if len(media_items) > 1 else "ready",
        "validation_id": str(validation_id),
        "expires_at": int(expires_at),
        "url": str(url),
        "title": str(picker.get("title", "") or ""),
        "platform": str(picker.get("platform", "") or ""),
        "channel": str(picker.get("channel", "") or ""),
        "duration": str(picker.get("duration", "") or ""),
        "media_items": media_items,
        "picker": media_items,
        "background_audio": background_audio,
        "selection": {
            "media_item_id": selected_media.get("id", ""),
            "background_audio_id": selected_audio.get("id", ""),
        },
        "truncated": bool(picker.get("truncated", False)),
    }


class ProbeCache:
    """Thread-safe, one-use cache for private probe results."""

    def __init__(
        self,
        *,
        ttl_seconds: int = PROBE_TTL_SECONDS,
        max_entries: int = 64,
    ):
        try:
            ttl = int(ttl_seconds or PROBE_TTL_SECONDS)
        except (TypeError, ValueError):
            ttl = PROBE_TTL_SECONDS
        try:
            entries = int(max_entries or 64)
        except (TypeError, ValueError):
            entries = 64
        self.ttl_seconds = max(30, min(30 * 60, ttl))
        self.max_entries = max(4, min(256, entries))
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}

    def put(self, url: str, picker: dict[str, Any]) -> tuple[str, float]:
        now = time.time()
        validation_id = uuid.uuid4().hex
        expires_at = now + self.ttl_seconds
        with self._lock:
            self._prune_locked(now)
            while len(self._entries) >= self.max_entries:
                oldest = min(
                    self._entries,
                    key=lambda key: float(
                        self._entries[key].get("created_at", 0)
                    ),
                )
                self._entries.pop(oldest, None)
            self._entries[validation_id] = {
                "url": str(url),
                "picker": picker,
                "created_at": now,
                "expires_at": expires_at,
            }
        return validation_id, expires_at

    def take(self, validation_id: Any, url: Any) -> dict[str, Any]:
        key = _selection_id(validation_id, "validation_id")
        requested_url = _http_url(url)
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            entry = self._entries.get(key)
            if entry is None:
                raise PreflightError(
                    "validation_id is missing, expired, or already used"
                )
            if entry.get("url") != requested_url:
                raise PreflightError("validation_id does not match url")
            self._entries.pop(key, None)
        return dict(entry.get("picker") or {})

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _prune_locked(self, now: float) -> None:
        expired = [
            key for key, entry in self._entries.items()
            if float(entry.get("expires_at", 0) or 0) <= now
        ]
        for key in expired:
            self._entries.pop(key, None)


def normalize_media_selection(
    payload: Any,
    cached_picker: dict[str, Any],
) -> dict[str, Any]:
    """Verify picker ids and return private queue fields for one job."""
    normalized = validate_queue_payload(payload)
    if not normalized.get("validation_id"):
        return {}
    media_items = cached_picker.get("_media_items") or {}
    audio_items = cached_picker.get("_background_audio") or {}
    if not isinstance(media_items, dict) or not media_items:
        raise PreflightError("validation result contains no media items")

    ids = normalized.get("media_item_ids")
    if ids and len(ids) != 1:
        raise PreflightError("queue accepts one media item per request")
    media_id = normalized.get("media_item_id") or (ids[0] if ids else "")
    if not media_id:
        public_items = cached_picker.get("media_items") or []
        selected = next(
            (item for item in public_items if item.get("selected")),
            public_items[0] if len(public_items) == 1 else None,
        )
        media_id = str(selected.get("id", "") or "") if selected else ""
    if not media_id or media_id not in media_items:
        raise PreflightError(
            "media_item_id is required and must come from the picker"
        )

    selected = dict(media_items[media_id])
    result = {
        key: value for key, value in selected.items()
        if not key.startswith("_")
    }
    result["media_item_id"] = media_id
    if normalized.get("media_item_type") and normalized[
        "media_item_type"
    ] != result.get("media_item_type", ""):
        raise PreflightError("media_item_type does not match media_item_id")

    audio_id = normalized.get("background_audio_id", "")
    if audio_id:
        if audio_id not in audio_items:
            raise PreflightError("background_audio_id is not in the picker")
        audio = dict(audio_items[audio_id])
        result.update({
            key: value for key, value in audio.items()
            if not key.startswith("_")
        })
        result["background_audio_id"] = audio_id
    return result


def collect_probe_result(
    worker_factory,
    *,
    timeout_seconds: float = 45.0,
) -> tuple[str, Any]:
    """Run a FetchWorker-like object and return kind plus resolved value.

    Signal connections are direct so this helper is safe when called by the
    local HTTP thread, which must not wait on the GUI event loop.
    """
    event = threading.Event()
    result: dict[str, Any] = {}
    worker = worker_factory()

    def finished(info):
        result.update(kind="info", value=info)
        event.set()

    def vods_found(vods, platform, _cursor):
        result.update(kind="vods", value=vods, platform=str(platform or ""))
        event.set()

    def failed(error):
        result.update(kind="error", value=str(error or "Probe failed"))
        event.set()

    from PyQt6.QtCore import Qt

    direct = Qt.ConnectionType.DirectConnection
    worker.finished.connect(finished, direct)
    worker.vods_found.connect(vods_found, direct)
    worker.error.connect(failed, direct)
    worker.start()
    completed = event.wait(
        max(1.0, min(120.0, float(timeout_seconds or 45.0)))
    )
    if not completed:
        try:
            worker.requestInterruption()
        except Exception:
            pass
        try:
            worker.wait(2000)
        except Exception:
            pass
        raise PreflightError("probe timed out")
    try:
        if worker.isRunning():
            worker.wait(2000)
    except Exception:
        pass
    if result.get("kind") == "error":
        raise PreflightError(result.get("value") or "Probe failed")
    return str(result.get("kind") or "error"), result.get("value")
