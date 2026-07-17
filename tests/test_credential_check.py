import json
from unittest import mock

from streamkeep import credential_check as cc


def _mock_fetch(status, body=""):
    return mock.patch.object(cc, "_fetch", return_value=(status, body))


# ── Twitch ──────────────────────────────────────────────────────────

def test_twitch_valid_parses_redacted_metadata():
    body = json.dumps({
        "client_id": "abc", "login": "user",
        "scopes": ["channel:read", "user:read"], "expires_in": 500000,
    })
    with _mock_fetch(200, body):
        r = cc.probe_twitch("secret-token")
    assert r.status == cc.VALID and r.ok
    assert r.metadata["scope_count"] == 2
    assert r.metadata["has_client_id"] is True
    # The token must never appear anywhere in the redacted result.
    assert "secret-token" not in json.dumps(r.as_dict())


def test_twitch_valid_flags_imminent_expiry():
    with _mock_fetch(200, json.dumps({"scopes": [], "expires_in": 120})):
        r = cc.probe_twitch("t")
    assert r.status == cc.VALID
    assert "expires in 120s" in r.detail


def test_twitch_expired_on_401():
    with _mock_fetch(401):
        assert cc.probe_twitch("t").status == cc.EXPIRED


def test_twitch_rate_limited_on_429():
    with _mock_fetch(429):
        assert cc.probe_twitch("t").status == cc.RATE_LIMITED


def test_twitch_network_error_on_transport_failure():
    with _mock_fetch(-1):
        assert cc.probe_twitch("t").status == cc.NETWORK_ERROR


def test_twitch_cancel_short_circuits():
    with mock.patch.object(cc, "_fetch") as fetch:
        r = cc.probe_twitch("t", cancel_check=lambda: True)
    assert r.status == cc.CANCELLED
    fetch.assert_not_called()


# ── YouTube ─────────────────────────────────────────────────────────

def test_youtube_valid():
    with _mock_fetch(200, json.dumps({"items": []})):
        assert cc.probe_youtube("key").status == cc.VALID


def test_youtube_invalid_key():
    body = json.dumps({"error": {"errors": [{"reason": "keyInvalid"}]}})
    with _mock_fetch(400, body):
        r = cc.probe_youtube("badkey")
    assert r.status == cc.INVALID
    assert "badkey" not in json.dumps(r.as_dict())


def test_youtube_quota_exceeded_is_rate_limited():
    body = json.dumps({"error": {"errors": [{"reason": "quotaExceeded"}]}})
    with _mock_fetch(403, body):
        assert cc.probe_youtube("k").status == cc.RATE_LIMITED


def test_youtube_api_not_enabled_is_insufficient_scope():
    body = json.dumps({"error": {"errors": [{"reason": "accessNotConfigured"}]}})
    with _mock_fetch(403, body):
        assert cc.probe_youtube("k").status == cc.INSUFFICIENT_SCOPE


def test_youtube_network_error():
    with _mock_fetch(-1):
        assert cc.probe_youtube("k").status == cc.NETWORK_ERROR


# ── Kick ────────────────────────────────────────────────────────────

def test_kick_is_unsupported():
    assert cc.probe_kick("t").status == cc.UNSUPPORTED


# ── Cookies (local) ─────────────────────────────────────────────────

