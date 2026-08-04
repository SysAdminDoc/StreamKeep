"""Pure utility functions — no Qt imports, safe for any module to use."""

import os
import re
import shutil
import sys
import string
from pathlib import Path


def sanitize_xml_text(value):
    """Remove characters that XML 1.0 cannot represent.

    The same sanitized text is safe to pass through the XML and HTML
    escapers used by the publishing renderers. XML permits tab, line feed,
    carriage return, and the Unicode ranges below; all other code points are
    omitted so strict XML consumers can parse scraped metadata reliably.
    """
    text = str(value or "")
    return "".join(
        char
        for char in text
        if char in "\t\n\r"
        or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD
        or 0x10000 <= ord(char) <= 0x10FFFF
    )


def canonical_webpage_url(value, *, platform="", source_id="", channel=""):
    """Return the shared canonical public page URL for a source."""
    # The metadata module owns the privacy policy and identity derivation;
    # this lazy bridge keeps low-level utilities usable without importing Qt.
    from .metadata import canonical_webpage_url as _canonical_webpage_url

    return _canonical_webpage_url(
        value,
        platform=platform,
        source_id=source_id,
        channel=channel,
    )


def canonicalize_url(value, *, platform="", source_id="", channel=""):
    """Compatibility name for callers that need a stable URL key."""
    return canonical_webpage_url(
        value,
        platform=platform,
        source_id=source_id,
        channel=channel,
    )


def canonical_media_identity(
    platform="", source_id="", webpage_url="", channel="",
):
    """Return ``(platform, source_id, webpage_url)`` as one stable value."""
    from .metadata import build_archival_provenance
    from .models import StreamInfo

    provenance = build_archival_provenance(
        StreamInfo(
            platform=platform,
            source_id=source_id,
            webpage_url=webpage_url,
            channel=channel,
        ),
        source_url=webpage_url,
    )
    return (
        provenance.platform,
        provenance.source_id,
        provenance.webpage_url,
    )


def free_space_bytes(path):
    """Return free bytes on the disk containing `path`, or None on error.

    Walks up the path if needed (the target dir may not exist yet)."""
    if not path:
        return None
    probe = path
    for _ in range(6):
        try:
            return shutil.disk_usage(probe).free
        except (FileNotFoundError, OSError, ValueError):
            parent = os.path.dirname(probe)
            if not parent or parent == probe:
                return None
            probe = parent
    return None


def estimate_download_bytes(stream_info):
    """Rough estimate of total download size from a StreamInfo, based on
    manifest bandwidth × total_secs × 1.05 container overhead. Returns
    None when we can't make a meaningful guess (no duration / no
    bandwidth). Picks the highest bandwidth quality — matches the UI's
    default selection."""
    if not stream_info:
        return None
    total_secs = float(getattr(stream_info, "total_secs", 0) or 0)
    if total_secs <= 0:
        return None
    qualities = getattr(stream_info, "qualities", None) or []
    bandwidths = [int(getattr(q, "bandwidth", 0) or 0) for q in qualities]
    bw = max(bandwidths) if bandwidths else 0
    if bw <= 0:
        return None
    return int((bw / 8.0) * total_secs * 1.05)


DEFAULT_FOLDER_TEMPLATE = "{channel}/{date} - {title}"
DEFAULT_FILE_TEMPLATE = "{title}"
WINDOWS_SAFE_PATH_LENGTH = 240
POSIX_SAFE_PATH_LENGTH = 4096
MAX_PATH_COMPONENT_BYTES = 240


def platform_path_limit():
    """Return the conservative full-path limit for the current platform."""
    return WINDOWS_SAFE_PATH_LENGTH if os.name == "nt" else POSIX_SAFE_PATH_LENGTH


def truncate_utf8_bytes(value, max_bytes=MAX_PATH_COMPONENT_BYTES):
    """Trim *value* to a UTF-8 byte limit without splitting a code point."""
    text = str(value or "")
    try:
        limit = max(0, int(max_bytes))
    except (TypeError, ValueError, OverflowError):
        limit = MAX_PATH_COMPONENT_BYTES
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


