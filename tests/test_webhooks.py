import ipaddress
from types import SimpleNamespace
from unittest import mock

import pytest

from streamkeep import net_guard
import streamkeep.ui.main_window as main_window


class _WebhookHarness(SimpleNamespace):
    _validate_webhook_url = main_window.StreamKeep._validate_webhook_url
    _send_webhook = main_window.StreamKeep._send_webhook
    _fire_webhook_json = main_window.StreamKeep._fire_webhook_json

    def __init__(self, url):
        super().__init__(
            _webhook_url=url,
            stream_info=None,
            logs=[],
        )

    def _log(self, message):
        self.logs.append(message)


def _public_dns(_host, _port):
    return (ipaddress.ip_address("93.184.216.34"),)


def test_ntfy_curl_uses_validated_url_after_option_separator(monkeypatch):
    monkeypatch.setattr(net_guard, "resolve_host_addresses", _public_dns)
    window = _WebhookHarness("https://ntfy.sh/streamkeep")

    with mock.patch.object(
        main_window, "subprocess"
    ) as subprocess, mock.patch(
        "streamkeep.capabilities.resolve_tool_command",
        return_value=r"C:\Tools\curl.exe",
    ):
        window._send_webhook("complete", "Finished")

    command = subprocess.Popen.call_args.args[0]
    assert command[-2:] == ["--", "https://ntfy.sh/streamkeep"]
    assert window.logs == []


def test_json_webhook_uses_validated_url_after_option_separator(monkeypatch):
    monkeypatch.setattr(net_guard, "resolve_host_addresses", _public_dns)
    window = _WebhookHarness("https://hooks.example/webhook")

    with mock.patch.object(
        main_window, "subprocess"
    ) as subprocess, mock.patch(
        "streamkeep.capabilities.resolve_tool_command",
        return_value=r"C:\Tools\curl.exe",
    ):
        window._fire_webhook_json("https://hooks.example/webhook", {})

    command = subprocess.Popen.call_args.args[0]
    assert command[-2:] == ["--", "https://hooks.example/webhook"]
    assert window.logs == []


@pytest.mark.parametrize(
    "blocked_ip",
    ("192.168.1.50", "127.0.0.1", "169.254.169.254", "fe80::1"),
)
def test_json_webhook_refuses_blocked_targets(monkeypatch, blocked_ip):
    monkeypatch.setattr(
        net_guard,
        "resolve_host_addresses",
        lambda _host, _port: (ipaddress.ip_address(blocked_ip),),
    )
    window = _WebhookHarness("https://hooks.example/webhook")

    with mock.patch.object(main_window.subprocess, "Popen") as popen:
        window._fire_webhook_json("https://hooks.example/webhook", {})

    popen.assert_not_called()
    assert len(window.logs) == 1
    assert "Refused unsafe URL" in window.logs[0]


def test_json_webhook_refuses_option_like_url_without_spawning():
    window = _WebhookHarness("-o")

    with mock.patch.object(main_window.subprocess, "Popen") as popen:
        window._fire_webhook_json("-o", {})

    popen.assert_not_called()
    assert len(window.logs) == 1
    assert "Refused unsafe URL" in window.logs[0]