def _write_cookies(tmp_path, rows):
    p = tmp_path / "cookies.txt"
    p.write_text("# Netscape HTTP Cookie File\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return str(p)


def test_cookies_valid_counts_live(tmp_path):
    future = 9999999999
    rows = [
        f".twitch.tv\tTRUE\t/\tTRUE\t{future}\tauth-token\tabc",
        f".youtube.com\tTRUE\t/\tTRUE\t0\tSESSION\txyz",  # session cookie
    ]
    r = cc.probe_cookies(path=_write_cookies(tmp_path, rows))
    assert r.status == cc.VALID
    assert r.metadata["total"] == 2 and r.metadata["live"] == 2
    assert "twitch.tv" in r.metadata["domains"]


def test_cookies_all_expired(tmp_path):
    rows = [".twitch.tv\tTRUE\t/\tTRUE\t100\tauth\tabc"]
    r = cc.probe_cookies(path=_write_cookies(tmp_path, rows))
    assert r.status == cc.EXPIRED


def test_cookies_httponly_rows_are_counted(tmp_path):
    future = 9999999999
    rows = [f"#HttpOnly_.twitch.tv\tTRUE\t/\tTRUE\t{future}\tauth\tabc"]
    r = cc.probe_cookies(path=_write_cookies(tmp_path, rows))
    assert r.status == cc.VALID and r.metadata["total"] == 1


def test_cookies_no_file_is_no_credential():
    assert cc.probe_cookies(path="").status == cc.NO_CREDENTIAL


def test_cookies_malformed_is_invalid(tmp_path):
    p = tmp_path / "cookies.txt"
    p.write_text("not a cookie file\njust text\n", encoding="utf-8")
    assert cc.probe_cookies(path=str(p)).status == cc.INVALID


# ── Dispatch ────────────────────────────────────────────────────────

def test_probe_platform_no_credential():
    with mock.patch.object(cc.accounts, "get_credential", return_value=""):
        assert cc.probe_platform("twitch").status == cc.NO_CREDENTIAL


def test_probe_platform_dispatches_with_stored_credential():
    with mock.patch.object(cc.accounts, "get_credential", return_value="tok"), \
         _mock_fetch(200, json.dumps({"scopes": []})):
        assert cc.probe_platform("twitch").status == cc.VALID


def test_probe_all_covers_every_surface():
    with mock.patch.object(cc.accounts, "get_credential", return_value=""):
        results = cc.probe_all()
    platforms = {r.platform for r in results}
    assert platforms == {"twitch", "youtube", "kick", "cookies"}


# ── UI glue ─────────────────────────────────────────────────────────

def test_probe_worker_emits_per_platform_results(qt_application):
    from streamkeep.ui.tabs.settings_preferences import _CredentialProbeWorker

    def fake_probe(platform, timeout=15, cancel_check=None):
        return cc.ProbeResult(platform, cc.VALID, "ok")

    got = []
    worker = _CredentialProbeWorker(["twitch", "youtube"])
    worker.result_ready.connect(got.append)
    with mock.patch("streamkeep.credential_check.probe_platform", fake_probe):
        worker.run()  # synchronous — exercises the emit glue without a thread
    assert [r.platform for r in got] == ["twitch", "youtube"]
    assert all(r.status == cc.VALID for r in got)


def test_probe_worker_cancel_stops_emitting(qt_application):
    from streamkeep.ui.tabs.settings_preferences import _CredentialProbeWorker

    worker = _CredentialProbeWorker(["twitch", "youtube", "kick"])
    worker.cancel()
    got = []
    worker.result_ready.connect(got.append)
    with mock.patch("streamkeep.credential_check.probe_platform") as probe:
        worker.run()
    assert got == []
    probe.assert_not_called()


def test_cookie_check_handler_updates_status_label():
    from streamkeep.ui.tabs.settings_preferences import SettingsPreferencesMixin

    class _Label:
        def __init__(self):
            self.text = ""

        def setText(self, value):
            self.text = value

    class _Win:
        def __init__(self):
            self.cookies_status_label = _Label()
            self.statuses = []

        def _set_status(self, msg, tone="info"):
            self.statuses.append((msg, tone))

    win = _Win()
    result = cc.ProbeResult("cookies", cc.EXPIRED, "All 3 cookies have expired")
    with mock.patch("streamkeep.credential_check.probe_cookies", return_value=result):
        SettingsPreferencesMixin._on_check_cookies(win)
    assert "expired" in win.cookies_status_label.text.lower()
    assert win.statuses[-1][1] == result.tone
