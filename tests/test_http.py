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


class HostProfileTests(unittest.TestCase):
    def setUp(self):
        http.set_host_profiles({})

    def tearDown(self):
        http.set_host_profiles({})

    def test_host_profile_headers_applied_to_matching_url(self):
        http.set_host_profiles({
            "cdn.example.com": {
                "headers": {"X-Custom": "value123"},
                "referrer": "https://example.com/",
            }
        })
        with mock.patch.object(http, "_resolve_proxy", return_value=""), \
                mock.patch.object(http, "_append_cookie_args"):
            cmd = http._build_curl_cmd("https://cdn.example.com/video.mp4")

        self.assertIn("-H", cmd)
        header_indices = [i for i, v in enumerate(cmd) if v == "-H"]
        found_custom = any(cmd[i + 1] == "X-Custom: value123" for i in header_indices)
        self.assertTrue(found_custom, "Host profile header not found in curl command")
        self.assertIn("-e", cmd)
        ref_idx = cmd.index("-e")
        self.assertEqual(cmd[ref_idx + 1], "https://example.com/")

    def test_host_profile_not_applied_to_different_host(self):
        http.set_host_profiles({
            "cdn.example.com": {"headers": {"X-Custom": "secret"}}
        })
        with mock.patch.object(http, "_resolve_proxy", return_value=""), \
                mock.patch.object(http, "_append_cookie_args"):
            cmd = http._build_curl_cmd("https://other.example.com/video.mp4")

        header_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-H" and i + 1 < len(cmd)]
        self.assertFalse(any("X-Custom" in h for h in header_args))

    def test_invalid_referrer_scheme_rejected(self):
        http.set_host_profiles({
            "cdn.example.com": {"referrer": "ftp://evil.com/path"}
        })
        profile = http._host_profile_for_url("https://cdn.example.com/x")
        self.assertIsNone(profile)

    def test_set_host_profiles_clears_previous(self):
        http.set_host_profiles({"a.com": {"headers": {"X": "1"}}})
        http.set_host_profiles({"b.com": {"headers": {"Y": "2"}}})
        profiles = http.get_host_profiles()
        self.assertNotIn("a.com", profiles)
        self.assertIn("b.com", profiles)


if __name__ == "__main__":
    unittest.main()
