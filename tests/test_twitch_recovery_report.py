"""V176: recovery names what each CDN domain said, and refuses a gated VOD.

Twitch rotates its CDN domains, so "nothing found" without a per-domain answer
cannot distinguish a rotated domain list from a VOD that is genuinely gone. The
old probe returned a bare boolean, so every failure looked the same.

The line this must not cross: recovery reconstructs URLs for segments the CDN
still serves unauthenticated. A 401/403 means the platform is refusing that, and
the answer is to stop -- not to try the next domain or a lower quality until
something answers. That is an access control, not a rotated domain.
"""

import urllib.error

import pytest

from streamkeep.extractors import twitch_recover as recover


def _http_error(status):
    return urllib.error.HTTPError(
        "https://cdn.example/x", status, "err", {}, None,
    )


class _Response:
    def __init__(self, status):
        self.status = status


# ── A probe names its outcome ───────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    (200, recover.PROBE_HIT),
    (206, recover.PROBE_HIT),
])
def test_a_served_segment_set_is_a_hit(monkeypatch, status, expected):
    monkeypatch.setattr(recover.urllib.request, "urlopen",
                        lambda *a, **k: _Response(status))

    outcome, detail = recover._probe_url("https://cdn.example/x")

    assert outcome == expected
    assert str(status) in detail


def test_a_404_is_missing_not_an_error(monkeypatch):
    monkeypatch.setattr(recover.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(404)))

    outcome, detail = recover._probe_url("https://cdn.example/x")

    assert outcome == recover.PROBE_MISSING
    assert "404" in detail


@pytest.mark.parametrize("status", [401, 403])
def test_a_gated_response_is_distinct_from_missing(monkeypatch, status):
    """The whole safety property rests on these two not being the same thing."""
    monkeypatch.setattr(
        recover.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(_http_error(status)),
    )

    outcome, detail = recover._probe_url("https://cdn.example/x")

    assert outcome == recover.PROBE_FORBIDDEN
    assert outcome != recover.PROBE_MISSING
    assert str(status) in detail and "gated" in detail


def test_an_unreachable_host_names_the_reason(monkeypatch):
    monkeypatch.setattr(
        recover.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("no dns")),
    )

    outcome, detail = recover._probe_url("https://cdn.example/x")

    assert outcome == recover.PROBE_ERROR
    assert "unreachable" in detail and "no dns" in detail


def test_a_server_error_is_not_reported_as_a_missing_vod(monkeypatch):
    monkeypatch.setattr(recover.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(503)))

    outcome, detail = recover._probe_url("https://cdn.example/x")

    assert outcome == recover.PROBE_ERROR
    assert "503" in detail


def test_the_boolean_helper_still_answers_for_existing_callers(monkeypatch):
    monkeypatch.setattr(recover.urllib.request, "urlopen",
                        lambda *a, **k: _Response(200))
    assert recover._head_check("https://cdn.example/x") is True

    monkeypatch.setattr(recover.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(404)))
    assert recover._head_check("https://cdn.example/x") is False


# ── Every candidate domain is enumerated and reported ───────────────

def test_a_failed_recovery_reports_every_candidate_domain(monkeypatch):
    monkeypatch.setattr(recover, "_probe_url",
                        lambda url, timeout=8: (recover.PROBE_MISSING, "HTTP 404"))

    urls, report = recover.probe_vod("streamer", "12345", 1700000000)

    assert urls == []
    assert [entry["domain"] for entry in report] == recover.CDN_DOMAINS, (
        "a caller cannot tell a rotated domain list from a deleted VOD without "
        "an answer for each candidate"
    )
    assert all(entry["outcome"] == recover.PROBE_MISSING for entry in report)


def test_a_hit_records_which_domain_and_quality_resolved(monkeypatch):
    target = recover.CDN_DOMAINS[2]

    def probe(url, timeout=8):
        if url.startswith(target) and "chunked" in url:
            return recover.PROBE_HIT, "HTTP 200"
        return recover.PROBE_MISSING, "HTTP 404"

    monkeypatch.setattr(recover, "_probe_url", probe)

    urls, report = recover.probe_vod("streamer", "12345", 1700000000)

    assert len(urls) == 1 and urls[0].startswith(target)
    hits = [e for e in report if e["outcome"] == recover.PROBE_HIT]
    assert len(hits) == 1
    assert hits[0]["domain"] == target
    assert hits[0]["quality"] == "chunked"
    assert hits[0]["url"] == urls[0]


def test_the_report_formats_one_line_per_domain():
    lines = recover.format_recovery_report([
        {"domain": "https://a", "outcome": recover.PROBE_HIT,
         "detail": "HTTP 200", "quality": "720p60", "url": "https://a/x"},
        {"domain": "https://b", "outcome": recover.PROBE_MISSING,
         "detail": "HTTP 404"},
    ])

    assert "https://a: resolved at 720p60 (HTTP 200)" in lines[0]
    assert "https://b: missing - HTTP 404" in lines[1]


# ── The refusal ─────────────────────────────────────────────────────

