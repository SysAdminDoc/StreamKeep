"""Local HTTP server — browser-companion extension + REST API + Web Remote UI.

Binds to 127.0.0.1 (or 0.0.0.0 if LAN access is enabled in Settings)
on a random port. Every request requires a bearer token — 32-byte hex,
regenerated every app launch, displayed in the Settings tab.

Tokens carry scopes: ``status`` (read-only state), ``queue`` (send URLs),
``recovery`` (retry/discard failed jobs).  The master token created at
launch has all scopes.  ``rotate_token()`` replaces it atomically;
``create_scoped_token()`` mints a restricted token.

REST API endpoints (F37):
  GET  /api/status    — active downloads, queue, live channels  [status]
  POST /api/queue     — add a URL to the download queue         [queue]
  GET  /api/library   — search/list recorded VODs               [status]
  GET  /api/monitor   — channel monitor statuses                [status]
  POST /api/failures/retry    — retry a persisted failed job    [recovery]
  POST /api/failures/discard  — discard a persisted failed job  [recovery]
  GET  /               — serves the single-page web remote UI

The server runs on its own thread (stdlib http.server is threaded), and
hands received URLs to the main-thread Qt via a pyqtSignal.
"""

import ipaddress
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

from PyQt6.QtCore import QObject, pyqtSignal

SCOPE_STATUS = "status"
SCOPE_QUEUE = "queue"
SCOPE_RECOVERY = "recovery"
ALL_SCOPES = frozenset({SCOPE_STATUS, SCOPE_QUEUE, SCOPE_RECOVERY})


