import sys
import unittest
from unittest import mock

from streamkeep import cookies


class CookieTests(unittest.TestCase):
    def test_sanitize_cookie_field_strips_row_breakers(self):
        cleaned = cookies._sanitize_cookie_field("a\tb\r\nc")
        self.assertEqual(cleaned, "a b c")


class CookieFailureDiagnosisTests(unittest.TestCase):
    """V149 — the loader exception decides the message, not a fallback."""

    def test_a_locked_store_names_the_browser_the_file_and_the_remedy(self):
        error = PermissionError(13, "Access is denied")
        error.filename = r"C:\Users\x\AppData\Local\Google\Chrome\Cookies"
        message = cookies.describe_cookie_read_failure("chrome", error)
        self.assertIn("Google Chrome", message)
        self.assertIn("Chrome\\Cookies", message)
        self.assertIn("locked", message)
        self.assertIn("Close Google Chrome", message)
        self.assertIn("cookies.txt", message)
        self.assertNotIn("No cookie loader found", message)

    def test_app_bound_encryption_sends_the_user_to_an_exported_file(self):
        message = cookies.describe_cookie_read_failure(
            "edge", RuntimeError("failed to decrypt v20 app-bound cookie"),
        )
        self.assertIn("Microsoft Edge", message)
        self.assertIn("bound to the browser", message)
        self.assertIn("cookies.txt", message)

    def test_a_missing_profile_is_reported_as_missing(self):
        error = FileNotFoundError(2, "No such file or directory")
        error.filename = "/home/x/.mozilla/firefox/cookies.sqlite"
        message = cookies.describe_cookie_read_failure("firefox", error)
        self.assertIn("No Firefox cookie store was found", message)
        self.assertIn("cookies.sqlite", message)

    def test_an_unclassified_error_still_carries_its_text(self):
        message = cookies.describe_cookie_read_failure(
            "brave", ValueError("some novel loader bug"),
        )
        self.assertIn("Brave", message)
        self.assertIn("some novel loader bug", message)

    def test_an_unknown_browser_name_does_not_produce_an_empty_subject(self):
        message = cookies.describe_cookie_read_failure("", OSError("boom"))
        self.assertIn("the selected browser", message)


class CookieImportBranchTests(unittest.TestCase):
    """The two failure branches must not be reported as each other."""

    def _run_import(self, rookiepy=None, bc3=None):
        modules = {}
        if rookiepy is not None:
            modules["rookiepy"] = rookiepy
        if bc3 is not None:
            modules["browser_cookie3"] = bc3
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name in ("rookiepy", "browser_cookie3"):
                if name in modules:
                    return modules[name]
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        with mock.patch.dict(sys.modules), \
                mock.patch("builtins.__import__", side_effect=fake_import):
            for name in ("rookiepy", "browser_cookie3"):
                sys.modules.pop(name, None)
            return cookies.import_from_browser("chrome")

    def test_no_importable_loader_keeps_the_install_message(self):
        ok, message = self._run_import()
        self.assertFalse(ok)
        self.assertIn("No cookie loader found", message)

    def test_a_locked_store_does_not_claim_the_loader_is_missing(self):
        error = PermissionError(13, "Access is denied")
        error.filename = r"C:\Chrome\Cookies"
        rookiepy = mock.Mock()
        rookiepy.chrome = mock.Mock(side_effect=error)
        ok, message = self._run_import(rookiepy=rookiepy)
        self.assertFalse(ok)
        self.assertNotIn("No cookie loader found", message)
        self.assertIn("locked", message)
        self.assertIn("Google Chrome", message)

    def test_the_fallback_loader_is_tried_before_reporting(self):
        rookiepy = mock.Mock()
        rookiepy.chrome = mock.Mock(side_effect=RuntimeError("rookie failed"))
        bc3 = mock.Mock()
        bc3.chrome = mock.Mock(return_value=[])
        with mock.patch.object(
            cookies, "_write_cookies", return_value=(True, "wrote 0"),
        ) as write:
            ok, message = self._run_import(rookiepy=rookiepy, bc3=bc3)
        self.assertTrue(ok, message)
        bc3.chrome.assert_called_once()
        write.assert_called_once()

    def test_a_loader_without_the_browser_is_not_a_read_failure(self):
        """rookiepy has no `safari` on Windows; that is not a locked store."""
        rookiepy = mock.Mock(spec=[])
        ok, message = self._run_import(rookiepy=rookiepy)
        self.assertFalse(ok)
        self.assertIn("No cookie loader found", message)


if __name__ == "__main__":
    unittest.main()
