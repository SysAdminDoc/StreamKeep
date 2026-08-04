"""Optional, guarded Streamlink live-capture integration (V13).

Streamlink is deliberately not a required StreamKeep dependency.  When the
user installs a recent enough release, this module provides an in-process
Twitch/Kick live path with Streamlink's plugin handling, ad filtering, low
latency, and HLS DVR options.  Every HTTP request is sent through the same
short-lived SSRF-guarded proxy used by the native downloader.  The wrapped
HTTP session is also checked before each request so a nested ``file://`` URI
cannot fall through to an engine-specific file loader.

The public surface is intentionally small and testable without Streamlink:
session option construction, availability checks, stream selection, status
probing, and a byte-copy/remux capture handle.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import re
import subprocess
from dataclasses import dataclass

from ..har import normalize_replay_headers
from ..net_guard import (
    GuardedHTTPProxy,
    RemoteURLPolicyError,
    validate_remote_url,
)
from ..paths import _CREATE_NO_WINDOW

MIN_STREAMLINK_VERSION = (8, 4, 0)
SUPPORTED_PLATFORMS = frozenset({"twitch", "kick"})
_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


class StreamlinkUnavailable(RuntimeError):
    """Raised when the optional Streamlink engine is not usable."""


class StreamlinkNoStream(RuntimeError):
    """Raised when Streamlink resolves a source but reports no live stream."""


class StreamlinkSecurityError(RemoteURLPolicyError):
    """Raised when Streamlink exposes a non-remote stream target."""


@dataclass(frozen=True)
class StreamlinkOptions:
    """Bounded options for one live capture.

    Streamlink's Twitch ad filtering is mandatory in current releases, so
    there is intentionally no disable-ads option here.  ``start_offset`` is
    the HLS DVR offset in seconds; ``live_restart`` takes precedence when set.
    """

    quality: str = "best"
    low_latency: bool = True
    start_offset: float = 0.0
    live_restart: bool = False

    def normalized(self) -> "StreamlinkOptions":
        quality = str(self.quality or "best").strip().lower() or "best"
        if quality in {"source", "highest"}:
            quality = "best"
        elif quality in {"lowest", "minimum"}:
            quality = "worst"
        try:
            offset = max(0.0, float(self.start_offset or 0.0))
        except (TypeError, ValueError):
            offset = 0.0
        return StreamlinkOptions(
            quality=quality,
            low_latency=bool(self.low_latency),
            start_offset=offset,
            live_restart=bool(self.live_restart),
        )


@dataclass(frozen=True)
class StreamlinkCaptureResult:
    """Result of copying a Streamlink stream to a local staging file."""

    path: str
    bytes_written: int = 0
    stopped: bool = False


def _version_tuple(value) -> tuple[int, int, int]:
    match = _VERSION_RE.search(str(value or ""))
    if not match:
        return (0, 0, 0)
    return tuple(int(match.group(index) or 0) for index in range(1, 4))


def streamlink_version() -> str:
    """Return the installed Streamlink version, or ``""`` when absent."""
    try:
        return importlib.metadata.version("streamlink")
    except importlib.metadata.PackageNotFoundError:
        try:
            module = importlib.import_module("streamlink")
        except ImportError:
            return ""
        return str(getattr(module, "__version__", "") or "")
    except Exception:
        return ""


def streamlink_install_hint() -> str:
    """Return the optional-install guidance shown by the settings UI."""
    return (
        "Streamlink 8.4 or newer is not installed. Install the optional live "
        "engine with 'py -m pip install \"streamlink>=8.4,<9\"'."
    )


def streamlink_available() -> bool:
    """Return whether a security-supported Streamlink import is available."""
    version = streamlink_version()
    if not version or _version_tuple(version) < MIN_STREAMLINK_VERSION:
        return False
    try:
        importlib.import_module("streamlink")
    except ImportError:
        return False
    return True


def build_session_options(
    options: StreamlinkOptions | None = None,
    *,
    proxy_url: str = "",
    request_headers=None,
) -> dict:
    """Build the bounded Streamlink session options for one capture."""
    normalized = (options or StreamlinkOptions()).normalized()
    values = {
        # Never inherit HTTP_PROXY/NETRC from the interactive environment.
        "http-trust-env": False,
        # StreamKeep cannot permit a plugin to launch a browser from a worker.
        "webbrowser": False,
        "hls-live-edge": 2 if normalized.low_latency else 3,
        "hls-segment-stream-data": bool(normalized.low_latency),
        "hls-start-offset": normalized.start_offset,
        "hls-live-restart": bool(normalized.live_restart),
    }
    if proxy_url:
        values["http-proxy"] = str(proxy_url)
    headers = normalize_replay_headers(request_headers)
    if headers:
        values["http-headers"] = headers
    return values


def _validated_source(url: str) -> str:
    target = validate_remote_url(url)
    return target.url


def _stream_url_values(stream, *, seen=None):
    """Yield URL-like values from a Streamlink stream object.

    Muxed streams expose nested ``video``/``audio`` objects.  Checking those
    as well as the top-level URL keeps the boundary closed before any bytes
    are read.
    """
    seen = seen or set()
    marker = id(stream)
    if marker in seen:
        return
    seen.add(marker)
    url = getattr(stream, "url", None)
    if url:
        yield str(url)
    for name in ("video", "audio", "streams"):
        child = getattr(stream, name, None)
        if isinstance(child, dict):
            children = child.values()
        elif isinstance(child, (list, tuple, set)):
            children = child
        else:
            children = (child,) if child is not None else ()
        for value in children:
            yield from _stream_url_values(value, seen=seen)


def validate_streams(streams: dict) -> dict:
    """Reject empty or non-HTTP(S) Streamlink results and return the mapping."""
    if not isinstance(streams, dict) or not streams:
        raise StreamlinkNoStream("Streamlink found no live streams")
    for name, stream in streams.items():
        urls = tuple(_stream_url_values(stream))
        if not urls:
            raise StreamlinkSecurityError(
                f"Streamlink stream {name!r} has no remote URL"
            )
        for url in urls:
            try:
                _validated_source(url)
            except RemoteURLPolicyError as error:
                raise StreamlinkSecurityError(
                    f"Streamlink returned an unsafe nested URL: {error}"
                ) from error
    return streams


def select_stream(streams: dict, quality: str = "best"):
    """Select a named Streamlink stream with deterministic best/worst fallback."""
    validate_streams(streams)
    wanted = str(quality or "best").strip().lower() or "best"
    if wanted in {"source", "highest"}:
        wanted = "best"
    elif wanted in {"lowest", "minimum"}:
        wanted = "worst"
    if wanted in streams:
        return streams[wanted]
    lowered = {str(key).casefold(): value for key, value in streams.items()}
    if wanted in lowered:
        return lowered[wanted]
    for fallback in ("best", "worst"):
        if fallback in lowered:
            return lowered[fallback]
    return next(iter(streams.values()))


class StreamlinkCapture:
    """A selected Streamlink stream and the guard that protects its requests."""

    def __init__(self, stream, session, proxy):
        self.stream = stream
        self.session = session
        self.proxy = proxy
        self._reader = None

    def copy_to(
        self,
        path,
        *,
        cancel_check=None,
        progress_cb=None,
        chunk_size=1024 * 1024,
    ) -> StreamlinkCaptureResult:
        """Copy stream bytes to *path* until EOF or cancellation."""
        destination = str(path)
        total = 0
        stopped = False
        try:
            self._reader = self.stream.open()
            with open(destination, "wb") as output:
                while True:
                    if cancel_check is not None and cancel_check():
                        stopped = True
                        break
                    chunk = self._reader.read(chunk_size)
                    if not chunk:
                        break
                    output.write(chunk)
                    total += len(chunk)
                    if progress_cb is not None:
                        progress_cb(total)
        finally:
            self.close_reader()
        return StreamlinkCaptureResult(destination, total, stopped)

    def close_reader(self):
        reader, self._reader = self._reader, None
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass  # safe: best-effort fallback; preserve the primary operation

    def close(self):
        self.close_reader()
        http = getattr(self.session, "http", None)
        if http is not None:
            try:
                http.close()
            except Exception:
                pass  # safe: best-effort fallback; preserve the primary operation
        if self.proxy is not None:
            self.proxy.stop()
            self.proxy = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()


class StreamlinkEngine:
    """Create guarded in-process Streamlink captures."""

    def _load(self):
        version = streamlink_version()
        if not version or _version_tuple(version) < MIN_STREAMLINK_VERSION:
            raise StreamlinkUnavailable(streamlink_install_hint())
        try:
            return importlib.import_module("streamlink")
        except ImportError as error:
            raise StreamlinkUnavailable(streamlink_install_hint()) from error

    @staticmethod
    def _guard_http_session(session):
        """Pin every Streamlink HTTP request to StreamKeep's URL policy."""
        http = getattr(session, "http", None)
        if http is None or not hasattr(http, "request"):
            raise StreamlinkSecurityError(
                "Streamlink did not expose a guardable HTTP session"
            )
        try:
            http.trust_env = False
        except Exception:
            pass  # safe: best-effort fallback; preserve the primary operation
        original_request = http.request

        def guarded_request(method, url, *args, **kwargs):
            target = _validated_source(url)
            return original_request(method, target, *args, **kwargs)

        http.request = guarded_request

    @staticmethod
    def _resolve_streams(session, source, platform, low_latency):
        platform_name = str(platform or "").strip().casefold()
        plugin_options = {"low-latency": True} if low_latency else {}
        try:
            resolved = session.resolve_url(source)
        except Exception:
            resolved = None
        if isinstance(resolved, tuple) and len(resolved) == 3:
            _plugin_name, plugin_class, resolved_url = resolved
            plugin = plugin_class(
                session, resolved_url, options=plugin_options,
            )
            return plugin.streams()
        # A direct HLS URL has no site plugin.  Streamlink's protocol plugin
        # accepts the explicit hls:// form; the original URL was validated
        # before this function and nested requests are guarded as well.
        if platform_name not in SUPPORTED_PLATFORMS:
            source = f"hls://{source}"
        return session.streams(source)

    def open(
        self,
        source_url,
        *,
        platform="",
        options: StreamlinkOptions | None = None,
        request_headers=None,
    ) -> StreamlinkCapture:
        """Resolve and select a guarded Streamlink stream."""
        source = _validated_source(source_url)
        normalized = (options or StreamlinkOptions()).normalized()
        module = self._load()
        proxy = GuardedHTTPProxy()
        try:
            proxy_url = proxy.start()
            session = module.Streamlink(build_session_options(
                normalized,
                proxy_url=proxy_url,
                request_headers=request_headers,
            ))
            self._guard_http_session(session)
            streams = self._resolve_streams(
                session, source, platform, normalized.low_latency,
            )
            selected = select_stream(streams, normalized.quality)
            return StreamlinkCapture(selected, session, proxy)
        except Exception:
            proxy.stop()
            raise


