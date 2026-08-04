import json
import sqlite3
import zipfile

from streamkeep import backup, db


def _entry(tmp_path, *, favorite=False, watched=False, position=0.0):
    return {
        "date": "2026-08-04T12:00:00Z",
        "platform": "Twitch",
        "source_id": "vod:history-actions",
        "webpage_url": "https://www.twitch.tv/videos/history-actions",
        "title": "Action log recording",
        "channel": "ActionChannel",
        "quality": "source",
        "size": "10 B",
        "path": str(tmp_path / "recording"),
        "url": "https://www.twitch.tv/videos/history-actions",
        "favorite": favorite,
        "watched": watched,
        "watch_position_secs": position,
        "bookmarks": [],
    }


def test_history_actions_rebuild_projection_and_delete_event(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "library.db")
    db.init_db()
    history_id = db.save_completed_recording(_entry(tmp_path))

    assert db.history_action_count() == 1
    first = db.load_history_actions(history_id=history_id)
    assert first[0]["action"] == "snapshot"
    assert first[0]["value"]["favorite"] is False

    db.update_history_entry(
        history_id,
        {
            "favorite": True,
            "watched": True,
            "watch_position_secs": 91.5,
            "bookmarks": [{"position": 12.0, "label": "intro"}],
        },
    )
    connection = db._connect()
    try:
        connection.execute(
            "UPDATE history SET favorite=0, watched=0, "
            "watch_position_secs=0, bookmarks='[]' WHERE id=?",
            (history_id,),
        )
        connection.commit()
    finally:
        connection.close()

    replayed = db.replay_history_actions()
    assert replayed["applied"] == 1
    restored = db.load_history()[0]
    assert restored["favorite"] is True
    assert restored["watched"] is True
    assert restored["watch_position_secs"] == 91.5
    assert restored["bookmarks"] == [{"position": 12.0, "label": "intro"}]

    db.delete_history_entries([history_id])
    assert db.load_history() == []
    actions = db.load_history_actions(history_id=history_id)
    assert actions[0]["action"] == "delete"
    assert db.list_tombstones()[0]["source_id"] == "vod:history-actions"


def test_history_actions_compact_to_latest_active_projection(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "library.db")
    db.init_db()
    history_id = db.save_completed_recording(_entry(tmp_path))
    for position in range(1, 7):
        db.update_history_entry(
            history_id, {"watch_position_secs": float(position)}
        )

    assert db.history_action_count() == 7
    removed = db.compact_history_actions(max_rows=1)
    assert removed == 6
    assert db.history_action_count() == 1
    assert db.replay_history_actions()["applied"] == 1
    assert db.load_history()[0]["watch_position_secs"] == 6.0


def test_schema_v20_migration_seeds_existing_history_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "library.db")
    db.init_db()
    history_id = db.save_completed_recording(_entry(tmp_path, watched=True))
    connection = db._connect()
    try:
        connection.execute("DROP TABLE history_actions")
        connection.execute("PRAGMA user_version = 19")
        connection.commit()
    finally:
        connection.close()

    db.init_db()
    actions = db.load_history_actions(history_id=history_id)
    assert len(actions) == 1
    assert actions[0]["value"]["watched"] is True


def test_rebuild_replays_state_by_identity_when_row_ids_are_recreated(
    tmp_path, monkeypatch,
):
    live_db = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", live_db)
    db.init_db()
    entry = _entry(tmp_path)
    history_id = db.save_completed_recording(entry)
    db.update_history_entry(
        history_id,
        {"favorite": True, "watched": True, "watch_position_secs": 44.0},
    )

    target = tmp_path / "rebuilt.db"
    db.build_rebuilt_library_database(target, [entry])
    connection = sqlite3.connect(target)
    try:
        row = connection.execute(
            "SELECT favorite, watched, watch_position_secs FROM history"
        ).fetchone()
    finally:
        connection.close()
    assert row == (1, 1, 44.0)


def test_restore_replays_staged_library_action_log(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    live_db = config_dir / "library.db"
    monkeypatch.setattr(db, "DB_PATH", live_db)
    db.init_db()
    history_id = db.save_completed_recording(
        _entry(tmp_path, favorite=True)
    )

    backup_path = tmp_path / "state.skbackup"
    monkeypatch.setattr(backup, "CONFIG_DIR", config_dir)
    ok, detail = backup.create_backup(backup_path)
    assert ok, detail

    staged_db = tmp_path / "staged.db"
    with zipfile.ZipFile(backup_path, "r") as archive:
        staged_db.write_bytes(archive.read("library.db"))
        metadata = archive.read("_backup_meta.json")
    connection = sqlite3.connect(staged_db)
    try:
        connection.execute(
            "UPDATE history SET favorite=0 WHERE id=?", (history_id,)
        )
        connection.commit()
    finally:
        connection.close()
    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("_backup_meta.json", metadata)
        archive.write(staged_db, "library.db")

    restored, message = backup.restore_backup(backup_path)
    assert restored, message
    connection = sqlite3.connect(live_db)
    try:
        assert connection.execute(
            "SELECT favorite FROM history WHERE id=?", (history_id,)
        ).fetchone()[0] == 1
        action_payload = connection.execute(
            "SELECT value_json FROM history_actions WHERE history_id=? "
            "ORDER BY id DESC LIMIT 1", (history_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    assert json.loads(action_payload)["favorite"] is True
