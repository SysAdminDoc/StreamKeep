"""V42: let an operator track yt-dlp nightly without waiting for a release.

StreamKeep bundles a frozen yt-dlp, and stable yt-dlp cadence cannot track
YouTube breakage — there was a 12-week gap between 2026.03.17 and 2026.06.09
while YouTube broke repeatedly. The toggle points at an operator-supplied
build; the safety property is that the named build is version-probed like any
other tool rather than trusted, so a below-floor binary can never reach a
download path.
"""

from datetime import datetime, timezone
import textwrap
from unittest import mock

import pytest

from streamkeep import capabilities
from streamkeep.extractors.ytdlp import ytdlp_runtime_status


@pytest.fixture(autouse=True)
def _isolate_capability_cache():
    capabilities.invalidate_runtime_capabilities_cache()
    capabilities.clear_ytdlp_release_cache()
    yield
    capabilities.invalidate_runtime_capabilities_cache()
    capabilities.clear_ytdlp_release_cache()


def _stub_ytdlp(tmp_path, name, version):
    """A yt-dlp stand-in that answers --version, which is all the probe reads."""
    script = tmp_path / f"{name}.cmd"
    script.write_text(
        textwrap.dedent(f"""\
        @echo off
        echo {version}
        """),
        encoding="utf-8",
    )
    return script


def _record(config):
    return capabilities.get_runtime_capabilities(refresh=True, config=config)["yt_dlp"]


# ── The setting ─────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("bundled", "bundled"), ("external", "external"), ("External", "external"),
    (" external ", "external"), ("nightly", "bundled"), ("", "bundled"),
    (None, "bundled"), (0, "bundled"), ("../../etc/passwd", "bundled"),
])
def test_an_unrecognised_channel_falls_back_to_bundled(value, expected):
    """An unknown channel must never mean 'skip the bundled build'."""
    assert capabilities.normalize_ytdlp_channel(value) == expected


def test_the_bundled_build_is_the_default():
    assert capabilities.read_ytdlp_channel({}) == "bundled"
    assert capabilities.read_ytdlp_external_command({}) == ""

    record = _record({})

    assert record["channel"] == "bundled"
    assert record["supported"], "the shipped build must stay usable"


# ── The safety property ─────────────────────────────────────────────

def test_a_below_floor_external_build_is_refused_by_name(tmp_path):
    """The whole point of probing rather than trusting.

    yt-dlp 2026.06.09 is a real release below the supported floor; naming it
    must not put it on a download path.
    """
    stub = _stub_ytdlp(tmp_path, "yt-dlp-old", "2026.06.09")

    record = _record({
        "ytdlp_channel": "external",
        "ytdlp_external_command": str(stub),
    })

    assert record["channel"] == "bundled", "it must not be adopted"
    assert "2026.6.9" in record["external_problem"]
    assert "below the required minimum" in record["external_problem"]
    # And the command that will actually run is not the refused binary.
    assert str(stub) not in " ".join(str(part) for part in record["command"])


def test_a_missing_external_build_says_so_and_keeps_downloads_working(tmp_path):
    record = _record({
        "ytdlp_channel": "external",
        "ytdlp_external_command": str(tmp_path / "not-installed.cmd"),
    })

    assert record["channel"] == "bundled"
    assert record["supported"], "a settings typo must not take downloads down"
    assert "not found" in record["external_problem"]


def test_a_nightly_build_above_the_floor_is_adopted(tmp_path):
    """Nightly versions carry a fourth component; the floor must still compare."""
    stub = _stub_ytdlp(tmp_path, "yt-dlp-nightly", "2026.08.05.232712")

    record = _record({
        "ytdlp_channel": "external",
        "ytdlp_external_command": str(stub),
    })

    assert record["channel"] == "external"
    assert record["supported"]
    assert record["version"].startswith("2026.8.5")
    assert record["command"] == [str(stub.resolve())]


def test_naming_a_build_without_selecting_the_channel_changes_nothing(tmp_path):
    """The channel is the switch; a stored path alone must not take effect."""
    stub = _stub_ytdlp(tmp_path, "yt-dlp-nightly", "2026.08.05.232712")

    record = _record({
        "ytdlp_channel": "bundled",
        "ytdlp_external_command": str(stub),
    })

    assert record["channel"] == "bundled"
    assert str(stub) not in " ".join(str(part) for part in record["command"])


