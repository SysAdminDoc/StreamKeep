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
  GET  /api/health    — persistent severity-ranked health state  [status]
  GET  /api/operations — paged queue/monitor/failure operations [status]
  GET  /api/jobs/{id} — inspect one durable queue job            [status]
  POST /api/validate  — resolve a URL into safe picker metadata  [queue]
  POST /api/queue     — add a URL to the download queue         [queue]
  POST /api/jobs/cancel — durably cancel a queue job             [queue]
  GET  /api/library   — search/list recorded VODs               [status]
  GET  /gallery       — authenticated published-recording gallery [status]
  GET  /share/{id}    — authenticated player page                [status]
  GET  /media/{id}    — authenticated Range media stream         [status]
  GET  /feed/{id}.xml — authenticated published RSS feed         [status]
  GET  /api/shares    — published recordings/feed definitions     [status]
  GET  /api/uploads   — persisted upload progress and retry state  [status]
  GET  /api/uploads/profiles — redacted upload profiles           [status]
  DELETE /api/uploads/profiles/{id} — remove a profile and its secret [queue]
  GET  /api/intelligence — persisted summary/thumbnail jobs       [status]
  GET  /api/intelligence/profiles — redacted AI profiles          [status]
  POST /api/uploads   — queue one completed file for delivery      [queue]
  POST /api/uploads/profiles — save a secure destination profile   [queue]
  POST /api/media-server/preview — preview a library layout        [status]
  POST /api/media-server/export — materialize and optionally upload [queue]
  POST /api/intelligence/preview — show exact transcript boundary  [status]
  POST /api/intelligence/profiles — save a secure AI profile       [queue]
  POST /api/intelligence/summary — queue a consent-aware summary   [queue]
  POST /api/intelligence/thumbnail — queue smart thumbnail        [queue]
  POST /api/intelligence/cancel — cancel an analysis job          [queue]
  POST /api/intelligence/summary/edit — edit a saved summary      [queue]
  POST /api/intelligence/summary/rebuild — rebuild a saved summary [queue]
  GET  /api/monitor   — channel monitor statuses                [status]
  POST /api/failures/retry    — retry a persisted failed job    [recovery]
  POST /api/failures/cancel-retry — stop an automatic retry     [recovery]
  POST /api/failures/discard  — discard a persisted failed job  [recovery]
  POST /api/operations/action — retry or discard selected failures [recovery]
  POST /api/operations/export — return a redacted operations report [status]
  GET  /api/spec       — OpenAPI 3.1 specification (unauthenticated)
  GET  /api/tokens     — list active scoped token metadata [master]
  POST /api/tokens     — mint one labeled scoped token [master]
  DELETE /api/tokens/{id} — revoke one scoped token [master]
  GET  /               — serves the single-page web remote UI

