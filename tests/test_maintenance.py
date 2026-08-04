import json
from pathlib import Path

import pytest

from streamkeep import db, tags
from streamkeep.maintenance import (
    _order_file_renames, apply_maintenance, apply_retemplate, load_pending_plan,
    plan_maintenance, plan_retemplate, save_pending_plan,
)
from streamkeep.utils import TemplateRenderError


def _recording(root, name, *, platform="Twitch", channel="alpha", title="Show"):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "video.mp4").write_bytes(b"media")
    (folder / "metadata.json").write_text(json.dumps({
        "platform": platform, "channel": channel, "title": title,
        "url": f"https://example.test/{name}",
    }), encoding="utf-8")
    return folder


def _history(path, *, platform="Twitch", channel="alpha", title="Show"):
    return db.save_history_entry({
        "date": "2026-07-17T00:00:00+00:00", "platform": platform,
        "channel": channel, "title": title, "path": str(path),
    })


def _backup(path):
    Path(path).write_bytes(b"backup")
    return True, "created"


def test_preview_classifies_imports_moves_missing_health_and_persists(tmp_path, monkeypatch):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    moved_old = tmp_path / "gone" / "moved"
    missing_old = tmp_path / "gone" / "missing"
    moved_id = _history(moved_old, title="Moved")
    missing_id = _history(missing_old, title="Missing")
    moved_new = _recording(tmp_path, "moved-new", title="Moved")
    imported = _recording(tmp_path, "untracked", channel="beta", title="New")
    (imported / ".notes.md").write_text("preserve me", encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "prior.skbackup").write_bytes(b"prior")

    plan = plan_maintenance(tmp_path, config={
        "archive_backup_dir": str(backup_dir),
        "archive_disk_warning_gb": 0.001,
        "archive_disk_critical_gb": 0.0001,
    })
    kinds = [action.kind for action in plan.actions]
    assert kinds.count("move") == 1
    assert kinds.count("import") == 1
    assert kinds.count("remove_missing") == 1
    assert kinds.count("rebuild") == 1
    move = next(action for action in plan.actions if action.kind == "move")
    assert move.payload == {
        "history_id": moved_id, "old_path": str(moved_old),
        "new_path": str(moved_new),
    }
    removal = next(action for action in plan.actions if action.kind == "remove_missing")
    assert removal.payload["history_id"] == missing_id
    import_action = next(action for action in plan.actions if action.kind == "import")
    assert import_action.payload["path"] == str(imported)
    assert plan.diagnostics["database"]["quick_check"] == "ok"
    assert plan.diagnostics["scan"]["note_sidecars"] == 1
    assert plan.diagnostics["backup"]["status"] == "available"
    assert plan.diagnostics["library"] == {
        "rows": 2, "missing": 2, "untracked": 2, "moved": 1,
    }

    path = save_pending_plan(plan, config_dir=tmp_path / "state")
    restored = load_pending_plan(config_dir=tmp_path / "state")
    assert path.is_file()
    assert restored.to_dict() == plan.to_dict()


def test_apply_requires_exact_approval_creates_backup_and_audits_each_action(
    tmp_path, monkeypatch,
):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    missing = tmp_path / "missing"
    missing_id = _history(missing, title="Missing")
    imported = _recording(tmp_path, "imported", channel="beta", title="New")
    metadata_before = (imported / "metadata.json").read_bytes()
    backup_dir = tmp_path / "backups"
    plan = plan_maintenance(tmp_path, config={"archive_backup_dir": str(backup_dir)})
    approved = [action.action_id for action in plan.actions]
    ledger = tmp_path / "state" / "audit.jsonl"

    result = apply_maintenance(
        plan, approved, ledger_path=ledger, backup_fn=_backup,
        config_dir=tmp_path / "state",
    )
    assert result.status == "completed"
    assert result.applied == 3
    assert result.failed == 0
    assert Path(result.backup_path).is_file()
    history = db.load_history()
    assert {row["title"] for row in history} == {"New"}
    assert history[0]["path"] == str(imported)
    assert (imported / "metadata.json").read_bytes() == metadata_before
    assert all(row["id"] != missing_id for row in history)
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert records[0]["event"] == "apply_started"
    assert len([row for row in records if row["event"] == "action_applied"]) == 3
    assert records[-1]["event"] == "apply_finished"


def test_apply_refuses_stale_plan_without_backup_or_changes(tmp_path, monkeypatch):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    _recording(tmp_path, "first")
    plan = plan_maintenance(tmp_path)
    extra_id = _history(tmp_path / "still-present", title="Changed")
    called = []

    result = apply_maintenance(
        plan, [action.action_id for action in plan.actions],
        ledger_path=tmp_path / "audit.jsonl",
        backup_fn=lambda path: called.append(path),
        config_dir=tmp_path / "state",
    )
    assert result.status == "stale"
    assert called == []
    assert db.load_history()[0]["id"] == extra_id