class TokenStore:
    """Thread-safe token → scopes mapping."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens = {}  # token_str -> frozenset of scopes

    def add(self, token, scopes):
        with self._lock:
            self._tokens[token] = frozenset(scopes)

    def remove(self, token):
        with self._lock:
            self._tokens.pop(token, None)

    def revoke_all(self):
        with self._lock:
            self._tokens.clear()

    def check(self, token):
        with self._lock:
            return self._tokens.get(token)

    def __len__(self):
        with self._lock:
            return len(self._tokens)


class _ServerSignals(QObject):
    url_received = pyqtSignal(str, str)   # url, action ("fetch" | "queue")
    clip_received = pyqtSignal(str, float, float)  # url, start_secs, end_secs
    failed_job_retry_requested = pyqtSignal(int)
    failed_job_discard_requested = pyqtSignal(int)


class _CompanionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


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
        return host


def _normalize_host_header(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("["):
        end = raw.find("]")
        return _canonical_host(raw[1:end] if end > 0 else raw)
    if raw.count(":") == 1:
        raw = raw.rsplit(":", 1)[0]
    return _canonical_host(raw)


def _usable_interface_addr(addr):
    try:
        ip = ipaddress.ip_address(str(addr).split("%", 1)[0])
    except ValueError:
        return False
    return not (ip.is_unspecified or ip.is_multicast)


def _discover_lan_hosts():
    hosts = set()
    names = {socket.gethostname(), socket.getfqdn()}
    for name in names:
        norm_name = _canonical_host(name)
        if norm_name and norm_name not in _LOCAL_HOSTS:
            hosts.add(norm_name)
        try:
            for info in socket.getaddrinfo(name, None):
                addr = info[4][0]
                if _usable_interface_addr(addr):
                    hosts.add(_canonical_host(addr))
        except OSError:
            pass

    probes = (
        (socket.AF_INET, ("8.8.8.8", 80)),
        (socket.AF_INET6, ("2001:4860:4860::8888", 80)),
    )
    for family, target in probes:
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.connect(target)
                addr = sock.getsockname()[0]
            if _usable_interface_addr(addr):
                hosts.add(_canonical_host(addr))
        except OSError:
            pass

    return hosts


def _build_allowed_hosts(bind_lan=False, extra_hosts=None):
    hosts = {_canonical_host(host) for host in _LOCAL_HOSTS}
    if bind_lan:
        hosts.update(_discover_lan_hosts())
    for host in extra_hosts or ():
        norm = _normalize_host_header(host)
        if norm:
            hosts.add(norm)
    return frozenset(hosts)


def _preferred_display_host(allowed_hosts):
    for host in sorted(allowed_hosts):
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue
        if ip.version == 4 and not ip.is_loopback:
            return host
    for host in sorted(allowed_hosts):
        if host and host not in _LOCAL_HOSTS:
            return host
    return "127.0.0.1"


def _format_url_host(host):
    host = _canonical_host(host) or "127.0.0.1"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


class LocalCompanionServer:
    """Wraps a ThreadingHTTPServer bound to 127.0.0.1 (or 0.0.0.0) on a
    random port.

    Usage:
        server = LocalCompanionServer()
        server.state_provider = lambda: {...}  # F37 API state callback
        server.url_received.connect(main_window._on_companion_url)
        server.start()

    Token / port are accessible via `server.token` / `server.port`.
    """

    def __init__(self, *, bind_lan=False, allowed_hosts=None, port=0):
        self._token_store = TokenStore()
        self.token = secrets.token_hex(16)
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
        self._bind_lan = bool(bind_lan)
        self._bind_addr = "0.0.0.0" if self._bind_lan else "127.0.0.1"
        self._allowed_hosts = _build_allowed_hosts(self._bind_lan, allowed_hosts)
        self.allowed_hosts = tuple(sorted(host for host in self._allowed_hosts if host))
        self.display_host = (
            _preferred_display_host(self._allowed_hosts)
            if self._bind_lan else "127.0.0.1"
        )

    def rotate_token(self):
        """Replace the master token. Old tokens stop working immediately."""
        old = self.token
        self._token_store.remove(old)
        self.token = secrets.token_hex(16)
        self._token_store.add(self.token, ALL_SCOPES)
        return self.token

    def create_scoped_token(self, scopes):
        """Mint a token restricted to the given scopes."""
        valid = frozenset(scopes) & ALL_SCOPES
        if not valid:
            raise ValueError(f"No valid scopes in {scopes!r}")
        tok = secrets.token_hex(16)
        self._token_store.add(tok, valid)
        return tok

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
            allowed_hosts=self._allowed_hosts,
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


def _build_handler(token_store, signals, state_provider=None, *, allowed_hosts=None):
    allowed_hosts = frozenset(allowed_hosts or _build_allowed_hosts(False))

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args, **_kwargs):
            return

        def _host_ok(self):
            return _normalize_host_header(self.headers.get("Host")) in allowed_hosts

        def _origin_ok(self, origin):
            if not origin:
                return False
            if origin.startswith("chrome-extension://"):
                return True
            try:
                parsed = urlsplit(origin)
            except ValueError:
                return False
            if parsed.scheme not in ("http", "https"):
                return False
            return _normalize_host_header(parsed.netloc) in allowed_hosts

        def _reject_bad_host(self):
            if not self._host_ok():
                self.send_response(403)
                self.end_headers()
                return True
            return False

        def _token_scopes(self):
            """Return the scopes for the bearer token, or None."""
            hdr = self.headers.get("Authorization", "") or ""
            if not hdr.startswith("Bearer "):
                return None
            candidate = hdr[7:].strip()
            if not candidate:
                return None
            return token_store.check(candidate)

        def _require_auth(self, scope=None):
            """Check auth + optional scope. Returns True if authorized."""
            scopes = self._token_scopes()
            if scopes is None:
                self._json_response(401, {
                    "ok": False,
                    "err": "token_invalid",
                    "message": "Missing or expired bearer token. Re-pair with the current token from Settings.",
                })
                return False
            if scope and scope not in scopes:
                self._json_response(403, {
                    "ok": False,
                    "err": "scope_denied",
                    "message": f"This token does not have the '{scope}' scope.",
                })
                return False
            return True

        def _cors(self):
            origin = self.headers.get("Origin", "")
            allowed = origin if self._origin_ok(origin) else "http://localhost"
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type, Authorization"
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
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self._cors()
            self._security_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self, max_bytes=1_048_576):
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length <= 0 or length > max_bytes:
                    return {}
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
            except (ValueError, OSError):
                return {}

        def do_OPTIONS(self):
            if self._reject_bad_host():
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

            if not self._require_auth():
                return

            if path == "/ping":
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
            else:
                self.send_response(404)
                self._cors()
                self.end_headers()

        def do_POST(self):
            if self._reject_bad_host():
                return
            path = self.path.split("?")[0]
            if not self._require_auth():
                return

            if path == "/send_url":
                if self._require_auth(SCOPE_QUEUE):
                    self._handle_send_url()
            elif path == "/api/queue":
                if self._require_auth(SCOPE_QUEUE):
                    self._handle_api_queue()
            elif path == "/api/failures/retry":
                if self._require_auth(SCOPE_RECOVERY):
                    self._handle_api_failure_retry()
            elif path == "/api/failures/discard":
                if self._require_auth(SCOPE_RECOVERY):
                    self._handle_api_failure_discard()
            else:
                self.send_response(404)
                self._cors()
                self.end_headers()

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

        def _handle_api_queue(self):
            data = self._read_body()
            url = str(data.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                self._json_response(400, {"ok": False, "err": "invalid url"})
                return
            signals.url_received.emit(url, "queue")
            self._json_response(200, {"ok": True})

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
                from . import db as _db
                job = _db.mark_failed_job_retrying(job_id)
            except Exception as e:
                self._json_response(500, {"ok": False, "err": str(e)})
                return
            if not job:
                self._json_response(404, {"ok": False, "err": "failure not found"})
                return
            signals.failed_job_retry_requested.emit(job_id)
            self._json_response(200, {"ok": True, "failure": job})

        def _handle_api_failure_discard(self):
            job_id = self._read_failure_id()
            if not job_id:
                return
            try:
                from . import db as _db
                _db.mark_failed_job_discarded(job_id)
            except Exception as e:
                self._json_response(500, {"ok": False, "err": str(e)})
                return
            signals.failed_job_discard_requested.emit(job_id)
            self._json_response(200, {"ok": True})

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
<p style="color:#a6adc8;margin-bottom:12px">Enter the bearer token from Settings to connect.</p>
<div class="row"><input id="token-input" type="password" placeholder="Bearer token"></div>
<button onclick="doAuth()" style="width:100%;margin-top:8px">Connect</button>
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
function api(path,opts){
  opts=opts||{};opts.headers=opts.headers||{};
  opts.headers['Authorization']='Bearer '+TOKEN;
  return fetch(BASE+path,opts).then(r=>{
    if(r.status===401){clearInterval(_refreshId);
      document.getElementById('app').style.display='none';
      document.getElementById('auth').style.display='block';
      document.getElementById('auth-err').style.display='block';
      document.getElementById('auth-err').textContent=
        'Token expired or rotated. Re-enter the current token from Settings.';
      return {ok:false};}
    return r.json();});
}
function doAuth(){
  TOKEN=document.getElementById('token-input').value.trim();
  api('/ping').then(d=>{
    if(d.ok){document.getElementById('auth').style.display='none';
      document.getElementById('app').style.display='block';refresh();
      _refreshId=setInterval(refresh,5000);}
    else throw new Error(d.message||'');
  }).catch(e=>{
    document.getElementById('auth-err').style.display='block';
    var msg=e&&e.message&&e.message.includes('expired')?e.message:
      'Invalid token or server unreachable. Check the token in StreamKeep Settings.';
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
