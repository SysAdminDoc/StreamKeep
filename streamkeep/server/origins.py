"""Host and origin validation for the companion server (V163).

The companion listens on loopback and is reached from a browser extension, so
"which Host header and which Origin are acceptable" is a security decision, not
a formatting one. Split out of ``_legacy`` so that decision is a module with its
own tests rather than a handful of helpers above a 1,900-line request handler.

Pure: no Qt, no sockets, no config reads. Nothing here imports a server sibling.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit


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

def _normalize_extension_origin(value):
    origin = _normalize_origin(value)
    if origin.startswith(("chrome-extension://", "moz-extension://")):
        return origin
    return ""

def _validate_external_origin(value):
    origin = _normalize_origin(value, allow_extensions=False)
    if not origin or not origin.startswith("https://"):
        raise ValueError("LAN remote access requires an explicit HTTPS reverse-proxy origin.")
    return origin
