"""Windows onedir distribution and unsigned installer (V35)."""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "packaging") not in sys.path:
    sys.path.insert(0, str(ROOT / "packaging"))

import build as build_mod  # noqa: E402
import winget_hash  # noqa: E402


class SpecShapeTests(unittest.TestCase):
    def setUp(self):
        self.spec = (ROOT / "StreamKeep.spec").read_text(encoding="utf-8")

    def test_onedir_is_the_default_and_onefile_is_opt_in(self):
        self.assertIn("STREAMKEEP_ONEFILE", self.spec)
        self.assertIn("COLLECT(", self.spec)
        # The default must be onedir: the env var selects the legacy shape.
        self.assertRegex(
            self.spec, r"onedir\s*=\s*os\.environ\.get\('STREAMKEEP_ONEFILE'",
        )

    def test_the_onedir_exe_excludes_the_bundled_payload(self):
        self.assertIn("exclude_binaries=onedir", self.spec)

    def test_no_signing_identity_is_configured(self):
        self.assertIn("codesign_identity=None", self.spec)
        self.assertNotIn("signtool", self.spec.casefold())


class BuildDriverTests(unittest.TestCase):
    def test_the_build_defaults_to_onedir(self):
        seen = {}

        def fake_call(command, cwd=None, env=None):
            seen["env"] = dict(env or {})
            return 0

        with mock.patch.object(build_mod, "stamp_versions"), \
                mock.patch.object(build_mod.subprocess, "call", fake_call):
            build_mod.main([])
        self.assertNotIn("STREAMKEEP_ONEFILE", seen["env"])

    def test_catalogs_are_compiled_before_pyinstaller(self):
        commands = []

        def fake_call(command, cwd=None, env=None):
            commands.append([str(part) for part in command])
            return 0

        with mock.patch.object(build_mod, "stamp_versions"), \
                mock.patch.object(build_mod.subprocess, "call", fake_call):
            self.assertEqual(build_mod.main([]), 0)

        self.assertIn("streamkeep.i18n.compile_translations", commands[0])
        self.assertIn("--check", commands[0])
        self.assertIn("PyInstaller", commands[1])

    def test_a_failed_catalog_compile_never_reaches_pyinstaller(self):
        with mock.patch.object(build_mod, "stamp_versions"), \
                mock.patch.object(build_mod.subprocess, "call", return_value=2) as call:
            self.assertEqual(build_mod.main([]), 2)

        self.assertEqual(call.call_count, 1)

    def test_onefile_is_requested_explicitly(self):
        seen = {}

        def fake_call(command, cwd=None, env=None):
            seen["env"] = dict(env or {})
            return 0

        with mock.patch.object(build_mod, "stamp_versions"), \
                mock.patch.object(build_mod.subprocess, "call", fake_call):
            build_mod.main(["--onefile"])
        self.assertEqual(seen["env"].get("STREAMKEEP_ONEFILE"), "1")

    def test_an_installer_is_never_built_from_a_onefile_build(self):
        with mock.patch.object(build_mod, "stamp_versions"), \
                mock.patch.object(build_mod.subprocess, "call", return_value=0), \
                mock.patch.object(build_mod, "build_installer") as installer:
            code = build_mod.main(["--onefile", "--installer"])
        installer.assert_not_called()
        self.assertEqual(code, 1)

    def test_a_failed_pyinstaller_run_never_reaches_the_installer(self):
        with mock.patch.object(build_mod, "stamp_versions"), \
                mock.patch.object(build_mod.subprocess, "call", side_effect=[0, 3]), \
                mock.patch.object(build_mod, "build_installer") as installer:
            code = build_mod.main(["--installer"])
        installer.assert_not_called()
        self.assertEqual(code, 3)

    def test_a_missing_onedir_tree_is_reported_not_silently_skipped(self):
        self.assertEqual(
            build_mod.build_installer(ROOT / "does-not-exist", ROOT / "dist"), 1,
        )


