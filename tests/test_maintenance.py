import json
from pathlib import Path

from streamkeep import db
from streamkeep.maintenance import (
    apply_maintenance, load_pending_plan, plan_maintenance, save_pending_plan,
)


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