The server runs on its own thread (stdlib http.server is threaded), and
hands received URLs to the main-thread Qt via a pyqtSignal.
"""

import hashlib
import json
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from PyQt6.QtCore import QObject, pyqtSignal

from ..har import normalize_replay_headers
from ..notifications import record_security_event as _write_security_event
from . import routes as _routes

# V163: the authentication and replay layer lives in ``auth``. Imported,
# not re-exported -- the definitions are there and nothing here redefines
# them. The facade propagates a patch of any of these to both modules.
from .auth import (  # noqa: F401
    ALL_SCOPES,
    PAIRED_TOKEN_TTL_SECONDS,
    PairingStore,
    ReplayStore,
    SCOPE_QUEUE,
    SCOPE_RECOVERY,
    SCOPE_STATUS,
    TOKEN_LABEL_MAX_LENGTH,
    TOKEN_TTL_MAX_SECONDS,
    TokenStore,
    generate_bearer_token,
    valid_bearer_token,
)

# V163: host/origin validation lives in ``origins`` and the remote UI
# rendering in ``static_assets``. Imported, not re-exported -- the
# definitions are there and nothing here redefines them.
from .origins import (  # noqa: F401
    _LOCAL_HOSTS,
    _build_allowed_hosts,
    _canonical_host,
    _format_url_host,
    _normalize_extension_origin,
    _normalize_host_header,
    _normalize_origin,
    _validate_external_origin,
)
from .static_assets import (  # noqa: F401
    _render_web_ui,
    _select_web_language,
)

PRODUCT_REST_PATHS = _routes.PRODUCT_REST_PATHS

SECURITY_EVENT_WINDOW_SECONDS = 60.0
SECURITY_EVENT_PER_CLIENT_LIMIT = 12
SECURITY_EVENT_TOTAL_LIMIT = 100


class _SecurityEventRateLimiter:
    """Bound local-server security audit writes during an attack burst."""

    def __init__(
        self, *, window_seconds=SECURITY_EVENT_WINDOW_SECONDS,
        per_client_limit=SECURITY_EVENT_PER_CLIENT_LIMIT,
        total_limit=SECURITY_EVENT_TOTAL_LIMIT,
    ):
        self.window_seconds = float(window_seconds)
        self.per_client_limit = int(per_client_limit)
        self.total_limit = int(total_limit)
        self._events = deque()
        self._lock = threading.Lock()

    def allow(self, client_id):
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            while self._events and self._events[0][0] <= cutoff:
                self._events.popleft()
            if len(self._events) >= self.total_limit:
                return False
            client_count = sum(item[1] == client_id for item in self._events)
            if client_count >= self.per_client_limit:
                return False
            self._events.append((now, client_id))
            return True


def _redacted_client_identifier(client_address):
    """Return a stable per-process client hash without retaining its address."""
    try:
        peer = str(client_address[0] or "").strip()
    except (IndexError, TypeError):
        peer = ""
    peer = peer or "unknown"
    digest = hashlib.sha256(
        f"streamkeep-local-server-client-v1:{peer}".encode("utf-8")
    ).hexdigest()[:16]
    return f"client-{digest}"














class _ServerSignals(QObject):
    url_received = pyqtSignal(str, str)   # url, action ("fetch" | "queue")
    handoff_received = pyqtSignal(object, str)  # bounded context, action
    clip_received = pyqtSignal(str, float, float)  # url, start_secs, end_secs
    extension_origin_pinned = pyqtSignal(str)  # origin, or empty when cleared
    security_event = pyqtSignal(object)  # sanitized auth/mutation rejection
    failed_job_retry_requested = pyqtSignal(int)
    failed_job_discard_requested = pyqtSignal(int)


class _CompanionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


_HANDOFF_CONTEXT_FIELDS = ("tab_url", "tab_title", "kind", "content_type")


def _clean_handoff_text(value, max_len):
    text = str(value or "").strip()
    if len(text) > max_len or any(
        ord(char) < 32 or ord(char) == 127 for char in text
    ):
        return ""
    return text


def _normalize_handoff_context(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        return None
    context = {}
    for field in _HANDOFF_CONTEXT_FIELDS:
        text = _clean_handoff_text(value.get(field), 4096 if field == "tab_url" else 256)
        if text:
            context[field] = text
    tab_url = context.get("tab_url", "")
    if tab_url and not tab_url.startswith(("http://", "https://")):
        context.pop("tab_url", None)
    return context


def _normalize_handoff_payload(data):
    """Return ``(request_headers, source_context)`` for a handoff body."""
    raw_headers = data.get("request_headers")
    if raw_headers is not None and not isinstance(raw_headers, (dict, list)):
        return None
    headers = normalize_replay_headers(raw_headers)
    context = _normalize_handoff_context(data.get("source_context"))
    if context is None:
        return None
    return headers, context
















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
        allow_private_network=False,
        extension_origin="",
    ):
        self.allow_private_network = bool(allow_private_network)
        self._token_store = TokenStore()
        self._pairing_store = PairingStore()
        self._replay_store = ReplayStore()
        self.token = str(master_token or generate_bearer_token())
        if not valid_bearer_token(self.token):
            raise ValueError("Stored companion token is invalid or too short.")
        self._token_store.add(
            self.token, ALL_SCOPES, label="master", is_master=True,
        )
        self.port = int(port or 0)
        self._httpd = None
        self._thread = None
        self._signals = _ServerSignals()
        self._extension_origin_lock = threading.Lock()
        raw_extension_origin = str(extension_origin or "").strip()
        self._extension_origin = _normalize_extension_origin(raw_extension_origin)
        if raw_extension_origin and not self._extension_origin:
            raise ValueError("Stored companion extension origin is invalid.")
        self.url_received = self._signals.url_received
        self.handoff_received = self._signals.handoff_received
        self.clip_received = self._signals.clip_received
        self.extension_origin_pinned = self._signals.extension_origin_pinned
        self.security_event = self._signals.security_event
        self.failed_job_retry_requested = self._signals.failed_job_retry_requested
        self.failed_job_discard_requested = self._signals.failed_job_discard_requested
        self.state_provider = None   # callable -> dict (F37)
        self.probe_submitter = None  # callable(dict) -> picker response
        self.queue_submitter = None  # callable(dict) -> durable job dict
        self.job_canceller = None    # callable(job_id) -> durable job dict
        self.failure_retrier = None  # callable(failure_id) -> durable job dict
        self.failure_retry_canceller = None  # callable(failure_id) -> bool
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
        self._token_store.add(
            self.token, ALL_SCOPES, label="master", is_master=True,
        )
        self._clear_extension_origin()
        return self.token

    @property
    def extension_origin(self):
        with self._extension_origin_lock:
            return self._extension_origin

    def _get_extension_origin(self):
        return self.extension_origin

    def _pin_extension_origin(self, origin):
        normalized = _normalize_extension_origin(origin)
        if not normalized:
            return False
        with self._extension_origin_lock:
            current = self._extension_origin
            if current:
                return secrets.compare_digest(current, normalized)
            self._extension_origin = normalized
        self.extension_origin_pinned.emit(normalized)
        return True

    def _clear_extension_origin(self):
        with self._extension_origin_lock:
            if not self._extension_origin:
                return
            self._extension_origin = ""
        self.extension_origin_pinned.emit("")

    def create_scoped_token(self, scopes, *, label="", origin="", expires_at=0.0):
        """Mint a token restricted to the given scopes."""
        valid = frozenset(scopes) & ALL_SCOPES
        if not valid:
            raise ValueError(f"No valid scopes in {scopes!r}")
        tok = generate_bearer_token()
        self._token_store.add(
            tok, valid, label=label or "unnamed token",
            origin=str(origin or ""), expires_at=expires_at,
        )
        return tok

    def create_pairing_code(self, scopes=ALL_SCOPES, *, ttl_seconds=300):
        """Create a one-use code that never appears in a URL or request log."""
        return self._pairing_store.issue(scopes, ttl_seconds=ttl_seconds)

    def revoke_token(self, token):
        """Revoke a specific token (scoped or master)."""
        self._token_store.remove(token)

    def list_scoped_tokens(self):
        """Return active scoped-token metadata without bearer values."""
        return self._token_store.list_metadata()

    def revoke_token_by_id(self, token_id):
        """Revoke one scoped token by its metadata id."""
        return self._token_store.remove_by_id(token_id)

    def start(self):
        if self._httpd is not None:
            self.stop()
        handler_cls = _build_handler(
            self._token_store,
            self._signals,
            self.state_provider,
            probe_submitter=self.probe_submitter,
            queue_submitter=self.queue_submitter,
            job_canceller=self.job_canceller,
            failure_retrier=self.failure_retrier,
            failure_retry_canceller=self.failure_retry_canceller,
            failure_discarder=self.failure_discarder,
            allowed_hosts=self._allowed_hosts,
            pairing_store=self._pairing_store,
            replay_store=self._replay_store,
            external_origin=self.external_origin,
            allow_private_network=self.allow_private_network,
            extension_origin_getter=self._get_extension_origin,
            extension_origin_pinner=self._pin_extension_origin,
            master_token=self.token,
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
    probe_submitter=None,
    queue_submitter=None,
    job_canceller=None,
    failure_retrier=None,
    failure_retry_canceller=None,
    failure_discarder=None,
    allowed_hosts=None,
    pairing_store=None,
    replay_store=None,
    external_origin="",
    allow_private_network=False,
    extension_origin_getter=None,
    extension_origin_pinner=None,
    master_token="",
):
    allow_private_network = bool(allow_private_network)
    allowed_hosts = frozenset(allowed_hosts or _build_allowed_hosts())
    pairing_store = pairing_store or PairingStore()
    replay_store = replay_store or ReplayStore()
    external_origin = str(external_origin or "")
    extension_origin_getter = extension_origin_getter or (lambda: "")
    extension_origin_pinner = extension_origin_pinner or (lambda _origin: True)
    master_token = str(master_token or "")
    external_host = (
        _canonical_host(urlsplit(external_origin).hostname)
        if external_origin else ""
    )
    external_authority = (
        urlsplit(external_origin).netloc.lower() if external_origin else ""
    )
    security_event_limiter = _SecurityEventRateLimiter()

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args, **_kwargs):
            return

        def _request_route(self):
            raw_path = str(self.path or "/").split("?", 1)[0]
            try:
                return urlsplit(raw_path).path or raw_path
            except ValueError:
                return raw_path

        def _record_security_event(self, reason):
            client_id = _redacted_client_identifier(self.client_address)
            if not security_event_limiter.allow(client_id):
                return None
            event = _write_security_event({
                "route": self._request_route(),
                "reason": reason,
                "client_id": client_id,
            })
            try:
                signals.security_event.emit(event)
            except RuntimeError:
                pass
            return event

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
                pinned = _normalize_extension_origin(extension_origin_getter())
                return not pinned or secrets.compare_digest(normalized, pinned)
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

        def _effective_request_origin(self):
            """Return the browser-visible origin after validating its boundary.

            Same-origin fetches omit ``Origin`` on safe methods.  In that case
            the request authority plus ``Sec-Fetch-Site: same-origin`` is the
            browser invariant we can verify.  Reverse-proxy deployments use
            their already-validated forwarded authority; direct requests must
            name this listener's exact loopback port.
            """
            forwarded_proto = str(
                self.headers.get("X-Forwarded-Proto", "") or ""
            ).lower()
            forwarded_host = str(
                self.headers.get("X-Forwarded-Host", "") or ""
            ).lower()
            if (
                external_origin
                and forwarded_proto == "https"
                and secrets.compare_digest(forwarded_host, external_authority)
                and self._external_boundary_ok()
            ):
                return external_origin

            host_values = self.headers.get_all("Host", failobj=[])
            if len(host_values) != 1:
                return ""
            origin = _normalize_origin(
                f"http://{host_values[0]}", allow_extensions=False
            )
            if not origin:
                return ""
            parsed = urlsplit(origin)
            return origin if (
                _canonical_host(parsed.hostname) in _LOCAL_HOSTS
                and parsed.port == int(self.server.server_address[1])
            ) else ""

        def _reject_bad_host(self):
            if not self._host_ok():
                self._record_security_event("host_denied")
                self._json_response(403, {
                    "ok": False,
                    "err": "host_denied",
                    "message": "Request Host is not configured for this listener.",
                })
                return True
            if not self._external_boundary_ok():
                self._record_security_event("transport_denied")
                self._json_response(403, {
                    "ok": False,
                    "err": "transport_denied",
                    "message": "LAN control requires the configured HTTPS reverse proxy.",
                })
                return True
            return False

        def _token_grant(self):
            """Return ``((grant, token), error)`` for a bearer request."""
            hdr = self.headers.get("Authorization", "") or ""
            scheme, separator, candidate = hdr.partition(" ")
            if separator and scheme.lower() == "bearer":
                candidate = candidate.strip()
            else:
                candidate = self._session_cookie()
            if not candidate:
                return None, "token_invalid"
            grant = token_store.check(candidate)
            if grant is None:
                return None, "token_invalid"
            raw_origin = str(self.headers.get("Origin", "") or "").strip()
            request_origin = _normalize_origin(raw_origin)
            try:
                request_scheme = urlsplit(raw_origin).scheme.lower()
            except ValueError:
                request_scheme = ""
            if request_scheme in ("chrome-extension", "moz-extension"):
                pinned = _normalize_extension_origin(extension_origin_getter())
                if not request_origin or (pinned and not secrets.compare_digest(
                    request_origin, pinned
                )):
                    return None, "token_origin_mismatch"
            if grant.origin:
                if request_origin:
                    if not secrets.compare_digest(request_origin, grant.origin):
                        return None, "token_origin_mismatch"
                else:
                    fetch_site = str(
                        self.headers.get("Sec-Fetch-Site", "") or ""
                    ).lower()
                    if fetch_site == "cross-site":
                        return None, "cross_site_denied"
                    if fetch_site != "same-origin":
                        return None, "token_origin_required"
                    request_origin = self._effective_request_origin()
                    if (
                        not request_origin
                        or not secrets.compare_digest(request_origin, grant.origin)
                    ):
                        return None, "token_origin_mismatch"
            self._auth_token = candidate
            return (grant, candidate), ""

        def _session_cookie(self):
            """Read the browser-only session cookie used by HTML media links."""
            raw = str(self.headers.get("Cookie", "") or "")
            for part in raw.split(";"):
                name, separator, value = part.strip().partition("=")
                if separator and name == "streamkeep_session":
                    return value.strip()
            return ""

        def _require_auth(self, scope=None, *, mutating=False, master_only=False):
            """Check auth + optional scope. Returns True if authorized."""
            auth, error = self._token_grant()
            if auth is None:
                self._record_security_event(error)
                messages = {
                    "token_invalid": (
                        "The access token is missing, expired, or revoked. "
                        "Re-pair with a new code from StreamKeep Settings."
                    ),
                    "token_origin_required": (
                        "This paired token requires its original browser origin. "
                        "Open the StreamKeep remote from the same address used to pair."
                    ),
                    "token_origin_mismatch": (
                        "This token was paired with a different browser origin. "
                        "Return to the original StreamKeep remote address or pair again."
                    ),
                    "cross_site_denied": (
                        "A cross-site page cannot use this paired StreamKeep session."
                    ),
                }
                self._json_response(
                    403 if error == "cross_site_denied" else 401,
                    {
                        "ok": False,
                        "err": error,
                        "message": messages[error],
                    },
                )
                return False
            grant, token = auth
            if master_only and not (
                grant.is_master
                or (master_token and secrets.compare_digest(token, master_token))
            ):
                self._record_security_event("master_token_required")
                self._json_response(403, {
                    "ok": False,
                    "err": "master_token_required",
                    "message": "Only the secure master token can manage API tokens.",
                })
                return False
            if scope and scope not in grant.scopes:
                self._record_security_event("scope_denied")
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
                self._record_security_event("origin_denied")
                self._json_response(403, {
                    "ok": False,
                    "err": "origin_denied",
                    "message": "Mutating requests require an approved origin.",
                })
                return False
            fetch_site = str(self.headers.get("Sec-Fetch-Site", "") or "").lower()
            if fetch_site == "cross-site":
                self._record_security_event("cross_site_denied")
                self._json_response(403, {
                    "ok": False,
                    "err": "cross_site_denied",
                    "message": "Cross-site mutation was rejected.",
                })
                return False
            content_type = str(self.headers.get("Content-Type", "") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self._record_security_event("content_type_denied")
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
                self._record_security_event(error)
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
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS"
            )
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

        def _json_response(self, code, obj, *, headers=None):
            # Early auth/origin/Host rejections happen before endpoint handlers
            # read the JSON body. Draining a bounded body avoids a Windows TCP
            # reset that can otherwise hide the response from the client.
            self._discard_unread_body()
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self._cors()
            self._security_headers()
            self._set_auth_cookie()
            self.send_header("Content-Type", "application/json")
            for name, value in (headers or {}).items():
                self.send_header(str(name), str(value))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html_response(self, code, body):
            payload = str(body or "").encode("utf-8")
            self.send_response(code)
            self._cors()
            self._security_headers()
            self._csp_header()
            self._set_auth_cookie()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _bytes_response(self, code, body, headers=None):
            payload = bytes(body or b"")
            self.send_response(code)
            self._cors()
            self._security_headers()
            self._set_auth_cookie()
            for name, value in (headers or {}).items():
                self.send_header(str(name), str(value))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _set_auth_cookie(self):
            token = str(getattr(self, "_auth_token", "") or "")
            if token:
                cookie = (
                    "streamkeep_session=" + token
                    + "; Path=/; HttpOnly; SameSite=Strict"
                )
                if external_origin and self._external_boundary_ok():
                    cookie += "; Secure"
                self.send_header(
                    "Set-Cookie",
                    cookie,
                )

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
                data = json.loads(raw) if raw else {}
                return data if isinstance(data, dict) else {}
            except (ValueError, OSError):
                return {}

        def do_OPTIONS(self):
            if self._reject_bad_host():
                return
            origin = self.headers.get("Origin", "")
            if not origin or not self._origin_ok(origin):
                self._record_security_event("origin_denied")
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
            request = urlsplit(self.path)
            path = request.path

            if path == "/":
                explicit_language = parse_qs(request.query).get("lang", [""])[0]
                self._serve_web_ui(explicit_language=explicit_language)
                return

            if path == "/api/spec":
                self._handle_api_spec()
            elif path == "/api/tokens":
                if self._require_auth(master_only=True):
                    self._handle_api_tokens()
            elif path == "/ping":
                if self._require_auth():
                    self._json_response(200, {"ok": True, "app": "StreamKeep"})
            elif path == "/gallery":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_gallery()
            elif path.startswith("/share/"):
                if self._require_auth(SCOPE_STATUS):
                    self._handle_share(path.removeprefix("/share/"))
            elif path.startswith("/media/"):
                if self._require_auth(SCOPE_STATUS):
                    self._handle_media(path.removeprefix("/media/"))
            elif path.startswith("/feed/") and path.endswith(".xml"):
                if self._require_auth(SCOPE_STATUS):
                    self._handle_feed(path.removeprefix("/feed/")[:-4])
            elif path == "/api/status":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_status()
            elif path == "/api/health":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_health()
            elif path == "/api/operations":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_operations()
            elif path == "/api/library":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_library()
            elif path == "/api/shares":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_shares()
            elif path == "/api/uploads":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_uploads()
            elif path == "/api/uploads/profiles":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_upload_profiles()
            elif path == "/api/intelligence":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_intelligence()
            elif path == "/api/intelligence/profiles":
                if self._require_auth(SCOPE_STATUS):
                    self._handle_api_intelligence_profiles()
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
            elif path == "/api/tokens":
                if self._require_auth(mutating=True, master_only=True):
                    self._handle_api_token_create()
            elif path == "/send_url":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_send_url()
            elif path == "/api/validate":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_validate()
            elif path == "/api/shares/recording":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_share_recording()
            elif path == "/api/shares/recording/revoke":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_share_recording_revoke()
            elif path == "/api/shares/feed":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_share_feed()
            elif path == "/api/shares/feed/revoke":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_share_feed_revoke()
            elif path == "/api/uploads/profiles":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_upload_profile_save()
            elif path == "/api/intelligence/profiles":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_intelligence_profile_save()
            elif path == "/api/uploads":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_upload_create()
            elif path == "/api/uploads/retry":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_upload_retry()
            elif path == "/api/uploads/cancel":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_upload_cancel()
            elif path == "/api/media-server/preview":
                if self._require_auth(SCOPE_STATUS, mutating=True):
                    self._handle_api_media_server_preview()
            elif path == "/api/media-server/export":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_media_server_export()
            elif path == "/api/intelligence/preview":
                if self._require_auth(SCOPE_STATUS, mutating=True):
                    self._handle_api_intelligence_preview()
            elif path == "/api/intelligence/summary":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_intelligence_summary()
            elif path == "/api/intelligence/thumbnail":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_intelligence_thumbnail()
            elif path == "/api/intelligence/cancel":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_intelligence_cancel()
            elif path == "/api/intelligence/summary/edit":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_intelligence_summary_edit()
            elif path == "/api/intelligence/summary/rebuild":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_intelligence_summary_rebuild()
            elif path == "/api/queue":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_queue()
            elif path == "/api/jobs/cancel":
                if self._require_auth(SCOPE_QUEUE, mutating=True):
                    self._handle_api_job_cancel()
            elif path == "/api/failures/retry":
                if self._require_auth(SCOPE_RECOVERY, mutating=True):
                    self._handle_api_failure_retry()
            elif path == "/api/failures/cancel-retry":
                if self._require_auth(SCOPE_RECOVERY, mutating=True):
                    self._handle_api_failure_cancel_retry()
            elif path == "/api/failures/discard":
                if self._require_auth(SCOPE_RECOVERY, mutating=True):
                    self._handle_api_failure_discard()
            elif path == "/api/operations/action":
                if self._require_auth(SCOPE_RECOVERY, mutating=True):
                    self._handle_api_operations_action()
            elif path == "/api/operations/export":
                if self._require_auth(SCOPE_STATUS, mutating=True):
                    self._handle_api_operations_export()
            else:
                self._json_response(404, {"ok": False, "err": "not_found"})

        def do_DELETE(self):
            if self._reject_bad_host():
                return
            path = self.path.split("?", 1)[0]
            from .routes import handle_delete
            if not handle_delete(self, path):
                self._json_response(404, {"ok": False, "err": "not_found"})

        def _handle_pair(self):
            origin_header = self.headers.get("Origin", "")
            if origin_header and not self._origin_ok(origin_header):
                self._record_security_event("origin_denied")
                self._json_response(403, {
                    "ok": False,
                    "err": "origin_denied",
                    "message": "Pairing origin is not approved.",
                })
                return
            fetch_site = str(self.headers.get("Sec-Fetch-Site", "") or "").lower()
            if fetch_site == "cross-site":
                self._record_security_event("cross_site_denied")
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
                self._record_security_event(error)
                self._json_response(400, {
                    "ok": False,
                    "err": error,
                    "message": "Pairing requires a fresh timestamp and nonce.",
                })
                return
            content_type = str(self.headers.get("Content-Type", "") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self._record_security_event("content_type_denied")
                self._json_response(415, {"ok": False, "err": "content_type_denied"})
                return
            data = self._read_body(max_bytes=4096)
            scopes = pairing_store.consume(data.get("code"))
            if not scopes:
                self._record_security_event("pairing_invalid")
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
                    self._record_security_event("pairing_scope_invalid")
                    self._json_response(400, {
                        "ok": False,
                        "err": "pairing_scope_invalid",
                        "message": "The client did not request an approved scope.",
                    })
                    return
            origin = _normalize_origin(origin_header) if origin_header else ""
            if not origin and fetch_site == "same-origin":
                origin = self._effective_request_origin()
                if not origin:
                    self._record_security_event("origin_denied")
                    self._json_response(403, {
                        "ok": False,
                        "err": "origin_denied",
                        "message": (
                            "The browser's same-origin pairing address could not "
                            "be verified."
                        ),
                    })
                    return
            label = str(data.get("label") or "paired client").strip()
            if not label or len(label) > TOKEN_LABEL_MAX_LENGTH or any(
                character in label for character in "\r\n"
            ):
                self._record_security_event("pairing_label_invalid")
                self._json_response(400, {
                    "ok": False,
                    "err": "pairing_label_invalid",
                    "message": "Token label must be 1-128 characters.",
                })
                return
            if origin.startswith(("chrome-extension://", "moz-extension://")):
                if not extension_origin_pinner(origin):
                    self._record_security_event("origin_denied")
                    self._json_response(403, {
                        "ok": False,
                        "err": "origin_denied",
                        "message": "Pairing origin is not approved.",
                    })
                    return
            token = generate_bearer_token()
            expires_at = time.time() + PAIRED_TOKEN_TTL_SECONDS
            token_id = token_store.add(
                token, scopes, label=label, origin=origin, expires_at=expires_at,
            )
            self._json_response(201, {
                "ok": True,
                "token": token,
                "id": token_id,
                "label": label,
                "scopes": sorted(scopes),
                "origin": origin,
                "expires_at": int(expires_at),
            })

        def _ssrf_reject(self, url):
            """Return True (and send 400) when *url* targets a blocked address.

            Guards against SSRF via the network-exposed server: a submitted URL
            resolving to loopback/link-local/cloud-metadata/private-LAN space is
            refused unless private-network access was explicitly enabled.
            """
            from ..net_guard import url_target_allowed
            ok, reason = url_target_allowed(
                url, allow_private_network=allow_private_network,
            )
            if not ok:
                self._json_response(400, {"ok": False, "err": "url_not_allowed",
                                          "message": reason})
                return True
            return False

        def _handle_send_url(self):
            data = self._read_body()
            url = str(data.get("url") or "").strip()
            action = str(data.get("action") or "fetch").strip().lower()
            if action not in ("fetch", "queue"):
                action = "fetch"
            if not url.startswith(("http://", "https://")):
                self._json_response(400, {"ok": False, "err": "invalid url"})
                return
            if self._ssrf_reject(url):
                return
            handoff = _normalize_handoff_payload(data)
            if handoff is None:
                self._json_response(400, {
                    "ok": False,
                    "err": "invalid handoff context",
                })
                return
            request_headers, source_context = handoff
            if "request_headers" in data:
                data["request_headers"] = request_headers
            else:
                data.pop("request_headers", None)
            if source_context:
                data["source_context"] = source_context
            else:
                data.pop("source_context", None)
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
                has_selection = any(
                    data.get(key)
                    for key in (
                        "validation_id",
                        "media_item_id",
                        "media_item_ids",
                        "background_audio_id",
                    )
                )
                if request_headers or source_context or has_selection:
                    signals.handoff_received.emit(
                        {**data, "url": url}, action
                    )
                else:
                    signals.url_received.emit(url, action)
                self._json_response(200, {"ok": True})

        # ── REST API handlers (F37) ────────────────────────────────

        def _get_state(self):
            if state_provider:
                try:
                    return state_provider()
                except Exception:
                    pass  # safe: best-effort fallback; preserve the primary operation
            return {}

        def _handle_api_spec(self):
            from .. import openapi
            self._json_response(200, openapi.build_openapi_spec())

        def _handle_api_tokens(self):
            self._json_response(200, {
                "ok": True,
                "tokens": token_store.list_metadata(),
            })

        def _handle_api_token_create(self):
            data = self._read_body(max_bytes=16_384)
            label = str(data.get("label") or "").strip()
            if not label or len(label) > TOKEN_LABEL_MAX_LENGTH or any(
                character in label for character in "\r\n"
            ):
                self._json_response(400, {
                    "ok": False,
                    "err": "label_required",
                    "message": "A token label between 1 and 128 characters is required.",
                })
                return
            requested_scopes = data.get("scopes")
            if not isinstance(requested_scopes, list):
                self._json_response(400, {
                    "ok": False,
                    "err": "scopes_required",
                    "message": "Scopes must be a non-empty list.",
                })
                return
            scopes = frozenset(str(scope).strip() for scope in requested_scopes)
            if not scopes or not scopes.issubset(ALL_SCOPES):
                self._json_response(400, {
                    "ok": False,
                    "err": "invalid_scopes",
                    "message": f"Scopes must be drawn from: {', '.join(sorted(ALL_SCOPES))}.",
                })
                return
            raw_origin = str(data.get("origin") or "").strip()
            origin = _normalize_origin(raw_origin) if raw_origin else ""
            if raw_origin and not origin:
                self._json_response(400, {
                    "ok": False,
                    "err": "invalid_origin",
                    "message": "Origin must be a valid browser origin.",
                })
                return
            raw_ttl = data.get("expires_in")
            expires_at = 0.0
            if raw_ttl not in (None, ""):
                try:
                    ttl = int(raw_ttl)
                except (TypeError, ValueError):
                    ttl = -1
                if ttl < 60 or ttl > TOKEN_TTL_MAX_SECONDS:
                    self._json_response(400, {
                        "ok": False,
                        "err": "invalid_expiry",
                        "message": (
                            "expires_in must be between 60 seconds and "
                            f"{TOKEN_TTL_MAX_SECONDS} seconds."
                        ),
                    })
                    return
                expires_at = time.time() + ttl
            token = generate_bearer_token()
            token_store.add(
                token, scopes, label=label, origin=origin, expires_at=expires_at,
            )
            metadata = token_store.metadata_for_token(token)
            self._json_response(201, {
                "ok": True,
                "token": token,
                **(metadata or {}),
            })

        def _handle_api_token_revoke(self, token_id):
            if not token_id or "/" in token_id:
                self._json_response(404, {"ok": False, "err": "token_not_found"})
                return
            if not token_store.remove_by_id(token_id):
                self._json_response(404, {
                    "ok": False,
                    "err": "token_not_found",
                    "message": "No active scoped token has that id.",
                })
                return
            self._json_response(200, {
                "ok": True,
                "id": token_id,
                "revoked": True,
            })

        def _handle_api_status(self):
            state = self._get_state()
            from .. import db as _db
            failures = [
                _db.failed_job_public_view(item)
                for item in state.get("failures", [])
                if isinstance(item, dict)
            ]
            self._json_response(200, {
                "ok": True,
                "downloads": state.get("downloads", []),
                "queue": state.get("queue", []),
                "failures": failures,
                "retry_circuits": state.get("retry_circuits", []),
                "backup": state.get("backup", {}),
                "live_channels": state.get("live_channels", []),
                "active_workers": state.get("active_workers", []),
                "resumable": state.get("resumable", []),
            })

        def _handle_api_health(self):
            state = self._get_state()
            from ..health import public_snapshot
            self._json_response(200, {
                "ok": True,
                "health": public_snapshot(state.get("health", {})),
            })

        def _handle_api_operations(self):
            from ..operations import OperationsFilters, query_operations

            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            values = {key: entries[-1] for key, entries in query.items() if entries}
            page = query_operations(OperationsFilters.from_mapping(values))
            self._json_response(200, page.to_dict())

        def _handle_api_operations_export(self):
            from ..operations import OperationsFilters, export_operations_report

            data = self._read_body()
            raw_filters = data.get("filters", data)
            if not isinstance(raw_filters, dict):
                self._json_response(400, {
                    "ok": False, "err": "filters must be an object",
                })
                return
            try:
                report = export_operations_report(OperationsFilters.from_mapping(raw_filters))
            except Exception as error:
                from ..diagnostics import redact_text
                self._json_response(400, {
                    "ok": False, "err": "operations_export_failed",
                    "message": redact_text(str(error)),
                })
                return
            self._json_response(200, {"ok": True, "report": report})

        def _handle_api_operations_action(self):
            from ..operations import discard_failure_ids, retry_failure_ids

            data = self._read_body()
            action = str(data.get("action") or "").strip().lower()
            raw_ids = data.get("failure_ids", data.get("ids", data.get("id", [])))
            if isinstance(raw_ids, str):
                raw_ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
            if not isinstance(raw_ids, list):
                raw_ids = [raw_ids]
            raw_ids = raw_ids[:100]
            if action == "retry":
                results = retry_failure_ids(raw_ids)
            elif action == "discard":
                results = discard_failure_ids(raw_ids)
            else:
                self._json_response(400, {
                    "ok": False, "err": "action must be retry or discard",
                })
                return
            self._json_response(200, {
                "ok": True, "action": action, "results": results,
            })

        def _handle_api_library(self):
            state = self._get_state()
            self._json_response(200, {
                "ok": True,
                "history": state.get("history", []),
            })

        def _publishing_base_url(self):
            if external_origin:
                return external_origin.rstrip("/")
            host = _format_url_host(self.server.server_address[0])
            return f"http://{host}:{int(self.server.server_address[1])}"

        @staticmethod
        def _published_media(row):
            from ..gallery import find_media_file

            return find_media_file(row.get("path", "")) if row else ""

        def _share_public_view(self, row, media_path=""):
            from ..gallery import _media_type

            share_id = str(row.get("share_id", "") or "")
            base = self._publishing_base_url()
            return {
                "share_id": share_id,
                "history_id": int(row.get("id", 0) or 0),
                "title": str(row.get("title", "") or ""),
                "platform": str(row.get("platform", "") or ""),
                "channel": str(row.get("channel", "") or ""),
                "date": str(row.get("date", "") or ""),
                "size": str(row.get("size", "") or ""),
                "available": bool(media_path),
                "media_type": _media_type(media_path) if media_path else "",
                "share_url": f"{base}/share/{share_id}",
                "media_url": f"{base}/media/{share_id}",
            }

        def _handle_gallery(self):
            from .. import db as _db
            from ..gallery import render_gallery_html

            entries = []
            for row in _db.published_recordings():
                media_path = self._published_media(row)
                if not media_path:
                    continue
                entry = dict(row)
                entry["media"] = media_path
                entries.append(entry)
            self._html_response(
                200,
                render_gallery_html(self._publishing_base_url(), entries),
            )

        def _handle_share(self, share_id):
            from .. import db as _db
            from ..gallery import render_share_html

            row = _db.published_recording(share_id)
            media_path = self._published_media(row)
            if not row or not media_path:
                self._html_response(404, "<h1>Not Found</h1>")
                return
            entry = dict(row)
            entry["media"] = media_path
            self._html_response(
                200,
                render_share_html(
                    row.get("share_id", share_id),
                    self._publishing_base_url(),
                    info=entry,
                ),
            )

        def _handle_media(self, share_id):
            from .. import db as _db
            from ..gallery import serve_media_range

            row = _db.published_recording(share_id)
            media_path = self._published_media(row)
            if not row or not media_path:
                self._bytes_response(404, b"", {"Content-Type": "text/plain"})
                return
            data, status, headers = serve_media_range(
                media_path, self.headers.get("Range", "")
            )
            if data is None:
                data = b""
            self._bytes_response(status, data, headers)

        def _handle_feed(self, feed_id):
            from .. import db as _db
            from ..feed import generate_rss

            feed_row = _db.published_feed(feed_id)
            if feed_row is None:
                self._bytes_response(404, b"", {"Content-Type": "text/plain"})
                return
            entries = []
            for row in _db.published_recordings_for_feed(feed_id) or []:
                media_path = self._published_media(row)
                if not media_path:
                    continue
                entry = dict(row)
                entry["media_path"] = media_path
                entries.append(entry)
            try:
                body = generate_rss(
                    entries,
                    self._publishing_base_url(),
                    title=feed_row.get("title", "StreamKeep"),
                    channel=feed_row.get("channel") or None,
                ).encode("utf-8")
            except ValueError:
                self._bytes_response(500, b"", {"Content-Type": "text/plain"})
                return
            self._bytes_response(
                200,
                body,
                {"Content-Type": "application/rss+xml; charset=utf-8"},
            )

        def _handle_api_shares(self):
            from .. import db as _db

            recordings = [
                self._share_public_view(row, self._published_media(row))
                for row in _db.published_recordings()
            ]
            feeds = []
            base = self._publishing_base_url()
            for row in _db.published_feeds():
                feed = dict(row)
                feed["feed_url"] = f"{base}/feed/{feed['feed_id']}.xml"
                feeds.append(feed)
            self._json_response(200, {
                "ok": True,
                "recordings": recordings,
                "feeds": feeds,
            })

        def _handle_api_uploads(self):
            from ..upload.runtime import get_runtime, public_job

            runtime = get_runtime()
            runtime.start_due()
            from .. import db as _db
            self._json_response(200, {
                "ok": True,
                "uploads": [
                    public_job(row) for row in _db.load_upload_jobs(limit=100)
                ],
            })

        def _handle_api_upload_profiles(self):
            from ..upload.runtime import list_profiles

            self._json_response(200, {"ok": True, "profiles": list_profiles()})

        def _handle_api_upload_profile_save(self):
            from ..upload.runtime import save_profile

            data = self._read_body()
            profile_id = str(data.get("profile_id") or data.get("id") or "").strip()
            adapter = str(data.get("adapter") or "").strip()
            config = data.get("config", {})
            if not profile_id or not adapter or not isinstance(config, dict):
                self._json_response(400, {
                    "ok": False,
                    "err": "profile_id, adapter, and object config are required",
                })
                return
            try:
                profile = save_profile(
                    profile_id, adapter, config,
                    label=str(data.get("label") or "").strip(),
                )
            except Exception as error:
                self._json_response(400, {
                    "ok": False,
                    "err": "upload_profile_invalid",
                    "message": str(error),
                })
                return
            self._json_response(201, {"ok": True, "profile": profile})

        def _handle_api_upload_create(self):
            from ..upload.runtime import get_runtime, public_job

            data = self._read_body()
            profile_id = str(data.get("profile_id") or "").strip()
            source_path = str(data.get("source_path") or data.get("path") or "").strip()
            metadata = data.get("metadata", {})
            if not profile_id or not source_path or not isinstance(metadata, dict):
                self._json_response(400, {
                    "ok": False,
                    "err": "profile_id, source_path, and object metadata are required",
                })
                return
            try:
                job = get_runtime().enqueue(
                    profile_id, source_path, metadata=metadata,
                )
            except Exception as error:
                self._json_response(400, {
                    "ok": False,
                    "err": "upload_job_invalid",
                    "message": str(error),
                })
                return
            self._json_response(202, {"ok": True, "job": public_job(job)})

        def _handle_api_upload_retry(self):
            from ..upload.runtime import get_runtime, public_job

            data = self._read_body()
            upload_id = str(data.get("upload_id") or data.get("id") or "").strip()
            runtime = get_runtime()
            if not runtime.retry(upload_id):
                self._json_response(404, {"ok": False, "err": "upload_not_retryable"})
                return
            from .. import db as _db
            self._json_response(202, {
                "ok": True,
                "job": public_job(_db.load_upload_job(upload_id)),
            })

        def _handle_api_upload_cancel(self):
            from ..upload.runtime import get_runtime, public_job

            data = self._read_body()
            upload_id = str(data.get("upload_id") or data.get("id") or "").strip()
            runtime = get_runtime()
            if not runtime.cancel(upload_id):
                self._json_response(404, {"ok": False, "err": "upload_not_cancellable"})
                return
            from .. import db as _db
            self._json_response(200, {
                "ok": True,
                "job": public_job(_db.load_upload_job(upload_id)),
            })

        @staticmethod
        def _intelligence_error(error):
            from ..diagnostics import redact_text

            return redact_text(str(error or "intelligence request failed"))

        def _handle_api_intelligence(self):
            from ..intelligence.runtime import get_runtime

            runtime = get_runtime()
            self._json_response(200, {
                "ok": True, "jobs": runtime.list_jobs(limit=100),
            })

        def _handle_api_intelligence_profiles(self):
            from ..intelligence.runtime import list_profiles

            self._json_response(200, {"ok": True, "profiles": list_profiles()})

        def _handle_api_intelligence_profile_save(self):
            from ..intelligence.runtime import save_profile

            data = self._read_body()
            profile_id = str(data.get("profile_id") or data.get("id") or "").strip()
            provider = str(data.get("provider") or "").strip()
            config = data.get("config", {})
            if not profile_id or not provider or not isinstance(config, dict):
                self._json_response(400, {
                    "ok": False,
                    "err": "profile_id, provider, and object config are required",
                })
                return
            try:
                profile = save_profile(
                    profile_id, provider, config,
                    label=str(data.get("label") or "").strip(),
                )
            except Exception as error:
                self._json_response(400, {
                    "ok": False, "err": "intelligence_profile_invalid",
                    "message": self._intelligence_error(error),
                })
                return
            self._json_response(201, {"ok": True, "profile": profile})

        @staticmethod
        def _intelligence_request(data):
            return {
                "profile_id": str(data.get("profile_id") or "").strip(),
                "provider": str(data.get("provider") or "ollama").strip(),
                "model": str(data.get("model") or "").strip(),
                "api_url": str(data.get("api_url") or "").strip(),
                "redact": bool(data.get("redact", False)),
            }

        def _handle_api_intelligence_preview(self):
            from ..intelligence.runtime import get_runtime

            data = self._read_body()
            recording_dir = str(
                data.get("recording_dir") or data.get("source_path") or ""
            ).strip()
            if not recording_dir:
                self._json_response(400, {"ok": False, "err": "recording_dir is required"})
                return
            try:
                preview = get_runtime().preview(
                    recording_dir, **self._intelligence_request(data),
                )
            except Exception as error:
                self._json_response(400, {
                    "ok": False, "err": "intelligence_preview_failed",
                    "message": self._intelligence_error(error),
                })
                return
            self._json_response(200, {"ok": True, "preview": preview})

        def _handle_api_intelligence_summary(self):
            from ..intelligence.runtime import get_runtime, public_job

            data = self._read_body()
            recording_dir = str(
                data.get("recording_dir") or data.get("source_path") or ""
            ).strip()
            if not recording_dir:
                self._json_response(400, {"ok": False, "err": "recording_dir is required"})
                return
            try:
                request = self._intelligence_request(data)
                job = get_runtime().start_summary(
                    recording_dir, **request,
                    consent_token=str(data.get("consent_token") or "").strip(),
                    history_id=int(data.get("history_id") or 0),
                )
            except Exception as error:
                self._json_response(400, {
                    "ok": False, "err": "intelligence_summary_failed",
                    "message": self._intelligence_error(error),
                })
                return
            self._json_response(202, {"ok": True, "job": public_job(job)})

        def _handle_api_intelligence_thumbnail(self):
            from ..intelligence.runtime import get_runtime, public_job

            data = self._read_body()
            recording_dir = str(
                data.get("recording_dir") or data.get("source_path") or ""
            ).strip()
            if not recording_dir:
                self._json_response(400, {"ok": False, "err": "recording_dir is required"})
                return
            try:
                job = get_runtime().start_thumbnail(
                    recording_dir,
                    history_id=int(data.get("history_id") or 0),
                    title=str(data.get("title") or ""),
                    channel=str(data.get("channel") or ""),
                    date=str(data.get("date") or ""),
                )
            except Exception as error:
                self._json_response(400, {
                    "ok": False, "err": "intelligence_thumbnail_failed",
                    "message": self._intelligence_error(error),
                })
                return
            self._json_response(202, {"ok": True, "job": public_job(job)})

        def _handle_api_intelligence_cancel(self):
            from ..intelligence.runtime import get_runtime

            data = self._read_body()
            job_id = str(data.get("job_id") or data.get("id") or "").strip()
            if not job_id or not get_runtime().cancel(job_id):
                self._json_response(404, {"ok": False, "err": "intelligence_job_not_cancellable"})
                return
            from .. import db as _db
            from ..intelligence.runtime import public_job
            self._json_response(200, {
                "ok": True, "job": public_job(_db.load_intelligence_job(job_id)),
            })

        def _handle_api_intelligence_summary_edit(self):
            from ..intelligence.runtime import get_runtime

            data = self._read_body()
            job_id = str(data.get("job_id") or data.get("id") or "").strip()
            if not job_id or not isinstance(data.get("text"), str):
                self._json_response(400, {
                    "ok": False, "err": "job_id and string text are required",
                })
                return
            try:
                job = get_runtime().edit_summary(job_id, data["text"])
            except Exception as error:
                self._json_response(400, {
                    "ok": False, "err": "intelligence_summary_edit_failed",
                    "message": self._intelligence_error(error),
                })
                return
            self._json_response(200, {"ok": True, "job": job})

        def _handle_api_intelligence_summary_rebuild(self):
            from ..intelligence.runtime import get_runtime

            data = self._read_body()
            job_id = str(data.get("job_id") or data.get("id") or "").strip()
            if not job_id:
                self._json_response(400, {"ok": False, "err": "job_id is required"})
                return
            try:
                job = get_runtime().rebuild_summary(
                    job_id,
                    consent_token=str(data.get("consent_token") or "").strip(),
                )
            except Exception as error:
                self._json_response(400, {
                    "ok": False, "err": "intelligence_summary_rebuild_failed",
                    "message": self._intelligence_error(error),
                })
                return
            self._json_response(202, {"ok": True, "job": job})

        @staticmethod
        def _media_server_info(data):
            from types import SimpleNamespace

            raw = data.get("info", {})
            if raw is None:
                return None
            if not isinstance(raw, dict):
                raise ValueError("info must be an object")
            if not raw:
                return None
            allowed = {
                "platform", "channel", "title", "source_id", "start_time",
                "thumbnail_url", "total_secs", "duration_str", "url",
                "feed_url", "is_live", "chapters", "qualities",
            }
            return SimpleNamespace(**{
                key: raw[key] for key in allowed if key in raw
            })

        @staticmethod
        def _public_media_export(result):
            if not isinstance(result, dict):
                return {"ok": False, "error": "invalid export result"}
            payload = {
                key: value for key, value in result.items() if key != "plan"
            }
            payload["files"] = [
                {
                    key: item.get(key)
                    for key in ("kind", "path", "relative_path", "bytes")
                    if key in item
                }
                for item in result.get("files", [])
                if isinstance(item, dict)
            ]
            return payload

        def _handle_api_media_server_preview(self):
            from ..integrations.media_server import preview_media_import

            data = self._read_body()
            config = data.get("config", {})
            if not isinstance(config, dict):
                self._json_response(400, {"ok": False, "err": "config must be an object"})
                return
            try:
                result = preview_media_import(
                    config, str(data.get("out_dir") or data.get("source_dir") or ""),
                    self._media_server_info(data),
                )
            except Exception as error:
                self._json_response(400, {
                    "ok": False, "err": "media_server_preview_failed", "message": str(error),
                })
                return
            self._json_response(200, self._public_media_export(result))

        def _handle_api_media_server_export(self):
            from ..integrations import media_server

            data = self._read_body()
            config = data.get("config", {})
            if not isinstance(config, dict):
                self._json_response(400, {"ok": False, "err": "config must be an object"})
                return
            out_dir = str(data.get("out_dir") or data.get("source_dir") or "")
            profile_id = str(
                data.get("upload_profile_id")
                or config.get("upload_profile_id", "")
                or ""
            ).strip()
            try:
                info = self._media_server_info(data)
                if profile_id:
                    result = media_server.queue_media_server_export(
                        config, out_dir, info, profile_id,
                    )
                else:
                    result = media_server.materialize_media_import(
                        config, out_dir, info,
                    )
            except Exception as error:
                self._json_response(400, {
                    "ok": False, "err": "media_server_export_failed", "message": str(error),
                })
                return
            status = 202 if profile_id else 201
            self._json_response(status, self._public_media_export(result))

        def _handle_api_share_recording(self):
            from .. import db as _db

            data = self._read_body()
            try:
                history_id = int(data.get("history_id") or data.get("id") or 0)
            except (TypeError, ValueError):
                history_id = 0
            if history_id <= 0:
                self._json_response(400, {
                    "ok": False, "err": "invalid history_id",
                })
                return
            row = _db.publish_recording(history_id)
            media_path = self._published_media(row)
            if row is None or not media_path:
                if row is not None:
                    _db.unpublish_recording(history_id=history_id)
                self._json_response(404, {
                    "ok": False,
                    "err": "recording_not_available",
                    "message": "The recording folder or media file is missing.",
                })
                return
            self._json_response(201, {
                "ok": True,
                "recording": self._share_public_view(row, media_path),
            })

        def _handle_api_share_recording_revoke(self):
            from .. import db as _db

            data = self._read_body()
            share_id = str(data.get("share_id") or "").strip()
            try:
                history_id = int(data.get("history_id") or data.get("id") or 0)
            except (TypeError, ValueError):
                history_id = 0
            try:
                revoked = _db.unpublish_recording(
                    share_id=share_id, history_id=history_id,
                )
            except ValueError:
                revoked = False
            if not revoked:
                self._json_response(404, {"ok": False, "err": "share_not_found"})
                return
            self._json_response(200, {"ok": True, "revoked": True})

        def _handle_api_share_feed(self):
            from .. import db as _db

            data = self._read_body()
            if "channel" not in data:
                self._json_response(400, {
                    "ok": False, "err": "channel is required",
                })
                return
            try:
                feed = _db.publish_feed(
                    channel=data.get("channel", ""),
                    title=data.get("title", ""),
                )
            except ValueError as error:
                self._json_response(400, {"ok": False, "err": str(error)})
                return
            feed["feed_url"] = (
                f"{self._publishing_base_url()}/feed/{feed['feed_id']}.xml"
            )
            self._json_response(201, {"ok": True, "feed": feed})

        def _handle_api_share_feed_revoke(self):
            from .. import db as _db

            data = self._read_body()
            feed_id = str(data.get("feed_id") or data.get("id") or "").strip()
            if not _db.unpublish_feed(feed_id):
                self._json_response(404, {"ok": False, "err": "feed_not_found"})
                return
            self._json_response(200, {"ok": True, "revoked": True})

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

        def _handle_api_validate(self):
            from ..preflight import (
                PreflightError,
                ProbeBusyError,
                validate_probe_request,
            )

            try:
                data = validate_probe_request(self._read_body())
            except PreflightError as error:
                self._json_response(
                    400, {"ok": False, "err": "invalid probe", "message": str(error)}
                )
                return
            url = data["url"]
            if self._ssrf_reject(url):
                return
            handoff = _normalize_handoff_payload(data)
            if handoff is None:
                self._json_response(400, {
                    "ok": False,
                    "err": "invalid handoff context",
                })
                return
            request_headers, source_context = handoff
            if "request_headers" in data:
                data["request_headers"] = request_headers
            else:
                data.pop("request_headers", None)
            if source_context:
                data["source_context"] = source_context
            else:
                data.pop("source_context", None)
            if not probe_submitter:
                self._json_response(
                    503,
                    {"ok": False, "err": "validation unavailable"},
                )
                return
            try:
                response = probe_submitter(data)
            except ProbeBusyError as error:
                self._json_response(
                    429,
                    {
                        "ok": False,
                        "err": "probe_busy",
                        "message": str(error),
                    },
                    headers={
                        "Retry-After": str(error.retry_after_seconds),
                    },
                )
                return
            except PreflightError as error:
                self._json_response(
                    400, {"ok": False, "err": "probe_failed", "message": str(error)}
                )
                return
            except Exception as error:
                self._json_response(
                    500, {"ok": False, "err": "probe_failed", "message": str(error)}
                )
                return
            if not isinstance(response, dict):
                self._json_response(
                    500, {"ok": False, "err": "probe_failed"}
                )
                return
            self._json_response(200, {"ok": True, **response})

        def _handle_api_queue(self):
            from ..preflight import PreflightError, validate_queue_payload

            try:
                data = validate_queue_payload(self._read_body())
            except PreflightError as error:
                self._json_response(
                    400, {"ok": False, "err": str(error)}
                )
                return
            url = data["url"]
            if self._ssrf_reject(url):
                return
            handoff = _normalize_handoff_payload(data)
            if handoff is None:
                self._json_response(400, {
                    "ok": False,
                    "err": "invalid handoff context",
                })
                return
            request_headers, source_context = handoff
            if "request_headers" in data:
                data["request_headers"] = request_headers
            else:
                data.pop("request_headers", None)
            if source_context:
                data["source_context"] = source_context
            else:
                data.pop("source_context", None)
            if not queue_submitter:
                has_selection = any(
                    data.get(key)
                    for key in (
                        "validation_id",
                        "media_item_id",
                        "media_item_ids",
                        "background_audio_id",
                    )
                )
                if request_headers or source_context or has_selection:
                    signals.handoff_received.emit(
                        {**data, "url": url}, "queue"
                    )
                else:
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
                    from .. import db as _db
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

        def _handle_api_failure_cancel_retry(self):
            job_id = self._read_failure_id()
            if not job_id:
                return
            try:
                if failure_retry_canceller:
                    found = failure_retry_canceller(job_id)
                else:
                    from .. import db as _db
                    found = _db.cancel_failed_job_retry(job_id)
            except Exception as e:
                self._json_response(500, {"ok": False, "err": str(e)})
                return
            if not found:
                self._json_response(
                    404, {"ok": False, "err": "failure not found"}
                )
                return
            self._json_response(200, {
                "ok": True,
                "failure_id": job_id,
                "status": "intervention",
            })

        def _handle_api_failure_discard(self):
            job_id = self._read_failure_id()
            if not job_id:
                return
            try:
                if failure_discarder:
                    found = failure_discarder(job_id)
                else:
                    from .. import db as _db
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

        def _serve_web_ui(self, *, explicit_language=""):
            language = _select_web_language(
                explicit_language,
                self.headers.get("Accept-Language", ""),
            )
            body = _render_web_ui(language).encode("utf-8")
            self.send_response(200)
            self._security_headers()
            self._csp_header()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Language", language)
            self.send_header("Vary", "Accept-Language")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


# ── Bundled single-page web remote UI (F37) ─────────────────────────












