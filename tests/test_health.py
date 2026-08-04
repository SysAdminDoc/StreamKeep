from datetime import datetime, timezone

from streamkeep import credential_check as cc
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
        now=now,
        storage_path=storage,
        event_sink=events.append,
    )

    categories = {condition["category"] for condition in snapshot["conditions"]}
    assert categories == {"runtime", "credentials", "archive", "disk", "extractor"}
    assert snapshot["status"] == "error"
    assert {event["state"] for event in events} == {"opened"}
    assert {event["event"] for event in events} == set(HEALTH_EVENT_BY_CATEGORY.values())
    assert load_health_snapshot(storage)["conditions"]

    unchanged = run_health_check(
        config,
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
