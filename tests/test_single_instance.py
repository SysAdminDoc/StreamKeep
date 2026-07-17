from pathlib import Path

from streamkeep.single_instance import LOCK_FILENAME, acquire_gui_instance_lock


def test_gui_instance_lock_rejects_duplicate_and_recovers_after_unlock(tmp_path):
    first = acquire_gui_instance_lock(tmp_path)
    assert first is not None
    try:
        assert (tmp_path / LOCK_FILENAME).is_file()
        assert acquire_gui_instance_lock(tmp_path) is None
    finally:
        first.unlock()

    replacement = acquire_gui_instance_lock(tmp_path)
    assert replacement is not None
    replacement.unlock()


def test_launcher_acquires_instance_guard_before_persistent_window_state():
    launcher = (Path(__file__).resolve().parents[1] / "StreamKeep.py").read_text(
        encoding="utf-8"
    )

    acquire = launcher.index("instance_lock = acquire_gui_instance_lock(CONFIG_DIR)")
    construct = launcher.index("win = StreamKeep()")
    assert acquire < construct
