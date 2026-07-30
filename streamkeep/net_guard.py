"""Shared SSRF address policy.

Factored out of ``scrape.py`` so both the headless page-scraper and the
network-exposed REST/companion server enforce the same rule: never let a
user-supplied URL reach loopback, link-local, cloud-metadata, or (unless
explicitly allowed) private-LAN addresses. DNS is resolved and *every*
returned address is checked, so a public hostname that resolves to an
internal IP is still rejected.
"""

import ipaddress
import select
import socket
import socketserver
import threading
import urllib.parse
from dataclasses import dataclass

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_URL_LENGTH = 8192
_MAX_PROXY_HEADER_BYTES = 64 * 1024

# RFC1918 / ULA private ranges — blocked unless the caller opts in.
LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
))

# Cloud instance-metadata endpoints (AWS/GCP/Azure, Alibaba, AWS IMDSv6).
METADATA_ADDRESSES = frozenset({
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
})


class RemoteURLPolicyError(ValueError):
    """Raised when a remote URL cannot cross the media-network boundary."""


@dataclass(frozen=True)
class ValidatedRemoteURL:
    """Normalized remote URL plus the complete, policy-checked DNS result."""

    url: str
    host: str
    port: int
    addresses: tuple

    @property
    def authority(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        default_port = 443 if self.url.startswith("https://") else 80
        return host if self.port == default_port else f"{host}:{self.port}"


def address_allowed(address, allow_private_network=False):
    """Return whether a resolved IP address may be contacted."""
    if getattr(address, "ipv4_mapped", None) is not None:
        return False
    if address in METADATA_ADDRESSES:
        return False
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        return False
    if address.is_global:
        return True
    return bool(
        allow_private_network
        and any(address in network for network in LAN_NETWORKS)
    )


def resolve_host_addresses(host, port):
    """Resolve *host* to a tuple of ``ip_address`` objects (literal or DNS)."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        rows = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
        )
        values = {row[4][0].split("%", 1)[0] for row in rows}
        return tuple(sorted((ipaddress.ip_address(value) for value in values), key=str))
    return (literal,)


def validate_remote_url(url, *, base_url="", allow_private_network=False):
    """Normalize and resolve one HTTP(S) URL or raise on any unsafe target.

    Every DNS answer is checked, not just the address selected for a
    connection.  Callers that open a socket must connect to one of the
    returned numeric addresses so the validation cannot be bypassed by a
    second DNS lookup.
    """
    text = str(url or "").strip()
    if base_url:
        text = urllib.parse.urljoin(str(base_url), text)
    if not text or len(text) > _MAX_URL_LENGTH:
        raise RemoteURLPolicyError("URL is empty or exceeds the size limit")
    if "\\" in text or any(
        character.isspace() or ord(character) == 0x7F for character in text
    ):
        raise RemoteURLPolicyError("URL contains unsafe whitespace or controls")
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        raise RemoteURLPolicyError("URL is malformed") from None
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise RemoteURLPolicyError("Only HTTP(S) URLs are allowed")
    if not parsed.hostname:
        raise RemoteURLPolicyError("URL host is missing")
    if parsed.username is not None or parsed.password is not None:
        raise RemoteURLPolicyError("URL must not contain credentials")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise RemoteURLPolicyError("URL port is malformed") from None
    raw_host = parsed.hostname.rstrip(".")
    if "%" in raw_host:
        raise RemoteURLPolicyError("Scoped IP addresses are not allowed")
    try:
        host = str(ipaddress.ip_address(raw_host))
    except ValueError:
        try:
            host = raw_host.encode("idna").decode("ascii").lower()
        except (UnicodeError, ValueError):
            raise RemoteURLPolicyError("URL host is malformed") from None
    if not host:
        raise RemoteURLPolicyError("URL host is empty")
    authority_host = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    authority = (
        authority_host if port == default_port else f"{authority_host}:{port}"
    )
    normalized = urllib.parse.urlunsplit((
        scheme,
        authority,
        parsed.path or "/",
        parsed.query,
        "",
    ))
    try:
        addresses = resolve_host_addresses(host, port)
    except (OSError, ValueError):
        raise RemoteURLPolicyError(f"DNS resolution failed for {host}") from None
    if not addresses:
        raise RemoteURLPolicyError(f"DNS returned no addresses for {host}")
    for address in addresses:
        if not address_allowed(address, allow_private_network):
            raise RemoteURLPolicyError(
                f"Address class is not allowed for {host}"
            )
    return ValidatedRemoteURL(normalized, host, port, tuple(addresses))


def url_target_allowed(url, *, allow_private_network=False):
    """Validate a user-supplied URL against the SSRF policy.

    Returns ``(True, "")`` when every resolved address is permitted, else
    ``(False, reason)``. DNS failures are treated as blocked so a name that
    cannot be resolved never silently bypasses the check.
    """
    try:
        validate_remote_url(
            url, allow_private_network=allow_private_network,
        )
    except RemoteURLPolicyError as error:
        return False, str(error)
    return True, ""


def _connect_validated_target(target, timeout):
    """Connect to a numeric address from a previously validated DNS set."""
    last_error = None
    for address in target.addresses:
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        endpoint = (
            (str(address), target.port, 0, 0)
            if address.version == 6
            else (str(address), target.port)
        )
        try:
            sock.connect(endpoint)
            return sock
        except OSError as error:
            last_error = error
            sock.close()
    raise OSError(f"Could not connect to validated target: {last_error}")


def _parse_proxy_headers(header_bytes):
    try:
        text = header_bytes.decode("iso-8859-1")
    except UnicodeDecodeError:
        raise RemoteURLPolicyError("Proxy request headers are malformed") from None
    lines = text.split("\r\n")
    if not lines or len(lines[0].split(" ")) != 3:
        raise RemoteURLPolicyError("Proxy request line is malformed")
    method, target, version = lines[0].split(" ")
    if not version.startswith("HTTP/"):
        raise RemoteURLPolicyError("Proxy request protocol is malformed")
    headers = []
    for line in lines[1:]:
        if not line:
            continue
        if line[:1].isspace() or ":" not in line:
            raise RemoteURLPolicyError("Proxy request header is malformed")
        name, value = line.split(":", 1)
        if not name or any(character.isspace() for character in name):
            raise RemoteURLPolicyError("Proxy request header name is malformed")
        headers.append((name, value.strip()))
    return method.upper(), target, version, headers


def _authority_url(authority, scheme):
    raw = str(authority or "")
    if not raw or any(character in raw for character in "/?#@"):
        raise RemoteURLPolicyError("Proxy target authority is malformed")
    return f"{scheme}://{raw}/"


def _proxy_request_url(target, headers):
    if target.lower().startswith(("http://", "https://")):
        return target
    if not target.startswith("/"):
        raise RemoteURLPolicyError("Proxy request target is malformed")
    hosts = [value for name, value in headers if name.lower() == "host"]
    if len(hosts) != 1:
        raise RemoteURLPolicyError("Proxy request requires one Host header")
    return urllib.parse.urljoin(_authority_url(hosts[0], "http"), target)


def _tunnel(left, right, initial=b""):
    if initial:
        right.sendall(initial)
    sockets = [left, right]
    while sockets:
        readable, _, exceptional = select.select(sockets, [], sockets, 1.0)
        if exceptional:
            break
        if not readable:
            continue
        for source in readable:
            destination = right if source is left else left
            try:
                chunk = source.recv(64 * 1024)
            except OSError:
                chunk = b""
            if not chunk:
                return
            destination.sendall(chunk)


class _GuardedProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, *, allow_private_network, connect_timeout):
        self.allow_private_network = bool(allow_private_network)
        self.connect_timeout = max(1.0, float(connect_timeout))
        self.connection_count = 0
        self._metrics_lock = threading.Lock()
        super().__init__(address, _GuardedProxyHandler)

    def record_connection(self):
        with self._metrics_lock:
            self.connection_count += 1


class _GuardedProxyHandler(socketserver.BaseRequestHandler):
    def _response(self, status, message):
        body = (str(message) + "\n").encode("utf-8", errors="replace")
        response = (
            f"HTTP/1.1 {status}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body
        try:
            self.request.sendall(response)
        except OSError:
            pass

    def _read_request(self):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.request.recv(8192)
            if not chunk:
                raise RemoteURLPolicyError("Proxy request ended before headers")
            data += chunk
            if len(data) > _MAX_PROXY_HEADER_BYTES:
                raise RemoteURLPolicyError("Proxy request headers are too large")
        return data.split(b"\r\n\r\n", 1)

    def handle(self):
        self.server.record_connection()
        self.request.settimeout(self.server.connect_timeout)
        remote = None
        try:
            header_bytes, remainder = self._read_request()
            method, raw_target, version, headers = _parse_proxy_headers(
                header_bytes
            )
            if method == "CONNECT":
                target = validate_remote_url(
                    _authority_url(raw_target, "https"),
                    allow_private_network=self.server.allow_private_network,
                )
                remote = _connect_validated_target(
                    target, self.server.connect_timeout
                )
                self.request.sendall(
                    b"HTTP/1.1 200 Connection Established\r\n\r\n"
                )
                _tunnel(self.request, remote, remainder)
                return

            if method not in {"GET", "HEAD", "POST"}:
                raise RemoteURLPolicyError(
                    f"Proxy method {method or '<empty>'} is not allowed"
                )
            target = validate_remote_url(
                _proxy_request_url(raw_target, headers),
                allow_private_network=self.server.allow_private_network,
            )
            if not target.url.startswith("http://"):
                raise RemoteURLPolicyError(
                    "HTTPS proxy requests must use CONNECT"
                )
            remote = _connect_validated_target(
                target, self.server.connect_timeout
            )
            parsed = urllib.parse.urlsplit(target.url)
            request_target = parsed.path or "/"
            if parsed.query:
                request_target += f"?{parsed.query}"
            outgoing_headers = []
            blocked_headers = {
                "connection", "host", "proxy-authorization",
                "proxy-connection", "keep-alive",
            }
            for name, value in headers:
                if name.lower() not in blocked_headers:
                    outgoing_headers.append(f"{name}: {value}")
            outgoing_headers.extend([
                f"Host: {target.authority}",
                "Connection: close",
            ])
            outbound = (
                f"{method} {request_target} {version}\r\n"
                + "\r\n".join(outgoing_headers)
                + "\r\n\r\n"
            ).encode("iso-8859-1")
            remote.sendall(outbound)
            _tunnel(self.request, remote, remainder)
        except RemoteURLPolicyError as error:
            self._response("403 Forbidden", f"StreamKeep policy: {error}")
        except (OSError, ValueError):
            self._response("502 Bad Gateway", "StreamKeep guarded transport failed")
        finally:
            if remote is not None:
                try:
                    remote.close()
                except OSError:
                    pass


class GuardedHTTPProxy:
    """Short-lived loopback proxy that validates and pins every connection."""

    def __init__(self, *, allow_private_network=False, connect_timeout=10):
        self.allow_private_network = bool(allow_private_network)
        self.connect_timeout = connect_timeout
        self._server = None
        self._thread = None

    @property
    def url(self):
        if self._server is None:
            return ""
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def connection_count(self):
        return self._server.connection_count if self._server is not None else 0

    def start(self):
        if self._server is not None:
            return self.url
        self._server = _GuardedProxyServer(
            ("127.0.0.1", 0),
            allow_private_network=self.allow_private_network,
            connect_timeout=self.connect_timeout,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="streamkeep-guarded-media-proxy",
            daemon=True,
        )
        self._thread.start()
        return self.url

    def stop(self):
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.stop()
