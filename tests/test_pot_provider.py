"""YouTube PO-token provider lifecycle (V33)."""

import socket
import sys
import threading
import unittest
from unittest import mock

from streamkeep import pot_provider as pot


class _LoopbackServer:
    """A minimal accept-only listener so a probe has something to find."""

    def __init__(self):
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except (OSError, socket.timeout):
                continue
            conn.close()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close()


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class BaseUrlPolicyTests(unittest.TestCase):
    def test_the_default_is_the_documented_loopback_endpoint(self):
        self.assertEqual(pot.normalize_base_url(""), pot.DEFAULT_BASE_URL)
        self.assertIn("127.0.0.1", pot.DEFAULT_BASE_URL)

    def test_loopback_forms_normalize(self):
        cases = {
            "127.0.0.1:4416": "http://127.0.0.1:4416",
            "http://localhost:9000": "http://localhost:9000",
            "http://127.0.0.1:4416/": "http://127.0.0.1:4416",
            "127.0.0.1": "http://127.0.0.1:4416",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(pot.normalize_base_url(value), expected)

    def test_a_remote_host_is_refused_not_downgraded(self):
        # A PO-token provider handles account-bound tokens; sending them to a
        # third party must be impossible, not merely discouraged.
        for value in (
            "http://example.com:4416",
            "https://tokens.evil.net",
            "http://10.0.0.5:4416",
            "ftp://127.0.0.1:4416",
        ):
            with self.subTest(value=value):
                self.assertEqual(pot.normalize_base_url(value), "")

    def test_config_drives_the_endpoint(self):
        self.assertEqual(
            pot.base_url_from_config({pot.CONFIG_BASE_URL_KEY: "127.0.0.1:9999"}),
            "http://127.0.0.1:9999",
        )
        self.assertEqual(
            pot.base_url_from_config({pot.CONFIG_BASE_URL_KEY: "http://evil.com"}),
            "",
        )


class ProbeTests(unittest.TestCase):
    def test_a_listening_endpoint_is_detected(self):
        server = _LoopbackServer()
        self.addCleanup(server.close)
        reachable, detail = pot.probe_provider(server.base_url, timeout=2)
        self.assertTrue(reachable, detail)
        self.assertIn(server.base_url, detail)

    def test_a_dead_endpoint_reports_cleanly(self):
        url = f"http://127.0.0.1:{_free_port()}"
        reachable, detail = pot.probe_provider(url, timeout=0.4)
        self.assertFalse(reachable)
        self.assertIn("No PO-token provider", detail)

    def test_a_non_loopback_endpoint_is_never_contacted(self):
        with mock.patch("socket.create_connection") as connect:
            reachable, detail = pot.probe_provider("http://example.com:4416")
        connect.assert_not_called()
        self.assertFalse(reachable)
        self.assertIn("loopback", detail)


class ExtractorArgTests(unittest.TestCase):
    def test_the_provider_arg_names_the_endpoint(self):
        args = pot.provider_extractor_args("http://127.0.0.1:4416")
        self.assertEqual(args[0], "--extractor-args")
        self.assertEqual(
            args[1], "youtube:getpot_bgutil_baseurl=http://127.0.0.1:4416",
        )

    def test_non_youtube_urls_get_nothing(self):
        self.assertEqual(
            pot.provider_extractor_args(
                "http://127.0.0.1:4416", url="https://www.twitch.tv/x",
            ),
            [],
        )
        self.assertTrue(
            pot.provider_extractor_args(
                "http://127.0.0.1:4416", url="https://youtu.be/x",
            )
        )

    def test_a_rejected_endpoint_yields_no_argument(self):
        self.assertEqual(pot.provider_extractor_args("http://evil.com"), [])

    def test_injection_requires_both_a_plugin_and_a_live_endpoint(self):
        pot.invalidate_status_cache()
        self.addCleanup(pot.invalidate_status_cache)
        config = {pot.CONFIG_BASE_URL_KEY: "http://127.0.0.1:4416"}
        cases = {
            (True, True): True,
            (True, False): False,
            (False, True): False,
            (False, False): False,
        }
        for (plugin, reachable), expected in cases.items():
            with self.subTest(plugin=plugin, reachable=reachable):
                pot.invalidate_status_cache()
                with mock.patch.object(
                    pot, "provider_status",
                    return_value={
                        "plugin_installed": plugin,
                        "plugin": "bgutil_ytdlp_pot_provider",
                        "base_url": "http://127.0.0.1:4416",
                        "reachable": reachable,
                        "sidecar_running": False,
                        "detail": "",
                        "usable": plugin and reachable,
                    },
                ):
                    args = pot.active_extractor_args(
                        "https://www.youtube.com/watch?v=x", config,
                    )
                self.assertEqual(bool(args), expected)

    def test_a_broken_provider_check_never_breaks_a_download(self):
        pot.invalidate_status_cache()
        self.addCleanup(pot.invalidate_status_cache)
        with mock.patch.object(
            pot, "provider_status", side_effect=RuntimeError("boom"),
        ):
            self.assertEqual(
                pot.active_extractor_args("https://youtu.be/x", {}), [],
            )

    def test_the_status_probe_is_cached_between_jobs(self):
        pot.invalidate_status_cache()
        self.addCleanup(pot.invalidate_status_cache)
        config = {pot.CONFIG_BASE_URL_KEY: "http://127.0.0.1:4416"}
        with mock.patch.object(
            pot, "provider_status", return_value={"usable": False, "base_url": ""},
        ) as probe:
            pot.cached_status(config)
            pot.cached_status(config)
            pot.cached_status(config)
        self.assertEqual(probe.call_count, 1)


class CommandInjectionTests(unittest.TestCase):
    """The provider argument must reach the real yt-dlp command builders."""

    def setUp(self):
        pot.invalidate_status_cache()
        self.addCleanup(pot.invalidate_status_cache)

    def _usable(self):
        return mock.patch.object(
            pot, "provider_status",
            return_value={
                "plugin_installed": True,
                "plugin": "bgutil_ytdlp_pot_provider",
                "base_url": "http://127.0.0.1:4416",
                "reachable": True,
                "sidecar_running": False,
                "detail": "",
                "usable": True,
            },
        )

    def test_the_resolve_command_carries_the_provider_argument(self):
        from streamkeep.extractors.ytdlp import YtDlpExtractor

        extractor = YtDlpExtractor()
        with self._usable():
            cmd = extractor._build_cmd("https://www.youtube.com/watch?v=x")
        self.assertIn(
            "youtube:getpot_bgutil_baseurl=http://127.0.0.1:4416", cmd,
        )

    def test_a_non_youtube_resolve_is_unchanged(self):
        from streamkeep.extractors.ytdlp import YtDlpExtractor

        extractor = YtDlpExtractor()
        with self._usable():
            cmd = extractor._build_cmd("https://www.twitch.tv/x")
        self.assertFalse(
            any("getpot_bgutil_baseurl" in str(part) for part in cmd)
        )

    def test_without_a_provider_the_command_is_unchanged(self):
        from streamkeep.extractors.ytdlp import YtDlpExtractor

        extractor = YtDlpExtractor()
        with mock.patch.object(
            pot, "provider_status",
            return_value={
                "plugin_installed": False, "plugin": "",
                "base_url": "http://127.0.0.1:4416", "reachable": False,
                "sidecar_running": False, "detail": "", "usable": False,
            },
        ):
            cmd = extractor._build_cmd("https://www.youtube.com/watch?v=x")
        self.assertFalse(
            any("getpot_bgutil_baseurl" in str(part) for part in cmd)
        )


class SetupLifecycleTests(unittest.TestCase):
    def setUp(self):
        pot.invalidate_status_cache()
        self.addCleanup(pot.invalidate_status_cache)

    def test_a_frozen_build_never_shells_out_to_pip(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertFalse(pot.can_install_locally())
            with mock.patch("subprocess.run") as run:
                ok, message = pot.install_plugin()
        run.assert_not_called()
        self.assertFalse(ok)
        self.assertIn("packaged build", message)

    def test_setup_steps_name_what_is_actually_missing(self):
        with mock.patch.object(
            pot, "provider_status",
            return_value={
                "plugin_installed": False, "plugin": "",
                "base_url": pot.DEFAULT_BASE_URL, "reachable": False,
                "sidecar_running": False, "detail": "", "usable": False,
            },
        ):
            steps = pot.setup_steps()
        rendered = "\n".join(steps)
        self.assertIn(pot.INSTALL_COMMAND, rendered)
        self.assertIn(pot.DEFAULT_BASE_URL, rendered)

    def test_setup_steps_say_so_when_nothing_is_needed(self):
        with mock.patch.object(
            pot, "provider_status",
            return_value={
                "plugin_installed": True, "plugin": "bgutil_ytdlp_pot_provider",
                "base_url": pot.DEFAULT_BASE_URL, "reachable": True,
                "sidecar_running": False, "detail": "", "usable": True,
            },
        ):
            steps = pot.setup_steps()
        self.assertEqual(len(steps), 1)
        self.assertIn("already use it", steps[0])

    def test_only_a_configured_resolvable_command_is_ever_run(self):
        self.assertEqual(pot.server_command({}), [])
        self.assertEqual(
            pot.server_command({pot.CONFIG_COMMAND_KEY: "definitely-not-a-binary"}),
            [],
        )
        with mock.patch("shutil.which", return_value="/usr/bin/docker"):
            self.assertEqual(
                pot.server_command({pot.CONFIG_COMMAND_KEY: "docker start bgutil"}),
                ["/usr/bin/docker", "start", "bgutil"],
            )

    def test_launching_without_a_command_explains_rather_than_guessing(self):
        url = f"http://127.0.0.1:{_free_port()}"
        ok, message = pot.launch_sidecar({pot.CONFIG_BASE_URL_KEY: url})
        self.assertFalse(ok)
        self.assertIn(pot.CONFIG_COMMAND_KEY, message)
        self.assertIn("docker run", message)

    def test_launching_is_skipped_when_a_provider_already_answers(self):
        server = _LoopbackServer()
        self.addCleanup(server.close)
        with mock.patch("subprocess.Popen") as popen:
            ok, message = pot.launch_sidecar(
                {pot.CONFIG_BASE_URL_KEY: server.base_url}
            )
        popen.assert_not_called()
        self.assertTrue(ok)
        self.assertIn("already answering", message)

    def test_stop_never_touches_a_provider_this_process_did_not_start(self):
        self.assertFalse(pot.sidecar_running())
        self.assertFalse(pot.stop_sidecar())

    def test_ensure_reports_success_once_the_endpoint_answers(self):
        server = _LoopbackServer()
        self.addCleanup(server.close)
        config = {pot.CONFIG_BASE_URL_KEY: server.base_url}
        with mock.patch(
            "streamkeep.extractors.ytdlp.youtube_pot_provider_status",
            return_value={
                "available": True,
                "provider": "bgutil_ytdlp_pot_provider",
                "detail": "",
            },
        ):
            ok, message = pot.ensure_provider(config)
        self.assertTrue(ok, message)
        self.assertIn(server.base_url, message)

    def test_ensure_falls_back_to_instructions_when_it_cannot_get_there(self):
        url = f"http://127.0.0.1:{_free_port()}"
        config = {pot.CONFIG_BASE_URL_KEY: url}
        with mock.patch(
            "streamkeep.extractors.ytdlp.youtube_pot_provider_status",
            return_value={"available": True, "provider": "x", "detail": ""},
        ):
            ok, message = pot.ensure_provider(config)
        self.assertFalse(ok)
        self.assertIn("docker run", message)


class HealthReportTests(unittest.TestCase):
    def test_an_installed_but_dead_provider_is_called_out(self):
        from streamkeep.extractors.ytdlp import youtube_health_report

        pot.invalidate_status_cache()
        self.addCleanup(pot.invalidate_status_cache)
        url = f"http://127.0.0.1:{_free_port()}"
        with mock.patch(
            "streamkeep.extractors.ytdlp.youtube_pot_provider_status",
            return_value={"available": True, "provider": "x", "detail": ""},
        ), mock.patch.object(
            pot, "base_url_from_config", return_value=url,
        ):
            report = youtube_health_report()
        self.assertFalse(report["pot_endpoint"]["reachable"])
        self.assertTrue(
            any("answering" in warning for warning in report["warnings"]),
            report["warnings"],
        )