def test_a_gated_probe_stops_the_attempt_rather_than_trying_the_next_domain(
    monkeypatch,
):
    """The behaviour that keeps this on the right side of the line."""
    attempted = []

    def probe(url, timeout=8):
        attempted.append(url)
        return recover.PROBE_FORBIDDEN, "HTTP 403 (access is gated)"

    monkeypatch.setattr(recover, "_probe_url", probe)

    with pytest.raises(recover.RecoveryRefused) as excinfo:
        recover.probe_vod("streamer", "12345", 1700000000)

    assert "gating these segments" in str(excinfo.value)
    assert len(attempted) == 1, (
        f"it kept probing after being refused: {len(attempted)} attempts"
    )


def test_the_refusal_says_it_will_not_bypass_the_control(monkeypatch):
    monkeypatch.setattr(
        recover, "_probe_url",
        lambda url, timeout=8: (recover.PROBE_FORBIDDEN, "HTTP 403 (access is gated)"),
    )
    logged = []

    with pytest.raises(recover.RecoveryRefused):
        recover.probe_vod("streamer", "1", 1700000000, log_fn=logged.append)

    joined = " ".join(logged)
    assert "REFUSED" in joined
    assert "will not attempt to bypass" in joined


def test_a_channel_recovery_stops_at_a_refusal_instead_of_walking_timestamps(
    monkeypatch,
):
    """A gate applies to the channel's segments, not to one timestamp guess, so
    trying the other ~13 stamp variants is both pointless and wrong."""
    calls = []

    def probe(channel, stream_id, timestamp, log_fn=None):
        calls.append(timestamp)
        raise recover.RecoveryRefused("HTTP 403; the platform is gating these segments")

    monkeypatch.setattr(recover, "probe_vod", probe)
    monkeypatch.setattr(
        recover, "_scrape_twitchtracker",
        lambda *a, **k: [
            {"stream_id": "1", "timestamp": 1, "date_str": "2024-01-05 10:00:00"},
            {"stream_id": "2", "timestamp": 2, "date_str": "2024-01-06 10:00:00"},
        ],
    )

    progress = []
    results = recover.recover_channel_vods(
        "Good_Streamer1", 2024, 1, progress_fn=lambda p, m: progress.append(m),
    )

    assert results == []
    assert len(calls) == 1, f"walked past the refusal: {calls}"
    assert any("Refused" in message for message in progress)


def test_a_missing_vod_still_walks_the_timestamp_variants(monkeypatch):
    """The refusal must not have made an ordinary miss stop early."""
    calls = []

    def probe(channel, stream_id, timestamp, log_fn=None):
        calls.append(timestamp)
        return [], [{"domain": d, "outcome": recover.PROBE_MISSING,
                     "detail": "HTTP 404"} for d in recover.CDN_DOMAINS]

    monkeypatch.setattr(recover, "probe_vod", probe)
    monkeypatch.setattr(
        recover, "_scrape_twitchtracker",
        lambda *a, **k: [
            {"stream_id": "1", "timestamp": 1, "date_str": "2024-01-05 10:00:00"},
        ],
    )

    results = recover.recover_channel_vods("Good_Streamer1", 2024, 1)

    assert results == []
    assert len(calls) > 1, "an ordinary miss should try the other timestamps"


# ── Dates the fallback scraper actually emits ───────────────────────

@pytest.mark.parametrize("date_str", [
    "2024-01-05 10:00",          # the TwitchTracker pattern
    "2024-01-05 10:00:00",       # with seconds
    "2024-01-05T10:00:00",       # ISO separator
    "2024-01-05T10:00:00Z",      # ISO with a zone marker
    "2024-01-05",                # date only
])
def test_every_date_shape_the_scrapers_emit_yields_timestamps(date_str):
    """The sullygnome fallback takes ``data-date`` verbatim.

    Formats carrying seconds or an ISO ``T`` used to parse as nothing, so the
    stream was skipped with no probe and no message - a silent no-op rather
    than a miss.
    """
    assert len(recover._unix_timestamp_variants(date_str)) == 13


@pytest.mark.parametrize("date_str", ["garbage", "", None, "05/01/2024"])
def test_an_unusable_date_yields_nothing_rather_than_a_wrong_timestamp(date_str):
    assert recover._unix_timestamp_variants(date_str) == []


def test_a_stream_with_an_unreadable_date_says_so_instead_of_vanishing(monkeypatch):
    monkeypatch.setattr(
        recover, "_scrape_twitchtracker",
        lambda *a, **k: [{"stream_id": "77", "date_str": "not-a-date"}],
    )
    probed = []
    monkeypatch.setattr(recover, "probe_vod",
                        lambda *a, **k: probed.append(a) or ([], []))
    logged = []

    results = recover.recover_channel_vods(
        "Good_Streamer1", 2024, 1, log_fn=logged.append,
    )

    assert results == []
    assert probed == [], "nothing to hash, so nothing should have been probed"
    assert any("unreadable date" in line and "77" in line for line in logged)


def test_a_failed_recovery_logs_the_per_domain_report(monkeypatch):
    monkeypatch.setattr(
        recover, "probe_vod",
        lambda *a, **k: ([], [
            {"domain": "https://a", "outcome": recover.PROBE_MISSING,
             "detail": "HTTP 404"},
        ]),
    )
    monkeypatch.setattr(
        recover, "_scrape_twitchtracker",
        lambda *a, **k: [
            {"stream_id": "1", "timestamp": 1, "date_str": "2024-01-05 10:00:00"},
        ],
    )
    logged = []

    recover.recover_channel_vods("Good_Streamer1", 2024, 1, log_fn=logged.append)

    assert any("https://a: missing - HTTP 404" in line for line in logged)
