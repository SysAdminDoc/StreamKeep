"""Local HTTP server — browser-companion extension + REST API + Web Remote UI.

The application listener always binds to loopback. Optional LAN access is
terminated by an explicitly configured local HTTPS reverse proxy. Clients
exchange a short-lived, one-use pairing code for an origin-bound bearer token;
the persistent master token is never displayed or exposed to clients.

Tokens carry scopes: ``status`` (read-only state), ``queue`` (send URLs),
``recovery`` (retry/discard failed jobs). The secure master token has all
scopes. ``rotate_token()`` replaces it atomically;
``create_scoped_token()`` mints a restricted token.

REST API endpoints (F37):
  GET  /api/status    — active downloads, queue, live channels  [status]
  GET  /api/jobs/{id} — inspect one durable queue job            [status]
  POST /api/queue     — add a URL to the download queue         [queue]
  POST /api/jobs/cancel — durably cancel a queue job             [queue]
  GET  /api/library   — search/list recorded VODs               [status]
  GET  /api/monitor   — channel monitor statuses                [status]
  POST /api/failures/retry    — retry a persisted failed job    [recovery]
  POST /api/failures/discard  — discard a persisted failed job  [recovery]
  GET  /               — serves the single-page web remote UI

The server runs on its own thread (stdlib http.server is threaded), and
hands received URLs to the main-thread Qt via a pyqtSignal.
"""

import hashlib
import ipaddress
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

from PyQt6.QtCore import QObject, pyqtSignal

SCOPE_STATUS = "status"
SCOPE_QUEUE = "queue"
SCOPE_RECOVERY = "recovery"
ALL_SCOPES = frozenset({SCOPE_STATUS, SCOPE_QUEUE, SCOPE_RECOVERY})
PAIRED_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")


def generate_bearer_token():
    """Generate a 256-bit URL-safe bearer token."""
    return secrets.token_urlsafe(32)


def valid_bearer_token(token):
    token = str(token or "")
    # Legacy 128-bit hex tokens remain valid for one-way migration.
    return bool(_TOKEN_RE.fullmatch(token))


@dataclass(frozen=True)
class TokenGrant:
    scopes: frozenset
    origin: str = ""
    expires_at: float = 0.0


