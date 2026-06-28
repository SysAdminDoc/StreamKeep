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

    def test_load_plugin_appends_parent_path_without_prepending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            plugin_dir = base / "example_plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.json").write_text(
                json.dumps({"id": "example", "enabled": True}),
                encoding="utf-8",
            )
            (plugin_dir / "__init__.py").write_text("LOADED = True\n", encoding="utf-8")
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
                self.assertEqual(sys.path[0], original_sys_path[0])
                self.assertEqual(sys.path[-1], str(base))
            finally:
                sys.path[:] = original_sys_path

    def test_load_all_plugins_skips_enabled_but_untrusted_plugins(self):
        log_events = []
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            plugin_dir = base / "example_plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.json").write_text(
                json.dumps({"id": "example", "enabled": True, "trusted": False}),
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


if __name__ == "__main__":
    unittest.main()
