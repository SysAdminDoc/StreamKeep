"""Validation and normalization for per-download yt-dlp output controls."""

from __future__ import annotations

import re
import os
import shlex
import subprocess
import urllib.parse
from datetime import datetime


FORMAT_SORT_PRESETS = {
    "prefer-av1": "vcodec:av01,res,fps,hdr:12,acodec,br",
    "cap-2160p": "res:2160",
    "cap-1080p": "res:1080",
    "cap-720p": "res:720",
    "smallest": "+size,+br,+res,+fps",
}

VIDEO_CONTAINERS = ("mp4", "mkv", "webm", "original")
AUDIO_FORMATS = ("best", "mp3", "m4a", "opus", "flac", "wav")
SUBTITLE_CONVERSIONS = ("", "srt", "vtt", "ass")
SPONSORBLOCK_CATEGORIES = {
    "sponsor": "Sponsor",
    "intro": "Intermission / intro",
    "outro": "Endcards / credits",
    "selfpromo": "Unpaid / self promotion",
    "preview": "Preview / recap",
    "filler": "Filler tangent",
    "interaction": "Interaction reminder",
    "music_offtopic": "Non-music section",
    "hook": "Hook / greetings",
    "poi_highlight": "Highlight",
    "chapter": "Community chapter",
    "all": "All categories",
    "default": "yt-dlp default set",
}
SPONSORBLOCK_NON_REMOVABLE = frozenset({"poi_highlight", "chapter"})
SPONSORBLOCK_LEGACY_REMOVE = "sponsor,selfpromo,interaction"

# Named/raw argument templates are deliberately narrower than yt-dlp itself.
# Shortcut writers were the affected surface for CVE-2026-55404, while the
# remaining options below introduce a second command/config parser or an
# executable boundary.  Higher-level StreamKeep controls own those behaviors.
YTDLP_TEMPLATE_DENIED_OPTIONS = frozenset({
    "--batch-file",
    "--config-locations",
    "--downloader",
    "--downloader-args",
    "--exec",
    "--exec-before-download",
    "--external-downloader",
    "--external-downloader-args",
    "--load-info-json",
    "--netrc-cmd",
    "--postprocessor-args",
    "--ppa",
    "--use-postprocessor",
    "--write-desktop-link",
    "--write-link",
    "--write-url-link",
    "--write-webloc-link",
    "-a",
})

_AUDIO_QUALITY_RE = re.compile(
    r"(?:10|[0-9](?:\.\d+)?)|(?:[1-9][0-9]*(?:\.[0-9]+)?[kKmM])"
)
_RATE_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?[bBkKmMgGtT]?")
_WAIT_FOR_VIDEO_RE = re.compile(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?")
_RETRY_SLEEP_RE = re.compile(r"[A-Za-z0-9_.:+*/=,-]+")
_TEMPLATE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")
YTDLP_TRANSFER_FIELDS = (
    "concurrent_fragments",
    "retries",
    "fragment_retries",
    "retry_sleep",
    "unavailable_fragments",
    "throttled_rate",
    "live_from_start",
    "wait_for_video",
    "embed_chapters",
    "embed_metadata",
    "embed_thumbnail",
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


def validate_ytdlp_template_args(args):
    """Validate a structured yt-dlp argument template.

    Templates are argv lists, never shell strings.  Options that create
    executable shortcut files, load more arguments/configuration, or delegate
    to another command boundary are reserved for typed StreamKeep features.
    """
    if isinstance(args, (str, bytes)) or not isinstance(args, (list, tuple)):
        raise ValueError("yt-dlp template arguments must be a structured list")
    if len(args) > 128:
        raise ValueError("yt-dlp template has too many arguments (maximum 128)")

    validated = []
    for raw_arg in args:
        arg = _safe_argument(raw_arg, "yt-dlp template argument", max_len=4096)
        if not arg:
            raise ValueError("yt-dlp template arguments cannot be empty")
        option = arg.split("=", 1)[0].lower()
        if option == "--":
            raise ValueError("yt-dlp template cannot end option parsing with --")
        if option in YTDLP_TEMPLATE_DENIED_OPTIONS:
            raise ValueError(f"yt-dlp template option is not allowed: {option}")
        if option.startswith("-a") and option != "--":
            raise ValueError("yt-dlp template option is not allowed: -a")
        validated.append(arg)
    return tuple(validated)


def normalize_ytdlp_arg_templates(templates):
    """Validate a bounded ``name -> argv`` template registry.

    Names are display labels. Values remain argument arrays throughout the
    application and are never parsed as shell command strings.
    """
    if templates in (None, {}):
        return {}
    if not isinstance(templates, dict):
        raise ValueError("yt-dlp argument templates must be an object")
    if len(templates) > 32:
        raise ValueError("at most 32 yt-dlp argument templates are supported")
    normalized = {}
    for raw_name, raw_args in templates.items():
        if not isinstance(raw_name, str):
            raise ValueError("yt-dlp template names must be strings")
        name = raw_name.strip()
        if not _TEMPLATE_NAME_RE.fullmatch(name):
            raise ValueError(
                "yt-dlp template names must be 1-64 letters, numbers, spaces, "
                "dots, underscores, or hyphens"
            )
        normalized[name] = list(validate_ytdlp_template_args(raw_args))
    return normalized


def parse_ytdlp_template_text(text):
    """Parse the settings editor's one-argument-per-line representation."""
    if not isinstance(text, str):
        raise ValueError("yt-dlp template editor value must be text")
    args = [line.strip() for line in text.splitlines() if line.strip()]
    if not args:
        raise ValueError("yt-dlp template must contain at least one argument")
    return validate_ytdlp_template_args(args)


def resolve_ytdlp_arg_template(templates, name):
    """Return one validated named template, or an empty tuple for no template."""
    name = str(name or "").strip()
    if not name:
        return ()
    normalized = normalize_ytdlp_arg_templates(templates)
    try:
        return tuple(normalized[name])
    except KeyError as error:
        raise ValueError(f"Unknown yt-dlp argument template: {name}") from error


def format_command_argv(argv, *, windows=None):
    """Format a generated argv for the host shell without evaluating it."""
    if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)):
        raise ValueError("command must be a structured argument list")
    values = [str(value) for value in argv]
    if not values or any(not value for value in values):
        raise ValueError("command arguments cannot be empty")
    if windows is None:
        windows = os.name == "nt"
    return subprocess.list2cmdline(values) if windows else shlex.join(values)


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