def test_apply_detects_in_place_history_changes_after_preview(tmp_path, monkeypatch):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    row_id = _history(tmp_path / "missing", title="Before")
    plan = plan_maintenance(tmp_path)
    db.update_history_entry(row_id, {"title": "After"})
    result = apply_maintenance(
        plan, [action.action_id for action in plan.actions],
        ledger_path=tmp_path / "audit.jsonl", backup_fn=_backup,
        config_dir=tmp_path / "state",
    )
    assert result.status == "stale"


def test_cancelled_apply_stops_between_atomic_actions(tmp_path, monkeypatch):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    _recording(tmp_path, "first", title="One")
    _recording(tmp_path, "second", title="Two")
    plan = plan_maintenance(tmp_path, config={
        "archive_backup_dir": str(tmp_path / "backups")
    })
    checks = iter((False, True))
    result = apply_maintenance(
        plan, [action.action_id for action in plan.actions],
        cancel_fn=lambda: next(checks, True),
        ledger_path=tmp_path / "audit.jsonl", backup_fn=_backup,
        config_dir=tmp_path / "state",
    )
    assert result.status == "cancelled"
    assert result.applied == 1
    assert len(db.load_history()) == 1


def test_retemplate_preview_and_apply_moves_the_complete_recording_unit(
    tmp_path, monkeypatch,
):
    database = tmp_path / "library.db"
    tag_database = tmp_path / "tags.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(tags, "DB_PATH", tag_database)
    db.init_db()
    root = tmp_path / "archive"
    old = root / "legacy"
    old.mkdir(parents=True)
    (old / "video.mp4").write_bytes(b"media")
    (old / "video.nfo").write_text("nfo", encoding="utf-8")
    (old / "video.chapters.txt").write_text("00:00:00 Intro\n", encoding="utf-8")
    (old / "metadata.json").write_text(json.dumps({
        "platform": "Twitch", "channel": "alpha", "title": "Show",
        "downloaded_at": "2026-07-17T00:00:00+00:00",
    }), encoding="utf-8")
    (old / ".notes.md").write_text("keep these notes", encoding="utf-8")
    from streamkeep.verify import create_archive_manifest
    history_id = _history(old, platform="Twitch", channel="alpha", title="Show")
    manifest = create_archive_manifest(old, write_sidecar=True)
    db.save_archive_manifest(history_id, str(old), manifest)
    tag_conn = tags._connect()
    tags.tag_recording(tag_conn, str(old), "keep")
    tag_conn.close()
    published = db.publish_recording(history_id)
    plan = plan_retemplate(
        root, "{channel}/{year}", "{title}",
        config={"archive_backup_dir": str(tmp_path / "backups")},
    )
    action = next(item for item in plan.actions if item.kind == "retemplate")
    assert action.payload["new_path"] == str(root / "alpha" / "2026")
    assert {pair["new"] for pair in action.payload["file_renames"]} == {
        "Show.mp4", "Show.nfo", "Show.chapters.txt",
    }

    result = apply_retemplate(
        plan, [action.action_id], backup_fn=_backup,
        ledger_path=tmp_path / "audit.jsonl", config_dir=tmp_path / "state",
    )
    new = root / "alpha" / "2026"
    assert result.status == "completed"
    assert result.applied == 1
    assert not old.exists()
    assert (new / "Show.mp4").read_bytes() == b"media"
    assert (new / "Show.nfo").read_text(encoding="utf-8") == "nfo"
    assert (new / "Show.chapters.txt").is_file()
    assert (new / "metadata.json").is_file()
    assert (new / ".notes.md").read_text(encoding="utf-8") == "keep these notes"
    row = next(item for item in db.load_history() if item["id"] == history_id)
    assert row["path"] == str(new)
    stored_manifest = db.load_archive_manifest(history_id)
    assert stored_manifest["recording_path"] == str(new)
    assert stored_manifest["manifest"]["root"] == str(new)
    assert db.published_recording(published["share_id"])["path"] == str(new)
    tag_conn = tags._connect()
    assert tags.get_tags_for_recording(tag_conn, str(old)) == []
    assert tags.get_tags_for_recording(tag_conn, str(new)) == [("keep", "user")]
    tag_conn.close()
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert events[-2]["event"] == "action_applied"
    assert events[-2]["kind"] == "retemplate"


