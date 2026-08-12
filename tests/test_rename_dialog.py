import json

from streamkeep.models import HistoryEntry
from streamkeep.ui import rename_dialog


def _operation(old_path, new_path, **extra):
    return {"old": str(old_path), "new": str(new_path), **extra}


def test_undo_restores_every_directory_in_the_latest_batch(tmp_path):
    journal = tmp_path / "rename_undo.json"
    operations = []
    for index in range(2):
        old_path = tmp_path / f"old-{index}"
        new_path = tmp_path / f"new-{index}"
        new_path.mkdir()
        operations.append(_operation(old_path, new_path))

    rename_dialog.record_rename_undo(operations, journal)
    result = rename_dialog.undo_last_rename(journal)

    assert len(result.restored) == 2
    assert result.failed == []
    assert all((tmp_path / f"old-{index}").is_dir() for index in range(2))
    assert not journal.exists(), "a fully consumed undo batch must be removed"


def test_partial_undo_keeps_only_failed_operations_for_retry(tmp_path):
    journal = tmp_path / "rename_undo.json"
    blocked_old = tmp_path / "blocked-old"
    blocked_new = tmp_path / "blocked-new"
    restored_old = tmp_path / "restored-old"
    restored_new = tmp_path / "restored-new"
    blocked_old.mkdir()
    blocked_new.mkdir()
    restored_new.mkdir()
    rename_dialog.record_rename_undo([
        _operation(blocked_old, blocked_new),
        _operation(restored_old, restored_new),
    ], journal)

    result = rename_dialog.undo_last_rename(journal)

    assert len(result.restored) == 1
    assert len(result.failed) == 1
    assert "occupied" in result.failed[0]["error"]
    pending = json.loads(journal.read_text(encoding="utf-8"))
    assert pending[-1]["ops"] == [_operation(blocked_old, blocked_new)]

    blocked_old.rmdir()
    retry = rename_dialog.undo_last_rename(journal)
    assert len(retry.restored) == 1
    assert blocked_old.is_dir()
    assert not journal.exists()


def test_undo_relocates_the_history_row_and_rolls_back_on_db_failure(tmp_path):
    journal = tmp_path / "rename_undo.json"
    old_path = tmp_path / "old"
    new_path = tmp_path / "new"
    new_path.mkdir()
    operation = _operation(
        old_path, new_path, db_id=42, history_relocated=True,
    )
    rename_dialog.record_rename_undo([operation], journal)
    calls = []

    def refuse(history_id, expected, destination):
        calls.append((history_id, expected, destination))
        raise RuntimeError("database is busy")

    result = rename_dialog.undo_last_rename(
        journal, relocate_history=refuse,
    )

    assert calls == [(42, str(new_path), str(old_path))]
    assert len(result.failed) == 1
    assert "database is busy" in result.failed[0]["error"]
    assert new_path.is_dir(), "filesystem move must roll back with the DB"
    assert not old_path.exists()

    retry = rename_dialog.undo_last_rename(
        journal, relocate_history=lambda *args: calls.append(args),
    )
    assert len(retry.restored) == 1
    assert old_path.is_dir()
    assert not journal.exists()


def test_rename_dialog_creates_a_replayable_undo_batch(
    qt_application, tmp_path, monkeypatch,
):
    journal = tmp_path / "rename_undo.json"
    recording = tmp_path / "before"
    recording.mkdir()
    entry = HistoryEntry(title="After", path=str(recording))
    monkeypatch.setattr(
        rename_dialog,
        "_rename_undo_path",
        lambda log_path=None: journal if log_path is None else log_path,
    )
    dialog = rename_dialog.RenameDialog(None, [entry])
    dialog.template_input.setText("after")

    dialog._on_rename()

    renamed = tmp_path / "after"
    assert renamed.is_dir()
    assert entry.path == str(renamed)
    assert rename_dialog.rename_undo_available(journal)
    result = rename_dialog.undo_last_rename(journal)
    assert len(result.restored) == 1
    assert recording.is_dir()


def test_main_window_reports_partial_rename_undo(monkeypatch):
    from streamkeep.ui.main_window import StreamKeep

    result = rename_dialog.RenameUndoResult(
        restored=[{"old": "one", "new": "two"}],
        failed=[{"old": "three", "new": "four", "error": "path is occupied"}],
    )
    monkeypatch.setattr(rename_dialog, "undo_last_rename", lambda: result)
    statuses = []
    toasts = []

    class Notifications:
        def __init__(self):
            self.entries = []

        def push(self, text, level="info"):
            self.entries.append((text, level))

        def items(self):
            return list(self.entries)

    class Surface:
        _notifications = Notifications()

        def _set_status(self, text, tone="idle"):
            statuses.append((text, tone))

        def _toast(self, text, level="info"):
            toasts.append((text, level))

        def _refresh_history_table(self):
            pass

        def _persist_config(self):
            pass

        def _refresh_notif_badge(self):
            pass

    surface = Surface()
    StreamKeep._on_undo_last_rename(surface)

    assert statuses[-1][1] == "warning"
    assert "Restored 1" in statuses[-1][0]
    assert "could not restore 1" in statuses[-1][0]
    assert toasts[-1][1] == "warning"
    assert any("could not restore 1" in text for text, _level in surface._notifications.items())