class TokenStore:
    """Thread-safe token → scopes mapping."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens = {}  # token_str -> frozenset of scopes

    def add(self, token, scopes, *, origin="", expires_at=0.0):
        if not valid_bearer_token(token):
            raise ValueError("Bearer tokens must contain at least 128 bits.")
        with self._lock:
            self._tokens[token] = TokenGrant(
                frozenset(scopes), str(origin or ""), float(expires_at or 0.0)
            )

    def remove(self, token):
        with self._lock:
            self._tokens.pop(token, None)

    def revoke_all(self):
        with self._lock:
            self._tokens.clear()

    def check(self, token):
        candidate = str(token or "")
        if not candidate:
            return None
        with self._lock:
            rows = tuple(self._tokens.items())
        matched = None
        # Do not expose token-prefix timing through the loopback/LAN boundary.
        for stored, grant in rows:
            if secrets.compare_digest(stored, candidate):
                matched = grant
        if matched and matched.expires_at and matched.expires_at <= time.time():
            self.remove(candidate)
            return None
        return matched

    def __len__(self):
        with self._lock:
            return len(self._tokens)


class PairingStore:
    """One-time, short-lived pairing code registry."""

    def __init__(self):
        self._lock = threading.Lock()
        self._digest = b""
        self._expires_at = 0.0
        self._scopes = frozenset()
        self._attempts = 0

    def issue(self, scopes, ttl_seconds=300):
        code = secrets.token_urlsafe(18)
        with self._lock:
            self._digest = hashlib.sha256(code.encode("ascii")).digest()
            self._expires_at = time.time() + max(30, min(600, int(ttl_seconds)))
            self._scopes = frozenset(scopes) & ALL_SCOPES
            self._attempts = 0
        return code

    def consume(self, code):
        digest = hashlib.sha256(str(code or "").encode("utf-8")).digest()
        with self._lock:
            valid = bool(
                self._digest
                and self._expires_at > time.time()
                and self._attempts < 5
                and secrets.compare_digest(self._digest, digest)
            )
            if valid:
                scopes = self._scopes
                self._digest = b""
                self._expires_at = 0.0
                self._scopes = frozenset()
                return scopes
            self._attempts += 1
            if self._attempts >= 5 or self._expires_at <= time.time():
                self._digest = b""
                self._scopes = frozenset()
            return None


class ReplayStore:
    """Bounded per-token nonce cache for mutating requests."""

    def __init__(self, *, max_age_seconds=120, max_entries=20_000):
        self._lock = threading.Lock()
        self._seen = {}
        self._max_age = max(30, min(300, int(max_age_seconds)))
        self._max_entries = max(100, min(100_000, int(max_entries)))

    def validate_freshness(self, timestamp, nonce):
        try:
            request_time = int(str(timestamp or ""))
        except (TypeError, ValueError):
            return False, "request_timestamp_invalid"
        now = int(time.time())
        if abs(now - request_time) > self._max_age:
            return False, "request_timestamp_expired"
        nonce = str(nonce or "")
        if not _NONCE_RE.fullmatch(nonce):
            return False, "request_nonce_invalid"
        return True, ""

    def accept(self, token, timestamp, nonce):
        fresh, error = self.validate_freshness(timestamp, nonce)
        if not fresh:
            return False, error
        now = int(time.time())
        nonce = str(nonce or "")
        key = hashlib.sha256(
            str(token).encode("utf-8") + b"\0" + nonce.encode("ascii")
        ).digest()
        with self._lock:
            cutoff = now - self._max_age
            self._seen = {
                seen_key: seen_at for seen_key, seen_at in self._seen.items()
                if seen_at >= cutoff
            }
            if key in self._seen:
                return False, "request_replayed"
            if len(self._seen) >= self._max_entries:
                return False, "request_replay_window_full"
            self._seen[key] = now
        return True, ""


class _ServerSignals(QObject):
    url_received = pyqtSignal(str, str)   # url, action ("fetch" | "queue")
    clip_received = pyqtSignal(str, float, float)  # url, start_secs, end_secs
    failed_job_retry_requested = pyqtSignal(int)
    failed_job_discard_requested = pyqtSignal(int)


class _CompanionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


_LOCAL_HOSTS = frozenset(("", "127.0.0.1", "::1", "localhost"))


def _canonical_host(host):
    host = str(host or "").strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        return ""
    host = host.split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        try:
            return host.encode("idna").decode("ascii")
        except UnicodeError:
            return ""


def _normalize_host_header(value):
    raw = str(value or "")
    if not raw or raw != raw.strip() or re.search(r"[\s/@?#,\\]", raw):
        return ""
    if raw.startswith("["):
        match = re.fullmatch(r"\[([^]]+)](?::([0-9]{1,5}))?", raw)
        if not match:
            return ""
        host, port_text = match.groups()
        try:
            if ipaddress.ip_address(host.split("%", 1)[0]).version != 6:
                return ""
        except ValueError:
            return ""
    else:
        if raw.count(":") > 1:
            return ""
        host, separator, port_text = raw.partition(":")
        if not separator:
            port_text = ""
    if port_text:
        if not re.fullmatch(r"[0-9]{1,5}", port_text):
            return ""
        if not (1 <= int(port_text) <= 65_535):
            return ""
    host = _canonical_host(host)
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        labels = host.rstrip(".").split(".")
        if len(host) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ):
            return ""
        return host.rstrip(".")


def _build_allowed_hosts(extra_hosts=None):
    hosts = {
        normalized for host in _LOCAL_HOSTS
        if (normalized := _canonical_host(host))
    }
    for host in extra_hosts or ():
        norm = _normalize_host_header(host)
        if norm:
            hosts.add(norm)
    return frozenset(hosts)


def _format_url_host(host):
    host = _canonical_host(host) or "127.0.0.1"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _normalize_origin(value, *, allow_extensions=True):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    if parsed.path not in ("", "/"):
        return ""
    scheme = parsed.scheme.lower()
    host = _canonical_host(parsed.hostname)
    if allow_extensions and scheme in ("chrome-extension", "moz-extension"):
        if not host or port is not None:
            return ""
        if scheme == "chrome-extension" and not re.fullmatch(r"[a-p]{32}", host):
            return ""
        return f"{scheme}://{host}"
    if scheme not in ("http", "https") or not host:
        return ""
    authority = _format_url_host(host)
    if port is not None:
        authority = f"{authority}:{port}"
    return f"{scheme}://{authority}"


def _validate_external_origin(value):
    origin = _normalize_origin(value, allow_extensions=False)
    if not origin or not origin.startswith("https://"):
        raise ValueError("LAN remote access requires an explicit HTTPS reverse-proxy origin.")
    return origin


class LocalCompanionServer:
    """Wrap a loopback-only ``ThreadingHTTPServer`` on a random port.

    Usage:
        server = LocalCompanionServer()
        server.state_provider = lambda: {...}  # F37 API state callback
        server.url_received.connect(main_window._on_companion_url)
        server.start()

    Token / port are accessible via `server.token` / `server.port`.
    """

    def __init__(
        self,
        *,
        bind_lan=False,
        allowed_hosts=None,
        port=0,
        master_token=None,
        external_origin="",
    ):
        self._token_store = TokenStore()
        self._pairing_store = PairingStore()
        self._replay_store = ReplayStore()
        self.token = str(master_token or generate_bearer_token())
        if not valid_bearer_token(self.token):
            raise ValueError("Stored companion token is invalid or too short.")
        self._token_store.add(self.token, ALL_SCOPES)
        self.port = int(port or 0)
        self._httpd = None
        self._thread = None
        self._signals = _ServerSignals()
        self.url_received = self._signals.url_received
        self.clip_received = self._signals.clip_received
        self.failed_job_retry_requested = self._signals.failed_job_retry_requested
        self.failed_job_discard_requested = self._signals.failed_job_discard_requested
        self.state_provider = None   # callable -> dict (F37)
        self.queue_submitter = None  # callable(dict) -> durable job dict
        self.job_canceller = None    # callable(job_id) -> durable job dict
        self.failure_retrier = None  # callable(failure_id) -> durable job dict
        self.failure_discarder = None  # callable(failure_id) -> bool
        self._bind_lan = bool(bind_lan)
        self.external_origin = (
            _validate_external_origin(external_origin) if self._bind_lan else ""
        )
        # Even LAN access stays loopback-only. A locally managed reverse proxy
        # owns TLS and forwards to this listener.
        self._bind_addr = "127.0.0.1"
        extra_hosts = set(allowed_hosts or ())
        if self.external_origin:
            extra_hosts.add(urlsplit(self.external_origin).hostname)
        self._allowed_hosts = _build_allowed_hosts(extra_hosts)
        self.allowed_hosts = tuple(sorted(host for host in self._allowed_hosts if host))
        self.display_host = (
            _canonical_host(urlsplit(self.external_origin).hostname)
            if self.external_origin else "127.0.0.1"
        )

    def rotate_token(self):
        """Replace master access and revoke every paired client immediately."""
        self._token_store.revoke_all()
        self.token = generate_bearer_token()
        self._token_store.add(self.token, ALL_SCOPES)
        return self.token

    def create_scoped_token(self, scopes, *, origin="", expires_at=0.0):
        """Mint a token restricted to the given scopes."""
        valid = frozenset(scopes) & ALL_SCOPES
        if not valid:
            raise ValueError(f"No valid scopes in {scopes!r}")
        tok = generate_bearer_token()
        self._token_store.add(
            tok, valid, origin=str(origin or ""), expires_at=expires_at
        )
        return tok

    def create_pairing_code(self, scopes=ALL_SCOPES, *, ttl_seconds=300):
        """Create a one-use code that never appears in a URL or request log."""
        return self._pairing_store.issue(scopes, ttl_seconds=ttl_seconds)

    def revoke_token(self, token):
        """Revoke a specific token (scoped or master)."""
        self._token_store.remove(token)

    def start(self):
        if self._httpd is not None:
            self.stop()
        handler_cls = _build_handler(
            self._token_store,
            self._signals,
            self.state_provider,
            queue_submitter=self.queue_submitter,
            job_canceller=self.job_canceller,
            failure_retrier=self.failure_retrier,
            failure_discarder=self.failure_discarder,
            allowed_hosts=self._allowed_hosts,
            pairing_store=self._pairing_store,
            replay_store=self._replay_store,
            external_origin=self.external_origin,
        )
        self._httpd = _CompanionHTTPServer((self._bind_addr, self.port), handler_cls)
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
        self.port = 0

    @property
    def url(self):
        if int(self.port or 0) <= 0:
            return ""
        if self.external_origin:
            return f"{self.external_origin}/"
        return f"http://{_format_url_host(self.display_host)}:{self.port}/"


def _parse_timestamp(value):
    """Parse a timestamp value (seconds float, or HH:MM:SS string). Returns float or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except (ValueError, IndexError):
        pass
    return None


