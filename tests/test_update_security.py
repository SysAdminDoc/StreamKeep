import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from streamkeep.update_security import (
    UpdateSecurityError,
    canonical_json_bytes,
    certificate_sha256,
    sign_manifest_bytes,
    require_authenticode,
)
from streamkeep.updater import verify_release_document


def _certificate_pair(common_name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return key, cert


class UpdateSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key, cls.cert = _certificate_pair("StreamKeep Publisher")
        der = cls.cert.public_bytes(serialization.Encoding.DER)
        cls.signer = {
            "valid": True,
            "status": "Valid",
            "subject": cls.cert.subject.rfc4514_string(),
            "certificate_der": base64.b64encode(der).decode("ascii"),
            "certificate_sha256": certificate_sha256(cls.cert),
        }

    def _documents(self, *, version="4.31.8", sequence=43108, asset_url=None):
        binary_digest = "c" * 64
        manifest = {
            "schema_version": 1,
            "sequence": sequence,
            "version": version,
            "tag": f"v{version}",
            "assets": [{
                "name": "StreamKeep.exe",
                "format": "portable-exe",
                "size": 1234,
                "sha256": binary_digest,
                "signer_sha256": self.signer["certificate_sha256"],
            }],
        }
        manifest_bytes = canonical_json_bytes(manifest)
        signature_bytes = canonical_json_bytes(
            sign_manifest_bytes(manifest_bytes, self.key, self.cert)
        )
        url = asset_url or (
            f"https://github.com/SysAdminDoc/StreamKeep/releases/download/"
            f"v{version}/StreamKeep.exe"
        )
        release = {
            "tag_name": f"v{version}",
            "draft": False,
            "prerelease": False,
            "body": "Security update",
            "assets": [{
                "name": "StreamKeep.exe",
                "size": 1234,
                "browser_download_url": url,
            }],
        }
        return release, manifest_bytes, signature_bytes

    def test_valid_signed_release_is_mapped_to_exact_asset(self):
        release, manifest, signature = self._documents()
        payload = verify_release_document(
            release, manifest, signature, self.signer, "4.31.7", {}
        )
        self.assertTrue(payload["available"])
        self.assertEqual(payload["asset"]["name"], "StreamKeep.exe")
        self.assertEqual(payload["sequence"], 43108)

    def test_manifest_tampering_breaks_publisher_signature(self):
        release, manifest, signature = self._documents()
        tampered = manifest.replace(b"4.31.8", b"4.31.9")
        with self.assertRaisesRegex(UpdateSecurityError, "signature"):
            verify_release_document(
                release, tampered, signature, self.signer, "4.31.7", {}
            )

    def test_different_manifest_signer_is_rejected(self):
        release, manifest, _signature = self._documents()
        other_key, other_cert = _certificate_pair("Other Publisher")
        signature = canonical_json_bytes(
            sign_manifest_bytes(manifest, other_key, other_cert)
        )
        with self.assertRaisesRegex(UpdateSecurityError, "different publisher"):
            verify_release_document(
                release, manifest, signature, self.signer, "4.31.7", {}
            )

    def test_downgrade_or_current_version_is_rejected(self):
        release, manifest, signature = self._documents(version="4.31.7")
        with self.assertRaisesRegex(UpdateSecurityError, "replay or downgrade"):
            verify_release_document(
                release, manifest, signature, self.signer, "4.31.7", {}
            )

    def test_lower_sequence_replay_is_rejected(self):
        release, manifest, signature = self._documents(sequence=43108)
        state = {
            "highest_sequence": 43109,
            "highest_version": "4.31.9",
            "manifest_sha256": "d" * 64,
        }
        with self.assertRaisesRegex(UpdateSecurityError, "replayed or rolled back"):
            verify_release_document(
                release, manifest, signature, self.signer, "4.31.7", state
            )

    def test_same_sequence_with_different_manifest_is_rejected(self):
        release, manifest, signature = self._documents()
        state = {
            "highest_sequence": 43108,
            "highest_version": "4.31.8",
            "manifest_sha256": "d" * 64,
        }
        with self.assertRaisesRegex(UpdateSecurityError, "conflicts"):
            verify_release_document(
                release, manifest, signature, self.signer, "4.31.7", state
            )

    def test_release_path_substitution_is_rejected(self):
        bad_url = (
            "https://github.com/Attacker/StreamKeep/releases/download/"
            "v4.31.8/StreamKeep.exe"
        )
        release, manifest, signature = self._documents(asset_url=bad_url)
        with self.assertRaisesRegex(UpdateSecurityError, "path validation"):
            verify_release_document(
                release, manifest, signature, self.signer, "4.31.7", {}
            )

    def test_noncanonical_manifest_is_rejected_even_when_signed(self):
        release, canonical, _signature = self._documents()
        parsed = json.loads(canonical)
        noncanonical = json.dumps(parsed, indent=2).encode("utf-8")
        signature = canonical_json_bytes(
            sign_manifest_bytes(noncanonical, self.key, self.cert)
        )
        with self.assertRaisesRegex(UpdateSecurityError, "canonical"):
            verify_release_document(
                release, noncanonical, signature, self.signer, "4.31.7", {}
            )

    def test_msix_requires_the_same_valid_publisher_certificate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            package = Path(tmpdir) / "StreamKeep.msix"
            package.write_bytes(b"signed-package-placeholder")
            with mock.patch(
                "streamkeep.update_security.get_authenticode_info",
                return_value=self.signer,
            ):
                accepted = require_authenticode(
                    package,
                    expected_certificate_sha256=self.signer["certificate_sha256"],
                    asset_format="msix",
                )
                self.assertTrue(accepted["valid"])
                with self.assertRaisesRegex(UpdateSecurityError, "different publisher"):
                    require_authenticode(
                        package,
                        expected_certificate_sha256="f" * 64,
                        asset_format="msix",
                    )


if __name__ == "__main__":
    unittest.main()
