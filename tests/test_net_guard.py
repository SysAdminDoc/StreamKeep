"""SSRF address-policy tests for the shared net_guard module (V30)."""

import http.client
import http.server
import ipaddress
import socket
import threading
import urllib.parse
from unittest import mock

import pytest

from streamkeep import net_guard


def _patch_resolve(monkeypatch, ip):
    monkeypatch.setattr(
        net_guard, "resolve_host_addresses",
        lambda host, port: (ipaddress.ip_address(ip),),
    )


def test_public_url_allowed(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")  # example.com
    ok, reason = net_guard.url_target_allowed("https://example.com/video.mp4")
    assert ok is True
    assert reason == ""


def test_loopback_blocked(monkeypatch):
    _patch_resolve(monkeypatch, "127.0.0.1")
    ok, reason = net_guard.url_target_allowed("http://localhost/secret")
    assert ok is False
    assert "not allowed" in reason


def test_cloud_metadata_blocked(monkeypatch):
    _patch_resolve(monkeypatch, "169.254.169.254")
    ok, _ = net_guard.url_target_allowed("http://169.254.169.254/latest/meta-data/")
    assert ok is False


def test_private_lan_blocked_by_default(monkeypatch):
    _patch_resolve(monkeypatch, "192.168.1.50")
    ok, _ = net_guard.url_target_allowed("http://192.168.1.50:8080/stream")
    assert ok is False


def test_private_lan_allowed_when_opted_in(monkeypatch):
    _patch_resolve(monkeypatch, "192.168.1.50")
    ok, _ = net_guard.url_target_allowed(
        "http://192.168.1.50:8080/stream", allow_private_network=True,
    )
    assert ok is True


def test_dns_rebinding_public_name_to_internal_ip_blocked(monkeypatch):
    # A public hostname that resolves to a private address must still fail.
    _patch_resolve(monkeypatch, "10.0.0.5")
    ok, _ = net_guard.url_target_allowed("https://sneaky.example.com/x")
    assert ok is False


def test_every_dns_answer_must_be_public(monkeypatch):
    monkeypatch.setattr(
        net_guard,
        "resolve_host_addresses",
        lambda _host, _port: (
            ipaddress.ip_address("93.184.216.34"),
            ipaddress.ip_address("127.0.0.1"),
        ),
    )
    with pytest.raises(net_guard.RemoteURLPolicyError):
        net_guard.validate_remote_url("https://cdn.example.com/video.ts")


def test_remote_url_normalization_keeps_public_cdn_path(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    target = net_guard.validate_remote_url(
        "../segments/one.ts?token=abc#fragment",
        base_url="HTTPS://CDN.Example.COM/live/master.m3u8",
    )
    assert target.url == "https://cdn.example.com/segments/one.ts?token=abc"
    assert target.authority == "cdn.example.com"


def test_ipv4_mapped_and_alternate_loopback_forms_fail_closed():
    assert net_guard.address_allowed(
        ipaddress.ip_address("::ffff:127.0.0.1")
    ) is False
    ok, _ = net_guard.url_target_allowed("http://2130706433/secret")
    assert ok is False


def test_guarded_proxy_rejects_loopback_before_contact():
    hits = []

    class LocalHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            return

    local = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
    local_thread = threading.Thread(target=local.serve_forever, daemon=True)
    local_thread.start()
    try:
        with net_guard.GuardedHTTPProxy() as proxy:
            proxy_port = urllib.parse.urlsplit(proxy.url).port
            connection = http.client.HTTPConnection(
                "127.0.0.1", proxy_port, timeout=5
            )
            target = f"http://127.0.0.1:{local.server_address[1]}/sentinel"
            connection.putrequest("GET", target, skip_host=True)
            connection.putheader(
                "Host", f"127.0.0.1:{local.server_address[1]}"
            )
            connection.endheaders()
            response = connection.getresponse()
            response.read()
            connection.close()
    finally:
        local.shutdown()
        local.server_close()
        local_thread.join(timeout=2)

    assert response.status == 403
    assert hits == []


def test_guarded_proxy_rejects_private_https_connect():
    with net_guard.GuardedHTTPProxy() as proxy:
        parsed = urllib.parse.urlsplit(proxy.url)
        with socket.create_connection(
            (parsed.hostname, parsed.port), timeout=5
        ) as client:
            client.sendall(
                b"CONNECT 169.254.169.254:443 HTTP/1.1\r\n"
                b"Host: 169.254.169.254:443\r\n\r\n"
            )
            response = client.recv(4096)
    assert response.startswith(b"HTTP/1.1 403 Forbidden")


def test_non_http_scheme_and_credentials_blocked():
    ok, _ = net_guard.url_target_allowed("ftp://example.com/x")
    assert ok is False
    ok, reason = net_guard.url_target_allowed("http://user:pass@example.com/x")
    assert ok is False
    assert "credential" in reason.lower()


def test_dns_failure_is_blocked(monkeypatch):
    monkeypatch.setattr(
        net_guard, "resolve_host_addresses",
        mock.Mock(side_effect=OSError("no such host")),
    )
    ok, reason = net_guard.url_target_allowed("https://does-not-resolve.invalid/x")
    assert ok is False
    assert "DNS" in reason


def test_config_key_import_validated():
    from streamkeep.config import _BOOL_CONFIG_KEYS

    assert "companion_allow_private_network" in _BOOL_CONFIG_KEYS
