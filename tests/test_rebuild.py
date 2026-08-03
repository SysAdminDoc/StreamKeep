import json
from pathlib import Path
from unittest import mock

from streamkeep import db, tags
from streamkeep.metadata import MetadataSaver
from streamkeep.models import QualityInfo, StreamInfo
from streamkeep.rebuild import (
    apply_library_rebuild,
    plan_library_rebuild,
)
from streamkeep.verify import create_archive_manifest


def _info():
    return StreamInfo(
        platform="Twitch",
        channel="RebuildChannel",
        title="Rebuild recording",
        source_id="vod:2468",
        webpage_url="https://www.twitch.tv/videos/2468",
        total_secs=180,
        qualities=[QualityInfo(name="source", resolution="1920x1080")],
    )


def _remove_sqlite_files(path):
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def test_preview_then_apply_rebuilds_history_tags_and_manifest(tmp_path):
    root = tmp_path / "library"
    recording = root / "Twitch" / "RebuildChannel" / "recording"
    recording.mkdir(parents=True)
    (recording / "recording.mp4").write_bytes(b"media")
    info = _info()
    MetadataSaver.save(str(recording), info)

    library_db = tmp_path / "config" / "library.db"
    tags_db = tmp_path / "config" / "tags.db"
    with mock.patch.object(db, "DB_PATH", library_db), mock.patch.object(
        tags, "DB_PATH", tags_db,
    ):
        db.init_db()
        tag_db = tags._connect()
        tags.auto_tag_recording(tag_db, str(recording), info=info)
        expected_tags = tags.get_tags_for_recording(tag_db, str(recording))
        tag_db.close()
        manifest = create_archive_manifest(recording)
        history_id = db.save_completed_recording({
            "date": "2026-08-03T00:00:00Z",
            "platform": "Twitch",
            "source_id": "vod:2468",
            "webpage_url": "https://www.twitch.tv/videos/2468",
            "title": "Rebuild recording",
            "channel": "RebuildChannel",
            "size": "5 B",
            "path": str(recording),
            "url": "https://www.twitch.tv/videos/2468",
        }, manifest=manifest)
        assert history_id

        # The preview is taken after the live index has been lost.  This is
        # the real recovery path and proves the plan does not depend on DB rows.
        _remove_sqlite_files(library_db)
        _remove_sqlite_files(tags_db)
        db.init_db()
        plan = plan_library_rebuild(root, db_module=db, tags_module=tags)
        assert plan.diagnostics["rebuild"] == 1
        assert plan.diagnostics["manifest_count"] == 1
        assert any(issue["kind"] == "history" for issue in plan.issues)

        def backup_stub(path):
            Path(path).write_bytes(b"backup")
            return True, "backup created"

        result = apply_library_rebuild(
            plan,
            db_module=db,
            tags_module=tags,
            backup_fn=backup_stub,
        )
        assert result.status == "completed"
        assert result.rebuilt == 1
        assert Path(result.backup_path).read_bytes() == b"backup"

        rebuilt = db.load_history()
        assert len(rebuilt) == 1
        assert rebuilt[0]["source_id"] == "vod:2468"
        rebuilt_manifest = db.load_archive_manifest(rebuilt[0]["id"])
        assert rebuilt_manifest["manifest"] == manifest
        rebuilt_tag_db = tags._connect()
        assert tags.get_tags_for_recording(
            rebuilt_tag_db, str(recording)
        ) == expected_tags
        rebuilt_tag_db.close()
        assert (recording / "recording.mp4").read_bytes() == b"media"
        assert json.loads(
            (recording / "metadata.json").read_text(encoding="utf-8")
        )["schema_version"] == 3


def test_legacy_sidecar_migrates_and_missing_state_is_explicit(tmp_path):
    root = tmp_path / "legacy"
    recording = root / "old"
    recording.mkdir(parents=True)
    (recording / "old.mp4").write_bytes(b"old")
    (recording / "metadata.json").write_text(json.dumps({
        "platform": "Twitch",
        "url": "https://www.twitch.tv/videos/1357",
        "title": "Legacy recording",
        "channel": "LegacyChannel",
    }), encoding="utf-8")
    library_db = tmp_path / "config" / "library.db"
    tags_db = tmp_path / "config" / "tags.db"
    with mock.patch.object(db, "DB_PATH", library_db), mock.patch.object(
        tags, "DB_PATH", tags_db,
    ):
        db.init_db()
        plan = plan_library_rebuild(root, db_module=db, tags_module=tags)
        assert plan.diagnostics["rebuild"] == 1
        assert plan.items[0]["record"]["source_id"] == "vod:1357"
        reasons = [issue["reason"] for issue in plan.issues]
        assert any("history fields" in reason for reason in reasons)
        assert any("manifest sidecar" in reason for reason in reasons)


def test_duplicate_identity_is_reviewable_and_stale_apply_does_not_mutate(tmp_path):
    root = tmp_path / "duplicates"
    for name in ("one", "two"):
        recording = root / name
        recording.mkdir(parents=True)
        (recording / "clip.mp4").write_bytes(name.encode())
        (recording / "metadata.json").write_text(json.dumps({
            "schema": "streamkeep.metadata",
            "schema_version": 3,
            "provenance": {
                "platform": "Twitch",
                "source_id": "vod:99",
                "webpage_url": "https://www.twitch.tv/videos/99",
            },
            "title": name,
        }), encoding="utf-8")
    library_db = tmp_path / "config" / "library.db"
    tags_db = tmp_path / "config" / "tags.db"
    with mock.patch.object(db, "DB_PATH", library_db), mock.patch.object(
        tags, "DB_PATH", tags_db,
    ):
        db.init_db()
        plan = plan_library_rebuild(root, db_module=db, tags_module=tags)
        assert plan.diagnostics["conflict"] == 2
        assert all(item["action"] == "conflict" for item in plan.items)
        db.save_history_entry({"title": "existing", "path": str(root)})
        result = apply_library_rebuild(
            plan,
            db_module=db,
            tags_module=tags,
            backup_fn=lambda path: (True, "unused"),
        )
        assert result.status == "stale"
        assert db.history_count() == 1
