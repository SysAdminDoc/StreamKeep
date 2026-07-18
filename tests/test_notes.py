import tempfile
import unittest
from pathlib import Path

from streamkeep import notes


class NotesTests(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(notes.save_notes(tmpdir, "hello world"))
            self.assertEqual(notes.load_notes(tmpdir), "hello world")
            self.assertTrue(notes.has_notes(tmpdir))

    def test_load_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(notes.load_notes(tmpdir), "")
            self.assertFalse(notes.has_notes(tmpdir))

    def test_delete_notes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            notes.save_notes(tmpdir, "content")
            self.assertTrue(notes.delete_notes(tmpdir))
            self.assertFalse(notes.has_notes(tmpdir))

    def test_delete_missing_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(notes.delete_notes(tmpdir))

    def test_notes_path_empty(self):
        self.assertEqual(notes.notes_path(""), "")
        self.assertEqual(notes.notes_path(None), "")

    def test_save_empty_dir_returns_false(self):
        self.assertFalse(notes.save_notes("", "text"))

    def test_generate_template(self):
        tmpl = notes.generate_template(
            title="Test Video", channel="xQc", platform="Twitch",
        )
        self.assertIn("# Test Video", tmpl)
        self.assertIn("**Channel:** xQc", tmpl)
        self.assertIn("**Platform:** Twitch", tmpl)

    def test_search_notes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d1 = str(Path(tmpdir) / "rec1")
            d2 = str(Path(tmpdir) / "rec2")
            notes.save_notes(d1, "found the bug here")
            notes.save_notes(d2, "nothing interesting")

            results = notes.search_notes([d1, d2], "bug")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0], d1)

    def test_search_empty_query(self):
        self.assertEqual(notes.search_notes(["/tmp"], ""), [])


if __name__ == "__main__":
    unittest.main()
