from datetime import datetime, timezone
import time

from streamkeep import credential_check as cc
from streamkeep import health
from streamkeep.health import (
    HEALTH_EVENT_BY_CATEGORY,
    load_health_snapshot,
    public_snapshot,
    run_health_check,
)
from streamkeep.hooks import HOOK_EVENTS


def _runtime(*, ffmpeg_ready=True):
    names = ("sqlite", "curl", "ffmpeg", "ffprobe", "yt_dlp", "youtube")
    result = {
        name: {"name": name, "display_name": name.title(), "supported": True}
        for name in names
    }
    if not ffmpeg_ready:
        result["ffmpeg"] = {
            "name": "ffmpeg",
            "display_name": "FFmpeg",
            "available": False,
            "supported": False,
            "repair": "Install FFmpeg",
        }
    return result


def test_health_aggregates_conditions_persists_and_dispatches_transitions(tmp_path):
    missing = tmp_path / "missing-archive"
    existing = tmp_path / "archive"
    existing.mkdir()
    storage = tmp_path / "health.json"
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    config = {
        "disk_warning_gb": 2,
        "disk_critical_gb": 1,
        "health_failure_threshold": 3,
    }
    circuit = [{
        "source_key": "youtube:example",
        "source_label": "Example",
        "failure_count": 3,
        "opened_until": now.timestamp() + 60,
        "last_reason": "HTTP 503",
    }]
    unattended = [{
        "kind": "auto_record",
        "subject": "example-channel",
        "detail": "Cannot create the output folder for example-channel",
        "repair": "Check the output folder path",
        "severity": "error",
        "recorded_at": now.isoformat(),
    }]
    events = []
    snapshot = run_health_check(
        config,
        runtime=_runtime(ffmpeg_ready=False),
        credential_results=[cc.ProbeResult("youtube", cc.EXPIRED, "Expired")],
        archive_roots=[("Missing", str(missing)), ("Archive", str(existing))],
        retry_circuits=circuit,
        disk_usage={
            str(missing): (100, 99, int(1.5 * 1024 ** 3)),
            str(existing): (100, 99, int(1.5 * 1024 ** 3)),
        },
        unattended_failures=unattended,
        now=now,
        storage_path=storage,
        event_sink=events.append,
    )

    categories = {condition["category"] for condition in snapshot["conditions"]}
    assert categories == set(HEALTH_EVENT_BY_CATEGORY), (
        "the fixture must exercise every health category so the event "
        "completeness assertion below cannot pass by omission"
    )
    assert snapshot["status"] == "error"
    assert {event["state"] for event in events} == {"opened"}
    assert {event["event"] for event in events} == set(HEALTH_EVENT_BY_CATEGORY.values())
    assert load_health_snapshot(storage)["conditions"]

    unchanged = run_health_check(
        config,
        unattended_failures=unattended,
        runtime=_runtime(ffmpeg_ready=False),
        credential_results=[cc.ProbeResult("youtube", cc.EXPIRED, "Expired")],
        archive_roots=[("Missing", str(missing)), ("Archive", str(existing))],
        retry_circuits=circuit,
        disk_usage={
            str(missing): (100, 99, int(1.5 * 1024 ** 3)),
            str(existing): (100, 99, int(1.5 * 1024 ** 3)),
        },
        now=now,
        storage_path=storage,
        event_sink=events.append,
    )
    assert unchanged["events"] == []
    assert events.count(events[0]) == 1

    resolved_events = []
    resolved = run_health_check(
        config,
        runtime=_runtime(),
        credential_results=[cc.ProbeResult("youtube", cc.VALID, "Valid")],
        archive_roots=[("Archive", str(existing))],
        retry_circuits=[],
        disk_usage={str(existing): (100, 1, 99 * 1024 ** 3)},
        now=now,
        storage_path=storage,
        event_sink=resolved_events.append,
    )
    assert resolved["conditions"] == []
    assert resolved["status"] == "healthy"
    assert {event["state"] for event in resolved_events} == {"resolved"}
    assert load_health_snapshot(storage)["conditions"] == []


