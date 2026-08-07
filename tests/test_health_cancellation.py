"""V180: stopping a health probe must not require terminating its thread.

V179 was a health probe still inside ``subprocess.communicate()`` when the
window that owned it went away. The boundary fix stops the thread properly, but
the last resort was still ``QThread.terminate()`` -- and terminating a thread
mid-subprocess is the same undefined behaviour, just rarer. The probe now polls
for cancellation between checks so ``wait()`` returns on its own.
"""

import pytest

from streamkeep import capabilities, health


@pytest.fixture(autouse=True)
def _isolate_capability_cache():
    capabilities.invalidate_runtime_capabilities_cache()
    yield
    capabilities.invalidate_runtime_capabilities_cache()


# ── The registry stops between probes ───────────────────────────────

def test_a_cancelled_registry_stops_instead_of_running_every_probe(monkeypatch):
    """Counting the probes is the point.

    Asserting only that the call returned ``None`` would pass even if it had
    run all ten checks first and discarded the result -- which is exactly the
    behaviour this exists to rule out.
    """
    probes = []
    real = capabilities._probe_executable

    def counting_probe(name, *args, **kwargs):
        probes.append(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(capabilities, "_probe_executable", counting_probe)

    result = capabilities.get_runtime_capabilities(
        refresh=True, config={}, should_cancel=lambda: True,
    )

    assert result is None, "a cancelled probe must not return a partial registry"
    assert probes == [], f"it kept probing after being told to stop: {probes}"


def test_cancelling_part_way_through_stops_at_the_next_checkpoint(monkeypatch):
    """Cancel after the first executable probe; the rest must not run."""
    probes = []
    real = capabilities._probe_executable
    state = {"cancel": False}

    def counting_probe(name, *args, **kwargs):
        probes.append(name)
        state["cancel"] = True          # stop requested mid-registry
        return real(name, *args, **kwargs)

    monkeypatch.setattr(capabilities, "_probe_executable", counting_probe)

    result = capabilities.get_runtime_capabilities(
        refresh=True, config={}, should_cancel=lambda: state["cancel"],
    )

    assert result is None
    assert len(probes) == 1, f"expected to stop after the first probe, ran {probes}"


def test_a_cancelled_probe_is_not_cached_as_the_real_registry():
    """A partial registry cached as complete would report absent tools."""
    capabilities.get_runtime_capabilities(refresh=True, config={},
                                          should_cancel=lambda: True)

    registry = capabilities.get_runtime_capabilities(config={})

    assert registry is not None
    assert registry.get("ffmpeg"), "the next read must do a real probe"


def test_an_uncancelled_probe_is_unaffected():
    """The default path must not change: no predicate, no early return."""
    registry = capabilities.get_runtime_capabilities(refresh=True, config={})

    assert registry is not None
    assert "ffmpeg" in registry and "curl" in registry and "yt_dlp" in registry


# ── The health check honours it too ─────────────────────────────────

def test_run_health_check_returns_none_when_cancelled_before_any_work(tmp_path):
    result = health.run_health_check(
        {}, dispatch_events=False, storage_path=tmp_path,
        should_cancel=lambda: True,
    )

    assert result is None
    assert not list(tmp_path.glob("*.json")), "a cancelled run must persist nothing"


def test_run_health_check_stops_when_the_registry_stops(tmp_path, monkeypatch):
    """A cancelled registry must propagate, not be treated as 'no tools found'.

    Passing an empty runtime through would write a snapshot claiming every
    dependency was missing, which is a false alarm rather than a stopped probe.
    """
    monkeypatch.setattr(
        capabilities, "_probe_registry",
        lambda **kwargs: (_ for _ in ()).throw(capabilities.ProbeCancelled()),
    )

    result = health.run_health_check(
        {}, dispatch_events=False, storage_path=tmp_path,
        should_cancel=lambda: False,   # the registry stops on its own
    )

    assert result is None
    assert not list(tmp_path.glob("*.json"))


def test_an_uncancelled_health_check_still_writes_its_snapshot(tmp_path):
    result = health.run_health_check(
        {}, dispatch_events=False, storage_path=tmp_path,
    )

    assert result is not None
    assert result["conditions"] is not None
    assert result["schema_version"] == health.HEALTH_SCHEMA_VERSION


# ── The worker wires it up ──────────────────────────────────────────

def test_the_health_worker_passes_a_live_view_of_its_cancel_flag(monkeypatch,
                                                                qt_application):
    """The plumbing is the feature; a flag nothing reads is not cancellation.

    The worker must not be pre-cancelled here: ``run()`` returns immediately in
    that case and the probe is never called, so the test would pass while
    proving nothing about what gets passed down.
    """
    import streamkeep.health as health_module
    from streamkeep.ui.main_window import _HealthCheckWorker

    seen = {}

    def fake_run_health_check(config, **kwargs):
        seen["should_cancel"] = kwargs.get("should_cancel")
        return {"conditions": []}

    monkeypatch.setattr(health_module, "run_health_check", fake_run_health_check)

    worker = _HealthCheckWorker({})
    worker.run()

    predicate = seen.get("should_cancel")
    assert callable(predicate), "the probe was given no way to ask about stopping"
    # A live view of the flag, not a value snapshotted at construction.
    assert predicate() is False
    worker.cancel()
    assert predicate() is True, "cancel() must be visible through the predicate"


def test_a_cancelled_worker_emits_nothing(qt_application):
    """A cancelled probe returns None, and None must not reach the UI."""
    from streamkeep.ui.main_window import _HealthCheckWorker

    worker = _HealthCheckWorker({})
    emitted = []
    worker.result_ready.connect(emitted.append)

    worker.cancel()
    worker.run()

    assert emitted == []
