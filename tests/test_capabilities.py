import sys
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import capabilities


def _record(name, *, supported=True, available=True, version="1.0.0",
            minimum="1.0.0", path=r"C:\Tools\tool.exe", repair="Repair tool."):
    return capabilities._base_record(
        name,
        name,
        "executable",
        minimum,
        ["test-operation"],
        repair,
        path=path,
        version=version,
        available=available,
        supported=supported,
        command=[path] if path else [],
        provenance="test-fixture" if available else "missing",
    )


class CapabilityRegistryTests(unittest.TestCase):
    def test_security_floors_are_release_contract(self):
        self.assertEqual(capabilities.MINIMUM_VERSIONS, {
            "yt_dlp": "2026.07.04",
            "pillow": "12.3.0",
            "curl": "8.21.0",
            "ffmpeg": "8.1.2",
            "ffprobe": "8.1.2",
        })

    def test_calendar_and_semantic_versions_compare_numerically(self):
        self.assertTrue(capabilities.version_at_least("2026.7.4", "2026.07.04"))
        self.assertFalse(capabilities.version_at_least("2026.6.9", "2026.07.04"))
        self.assertTrue(capabilities.version_at_least("ffmpeg 8.1.2-full", "8.1.2"))
        self.assertFalse(capabilities.version_at_least("curl 8.19.0", "8.21.0"))
        self.assertFalse(capabilities.version_at_least("unknown", "8.21.0"))

    def test_unsafe_tool_is_blocked_with_repair_guidance(self):
        unsafe = _record(
            "curl", supported=False, version="8.19.0", minimum="8.21.0",
            path=r"C:\Windows\System32\curl.exe",
            repair="Install curl 8.21.0 or newer.",
        )
        with mock.patch.object(
                capabilities, "get_runtime_capabilities",
                return_value={"curl": unsafe},
        ):
            with self.assertRaises(capabilities.CapabilityUnavailableError) as raised:
                capabilities.resolve_tool_command("curl")

        self.assertEqual(raised.exception.record["path"], unsafe["path"])
        self.assertIn("8.19.0", str(raised.exception))
        self.assertIn("Install curl 8.21.0", str(raised.exception))

    def test_parallel_download_never_starts_blocked_curl(self):
        from streamkeep import http

        unsafe = _record(
            "curl", supported=False, version="8.19.0", minimum="8.21.0",
            repair="Install curl 8.21.0 or newer.",
        )
        error = capabilities.CapabilityUnavailableError(unsafe)
        logs = []
        with mock.patch.object(
                http, "resolve_tool_command", side_effect=error,
        ), mock.patch.object(http.subprocess, "Popen") as popen:
            ok = http.parallel_http_download(
                "https://example.com/video.mp4",
                "blocked.mp4",
                log_fn=logs.append,
            )

        self.assertFalse(ok)
        popen.assert_not_called()
        self.assertTrue(any("Install curl 8.21.0" in line for line in logs))

    def test_supported_tool_resolves_only_the_recorded_exact_path(self):
        safe = _record("curl", version="8.21.0", minimum="8.21.0")
        with mock.patch.object(
                capabilities, "get_runtime_capabilities",
                return_value={"curl": safe},
        ):
            self.assertEqual(
                capabilities.resolve_tool_command("curl"),
                r"C:\Tools\tool.exe",
            )

    def test_executable_probe_records_version_path_and_provenance(self):
        with mock.patch.object(
                capabilities.shutil, "which", return_value=r"C:\Tools\curl.exe",
        ), mock.patch.object(
                capabilities, "_run_version_command",
                return_value=("curl 8.21.0 libcurl/8.21.0", 0),
        ):
            record = capabilities._probe_executable(
                "curl", ["curl"], ["--version"], "8.21.0",
                ["https-fetch"], "Repair curl.",
            )

        self.assertEqual(record["version"], "8.21.0")
        self.assertEqual(record["path"], str(Path(r"C:\Tools\curl.exe").resolve()))
        self.assertEqual(record["provenance"], "PATH")
        self.assertTrue(record["supported"])
        self.assertEqual(record["command"], [record["path"]])

    def test_javascript_selection_is_deno_first_and_deterministic(self):
        calls = []

        def probe(_name, commands, _args, minimum, capabilities_list, repair,
                  *, display_name=None):
            calls.append(list(commands))
            return _record(
                "javascript", version="2.7.11", minimum=minimum,
                path=r"C:\Tools\deno.exe", repair=repair,
            )

        with mock.patch.object(capabilities, "_probe_executable", side_effect=probe):
            record = capabilities._probe_javascript_runtime()

        self.assertEqual(record["runtime"], "deno")
        self.assertEqual(record["path"], r"C:\Tools\deno.exe")
        self.assertEqual(calls, [["deno"]])

    def test_javascript_falls_through_to_supported_node(self):
        calls = []

        def probe(_name, commands, _args, minimum, capabilities_list, repair,
                  *, display_name=None):
            del capabilities_list, display_name
            calls.append(list(commands))
            if commands == ["deno"]:
                return _record(
                    "javascript", supported=False, version="1.0.0",
                    minimum=minimum, path=r"C:\Tools\deno.exe", repair=repair,
                )
            return _record(
                "javascript", version="22.3.0", minimum=minimum,
                path=r"C:\Tools\node.exe", repair=repair,
            )

        with mock.patch.object(capabilities, "_probe_executable", side_effect=probe):
            record = capabilities._probe_javascript_runtime()

        self.assertEqual(record["runtime"], "node")
        self.assertEqual(calls, [["deno"], ["node", "nodejs"]])

    def test_ejs_must_exactly_match_ytdlp_requirement(self):
        ejs = _record(
            "yt_dlp_ejs", version="0.9.0", minimum="",
            path=r"C:\Python\yt_dlp_ejs\__init__.py",
        )
        with mock.patch.object(capabilities, "_probe_module", return_value=ejs), \
                mock.patch.object(
                    capabilities, "_yt_dlp_ejs_requirement", return_value="==0.8.0"
                ):
            record = capabilities._probe_ejs({"available": True})

        self.assertFalse(record["supported"])
        self.assertEqual(record["required_by_ytdlp"], "==0.8.0")

    def test_source_and_frozen_ytdlp_commands_are_explicit(self):
        module = _record(
            "yt_dlp", version="2026.7.4", minimum="2026.07.04",
            path=r"C:\Python\yt_dlp\__init__.py",
        )
        with mock.patch.object(capabilities, "_probe_module", return_value=dict(module)), \
                mock.patch.object(sys, "frozen", False, create=True):
            source = capabilities._probe_yt_dlp()
        self.assertEqual(source["command"], [sys.executable, "-m", "yt_dlp"])

        with mock.patch.object(capabilities, "_probe_module", return_value=dict(module)), \
                mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"C:\Apps\StreamKeep.exe"):
            frozen = capabilities._probe_yt_dlp()
        self.assertEqual(
            frozen["command"],
            [r"C:\Apps\StreamKeep.exe", "--internal-ytdlp"],
        )

    def test_every_registry_record_has_identity_and_capability_fields(self):
        registry = {
            name: _record(name)
            for name in (
                "sqlite", "yt_dlp", "yt_dlp_ejs", "javascript", "youtube",
                "pillow", "curl", "ffmpeg", "ffprobe",
            )
        }
        with mock.patch.object(capabilities, "_probe_registry", return_value=registry):
            found = capabilities.get_runtime_capabilities(refresh=True)

        required = {
            "name", "display_name", "kind", "path", "version", "minimum",
            "provenance", "available", "supported", "capabilities", "command",
            "repair", "detail", "state",
        }
        for record in found.values():
            self.assertTrue(required.issubset(record))

    def test_vulnerable_source_sqlite_is_reported_as_safe_degraded_mode(self):
        with mock.patch.object(
                capabilities, "sqlite_runtime_status",
                return_value={
                    "version": "3.45.1",
                    "minimum": "3.51.3 or patched 3.50.7/3.44.6",
                    "supported": True,
                    "frozen": False,
                    "detail": "rollback journaling is enforced",
                    "wal_reset_fixed": False,
                    "degraded": True,
                    "journal_mode": "delete",
                },
        ):
            record = capabilities._probe_sqlite_runtime()

        self.assertTrue(record["supported"])
        self.assertEqual(record["state"], "degraded")
        self.assertEqual(record["journal_mode"], "delete")
        self.assertFalse(record["wal_reset_fixed"])


