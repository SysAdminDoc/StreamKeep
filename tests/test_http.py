import unittest
from unittest import mock

from streamkeep import http


class HttpCommandTests(unittest.TestCase):
    def test_build_curl_cmd_restricts_protocols_and_redirects(self):
        with mock.patch.object(http, "_resolve_proxy", return_value=""), \
                mock.patch.object(http, "_append_cookie_args"):
            cmd = http._build_curl_cmd("https://example.com/video.m3u8")

        self.assertIn("--proto", cmd)
        self.assertIn("=http,https", cmd)
        self.assertIn("--max-redirs", cmd)
        self.assertIn("5", cmd)

    def test_http_head_restricts_protocols_and_redirects(self):
        captured = {}

        def fake_run(cmd, timeout):
            captured["cmd"] = cmd
            captured["timeout"] = timeout
            return http.CommandResult(
                returncode=0,
                stdout=(
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Length: 42\r\n"
                    "Accept-Ranges: bytes\r\n\r\n"
                ),
            )

        with mock.patch.object(http, "_resolve_proxy", return_value=""), \
                mock.patch.object(http, "_append_cookie_args"), \
                mock.patch.object(http, "run_capture_interruptible", side_effect=fake_run):
            status, size, accepts = http.http_head("https://example.com/video.mp4")

        self.assertEqual((status, size, accepts), (200, 42, True))
        self.assertIn("--proto", captured["cmd"])
        self.assertIn("=http,https", captured["cmd"])
        self.assertIn("--max-redirs", captured["cmd"])
        self.assertIn("5", captured["cmd"])


if __name__ == "__main__":
    unittest.main()