def test_public_health_snapshot_removes_local_targets():
    snapshot = {
        "conditions": [{"id": "disk:archive", "target_path": r"C:\private"}],
        "events": [{"condition": "disk:archive", "target_path": r"C:\private"}],
    }
    visible = public_snapshot(snapshot)
    assert visible["conditions"] == [{"id": "disk:archive"}]
    assert visible["events"] == [{"condition": "disk:archive"}]


def test_health_event_vocabulary_is_registered_with_structured_hooks():
    assert set(HEALTH_EVENT_BY_CATEGORY.values()).issubset(set(HOOK_EVENTS))


def test_repeated_bot_checks_name_the_class_and_platform():
    now = time.time()
    condition = health._extractor_conditions(
        {}, [{
            "source_key": "bot-source",
            "source_label": "Example",
            "engine": "yt-dlp",
            "failure_count": 3,
            "opened_until": 0,
            "last_category": "authentication",
            "last_classification": "bot-check",
            "last_reason": "challenge required",
        }], health._now_iso(now), now,
    )[0]

    assert "Repeated bot-check failures" in condition["title"]
    assert "Example" in condition["title"]
    assert condition["classification"] == "bot-check"
    assert "bot-check" in condition["detail"]


# ── V184: unattended work reports its own failure ───────────────────

def test_an_unattended_failure_survives_to_the_next_health_check(tmp_path):
    """Auto-record and retention run on a timer; a log line is invisible.

    The failure is recorded durably so the next health check raises it, because
    a probe cannot rediscover a go-live that already came and went (V184).
    """
    ledger = tmp_path / "unattended.json"

    health.record_unattended_failure(
        "auto_record", "example-channel",
        "Cannot create the output folder for example-channel: denied",
        repair="Check the output folder permissions",
        storage_path=ledger,
    )

    entries = health.load_unattended_failures(ledger)
    assert [entry["kind"] for entry in entries] == ["auto_record"]
    assert entries[0]["subject"] == "example-channel"

    conditions = health._unattended_conditions(
        "2026-08-08T00:00:00+00:00", storage_path=ledger,
    )
    assert len(conditions) == 1
    assert conditions[0]["category"] == "unattended"
    assert conditions[0]["severity"] == "error"
    assert "example-channel" in conditions[0]["title"]
    assert conditions[0]["event"] == "health_unattended_failed"


def test_the_same_channel_failing_twice_does_not_grow_the_ledger(tmp_path):
    ledger = tmp_path / "unattended.json"
    for attempt in range(5):
        health.record_unattended_failure(
            "auto_record", "example-channel", f"attempt {attempt}",
            storage_path=ledger,
        )

    entries = health.load_unattended_failures(ledger)
    assert len(entries) == 1, "one standing condition per kind+subject"
    assert entries[0]["detail"] == "attempt 4", "the newest cause is kept"


def test_a_recorded_failure_clears_when_the_same_work_succeeds(tmp_path):
    ledger = tmp_path / "unattended.json"
    health.record_unattended_failure(
        "retention", "chan-a", "could not recycle", storage_path=ledger,
    )
    health.record_unattended_failure(
        "retention", "chan-b", "could not recycle", storage_path=ledger,
    )

    assert health.clear_unattended_failure(
        "retention", "chan-a", storage_path=ledger,
    ) is True
    assert health.clear_unattended_failure(
        "retention", "chan-a", storage_path=ledger,
    ) is False, "clearing an absent entry is not an error"

    remaining = health.load_unattended_failures(ledger)
    assert [entry["subject"] for entry in remaining] == ["chan-b"], (
        "clearing one channel must not clear another"
    )


def test_a_corrupt_unattended_ledger_is_ignored_rather_than_fatal(tmp_path):
    ledger = tmp_path / "unattended.json"
    ledger.write_text("{not json", encoding="utf-8")

    assert health.load_unattended_failures(ledger) == []
    assert health._unattended_conditions("now", storage_path=ledger) == []
    # And it recovers: a fresh record replaces the unreadable file.
    health.record_unattended_failure(
        "auto_record", "c", "detail", storage_path=ledger,
    )
    assert len(health.load_unattended_failures(ledger)) == 1
