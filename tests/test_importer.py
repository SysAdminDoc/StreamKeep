import json
from pathlib import Path

from streamkeep import db
from streamkeep.importer import apply_adoption, preview_adoption


def _metadata(
    folder, *, platform="Twitch", source_id="vod:123", channel="channel",
    title="Imported",
):
    (folder / "metadata.json").write_text(json.dumps({
        "provenance": {
            "platform": platform,
            "source_id": source_id,
            "webpage_url": (
                "https://www.twitch.tv/videos/"
                f"{source_id.rsplit(':', 1)[-1]}"
            ),
        },
        "title": title,
        "channel": channel,
        "downloaded_at": "2026-08-01T12:00:00+00:00",
    }), encoding="utf-8")


def _media(folder, name="video.mp4"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"media-bytes")
    return path


def test_preview_classifies_sidecars_duplicates_and_archive_lines(
    tmp_path, monkeypatch,
):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    db.save_monitor_channel({
        "url": "https://www.twitch.tv/channel",
        "platform": "Twitch",
        "channel_id": "channel",
    })
    adopted = tmp_path / "library" / "one"
    _media(adopted)
    _metadata(adopted)
    duplicate = tmp_path / "library" / "duplicate"
    _media(duplicate)
    _metadata(duplicate)
    info = tmp_path / "library" / "youtube"
    _media(info, "clip.webm")
    (info / "clip.webm.info.json").write_text(json.dumps({
        "id": "abc123",
        "extractor_key": "Youtube",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "title": "YouTube import",
        "channel": "creator",
        "upload_date": "20260801",
    }), encoding="utf-8")
    no_sidecar = tmp_path / "library" / "unknown"
    _media(no_sidecar)
    archive = tmp_path / "archive.txt"
    archive.write_text(
        "twitch vod:999\nnot-an-archive-line\n# comment\n",
        encoding="utf-8",
    )

    plan = preview_adoption(tmp_path / "library", [archive], db_module=db)

    actions = {Path(item["path"]).name: item["action"] for item in plan.items}
    assert actions == {
        "one": "conflict", "duplicate": "conflict",
        "youtube": "adopt", "unknown": "conflict",
    }
    assert plan.diagnostics["adopt"] == 1
    assert plan.diagnostics["conflict"] == 3
    assert len(plan.archive_entries) == 2
    assert len(plan.archive_issues) == 1
    assert plan.monitor_archive_seeds["https://www.twitch.tv/channel"] == [
        "twitch::vod:999",
    ]
    assert not (database.parent / "download-archives").exists()
    assert db.load_history() == []


def test_apply_adoption_is_atomic_and_preserves_media(tmp_path, monkeypatch):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    db.save_monitor_channel({
        "url": "https://www.twitch.tv/channel",
        "platform": "Twitch",
        "channel_id": "channel",
    })
    folder = tmp_path / "library" / "recording"
    media = _media(folder)
    _metadata(folder)
    archive = tmp_path / "archive.txt"
    archive.write_text("twitch vod:999\n", encoding="utf-8")
    media_before = media.read_bytes()
    metadata_before = (folder / "metadata.json").read_bytes()
    plan = preview_adoption(folder.parent, [archive], db_module=db)

    def backup_fn(path):
        Path(path).write_bytes(b"backup")
        return True, "created"

    result = apply_adoption(plan, db_module=db, backup_fn=backup_fn)

    assert result.status == "completed"
    assert result.adopted == 1
    assert result.archive_files == 1
    assert len(db.load_history()) == 1
    assert db.load_monitor_channels()[0]["archive_ids"] == [
        "twitch::vod:123", "twitch::vod:999",
    ]
    archive_files = list((database.parent / "download-archives").glob("*.txt"))
    assert len(archive_files) == 1
    archive_text = archive_files[0].read_text(encoding="utf-8")
    assert "twitch vod:123" in archive_text
    assert "twitch vod:999" in archive_text
    assert media.read_bytes() == media_before
    assert (folder / "metadata.json").read_bytes() == metadata_before


def test_cancelled_or_stale_apply_changes_nothing(tmp_path, monkeypatch):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    folder = tmp_path / "library" / "recording"
    _media(folder)
    _metadata(folder)
    plan = preview_adoption(folder.parent, db_module=db)
    cancelled = apply_adoption(plan, db_module=db, cancel_fn=lambda: True)
    assert cancelled.status == "cancelled"
    assert db.load_history() == []
    assert not (database.parent / "download-archives").exists()

    db.save_history_entry({
        "platform": "Twitch", "source_id": "vod:other",
        "webpage_url": "https://www.twitch.tv/videos/other",
        "title": "Changed after preview", "path": str(tmp_path / "other"),
    })
    stale = apply_adoption(plan, db_module=db, backup_fn=lambda *_: (True, "ok"))
    assert stale.status == "stale"
    assert len(db.load_history()) == 1
    assert not (database.parent / "download-archives").exists()


def test_nfo_sidecar_recovers_identity_without_rewriting_the_nfo(tmp_path, monkeypatch):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    folder = tmp_path / "library" / "nfo-recording"
    _media(folder)
    nfo = folder / "movie.nfo"
    nfo.write_text(
        "<?xml version=\"1.0\"?><movie>"
        "<title>NFO title</title><studio>Twitch</studio>"
        "<uniqueid type=\"twitch\">vod:77</uniqueid>"
        "<director>nfo-channel</director></movie>",
        encoding="utf-8",
    )
    nfo_before = nfo.read_bytes()

    plan = preview_adoption(folder.parent, db_module=db)

    assert plan.items[0]["action"] == "adopt"
    assert plan.items[0]["record"]["source_id"] == "vod:77"
    assert plan.items[0]["record"]["title"] == "NFO title"
    assert apply_adoption(
        plan, db_module=db,
        backup_fn=lambda path: (Path(path).write_bytes(b"backup") or True, "ok"),
    ).status == "completed"
    assert nfo.read_bytes() == nfo_before


def test_preview_walks_arbitrarily_deep_media_trees(tmp_path, monkeypatch):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    folder = tmp_path / "library" / "a" / "b" / "c" / "d" / "e" / "recording"
    _media(folder)
    _metadata(folder, source_id="vod:deep", title="Deep recording")

    plan = preview_adoption(folder.parents[5], db_module=db)

    assert len(plan.items) == 1
    assert plan.items[0]["action"] == "adopt"
    assert plan.items[0]["record"]["source_id"] == "vod:deep"
