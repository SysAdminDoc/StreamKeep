"""Single runtime registry for security-gated modules and executables."""

from __future__ import annotations

import copy
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .paths import _CREATE_NO_WINDOW


MINIMUM_VERSIONS = {
    "yt_dlp": "2026.07.04",
    "pillow": "12.3.0",
    "curl": "8.21.0",
    "ffmpeg": "8.1.2",
    "ffprobe": "8.1.2",
}

_CACHE = None
_CACHE_LOCK = threading.Lock()


class CapabilityUnavailableError(RuntimeError):
    """Raised before an unavailable or unsafe dependency can execute."""

    def __init__(self, record):
        self.record = copy.deepcopy(record)
        super().__init__(format_capability_problem(record))


def parse_version(value):
    """Return a numeric semantic/calendar version tuple from tool output."""
    match = re.search(r"(?<!\d)(\d+(?:[.\-]\d+){1,3})(?!\d)", str(value or ""))
    if not match:
        return ()
    try:
        return tuple(int(part) for part in match.group(1).replace("-", ".").split("."))
    except ValueError:
        return ()


def version_at_least(value, minimum):
    current = parse_version(value)
    required = parse_version(minimum)
    if not current or not required:
        return False
    length = max(len(current), len(required))
    return current + (0,) * (length - len(current)) >= required + (0,) * (
        length - len(required)
    )


def get_runtime_capabilities(*, refresh=False):
    """Return exact runtime identities, versions, provenance, and readiness."""
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is None or refresh:
            _CACHE = _probe_registry()
        return copy.deepcopy(_CACHE)


def get_capability(name, *, refresh=False):
    registry = get_runtime_capabilities(refresh=refresh)
    record = registry.get(str(name))
    if record is None:
        raise KeyError(f"unknown runtime capability: {name}")
    return record


def require_capability(name, *, refresh=False):
    record = get_capability(name, refresh=refresh)
    if not record.get("supported"):
        raise CapabilityUnavailableError(record)
    return record


def resolve_tool_command(name, *, refresh=False):
    """Return the exact supported executable path for a PATH-backed tool."""
    record = require_capability(name, refresh=refresh)
    command = record.get("command") or []
    if not command:
        raise CapabilityUnavailableError(record)
    return str(command[0])


def resolve_command_prefix(name, *, refresh=False):
    """Return an exact command prefix for a supported module/executable."""
    record = require_capability(name, refresh=refresh)
    command = record.get("command") or []
    if not command:
        raise CapabilityUnavailableError(record)
    return [str(part) for part in command]


def format_capability_problem(record):
    name = record.get("display_name") or record.get("name") or "Dependency"
    if not record.get("available"):
        reason = f"{name} was not found."
    else:
        version = record.get("version") or "unknown version"
        minimum = record.get("minimum") or "a supported release"
        maximum = record.get("maximum") or ""
        if record.get("name") == "yt_dlp_ejs" and minimum:
            reason = f"{name} {version} does not match yt-dlp requirement {minimum}."
        elif maximum and parse_version(version) > parse_version(maximum):
            reason = f"{name} {version} exceeds the supported maximum {maximum}."
        else:
            reason = f"{name} {version} is below the required minimum {minimum}."
    repair = str(record.get("repair") or "").strip()
    return f"{reason} {repair}".strip()


def capability_state(record):
    if record.get("supported"):
        return "ready"
    return "unsafe" if record.get("available") else "missing"


def _probe_registry():
    yt_dlp = _probe_yt_dlp()
    pillow = _probe_module(
        "pillow", "Pillow", "PIL", MINIMUM_VERSIONS["pillow"],
        ["thumbnail-decode", "chat-render", "image-export"],
        "Install Pillow 12.3.0 or newer from the signed StreamKeep dependency set.",
    )
    curl = _probe_executable(
        "curl", ["curl"], ["--version"], MINIMUM_VERSIONS["curl"],
        ["https-fetch", "range-download", "webhook"],
        "Install curl 8.21.0 or newer and ensure that executable is first in PATH.",
    )
    ffmpeg = _probe_executable(
        "ffmpeg", ["ffmpeg"], ["-version"], MINIMUM_VERSIONS["ffmpeg"],
        ["media-download", "decode", "transcode", "mux"],
        "Install FFmpeg 8.1.2 or newer and ensure that executable is first in PATH.",
    )
    ffprobe = _probe_executable(
        "ffprobe", ["ffprobe"], ["-version"], MINIMUM_VERSIONS["ffprobe"],
        ["media-inspection", "duration-probe"],
        "Install the ffprobe 8.1.2 companion binary from the same FFmpeg build.",
    )
    ejs = _probe_ejs(yt_dlp)
    javascript = _probe_javascript_runtime()
    youtube = _aggregate_youtube(yt_dlp, ejs, javascript)
    return {
        "yt_dlp": yt_dlp,
        "yt_dlp_ejs": ejs,
        "javascript": javascript,
        "youtube": youtube,
        "pillow": pillow,
        "curl": curl,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
    }


