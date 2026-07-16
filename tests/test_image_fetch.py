import io
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

from streamkeep import image_fetch
from streamkeep.image_fetch import (
    ImageFetchError,
    decode_image,
    detect_image_format,
    download_image,
    fetch_image_bytes,
)


def _png_bytes(size=(8, 8), color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _gif_bytes(frames=5, size=(8, 8)):
    buf = io.BytesIO()
    # Distinct frame content so Pillow keeps every frame (identical frames get
    # collapsed), giving a deterministic n_frames for the cap test.
    images = [Image.new("RGB", size, (i * 40, 0, 0)) for i in range(frames)]
    images[0].save(
        buf, format="GIF", save_all=True, append_images=images[1:],
        duration=10, optimize=False, disposal=2,
    )
    return buf.getvalue()


# ── Format detection ────────────────────────────────────────────────

def test_detect_image_format_by_magic():
    assert detect_image_format(_png_bytes()) == "png"
    assert detect_image_format(_gif_bytes()) == "gif"
    assert detect_image_format(b"not an image at all, really") is None
    assert detect_image_format(b"short") is None


# ── URL validation / SSRF ───────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x.png",
    "http://user:pass@example.com/x.png",
    "https:///nohost",
])
def test_fetch_rejects_unsafe_urls(url):
    with pytest.raises(ImageFetchError):
        fetch_image_bytes(url)


def test_fetch_rejects_loopback_and_private_targets():
    # Loopback is not globally routable — SSRF guard must reject it.
    with pytest.raises(ImageFetchError):
        fetch_image_bytes("http://127.0.0.1/emote.png")


# ── Decode hardening ────────────────────────────────────────────────

def test_decode_rejects_spoofed_and_unallowed_formats(tmp_path):
    fake = tmp_path / "evil.png"
    fake.write_bytes(b"this is text pretending to be a png" * 4)
    with pytest.raises(ImageFetchError):
        decode_image(str(fake))
    # A real GIF rejected when not in the allowlist.
    with pytest.raises(ImageFetchError):
        decode_image(_gif_bytes(), allowed_formats=("png",))


def test_decode_enforces_pixel_and_frame_caps():
    with pytest.raises(ImageFetchError):
        decode_image(_png_bytes((100, 100)), max_pixels=10)
    with pytest.raises(ImageFetchError):
        decode_image(_gif_bytes(frames=5), max_frames=2)


def test_decode_accepts_valid_image():
    image = decode_image(_png_bytes((16, 16)))
    try:
        assert image.size == (16, 16)
    finally:
        image.close()


# ── download_image atomicity ────────────────────────────────────────

def test_download_leaves_no_partial_file_on_rejection(tmp_path, monkeypatch):
    dest = tmp_path / "thumb.jpg"

    def fake_fetch(url, **kwargs):
        raise ImageFetchError("blocked")

    monkeypatch.setattr(image_fetch, "fetch_image_bytes", fake_fetch)
    assert download_image("https://example.com/x.png", str(dest)) is False
    assert not dest.exists()
    assert not (tmp_path / "thumb.jpg.img-tmp").exists()


# ── End-to-end fetch against a localhost server ─────────────────────

class _ImgHandler(BaseHTTPRequestHandler):
    payload = b""
    redirect_to = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.end_headers()
            return
        if self.path == "/oversize":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"P" * (200 * 1024))
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.end_headers()
        self.wfile.write(self.payload)


def _run_server():
    server = HTTPServer(("127.0.0.1", 0), _ImgHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_fetch_and_redirect_against_local_server(monkeypatch):
    # Allow the loopback test server past the SSRF guard for this test only.
    monkeypatch.setattr(image_fetch, "_address_allowed", lambda addr: True)
    _ImgHandler.payload = _png_bytes((10, 10))
    server = _run_server()
    port = server.server_address[1]
    _ImgHandler.redirect_to = f"http://127.0.0.1:{port}/image.png"
    try:
        data, fmt = fetch_image_bytes(f"http://127.0.0.1:{port}/image.png")
        assert fmt == "png"
        assert data == _ImgHandler.payload

        # Redirects are followed and re-validated.
        data2, fmt2 = fetch_image_bytes(f"http://127.0.0.1:{port}/redirect")
        assert fmt2 == "png" and data2 == _ImgHandler.payload

        # Oversized bodies are refused.
        with pytest.raises(ImageFetchError):
            fetch_image_bytes(
                f"http://127.0.0.1:{port}/oversize", max_bytes=1024,
            )

        # A non-image body is refused even with a 200 + image content-type.
        _ImgHandler.payload = b"totally not an image payload here"
        with pytest.raises(ImageFetchError):
            fetch_image_bytes(f"http://127.0.0.1:{port}/image.png")
    finally:
        server.shutdown()
