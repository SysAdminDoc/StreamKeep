import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import plugins


class PluginTests(unittest.TestCase):
    def test_discover_plugins_reports_invalid_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            plugin_dir = base / "bad_plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.json").write_text("{not json", encoding="utf-8")

            with mock.patch.object(plugins, "PLUGINS_DIR", base):
                found = plugins.discover_plugins()

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["id"], "bad_plugin")
        self.assertFalse(found[0]["enabled"])
        self.assertIn("Invalid plugin.json", found[0]["error"])

    def test_load_plugin_does_not_mutate_global_sys_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            plugin_dir = base / "example_plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.json").write_text(
                json.dumps({"id": "example", "enabled": True}),
                encoding="utf-8",
            )
            # Record sys.path from inside the plugin so we can prove the plugin
            # runs with its own directory importable but the global path is
            # restored afterward.
            (plugin_dir / "__init__.py").write_text(
                "import sys\nPATH_DURING = list(sys.path)\nLOADED = True\n",
                encoding="utf-8",
            )
            info = {
                "id": "example",
                "enabled": True,
                "path": str(plugin_dir),
                "version": "1.0.0",
            }

            original_sys_path = list(sys.path)
            try:
                loaded = plugins.load_plugin(info)
                self.assertTrue(loaded)
                # No plugin directory (or its parent) persists globally.
                self.assertEqual(sys.path, original_sys_path)
                self.assertNotIn(str(base), sys.path)
                self.assertNotIn(str(plugin_dir), sys.path)
                # The plugin's own directory was importable during execution,
                # appended at the end so it cannot shadow stdlib/app modules.
                mod = sys.modules["sk_plugin_example_plugin"]
                self.assertEqual(mod.PATH_DURING[-1], str(plugin_dir))
                self.assertEqual(
                    mod.PATH_DURING[:len(original_sys_path)], original_sys_path
                )
            finally:
                sys.path[:] = original_sys_path
                sys.modules.pop("sk_plugin_example_plugin", None)

    def test_load_all_plugins_skips_enabled_but_untrusted_plugins(self):
        log_events = []
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            plugin_dir = base / "example_plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.json").write_text(
                json.dumps({
                    "id": "example", "name": "Example", "version": "1.0.0",
                    "enabled": True, "trusted": False,
                }),
                encoding="utf-8",
            )
            (plugin_dir / "__init__.py").write_text("LOADED = True\n", encoding="utf-8")

            with mock.patch.object(plugins, "PLUGINS_DIR", base), \
                    mock.patch.object(plugins, "load_plugin") as load_plugin:
                loaded, errors = plugins.load_all_plugins(log_events.append)

        self.assertEqual((loaded, errors), (0, 0))
        load_plugin.assert_not_called()
        self.assertIn("[PLUGIN] Skipped untrusted: example", log_events)

    def test_mark_trusted_updates_manifest_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            plugin_dir = base / "example_plugin"
            plugin_dir.mkdir()
            manifest = plugin_dir / "plugin.json"
            manifest.write_text(
                json.dumps({"id": "example", "enabled": True, "trusted": False}),
                encoding="utf-8",
            )

            with mock.patch.object(plugins, "PLUGINS_DIR", base):
                updated = plugins.mark_trusted("example", True)

            data = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertTrue(updated)
        self.assertTrue(data["trusted"])


    def test_validate_manifest_rejects_missing_required_fields(self):
        errors = plugins.validate_manifest({"name": "X", "version": "1.0.0"})
        self.assertTrue(any("id" in e for e in errors))

    def test_validate_manifest_rejects_invalid_version(self):
        errors = plugins.validate_manifest(
            {"id": "x", "name": "X", "version": "not-semver"}
        )
        self.assertTrue(any("version format" in e for e in errors))

    def test_validate_manifest_rejects_future_manifest_version(self):
        errors = plugins.validate_manifest(
            {"id": "x", "name": "X", "version": "1.0.0", "manifest_version": 999}
        )
        self.assertTrue(any("Unsupported manifest_version" in e for e in errors))

    def test_validate_manifest_rejects_app_version_too_old(self):
        errors = plugins.validate_manifest(
            {"id": "x", "name": "X", "version": "1.0.0",
             "min_app_version": "999.0.0"}
        )
        self.assertTrue(any("Requires StreamKeep" in e for e in errors))

    def test_validate_manifest_accepts_valid_manifest(self):
        errors = plugins.validate_manifest(
            {"id": "x", "name": "X", "version": "1.0.0",
             "manifest_version": 1, "min_app_version": "4.0.0"}
        )
        self.assertEqual(errors, [])

    def test_discover_disables_plugin_with_incompatible_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            plugin_dir = base / "future_plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.json").write_text(
                json.dumps({
                    "id": "future", "name": "Future Plugin",
                    "version": "2.0.0", "manifest_version": 999,
                    "enabled": True,
                }),
                encoding="utf-8",
            )

            with mock.patch.object(plugins, "PLUGINS_DIR", base):
                found = plugins.discover_plugins()

        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["enabled"])
        self.assertIn("Unsupported manifest_version", found[0]["error"])

    def test_sample_plugin_fixture_has_valid_manifest(self):
        fixture_path = Path(__file__).parent / "fixtures" / "sample_plugin" / "plugin.json"
        self.assertTrue(fixture_path.is_file(), "Sample plugin fixture missing")
        with open(fixture_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        errors = plugins.validate_manifest(meta)
        self.assertEqual(errors, [], f"Sample plugin manifest errors: {errors}")

    def test_sample_plugin_loads_only_when_trusted(self):
        fixture_dir = Path(__file__).parent / "fixtures"
        with mock.patch.object(plugins, "PLUGINS_DIR", fixture_dir):
            found = plugins.discover_plugins()
        sample = [p for p in found if p["id"] == "sample-extractor"]
        self.assertEqual(len(sample), 1)
        self.assertFalse(sample[0]["trusted"])
        log_events = []
        with mock.patch.object(plugins, "PLUGINS_DIR", fixture_dir):
            loaded, errors = plugins.load_all_plugins(log_events.append)
        self.assertEqual(loaded, 0)


if __name__ == "__main__":
    unittest.main()
