"""Tiny local HTTP server for the browser-companion extension.

Binds strictly to 127.0.0.1 on a random port. Every request requires a
bearer token — 32-byte hex, regenerated every app launch, displayed in
the Settings tab so the user can paste it into the extension popup.

Design constraints:
  - 127.0.0.1 only, never 0.0.0.0
  - Per-launch token, never stored on disk
  - Constant-time token compare
  - Only accepts POST /send_url {url, action}
  - CORS: Access-Control-Allow-Origin: * is fine because the token gates
    actual execution — an attacker page can only "send URLs" if they've
    already exfiltrated the token, which requires StreamKeep to have
    shown it.

The server runs on its own thread (stdlib http.server is threaded), and
hands received URLs to the main-thread Qt via a pyqtSignal.
"""

import hmac
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from PyQt6.QtCore import QObject, pyqtSignal


class _ServerSignals(QObject):
    url_received = pyqtSignal(str, str)   # url, action ("fetch" | "queue")


class LocalCompanionServer:
    """Wraps a ThreadingHTTPServer bound to 127.0.0.1 on a random port.

    Usage:
        server = LocalCompanionServer()
        server.url_received.connect(main_window._on_companion_url)
        server.start()
        ...
        server.stop()

    Token / port are accessible via `server.token` / `server.port`.
    """

    def __init__(self):
        self.token = secrets.token_hex(16)
        self.port = 0
        self._httpd = None
        self._thread = None
        self._signals = _ServerSignals()
        self.url_received = self._signals.url_received

    def start(self):
        handler_cls = _build_handler(self.token, self._signals)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self._httpd.server_address[1]
        self._thread = Thread(
            target=self._httpd.serve_forever,
            name="streamkeep-local-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except OSError:
                pass
        self._httpd = None
        if self._thread is not None:
            try:
                self._thread.join(timeout=2.0)
            except RuntimeError:
                pass
        self._thread = None


def _build_handler(expected_token, signals):
    class _Handler(BaseHTTPRequestHandler):
        # Silence stdlib access-log noise — we log via the Qt log panel.
        def log_message(self, *_args, **_kwargs):
            return

        def _auth_ok(self):
            hdr = self.headers.get("Authorization", "") or ""
            if not hdr.startswith("Bearer "):
                return False
            return hmac.compare_digest(hdr[7:].strip(), expected_token)

        def _cors(self):
            # Token-gated; CORS wildcard is fine.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type, Authorization"
            )

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_POST(self):
            if self.path != "/send_url":
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            if not self._auth_ok():
                self.send_response(401)
                self._cors()
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                data = json.loads(raw) if raw else {}
            except (ValueError, OSError):
                data = {}
            url = str(data.get("url") or "").strip()
            action = str(data.get("action") or "fetch").strip().lower()
            if action not in ("fetch", "queue"):
                action = "fetch"
            if not url.startswith(("http://", "https://")):
                self.send_response(400)
                self._cors()
                self.end_headers()
                self.wfile.write(b'{"ok":false,"err":"invalid url"}')
                return
            signals.url_received.emit(url, action)
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def do_GET(self):
            # /ping returns a tiny JSON ack so the extension can verify
            # pairing before issuing a real send_url.
            if self.path != "/ping":
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            if not self._auth_ok():
                self.send_response(401)
                self._cors()
                self.end_headers()
                return
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"app":"StreamKeep"}')

    return _Handler
