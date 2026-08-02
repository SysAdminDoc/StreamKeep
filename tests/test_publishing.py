import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

from streamkeep import db, feed, gallery


def _history(path, *, channel="Show"):
    return {
        "date": "2026-08-02 12:00",
        "platform": "Podcast",
        "title": "Episode",
        "channel": channel,
        "path": str(path),
        "url": "https://example.com/episode",
    }


def test_publishing_state_survives_database_reopen_and_revokes_cleanly():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "library.db"
        recording_dir = Path(tmpdir) / "recording"
        recording_dir.mkdir()
        (recording_dir / "episode.mp3").write_bytes(b"audio")
        with mock.patch.object(db, "DB_PATH", db_path):
            db.init_db()
            history_id = db.save_history_entry(_history(recording_dir))
            share = db.publish_recording(history_id)
            feed_row = db.publish_feed(channel="Show")

            assert share is not None
            assert len(share["share_id"]) == 32
            assert db.published_recording(share["share_id"])["id"] == history_id
            assert db.published_recordings_for_feed(feed_row["feed_id"])[0]["id"] == history_id

            db.init_db()
            assert db.published_recording_for_history(history_id)["share_id"] == share[
                "share_id"
            ]
            assert db.published_feed(feed_row["feed_id"])["channel"] == "Show"

            assert db.unpublish_recording(share_id=share["share_id"])
            assert db.published_recording(share["share_id"]) is None
            assert db.unpublish_feed(feed_row["feed_id"])
            assert db.published_feed(feed_row["feed_id"]) is None

        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        finally:
            conn.close()


def test_canonical_media_file_rejects_outside_paths_and_symlink_redirection():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "recording"
        root.mkdir()
        inside = root / "episode.mp4"
        inside.write_bytes(b"media")
        outside = Path(tmpdir) / "outside.mp4"
        outside.write_bytes(b"outside")

        assert gallery.canonical_media_file(root, inside.name) == str(inside.resolve())
        assert gallery.canonical_media_file(root, str(outside)) == ""
        assert gallery.canonical_media_file(root, "..\\outside.mp4") == ""

        link = root / "link.mp4"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            link = None
        if link is not None:
            assert gallery.find_media_file(root) == str(inside.resolve())


def test_rss_validates_base_url_and_uses_media_mime_type(tmp_path):
    media = tmp_path / "episode.opus"
    media.write_bytes(b"audio")
    share_id = "a" * 32
    xml = feed.generate_rss(
        [{
            "share_id": share_id,
            "title": "Episode & One",
            "media_path": str(media),
        }],
        "https://media.example",
    )
    assert f"/media/{share_id}" in xml
    assert 'type="audio/ogg"' in xml
    assert "Episode &amp; One" in xml

    for base_url in ("", "file:///private", "https://user:pass@example.com"):
        try:
            feed.generate_rss([], base_url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid RSS base URL: {base_url!r}")
