"""Typed, machine-readable failure taxonomy (V154).

The ledger used to record only prose, so nothing could distinguish "come back
later" from "gone forever" — which is what drives retry policy and what stops
a permanently-dead item from poisoning the queue. Every failure now carries a
stable reason code alongside the human sentence, retry policy is read off the
code rather than re-matched from strings, and a terminal item is marked as
such instead of being offered for retry forever.
"""

import pytest

from streamkeep.retry import (
    FAILURE_CODES,
    classify_failure,
    failure_code_policy,
    failure_remediation,
)


def _code(text):
    return classify_failure(text).code


# ── Every code is a complete, self-consistent policy ─────────────────

def test_every_code_names_a_category_that_has_operator_guidance():
    for code, (category, _retryable, _terminal) in FAILURE_CODES.items():
        remediation = failure_remediation(category)
        assert remediation["message"], f"{code} -> {category} has no guidance"
        # Falling through to the unknown bucket would hand the operator
        # "no safe remediation is known" for a condition we named precisely.
        if category != "unknown":
            assert remediation != failure_remediation("unknown"), (
                f"{code} -> {category} silently reuses the unknown guidance"
            )


def test_a_terminal_code_is_never_also_retryable():
    for code, (_category, retryable, terminal) in FAILURE_CODES.items():
        assert not (retryable and terminal), f"{code} is both retryable and terminal"


def test_an_unrecognised_code_degrades_to_unknown():
    assert failure_code_policy("no_such_code") == ("unknown", False, False)
    assert failure_code_policy("") == ("unknown", False, False)
    assert failure_code_policy(None) == ("unknown", False, False)


def test_a_classification_never_carries_a_code_outside_the_table():
    samples = [
        "", "boom", "HTTP Error 404", "HTTP Error 429", "no space left on device",
        "Video unavailable", "connection reset by peer", "operation timed out",
    ]
    for text in samples:
        assert classify_failure(text).code in FAILURE_CODES


# ── The distinctions the taxonomy exists to make ─────────────────────

@pytest.mark.parametrize("text, code", [
    ("This video is not available in your country", "geo_blocked"),
    ("Join this channel to get access to members-only content", "members_only"),
    ("This video requires a channel subscription", "members_only"),
    ("This video has been deleted by the uploader", "deleted"),
    ("The account has been terminated", "deleted"),
    ("HTTP Error 404: Not Found", "not_found"),
    ("HTTP Error 429: Too Many Requests", "throttled"),
    ("HTTP Error 503: Service Unavailable", "server_error"),
    ("The read operation timed out", "timeout"),
    ("curl: (6) Could not resolve host", "network_unreachable"),
    ("No space left on device", "disk_full"),
    ("Access is denied", "permission_denied"),
    ("This content is protected by Widevine DRM", "drm_protected"),
    ("Sign in to confirm your age", "login_required"),
    ("Unsupported URL", "invalid_config"),
])
def test_conditions_get_their_own_code(text, code):
    assert _code(text) == code


@pytest.mark.parametrize("text, classification", [
    ("Cloudflare challenge required: verify you are human", "bot-check"),
    ("HTTP Error 429: Too Many Requests", "rate-limited"),
    ("This video is not available in your country", "geo-blocked"),
    ("Join this channel for members-only access", "members-only"),
    ("HTTP Error 404: Video unavailable", "genuinely-gone"),
])
def test_cross_cutting_classification_names_the_operator_condition(text, classification):
    assert classify_failure(text).classification == classification


@pytest.mark.parametrize("text", [
    "This live event will begin in 3 hours",
    "Premieres in 20 minutes",
    "The stream has not started yet",
    "channel is not live",
])
def test_a_scheduled_broadcast_is_retryable_not_a_dead_end(text):
    """The canonical "retry later" case. It previously matched nothing and so
    was classified unknown, which is non-retryable — the job gave up on a
    stream that had simply not started."""
    decision = classify_failure(text)
    assert decision.code == "scheduled_not_live"
    assert decision.retryable is True
    assert decision.terminal is False


@pytest.mark.parametrize("text, terminal", [
    ("This video is not available in your country", True),
    ("This video has been deleted by the uploader", True),
    ("protected by Widevine DRM", True),
    # An operator can still fix these by supplying a session or fixing a path.
    ("Join this channel to get access to members-only content", False),
    ("Sign in to confirm your age", False),
    ("Access is denied", False),
    ("No space left on device", False),
    # Transient conditions are never terminal.
    ("HTTP Error 429: Too Many Requests", False),
    ("The read operation timed out", False),
])
def test_terminal_means_no_action_can_ever_fix_it(text, terminal):
    assert classify_failure(text).terminal is terminal