def remux_capture(raw_path, output_path, *, ffmpeg="ffmpeg") -> bool:
    """Remux a Streamlink byte capture, preserving the raw file on failure."""
    raw = str(raw_path)
    target = str(output_path)
    if not os.path.isfile(raw) or os.path.getsize(raw) <= 0:
        return False
    command = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", raw, "-c", "copy", "-y", target,
    ]
    try:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False, creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, ValueError):
        return False
    if result.returncode != 0 or not os.path.isfile(target):
        return False
    try:
        if os.path.getsize(target) <= 0:
            return False
        os.unlink(raw)
    except OSError:
        pass
    return True


def streamlink_live_status(url, *, platform=""):
    """Probe whether Streamlink sees a live stream, or return ``None``.

    This is used only as a monitor fallback when the native platform API has
    no answer.  It never launches Streamlink's browser helper.
    """
    if platform and str(platform).strip().casefold() not in SUPPORTED_PLATFORMS:
        return None
    try:
        with StreamlinkEngine().open(
            url,
            platform=platform,
            options=StreamlinkOptions(low_latency=False),
        ):
            return True
    except StreamlinkNoStream:
        return False
    except (StreamlinkUnavailable, RemoteURLPolicyError, OSError, ValueError):
        return None
    except Exception:
        return None