class OutputPathError(ValueError):
    """A rendered output path cannot be used safely on this platform."""

    def __init__(self, path, *, kind="path", limit=0, actual=0):
        self.code = "path_too_long"
        self.path = str(path)
        self.offending_path = self.path
        self.kind = str(kind or "path")
        self.limit = int(limit or 0)
        self.actual = int(actual or 0)
        subject = "Output path component" if self.kind == "component" else "Output path"
        unit = "UTF-8 bytes" if self.kind == "component" else "platform path units"
        super().__init__(
            f"[PREFLIGHT:path_too_long] {subject} exceeds the safe platform "
            f"limit ({self.actual} {unit} > {self.limit}): {self.path}"
        )


# The longer name is useful to callers that want to make the preflight stage
# explicit without creating a second exception type.
OutputPathPreflightError = OutputPathError


class TemplateRenderError(ValueError):
    """A template cannot be rendered without silently changing its value."""

    def __init__(self, code, message, *, field=""):
        super().__init__(message)
        self.code = str(code or "invalid_template")
        self.field = str(field or "")


_TEMPLATE_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_TEMPLATE_INVALID_CHARS = set('<>:"/\\|?*')


def _strict_template_component(
    value, *, max_len=80, field="", max_bytes=MAX_PATH_COMPONENT_BYTES,
):
    """Validate one rendered Windows path component without sanitizing it."""
    rendered = str(value or "")
    if not rendered or not rendered.strip():
        raise TemplateRenderError(
            "unresolvable_field", "Template rendered an empty path component",
            field=field,
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in rendered):
        raise TemplateRenderError(
            "invalid_character", "Template contains a control character",
            field=field,
        )
    if any(char in _TEMPLATE_INVALID_CHARS for char in rendered):
        raise TemplateRenderError(
            "invalid_character", "Template contains a Windows-invalid character",
            field=field,
        )
    if rendered in {".", ".."}:
        raise TemplateRenderError(
            "reserved_name", "Template contains a traversal component", field=field
        )
    if rendered != rendered.rstrip(". "):
        raise TemplateRenderError(
            "reserved_name", "Template ends with a dot or space", field=field
        )
    stem = rendered.split(".", 1)[0].upper()
    if stem in _TEMPLATE_RESERVED_NAMES:
        raise TemplateRenderError(
            "reserved_name", f"Template uses reserved Windows name {rendered!r}",
            field=field,
        )
    if len(rendered) > int(max_len):
        raise TemplateRenderError(
            "component_too_long",
            f"Template component is {len(rendered)} characters; maximum is {max_len}",
            field=field,
        )
    encoded_length = len(rendered.encode("utf-8"))
    if encoded_length > int(max_bytes):
        raise TemplateRenderError(
            "component_too_long",
            f"Template component is {encoded_length} UTF-8 bytes; maximum is {max_bytes}",
            field=field,
        )
    return rendered


def render_template_strict(template, context, *, max_component=80):
    """Render a template for archive migration without lossy sanitization.

    The normal download renderer intentionally cleans user metadata. A
    migration must refuse values that would be cleaned or truncated, because
    a preview must describe the exact destination that will be applied.
    """
    template = str(template or "")
    if not template:
        return []
    if "\\" in template or template.startswith("/") or os.path.isabs(template):
        raise TemplateRenderError(
            "invalid_template", "Templates may contain only relative '/' separators"
        )
    formatter = string.Formatter()
    result = []
    for segment in template.split("/"):
        if not segment:
            raise TemplateRenderError(
                "invalid_template", "Templates may not contain empty path components"
            )
        fields = []
        try:
            for _literal, field_name, format_spec, conversion in formatter.parse(segment):
                if field_name is None:
                    continue
                if (
                    not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field_name)
                    or format_spec
                    or conversion
                ):
                    raise TemplateRenderError(
                        "unsupported_field", f"Unsupported template field {field_name!r}",
                        field=field_name,
                    )
                fields.append(field_name)
                if field_name not in context or context.get(field_name) in (None, ""):
                    raise TemplateRenderError(
                        "unresolvable_field",
                        f"Template field {field_name!r} has no value",
                        field=field_name,
                    )
            rendered = formatter.vformat(segment, (), dict(context))
        except TemplateRenderError:
            raise
        except (KeyError, IndexError, ValueError) as exc:
            field = str(exc.args[0]) if getattr(exc, "args", None) else ""
            raise TemplateRenderError(
                "unresolvable_field", f"Template field {field!r} could not be resolved",
                field=field,
            ) from exc
        result.append(
            _strict_template_component(
                rendered, max_len=max_component, field=fields[-1] if fields else ""
            )
        )
    return result


