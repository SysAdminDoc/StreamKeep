import unittest
from unittest import mock

from streamkeep.models import QualityInfo
from streamkeep import scrape
from streamkeep.scrape import detect_direct_media


class _FakeRoute:
    def __init__(self):
        self.action = ""

    def abort(self, _reason=None):
        self.action = "abort"

    def continue_(self):
        self.action = "continue"


class _FakeRequest:
    def __init__(self, url, resource_type="document"):
        self.url = url
        self.resource_type = resource_type


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

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self):
        self.page = _FakePage()
        self.route_handler = None
        self.closed = False

    def new_page(self):
        return self.page

    def route(self, _pattern, handler):
        self.route_handler = handler

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
                mock.patch("playwright.sync_api.sync_playwright", return_value=manager):
            captured = scrape.scrape_media_links_headless(
                "https://example.com/watch",
                wait_seconds=0,
            )

        self.assertNotIn("--no-sandbox", fake.chromium.launch_kwargs["args"])
        self.assertEqual(fake.chromium.launch_kwargs["args"], ["--mute-audio"])
        self.assertFalse(fake.browser.context_kwargs["accept_downloads"])
        self.assertEqual(fake.browser.context_kwargs["service_workers"], "block")
        self.assertEqual(fake.browser.context.page.default_timeout, 750)
        self.assertEqual(fake.browser.context.page.navigation_timeout, 8000)
        self.assertTrue(fake.browser.context.closed)
        self.assertTrue(fake.browser.closed)
        self.assertEqual(captured, [])

    def test_headless_route_blocks_unsafe_and_unbounded_media_requests(self):
        fake = _FakePlaywright()
        manager = _FakePlaywrightManager(fake)
        with mock.patch("streamkeep.scrape.ensure_playwright_browser", return_value=True), \
                mock.patch("playwright.sync_api.sync_playwright", return_value=manager):
            captured = scrape.scrape_media_links_headless(
                "https://example.com/watch",
                wait_seconds=0,
            )

        route = _FakeRoute()
        fake.browser.context.route_handler(route, _FakeRequest("file:///tmp/video.mp4", "media"))
        self.assertEqual(route.action, "abort")

        route = _FakeRoute()
        fake.browser.context.route_handler(
            route,
            _FakeRequest("https://cdn.example.com/live/source", "media"),
        )
        self.assertEqual(route.action, "abort")
        self.assertEqual(captured, [("https://cdn.example.com/live/source", "headless media")])

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
