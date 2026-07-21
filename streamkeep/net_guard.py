"""Shared SSRF address policy.

Factored out of ``scrape.py`` so both the headless page-scraper and the
network-exposed REST/companion server enforce the same rule: never let a
user-supplied URL reach loopback, link-local, cloud-metadata, or (unless
explicitly allowed) private-LAN addresses. DNS is resolved and *every*
returned address is checked, so a public hostname that resolves to an
internal IP is still rejected.
"""

import ipaddress
import socket
import urllib.parse

_ALLOWED_SCHEMES = frozenset({"http", "https"})

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


def url_target_allowed(url, *, allow_private_network=False):
    """Validate a user-supplied URL against the SSRF policy.

    Returns ``(True, "")`` when every resolved address is permitted, else
    ``(False, reason)``. DNS failures are treated as blocked so a name that
    cannot be resolved never silently bypasses the check.
    """
    text = str(url or "").strip()
    if not text or len(text) > 8192:
        return False, "URL is empty or exceeds the size limit"
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return False, "URL is malformed"
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        return False, "Only HTTP(S) URLs are allowed"
    if not parsed.hostname:
        return False, "URL host is missing"
    if parsed.username is not None or parsed.password is not None:
        return False, "URL must not contain credentials"
    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return False, "URL host is malformed"
    if not host:
        return False, "URL host is empty"
    try:
        addresses = resolve_host_addresses(host, port)
    except (OSError, ValueError):
        return False, f"DNS resolution failed for {host}"
    if not addresses:
        return False, f"DNS returned no addresses for {host}"
    for address in addresses:
        if not address_allowed(address, allow_private_network):
            return False, f"Address class is not allowed for {host}"
    return True, ""
