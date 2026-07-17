"""Bounded, SSRF-safe remote image acquisition and hardened decoding.

Every remote image StreamKeep pulls in — metadata thumbnails, third-party chat
emotes, smart-thumbnail inputs — flows through this one policy instead of an
ad-hoc ``urlopen``/``curl``/``Image.open`` call. The policy enforces:

* HTTP(S)-only requests and redirects, each hop re-validated (no ``file://``,
  no credential-bearing authority);
* SSRF protection — the resolved address must be globally routable and is
  pinned for the connection, so a hostname cannot rebind to a private/metadata
  target mid-request;
* strict byte and time limits with partial-file cleanup;
* content-magic sniffing against an allowlist of raster formats (the declared
  ``Content-Type`` is not trusted);
* Pillow decompression-bomb warnings treated as errors plus dimension, frame,
  and allocation caps on decode.

Callers get validated bytes or a loaded ``PIL.Image`` — never a half-written
file or an unbounded decode.
"""

import http.client
import io
import socket
import ssl
import urllib.parse
import warnings
from pathlib import Path

from . import CURL_UA
from .scrape import _address_allowed, _resolve_headless_addresses

DEFAULT_ALLOWED_FORMATS = ("png", "jpeg", "gif", "webp", "bmp")
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 15
MAX_REDIRECTS = 5
DEFAULT_MAX_PIXELS = 40_000_000
DEFAULT_MAX_FRAMES = 300

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class ImageFetchError(Exception):
    """A remote image could not be acquired or decoded within policy."""


