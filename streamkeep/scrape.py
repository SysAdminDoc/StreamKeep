"""Webpage scraping helpers — regex-based + headless Playwright.

Used by the FetchWorker when a URL doesn't match any extractor: try to
find embedded media links or platform URLs on the page. Kept separate
from the extractors because these are generic URL sniffers, not
platform-specific parsers.
"""

import http.client
import os
import re
import socket
import ssl
import sys
import time
import urllib.parse

from . import CURL_UA
from .http import http_interrupted, http_probe, run_capture_interruptible
from .models import QualityInfo, StreamInfo
from .net_guard import (
    address_allowed as _address_allowed,
    resolve_host_addresses as _resolve_headless_addresses,
)


# Module-level flag so we only probe the Playwright browser install once.
# None = not checked yet; True = ready; False = failed (will retry after
# _PLAYWRIGHT_RETRY_AFTER seconds in case the user installs it mid-session).
_PLAYWRIGHT_READY = None
_PLAYWRIGHT_LAST_CHECK = 0.0
_PLAYWRIGHT_RETRY_AFTER = 300  # 5 minutes

_HEADLESS_ALLOWED_SCHEMES = frozenset({"http", "https"})
_HEADLESS_MAX_WAIT_SECONDS = 15.0
_HEADLESS_MAX_LINKS = 100
_HEADLESS_MAX_REQUESTS = 512
_HEADLESS_MAX_REDIRECTS = 10
_HEADLESS_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_HEADLESS_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_HEADLESS_HOST_RESOLVER_RULES = "MAP * ~NOTFOUND"


class HeadlessNetworkBlocked(ValueError):
    """Raised when a page-scan network destination violates policy."""


class _HeadlessNetworkPolicy:
    def __init__(self, *, allow_private_network=False):
        self.allow_private_network = bool(allow_private_network)
        self.resolutions = {}
        self.requests = 0
        self.blocked = 0
        self.redirects = 0
        self.total_bytes = 0

    def resolve(self, url):
        normalized, host, port = _parse_headless_url(url)
        try:
            addresses = _resolve_headless_addresses(host, port)
        except (OSError, ValueError) as error:
            self.blocked += 1
            raise HeadlessNetworkBlocked(
                f"DNS resolution failed for {host}"
            ) from error
        if not addresses:
            self.blocked += 1
            raise HeadlessNetworkBlocked(f"DNS returned no addresses for {host}")
        for address in addresses:
            if not _address_allowed(address, self.allow_private_network):
                self.blocked += 1
                raise HeadlessNetworkBlocked(
                    f"Address class is not allowed for {host}"
                )
        key = (host, port)
        stable = frozenset(str(address) for address in addresses)
        previous = self.resolutions.get(key)
        if previous is not None and stable != previous:
            self.blocked += 1
            raise HeadlessNetworkBlocked(
                f"DNS answer changed during scan for {host}"
            )
        self.resolutions[key] = stable
        return normalized, host, port, tuple(addresses)


def _parse_headless_url(url):
    text = str(url or "").strip()
    if not text or len(text) > 8192:
        raise HeadlessNetworkBlocked("URL is empty or exceeds the size limit")
    try:
        parsed = urllib.parse.urlsplit(text)
        scheme = parsed.scheme.lower()
        if scheme not in _HEADLESS_ALLOWED_SCHEMES:
            raise HeadlessNetworkBlocked("Only HTTP(S) URLs are allowed")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise HeadlessNetworkBlocked("URL authority is missing or contains credentials")
        port = parsed.port or (443 if scheme == "https" else 80)
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if not host:
            raise HeadlessNetworkBlocked("URL host is empty")
    except (TypeError, ValueError, UnicodeError) as error:
        raise HeadlessNetworkBlocked("URL authority is malformed") from error
    display_host = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    authority = display_host if port == default_port else f"{display_host}:{port}"
    normalized = urllib.parse.urlunsplit((
        scheme,
        authority,
        parsed.path or "/",
        parsed.query,
        "",
    ))
    return normalized, host, port