def _retry_count(value, label):
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text == "infinite":
        return text
    try:
        count = int(text)
    except ValueError as error:
        raise ValueError(f"{label} must be 0-1000 or infinite") from error
    if not 0 <= count <= 1000:
        raise ValueError(f"{label} must be 0-1000 or infinite")
    return str(count)


def validate_ytdlp_transfer_options(
    *,
    concurrent_fragments=0,
    retries="",
    fragment_retries="",
    retry_sleep="",
    unavailable_fragments="",
    throttled_rate="",
    live_from_start=False,
    wait_for_video="",
    embed_chapters=None,
    embed_metadata=None,
    embed_thumbnail=None,
):
    """Validate advanced yt-dlp transfer/retry/live controls."""
    try:
        concurrent_fragments = int(concurrent_fragments or 0)
    except (TypeError, ValueError) as error:
        raise ValueError("Concurrent fragments must be a number") from error
    if not 0 <= concurrent_fragments <= 32:
        raise ValueError("Concurrent fragments must be between 1 and 32")

    retry_sleep = _safe_argument(
        retry_sleep, "Retry sleep expression", max_len=256
    ).strip()
    if retry_sleep and not _RETRY_SLEEP_RE.fullmatch(retry_sleep):
        raise ValueError("Retry sleep expression contains unsupported characters")

    unavailable_fragments = str(unavailable_fragments or "").strip().lower()
    if unavailable_fragments not in {"", "skip", "abort"}:
        raise ValueError("Unavailable fragments must be skip or abort")

    throttled_rate = _safe_argument(
        throttled_rate, "Throttled-rate threshold", max_len=32
    ).strip()
    if throttled_rate and not _RATE_RE.fullmatch(throttled_rate):
        raise ValueError("Throttled-rate threshold must look like 100K or 2M")
    if throttled_rate and float(throttled_rate.rstrip("bBkKmMgGtT")) <= 0:
        raise ValueError("Throttled-rate threshold must be greater than zero")

    wait_for_video = _safe_argument(
        wait_for_video, "Wait-for-video interval", max_len=32
    ).strip()
    if wait_for_video:
        match = _WAIT_FOR_VIDEO_RE.fullmatch(wait_for_video)
        if not match:
            raise ValueError("Wait-for-video must be seconds or MIN-MAX seconds")
        minimum = int(match.group(1))
        maximum = int(match.group(2) or minimum)
        if minimum > maximum or maximum > 604800:
            raise ValueError(
                "Wait-for-video must be ordered and no longer than seven days"
            )

    return {
        "concurrent_fragments": concurrent_fragments,
        "retries": _retry_count(retries, "Retries"),
        "fragment_retries": _retry_count(
            fragment_retries, "Fragment retries"
        ),
        "retry_sleep": retry_sleep,
        "unavailable_fragments": unavailable_fragments,
        "throttled_rate": throttled_rate,
        "live_from_start": bool(live_from_start),
        "wait_for_video": wait_for_video,
        "embed_chapters": (
            embed_chapters if isinstance(embed_chapters, bool) else None
        ),
        "embed_metadata": (
            embed_metadata if isinstance(embed_metadata, bool) else None
        ),
        "embed_thumbnail": (
            embed_thumbnail if isinstance(embed_thumbnail, bool) else None
        ),
    }


