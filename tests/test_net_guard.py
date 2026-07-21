"""SSRF address-policy tests for the shared net_guard module (V30)."""

import ipaddress
from unittest import mock

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