class InstallerScriptTests(unittest.TestCase):
    def setUp(self):
        self.iss = (
            ROOT / "packaging" / "installer" / "streamkeep.iss"
        ).read_text(encoding="utf-8")

    def test_the_installer_is_unsigned(self):
        lowered = self.iss.casefold()
        # Inno signs only through these directives; the word may legitimately
        # appear in a comment that explains no signing is performed.
        for directive in ("signtool=", "signeduninstaller=", "signtoolretrycount="):
            self.assertNotIn(directive, lowered)
        self.assertIn("unsigned", lowered)

    def test_it_installs_the_whole_tree_not_one_executable(self):
        self.assertIn("recursesubdirs", self.iss)
        self.assertIn("createallsubdirs", self.iss)

    def test_it_supports_silent_install_for_package_managers(self):
        # WinGet drives Inno installers with these switches.
        manifest = (
            ROOT / "packaging" / "winget" / "SysAdminDoc.StreamKeep.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("/VERYSILENT", manifest)
        self.assertIn("InstallerType: inno", manifest)

    def test_uninstall_never_touches_the_user_profile(self):
        # Only files under {app} may be removed; the library, history, and
        # queue live in the user profile and must survive an uninstall.
        for line in self.iss.splitlines():
            if line.strip().startswith("Type: filesandordirs"):
                self.assertIn("{app}", line)


class WingetManifestTests(unittest.TestCase):
    def setUp(self):
        self.path = ROOT / "packaging" / "winget" / "SysAdminDoc.StreamKeep.yaml"
        self.text = self.path.read_text(encoding="utf-8")

    def test_the_version_matches_the_package(self):
        from streamkeep import VERSION

        self.assertIn(f"PackageVersion: {VERSION}", self.text)
        self.assertIn(f"/v{VERSION}/", self.text)

    def test_the_stale_placeholder_hash_is_gone(self):
        self.assertNotIn("PLACEHOLDER_SHA256_HASH", self.text)
        self.assertNotIn("4.38.0", self.text)

    def test_the_hash_field_is_a_real_64_character_field(self):
        match = re.search(r"InstallerSha256: ([0-9A-Fa-f]{64})", self.text)
        self.assertIsNotNone(match)
        # Unfilled is fine in the repo, but it must be recognisable as such.
        self.assertTrue(winget_hash.is_placeholder(match.group(1)))

    def test_versioning_stamps_the_manifest(self):
        from versioning import version_drift

        self.assertEqual(
            [d for d in version_drift(ROOT) if "winget" in d], [],
        )


class WingetHashHelperTests(unittest.TestCase):
    def test_a_real_hash_is_not_a_placeholder(self):
        self.assertFalse(winget_hash.is_placeholder("a" * 64))
        self.assertTrue(winget_hash.is_placeholder("0" * 64))
        self.assertTrue(winget_hash.is_placeholder(""))

    def test_writing_a_hash_replaces_the_placeholder(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.yaml"
            manifest.write_text(
                "Installers:\n  - Architecture: x64\n"
                f"    InstallerSha256: {'0' * 64}\n", encoding="utf-8",
            )
            digest = "A" * 64
            self.assertTrue(winget_hash.write_hash(digest, manifest))
            self.assertEqual(winget_hash.manifest_hash(manifest), digest)
            # Idempotent: writing the same hash again is not a change.
            self.assertFalse(winget_hash.write_hash(digest, manifest))

    def test_a_manifest_without_the_field_is_refused(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.yaml"
            manifest.write_text("Installers: []\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                winget_hash.write_hash("b" * 64, manifest)


class UpdaterDistributionTests(unittest.TestCase):
    def test_a_directory_install_is_recognised(self):
        import tempfile

        from streamkeep import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "StreamKeep.exe"
            exe.write_bytes(b"x")
            self.assertFalse(updater.is_directory_install(exe))
            (root / "_internal").mkdir()
            self.assertTrue(updater.is_directory_install(exe))

    def test_self_replacement_refuses_a_directory_install(self):
        import tempfile

        from streamkeep import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "StreamKeep.exe"
            exe.write_bytes(b"x")
            (root / "_internal").mkdir()
            staged = root / "StreamKeep.exe.new"
            staged.write_bytes(b"y")
            with mock.patch.object(sys, "executable", str(exe)), \
                    mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(updater.subprocess, "Popen") as popen:
                self.assertFalse(updater.arm_self_replace(staged, {}))
            popen.assert_not_called()

    def test_the_refusal_explains_how_to_update_instead(self):
        import tempfile

        from streamkeep import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "StreamKeep.exe"
            exe.write_bytes(b"x")
            (root / "_internal").mkdir()
            with mock.patch.object(sys, "frozen", True, create=True):
                reason = updater.self_replace_unavailable_reason(exe)
        self.assertIn("SHA-256", reason)
        self.assertIn("package manager", reason)

    def test_a_source_checkout_says_self_update_is_frozen_only(self):
        from streamkeep import updater

        reason = updater.self_replace_unavailable_reason()
        self.assertIn("frozen", reason)
