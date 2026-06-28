import unittest
from unittest import mock

from streamkeep import secrets


class SecretsTests(unittest.TestCase):
    def test_protect_requires_secure_backend_by_default(self):
        with mock.patch("streamkeep.secrets.sys.platform", "linux"), \
             mock.patch("streamkeep.secrets._keyring_set", return_value=False):
            with self.assertRaises(secrets.SecretStorageError):
                secrets.protect("secret", field_name="token")

    def test_insecure_fallback_must_be_explicit(self):
        with mock.patch("streamkeep.secrets.sys.platform", "linux"), \
             mock.patch("streamkeep.secrets._keyring_set", return_value=False):
            stored = secrets.protect(
                "secret",
                field_name="token",
                allow_insecure_fallback=True,
            )

        self.assertTrue(stored.startswith("b64:"))
        self.assertEqual(secrets.unprotect(stored), "secret")


if __name__ == "__main__":
    unittest.main()