# ── Reversibility ───────────────────────────────────────────────────

def test_switching_channels_re_probes_rather_than_serving_the_cached_build(tmp_path):
    """The registry is cached; the channel has to be part of its key.

    Without that, flipping the setting would keep returning the previously
    probed yt-dlp and the switch would read as having no effect.
    """
    stub = _stub_ytdlp(tmp_path, "yt-dlp-nightly", "2026.08.05.232712")
    external = {"ytdlp_channel": "external", "ytdlp_external_command": str(stub)}

    # Warm the cache on the bundled build, then switch *without* refresh=True.
    capabilities.get_runtime_capabilities(config={})
    switched = capabilities.get_runtime_capabilities(config=external)["yt_dlp"]

    assert switched["channel"] == "external", "the cache key ignored the channel"

    # And back again, still without an explicit refresh.
    reverted = capabilities.get_runtime_capabilities(config={})["yt_dlp"]

    assert reverted["channel"] == "bundled", "the switch must be reversible"


# ── What the operator is told ───────────────────────────────────────

def test_the_health_status_names_the_channel_actually_in_use(tmp_path):
    stub = _stub_ytdlp(tmp_path, "yt-dlp-nightly", "2026.08.05.232712")

    status = ytdlp_runtime_status({
        "ytdlp_channel": "external",
        "ytdlp_external_command": str(stub),
    })

    assert status["yt_dlp_channel"] == "external"
    assert status["yt_dlp_channel_requested"] == "external"
    assert "external" in status["detail"]


def test_a_fallback_is_reported_as_bundled_not_as_the_channel_requested(tmp_path):
    """Reporting the request would let an operator believe they were on
    nightly while running the bundled build."""
    stub = _stub_ytdlp(tmp_path, "yt-dlp-old", "2026.06.09")

    status = ytdlp_runtime_status({
        "ytdlp_channel": "external",
        "ytdlp_external_command": str(stub),
    })

    assert status["yt_dlp_channel"] == "bundled", "what is in use"
    assert status["yt_dlp_channel_requested"] == "external", "what was asked for"
    assert status["yt_dlp_external_problem"], "and why the request was refused"
    assert "fell back" in status["yt_dlp_channel_detail"]


def test_the_bundled_channel_is_still_named_when_nothing_is_configured():
    status = ytdlp_runtime_status({})

    assert status["yt_dlp_channel"] == "bundled"
    assert status["yt_dlp_channel_requested"] == "bundled"
    assert not status["yt_dlp_external_problem"]


def test_stale_ytdlp_release_reports_age_and_external_binary_remedy():
    snapshot = {
        "latest_version": "2026.08.01",
        "latest_release_at": "2026-08-01T00:00:00+00:00",
        "release_dates": {
            "2026.07.04": "2026-07-04T00:00:00+00:00",
            "2026.08.01": "2026-08-01T00:00:00+00:00",
        },
    }
    with mock.patch.object(
        capabilities, "_fetch_ytdlp_release_snapshot", return_value=snapshot,
    ) as fetch:
        status = capabilities.ytdlp_update_status(
            "2026.07.04",
            check_online=True,
            now=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )

    assert status["state"] == "stale"
    assert status["latest_version"] == "2026.08.01"
    assert status["behind_days"] == 28
    assert status["age_days"] == 28
    assert "external yt-dlp executable" in status["warning"]
    fetch.assert_called_once_with()


def test_ytdlp_release_check_is_unknown_when_offline():
    with mock.patch.object(
        capabilities,
        "_fetch_ytdlp_release_snapshot",
        side_effect=OSError("offline"),
    ):
        status = capabilities.ytdlp_update_status(
            "2026.07.04", check_online=True,
        )

    assert status["state"] == "unknown"
    assert status["summary"] == "unknown"
    assert "unknown" in status["detail"].lower()


# ── The download path resolves through the gate ─────────────────────

def test_the_resolved_command_prefix_is_the_adopted_build(tmp_path):
    """`resolve_command_prefix` is the only door into a download path, and it
    goes through `require_capability`, so an unsupported build raises."""
    stub = _stub_ytdlp(tmp_path, "yt-dlp-nightly", "2026.08.05.232712")

    prefix = capabilities.resolve_command_prefix("yt_dlp", refresh=True, config={
        "ytdlp_channel": "external", "ytdlp_external_command": str(stub),
    })

    assert prefix == [str(stub.resolve())]