def _build_handler(
    token_store,
    signals,
    state_provider=None,
    *,
    queue_submitter=None,
    job_canceller=None,
    failure_retrier=None,
    failure_discarder=None,
    allowed_hosts=None,
    pairing_store=None,
    replay_store=None,
    external_origin="",
):
    allowed_hosts = frozenset(allowed_hosts or _build_allowed_hosts())
    pairing_store = pairing_store or PairingStore()
    replay_store = replay_store or ReplayStore()
    external_origin = str(external_origin or "")
    external_host = (
        _canonical_host(urlsplit(external_origin).hostname)
        if external_origin else ""
    )
    external_authority = (
        urlsplit(external_origin).netloc.lower() if external_origin else ""
    )

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args, **_kwargs):
            return

        def _host_ok(self):
            host_values = self.headers.get_all("Host", failobj=[])
            return (
                len(host_values) == 1
                and _normalize_host_header(host_values[0]) in allowed_hosts
            )

        def _origin_ok(self, origin):
            normalized = _normalize_origin(origin)
            if not normalized:
                return False
            if normalized.startswith(("chrome-extension://", "moz-extension://")):
                return True
            if external_origin and secrets.compare_digest(normalized, external_origin):
                return True
            parsed = urlsplit(normalized)
            local_port = int(self.server.server_address[1])
            return (
                parsed.scheme == "http"
                and _canonical_host(parsed.hostname) in _LOCAL_HOSTS
                and parsed.port == local_port
            )

        def _external_boundary_ok(self):
            forwarded_proto = str(self.headers.get("X-Forwarded-Proto", "") or "").lower()
            forwarded_host = str(self.headers.get("X-Forwarded-Host", "") or "").lower()
            origin = _normalize_origin(self.headers.get("Origin", ""))
            host = _normalize_host_header(self.headers.get("Host", ""))
            uses_external = bool(
                external_origin
                and (
                    origin == external_origin
                    or host == external_host
                    or forwarded_proto
                    or forwarded_host
                )
            )
            if not uses_external:
                return not (forwarded_proto or forwarded_host)
            peer = _canonical_host(self.client_address[0] if self.client_address else "")
            return bool(
                peer in ("127.0.0.1", "::1")
                and forwarded_proto == "https"
                and secrets.compare_digest(forwarded_host, external_authority)
            )

        def _reject_bad_host(self):
            if not self._host_ok():
                self._json_response(403, {
                    "ok": False,
                    "err": "host_denied",
                    "message": "Request Host is not configured for this listener.",
                })
                return True
            if not self._external_boundary_ok():
                self._json_response(403, {
                    "ok": False,
                    "err": "transport_denied",
                    "message": "LAN control requires the configured HTTPS reverse proxy.",
                })
                return True
            return False

        def _token_grant(self):
            """Return ``(grant, token)`` for a valid origin-bound bearer."""
            hdr = self.headers.get("Authorization", "") or ""
            scheme, separator, candidate = hdr.partition(" ")
            if not separator or scheme.lower() != "bearer":
                return None
            candidate = candidate.strip()
            if not candidate:
                return None
            grant = token_store.check(candidate)
            if grant is None:
                return None
            if grant.origin:
                origin = _normalize_origin(self.headers.get("Origin", ""))
                if not origin or not secrets.compare_digest(origin, grant.origin):
                    return None
            return grant, candidate

        def _require_auth(self, scope=None, *, mutating=False):
            """Check auth + optional scope. Returns True if authorized."""
            auth = self._token_grant()
            if auth is None:
                self._json_response(401, {
                    "ok": False,
                    "err": "token_invalid",
                    "message": "Missing, expired, or origin-mismatched token. Re-pair from Settings.",
                })
                return False
            grant, token = auth
            if scope and scope not in grant.scopes:
                self._json_response(403, {
                    "ok": False,
                    "err": "scope_denied",
                    "message": f"This token does not have the '{scope}' scope.",
                })
                return False
            if mutating and not self._require_mutation_proof(token):
                return False
            return True

        def _require_mutation_proof(self, token):
            origin = self.headers.get("Origin", "")
            if origin and not self._origin_ok(origin):
                self._json_response(403, {
                    "ok": False,
                    "err": "origin_denied",
                    "message": "Mutating requests require an approved origin.",
                })
                return False
            fetch_site = str(self.headers.get("Sec-Fetch-Site", "") or "").lower()
            if fetch_site == "cross-site":
                self._json_response(403, {
                    "ok": False,
                    "err": "cross_site_denied",
                    "message": "Cross-site mutation was rejected.",
                })
                return False
            content_type = str(self.headers.get("Content-Type", "") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self._json_response(415, {
                    "ok": False,
                    "err": "content_type_denied",
                    "message": "Mutating requests require application/json.",
                })
                return False
            accepted, error = replay_store.accept(
                token,
                self.headers.get("X-StreamKeep-Timestamp", ""),
                self.headers.get("X-StreamKeep-Nonce", ""),
            )
            if not accepted:
                code = 409 if error == "request_replayed" else 400
                self._json_response(code, {
                    "ok": False,
                    "err": error,
                    "message": "Request freshness proof was missing, stale, or already used.",
                })
                return False
            return True

        def _cors(self):
            origin = self.headers.get("Origin", "")
            if origin and self._origin_ok(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin, X-Forwarded-Proto, X-Forwarded-Host")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Authorization, X-StreamKeep-Timestamp, X-StreamKeep-Nonce",
            )

        def _security_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")

        def _csp_header(self):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'",
            )

        def _json_response(self, code, obj):
            # Early auth/origin/Host rejections happen before endpoint handlers
            # read the JSON body. Draining a bounded body avoids a Windows TCP
            # reset that can otherwise hide the response from the client.
            self._discard_unread_body()
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self._cors()
            self._security_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _discard_unread_body(self, max_bytes=1_048_576):
            if getattr(self, "_request_body_consumed", False):
                return
            self._request_body_consumed = True
            if self.command not in ("POST", "PUT", "PATCH", "DELETE"):
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except (TypeError, ValueError):
                self.close_connection = True
                return
            if length <= 0:
                return
            if length > max_bytes:
                self.close_connection = True
                return
            try:
                self.rfile.read(length)
            except OSError:
                self.close_connection = True

        def _read_body(self, max_bytes=1_048_576):
            self._request_body_consumed = True
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length <= 0 or length > max_bytes:
                    if length > max_bytes:
                        self.close_connection = True
                    return {}
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
            except (ValueError, OSError):
                return {}

        def do_OPTIONS(self):
            if self._reject_bad_host():
                return
            origin = self.headers.get("Origin", "")
            if not origin or not self._origin_ok(origin):
                self._json_response(403, {
                    "ok": False,
                    "err": "origin_denied",
                    "message": "CORS preflight origin is not approved.",
                })
                return
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            if self._reject_bad_host():
                return
            path = self.path.split("?")[0]

            if path == "/":
                self._serve_web_ui()
                return

            if path == "/ping":
                if self._require_auth():
                    self._json_response(200, {"ok": True, "app": "StreamKeep"})
            elif path == "/api/status":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_status()
            elif path == "/api/library":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_library()
            elif path == "/api/monitor":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_monitor()
            elif path.startswith("/api/jobs/"):
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_job(path.removeprefix("/api/jobs/"))
            else:
                self.send_response(404)
                self._cors()
                self.end_headers()

        def do_POST(self):
            if self._reject_bad_host():
                return
            path = self.path.split("?")[0]

            if path == "/pair":
                self._handle_pair()
            elif path == "/send_url":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_send_url()
            elif path == "/api/queue":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_queue()
            elif path == "/api/jobs/cancel":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_job_cancel()
            elif path == "/api/failures/retry":
                if self._require_auth(SCOPE_RECOVERY, mutating=True):
                    self._handle_api_failure_retry()
            elif path == "/api/failures/discard":
                if self._require_auth(SCOPE_RECOVERY, mutating=True):
                    self._handle_api_failure_discard()
            else:
                self._json_response(404, {"ok": False, "err": "not_found"})

        def _handle_pair(self):
            origin_header = self.headers.get("Origin", "")
            if origin_header and not self._origin_ok(origin_header):
                self._json_response(403, {
                    "ok": False,
                    "err": "origin_denied",
                    "message": "Pairing origin is not approved.",
                })
                return
            fetch_site = str(self.headers.get("Sec-Fetch-Site", "") or "").lower()
            if fetch_site == "cross-site":
                self._json_response(403, {
                    "ok": False,
                    "err": "cross_site_denied",
                    "message": "Cross-site pairing was rejected.",
                })
                return
            fresh, error = replay_store.validate_freshness(
                self.headers.get("X-StreamKeep-Timestamp", ""),
                self.headers.get("X-StreamKeep-Nonce", ""),
            )
            if not fresh:
                self._json_response(400, {
                    "ok": False,
                    "err": error,
                    "message": "Pairing requires a fresh timestamp and nonce.",
                })
                return
            content_type = str(self.headers.get("Content-Type", "") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self._json_response(415, {"ok": False, "err": "content_type_denied"})
                return
            data = self._read_body(max_bytes=4096)
            scopes = pairing_store.consume(data.get("code"))
            if not scopes:
                self._json_response(401, {
                    "ok": False,
                    "err": "pairing_invalid",
                    "message": "Pairing code is invalid, expired, used, or locked.",
                })
                return
            requested_scopes = data.get("scopes")
            if isinstance(requested_scopes, list):
                scopes &= frozenset(str(scope) for scope in requested_scopes)
                if not scopes:
                    self._json_response(400, {
                        "ok": False,
                        "err": "pairing_scope_invalid",
                        "message": "The client did not request an approved scope.",
                    })
                    return
            origin = _normalize_origin(origin_header) if origin_header else ""
            token = generate_bearer_token()
            expires_at = time.time() + PAIRED_TOKEN_TTL_SECONDS
            token_store.add(token, scopes, origin=origin, expires_at=expires_at)
            self._json_response(201, {
                "ok": True,
                "token": token,
                "scopes": sorted(scopes),
                "origin": origin,
                "expires_at": int(expires_at),
            })

        def _handle_send_url(self):
            data = self._read_body()
            url = str(data.get("url") or "").strip()
            action = str(data.get("action") or "fetch").strip().lower()
            if action not in ("fetch", "queue"):
                action = "fetch"
            if not url.startswith(("http://", "https://")):
                self._json_response(400, {"ok": False, "err": "invalid url"})
                return
            clip_start = _parse_timestamp(data.get("clip_start"))
            clip_end = _parse_timestamp(data.get("clip_end"))
            if clip_start is not None and clip_end is not None and clip_end <= clip_start:
                self._json_response(400, {
                    "ok": False,
                    "err": "clip_end must be after clip_start",
                })
                return
            if clip_start is not None or clip_end is not None:
                signals.clip_received.emit(
                    url,
                    clip_start if clip_start is not None else 0.0,
                    clip_end if clip_end is not None else 0.0,
                )
            if queue_submitter:
                try:
                    job = queue_submitter({**data, "url": url, "source": "browser"})
                except Exception as error:
                    self._json_response(500, {"ok": False, "err": str(error)})
                    return
                self._json_response(202, {
                    "ok": True,
                    "job_id": str(job.get("job_id", "")),
                    "job": job,
                })
            else:
                signals.url_received.emit(url, action)
                self._json_response(200, {"ok": True})

        # ── REST API handlers (F37) ────────────────────────────────

        def _get_state(self):
            if state_provider:
                try:
                    return state_provider()
                except Exception:
                    pass
            return {}

        def _handle_api_status(self):
            state = self._get_state()
            self._json_response(200, {
                "ok": True,
                "downloads": state.get("downloads", []),
                "queue": state.get("queue", []),
                "failures": state.get("failures", []),
                "live_channels": state.get("live_channels", []),
                "active_workers": state.get("active_workers", []),
                "resumable": state.get("resumable", []),
            })

        def _handle_api_library(self):
            state = self._get_state()
            self._json_response(200, {
                "ok": True,
                "history": state.get("history", []),
            })

        def _handle_api_monitor(self):
            state = self._get_state()
            self._json_response(200, {
                "ok": True,
                "channels": state.get("monitor", []),
            })

        def _handle_api_job(self, job_id):
            job_id = str(job_id or "").strip()
            if not job_id:
                self._json_response(400, {"ok": False, "err": "invalid job id"})
                return
            state = self._get_state()
            job = next(
                (item for item in state.get("queue", [])
                 if str(item.get("job_id", "")) == job_id),
                None,
            )
            if not job:
                self._json_response(404, {"ok": False, "err": "job not found"})
                return
            self._json_response(200, {"ok": True, "job_id": job_id, "job": job})

        def _handle_api_queue(self):
            data = self._read_body()
            url = str(data.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                self._json_response(400, {"ok": False, "err": "invalid url"})
                return
            if not queue_submitter:
                signals.url_received.emit(url, "queue")
                self._json_response(200, {"ok": True})
                return
            try:
                job = queue_submitter({**data, "url": url, "source": "rest-api"})
            except Exception as error:
                self._json_response(500, {"ok": False, "err": str(error)})
                return
            self._json_response(202, {
                "ok": True,
                "job_id": str(job.get("job_id", "")),
                "job": job,
            })

        def _handle_api_job_cancel(self):
            data = self._read_body()
            job_id = str(data.get("job_id") or data.get("id") or "").strip()
            if not job_id:
                self._json_response(400, {"ok": False, "err": "invalid job id"})
                return
            if not job_canceller:
                self._json_response(503, {"ok": False, "err": "cancellation unavailable"})
                return
            try:
                job = job_canceller(job_id)
            except Exception as error:
                self._json_response(500, {"ok": False, "err": str(error)})
                return
            if not job:
                self._json_response(404, {"ok": False, "err": "job not found"})
                return
            self._json_response(200, {"ok": True, "job_id": job_id, "job": job})

        def _read_failure_id(self):
            data = self._read_body()
            try:
                job_id = int(data.get("id") or data.get("job_id") or 0)
            except (TypeError, ValueError):
                job_id = 0
            if job_id <= 0:
                self._json_response(400, {"ok": False, "err": "invalid failure id"})
                return 0
            return job_id

        def _handle_api_failure_retry(self):
            job_id = self._read_failure_id()
            if not job_id:
                return
            try:
                if failure_retrier:
                    queue_job = failure_retrier(job_id)
                    failure = None
                else:
                    from . import db as _db
                    failure = _db.mark_failed_job_retrying(job_id)
                    queue_job = None
            except Exception as e:
                self._json_response(500, {"ok": False, "err": str(e)})
                return
            if not queue_job and not failure:
                self._json_response(404, {"ok": False, "err": "failure not found"})
                return
            if queue_job:
                self._json_response(202, {
                    "ok": True,
                    "job_id": str(queue_job.get("job_id", "")),
                    "job": queue_job,
                })
            else:
                signals.failed_job_retry_requested.emit(job_id)
                self._json_response(200, {"ok": True, "failure": failure})

        def _handle_api_failure_discard(self):
            job_id = self._read_failure_id()
            if not job_id:
                return
            try:
                if failure_discarder:
                    found = failure_discarder(job_id)
                else:
                    from . import db as _db
                    found = _db.load_failed_job(job_id) is not None
                    if found:
                        _db.mark_failed_job_discarded(job_id)
            except Exception as e:
                self._json_response(500, {"ok": False, "err": str(e)})
                return
            if not found:
                self._json_response(404, {"ok": False, "err": "failure not found"})
                return
            if not failure_discarder:
                signals.failed_job_discard_requested.emit(job_id)
            self._json_response(200, {"ok": True, "failure_id": job_id})

        def _serve_web_ui(self):
            body = _WEB_UI_HTML.encode("utf-8")
            self.send_response(200)
            self._security_headers()
            self._csp_header()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


# ── Bundled single-page web remote UI (F37) ─────────────────────────

_WEB_UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>StreamKeep Remote</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:#1e1e2e;color:#cdd6f4;min-height:100vh;padding:16px}
h1{color:#89b4fa;font-size:1.4em;margin-bottom:12px}
h2{color:#a6adc8;font-size:1.1em;margin:16px 0 8px}
.card{background:#313244;border-radius:10px;padding:14px;margin-bottom:12px}
input,button{font-size:14px;border:none;border-radius:6px;padding:8px 14px;outline:none}
input{background:#45475a;color:#cdd6f4;width:100%}
input::placeholder{color:#6c7086}
button{background:#89b4fa;color:#1e1e2e;cursor:pointer;font-weight:600}
button:hover{background:#74c7ec}
.row{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.row input{flex:1}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;
  font-weight:600;margin-right:6px}
.badge-live{background:#a6e3a1;color:#1e1e2e}
.badge-offline{background:#45475a;color:#6c7086}
.badge-queue{background:#fab387;color:#1e1e2e}
.badge-failure{background:#f38ba8;color:#1e1e2e}
.item-actions{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.item-actions button{font-size:12px;padding:5px 9px}
.item{padding:8px 0;border-bottom:1px solid #45475a}
.item:last-child{border:none}
.item .title{color:#cdd6f4}
.item .meta{color:#6c7086;font-size:12px;margin-top:2px}
.progress{height:4px;background:#45475a;border-radius:2px;margin-top:4px}
.progress .fill{height:100%;background:#a6e3a1;border-radius:2px;transition:width .3s}
.empty{color:#6c7086;font-style:italic;padding:8px 0}
#auth{max-width:360px;margin:80px auto}
#auth h1{text-align:center;margin-bottom:20px}
#app{display:none;max-width:600px;margin:0 auto}
.tab-bar{display:flex;gap:4px;margin-bottom:12px}
.tab-bar button{flex:1;background:#313244;color:#a6adc8}
.tab-bar button.active{background:#89b4fa;color:#1e1e2e}
.tab-content{display:none}
.tab-content.active{display:block}
</style>
</head>
<body>
<div id="auth">
<h1>StreamKeep Remote</h1>
<div class="card">
<p style="color:#a6adc8;margin-bottom:12px">Generate a one-time pairing code in StreamKeep Settings, then enter it here.</p>
<div class="row"><input id="token-input" type="password" placeholder="One-time pairing code"></div>
<button onclick="doAuth()" style="width:100%;margin-top:8px">Pair and connect</button>
<p id="auth-err" style="color:#f38ba8;margin-top:8px;display:none"></p>
</div>
</div>
<div id="app">
<h1>StreamKeep Remote</h1>
<div class="tab-bar">
<button class="active" onclick="switchTab('status',this)">Status</button>
<button onclick="switchTab('queue',this)">Add URL</button>
<button onclick="switchTab('library',this)">Library</button>
<button onclick="switchTab('monitor',this)">Channels</button>
</div>
<div id="tab-status" class="tab-content active">
<div class="card"><h2>Active Downloads</h2><div id="dl-list"><p class="empty">Loading...</p></div></div>
<div class="card"><h2>Active Workers</h2><div id="worker-list"><p class="empty">Loading...</p></div></div>
<div class="card"><h2>Queue</h2><div id="q-list"><p class="empty">Loading...</p></div></div>
<div class="card"><h2>Resumable</h2><div id="resume-list"><p class="empty">Loading...</p></div></div>
<div class="card"><h2>Failures</h2><div id="failure-list"><p class="empty">Loading...</p></div></div>
</div>
<div id="tab-queue" class="tab-content">
<div class="card">
<h2>Add to Queue</h2>
<div class="row"><input id="url-input" placeholder="Paste a stream or VOD URL...">
<button onclick="addUrl()">Add</button></div>
<p id="q-msg" style="color:#a6e3a1;display:none;margin-top:8px"></p>
</div>
</div>
<div id="tab-library" class="tab-content">
<div class="card"><h2>Library</h2><div id="lib-list"><p class="empty">Loading...</p></div></div>
</div>
<div id="tab-monitor" class="tab-content">
<div class="card"><h2>Monitored Channels</h2><div id="mon-list"><p class="empty">Loading...</p></div></div>
</div>
</div>
<script>
let TOKEN='';
const BASE=location.origin;
function freshHeaders(){
  const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);
  return {'X-StreamKeep-Timestamp':String(Math.floor(Date.now()/1000)),
    'X-StreamKeep-Nonce':Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('')};
}
function api(path,opts){
  opts=opts||{};opts.headers=opts.headers||{};
  opts.headers['Authorization']='Bearer '+TOKEN;
  if((opts.method||'GET').toUpperCase()!=='GET'){
    Object.assign(opts.headers,freshHeaders());
    opts.headers['Content-Type']=opts.headers['Content-Type']||'application/json';}
  return fetch(BASE+path,opts).then(r=>{
    if(r.status===401){clearInterval(_refreshId);
      document.getElementById('app').style.display='none';
      document.getElementById('auth').style.display='block';
      document.getElementById('auth-err').style.display='block';
      document.getElementById('auth-err').textContent=
        'Access expired or was rotated. Generate a new pairing code in Settings.';
      return {ok:false};}
    return r.json();});
}
function doAuth(){
  const code=document.getElementById('token-input').value.trim();
  fetch(BASE+'/pair',{method:'POST',headers:Object.assign(
    {'Content-Type':'application/json'},freshHeaders()),
    body:JSON.stringify({code,scopes:['status','queue','recovery']})}).then(async r=>{
      const d=await r.json();if(!r.ok)throw new Error(d.message||d.err||'Pairing failed');
      TOKEN=d.token||'';return api('/ping');}).then(d=>{
    if(d.ok){document.getElementById('auth').style.display='none';
      document.getElementById('app').style.display='block';refresh();
      _refreshId=setInterval(refresh,5000);}
    else throw new Error(d.message||'');
  }).catch(e=>{
    document.getElementById('auth-err').style.display='block';
    var msg=e&&e.message?e.message:
      'Pairing failed. Generate a fresh code in StreamKeep Settings.';
    document.getElementById('auth-err').textContent=msg;
  });
}
function switchTab(name,btn){
  document.querySelectorAll('.tab-content').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.tab-bar button').forEach(e=>e.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  btn.classList.add('active');
}
function addUrl(){
  const url=document.getElementById('url-input').value.trim();
  if(!url)return;
  api('/api/queue',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({url})}).then(d=>{
    const m=document.getElementById('q-msg');m.style.display='block';
    m.textContent=d.ok?'Added to queue!':'Failed: '+(d.err||'unknown');
    document.getElementById('url-input').value='';
    setTimeout(()=>m.style.display='none',3000);
  });
}
function renderItems(el,items,empty,renderFn){
  if(!items||!items.length){el.innerHTML='<p class="empty">'+empty+'</p>';return;}
  el.innerHTML=items.map(renderFn).join('');
}
function retryFailure(id){
  api('/api/failures/retry',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id})}).then(()=>refresh());
}
function discardFailure(id){
  api('/api/failures/discard',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id})}).then(()=>refresh());
}
function refresh(){
  api('/api/status').then(d=>{
    renderItems(document.getElementById('dl-list'),d.downloads,'No active downloads.',
      i=>'<div class="item"><div class="title">'+esc(i.title||i.url||'Download')+'</div>'+
        '<div class="meta">'+esc(i.status||'')+'</div>'+
        (i.percent!=null?'<div class="progress"><div class="fill" style="width:'+i.percent+'%"></div></div>':'')+
        '</div>');
    renderItems(document.getElementById('q-list'),d.queue,'Queue empty.',
      i=>'<div class="item"><span class="badge badge-queue">'+esc((i.status||'queued').toUpperCase())+'</span>'+
        esc(i.title||i.url||'?')+
        (i.note?'<div class="meta">'+esc(i.note)+'</div>':'')+'</div>');
    renderItems(document.getElementById('worker-list'),d.active_workers||[],'No active workers.',
      i=>'<div class="item"><span class="badge badge-live">'+esc((i.type||'worker').toUpperCase())+'</span>'+
        esc(i.title||i.channel||'Worker')+
        (i.running?' <span style="color:#a6e3a1">(running)</span>':'')+'</div>');
    renderItems(document.getElementById('resume-list'),d.resumable||[],'No resumable downloads.',
      i=>'<div class="item"><div class="title">'+esc(i.title||i.url||'Download')+'</div>'+
        '<div class="meta">'+esc(String(i.remaining||0))+' segments remaining</div></div>');
    renderItems(document.getElementById('failure-list'),d.failures,'No retryable failures.',
      i=>'<div class="item"><span class="badge badge-failure">'+esc((i.stage||'failed').toUpperCase())+'</span>'+
        '<span class="title">'+esc(i.title||i.url||'Failed job')+'</span>'+
        '<div class="meta">'+esc(i.platform||'')+' &middot; retry '+esc(String(i.retry_count||0))+
        ' &middot; '+esc(i.error||'')+
        (i.resume_sidecar?' &middot; <span style="color:#a6e3a1">resume available</span>':'')+
        '</div>'+
        '<div class="item-actions"><button onclick="retryFailure('+Number(i.id||0)+')">Retry</button>'+
        '<button onclick="discardFailure('+Number(i.id||0)+')">Discard</button></div></div>');
  }).catch(()=>{});
  api('/api/library').then(d=>{
    renderItems(document.getElementById('lib-list'),d.history,'No recordings yet.',
      i=>'<div class="item"><div class="title">'+esc(i.title||'Untitled')+'</div>'+
        '<div class="meta">'+esc(i.platform||'')+' &middot; '+esc(i.date||'')+' &middot; '+esc(i.quality||'')+'</div></div>');
  }).catch(()=>{});
  api('/api/monitor').then(d=>{
    renderItems(document.getElementById('mon-list'),d.channels,'No channels monitored.',
      i=>'<div class="item"><span class="badge '+(i.status==='live'?'badge-live':'badge-offline')+'">'+
        esc((i.status||'offline').toUpperCase())+'</span>'+esc(i.channel||i.channel_id||'?')+
        ' <span style="color:#6c7086">('+esc(i.platform||'')+')</span></div>');
  }).catch(()=>{});
}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
let _refreshId=0;
</script>
</body>
</html>"""
