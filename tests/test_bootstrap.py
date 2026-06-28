import unittest
from unittest import mock

from streamkeep import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_cli_bootstrap_skips_optional_dependency_installs(self):
        def fake_import(name):
            if name == "PyQt6":
                return object()
            raise ImportError(name)

        with mock.patch.object(bootstrap, "_is_frozen", return_value=False), \
                mock.patch.object(bootstrap.subprocess, "check_call") as check_call:
            with mock.patch("importlib.import_module", side_effect=fake_import):
                bootstrap.bootstrap(include_optional=False)

        check_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