def detect_image_format(data):
    """Return an allowlisted format name from *data*'s magic bytes, or ``None``.

    The wire ``Content-Type`` is deliberately ignored — only the leading bytes
    decide the format.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < 12:
        return None
    head = bytes(data[:16])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:2] == b"BM":
        return "bmp"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


class _PinnedHTTPSConnection(http.client.HTTPConnection):
    """HTTPS connection to one validated IP while verifying the URL host."""

    default_port = 443

    def __init__(self, host, port, address, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self._address = str(address)
        self._context = ssl.create_default_context()

    def connect(self):
        self.sock = socket.create_connection(
            (self._address, self.port), self.timeout, self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Plain HTTP connection pinned to one validated IP."""

    def __init__(self, host, port, address, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self._address = str(address)

    def connect(self):
        self.sock = socket.create_connection(
            (self._address, self.port), self.timeout, self.source_address,
        )


def _validate_target(url):
    """Return ``(scheme, host, port, address, path)`` for a safe HTTP(S) URL."""
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ImageFetchError("only http and https image URLs are allowed")
    if parsed.username or parsed.password:
        raise ImageFetchError("credential-bearing image URLs are not allowed")
    host = parsed.hostname
    if not host:
        raise ImageFetchError("image URL has no host")
    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        addresses = _resolve_headless_addresses(host, port)
    except (OSError, ValueError) as e:
        raise ImageFetchError(f"could not resolve image host: {e}") from e
    if not addresses or not all(_address_allowed(a) for a in addresses):
        raise ImageFetchError("image host resolves to a disallowed address")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return scheme, host, port, addresses[0], path


def _read_capped(resp, max_bytes):
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImageFetchError("image exceeds the maximum allowed size")
    return data


def fetch_url_bytes(
    url,
    *,
    max_bytes=DEFAULT_MAX_BYTES,
    timeout=DEFAULT_TIMEOUT,
    accept="*/*",
):
    """Fetch *url* into bounded bytes through the shared SSRF-safe policy.

    HTTP(S)-only, each redirect hop re-validated and address-pinned, with a
    hard byte cap. This is the generic transport that :func:`fetch_image_bytes`
    and other remote pulls (e.g. podcast transcript/chapter sidecars) share so
    there is one connection policy. Raises :class:`ImageFetchError`.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, port, address, path = _validate_target(current)
        conn_cls = (
            _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
        )
        conn = conn_cls(host, port, address, timeout)
        try:
            conn.request(
                "GET", path,
                headers={"User-Agent": CURL_UA, "Accept": accept},
            )
            resp = conn.getresponse()
            if resp.status in _REDIRECT_STATUSES:
                location = resp.getheader("Location")
                resp.read()
                if not location:
                    raise ImageFetchError("redirect response had no Location")
                current = urllib.parse.urljoin(current, location)
                continue
            if resp.status != 200:
                raise ImageFetchError(f"unexpected HTTP status {resp.status}")
            return _read_capped(resp, max_bytes)
        except (OSError, http.client.HTTPException) as e:
            raise ImageFetchError(f"request failed: {e}") from e
        finally:
            conn.close()
    raise ImageFetchError("too many redirects")


def fetch_image_bytes(
    url,
    *,
    max_bytes=DEFAULT_MAX_BYTES,
    timeout=DEFAULT_TIMEOUT,
    allowed_formats=DEFAULT_ALLOWED_FORMATS,
):
    """Fetch *url* into validated image bytes.

    Follows HTTP(S) redirects (re-validating each hop) up to ``MAX_REDIRECTS``,
    enforces the byte limit, and rejects any payload whose magic bytes are not
    in *allowed_formats*. Returns ``(data, format_name)``.
    """
    data = fetch_url_bytes(
        url, max_bytes=max_bytes, timeout=timeout, accept="image/*"
    )
    fmt = detect_image_format(data)
    if fmt is None or fmt not in allowed_formats:
        raise ImageFetchError("payload is not an allowed image format")
    return data, fmt


def decode_image(
    source,
    *,
    max_pixels=DEFAULT_MAX_PIXELS,
    max_frames=DEFAULT_MAX_FRAMES,
    allowed_formats=DEFAULT_ALLOWED_FORMATS,
):
    """Decode *source* (bytes or a filesystem path) under strict Pillow limits.

    Enforces the format allowlist, treats decompression-bomb warnings as
    errors, and caps pixel count and animation frames. Returns a loaded
    ``PIL.Image.Image``.
    """
    from PIL import Image

    if isinstance(source, (bytes, bytearray)):
        head = bytes(source[:16])
        stream = io.BytesIO(bytes(source))
    else:
        try:
            with open(source, "rb") as handle:
                head = handle.read(16)
        except OSError as e:
            raise ImageFetchError(f"could not read image file: {e}") from e
        stream = str(source)

    fmt = detect_image_format(head)
    if fmt is None or fmt not in allowed_formats:
        raise ImageFetchError("unsupported or unrecognized image format")

    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(stream)
            width, height = image.size
            if width <= 0 or height <= 0:
                raise ImageFetchError("image has invalid dimensions")
            if width * height > max_pixels:
                raise ImageFetchError("image dimensions exceed the pixel limit")
            frames = getattr(image, "n_frames", 1)
            if frames > max_frames:
                raise ImageFetchError("animated image exceeds the frame limit")
            image.load()
            return image
    except Image.DecompressionBombWarning as e:
        raise ImageFetchError("image tripped the decompression-bomb guard") from e
    except Image.DecompressionBombError as e:
        raise ImageFetchError("image exceeds the decompression-bomb limit") from e
    except (OSError, ValueError) as e:
        raise ImageFetchError(f"image could not be decoded: {e}") from e
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def download_image(
    url,
    dest,
    *,
    max_bytes=DEFAULT_MAX_BYTES,
    timeout=DEFAULT_TIMEOUT,
    allowed_formats=DEFAULT_ALLOWED_FORMATS,
    max_pixels=DEFAULT_MAX_PIXELS,
    max_frames=DEFAULT_MAX_FRAMES,
    verify_decodable=True,
):
    """Fetch, validate, and atomically write a remote image to *dest*.

    Returns ``True`` on success. On any policy violation or error nothing is
    left at *dest*: the write goes to a sibling temp file that is only renamed
    into place after validation succeeds.
    """
    dest_path = Path(dest)
    try:
        data, _fmt = fetch_image_bytes(
            url, max_bytes=max_bytes, timeout=timeout,
            allowed_formats=allowed_formats,
        )
        if verify_decodable:
            image = decode_image(
                data, max_pixels=max_pixels, max_frames=max_frames,
                allowed_formats=allowed_formats,
            )
            image.close()
    except ImageFetchError:
        return False

    tmp_path = dest_path.with_name(dest_path.name + ".img-tmp")
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "wb") as handle:
            handle.write(data)
        tmp_path.replace(dest_path)
        return True
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