def fmt_duration(secs):
    """Format seconds as Xh Ym Zs."""
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def fmt_size(b):
    """Format bytes as a human-readable string."""
    for unit in ('B', 'KB', 'MB', 'GB'):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def safe_filename(s, max_len=60, max_bytes=MAX_PATH_COMPONENT_BYTES):
    """Sanitize a string for use as a filename. Strips invalid chars,
    control chars, trailing dots/spaces (invalid on Windows), template
    braces left behind by render_template fallbacks, and truncates."""
    if not s:
        return ""
    # Drop NT-invalid chars, control chars, and {} left over from templates.
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f{}]', '', s)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = cleaned.rstrip(". ")
    reserved = (
        {"CON", "PRN", "AUX", "NUL"}
        | {f"COM{i}" for i in range(1, 10)}
        | {f"LPT{i}" for i in range(1, 10)}
    )
    if cleaned.upper() in reserved:
        cleaned = f"_{cleaned}"
    # Truncate, then re-strip trailing whitespace/dots exposed by the cut.
    cleaned = cleaned[:max_len].rstrip(". ")
    cleaned = truncate_utf8_bytes(cleaned, max_bytes).rstrip(". ")
    return cleaned or "download"


def safe_path_component(
    s, max_len=80, max_bytes=MAX_PATH_COMPONENT_BYTES,
):
    """Sanitize a path component (filename or folder name)."""
    return safe_filename(
        s, max_len=max_len, max_bytes=max_bytes,
    ) or "download"


def _path_components(path):
    """Yield non-root components from a Windows or POSIX path."""
    _drive, tail = os.path.splitdrive(str(path))
    return (
        part for part in re.split(r"[\\/]", tail)
        if part and part not in {".", os.curdir}
    )


def _expected_output_names(file_base):
    """Return media and sidecar names that may be written for one capture."""
    base = os.path.basename(str(file_base or "recording")) or "recording"
    return [
        f"{base}.{suffix}"
        for suffix in (
            "mp4", "mkv", "webm", "ts", "nfo", "chapters.txt",
            "chapters.json", "markers.json", "chat.json", "chat.txt",
            "info.json", "description", "vtt",
        )
    ] + [
        "metadata.json", "thumbnail.jpg", ".streamkeep_manifest.json",
        ".streamkeep_resume.json",
    ]


def validate_output_path(
    output_dir,
    *,
    file_base="",
    expected_names=(),
    max_path_bytes=None,
    max_component_bytes=MAX_PATH_COMPONENT_BYTES,
):
    """Validate an output directory and all expected sibling paths.

    The returned value is the absolute output directory.  ``file_base`` adds
    the normal media and metadata sidecars to the candidate set, while
    ``expected_names`` lets a caller add format-specific files.  Validation is
    deliberately conservative on Windows so a later sidecar write cannot be
    the first operation to discover an unsafe destination.
    """
    root = os.path.abspath(os.fspath(output_dir or ""))
    try:
        path_limit = int(
            platform_path_limit() if max_path_bytes is None else max_path_bytes
        )
        component_limit = int(max_component_bytes)
    except (TypeError, ValueError, OverflowError):
        path_limit = platform_path_limit()
        component_limit = MAX_PATH_COMPONENT_BYTES
    candidates = [root]
    names = []
    if file_base:
        names.extend(_expected_output_names(file_base))
    if isinstance(expected_names, (str, bytes, os.PathLike)):
        expected_names = [expected_names]
    names.extend(expected_names or [])
    for name in names:
        candidate = os.fspath(name)
        if not os.path.isabs(candidate):
            candidate = os.path.join(root, candidate)
        candidates.append(os.path.abspath(candidate))

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        path_length = (
            len(candidate)
            if os.name == "nt"
            else len(candidate.encode("utf-8"))
        )
        if path_length > path_limit:
            raise OutputPathError(
                candidate, kind="path", limit=path_limit, actual=path_length,
            )
        for component in _path_components(candidate):
            component_length = len(component.encode("utf-8"))
            if component_length > component_limit:
                raise OutputPathError(
                    candidate,
                    kind="component",
                    limit=component_limit,
                    actual=component_length,
                )
    return root


