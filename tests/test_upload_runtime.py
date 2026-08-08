import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import db
from streamkeep.upload.base import UploadDestination
from streamkeep.upload.runtime import UploadRuntime, save_profile


class UploadRuntimeTests(unittest.TestCase):
    def test_builtin_upload_adapters_are_registered_in_clean_process(self):
        repo_root = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json;"
                    "from streamkeep.upload import UploadDestination;"
                    "print(json.dumps(sorted(UploadDestination.all_adapters().keys())))"
                ),
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(
            json.loads(proc.stdout.strip()),
            ["FTP / SFTP", "S3 / B2 / MinIO", "WebDAV"],
        )

    def test_runtime_persists_failure_when_adapter_crashes(self):
        class BrokenAdapter:
            def __init__(self, config):
                self.config = config

            def upload(self, file_path, metadata=None, progress_cb=None):
                raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(db, "DB_PATH", Path(folder) / "library.db"), \
                 mock.patch.object(
                    UploadDestination,
                    "all_adapters",
                    return_value={"Broken": BrokenAdapter},
                 ), mock.patch("streamkeep.upload.runtime.delete_secret_value"), \
                 mock.patch("streamkeep.upload.runtime.set_secret_value", return_value=""):
                db.init_db()
                save_profile("broken", "Broken", {})
                source = Path(folder) / "clip.bin"
                source.write_bytes(b"payload")
                job = db.create_upload_job("broken", "Broken", str(source))
                UploadRuntime()._run(job["upload_id"])
                result = db.load_upload_job(job["upload_id"])
            self.assertEqual(result["status"], "retryable")
            self.assertIn("Upload crashed: boom", result["last_error"])


if __name__ == "__main__":
    unittest.main()
