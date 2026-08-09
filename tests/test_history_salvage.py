from pathlib import Path
from unittest import mock

from streamkeep import workers
from streamkeep.live_capture import salvage_target
from streamkeep.ui.tabs.history import HistoryTabMixin
from streamkeep.workers import salvage as salvage_module


def _staging_dir(tmp_path, name):
    staging = tmp_path / f"{name}.rawcapture"
    staging.mkdir()
    fragment = staging / f"{name}.part1.ts"
    fragment.write_bytes(b"raw-fragment")
    return staging, fragment


def test_salvage_worker_reports_each_item_and_preserves_raw_fragments(
    tmp_path, monkeypatch,
):
    first, first_fragment = _staging_dir(tmp_path, "first")
    second, second_fragment = _staging_dir(tmp_path, "second")
    original = {
        first_fragment: first_fragment.read_bytes(),
        second_fragment: second_fragment.read_bytes(),
    }
    progress = []
    results = []

    monkeypatch.setattr(salvage_module, "resolve_tool_command", lambda _name: "ffmpeg")

    def fake_ffmpeg(worker, command):
        Path(command[-1]).write_bytes(b"salvaged")
        return 0, ""

    monkeypatch.setattr(salvage_module.SalvageWorker, "_run_ffmpeg", fake_ffmpeg)
    worker = salvage_module.SalvageWorker([first, second])
    worker.progress.connect(lambda index, total, status: progress.append(
        (index, total, status)
    ))
    worker.done.connect(results.append)

    worker.run()

    assert results == [{
        "built": 2,
        "failed": 0,
        "skipped": 0,
        "cancelled": False,
        "error": "",
        "total": 2,
    }]
    assert [item[:2] for item in progress] == [(1, 2), (2, 2)]
    assert Path(salvage_target(first)).read_bytes() == b"salvaged"
    assert Path(salvage_target(second)).read_bytes() == b"salvaged"
    assert first_fragment.read_bytes() == original[first_fragment]
    assert second_fragment.read_bytes() == original[second_fragment]


def test_salvage_worker_cancel_stops_before_the_next_item(tmp_path, monkeypatch):
    first, _first_fragment = _staging_dir(tmp_path, "first")
    second, _second_fragment = _staging_dir(tmp_path, "second")
    progress = []
    results = []

    monkeypatch.setattr(salvage_module, "resolve_tool_command", lambda _name: "ffmpeg")

    def cancel_ffmpeg(worker, _command):
        worker.cancel()
        return None, ""

    monkeypatch.setattr(salvage_module.SalvageWorker, "_run_ffmpeg", cancel_ffmpeg)
    worker = salvage_module.SalvageWorker([first, second])
    worker.progress.connect(lambda index, total, status: progress.append(
        (index, total, status)
    ))
    worker.done.connect(results.append)

    worker.run()

    assert results[0]["cancelled"] is True
    assert results[0]["built"] == 0
    assert len(progress) == 1
    assert not Path(salvage_target(first)).exists()
    assert not Path(salvage_target(second)).exists()


class _HistoryOwner(HistoryTabMixin):
    def __init__(self):
        self.statuses = []
        self.notifications = []
        self.logs = []

    def _set_status(self, text, level):
        self.statuses.append((text, level))

    def _notify_center(self, text, level):
        self.notifications.append((text, level))

    def _log(self, text):
        self.logs.append(text)


def test_history_starts_owned_worker_and_surfaces_failure(monkeypatch):
    owner = _HistoryOwner()
    fake_worker = mock.Mock()
    fake_worker.isRunning.return_value = False
    monkeypatch.setattr(workers, "SalvageWorker", lambda paths: fake_worker)

    owner._salvage_raw_captures(["one.rawcapture", "two.rawcapture"])

    fake_worker.start.assert_called_once_with()
    assert owner._salvage_worker is fake_worker
    assert owner.statuses[-1][1] == "processing"

    owner._on_salvage_done({"built": 0, "failed": 1})

    assert owner._salvage_worker is None
    assert owner.statuses[-1][1] == "warning"
    assert owner.notifications[-1][1] == "error"