def resolve_ytdlp_transfer_options(source=None, *, overrides=None):
    """Merge a config/object with per-job overrides and validate the result."""
    source = source or {}
    overrides = overrides or {}
    defaults = {
        "concurrent_fragments": 0,
        "retries": "",
        "fragment_retries": "",
        "retry_sleep": "",
        "unavailable_fragments": "",
        "throttled_rate": "",
        "live_from_start": False,
        "wait_for_video": "",
        "embed_chapters": None,
        "embed_metadata": None,
        "embed_thumbnail": None,
    }
    values = {}
    for name in YTDLP_TRANSFER_FIELDS:
        key = f"ytdlp_{name}"
        if key in overrides:
            value = overrides[key]
        elif isinstance(source, dict):
            value = source.get(key, source.get(name, defaults[name]))
        else:
            value = getattr(source, key, defaults[name])
        values[name] = value
    normalized = validate_ytdlp_transfer_options(**values)
    return normalized


def apply_ytdlp_transfer_options(worker, source=None, *, overrides=None):
    """Validate transfer defaults/overrides and apply them to a worker."""
    normalized = resolve_ytdlp_transfer_options(
        source, overrides=overrides,
    )
    for name, value in normalized.items():
        setattr(worker, f"ytdlp_{name}", value)
    return normalized


def validate_subtitle_options(
    *, enabled=False, languages="", automatic=True, convert="", embed=True
):
    """Validate a yt-dlp subtitle policy and preserve its language expression."""
    enabled = bool(enabled)
    languages = _safe_argument(languages, "Subtitle languages")
    conversion = str(convert or "").strip().lower()
    if conversion not in SUBTITLE_CONVERSIONS:
        raise ValueError("Subtitle conversion must be srt, vtt, ass, or unchanged")
    if enabled and not languages:
        raise ValueError("Select at least one subtitle language")
    return {
        "enabled": enabled,
        "languages": languages,
        "automatic": bool(automatic),
        "convert": conversion,
        "embed": bool(embed),
    }


def _normalize_sponsorblock_categories(value, *, removal=False):
    value = _safe_argument(
        value, "SponsorBlock remove categories" if removal
        else "SponsorBlock mark categories"
    )
    if not value:
        return ""
    normalized = []
    for token in value.split(","):
        token = token.strip()
        excluded = token.startswith("-")
        category = token[1:] if excluded else token
        if category not in SPONSORBLOCK_CATEGORIES:
            raise ValueError(f"Unknown SponsorBlock category: {category or token}")
        if removal and category in SPONSORBLOCK_NON_REMOVABLE:
            raise ValueError(f"SponsorBlock category {category} can only be marked")
        rendered = ("-" if excluded else "") + category
        if rendered not in normalized:
            normalized.append(rendered)
    return ",".join(normalized)


def _normalize_sponsorblock_api(value):
    value = _safe_argument(value, "SponsorBlock API URL", max_len=2048)
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("SponsorBlock API URL is invalid") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("SponsorBlock API URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "SponsorBlock API URL cannot contain credentials, a query, or a fragment"
        )
    if parsed.scheme == "http" and parsed.hostname.lower() not in {
        "localhost", "127.0.0.1", "::1",
    }:
        raise ValueError(
            "SponsorBlock API URL must use HTTPS (HTTP is allowed only on loopback)"
        )
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("SponsorBlock API URL port is invalid")
    return value.rstrip("/")


def validate_sponsorblock_options(*, enabled=False, mark="", remove="", api_url=""):
    """Validate SponsorBlock mark/remove categories and a custom API base."""
    enabled = bool(enabled)
    mark = _normalize_sponsorblock_categories(mark, removal=False)
    remove = _normalize_sponsorblock_categories(remove, removal=True)
    api_url = _normalize_sponsorblock_api(api_url)
    if enabled and not mark and not remove:
        raise ValueError("Choose at least one SponsorBlock category to mark or remove")
    return {
        "enabled": enabled,
        "mark": mark,
        "remove": remove,
        "api_url": api_url,
    }


def validate_playlist_options(
    *, items="", date_after="", date_before="", match_filter="",
    max_downloads=0, archive_path="", break_on_existing=False,
):
    """Validate playlist expansion and incremental archive controls."""
    items = _safe_argument(items, "Playlist item range", max_len=512).strip()
    date_after = str(date_after or "").strip()
    date_before = str(date_before or "").strip()
    for label, value in (("Date after", date_after), ("Date before", date_before)):
        if not value:
            continue
        try:
            datetime.strptime(value, "%Y%m%d")
        except ValueError as error:
            raise ValueError(f"{label} must use YYYYMMDD") from error
    match_filter = _safe_argument(
        match_filter, "Playlist match filter", max_len=2048
    )
    try:
        max_downloads = int(max_downloads or 0)
    except (TypeError, ValueError) as error:
        raise ValueError("Maximum downloads must be a number") from error
    if not 0 <= max_downloads <= 10000:
        raise ValueError("Maximum downloads must be between 1 and 10000")
    archive_path = _safe_argument(
        archive_path, "Download archive path", max_len=4096
    ).strip()
    if break_on_existing and not archive_path:
        raise ValueError("Break-on-existing requires a download archive")
    return {
        "items": items,
        "date_after": date_after,
        "date_before": date_before,
        "match_filter": match_filter,
        "max_downloads": max_downloads,
        "archive_path": archive_path,
        "break_on_existing": bool(break_on_existing),
    }
