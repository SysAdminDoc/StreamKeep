"""V165: a broken extractor must read as a broken source, not a broken app.

Two properties matter. The standing condition has to name the platform *and*
the engine that failed, otherwise the operator cannot tell which switch
recovers it. And the choice has to be per-platform, so switching Kick to a
different engine does not change how YouTube is captured.
"""

import time

import pytest

from streamkeep import capabilities, health
from streamkeep.db import _legacy


@pytest.fixture
def config():
    return {}


# ── The override map ────────────────────────────────────────────────

def test_an_override_applies_to_one_platform_only(config):
    capabilities.set_source_engine(config, "Kick", "yt-dlp")

    assert capabilities.resolve_source_engine(config, "kick") == "yt-dlp"
    assert capabilities.resolve_source_engine(config, "youtube") == ""


def test_platform_names_fold_so_the_choice_survives_capitalisation(config):
    capabilities.set_source_engine(config, "Kick", "yt-dlp")

    assert capabilities.resolve_source_engine(config, "KICK") == "yt-dlp"
    assert capabilities.resolve_source_engine(config, " kick ") == "yt-dlp"


def test_clearing_an_override_returns_the_platform_to_automatic(config):
    capabilities.set_source_engine(config, "kick", "yt-dlp")
    capabilities.set_source_engine(config, "kick", "")

    assert capabilities.resolve_source_engine(config, "kick") == ""
    assert config["source_engine_overrides"] == {}


def test_an_unknown_engine_name_is_refused_rather_than_stored(config):
    capabilities.set_source_engine(config, "kick", "definitely-not-an-engine")

    assert config["source_engine_overrides"] == {}


def test_an_override_naming_an_uninstalled_engine_is_ignored(config):
    """Honouring it would guarantee a failure; automatic is the safe read."""
    config["source_engine_overrides"] = {"kick": "streamlink"}
    installed = capabilities.available_engines()

    resolved = capabilities.resolve_source_engine(config, "kick")
    if "streamlink" in installed:
        assert resolved == "streamlink"
    else:
        assert resolved == ""


def test_a_corrupt_override_map_does_not_break_resolution():
    assert capabilities.resolve_source_engine({"source_engine_overrides": 7}, "k") == ""
    assert capabilities.load_source_engine_overrides(
        {"source_engine_overrides": {"": "yt-dlp", "kick": 5}}
    ) == {}


# ── Naming the engine on the health condition ───────────────────────

def _circuit(engine="", count=5, label="Kick"):
    return {
        "source_key": "abc123", "source_label": label, "engine": engine,
        "failure_count": count, "opened_until": 0,
        "last_category": "extractor", "last_reason": "no formats found",
    }


def test_the_condition_names_the_engine_that_failed():
    now = time.time()
    conditions = health._extractor_conditions(
        {}, [_circuit(engine="yt-dlp")], health._now_iso(now), now,
    )

    assert len(conditions) == 1
    condition = conditions[0]
    assert "Kick" in condition["title"]
    assert "yt-dlp" in condition["title"]
    assert "yt-dlp" in condition["detail"]
    assert condition["engine"] == "yt-dlp"


def test_the_condition_offers_the_installed_alternates_not_the_failing_one():
    now = time.time()
    condition = health._extractor_conditions(
        {}, [_circuit(engine="yt-dlp")], health._now_iso(now), now,
    )[0]

    assert "yt-dlp" not in condition["alternate_engines"]
    assert set(condition["alternate_engines"]) <= set(
        capabilities.available_engines()
    )
    assert condition["alternate_engines"], "at least native should be offered"
    assert "Settings" in condition["repair"]


def test_an_unknown_engine_still_raises_the_condition():
    """A circuit recorded before V165 has no engine; it must not be dropped."""
    now = time.time()
    condition = health._extractor_conditions(
        {}, [_circuit(engine="")], health._now_iso(now), now,
    )[0]

    assert "Kick" in condition["title"]
    assert condition["engine"] == ""


def test_the_condition_clears_once_failures_fall_below_the_threshold():
    now = time.time()
    assert health._extractor_conditions(
        {}, [_circuit(engine="yt-dlp", count=1)], health._now_iso(now), now,
    ) == []


# ── Recording which engine failed ───────────────────────────────────

@pytest.mark.parametrize("queue, context, expected", [
    ({"format_type": "ytdlp_direct"}, {}, "yt-dlp"),
    ({"format_type": "hls"}, {}, "native"),
    ({"format_type": "direct"}, {}, "native"),
    ({"format_type": "ytdlp_direct"}, {"engine": "streamlink"}, "streamlink"),
    ({}, {}, ""),
    ({"format_type": "something-new"}, {}, ""),
])
def test_the_engine_is_derived_from_the_job_shape(queue, context, expected):
    assert _legacy._circuit_engine(queue, context) == expected


def test_a_non_dict_payload_does_not_break_engine_derivation():
    assert _legacy._circuit_engine(None, None) == ""
    assert _legacy._circuit_engine("nonsense", 7) == ""


# ── The override actually changes which engine runs ─────────────────

class _Job:
    """Minimal stand-in carrying only what the decision reads."""

    def __init__(self, source_engine="", **flags):
        self.source_engine = source_engine
        for name, value in flags.items():
            setattr(self, name, value)

    _engine_enabled = None  # bound below


def _enabled(job, engine, flag):
    from streamkeep.workers.download import DownloadWorker

    return DownloadWorker._engine_enabled(job, engine, flag)


def test_with_no_override_the_global_switch_still_decides():
    assert _enabled(_Job(streamlink_live_engine=True), "streamlink",
                    "streamlink_live_engine") is True
    assert _enabled(_Job(streamlink_live_engine=False), "streamlink",
                    "streamlink_live_engine") is False


def test_an_override_turns_an_engine_on_despite_the_global_switch():
    job = _Job(source_engine="streamlink", streamlink_live_engine=False)

    assert _enabled(job, "streamlink", "streamlink_live_engine") is True


def test_choosing_another_engine_turns_this_one_off_despite_the_switch():
    """Otherwise "switch Kick to yt-dlp" would leave Streamlink still running."""
    job = _Job(source_engine="yt-dlp", streamlink_live_engine=True)

    assert _enabled(job, "streamlink", "streamlink_live_engine") is False


def test_the_two_optional_engines_do_not_interfere():
    job = _Job(source_engine="ytarchive",
               streamlink_live_engine=True, live_engine_fallback=False)

    assert _enabled(job, "ytarchive", "live_engine_fallback") is True
    assert _enabled(job, "streamlink", "streamlink_live_engine") is False
