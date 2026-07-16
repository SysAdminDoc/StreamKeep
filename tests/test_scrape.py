import ipaddress
import unittest
from unittest import mock

from streamkeep.models import QualityInfo
from streamkeep import scrape
from streamkeep.scrape import detect_direct_media
from streamkeep.workers.page_scrape import PageScrapeWorker


class _FakeRoute:
    def __init__(self):
        self.action = ""
        self.fulfill_kwargs = None

    def abort(self, _reason=None):
        self.action = "abort"

    def continue_(self):
        self.action = "continue"

    def fulfill(self, **kwargs):
        self.action = "fulfill"
        self.fulfill_kwargs = kwargs


class _FakeRequest:
    def __init__(self, url, resource_type="document"):
        self.url = url
        self.resource_type = resource_type
        self.method = "GET"
        self.headers = {}


class _FakePage:
    def __init__(self):
        self.handlers = {}
        self.goto_args = None
        self.closed = False

    def set_default_timeout(self, timeout):
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout):
        self.navigation_timeout = timeout

    def on(self, event, handler):
        self.handlers[event] = handler

    def goto(self, *args, **kwargs):
        self.goto_args = (args, kwargs)

    def evaluate(self, _script):
        return None

    def locator(self, _selector):
        locator = mock.Mock()
        locator.first.click.side_effect = RuntimeError("not found")
        return locator

    def wait_for_timeout(self, _timeout):
        return None

    def content(self):
        return ""

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self):
        self.page = _FakePage()
        self.route_handler = None
        self.web_socket_handler = None
        self.closed = False

    def new_page(self):
        return self.page

    def route(self, _pattern, handler):
        self.route_handler = handler

    def route_web_socket(self, _pattern, handler):
        self.web_socket_handler = handler

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self):
        self.context = _FakeContext()
        self.context_kwargs = None
        self.closed = False

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return self.context

    def close(self):
        self.closed = True


class _FakeWebSocketRoute:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_kwargs = None

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self.browser


class _FakePlaywright:
    def __init__(self):
        self.browser = _FakeBrowser()
        self.chromium = _FakeChromium(self.browser)


class _FakePlaywrightManager:
    def __init__(self, playwright):
        self.playwright = playwright

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return False


