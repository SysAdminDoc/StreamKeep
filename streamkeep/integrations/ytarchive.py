"""ytarchive integration — optional from-start YouTube live engine (V36).

ytarchive (https://github.com/Kethsar/ytarchive) exists for one job StreamKeep's
yt-dlp path does unevenly: recording a YouTube livestream from its start without
dropping fragments on an unstable connection. It is never bundled; when it is
absent every caller degrades to the existing yt-dlp behaviour and says so.

Following the gallery-dl/lux pattern: detection by PATH, an explicit install
hint, and a pure argv builder that is fully testable without the binary.
"""

import re
import shutil

_EXECUTABLE = "ytarchive"

_YOUTUBE_HOST_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"(?:^|\.)youtube\.com$",
    r"(?:^|\.)youtu\.be$",
))

# ytarchive quality selectors, best first. "best" is always valid.
QUALITY_SELECTORS = ("best", "1080p", "720p", "480p", "360p", "audio_only")


class YtArchiveUnavailable(RuntimeError):
    """Raised when a ytarchive operation is requested but it is not installed."""


def ytarchive_available():
    """Return True when the ytarchive executable is on PATH."""
    return shutil.which(_EXECUTABLE) is not None


def ytarchive_command_prefix():
    """Return the argv prefix that invokes ytarchive, or raise."""
    exe = shutil.which(_EXECUTABLE)
    if exe:
        return [exe]
    raise YtArchiveUnavailable(ytarchive_install_hint())


def ytarchive_install_hint():
    """Return a one-line install hint for when ytarchive is missing."""
    return (
        "ytarchive is not installed. Install it with "
        "'go install github.com/Kethsar/ytarchive@latest' (or download a "
        "release binary and put it on PATH) to capture YouTube livestreams "
        "from the start when yt-dlp drops fragments."
    )


def is_youtube_live_url(url):
    """Return True when *url* is a YouTube address ytarchive can record."""
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(pattern.search(host) for pattern in _YOUTUBE_HOST_PATTERNS)


def normalize_quality(quality):
    """Map a StreamKeep quality preference onto a ytarchive selector."""
    text = str(quality or "").strip().lower()
    if not text or text in ("best", "source", "highest"):
        return "best"
    if text in ("audio", "audio_only", "audioonly", "bestaudio"):
        return "audio_only"
    match = re.search(r"(\d{3,4})p?", text)
    if match:
        candidate = f"{match.group(1)}p"
        if candidate in QUALITY_SELECTORS:
            return candidate
    return "best"


def build_ytarchive_command(
    url,
    output_template,
    *,
    quality="best",
    cookies="",
    proxy="",
    wait=False,
    retry_stream_secs=0,
    merge=True,
):
    """Build the ytarchive argv for a from-start capture of *url*.

    A URL beginning with ``-`` is rejected so it cannot be smuggled as an
    option — ytarchive has no ``--`` argument terminator. The quality selector
    is a trailing positional and is normalized to a value ytarchive accepts.
    """
    text = str(url or "").strip()
    if not text:
        raise ValueError("ytarchive requires a URL")
    if text.startswith("-"):
        raise ValueError("Download URL cannot begin with a dash")
    if not str(output_template or "").strip():
        raise ValueError("ytarchive requires an output template")

    cmd = ytarchive_command_prefix()
    cmd += ["--output", str(output_template)]
    if merge:
        cmd.append("--merge")
    if cookies:
        cmd += ["--cookies", str(cookies)]
    if proxy:
        cmd += ["--proxy", str(proxy)]
    if wait:
        cmd.append("--wait")
    try:
        retry = int(retry_stream_secs or 0)
    except (TypeError, ValueError):
        retry = 0
    if retry > 0:
        cmd += ["--retry-stream", str(retry)]
    cmd += [text, normalize_quality(quality)]
    return cmd