def test_a_permanent_condition_is_not_retried_even_with_a_retry_after():
    """A Retry-After header on a gone-forever response must not resurrect it."""
    decision = classify_failure(
        "HTTP Error 410: Gone. Retry-After: 120. This video has been deleted."
    )
    assert decision.terminal is True
    assert decision.retryable is False
    assert decision.retry_after_seconds == 0


def test_a_transient_condition_keeps_its_retry_after():
    decision = classify_failure("HTTP Error 429: Too Many Requests\nRetry-After: 90")
    assert decision.code == "throttled"
    assert decision.retryable is True
    assert decision.retry_after_seconds == 90


def test_the_human_sentence_survives_alongside_the_code():
    decision = classify_failure("HTTP Error 404: Not Found")
    assert decision.code == "not_found"
    assert decision.reason
    assert "404" in decision.reason


def test_the_reason_is_still_scrubbed_of_urls_and_credentials():
    decision = classify_failure(
        "Download failed for https://example.com/secret/path --password hunter2"
    )
    assert "example.com" not in decision.reason
    assert "hunter2" not in decision.reason


# ── The ledger stores it, and terminal really is never retried ───────

def _saved(tmp_path, error, now=1_700_000_000.0):
    from unittest import mock

    from streamkeep import db

    with mock.patch.object(db, "DB_PATH", tmp_path / "library.db"):
        db.init_db()
        job_id = db.save_failed_job(
            url="https://example.com/video",
            platform="Example",
            title="Video",
            stage="download",
            error=error,
            queue_data={"url": "https://example.com/video"},
            now=now,
        )
        return (
            db.load_failed_job(job_id),
            db.load_due_failed_jobs(now=now + 30 * 24 * 60 * 60),
            db.failed_job_public_view(db.load_failed_job(job_id)),
        )


def test_a_terminal_failure_is_recorded_as_terminal_and_never_comes_due(tmp_path):
    """Never retried "forever" is enforced by the query, not by a caller
    remembering to check: a terminal row is not status='retryable', and the
    due-jobs query only ever selects that status."""
    row, due, _view = _saved(tmp_path, "This video is not available in your country")

    assert row["reason_code"] == "geo_blocked"
    assert row["terminal"] is True
    assert row["status"] == "terminal"
    assert row["auto_retry"] is False
    # A month later it is still not due.
    assert [item["id"] for item in due] == []


def test_a_transient_failure_still_schedules_itself(tmp_path):
    row, due, _view = _saved(tmp_path, "HTTP Error 503: Service Unavailable")

    assert row["reason_code"] == "server_error"
    assert row["terminal"] is False
    assert row["status"] == "retryable"
    assert [item["id"] for item in due] == [row["id"]]


def test_an_operator_fixable_failure_is_intervention_not_terminal(tmp_path):
    row, due, _view = _saved(
        tmp_path, "Join this channel to get access to members-only content",
    )

    assert row["reason_code"] == "members_only"
    assert row["terminal"] is False
    assert row["status"] == "intervention"
    assert [item["id"] for item in due] == []


def test_the_rest_view_exposes_the_code_and_the_human_sentence(tmp_path):
    _row, _due, view = _saved(tmp_path, "This video has been deleted by the uploader")

    assert view["reason_code"] == "deleted"
    assert view["terminal"] is True
    assert view["category"] == "missing_media"
    assert view["last_reason"]
    assert view["remediation"]["message"]


def test_a_scheduled_broadcast_is_scheduled_rather_than_abandoned(tmp_path):
    row, due, view = _saved(tmp_path, "This live event will begin in 3 hours")

    assert row["reason_code"] == "scheduled_not_live"
    assert row["status"] == "retryable"
    assert view["reason_code"] == "scheduled_not_live"
    assert [item["id"] for item in due] == [row["id"]]


def test_the_decision_category_always_matches_its_codes_policy():
    for text in (
        "not available in your country", "members-only", "has been deleted",
        "HTTP Error 429", "premieres in 5 minutes", "no space left",
    ):
        decision = classify_failure(text)
        category, retryable, terminal = failure_code_policy(decision.code)
        assert decision.category == category
        assert decision.retryable == retryable
        assert decision.terminal == terminal
