import unittest
from unittest import mock

from streamkeep import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_reports_missing_modules_without_installing(self):
        def fake_import(name):
            if name == "PyQt6":
                return object()
            raise ImportError(name)

        with mock.patch.object(
            bootstrap.importlib, "import_module", side_effect=fake_import
        ):
            status = bootstrap.bootstrap(include_optional=True)

        self.assertTrue(status["PyQt6"]["available"])
        self.assertFalse(status["yt_dlp"]["available"])
        self.assertNotIn("subprocess", bootstrap.__dict__)

    def test_cli_bootstrap_skips_optional_probes(self):
        with mock.patch.object(bootstrap.importlib, "import_module") as import_module:
            status = bootstrap.bootstrap(include_optional=False)

        self.assertEqual(set(status), {"PyQt6"})
        import_module.assert_called_once_with("PyQt6")


if __name__ == "__main__":
    unittest.main()
