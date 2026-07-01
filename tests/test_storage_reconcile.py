import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import db
from streamkeep.storage import reconcile_folders, import_folders


class ReconcileTests(unittest.TestCase):
    def _create_recording(self, tmpdir, name, *, metadata=None):
        rec_dir = Path(tmpdir) / name
        rec_dir.mkdir(parents=True, exist_ok=True)
        (rec_dir / "video.mp4").write_bytes(b"\x00" * 1024)
        if metadata:
            (rec_dir / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
        return str(rec_dir)

    def test_identifies_importable_folders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_recording(tmpdir, "new_recording", metadata={
                "platform": "Kick", "title": "New", "channel": "ch1",
            })
            result = reconcile_folders(tmpdir, existing_paths=set())
            self.assertEqual(len(result.importable), 1)
            self.assertEqual(result.importable[0].title, "New")

    def test_marks_existing_paths_as_duplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._create_recording(tmpdir, "existing", metadata={
                "platform": "Twitch", "title": "Old",
            })
            result = reconcile_folders(tmpdir, existing_paths={os.path.normpath(path)})
            self.assertEqual(len(result.duplicates), 1)
            self.assertEqual(len(result.importable), 0)

    def test_flags_missing_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_recording(tmpdir, "no_meta")
            result = reconcile_folders(tmpdir)
            self.assertEqual(len(result.missing_metadata), 1)
            self.assertEqual(len(result.importable), 1)

    def test_import_creates_history_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            self._create_recording(tmpdir, "rec1", metadata={
                "platform": "Kick", "title": "Stream 1", "channel": "ch1",
                "url": "https://kick.com/ch1/v1",
            })
            self._create_recording(tmpdir, "rec2", metadata={
                "platform": "Twitch", "title": "Stream 2", "channel": "ch2",
            })
            result = reconcile_folders(tmpdir)
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                imported, errors = import_folders(result.importable, db_module=db)
                history = db.load_history()
            self.assertEqual(imported, 2)
            self.assertEqual(len(errors), 0)
            self.assertEqual(len(history), 2)
            titles = {h["title"] for h in history}
            self.assertEqual(titles, {"Stream 1", "Stream 2"})

    def test_dry_run_does_not_modify_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            self._create_recording(tmpdir, "rec1", metadata={
                "platform": "Kick", "title": "Test",
            })
            result = reconcile_folders(tmpdir, dry_run=True)
            self.assertEqual(len(result.importable), 1)
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                history = db.load_history()
            self.assertEqual(len(history), 0)


if __name__ == "__main__":
    unittest.main()