def _safe_headless_url(url, *, policy=None, allow_private_network=False):
    """Return a normalized HTTP(S) URL suitable for browser navigation.

    Browser scraping is deliberately narrower than generic URL parsing: local
    browser/file schemes and credential-bearing authority strings are never
    handed to Chromium.
    """
    try:
        active_policy = policy or _HeadlessNetworkPolicy(
            allow_private_network=allow_private_network,
        )
        normalized, _host, _port, _addresses = active_policy.resolve(url)
        return normalized
    except HeadlessNetworkBlocked:
        return ""


def _launch_scrape_browser(playwright):
    """Launch a sandboxed Chromium whose network is route-brokered."""
    return playwright.chromium.launch(
        headless=True,
        chromium_sandbox=True,
        args=[
            "--mute-audio",
            "--disable-quic",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            f"--host-resolver-rules={_HEADLESS_HOST_RESOLVER_RULES}",
        ],
    )


class _PinnedHTTPSConnection(http.client.HTTPConnection):
    """HTTPS connection to one validated IP while verifying the URL host."""

    default_port = 443

    def __init__(self, host, port, address, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self._address = str(address)
        self._context = ssl.create_default_context()

    def connect(self):
        self.sock = socket.create_connection(
            (self._address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _safe_request_headers(headers, host, port, scheme):
    allowed = {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "cookie",
        "if-modified-since",
        "if-none-match",
        "origin",
        "pragma",
        "range",
        "referer",
        "user-agent",
    }
    clean = {}
    total = 0
    for name, value in dict(headers or {}).items():
        key = str(name).strip().lower()
        text = str(value)
        size = len(key) + len(text)
        if (
            key in allowed
            and len(clean) < 64
            and len(text) <= 8192
            and total + size <= 65536
            and "\r" not in text
            and "\n" not in text
        ):
            clean[key] = text
            total += size
    default_port = 443 if scheme == "https" else 80
    display_host = f"[{host}]" if ":" in host else host
    clean["host"] = display_host if port == default_port else f"{display_host}:{port}"
    clean.setdefault("user-agent", CURL_UA)
    return clean


def _pinned_request(url, *, policy, method="GET", headers=None, timeout=8.0):
    """Perform one non-redirecting request pinned to a validated DNS answer."""
    normalized, host, port, addresses = policy.resolve(url)
    method = str(method or "GET").upper()
    if method not in {"GET", "HEAD"}:
        policy.blocked += 1
        raise HeadlessNetworkBlocked(f"HTTP method {method} is not allowed")
    parsed = urllib.parse.urlsplit(normalized)
    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    request_headers = _safe_request_headers(headers, host, port, parsed.scheme)
    last_error = None
    for address in addresses:
        connection = None
        try:
            if parsed.scheme == "https":
                connection = _PinnedHTTPSConnection(
                    host, port, address, timeout,
                )
            else:
                connection = http.client.HTTPConnection(
                    str(address), port=port, timeout=timeout,
                )
            connection.request(method, target, headers=request_headers)
            response = connection.getresponse()
            length = response.getheader("content-length")
            if length and int(length) > _HEADLESS_MAX_RESPONSE_BYTES:
                raise HeadlessNetworkBlocked("Response exceeds the per-request byte limit")
            body = response.read(_HEADLESS_MAX_RESPONSE_BYTES + 1)
            if len(body) > _HEADLESS_MAX_RESPONSE_BYTES:
                raise HeadlessNetworkBlocked("Response exceeds the per-request byte limit")
            if policy.total_bytes + len(body) > _HEADLESS_MAX_TOTAL_BYTES:
                raise HeadlessNetworkBlocked("Scan exceeds the total response byte limit")
            policy.total_bytes += len(body)
            response_headers = {}
            header_bytes = 0
            for name, value in response.getheaders():
                key = str(name).lower()
                if key in {"connection", "content-length", "proxy-authenticate",
                           "proxy-authorization", "transfer-encoding", "upgrade"}:
                    continue
                if "\r" in value or "\n" in value:
                    continue
                size = len(key) + len(value)
                if len(response_headers) >= 64 or header_bytes + size > 65536:
                    continue
                response_headers[key] = value
                header_bytes += size
            return {
                "url": normalized,
                "status": int(response.status),
                "headers": response_headers,
                "body": body,
            }
        except HeadlessNetworkBlocked:
            policy.blocked += 1
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as error:
            last_error = error
        finally:
            if connection is not None:
                connection.close()
    policy.blocked += 1
    raise HeadlessNetworkBlocked(f"Connection failed for {host}") from last_error


def _cancelled(checker=None):
    if checker is not None:
        try:
            if checker():
                return True
        except Exception:
            pass
    return http_interrupted()


def ensure_playwright_browser(log_fn=None, should_cancel=None):
    """Check that Playwright is importable AND has a Chromium install.
    Attempts `playwright install chromium` on first use. Returns True
    if the browser is ready to use."""
    global _PLAYWRIGHT_READY, _PLAYWRIGHT_LAST_CHECK
    if _PLAYWRIGHT_READY is True:
        return True
    # Allow retry after cooldown so a mid-session install can succeed
    if _PLAYWRIGHT_READY is False:
        import time as _time
        if _time.time() - _PLAYWRIGHT_LAST_CHECK < _PLAYWRIGHT_RETRY_AFTER:
            return False
    import time as _time
    _PLAYWRIGHT_LAST_CHECK = _time.time()
    if _cancelled(should_cancel):
        return False
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if log_fn:
            log_fn("[HEADLESS] Playwright not installed — falling back to regex scraper.")
        _PLAYWRIGHT_READY = False
        return False
    try:
        with sync_playwright() as p:
            if _cancelled(should_cancel):
                _PLAYWRIGHT_READY = False
                return False
            browser = _launch_scrape_browser(p)
            browser.close()
        _PLAYWRIGHT_READY = True
        return True
    except Exception as e:
        err_str = str(e)
        # `sys.executable` in a frozen PyInstaller exe is the exe itself,
        # so `sys.executable -m playwright install chromium` would
        # re-launch StreamKeep.exe in a loop. Skip the auto-install path
        # when frozen and just surface the failure — the user must install
        # Playwright browsers manually in that case.
        _frozen = getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")
        if (
            not _frozen
            and ("Executable doesn't exist" in err_str or "playwright install" in err_str)
        ):
            if _cancelled(should_cancel):
                _PLAYWRIGHT_READY = False
                return False
            if log_fn:
                log_fn("[HEADLESS] Installing Chromium for Playwright (one-time, ~120MB)...")
            result = run_capture_interruptible(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                timeout=300,
            )
            if result.interrupted or _cancelled(should_cancel):
                _PLAYWRIGHT_READY = False
                return False
            if result.returncode != 0:
                if log_fn:
                    install_err = (
                        result.stderr.strip().split("\n")[-1]
                        if result.stderr else result.error or "Unknown error"
                    )
                    log_fn(f"[HEADLESS] Install failed: {install_err}")
                _PLAYWRIGHT_READY = False
                return False
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    if _cancelled(should_cancel):
                        _PLAYWRIGHT_READY = False
                        return False
                    browser = _launch_scrape_browser(p)
                    browser.close()
                _PLAYWRIGHT_READY = True
                return True
            except Exception as retry_err:
                if log_fn:
                    log_fn(f"[HEADLESS] Still can't launch after install: {retry_err}")
                _PLAYWRIGHT_READY = False
                return False
        if log_fn:
            log_fn(
                f"[HEADLESS] Launch failed: {err_str[:120]}"
                + (" (frozen build — install Playwright browsers manually)"
                   if _frozen else "")
            )
        _PLAYWRIGHT_READY = False
        return False


def scrape_media_links_headless(page_url, log_fn=None, max_links=100,
                                wait_seconds=8, should_cancel=None,
                                allow_private_network=False):
    """Load a page in a headless Chromium browser and capture any network
    requests that look like media streams. Catches lazy-loaded players
    that the regex scraper can't see."""
    policy = _HeadlessNetworkPolicy(
        allow_private_network=allow_private_network,
    )
    page_url = _safe_headless_url(page_url, policy=policy)
    if not page_url:
        if log_fn:
            log_fn("[HEADLESS] Refused URL outside the page-scan network policy.")
        return []

    try:
        max_links = max(0, min(_HEADLESS_MAX_LINKS, int(max_links)))
        wait_seconds = max(0.0, min(_HEADLESS_MAX_WAIT_SECONDS, float(wait_seconds)))
    except (TypeError, ValueError, OverflowError):
        return []
    if max_links == 0:
        return []

    if not ensure_playwright_browser(log_fn, should_cancel=should_cancel):
        return []
    if _cancelled(should_cancel):
        return []

    media_ext_re = re.compile(
        r'\.(mp4|webm|mkv|mov|mp3|m4a|ogg|flac|wav|m3u8|mpd|ts|aac|opus)'
        r'(?:\?|$|/)',
        re.IGNORECASE,
    )
    media_ct_prefixes = (
        "video/", "audio/", "application/vnd.apple.mpegurl",
        "application/x-mpegurl", "application/dash+xml",
    )

    captured = []
    seen = set()
    request_count = 0
    deadline = time.monotonic() + wait_seconds + 10.0

    def add(u, hint, *, validated=False):
        if _cancelled(should_cancel):
            return
        if not u or u in seen:
            return
        if not validated:
            u = _safe_headless_url(u, policy=policy)
            if not u:
                return
        if len(captured) >= max_links:
            return
        lower = u.lower()
        if any(x in lower for x in (
            "/ads/", "doubleclick", "analytics",
            "telemetry", "/ping", "googlevideo.com/ptracking",
        )):
            return
        seen.add(u)
        captured.append((u, hint))

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if _cancelled(should_cancel):
                return []
            browser = _launch_scrape_browser(p)
            context = None
            page = None
            try:
                context = browser.new_context(
                    user_agent=CURL_UA,
                    viewport={"width": 1280, "height": 800},
                    accept_downloads=False,
                    service_workers="block",
                    ignore_https_errors=False,
                )
                page = context.new_page()
                page.set_default_timeout(750)
                page.set_default_navigation_timeout(8000)

                def route_request(route, req):
                    nonlocal request_count
                    if _cancelled(should_cancel) or time.monotonic() >= deadline:
                        route.abort("blockedbyclient")
                        return
                    normalized = _safe_headless_url(req.url, policy=policy)
                    if not normalized:
                        route.abort("blockedbyclient")
                        return
                    request_count += 1
                    policy.requests = request_count
                    if request_count > _HEADLESS_MAX_REQUESTS:
                        policy.blocked += 1
                        route.abort("blockedbyclient")
                        return
                    resource_type = req.resource_type
                    if resource_type == "media":
                        add(normalized, "headless media", validated=True)
                        # Capturing the media URL is sufficient. Do not let the
                        # browser stream an unbounded response into memory/disk.
                        route.abort("blockedbyclient")
                        return
                    if resource_type in ("image", "font"):
                        route.abort("blockedbyclient")
                        return
                    try:
                        remaining = max(0.1, min(8.0, deadline - time.monotonic()))
                        result = _pinned_request(
                            normalized,
                            policy=policy,
                            method=getattr(req, "method", "GET"),
                            headers=getattr(req, "headers", {}),
                            timeout=remaining,
                        )
                        if 300 <= result["status"] < 400:
                            policy.redirects += 1
                            if policy.redirects > _HEADLESS_MAX_REDIRECTS:
                                policy.blocked += 1
                                route.abort("blockedbyclient")
                                return
                        route.fulfill(
                            status=result["status"],
                            headers=result["headers"],
                            body=result["body"],
                        )
                    except HeadlessNetworkBlocked as error:
                        if log_fn:
                            log_fn(f"[HEADLESS] Blocked request: {error}")
                        route.abort("blockedbyclient")

                context.route("**/*", route_request)
                if hasattr(context, "route_web_socket"):
                    context.route_web_socket(
                        "**/*", lambda websocket: websocket.close()
                    )
                page.on("dialog", lambda dialog: dialog.dismiss())
                page.on("download", lambda download: download.cancel())
                page.on("popup", lambda popup: popup.close())

                def on_request(req):
                    if _cancelled(should_cancel):
                        return
                    u = req.url
                    rtype = req.resource_type
                    if rtype in ("media", "xhr", "fetch"):
                        if media_ext_re.search(u):
                            add(u, f"headless {rtype}")
                            return
                    if media_ext_re.search(u):
                        add(u, "headless url-ext")

                def on_response(resp):
                    if _cancelled(should_cancel):
                        return
                    try:
                        ct = (resp.headers.get("content-type", "") or "").lower()
                    except Exception:
                        return
                    if any(ct.startswith(prefix) for prefix in media_ct_prefixes):
                        add(resp.url, f"headless {ct.split(';')[0]}")

                page.on("request", on_request)
                page.on("response", on_response)

                if log_fn:
                    log_fn(f"[HEADLESS] Loading {page_url[:80]} (up to {wait_seconds}s)")
                try:
                    nav_timeout = max(2000, min(8000, int((wait_seconds + 1) * 1000)))
                    page.goto(page_url, wait_until="domcontentloaded", timeout=nav_timeout)
                except Exception as nav_err:
                    if log_fn:
                        log_fn(f"[HEADLESS] Navigation warning: {str(nav_err)[:80]}")
                if _cancelled(should_cancel):
                    return captured

                try:
                    page.evaluate("""
                        const v = document.querySelector('video');
                        if (v) v.scrollIntoView({behavior:'instant',block:'center'});
                    """)
                except Exception:
                    pass
                if _cancelled(should_cancel):
                    return captured

                try:
                    for sel in [
                        "button[aria-label*='play' i]", ".play-button",
                        ".vjs-big-play-button", "button.ytp-large-play-button",
                        ".plyr__control--overlaid",
                    ]:
                        if _cancelled(should_cancel):
                            return captured
                        try:
                            page.locator(sel).first.click(timeout=500)
                            break
                        except Exception:
                            continue
                except Exception:
                    pass

                try:
                    remaining_ms = max(0, int(wait_seconds * 1000))
                    while remaining_ms > 0 and time.monotonic() < deadline:
                        if _cancelled(should_cancel):
                            return captured
                        step = min(250, remaining_ms)
                        page.wait_for_timeout(step)
                        remaining_ms -= step
                except Exception:
                    pass

                try:
                    html = page.content()
                    for url, hint in _extract_media_links(
                        html, max_links=max_links,
                    ):
                        add(url, hint)
                except Exception:
                    pass
            finally:
                try:
                    if page is not None:
                        page.close()
                except Exception:
                    pass
                try:
                    if context is not None:
                        context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        if log_fn:
            log_fn(f"[HEADLESS] Error: {str(e)[:120]}")
        return []

    if log_fn:
        log_fn(f"[HEADLESS] Captured {len(captured)} media request(s)")
        log_fn(
            "[HEADLESS] Network policy: "
            f"requests={policy.requests}, blocked={policy.blocked}, "
            f"redirects={policy.redirects}, bytes={policy.total_bytes}, "
            f"lan_override={'on' if policy.allow_private_network else 'off'}"
        )
    return captured


def _extract_media_links(body, *, max_links=100):
    """Extract bounded media candidates from already-fetched page HTML."""
    found = []
    seen = set()

    def add(url, hint):
        if not url or url in seen:
            return
        if len(found) >= max_links:
            return
        seen.add(url)
        found.append((url, hint))

    media_re = re.compile(
        r'https?://[^\s"\'<>]+\.(?:mp4|webm|mkv|mov|mp3|m4a|ogg|flac|wav|m3u8|mpd|ts)'
        r'(?:\?[^\s"\'<>]*)?',
        re.IGNORECASE,
    )
    for m in media_re.finditer(body):
        add(m.group(0), "direct media")

    host_re = re.compile(
        r'https?://(?:www\.)?'
        r'(?:youtube\.com/(?:watch|shorts|embed)/[^\s"\'<>]+'
        r'|youtu\.be/[^\s"\'<>]+'
        r'|twitch\.tv/(?:videos/)?[a-zA-Z0-9_]+'
        r'|kick\.com/[a-zA-Z0-9_-]+(?:/videos/\d+)?'
        r'|rumble\.com/v[a-z0-9]+[^\s"\'<>]*'
        r'|soundcloud\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+'
        r'|reddit\.com/r/\w+/comments/\w+[^\s"\'<>]*'
        r'|vimeo\.com/\d+'
        r'|dailymotion\.com/video/[a-zA-Z0-9]+'
        r'|bitchute\.com/video/[a-zA-Z0-9]+'
        r'|odysee\.com/@[^\s"\'<>]+'
        r')',
        re.IGNORECASE,
    )
    for m in host_re.finditer(body):
        url = m.group(0).rstrip('.,;)')
        add(url, "platform link")

    iframe_re = re.compile(
        r'<iframe[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE,
    )
    for m in iframe_re.finditer(body):
        src = m.group(1)
        if src.startswith("//"):
            src = "https:" + src
        if not src.startswith("http"):
            continue
        if any(x in src.lower() for x in (
            "youtube", "vimeo", "twitch", "kick", "rumble",
            "dailymotion", "bitchute", "odysee",
        )):
            add(src, "iframe embed")

    return found


def scrape_media_links(page_url, log_fn=None, max_links=100,
                       allow_private_network=False):
    """Safely fetch a webpage and extract media or embeddable URLs.

    Redirects are followed manually through the same pinned DNS/address
    policy as the browser route broker. JavaScript is not executed here.
    """
    try:
        max_links = max(0, min(_HEADLESS_MAX_LINKS, int(max_links)))
    except (TypeError, ValueError, OverflowError):
        return []
    if max_links == 0:
        return []
    policy = _HeadlessNetworkPolicy(
        allow_private_network=allow_private_network,
    )
    current = _safe_headless_url(page_url, policy=policy)
    if not current:
        if log_fn:
            log_fn("[SCRAPE] Refused URL outside the page-scan network policy.")
        return []
    headers = {
        "User-Agent": CURL_UA,
        "Accept": "text/html,application/xhtml+xml",
    }
    result = None
    try:
        for _redirect in range(_HEADLESS_MAX_REDIRECTS + 1):
            policy.requests += 1
            result = _pinned_request(
                current, policy=policy, headers=headers, timeout=8.0,
            )
            if result["status"] not in {301, 302, 303, 307, 308}:
                break
            location = result["headers"].get("location", "")
            if not location:
                return []
            policy.redirects += 1
            if policy.redirects > _HEADLESS_MAX_REDIRECTS:
                raise HeadlessNetworkBlocked("Redirect budget exceeded")
            current = urllib.parse.urljoin(current, location)
        if result is None or not 200 <= result["status"] < 300:
            return []
        body = result["body"].decode("utf-8", "replace")
    except HeadlessNetworkBlocked as error:
        if log_fn:
            log_fn(f"[SCRAPE] Blocked request: {error}")
        return []

    found = []
    for url, hint in _extract_media_links(body, max_links=max_links):
        safe_url = _safe_headless_url(url, policy=policy)
        if safe_url:
            found.append((safe_url, hint))

    if log_fn:
        log_fn(
            f"[SCRAPE] Found {len(found)} candidate link(s); "
            f"requests={policy.requests}, redirects={policy.redirects}, "
            f"bytes={policy.total_bytes}, "
            f"lan_override={'on' if policy.allow_private_network else 'off'}"
        )
    return found


def detect_direct_media(url, log_fn=None):
    """Sniff a URL via HEAD request. Returns StreamInfo if it's a direct
    media file, else None."""
    MEDIA_TYPES = {
        "video/mp4": "mp4", "video/webm": "mp4", "video/x-matroska": "mp4",
        "video/quicktime": "mp4", "video/x-msvideo": "mp4", "video/x-flv": "mp4",
        "audio/mpeg": "mp4", "audio/mp3": "mp4", "audio/mp4": "mp4",
        "audio/ogg": "mp4", "audio/flac": "mp4", "audio/wav": "mp4",
        "audio/x-wav": "mp4", "audio/aac": "mp4",
        "application/vnd.apple.mpegurl": "hls", "audio/mpegurl": "hls",
        "application/x-mpegurl": "hls", "application/dash+xml": "dash",
        # Note: application/octet-stream is intentionally excluded — it
        # matches any binary file (exe, zip, etc.) and would false-positive.
        # Direct media URLs are caught earlier by extension matching.
    }
    MEDIA_EXTS = {
        ".mp4", ".webm", ".mkv", ".avi", ".mov", ".flv", ".wmv",
        ".mp3", ".m4a", ".ogg", ".flac", ".wav", ".aac", ".opus",
        ".m3u8", ".mpd", ".ts",
    }

    parsed = urllib.parse.urlparse(url)

    def infer_channel(parsed_url):
        parts = [p for p in parsed_url.path.strip("/").split("/") if p]
        if len(parts) < 2:
            return ""
        lead = parts[0].strip()
        if not lead or lead.isdigit():
            return ""
        return lead

    ext = os.path.splitext(parsed.path)[1].lower()
    if ext in MEDIA_EXTS:
        # DASH MPD manifest — parse into proper qualities (F50)
        if ext == ".mpd":
            from .dash import parse_mpd
            if log_fn:
                log_fn(f"DASH manifest detected: {url}")
            qualities = parse_mpd(url, log_fn=log_fn)
            if qualities:
                info = StreamInfo(
                    platform="Direct",
                    url=url,
                    title=parsed.path.split("/")[-1],
                    channel=infer_channel(parsed),
                    qualities=qualities,
                )
                return info
        fmt = "hls" if ext == ".m3u8" else "mp4"
        if log_fn:
            log_fn(f"Direct media URL detected by extension: {ext}")
        info = StreamInfo(
            platform="Direct",
            url=url,
            title=parsed.path.split("/")[-1],
            channel=infer_channel(parsed),
        )
        info.qualities.append(QualityInfo(name=f"direct ({ext})", url=url, format_type=fmt))
        return info

    try:
        probe = http_probe(url, headers={"User-Agent": CURL_UA}, timeout=10)
        ct = probe.get("content_type", "")
        final_url = probe.get("final_url") or url
        final_parsed = urllib.parse.urlparse(final_url)
        final_ext = os.path.splitext(final_parsed.path)[1].lower()
        if final_ext in MEDIA_EXTS:
            if final_ext == ".mpd":
                from .dash import parse_mpd
                if log_fn:
                    log_fn(f"DASH manifest detected after probe redirect: {final_url}")
                qualities = parse_mpd(final_url, log_fn=log_fn)
                if qualities:
                    return StreamInfo(
                        platform="Direct",
                        url=final_url,
                        title=os.path.basename(final_parsed.path) or parsed.path.split("/")[-1],
                        channel=infer_channel(final_parsed) or infer_channel(parsed),
                        qualities=qualities,
                    )
            fmt = "hls" if final_ext == ".m3u8" else "mp4"
            if log_fn:
                log_fn(f"Direct media URL detected after probe redirect: {final_ext}")
            info = StreamInfo(
                platform="Direct",
                url=final_url,
                title=os.path.basename(final_parsed.path) or parsed.path.split("/")[-1],
                channel=infer_channel(final_parsed) or infer_channel(parsed),
            )
            info.qualities.append(
                QualityInfo(name=f"direct ({final_ext})", url=final_url, format_type=fmt)
            )
            return info

        if ct in MEDIA_TYPES:
            fmt = MEDIA_TYPES[ct]
            title = os.path.basename(final_parsed.path) or parsed.path.split("/")[-1]
            if log_fn:
                log_fn(f"Direct media URL detected: {ct}")
            info = StreamInfo(
                platform="Direct",
                url=final_url,
                title=title,
                channel=infer_channel(final_parsed) or infer_channel(parsed),
            )
            info.qualities.append(
                QualityInfo(name=f"direct ({ct})", url=final_url, format_type=fmt)
            )
            return info
    except Exception:
        pass

    return None
