"""Tests for FetchWorker's yt-dlp fallback and yt-dlp resilience helpers.

A platform-specific extractor that breaks (site markup/API changed) should not
turn into a hard failure while yt-dlp — which supports 1700+ sites — could
still resolve the URL. These tests pin that fallback plus the auth-error and
Cloudflare-detection guards.
"""

import streamkeep.extractors.ytdlp as ytdlp_mod
from streamkeep.extractors.ytdlp import YtDlpExtractor
from streamkeep.workers.fetch import FetchWorker


class _FakeExt:
    def __init__(self, name, result=None, exc=None):
        self.NAME = name
        self._result = result
        self._exc = exc
        self.calls = 0

    def resolve(self, url, log_fn=None):
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._result


def test_fallback_used_when_native_returns_none(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(YtDlpExtractor, "resolve", lambda self, url, log_fn=None: sentinel)
    fw = FetchWorker("https://rumble.com/vabc-x.html")
    ext = _FakeExt("Rumble", result=None)
    assert fw._resolve_or_fallback(ext, fw.url) is sentinel
    assert ext.calls == 1


def test_fallback_used_when_native_raises(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(YtDlpExtractor, "resolve", lambda self, url, log_fn=None: sentinel)
    fw = FetchWorker("https://soundcloud.com/x/y")
    ext = _FakeExt("SoundCloud", exc=RuntimeError("api changed"))
    assert fw._resolve_or_fallback(ext, fw.url) is sentinel


def test_no_fallback_when_native_succeeds(monkeypatch):
    def _boom(self, url, log_fn=None):
        raise AssertionError("yt-dlp fallback must not run when native succeeds")

    monkeypatch.setattr(YtDlpExtractor, "resolve", _boom)
    fw = FetchWorker("https://twitch.tv/x")
    native_info = object()
    ext = _FakeExt("Twitch", result=native_info)
    assert fw._resolve_or_fallback(ext, fw.url) is native_info


def test_ytdlp_extractor_not_retried_against_itself(monkeypatch):
    fw = FetchWorker("https://example.com/x")
    ext = _FakeExt("yt-dlp", result=None)
    # Must return None without constructing a second YtDlpExtractor.
    assert fw._resolve_or_fallback(ext, fw.url) is None
    assert ext.calls == 1


# ── auth-error detection ────────────────────────────────────────────────

def test_download_webpage_error_is_not_auth():
    """The classic false positive: 'age' inside 'webpage' must not trigger
    the expensive multi-browser cookie scan."""
    ext = YtDlpExtractor()
    assert ext._is_auth_error("ERROR: Unable to download webpage: 403") is False


def test_network_errors_are_not_auth():
    ext = YtDlpExtractor()
    for msg in (
        "Failed to resolve 'www.facebook.com'",
        "HTTP Error 502: Bad Gateway",
        "Connection reset by peer",
        "The read operation timed out",
    ):
        assert ext._is_auth_error(msg) is False, msg


def test_real_auth_errors_still_detected():
    ext = YtDlpExtractor()
    for msg in (
        "ERROR: Sign in to confirm your age",
        "This video is private video",
        "Use --cookies-from-browser or --cookies for the authentication",
        "Join this channel to get access",
    ):
        assert ext._is_auth_error(msg) is True, msg


# ── Cloudflare detection + impersonation args ──────────────────────────

def test_cloudflare_detection():
    assert ytdlp_mod._looks_like_cloudflare("Got HTTP Error 403 caused by Cloudflare")
    assert ytdlp_mod._looks_like_cloudflare("Just a moment... checking your browser")
    assert not ytdlp_mod._looks_like_cloudflare("ERROR: Video unavailable")


def test_impersonate_args_present_when_curl_cffi_available(monkeypatch):
    monkeypatch.setattr(ytdlp_mod, "_impersonation_available", lambda: True)
    assert ytdlp_mod.ytdlp_impersonate_args() == ["--impersonate", "chrome"]


def test_impersonate_args_empty_without_curl_cffi(monkeypatch):
    monkeypatch.setattr(ytdlp_mod, "_impersonation_available", lambda: False)
    assert ytdlp_mod.ytdlp_impersonate_args() == []
