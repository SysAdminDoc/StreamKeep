"""Validation and normalization for per-download yt-dlp output controls."""

from __future__ import annotations

import re


FORMAT_SORT_PRESETS = {
    "prefer-av1": "vcodec:av01,res,fps,hdr:12,acodec,br",
    "cap-2160p": "res:2160",
    "cap-1080p": "res:1080",
    "cap-720p": "res:720",
    "smallest": "+size,+br,+res,+fps",
}

VIDEO_CONTAINERS = ("mp4", "mkv", "webm", "original")
AUDIO_FORMATS = ("best", "mp3", "m4a", "opus", "flac", "wav")

_AUDIO_QUALITY_RE = re.compile(
    r"(?:10|[0-9](?:\.\d+)?)|(?:[1-9][0-9]*(?:\.[0-9]+)?[kKmM])"
)


def _safe_argument(value, label, *, max_len=1024):
    """Return an argv-safe value without changing its yt-dlp semantics."""
    value = "" if value is None else str(value)
    if not value.strip():
        return ""
    if len(value) > max_len:
        raise ValueError(f"{label} is too long (maximum {max_len} characters)")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} cannot contain control characters")
    return value


def resolve_format_sort(*, preset="", custom=""):
    """Resolve a named format-sort preset or validated custom expression."""
    preset = str(preset or "").strip().lower()
    custom = _safe_argument(custom, "Format sort")
    if preset and custom:
        raise ValueError("Choose either a format-sort preset or a custom expression")
    if preset:
        try:
            return FORMAT_SORT_PRESETS[preset]
        except KeyError as error:
            choices = ", ".join(FORMAT_SORT_PRESETS)
            raise ValueError(f"Unknown format-sort preset; choose {choices}") from error
    return custom


def validate_download_options(
    *,
    format_spec="",
    format_sort_preset="",
    format_sort="",
    container="",
    audio_format="",
    audio_quality="",
):
    """Validate and normalize yt-dlp format/output settings.

    The raw format specification and custom sort expression are deliberately
    returned byte-for-byte unchanged so callers can pass each as one argv
    element without shell interpretation.
    """
    raw_container = str(container or "").strip().lower()
    normalized_container = raw_container or "mp4"
    if normalized_container not in VIDEO_CONTAINERS:
        raise ValueError(
            "Container must be one of: " + ", ".join(VIDEO_CONTAINERS)
        )

    normalized_audio = str(audio_format or "").strip().lower()
    if normalized_audio and normalized_audio not in AUDIO_FORMATS:
        raise ValueError(
            "Audio format must be one of: " + ", ".join(AUDIO_FORMATS)
        )

    quality = str(audio_quality or "").strip()
    if quality and not normalized_audio:
        raise ValueError("Audio quality requires audio-extract mode")
    if quality and not _AUDIO_QUALITY_RE.fullmatch(quality):
        raise ValueError(
            "Audio quality must be 0-10 or a bitrate such as 128K"
        )
    if quality:
        try:
            numeric_quality = float(quality)
        except ValueError:
            numeric_quality = None
        if numeric_quality is not None and not 0 <= numeric_quality <= 10:
            raise ValueError("Numeric audio quality must be between 0 and 10")
    return {
        "format_spec": _safe_argument(format_spec, "Format specification"),
        "format_sort": resolve_format_sort(
            preset=format_sort_preset,
            custom=format_sort,
        ),
        "container": normalized_container,
        "audio_format": normalized_audio,
        "audio_quality": quality,
    }