def test_retemplate_overlapping_renames_preserve_both_source_files(
    tmp_path, monkeypatch,
):
    database = tmp_path / "library.db"
    tag_database = tmp_path / "tags.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(tags, "DB_PATH", tag_database)
    db.init_db()
    root = tmp_path / "archive"
    old = root / "legacy"
    old.mkdir(parents=True)
    (old / "clip.mp4").write_bytes(b"clip bytes")
    (old / "Title.mp4").write_bytes(b"title bytes")
    (old / "metadata.json").write_text(json.dumps({
        "platform": "Twitch", "channel": "alpha", "title": "Show",
    }), encoding="utf-8")
    _history(old, title="Show")

    plan = plan_retemplate(root, "{title}", "Title")
    action = next(item for item in plan.actions if item.kind == "retemplate")
    # Simulate a persisted preview whose order follows casefold sorting. The
    # apply path must still protect the source at the destination.
    action.payload["file_renames"] = [
        {"old": "clip.mp4", "new": "Title.mp4"},
        {"old": "Title.mp4", "new": "Title_002.mp4"},
    ]

    result = apply_retemplate(
        plan, [action.action_id], backup_fn=_backup,
        ledger_path=tmp_path / "audit.jsonl", config_dir=tmp_path / "state",
    )
    new = root / "Show"
    assert result.status == "completed"
    assert (new / "Title.mp4").read_bytes() == b"clip bytes"
    assert (new / "Title_002.mp4").read_bytes() == b"title bytes"


def test_retemplate_preview_rejects_case_only_destination_collision():
    with pytest.raises(TemplateRenderError) as error:
        _order_file_renames([
            {"old": "clip.txt", "new": "Title.mp4"},
            {"old": "Title.txt", "new": "title.MP4"},
        ])
    assert error.value.code == "filename_collision"


def test_retemplate_preview_names_unorderable_filename_cycles():
    with pytest.raises(TemplateRenderError) as error:
        _order_file_renames([
            {"old": "clip.mp4", "new": "Title.mp4"},
            {"old": "Title.mp4", "new": "clip.mp4"},
        ])
    assert error.value.code == "filename_cycle"


def test_retemplate_refuses_reserved_names_and_duplicate_destinations(tmp_path, monkeypatch):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    root = tmp_path / "archive"
    first = _recording(root, "first", title="Same")
    second = _recording(root, "second", title="Same")
    _history(first, title="Same")
    _history(second, title="Same")
    reserved = _recording(root, "reserved", title="CON")
    _history(reserved, title="CON")
    plan = plan_retemplate(root, "{channel}", "{title}")
    conflicts = [item for item in plan.actions if item.kind == "retemplate_conflict"]
    assert len(conflicts) == 2
    reasons = {item.payload["reason_code"] for item in conflicts}
    assert "collision" in reasons
    assert "reserved_name" in reasons
    assert all(item.payload["status"] == "conflict" for item in conflicts)


def test_retemplate_cancel_and_database_failure_leave_each_item_untouched(
    tmp_path, monkeypatch,
):
    database = tmp_path / "library.db"
    tag_database = tmp_path / "tags.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(tags, "DB_PATH", tag_database)
    db.init_db()
    root = tmp_path / "archive"
    first = _recording(root, "first", title="One")
    second = _recording(root, "second", title="Two")
    _history(first, title="One")
    second_id = _history(second, title="Two")
    plan = plan_retemplate(root, "{title}", "{title}")
    actions = [item for item in plan.actions if item.kind == "retemplate"]
    result_checks = [False, True]
    result = apply_retemplate(
        plan, [item.action_id for item in actions],
        cancel_fn=lambda: result_checks.pop(0), backup_fn=_backup,
        ledger_path=tmp_path / "cancel-audit.jsonl",
        config_dir=tmp_path / "state",
    )
    assert result.status == "cancelled"
    assert result.applied == 1
    assert first.exists() is False
    assert second.exists()
    assert next(row for row in db.load_history() if row["id"] == second_id)["path"] == str(second)

    # Re-preview the untouched row, then make the canonical DB commit fail.
    fresh = plan_retemplate(root, "{title}", "{title}")
    fresh_action = next(
        item for item in fresh.actions
        if item.kind == "retemplate"
        and item.payload.get("old_path") == str(second)
        and item.payload.get("status") == "ready"
    )
    monkeypatch.setattr(
        db, "relocate_history_recording",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected db failure")),
    )
    result = apply_retemplate(
        fresh, [fresh_action.action_id], backup_fn=_backup,
        ledger_path=tmp_path / "failure-audit.jsonl", config_dir=tmp_path / "state",
    )
    assert result.failed == 1
    assert second.exists()
    assert next(row for row in db.load_history() if row["id"] == second_id)["path"] == str(second)
