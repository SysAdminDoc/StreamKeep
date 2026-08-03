"""Optional yt-dlp-ytse SABR fallback integration (V34).

yt-dlp-ytse is a plugin, not a separate downloader process.  The optional
package is therefore detected from its installed distribution files and the
plugin module surface before StreamKeep adds its extractor argument.  This
also distinguishes the current UMP-only development line from releases that
still expose the SABR downloader.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from pathlib import Path
from urllib.parse import urlsplit


_DISTRIBUTION = "yt-dlp-ytse"
_SABR_ARGS = ["--extractor-args", "youtube:formats=sabr"]
_LIMITATIONS = (
    "--download-sections",
    "-N",
    "resume",
)
_SABR_FILES = (
    "yt_dlp_plugins/extractor/ytse.py",
    "yt_dlp_plugins/extractor/_ytse/sabr.py",
    "yt_dlp_plugins/extractor/_ytse/downloader/sabr.py",
)


class YtseUnavailable(RuntimeError):
    """Raised when the optional SABR-capable plugin is not installed."""


def _is_youtube_url(url):
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return False
    return host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be" or host.endswith(".youtu.be")


def _distribution():
    try:
        return importlib.metadata.distribution(_DISTRIBUTION)
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return None


def _module_probe():
    """Return ``(has_sabr, origin)`` for editable/nonstandard installs."""
    for module_name in (
        "yt_dlp_plugins.extractor._ytse.sabr",
        "yt_dlp_plugins.extractor.ytse",
    ):
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, AttributeError, ModuleNotFoundError, ValueError):
            continue
        if spec is None:
            continue
        origin = str(getattr(spec, "origin", "") or "")
        if module_name.endswith("._ytse.sabr"):
            return True, origin
        if not origin or origin in {"built-in", "frozen"}:
            continue
        try:
            source = Path(origin).read_text(encoding="utf-8", errors="replace")[:262144]
        except OSError:
            continue
        lower = source.lower()
        if "sabr" in lower and "downloader" in lower:
            return True, origin
        return False, origin
    return False, ""


def ytse_status():
    """Return the local, non-network status of the optional SABR engine."""
    dist = _distribution()
    version = str(getattr(dist, "version", "") or "") if dist else ""
    path = ""
    has_sabr = False
    if dist:
        files = {
            str(item).replace("\\", "/").lower()
            for item in (getattr(dist, "files", None) or ())
        }
        required = {item.lower() for item in _SABR_FILES}
        has_sabr = required.issubset(files)
        if has_sabr:
            try:
                path = str(dist.locate_file(_SABR_FILES[0]))
            except (OSError, ValueError):
                path = ""

    module_has_sabr, module_path = _module_probe()
    if module_has_sabr:
        has_sabr = True
    if not path:
        path = module_path

    installed = bool(dist or module_path)
    if has_sabr:
        detail = (
            f"yt-dlp-ytse {version or 'unknown version'} exposes the optional "
            "SABR downloader."
        )
    elif installed:
        detail = (
            f"yt-dlp-ytse {version or 'unknown version'} is installed, but its "
            "SABR downloader is not available; no SABR extractor argument will "
            "be sent."
        )
    else:
        detail = "Optional yt-dlp-ytse SABR fallback is not installed."
    return {
        "name": _DISTRIBUTION,
        "display_name": "yt-dlp-ytse SABR fallback",
        "installed": installed,
        "available": has_sabr,
        "supported": has_sabr,
        "version": version,
        "path": path,
        "provenance": "python-distribution" if dist else "module-path" if module_path else "missing",
        "extractor_args": list(_SABR_ARGS) if has_sabr else [],
        "limitations": list(_LIMITATIONS),
        "detail": detail,
    }


def ytse_available():
    """Return whether a SABR-capable yt-dlp-ytse surface is present."""
    return bool(ytse_status()["available"])


def ytse_extractor_args(url=""):
    """Return the SABR extractor args only for supported YouTube requests."""
    status = ytse_status()
    if not status["available"] or (url and not _is_youtube_url(url)):
        return []
    return list(_SABR_ARGS)


def ytse_fallback_blockers(*, download_sections="", concurrent_fragments=0, resume=False):
    """Return documented ytse incompatibilities for one download request."""
    blockers = []
    if str(download_sections or "").strip():
        blockers.append("--download-sections is not supported by yt-dlp-ytse")
    try:
        fragments = int(concurrent_fragments or 0)
    except (TypeError, ValueError):
        fragments = 0
    if fragments > 0:
        blockers.append("-N/--concurrent-fragments is not supported by yt-dlp-ytse")
    if resume:
        blockers.append("resume is not supported by yt-dlp-ytse")
    return blockers


def ytse_install_hint():
    """Return the optional install command without making installation implicit."""
    return (
        "Optional SABR fallback is not installed. Install it with "
        "'python -m pip install -U yt-dlp-ytse' if YouTube exposes SABR-only "
        "formats."
    )

