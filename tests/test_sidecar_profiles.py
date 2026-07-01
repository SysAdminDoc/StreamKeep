import os
import tempfile
import unittest

from streamkeep.integrations.sidecar_profiles import (
    generate_sidecars,
    refresh_sidecars,
)
from streamkeep.models import StreamInfo, QualityInfo


def _make_info():
    return StreamInfo(
        title="Test Stream",
        url="https://example.com/video",
        platform="Test",
        channel="TestChannel",
        qualities=[QualityInfo(name="720p", url="https://example.com/v.mp4", format_type="mp4")],
        total_secs=3600,
        start_time="2026-07-01T12:00:00Z",
    )


class SidecarProfileTests(unittest.TestCase):
    def test_archive_profile_creates_json_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            info = _make_info()
            results = generate_sidecars(tmpdir, info, profile="archive")
            self.assertIn("metadata_json", results)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "metadata.json")))
            nfo_files = [f for f in os.listdir(tmpdir) if f.endswith(".nfo")]
            self.assertEqual(len(nfo_files), 0)

    def test_full_profile_creates_nfo_and_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            info = _make_info()
            results = generate_sidecars(tmpdir, info, profile="full")
            self.assertIn("metadata_json", results)
            self.assertIn("nfo", results)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "metadata.json")))
            nfo_files = [f for f in os.listdir(tmpdir) if f.endswith(".nfo")]
            self.assertGreater(len(nfo_files), 0)

    def test_none_profile_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            info = _make_info()
            results = generate_sidecars(tmpdir, info, profile="none")
            self.assertEqual(results, {})
            self.assertEqual(os.listdir(tmpdir), [])

    def test_no_overwrite_preserves_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            meta_path = os.path.join(tmpdir, "metadata.json")
            with open(meta_path, "w") as f:
                f.write('{"existing": true}')
            info = _make_info()
            results = generate_sidecars(tmpdir, info, profile="archive", overwrite=False)
            self.assertIsNone(results.get("metadata_json"))
            with open(meta_path) as f:
                content = f.read()
            self.assertIn("existing", content)

    def test_refresh_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            meta_path = os.path.join(tmpdir, "metadata.json")
            with open(meta_path, "w") as f:
                f.write('{"existing": true}')
            info = _make_info()
            results = refresh_sidecars(tmpdir, info, profile="archive")
            self.assertIsNotNone(results.get("metadata_json"))
            with open(meta_path) as f:
                content = f.read()
            self.assertIn("Test Stream", content)

    def test_unknown_profile_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            messages = []
            results = generate_sidecars(tmpdir, _make_info(), profile="bogus", log_fn=messages.append)
            self.assertEqual(results, {})
            self.assertTrue(any("Unknown profile" in m for m in messages))

    def test_idempotent_rerun_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            info = _make_info()
            r1 = generate_sidecars(tmpdir, info, profile="full")
            r2 = generate_sidecars(tmpdir, info, profile="full", overwrite=False)
            self.assertIsNotNone(r1.get("metadata_json"))
            self.assertIsNone(r2.get("metadata_json"))


if __name__ == "__main__":
    unittest.main()
