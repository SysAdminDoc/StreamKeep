"""lux integration — optional fallback engine for CN video platforms (V25).

lux (https://github.com/iawia002/lux) is a Go video downloader with strong
native support for Chinese platforms that yt-dlp covers unevenly: Bilibili,
Douyin, Youku, iQIYI, Tencent Video (v.qq.com), Weibo, AcFun, and others.
StreamKeep shells out to it as a separate process (never bundled), sharing the
configured output folder, cookies, and proxy. When lux is absent, callers get
a clear install hint instead of an opaque failure.

Note: lux has no proxy flag — it honours the ``HTTP_PROXY`` / ``HTTPS_PROXY``
environment variables, so proxy routing is applied by the caller's process
environment rather than in the argv.
"""

import re
import shutil

_EXECUTABLE = "lux"

# CN platforms lux handles natively. Matched against the URL host
# (case-insensitive). A routing hint, not an allow-list.
_CN_HOST_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"(?:^|\.)bilibili\.com$",
    r"(?:^|\.)b23\.tv$",
    r"(?:^|\.)douyin\.com$",
    r"(?:^|\.)iesdouyin\.com$",
    r"(?:^|\.)youku\.com$",
    r"(?:^|\.)iqiyi\.com$",
    r"(?:^|\.)v\.qq\.com$",
    r"(?:^|\.)weibo\.com$",
    r"(?:^|\.)acfun\.cn$",
    r"(?:^|\.)ixigua\.com$",
    r"(?:^|\.)kuaishou\.com$",
))


class LuxUnavailable(RuntimeError):
    """Raised when a lux operation is requested but lux is not installed."""


def lux_available():
    """Return True when the lux executable is on PATH."""
    return shutil.which(_EXECUTABLE) is not None


def lux_command_prefix():
    """Return the argv prefix that invokes lux, or raise ``LuxUnavailable``."""
    exe = shutil.which(_EXECUTABLE)
    if exe:
        return [exe]
    raise LuxUnavailable(lux_install_hint())


def lux_install_hint():
    """Return a one-line install hint for when lux is missing."""
    return (
        "lux is not installed. Install it with "
        "'go install github.com/iawia002/lux@latest' (or download a release "
        "binary and put it on PATH) to download from Chinese platforms such as "
        "Bilibili, Douyin, and Youku."
    )


def is_cn_platform(url):
    """Return True when *url*'s host is a CN platform lux is a better fit for."""
    host = _url_host(url)
    if not host:
        return False
    return any(pattern.search(host) for pattern in _CN_HOST_PATTERNS)


def _url_host(url):
    from urllib.parse import urlsplit
    try:
        host = urlsplit(str(url or "").strip()).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".").lower()


def build_lux_command(
    url,
    output_path,
    *,
    cookie="",
    info=False,
    stream_format="",
    referer="",
):
    """Build the lux argv for *url* into *output_path*.

    A URL beginning with ``-`` is rejected so it can't be smuggled as an option
    (lux has no ``--`` argument terminator). Proxy is intentionally not an argv
    flag; set ``HTTP_PROXY``/``HTTPS_PROXY`` in the child environment instead.
    """
    text = str(url or "").strip()
    if not text:
        raise ValueError("lux requires a URL")
    if text.startswith("-"):
        raise ValueError("Download URL cannot begin with a dash")

    cmd = lux_command_prefix()
    if output_path:
        cmd += ["--output-path", str(output_path)]
    if cookie:
        cmd += ["--cookie", str(cookie)]
    if stream_format:
        cmd += ["--stream-format", str(stream_format)]
    if referer:
        cmd += ["--refer", str(referer)]
    if info:
        cmd += ["--info"]
    cmd.append(text)
    return cmd