def _base_record(
    name, display_name, kind, minimum, capabilities, repair,
    *, path="", version="", available=False, supported=False, command=None,
    provenance="missing", detail="",
):
    record = {
        "name": name,
        "display_name": display_name,
        "kind": kind,
        "path": str(path or ""),
        "version": str(version or ""),
        "minimum": str(minimum or ""),
        "provenance": provenance,
        "available": bool(available),
        "supported": bool(supported),
        "capabilities": list(capabilities),
        "command": list(command or []),
        "repair": repair,
        "detail": detail,
    }
    record["state"] = capability_state(record)
    if not record["detail"]:
        record["detail"] = (
            f"{display_name} {record['version']} at {record['path']}"
            if record["supported"] else format_capability_problem(record)
        )
    return record


def _probe_module(name, distribution, module, minimum, capabilities, repair):
    spec = None
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, AttributeError, ValueError):
        pass
    path = str(getattr(spec, "origin", "") or "") if spec else ""
    try:
        version = importlib.metadata.version(distribution) if spec else ""
    except importlib.metadata.PackageNotFoundError:
        version = ""
    available = bool(spec and path and version)
    return _base_record(
        name, distribution, "python-module", minimum, capabilities, repair,
        path=path, version=version, available=available,
        supported=available and version_at_least(version, minimum),
        provenance=_path_provenance(path, module=True) if available else "missing",
    )


def _probe_yt_dlp():
    minimum = MINIMUM_VERSIONS["yt_dlp"]
    repair = (
        'Install or update the signed dependency set with '
        '"yt-dlp[default]>=2026.07.04".'
    )
    module_record = _probe_module(
        "yt_dlp", "yt-dlp", "yt_dlp", minimum,
        ["site-extraction", "direct-download", "youtube"], repair,
    )
    if module_record.get("available"):
        module_record["command"] = (
            [sys.executable, "--internal-ytdlp"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "yt_dlp"]
        )
        return module_record
    external = _probe_executable(
        "yt_dlp", ["yt-dlp"], ["--version"], minimum,
        ["site-extraction", "direct-download", "youtube"], repair,
        display_name="yt-dlp",
    )
    return external


def _probe_ejs(yt_dlp_record):
    record = _probe_module(
        "yt_dlp_ejs", "yt-dlp-ejs", "yt_dlp_ejs", "",
        ["youtube-js-challenge-scripts"],
        'Install the matching version through pip install -U "yt-dlp[default]".',
    )
    required = _yt_dlp_ejs_requirement() if yt_dlp_record.get("available") else ""
    compatible = bool(record.get("available"))
    if compatible and required:
        compatible = _version_matches_specifier(record.get("version", ""), required)
    record["minimum"] = required
    record["required_by_ytdlp"] = required
    record["supported"] = compatible
    record["state"] = capability_state(record)
    if compatible:
        record["detail"] = (
            f"yt-dlp-ejs {record['version']} matches yt-dlp requirement "
            f"{required or '(unspecified)'} at {record['path']}"
        )
    else:
        record["detail"] = format_capability_problem(record)
    return record


def _yt_dlp_ejs_requirement():
    try:
        for requirement in importlib.metadata.requires("yt-dlp") or []:
            if requirement.lower().startswith("yt-dlp-ejs"):
                base = requirement.split(";", 1)[0].strip()
                return base[len("yt-dlp-ejs"):].strip()
    except importlib.metadata.PackageNotFoundError:
        pass
    return ""


def _version_matches_specifier(version, specifier):
    if not specifier:
        return True
    clauses = [part.strip() for part in specifier.split(",") if part.strip()]
    current = parse_version(version)
    if not current or not clauses:
        return False
    for clause in clauses:
        match = re.fullmatch(r"(===|==|!=|>=|<=|>|<)\s*([0-9][0-9.\-]*)", clause)
        if not match:
            return False
        operator, required_text = match.groups()
        required = parse_version(required_text)
        if not required:
            return False
        length = max(len(current), len(required))
        left = current + (0,) * (length - len(current))
        right = required + (0,) * (length - len(required))
        matched = {
            "===": left == right,
            "==": left == right,
            "!=": left != right,
            ">=": left >= right,
            "<=": left <= right,
            ">": left > right,
            "<": left < right,
        }[operator]
        if not matched:
            return False
    return True