class ScrapeTests(unittest.TestCase):
    @staticmethod
    def _public_dns(_host, _port):
        return (ipaddress.ip_address("93.184.216.34"),)

    @staticmethod
    def _public_dns_with_literals(host, _port):
        try:
            return (ipaddress.ip_address(host),)
        except ValueError:
            return (ipaddress.ip_address("93.184.216.34"),)

    def test_headless_scrape_rejects_unsafe_navigation_before_launch(self):
        with mock.patch("streamkeep.scrape.ensure_playwright_browser") as ensure:
            self.assertEqual(scrape.scrape_media_links_headless("file:///etc/passwd"), [])
            self.assertEqual(
                scrape.scrape_media_links_headless("https://user:pass@example.com/watch"),
                [],
            )
        ensure.assert_not_called()

    def test_headless_scrape_keeps_sandbox_and_caps_browser_resources(self):
        fake = _FakePlaywright()
        manager = _FakePlaywrightManager(fake)
        with mock.patch("streamkeep.scrape.ensure_playwright_browser", return_value=True), \
                mock.patch(
                    "streamkeep.scrape._resolve_headless_addresses",
                    side_effect=self._public_dns,
                ), \
                mock.patch("playwright.sync_api.sync_playwright", return_value=manager):
            captured = scrape.scrape_media_links_headless(
                "https://example.com/watch",
                wait_seconds=0,
            )

        self.assertNotIn("--no-sandbox", fake.chromium.launch_kwargs["args"])
        self.assertTrue(fake.chromium.launch_kwargs["chromium_sandbox"])
        self.assertEqual(fake.chromium.launch_kwargs["args"], [
            "--mute-audio",
            "--disable-quic",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--host-resolver-rules=MAP * ~NOTFOUND",
        ])
        self.assertFalse(fake.browser.context_kwargs["accept_downloads"])
        self.assertEqual(fake.browser.context_kwargs["service_workers"], "block")
        self.assertEqual(fake.browser.context.page.default_timeout, 750)
        self.assertEqual(fake.browser.context.page.navigation_timeout, 8000)
        websocket = _FakeWebSocketRoute()
        fake.browser.context.web_socket_handler(websocket)
        self.assertTrue(websocket.closed)
        self.assertTrue(fake.browser.context.closed)
        self.assertTrue(fake.browser.closed)
        self.assertEqual(captured, [])

    def test_headless_route_blocks_unsafe_and_unbounded_media_requests(self):
        fake = _FakePlaywright()
        manager = _FakePlaywrightManager(fake)
        with mock.patch("streamkeep.scrape.ensure_playwright_browser", return_value=True), \
                mock.patch(
                    "streamkeep.scrape._resolve_headless_addresses",
                    side_effect=self._public_dns_with_literals,
                ), \
                mock.patch("playwright.sync_api.sync_playwright", return_value=manager):
            captured = scrape.scrape_media_links_headless(
                "https://example.com/watch",
                wait_seconds=0,
            )
            route = _FakeRoute()
            fake.browser.context.route_handler(
                route, _FakeRequest("file:///tmp/video.mp4", "media")
            )
            self.assertEqual(route.action, "abort")

            route = _FakeRoute()
            fake.browser.context.route_handler(
                route,
                _FakeRequest("https://cdn.example.com/live/source", "media"),
            )
        self.assertEqual(route.action, "abort")
        self.assertEqual(captured, [("https://cdn.example.com/live/source", "headless media")])

    def test_network_policy_blocks_special_addresses_and_narrowly_allows_lan(self):
        blocked = (
            "http://127.0.0.1/",
            "http://10.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.100.100.200/latest/meta-data/",
            "http://224.0.0.1/",
            "http://0.0.0.0/",
            "http://[::ffff:192.168.1.2]/",
            "http://[fd00:ec2::254]/",
        )
        for url in blocked:
            with self.subTest(url=url):
                self.assertEqual(scrape._safe_headless_url(url), "")

        self.assertEqual(
            scrape._safe_headless_url(
                "http://192.168.1.20/media",
                allow_private_network=True,
            ),
            "http://192.168.1.20/media",
        )
        self.assertEqual(
            scrape._safe_headless_url(
                "http://127.0.0.1/media",
                allow_private_network=True,
            ),
            "",
        )

    def test_dns_rebinding_change_fails_closed(self):
        policy = scrape._HeadlessNetworkPolicy()
        answers = [
            (ipaddress.ip_address("93.184.216.34"),),
            (ipaddress.ip_address("8.8.8.8"),),
        ]
        with mock.patch(
                "streamkeep.scrape._resolve_headless_addresses",
                side_effect=answers,
        ):
            policy.resolve("https://example.com/watch")
            with self.assertRaises(scrape.HeadlessNetworkBlocked) as raised:
                policy.resolve("https://example.com/watch")
        self.assertIn("DNS answer changed", str(raised.exception))

    def test_static_scrape_revalidates_redirect_and_blocks_private_target(self):
        calls = []

        def fake_request(url, *, policy, **_kwargs):
            normalized, _host, _port, _addresses = policy.resolve(url)
            calls.append(normalized)
            return {
                "url": normalized,
                "status": 302,
                "headers": {"location": "http://10.0.0.9/private"},
                "body": b"",
            }

        with mock.patch(
                "streamkeep.scrape._resolve_headless_addresses",
                side_effect=self._public_dns_with_literals,
        ), mock.patch(
                "streamkeep.scrape._pinned_request",
                side_effect=fake_request,
        ):
            found = scrape.scrape_media_links("https://example.com/watch")

        self.assertEqual(found, [])
        self.assertEqual(calls, ["https://example.com/watch"])

    def test_static_scrape_filters_private_media_candidates(self):
        body = (
            b'<video src="http://10.0.0.9/private.mp4"></video>'
            b'<video src="https://cdn.example.com/public.mp4"></video>'
        )

        def fake_request(url, *, policy, **_kwargs):
            normalized, _host, _port, _addresses = policy.resolve(url)
            return {
                "url": normalized,
                "status": 200,
                "headers": {"content-type": "text/html"},
                "body": body,
            }

        with mock.patch(
                "streamkeep.scrape._resolve_headless_addresses",
                side_effect=self._public_dns_with_literals,
        ), mock.patch(
                "streamkeep.scrape._pinned_request",
                side_effect=fake_request,
        ):
            found = scrape.scrape_media_links("https://example.com/watch")

        self.assertEqual(found, [
            ("https://cdn.example.com/public.mp4", "direct media")
        ])

    def test_route_broker_fulfills_public_and_blocks_redirect_to_private(self):
        fake = _FakePlaywright()
        manager = _FakePlaywrightManager(fake)
        response = {
            "url": "https://example.com/watch",
            "status": 302,
            "headers": {"location": "http://10.0.0.2/private"},
            "body": b"",
        }
        with mock.patch("streamkeep.scrape.ensure_playwright_browser", return_value=True), \
                mock.patch(
                    "streamkeep.scrape._resolve_headless_addresses",
                    side_effect=self._public_dns_with_literals,
                ), \
                mock.patch("streamkeep.scrape._pinned_request", return_value=response), \
                mock.patch("playwright.sync_api.sync_playwright", return_value=manager):
            scrape.scrape_media_links_headless(
                "https://example.com/watch",
                wait_seconds=0,
            )
            public_route = _FakeRoute()
            fake.browser.context.route_handler(
                public_route,
                _FakeRequest("https://example.com/watch"),
            )
            private_route = _FakeRoute()
            fake.browser.context.route_handler(
                private_route,
                _FakeRequest("http://10.0.0.2/private"),
            )

        self.assertEqual(public_route.action, "fulfill")
        self.assertEqual(public_route.fulfill_kwargs["status"], 302)
        self.assertEqual(private_route.action, "abort")

    def test_route_broker_enforces_request_budget(self):
        fake = _FakePlaywright()
        manager = _FakePlaywrightManager(fake)
        response = {
            "url": "https://example.com/script.js",
            "status": 200,
            "headers": {"content-type": "application/javascript"},
            "body": b"",
        }
        with mock.patch("streamkeep.scrape.ensure_playwright_browser", return_value=True), \
                mock.patch(
                    "streamkeep.scrape._resolve_headless_addresses",
                    side_effect=self._public_dns_with_literals,
                ), \
                mock.patch(
                    "streamkeep.scrape._pinned_request", return_value=response,
                ) as pinned, \
                mock.patch("playwright.sync_api.sync_playwright", return_value=manager):
            scrape.scrape_media_links_headless(
                "https://example.com/watch",
                wait_seconds=0,
            )
            first = last = None
            for index in range(scrape._HEADLESS_MAX_REQUESTS + 1):
                route = _FakeRoute()
                fake.browser.context.route_handler(
                    route,
                    _FakeRequest(f"https://example.com/script-{index}.js", "script"),
                )
                first = first or route
                last = route

        self.assertEqual(first.action, "fulfill")
        self.assertEqual(last.action, "abort")
        self.assertEqual(pinned.call_count, scrape._HEADLESS_MAX_REQUESTS)

    def test_page_worker_scopes_lan_override_to_both_scan_passes(self):
        worker = PageScrapeWorker(
            "http://192.168.1.20/watch",
            allow_private_network=True,
        )
        with mock.patch(
                "streamkeep.workers.page_scrape.scrape_media_links_headless",
                return_value=[],
        ) as headless, mock.patch(
                "streamkeep.workers.page_scrape.scrape_media_links",
                return_value=[],
        ) as static:
            worker.run()

        self.assertTrue(headless.call_args.kwargs["allow_private_network"])
        self.assertTrue(static.call_args.kwargs["allow_private_network"])

    @mock.patch("streamkeep.scrape.http_probe")
    def test_detect_direct_media_uses_redirected_playlist_extension(self, mock_probe):
        mock_probe.return_value = {
            "status": 200,
            "content_type": "",
            "final_url": "https://cdn.example.com/channel/master.m3u8?token=abc",
        }

        info = detect_direct_media("https://example.com/watch?id=123")

        self.assertIsNotNone(info)
        self.assertEqual(info.url, "https://cdn.example.com/channel/master.m3u8?token=abc")
        self.assertEqual(info.qualities[0].format_type, "hls")

    @mock.patch("streamkeep.dash.parse_mpd")
    @mock.patch("streamkeep.scrape.http_probe")
    def test_detect_direct_media_parses_redirected_dash_manifests(
        self,
        mock_probe,
        mock_parse_mpd,
    ):
        mock_probe.return_value = {
            "status": 200,
            "content_type": "application/octet-stream",
            "final_url": "https://cdn.example.com/channel/manifest.mpd",
        }
        mock_parse_mpd.return_value = [
            QualityInfo(name="1080p", url="https://cdn.example.com/seg.m4s", format_type="dash")
        ]

        info = detect_direct_media("https://example.com/watch?id=456")

        self.assertIsNotNone(info)
        mock_parse_mpd.assert_called_once_with(
            "https://cdn.example.com/channel/manifest.mpd",
            log_fn=None,
        )
        self.assertEqual(info.url, "https://cdn.example.com/channel/manifest.mpd")
        self.assertEqual(info.qualities, mock_parse_mpd.return_value)


if __name__ == "__main__":
    unittest.main()
