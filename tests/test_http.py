import base64
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
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

    def test_http_head_exposes_strong_validator_and_digests(self):
        digest = base64.b64encode(hashlib.sha256(b"payload").digest()).decode()

        def fake_run(_cmd, timeout):
            self.assertGreater(timeout, 0)
            return http.CommandResult(
                returncode=0,
                stdout=(
                    "HTTP/1.1 302 Found\r\nLocation: /media\r\n\r\n"
                    "HTTP/2 200\r\n"
                    "Content-Length: 7\r\n"
                    "Accept-Ranges: bytes\r\n"
                    'ETag: "representation-1"\r\n'
                    "Last-Modified: Wed, 15 Jul 2026 12:00:00 GMT\r\n"
                    f"Content-Digest: sha-256=:{digest}:\r\n\r\n"
                ),
            )

        with mock.patch.object(http, "_resolve_proxy", return_value=""), \
                mock.patch.object(http, "_append_cookie_args"), \
                mock.patch.object(http, "run_capture_interruptible", side_effect=fake_run):
            details = http.http_head_details("https://example.com/video.mp4")

        self.assertEqual(details["status"], 200)
        self.assertEqual(details["content_length"], 7)
        self.assertTrue(details["accepts_ranges"])
        self.assertEqual(details["etag"], '"representation-1"')
        self.assertEqual(details["content_digest"], f"sha-256=:{digest}:")


class ParallelDownloadTests(unittest.TestCase):
    payload = b"abcdefgh"

    def _head(self, **overrides):
        details = {
            "status": 200,
            "content_length": len(self.payload),
            "accepts_ranges": True,
            "etag": '"v1"',
            "last_modified": "",
            "content_digest": "",
            "repr_digest": "",
        }
        details.update(overrides)
        return details

    def _popen(self, commands, *, status=206, content_range=True):
        payload = self.payload

        def fake_popen(cmd, **_kwargs):
            commands.append(list(cmd))
            start, end = map(int, cmd[cmd.index("-r") + 1].split("-"))
            part_path = Path(cmd[cmd.index("-o") + 1])
            header_path = Path(cmd[cmd.index("-D") + 1])
            part_path.write_bytes(payload[start:end + 1])
            range_header = (
                f"Content-Range: bytes {start}-{end}/{len(payload)}\r\n"
                if content_range else ""
            )
            header_path.write_bytes(
                f"HTTP/1.1 {status} Test\r\n{range_header}\r\n".encode(
                    "iso-8859-1"
                )
            )
            proc = mock.Mock()
            proc.poll.return_value = 0
            proc.returncode = 0
            proc.stderr = BytesIO()
            return proc

        return fake_popen

    def _run(self, outfile, head, commands, **popen_options):
        with mock.patch.object(http, "http_head_details", return_value=head), \
                mock.patch.object(http, "_resolve_proxy", return_value=""), \
                mock.patch.object(http, "_append_cookie_args"), \
                mock.patch.object(
                    http.subprocess,
                    "Popen",
                    side_effect=self._popen(commands, **popen_options),
                ):
            return http.parallel_http_download(
                "https://example.com/video.mp4",
                str(outfile),
                connections=2,
                min_size_mb=0,
            )

    def test_range_requests_use_if_range_and_require_exact_206(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            outfile = Path(tmpdir) / "video.mp4"
            commands = []
            self.assertTrue(self._run(outfile, self._head(), commands))

            self.assertEqual(outfile.read_bytes(), self.payload)
            self.assertEqual(len(commands), 2)
            for cmd in commands:
                self.assertIn("If-Range: \"v1\"", cmd)

            bad_outfile = Path(tmpdir) / "bad.mp4"
            self.assertFalse(self._run(
                bad_outfile,
                self._head(),
                [],
                status=200,
                content_range=False,
            ))
            self.assertFalse(bad_outfile.exists())

    def test_validator_change_invalidates_matching_size_parts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            outfile = Path(tmpdir) / "video.mp4"
            parts = Path(str(outfile) + ".parts")
            parts.mkdir()
            (parts / "part_00").write_bytes(b"XXXX")
            (parts / "part_01").write_bytes(b"YYYY")
            ranges = [(0, 0, 3), (1, 4, 7)]
            old_metadata = http._parallel_resume_metadata(
                "https://example.com/video.mp4",
                len(self.payload),
                ranges,
                self._head(etag='"old"'),
            )
            self.assertTrue(http._write_parallel_resume_metadata(
                str(parts / "resume.json"), old_metadata
            ))

            commands = []
            self.assertTrue(self._run(outfile, self._head(etag='"new"'), commands))
            self.assertEqual(len(commands), 2)
            self.assertEqual(outfile.read_bytes(), self.payload)

    def test_old_parts_are_reused_only_with_safe_matching_validator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            outfile = Path(tmpdir) / "video.mp4"
            parts = Path(str(outfile) + ".parts")
            parts.mkdir()
            (parts / "part_00").write_bytes(self.payload[:4])
            (parts / "part_01").write_bytes(self.payload[4:])
            ranges = [(0, 0, 3), (1, 4, 7)]
            metadata = http._parallel_resume_metadata(
                "https://example.com/video.mp4",
                len(self.payload),
                ranges,
                self._head(),
            )
            self.assertTrue(http._write_parallel_resume_metadata(
                str(parts / "resume.json"), metadata
            ))

            commands = []
            self.assertTrue(self._run(outfile, self._head(), commands))
            self.assertEqual(commands, [])
            self.assertEqual(outfile.read_bytes(), self.payload)

            no_validator_outfile = Path(tmpdir) / "unvalidated.mp4"
            no_validator_parts = Path(str(no_validator_outfile) + ".parts")
            no_validator_parts.mkdir()
            (no_validator_parts / "part_00").write_bytes(b"XXXX")
            (no_validator_parts / "part_01").write_bytes(b"YYYY")
            no_validator_metadata = http._parallel_resume_metadata(
                "https://example.com/video.mp4",
                len(self.payload),
                ranges,
                self._head(etag=""),
            )
            self.assertTrue(http._write_parallel_resume_metadata(
                str(no_validator_parts / "resume.json"), no_validator_metadata
            ))
            commands = []
            self.assertTrue(self._run(
                no_validator_outfile,
                self._head(etag=""),
                commands,
            ))
            self.assertEqual(len(commands), 2)
            self.assertEqual(no_validator_outfile.read_bytes(), self.payload)

    def test_advertised_digest_is_verified_before_success(self):
        digest = base64.b64encode(hashlib.sha256(self.payload).digest()).decode()
        with tempfile.TemporaryDirectory() as tmpdir:
            good = Path(tmpdir) / "good.mp4"
            self.assertTrue(self._run(
                good,
                self._head(content_digest=f"sha-256=:{digest}:"),
                [],
            ))

            bad = Path(tmpdir) / "bad.mp4"
            wrong = base64.b64encode(hashlib.sha256(b"wrong").digest()).decode()
            self.assertFalse(self._run(
                bad,
                self._head(repr_digest=f"sha-256=:{wrong}:"),
                [],
            ))
            self.assertFalse(bad.exists())


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
