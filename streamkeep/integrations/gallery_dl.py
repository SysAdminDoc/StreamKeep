"""gallery-dl integration — optional second engine for image galleries and
social-media posts (V10).

gallery-dl (https://github.com/mikf/gallery-dl) downloads image/media
collections from sites the video pipeline handles poorly: Twitter/X media,
Instagram posts, Pixiv, boorus, DeviantArt, Reddit galleries, Tumblr, Flickr,
and more. StreamKeep shells out to it as a separate process (never bundled),
sharing the configured output folder, download-archive, cookies, and proxy.

When gallery-dl is absent, callers get a clear install hint via
``gallery_dl_install_hint()`` instead of an opaque failure.
"""

import importlib.util
import re
import shutil
import sys

_MODULE = "gallery_dl"
_EXECUTABLE = "gallery-dl"

# Hosts gallery-dl covers well that are a poor fit for the streaming/ffmpeg
# pipeline. Matched against the URL host (case-insensitive). This is a routing
# hint, not an allow-list — gallery-dl itself supports far more sites.
_GALLERY_HOST_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"(?:^|\.)twitter\.com$",
    r"(?:^|\.)x\.com$",
    r"(?:^|\.)nitter\.",
    r"(?:^|\.)instagram\.com$",
    r"(?:^|\.)pixiv\.net$",
    r"(?:^|\.)deviantart\.com$",
    r"(?:^|\.)flickr\.com$",
    r"(?:^|\.)tumblr\.com$",
    r"(?:^|\.)redgifs\.com$",
    r"(?:^|\.)imgur\.com$",
    r"(?:^|\.)artstation\.com$",
    r"(?:^|\.)newgrounds\.com$",
    r"(?:^|\.)gelbooru\.com$",
    r"(?:^|\.)safebooru\.org$",
    r"(?:^|\.)danbooru\.donmai\.us$",
    r"(?:^|\.)konachan\.com$",
    r"(?:^|\.)yande\.re$",
))


class GalleryDlUnavailable(RuntimeError):
    """Raised when a gallery-dl operation is requested but it is not installed."""


def gallery_dl_available():
    """Return True when gallery-dl can be invoked (module or executable)."""
    try:
        if importlib.util.find_spec(_MODULE) is not None:
            return True
    except (ImportError, ValueError):
        pass
    return shutil.which(_EXECUTABLE) is not None


def gallery_dl_command_prefix():
    """Return the argv prefix that invokes gallery-dl.

    Prefers ``python -m gallery_dl`` (same interpreter, version we detected)
    and falls back to a PATH executable. Raises ``GalleryDlUnavailable`` when
    neither is present.
    """
    try:
        if importlib.util.find_spec(_MODULE) is not None:
            return [sys.executable, "-m", _MODULE]
    except (ImportError, ValueError):
        pass
    exe = shutil.which(_EXECUTABLE)
    if exe:
        return [exe]
    raise GalleryDlUnavailable(gallery_dl_install_hint())


def gallery_dl_install_hint():
    """Return a one-line install hint for when gallery-dl is missing."""
    return (
        "gallery-dl is not installed. Install it with "
        "'python -m pip install -U gallery-dl' to download image galleries and "
        "social-media posts (Twitter/X, Instagram, Pixiv, boorus, and more)."
    )


def is_gallery_host(url):
    """Return True when *url*'s host is one gallery-dl is a better fit for."""
    host = _url_host(url)
    if not host:
        return False
    return any(pattern.search(host) for pattern in _GALLERY_HOST_PATTERNS)


def _url_host(url):
    from urllib.parse import urlsplit
    try:
        host = urlsplit(str(url or "").strip()).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".").lower()


def build_gallery_dl_command(
    url,
    dest_dir,
    *,
    archive_path="",
    cookies_file="",
    proxy="",
    simulate=False,
    rate_limit="",
    extra_options=None,
):
    """Build the gallery-dl argv for *url* into *dest_dir*.

    Shares StreamKeep's output folder, download-archive, cookies, and proxy so
    galleries land alongside video downloads and re-runs skip already-fetched
    files. A URL beginning with ``-`` is rejected so it can't be smuggled as an
    option (gallery-dl has no ``--`` argument terminator).
    """
    text = str(url or "").strip()
    if not text:
        raise ValueError("gallery-dl requires a URL")
    if text.startswith("-"):
        raise ValueError("Download URL cannot begin with a dash")

    cmd = gallery_dl_command_prefix()
    if dest_dir:
        cmd += ["--destination", str(dest_dir)]
    if archive_path:
        cmd += ["--download-archive", str(archive_path)]
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]
    if proxy:
        cmd += ["--proxy", str(proxy)]
    if rate_limit:
        cmd += ["--limit-rate", str(rate_limit)]
    if simulate:
        cmd += ["--simulate"]
    for key, value in (extra_options or {}).items():
        cmd += ["--option", f"{key}={value}"]
    cmd.append(text)
    return cmd