def user_videos_dir():
    """Return the current user's Videos folder (platform-specific).
    On Windows queries SHGetKnownFolderPath for FOLDERID_Videos to honor
    redirected / OneDrive-mapped profiles. Falls back to ~/Videos (Linux,
    Windows default) or ~/Movies (macOS)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            FOLDERID_Videos = _GUID(
                0x18989B1D, 0x99B5, 0x455B,
                (ctypes.c_ubyte * 8)(0x84, 0x1C, 0xAB, 0x7C, 0x74, 0xE4, 0xDD, 0xFC),
            )
            SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
            SHGetKnownFolderPath.argtypes = [
                ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE,
                ctypes.POINTER(ctypes.c_wchar_p),
            ]
            SHGetKnownFolderPath.restype = ctypes.HRESULT
            out_ptr = ctypes.c_wchar_p()
            hr = SHGetKnownFolderPath(
                ctypes.byref(FOLDERID_Videos), 0, 0, ctypes.byref(out_ptr)
            )
            if hr == 0 and out_ptr.value:
                result = Path(out_ptr.value)
                ctypes.windll.ole32.CoTaskMemFree(out_ptr)
                return result
        except Exception:
            pass
        return Path.home() / "Videos"
    if sys.platform == "darwin":
        return Path.home() / "Movies"
    # Linux / BSD: honor XDG_VIDEOS_DIR
    xdg_config = Path.home() / ".config" / "user-dirs.dirs"
    try:
        if xdg_config.exists():
            text = xdg_config.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'XDG_VIDEOS_DIR\s*=\s*"(.+)"', text)
            if m:
                path = m.group(1).replace("$HOME", str(Path.home()))
                return Path(path)
    except Exception:
        pass
    return Path.home() / "Videos"


def default_output_dir():
    """Default root directory for new downloads: <User Videos>/StreamKeep."""
    return user_videos_dir() / "StreamKeep"


def render_template(template, context):
    """Render a filename template. Each path segment is sanitized
    separately so that '{channel}/{title}' produces a valid nested path.
    Returns a list of path components (to be joined with os.path.join)."""
    if not template:
        return []
    result = []
    for segment in template.split("/"):
        if not segment.strip():
            continue
        try:
            rendered = segment.format(**context)
        except (KeyError, IndexError, ValueError):
            def safe_sub(m):
                key = m.group(1)
                return str(context.get(key, "")) or m.group(0)
            rendered = re.sub(r'\{(\w+)\}', safe_sub, segment)
        rendered = safe_path_component(rendered)
        if rendered:
            result.append(rendered)
    return result


def resolve_output_paths(
    stream_info,
    output_root,
    *,
    vod_info=None,
    folder_template="",
    file_template="",
    config=None,
):
    """Resolve one download's folder and base filename from the templates.

    The single template code path shared by the GUI, the CLI, and monitor
    jobs (V39). Precedence is explicit override, then the configured global
    default, then the built-in default, so a headless run and a desktop run
    with the same configuration name a file identically.

    Returns ``(output_dir, base_name)``; *base_name* carries no extension.
    """
    config = config or {}
    folder = str(
        folder_template
        or config.get("folder_template", "")
        or DEFAULT_FOLDER_TEMPLATE
    )
    filename = str(
        file_template
        or config.get("file_template", "")
        or DEFAULT_FILE_TEMPLATE
    )
    context = build_template_context(stream_info, vod_info)
    folder_parts = render_template(folder, context)
    file_parts = render_template(filename, context)

    base = file_parts[-1] if file_parts else ""
    if not base:
        title = (stream_info.title if stream_info else "") or ""
        platform = (stream_info.platform if stream_info else "") or "media"
        base = safe_filename(title) or f"{platform}_download"

    # A file template may itself contain directories ("{channel}/{title}");
    # everything before the last component belongs to the folder path.
    parts = list(folder_parts) + list(file_parts[:-1])
    output_dir = os.path.join(str(output_root), *parts) if parts else str(output_root)
    return output_dir, base


def build_template_context(stream_info, vod_info=None):
    """Build the variable dict for template rendering.
    Variables: {title}, {channel}, {platform}, {date}, {year}, {month},
    {day}, {id}, {quality}, {ext}"""
    from datetime import datetime as _dt
    now = _dt.now()
    title = (stream_info.title if stream_info else "") or (
        vod_info.title if vod_info else "") or "download"
    channel = ""
    if vod_info and vod_info.channel:
        channel = vod_info.channel
    elif stream_info and stream_info.channel:
        channel = stream_info.channel
    date_str = ""
    try:
        if stream_info and stream_info.start_time:
            dt = _dt.fromisoformat(stream_info.start_time.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        elif vod_info and vod_info.date:
            raw = vod_info.date.replace("T", " ").split(".")[0].split("+")[0]
            dt = _dt.fromisoformat(raw[:19])
            date_str = dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    if not date_str:
        date_str = now.strftime("%Y-%m-%d")
    year, month, day = date_str.split("-") if "-" in date_str else ("", "", "")
    return {
        "title": title,
        "channel": channel or "unknown",
        "platform": stream_info.platform if stream_info else "",
        "date": date_str,
        "year": year,
        "month": month,
        "day": day,
        "id": "",
        "quality": "",
        "ext": "mp4",
    }


def scan_browser_cookies():
    """Scan for installed browsers with cookie stores.
    Returns list of (display_name, ytdlp_name, path)."""
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    browsers = [
        ("Chrome", "chrome", [
            os.path.join(local, "Google", "Chrome", "User Data", "Default", "Cookies"),
            os.path.join(local, "Google", "Chrome", "User Data", "Default", "Network", "Cookies"),
        ]),
        ("Chromium", "chromium", [
            os.path.join(local, "Chromium", "User Data", "Default", "Cookies"),
            os.path.join(local, "Chromium", "User Data", "Default", "Network", "Cookies"),
        ]),
        ("Firefox", "firefox", [
            os.path.join(roaming, "Mozilla", "Firefox", "Profiles"),
        ]),
        ("Edge", "edge", [
            os.path.join(local, "Microsoft", "Edge", "User Data", "Default", "Cookies"),
            os.path.join(local, "Microsoft", "Edge", "User Data", "Default", "Network", "Cookies"),
        ]),
        ("Brave", "brave", [
            os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Cookies"),
            os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Network", "Cookies"),
        ]),
        ("Opera", "opera", [
            os.path.join(roaming, "Opera Software", "Opera Stable", "Cookies"),
            os.path.join(roaming, "Opera Software", "Opera Stable", "Network", "Cookies"),
        ]),
        ("Opera GX", "opera", [
            os.path.join(roaming, "Opera Software", "Opera GX Stable", "Cookies"),
            os.path.join(roaming, "Opera Software", "Opera GX Stable", "Network", "Cookies"),
        ]),
        ("Vivaldi", "vivaldi", [
            os.path.join(local, "Vivaldi", "User Data", "Default", "Cookies"),
            os.path.join(local, "Vivaldi", "User Data", "Default", "Network", "Cookies"),
        ]),
        ("LibreWolf", "firefox", [
            os.path.join(roaming, "librewolf", "Profiles"),
        ]),
        ("Waterfox", "firefox", [
            os.path.join(roaming, "Waterfox", "Profiles"),
        ]),
    ]
    found = []
    seen_ytdlp = set()
    for display, ytdlp_name, paths in browsers:
        for p in paths:
            if os.path.exists(p):
                actual_ytdlp = ytdlp_name
                if ytdlp_name == "firefox" and display != "Firefox":
                    if os.path.isdir(p):
                        for entry in os.listdir(p):
                            profile_dir = os.path.join(p, entry)
                            if os.path.isdir(profile_dir) and os.path.exists(
                                os.path.join(profile_dir, "cookies.sqlite")
                            ):
                                actual_ytdlp = f"firefox:{profile_dir}"
                                break
                key = actual_ytdlp if actual_ytdlp.startswith("firefox:") else ytdlp_name
                if key not in seen_ytdlp:
                    found.append((display, actual_ytdlp, p))
                    seen_ytdlp.add(key)
                break
    return found