class ReleaseFloorTests(unittest.TestCase):
    def test_python_and_flatpak_inputs_cannot_regress_security_floors(self):
        root = Path(__file__).resolve().parents[1]
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        flatpak = (
            root / "packaging" / "flatpak" /
            "com.github.SysAdminDoc.StreamKeep.yml"
        ).read_text(encoding="utf-8")
        flatpak_lock = (
            root / "packaging" / "flatpak" / "requirements.lock"
        ).read_text(encoding="utf-8")
        spec = (root / "StreamKeep.spec").read_text(encoding="utf-8")

        self.assertIn("Pillow>=12.3.0", requirements)
        self.assertIn("yt-dlp[default]>=2026.07.04", requirements)
        self.assertRegex(flatpak_lock, r"(?m)^yt-dlp==2026\.7\.4 \\")
        self.assertRegex(flatpak_lock, r"(?m)^pillow==12\.3\.0 \\")
        self.assertIn("python3-requirements.json", flatpak)
        self.assertIn("copy_metadata('yt-dlp-ejs')", spec)
        self.assertIn("Frozen builds require fixed SQLite", spec)
        self.assertIn("ffmpeg-8.1.2.tar.xz", flatpak)
        self.assertIn(
            "464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c",
            flatpak,
        )

    def test_launcher_does_not_bypass_registry_or_block_degraded_startup(self):
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "StreamKeep.py").read_text(encoding="utf-8")
        self.assertNotIn('["ffmpeg", "-version"]', launcher)
        self.assertNotIn("pip install", launcher)


if __name__ == "__main__":
    unittest.main()
