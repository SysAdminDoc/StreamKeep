"""Release-startup contract tests (V220)."""

import json

import pytest

from streamkeep import startup_check


def _complete_outcomes():
    return {
        "scheduler": {"state": "completed"},
        "resume_scan": {"state": "completed"},
        "transcript_index": {"state": "started"},
        "health_probe": {"state": "completed"},
        "companion_server": {"state": "disabled"},
        "update_check": {"state": "suppressed"},
        "tray_icon": {"state": "suppressed"},
    }


def test_fixture_count_mismatch_is_rejected():
    expected = {"history": 1, "monitor_channels": 1, "download_queue": 1}

    assert startup_check._fixture_counts_match(expected, expected)
    assert not startup_check._fixture_counts_match(
        {**expected, "history": 0}, expected,
    )
    assert not startup_check._fixture_counts_match({}, expected)


def test_every_required_startup_subsystem_has_an_independent_check():
    checks = startup_check._startup_contract_checks(_complete_outcomes())

    assert checks
    assert all(checks.values())
    assert set(checks) == {
        "startup_scheduler_applied",
        "startup_resume_scan_completed",
        "startup_transcript_index_started",
        "startup_health_probe_bounded",
        "startup_companion_evaluated",
        "startup_update_network_suppressed",
        "startup_tray_suppressed",
    }


def test_a_broken_health_probe_or_resume_scan_fails_the_contract():
    outcomes = _complete_outcomes()
    outcomes["health_probe"] = {"state": "failed"}
    outcomes["resume_scan"] = {"state": "failed"}

    checks = startup_check._startup_contract_checks(outcomes)

    assert not checks["startup_health_probe_bounded"]
    assert not checks["startup_resume_scan_completed"]
    assert not all(checks.values())


def test_the_contract_cleanly_cancels_a_health_probe_at_its_deadline():
    class Window:
        def __init__(self):
            self.outcomes = _complete_outcomes()
            self.outcomes.pop("health_probe")
            self._health_worker = Worker(self)

        def startup_outcomes(self):
            return {name: dict(value) for name, value in self.outcomes.items()}

    class Worker:
        def __init__(self, window):
            self.window = window
            self.cancelled = False

        def isRunning(self):
            return True

        def cancel(self):
            self.cancelled = True

        def wait(self, _timeout):
            self.window.outcomes["health_probe"] = {"state": "cancelled"}
            return True

    class App:
        def processEvents(self):
            pass

    window = Window()
    outcomes = startup_check._wait_for_startup_contract(App(), window, timeout=0)

    assert window._health_worker.cancelled
    assert outcomes["health_probe"]["state"] == "cancelled"
    assert all(startup_check._startup_contract_checks(outcomes).values())


def test_the_contract_pumps_events_until_subsystems_finish():
    class Window:
        def __init__(self):
            self.outcomes = _complete_outcomes()
            self.outcomes["scheduler"] = {"state": "running"}
            self._health_worker = None

        def startup_outcomes(self):
            return {name: dict(value) for name, value in self.outcomes.items()}

    window = Window()

    class App:
        def processEvents(self):
            window.outcomes["scheduler"] = {"state": "completed"}

    outcomes = startup_check._wait_for_startup_contract(App(), window, timeout=1)

    assert outcomes["scheduler"]["state"] == "completed"


def test_empty_and_migrated_fixtures_are_written_to_fresh_profiles(tmp_path):
    empty = tmp_path / "empty"
    startup_check._prepare_fixture(empty, "empty")
    empty_config = json.loads((empty / "config.json").read_text(encoding="utf-8"))
    assert empty_config["first_run_complete"] is True
    assert "history" not in empty_config

    migrated = tmp_path / "migrated"
    startup_check._prepare_fixture(migrated, "migrated")
    migrated_config = json.loads(
        (migrated / "config.json").read_text(encoding="utf-8")
    )
    assert len(migrated_config["history"]) == 1
    assert len(migrated_config["monitor_channels"]) == 1
    assert len(migrated_config["download_queue"]) == 1
    assert (migrated / "fixture-recording" / "capture.mp4").is_file()


def test_populated_fixture_seeds_the_database_and_requires_a_fresh_profile(
    tmp_path, monkeypatch,
):
    from streamkeep import db

    populated = tmp_path / "populated"
    monkeypatch.setattr(db, "CONFIG_DIR", populated)
    monkeypatch.setattr(db, "DB_PATH", populated / "library.db")

    startup_check._prepare_fixture(populated, "populated")

    counts = db.db_diagnostics()["row_counts"]
    assert {
        name: counts[name]
        for name in ("history", "monitor_channels", "download_queue")
    } == {
        "history": 1,
        "monitor_channels": 1,
        "download_queue": 1,
    }
    with pytest.raises(RuntimeError, match="fresh isolated config"):
        startup_check._prepare_fixture(populated, "empty")


def test_unknown_fixture_writes_a_failed_readiness_marker(tmp_path):
    ready_file = tmp_path / "ready.json"

    result = startup_check.run_startup_check(
        ready_file=ready_file, fixture="not-a-fixture",
    )

    assert result["ready"] is False
    assert "unknown startup fixture" in result["error"]
    assert json.loads(ready_file.read_text(encoding="utf-8"))["ready"] is False
