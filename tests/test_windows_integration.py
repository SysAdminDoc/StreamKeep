"""Coverage for optional Windows shell integration (V135)."""

from unittest import mock

from streamkeep.windows_integration import (
    TaskbarProgress,
    aggregate_queue_progress,
)


def test_aggregate_queue_progress_scales_a_batch_to_one_hundred_per_item():
    snapshot = aggregate_queue_progress([
        {"status": "done", "progress": 100},
        {"status": "downloading", "progress": 50},
        {"status": "queued"},
    ])
    assert snapshot == {
        "state": "normal",
        "completed": 150,
        "total": 300,
        "failed": 0,
        "items_done": 1,
        "items_total": 3,
    }


def test_aggregate_queue_progress_reports_pause_and_error_states():
    paused = aggregate_queue_progress(
        [{"status": "queued"}, {"status": "downloading", "progress": 10}],
        paused=True,
    )
    assert paused["state"] == "paused"

    failed = aggregate_queue_progress([
        {"status": "failed"},
        {"status": "queued"},
    ])
    assert failed["state"] == "error"
    assert failed["failed"] == 1


def test_aggregate_queue_progress_clears_after_a_clean_batch():
    assert aggregate_queue_progress([{"status": "done"}]) == {
        "state": "none",
        "completed": 0,
        "total": 0,
        "failed": 0,
        "items_done": 0,
        "items_total": 0,
    }
    assert aggregate_queue_progress([{"status": "cancelled"}])["state"] == "none"


def test_a_batch_that_ends_with_a_failure_is_full_width_and_counts_truthfully():
    """V153: the terminal branch used item counts where every other branch
    uses hundredths, so a failed batch painted a zero-width red bar and the
    progress notification reported "1 of 1"."""
    snapshot = aggregate_queue_progress([
        {"status": "done"},
        {"status": "done"},
        {"status": "failed"},
        {"status": "cancelled"},
    ])
    assert snapshot["state"] == "error"
    assert snapshot["failed"] == 1
    # Three considered rows (the cancelled row is excluded), in hundredths.
    assert snapshot["total"] == 300
    assert snapshot["completed"] == snapshot["total"]
    # ...but only two of them actually completed.
    assert (snapshot["items_done"], snapshot["items_total"]) == (2, 3)


def test_terminal_and_running_branches_agree_on_units():
    """Both branches must express total in hundredths of an item so the
    shell surfaces can recover a job count by dividing by 100."""
    running = aggregate_queue_progress([
        {"status": "done"}, {"status": "queued"},
    ])
    terminal = aggregate_queue_progress([
        {"status": "done"}, {"status": "failed"},
    ])
    assert running["total"] == terminal["total"] == 200
    assert terminal["items_total"] == running["items_total"] == 2


def test_taskbar_wrapper_is_a_noop_when_disabled():
    taskbar = TaskbarProgress(enabled=False)
    assert taskbar.available is False
    assert taskbar.update(123, {"state": "normal", "completed": 1, "total": 2}) is False
    assert taskbar.clear(123) is False
    taskbar.close()


def test_power_state_reads_battery_and_energy_saver_without_mutating_power():
    import streamkeep.power as power

    with mock.patch.object(
        power, "sys", mock.Mock(platform="win32")
    ), mock.patch.object(
        power,
        "_read_system_power_status",
        return_value={"ac_line_status": 0, "battery_flag": 0},
    ), mock.patch.object(power, "_read_effective_power_mode", return_value=0):
        state = power.read_windows_power_state()

    assert state.available is True
    assert state.on_battery is True
    assert state.energy_saver is True
    assert power.should_pause_for_power(state) is True
    assert power.power_pause_reason(state) == "battery"


def test_power_state_resumes_when_ac_and_energy_saver_are_clear():
    import streamkeep.power as power

    with mock.patch.object(
        power, "sys", mock.Mock(platform="win32")
    ), mock.patch.object(
        power,
        "_read_system_power_status",
        return_value={"ac_line_status": 1, "battery_flag": 128},
    ), mock.patch.object(power, "_read_effective_power_mode", return_value=2):
        state = power.read_windows_power_state()

    assert state == power.PowerState(True, False, False, "balanced")
    assert power.should_pause_for_power(state) is False


def test_queue_complete_action_waits_while_power_policy_holds_work():
    from streamkeep.ui.tabs.download_queue import DownloadQueueMixin

    window = DownloadQueueMixin.__new__(DownloadQueueMixin)
    window._power_action_armed = True
    window._power_pause_active = True
    window._disk_pause_active = False
    window._maybe_fire_queue_complete_power_action()
    assert window._power_action_armed is True