def _probe_executable(
    name, candidates, version_args, minimum, capabilities, repair,
    *, display_name=None,
):
    for candidate in candidates:
        path = shutil.which(candidate)
        if not path:
            continue
        path = str(Path(path).resolve())
        output, returncode = _run_version_command(path, version_args)
        version = ".".join(str(part) for part in parse_version(output))
        available = returncode == 0 and bool(version)
        return _base_record(
            name, display_name or name, "executable", minimum, capabilities, repair,
            path=path, version=version, available=available,
            supported=available and version_at_least(version, minimum),
            command=[path], provenance=_path_provenance(path),
            detail=output.splitlines()[0][:240] if output else "",
        )
    return _base_record(
        name, display_name or name, "executable", minimum, capabilities, repair,
    )


def _run_version_command(path, args):
    try:
        result = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace", creationflags=_CREATE_NO_WINDOW,
        )
        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        return output, int(result.returncode)
    except (OSError, subprocess.SubprocessError):
        return "", -1


def _probe_javascript_runtime():
    candidates = [
        ("deno", ["deno"], "2.3.0", ""),
        ("node", ["node", "nodejs"], "22.0.0", ""),
        ("quickjs", ["qjs"], "2023.12.9", ""),
        ("bun", ["bun"], "1.2.11", "1.3.14"),
    ]
    first_unsafe = None
    for name, commands, minimum, maximum in candidates:
        record = _probe_executable(
            "javascript", commands, ["--version"], minimum,
            ["youtube-js-runtime"],
            "Install Deno 2.3+ (recommended) or Node.js 22+ and add it to PATH.",
            display_name=name,
        )
        if not record.get("available"):
            continue
        record["runtime"] = name
        record["maximum"] = maximum
        if maximum and parse_version(record.get("version")) > parse_version(maximum):
            record["supported"] = False
            record["state"] = "unsafe"
            record["detail"] = (
                f"{name} {record['version']} exceeds the supported maximum {maximum}."
            )
        if record.get("supported"):
            return record
        if first_unsafe is None:
            first_unsafe = record
    if first_unsafe:
        return first_unsafe
    missing = _base_record(
        "javascript", "JavaScript runtime", "executable", "Deno 2.3 / Node 22",
        ["youtube-js-runtime"],
        "Install Deno 2.3+ (recommended) or Node.js 22+ and add it to PATH.",
    )
    missing["runtime"] = ""
    missing["maximum"] = ""
    return missing


def _aggregate_youtube(yt_dlp, ejs, javascript):
    components = [yt_dlp, ejs, javascript]
    supported = all(item.get("supported") for item in components)
    problems = [format_capability_problem(item) for item in components if not item.get("supported")]
    paths = [item.get("path", "") for item in components if item.get("path")]
    versions = [
        f"{item.get('display_name')} {item.get('version')}"
        for item in components if item.get("version")
    ]
    return _base_record(
        "youtube", "YouTube support", "aggregate", "", ["full-youtube"],
        " ".join(item.get("repair", "") for item in components if not item.get("supported")),
        path="; ".join(paths), version=" + ".join(versions), available=bool(yt_dlp.get("available")),
        supported=supported, provenance="deterministic-local-components",
        detail=(
            "Full YouTube support uses local matching yt-dlp/EJS and the exact "
            f"{javascript.get('runtime') or 'JavaScript'} executable."
            if supported else " ".join(problems)
        ),
    )


def _path_provenance(path, *, module=False):
    if not path:
        return "missing"
    resolved = os.path.normcase(os.path.abspath(path))
    bundle_root = str(getattr(sys, "_MEIPASS", "") or "")
    if getattr(sys, "frozen", False) and bundle_root:
        try:
            if os.path.commonpath([resolved, os.path.normcase(os.path.abspath(bundle_root))]) == os.path.normcase(os.path.abspath(bundle_root)):
                return "bundled"
        except ValueError:
            pass
    if module:
        return "bundled" if getattr(sys, "frozen", False) else "python-environment"
    system_root = os.path.normcase(os.environ.get("SystemRoot", ""))
    if system_root and resolved.startswith(system_root + os.sep):
        return "operating-system"
    return "PATH"
