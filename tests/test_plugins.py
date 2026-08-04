import json
import sys
import tempfile
import threading
import time
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
            # never receives a process-wide import-path mutation.
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
                # The plugin is package-scoped through its module spec; its
                # directory is never appended to the global path.
                mod = sys.modules["sk_plugin_example_plugin"]
                self.assertEqual(
                    mod.PATH_DURING, original_sys_path
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
        self.assertIn("trust_review", data)
        self.assertEqual(len(data["trust_review"]["contract_fingerprint"]), 64)

    def test_contract_diagnostics_expose_reviewable_fields(self):
        manifest = {
            "id": "reviewable",
            "name": "Reviewable",
            "version": "1.2.3",
            "manifest_version": 2,
            "min_app_version": "4.0.0",
            "max_app_version": "5.0.0",
            "adapters": [{
                "type": "extractor",
                "entrypoint": "Extractor",
                "interface_version": 1,
                "permissions": ["network", "filesystem_read"],
                "dependencies": [
                    "json",
                    {"name": "requests", "minimum_version": "2.0.0"},
                ],
                "timeout_seconds": 12,
            }],
        }

        details = plugins.plugin_contract_details(manifest)

        self.assertEqual(details["permissions"], ["filesystem_read", "network"])
        self.assertEqual(
            details["dependencies"],
            [
                {"name": "json"},
                {"name": "requests", "minimum_version": "2.0.0"},
            ],
        )
        self.assertEqual(details["compatibility"]["range"], ">= 4.0.0 and <= 5.0.0")
        self.assertEqual(
            details["entrypoints"],
            [{
                "type": "extractor",
                "entrypoint": "Extractor",
                "interface_version": 1,
            }],
        )
        self.assertEqual(len(details["contract_fingerprint"]), 64)

    def test_permission_change_requires_new_trust_review(self):
        log_events = []
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            plugin_dir = base / "changed_plugin"
            plugin_dir.mkdir()
            manifest = plugin_dir / "plugin.json"
            payload = {
                "id": "changed",
                "name": "Changed Plugin",
                "version": "1.0.0",
                "manifest_version": 2,
                "enabled": True,
                "trusted": False,
                "adapters": [{
                    "type": "postprocess",
                    "entrypoint": "process",
                    "interface_version": 1,
                    "permissions": ["network"],
                    "dependencies": [],
                    "timeout_seconds": 5,
                }],
            }
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            (plugin_dir / "__init__.py").write_text(
                "def process(value, context=None):\n    return value\n",
                encoding="utf-8",
            )

            with mock.patch.object(plugins, "PLUGINS_DIR", base):
                self.assertTrue(plugins.mark_trusted("changed", True))
                approved = plugins.discover_plugins()[0]
                self.assertTrue(approved["trust_reviewed"])

                payload = json.loads(manifest.read_text(encoding="utf-8"))
                payload["adapters"][0]["permissions"].append("filesystem_write")
                manifest.write_text(json.dumps(payload), encoding="utf-8")
                changed = plugins.discover_plugins()[0]
                self.assertTrue(changed["trusted"])
                self.assertFalse(changed["trust_reviewed"])

                with mock.patch.object(plugins, "load_plugin") as load_plugin:
                    loaded, errors = plugins.load_all_plugins(log_events.append)

        self.assertEqual((loaded, errors), (0, 1))
        load_plugin.assert_not_called()
        self.assertTrue(any(
            "Skipped contract review required: changed" in event
            for event in log_events
        ))


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

    def test_validate_manifest_rejects_old_and_unversioned_adapter_contracts(self):
        old_errors = plugins.validate_manifest({
            "id": "x", "name": "X", "version": "1.0.0", "manifest_version": 0,
        })
        self.assertTrue(any("Unsupported manifest_version" in e for e in old_errors))
        adapter_errors = plugins.validate_manifest({
            "id": "x", "name": "X", "version": "1.0.0", "manifest_version": 2,
            "adapters": [{"type": "extractor", "entrypoint": "X"}],
        })
        self.assertTrue(any("interface_version" in e for e in adapter_errors))

    def test_validate_manifest_rejects_unknown_adapter_and_permission(self):
        errors = plugins.validate_manifest({
            "id": "x", "name": "X", "version": "1.0.0", "manifest_version": 2,
            "adapters": [{
                "type": "unknown", "entrypoint": "X", "interface_version": 1,
                "permissions": ["admin"], "dependencies": [], "timeout_seconds": 5,
            }],
        })
        self.assertTrue(any("unsupported type" in e for e in errors))
        self.assertTrue(any("unsupported permission" in e for e in errors))

    def test_diagnostics_report_missing_adapter_dependency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir) / "dependency_plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.json").write_text(json.dumps({
                "id": "dependency", "name": "Dependency", "version": "1.0.0",
                "manifest_version": 2, "adapters": [{
                    "type": "postprocess", "entrypoint": "run", "interface_version": 1,
                    "permissions": [], "dependencies": ["module_that_does_not_exist"],
                    "timeout_seconds": 5,
                }],
            }), encoding="utf-8")
            with mock.patch.object(plugins, "PLUGINS_DIR", Path(tmpdir)):
                found = plugins.discover_plugins()
        self.assertFalse(found[0]["enabled"])
        self.assertIn("Missing dependency", found[0]["error"])

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

    def test_sample_plugin_registers_all_adapter_types_and_executes_typed_contracts(self):
        fixture_dir = Path(__file__).parent / "fixtures"
        with mock.patch.object(plugins, "PLUGINS_DIR", fixture_dir):
            found = plugins.discover_plugins()
            sample = next(plugin for plugin in found if plugin["id"] == "sample-extractor")
            sample["trusted"] = True
            handles = plugins.load_plugin_adapters(sample)

        self.assertEqual(
            {handle.spec.adapter_type for handle in handles},
            {"extractor", "postprocess", "upload", "youtube_backend"},
        )
        by_type = {handle.spec.adapter_type: handle for handle in handles}
        extractor = plugins.execute_plugin_adapter(
            by_type["extractor"], "https://sample-streaming.example.com/video",
        )
        postprocess = plugins.execute_plugin_adapter(by_type["postprocess"], "clip.mp4")
        upload = plugins.execute_plugin_adapter(
            by_type["upload"], "clip.mp4", metadata={"title": "Sample"},
        )
        backend_health = plugins.execute_plugin_adapter(
            by_type["youtube_backend"],
            {"backend_url": "https://helper.example.invalid"},
            operation="health",
            required_permissions=("network",),
        )
        backend_solve = plugins.execute_plugin_adapter(
            by_type["youtube_backend"],
            {"url": "https://www.youtube.com/watch?v=sample"},
            operation="solve",
            required_permissions=("network",),
        )
        self.assertTrue(extractor.ok)
        self.assertEqual(extractor.code, "ok")
        self.assertTrue(postprocess.ok)
        self.assertTrue(upload.ok)
        self.assertEqual(upload.value["metadata"]["title"], "Sample")
        self.assertTrue(backend_health.ok)
        self.assertTrue(backend_health.value["reachable"])
        self.assertTrue(backend_solve.ok)
        self.assertEqual(
            backend_solve.value["extractor_args"][0], "--extractor-args"
        )

    def test_adapter_outcomes_enforce_permissions_timeout_and_cancellation(self):
        class SlowAdapter:
            def process(self, context=None):
                while True:
                    context.check_cancelled()
                    time.sleep(0.01)

        spec = plugins.PluginAdapterSpec(
            "test", "postprocess", "SlowAdapter", 1, (), (), 0.05, 2,
        )
        handle = plugins.PluginAdapterHandle(spec, sys.modules[__name__], SlowAdapter, {})
        denied = plugins.execute_plugin_adapter(
            handle, required_permissions=("filesystem_read",),
        )
        self.assertEqual(denied.code, "permission_denied")
        timed_out = plugins.execute_plugin_adapter(handle)
        self.assertEqual(timed_out.code, "timeout")

        cancelled = threading.Event()
        cancelled.set()
        result = plugins.execute_plugin_adapter(handle, cancel_event=cancelled)
        self.assertEqual(result.code, "cancelled")


if __name__ == "__main__":
    unittest.main()
