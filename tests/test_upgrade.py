"""Identity and crash-safety coverage for monitor quality upgrades."""

from types import SimpleNamespace
from unittest import mock

from streamkeep.upgrade import (
    UpgradeSafetyError,
    activate_upgrade_version,
    evaluate_upgrade,
    identity_matches,
    list_upgrade_versions,
    plan_upgrade_paths,
    prepare_upgrade_staging,
    quality_rank,
)
from streamkeep.verify import STATUS_FAIL, STATUS_OK
from streamkeep.metadata import MetadataWriteError
from streamkeep.job_spec import DownloadJobSpec
from streamkeep import db
from streamkeep.models import HistoryEntry, MonitorEntry, VODInfo
from streamkeep.ui.tabs.monitor import MonitorTabMixin
from streamkeep.workers.finalize import FinalizeWorker


def _info():
    return SimpleNamespace(
        platform="Twitch",
        source_id="vod:123",
        url="https://delivery.invalid/vod/123.m3u8?token=secret",
        title="Test recording",
        channel="creator",
        chapters=[],
        thumbnail_url="",
        feed_url="",
        total_secs=60,
    )


def _upgrade_task(paths):
    return {
        "info": _info(),
        "out_dir": str(paths.staging),
        "upgrade_existing_path": str(paths.existing),
        "upgrade_final_dir": str(paths.final),
        "is_upgrade": True,
        "record_manifest": True,
        "source_id": "vod:123",
        "expected_duration": 60,
    }


def test_identity_requires_exact_platform_scoped_match():
    assert identity_matches("Twitch", "vod:123", "twitch", "vod:123")
    assert not identity_matches("Twitch", "vod:123", "Twitch", "vod:124")
    assert not identity_matches("Twitch", "vod:123", "Kick", "vod:123")
    assert not identity_matches("", "vod:123", "Twitch", "vod:123")
    assert not identity_matches("Twitch", "", "Twitch", "")


def test_quality_rank_compares_height_in_resolution_labels():
    assert quality_rank("1920x1080") == 1080
    assert quality_rank("1080p60") == 1080
    assert quality_rank("1280x720") == 720
    assert quality_rank("source") > quality_rank("2160p")


def test_upgrade_profile_has_ordered_cutoff_and_hard_veto_matchers():
    profile = {
        "ladder": ["720p", "1080p", "2160p"],
        "cutoff": "1080p",
        "minimum_score": 5,
        "matchers": [
            {"name": "official", "field": "title", "pattern": "official", "score": 5},
            {"name": "ad", "field": "title", "pattern": "ad", "score": -100},
        ],
    }
    base = {"platform": "Twitch", "source_id": "vod:1", "quality": "720p"}
    accepted = evaluate_upgrade(
        base,
        {
            **base,
            "quality": "1080p",
            "title": "Official recording",
        },
        profile,
    )
    assert accepted.accepted
    assert accepted.reason_code == "quality_upgrade_eligible"

    vetoed = evaluate_upgrade(
        base,
        {
            **base,
            "quality": "1080p",
            "title": "Official ad break",
        },
        profile,
    )
    assert vetoed.decision == "rejected"
    assert vetoed.reason_code == "matcher_veto"

    above_cutoff = evaluate_upgrade(
        base,
        {**base, "quality": "2160p", "title": "Official recording"},
        profile,
    )
    assert above_cutoff.reason_code == "above_upgrade_cutoff"


def test_upgrade_decisions_are_durable_and_projected_on_history(tmp_path):
    recording = tmp_path / "recording"
    recording.mkdir()
    with mock.patch.object(db, "DB_PATH", tmp_path / "library.db"):
        db.init_db()
        history_id = db.save_history_entry({
            "platform": "Twitch",
            "source_id": "vod:1",
            "title": "Episode",
            "quality": "720p",
            "path": str(recording),
        })
        decision_id = db.record_upgrade_decision(
            {
                "decision": "rejected",
                "reason_code": "matcher_veto",
                "reason": "Candidate rejected by hard-veto matcher 'ad'",
                "platform": "Twitch",
                "source_id": "vod:1",
                "current_quality": "720p",
                "candidate_quality": "1080p",
            },
            history_id=history_id,
            title="Episode",
            profile={"ladder": ["720p", "1080p"], "cutoff": "1080p"},
        )
        assert decision_id
        rows = db.list_upgrade_decisions(history_id=history_id)
        assert rows[0]["reason_code"] == "matcher_veto"
        projected = db.query_history_page(limit=1)[0]

    assert projected["upgrade_decision"] == "rejected"
    assert projected["upgrade_reason_code"] == "matcher_veto"


def test_upgrade_update_keeps_stable_history_state_and_manifest(tmp_path):
    recording = tmp_path / "recording"
    replacement = tmp_path / "recording-upgraded"
    recording.mkdir()
    replacement.mkdir()
    db_path = tmp_path / "library.db"
    with mock.patch.object(db, "DB_PATH", db_path):
        db.init_db()
        history_id = db.save_completed_recording({
            "date": "2026-08-03 12:00",
            "platform": "Twitch",
            "source_id": "vod:stable",
            "webpage_url": "https://www.twitch.tv/videos/123",
            "title": "Episode",
            "channel": "creator",
            "quality": "720p",
            "size": "1 MB",
            "path": str(recording),
            "url": "https://www.twitch.tv/videos/123",
            "favorite": True,
            "watched": True,
            "watch_position_secs": 42,
            "bookmarks": [{"name": "intro", "secs": 4}],
        })
        db.update_history_entry(history_id, {
            "favorite": True,
            "watched": True,
            "watch_position_secs": 42,
            "bookmarks": [{"name": "intro", "secs": 4}],
        })
        manifest = {"files": [{"path": "episode.mp4", "size": 12}]}
        assert db.update_completed_recording(
            history_id,
            {"quality": "1080p", "path": str(replacement)},
            manifest,
        ) == history_id
        updated = db.find_history_by_identity("twitch", "vod:stable")
        archive_manifest = db.load_archive_manifest(history_id)

    assert updated["id"] == history_id
    assert updated["quality"] == "1080p"
    assert updated["path"] == str(replacement)
    assert updated["title"] == "Episode"
    assert updated["webpage_url"] == "https://www.twitch.tv/videos/123"
    assert updated["favorite"]
    assert updated["watched"]
    assert updated["watch_position_secs"] == 42
    assert updated["bookmarks"] == [{"name": "intro", "secs": 4}]
    assert archive_manifest["recording_path"] == str(replacement)
    assert archive_manifest["manifest"] == manifest


def test_upgrade_deferred_and_malformed_profiles_have_named_outcomes():
    base = {"platform": "Twitch", "source_id": "vod:1", "quality": "720p"}
    profile = {"ladder": ["720p", "1080p"], "cutoff": "1080p"}
    deferred = evaluate_upgrade(
        base,
        {**base, "quality": ""},
        profile,
        defer_unknown_quality=True,
    )
    assert deferred.decision == "deferred"
    assert deferred.platform == "Twitch"
    assert deferred.source_id == "vod:1"
    invalid = evaluate_upgrade(base, {**base, "quality": "1080p"}, [])
    assert invalid.reason_code == "invalid_profile"


def test_version_retention_keeps_known_good_and_bounded_siblings(tmp_path):
    existing = tmp_path / "recording"
    existing.mkdir()
    original = existing / "known-good.txt"
    original.write_text("known good", encoding="utf-8")
    for index in range(4):
        paths = plan_upgrade_paths(existing, f"job-{index:012d}", f"{720 + index * 120}p")
        prepare_upgrade_staging(paths)
        (paths.staging / "new.txt").write_text(str(index), encoding="utf-8")
        activate_upgrade_version(paths, version_keep=2)

    assert original.read_text(encoding="utf-8") == "known good"
    assert len(list_upgrade_versions(existing)) <= 2


def test_source_identity_survives_immutable_job_round_trip():
    spec = DownloadJobSpec(
        source_platform="Twitch",
        source_id="vod:123",
        webpage_url="https://www.twitch.tv/videos/123",
    )

    restored = DownloadJobSpec.from_dict(spec.to_dict())

    assert restored.source_platform == "Twitch"
    assert restored.source_id == "vod:123"
    assert restored.webpage_url == "https://www.twitch.tv/videos/123"


def test_persisted_job_identity_drops_delivery_query_credentials():
    payload = DownloadJobSpec(
        source_platform="Example",
        source_id="video:abc",
        webpage_url=(
            "https://media.example.com/watch/abc?"
            "token=secret&signature=signed"
        ),
    ).to_dict()

    assert payload["source_id"] == "video:abc"
    assert payload["webpage_url"] == "https://media.example.com/watch/abc"
    assert "secret" not in str(payload)


def test_monitor_uses_exact_identity_not_latest_channel_row(tmp_path):
    target_dir = tmp_path / "target"
    unrelated_dir = tmp_path / "unrelated"
    target_dir.mkdir()
    unrelated_dir.mkdir()
    db_path = tmp_path / "library.db"
    with mock.patch.object(db, "DB_PATH", db_path):
        db.init_db()
        target_id = db.save_history_entry({
            "platform": "Twitch",
            "source_id": "vod:111",
            "channel": "creator",
            "quality": "720p",
            "path": str(target_dir),
        })
        db.save_history_entry({
            "platform": "Twitch",
            "source_id": "vod:222",
            "channel": "creator",
            "quality": "2160p",
            "path": str(unrelated_dir),
        })
        dummy = SimpleNamespace(
            monitor=SimpleNamespace(entries=[
                MonitorEntry(
                    channel_id="creator",
                    auto_upgrade=True,
                    min_upgrade_quality="1080p",
                )
            ]),
            _quality_rank=quality_rank,
        )
        eligible = MonitorTabMixin._check_quality_upgrade(
            dummy,
            "creator",
            VODInfo(
                platform="Twitch",
                channel="creator",
                source_id="vod:111",
            ),
            "1080p",
        )

    assert eligible is not None
    assert eligible.db_id == target_id
    assert eligible.source_id == "vod:111"
    assert eligible.quality == "720p"


def test_monitor_upgrade_bypasses_archive_only_for_that_job():
    captured = []
    queue = []

    def queue_add(url, **kwargs):
        captured.append((url, kwargs))
        queue.append(dict(kwargs))
        return True

    dummy = SimpleNamespace(
        monitor=SimpleNamespace(entries=[
            MonitorEntry(
                url="https://www.twitch.tv/creator",
                channel_id="creator",
                auto_upgrade=True,
                min_upgrade_quality="1080p",
            )
        ]),
        _check_quality_upgrade=lambda _channel, _vod: HistoryEntry(
            db_id=7,
            platform="Twitch",
            source_id="vod:111",
            quality="720p",
            path="C:/archive/old",
        ),
        _find_duplicate=lambda *_args, **_kwargs: None,
        _queue_add=queue_add,
        _download_queue=queue,
        _apply_sponsorblock_delay=lambda *_args: None,
        _log=lambda *_args: None,
        download_worker=SimpleNamespace(isRunning=lambda: True),
    )
    vod = VODInfo(
        title="One VOD",
        source="111",
        platform="Twitch",
        channel="creator",
        source_id="vod:111",
        webpage_url="https://www.twitch.tv/videos/111",
    )

    MonitorTabMixin._on_new_vods_found(dummy, "creator", [vod])

    assert len(captured) == 1
    _url, options = captured[0]
    assert options["is_upgrade"]
    assert options["source_id"] == "vod:111"
    assert options["download_archive"] == ""
    assert not options["break_on_existing"]
    assert options["upgrade_history_id"] == 7


def test_upgrade_activation_versions_instead_of_replacing(tmp_path):
    existing = tmp_path / "recording"
    existing.mkdir()
    marker = existing / "known-good.txt"
    marker.write_text("old", encoding="utf-8")
    paths = plan_upgrade_paths(existing, "12345678abcdef00", "1080p")
    prepare_upgrade_staging(paths)
    (paths.staging / "new.mp4").write_bytes(b"new")

    activated = activate_upgrade_version(paths)

    assert activated == paths.final
    assert marker.read_text(encoding="utf-8") == "old"
    assert (paths.final / "new.mp4").read_bytes() == b"new"
    assert not paths.staging.exists()


def test_failed_atomic_activation_leaves_old_and_stage_intact(tmp_path):
    existing = tmp_path / "recording"
    existing.mkdir()
    marker = existing / "known-good.txt"
    marker.write_text("old", encoding="utf-8")
    paths = plan_upgrade_paths(existing, "12345678abcdef00", "1080p")
    prepare_upgrade_staging(paths)
    staged = paths.staging / "new.mp4"
    staged.write_bytes(b"new")

    with mock.patch(
        "streamkeep.upgrade.os.replace", side_effect=OSError("disk error")
    ):
        try:
            activate_upgrade_version(paths)
        except OSError:
            pass
        else:  # pragma: no cover
            raise AssertionError("activation unexpectedly succeeded")

    assert marker.read_text(encoding="utf-8") == "old"
    assert staged.read_bytes() == b"new"
    assert not paths.final.exists()


def test_prepare_refuses_missing_known_good_directory(tmp_path):
    paths = plan_upgrade_paths(
        tmp_path / "missing", "12345678abcdef00", "1080p"
    )
    try:
        prepare_upgrade_staging(paths)
    except UpgradeSafetyError as error:
        assert "Known-good" in str(error)
    else:  # pragma: no cover
        raise AssertionError("missing recording was accepted")


def test_probe_failure_never_activates_upgrade(tmp_path):
    existing = tmp_path / "recording"
    existing.mkdir()
    (existing / "known-good.txt").write_text("old", encoding="utf-8")
    paths = plan_upgrade_paths(existing, "12345678abcdef00", "1080p")
    prepare_upgrade_staging(paths)
    (paths.staging / "new.mp4").write_bytes(b"new")
    worker = FinalizeWorker(_upgrade_task(paths))
    results = []
    worker.done.connect(results.append)

    with (
        mock.patch(
            "streamkeep.workers.finalize.MetadataSaver.save",
            return_value={},
        ),
        mock.patch(
            "streamkeep.workers.finalize.verify_recording_dir",
            return_value=(STATUS_FAIL, "ffprobe failed", ""),
        ),
    ):
        worker.run()

    assert len(results) == 1
    assert "ffprobe failed" in results[0]["archive_manifest_error"]
    assert not results[0]["upgrade_activated"]
    assert paths.staging.is_dir()
    assert existing.is_dir()
    assert not paths.final.exists()


def test_disk_full_during_metadata_never_activates_upgrade(tmp_path):
    existing = tmp_path / "recording"
    existing.mkdir()
    old = existing / "known-good.txt"
    old.write_text("old", encoding="utf-8")
    paths = plan_upgrade_paths(existing, "12345678abcdef00", "1080p")
    prepare_upgrade_staging(paths)
    worker = FinalizeWorker(_upgrade_task(paths))
    results = []
    worker.done.connect(results.append)

    with mock.patch(
        "streamkeep.workers.finalize.MetadataSaver.save",
        side_effect=MetadataWriteError("disk full"),
    ):
        worker.run()

    assert results[0]["finalize_error"] == "disk full"
    assert not results[0]["upgrade_activated"]
    assert old.read_text(encoding="utf-8") == "old"
    assert paths.staging.is_dir()
    assert not paths.final.exists()


def test_manifest_failure_never_activates_upgrade(tmp_path):
    existing = tmp_path / "recording"
    existing.mkdir()
    old = existing / "known-good.txt"
    old.write_text("old", encoding="utf-8")
    paths = plan_upgrade_paths(existing, "12345678abcdef00", "1080p")
    prepare_upgrade_staging(paths)
    (paths.staging / "new.mp4").write_bytes(b"new")
    worker = FinalizeWorker(_upgrade_task(paths))
    results = []
    worker.done.connect(results.append)

    with (
        mock.patch(
            "streamkeep.workers.finalize.MetadataSaver.save",
            return_value={},
        ),
        mock.patch(
            "streamkeep.workers.finalize.verify_recording_dir",
            return_value=(STATUS_OK, "valid media", "new.mp4"),
        ),
        mock.patch(
            "streamkeep.workers.finalize.verify_archive_manifest",
            return_value=(STATUS_FAIL, "checksum mismatch", {}),
        ),
    ):
        worker.run()

    assert "checksum mismatch" in results[0]["archive_manifest_error"]
    assert not results[0]["upgrade_activated"]
    assert old.read_text(encoding="utf-8") == "old"
    assert paths.staging.is_dir()
    assert not paths.final.exists()


def test_cancelled_finalize_never_activates_upgrade(tmp_path):
    existing = tmp_path / "recording"
    existing.mkdir()
    paths = plan_upgrade_paths(existing, "12345678abcdef00", "1080p")
    prepare_upgrade_staging(paths)
    worker = FinalizeWorker(_upgrade_task(paths))
    results = []
    worker.done.connect(results.append)
    worker.cancel()

    worker.run()

    assert results[0]["cancelled"]
    assert not results[0]["upgrade_activated"]
    assert existing.is_dir()
    assert paths.staging.is_dir()
    assert not paths.final.exists()


def test_verified_upgrade_is_atomically_activated(tmp_path):
    existing = tmp_path / "recording"
    existing.mkdir()
    old = existing / "known-good.txt"
    old.write_text("old", encoding="utf-8")
    paths = plan_upgrade_paths(existing, "12345678abcdef00", "1080p")
    prepare_upgrade_staging(paths)
    (paths.staging / "new.mp4").write_bytes(b"new media")
    worker = FinalizeWorker(_upgrade_task(paths))
    results = []
    worker.done.connect(results.append)

    with (
        mock.patch(
            "streamkeep.workers.finalize.MetadataSaver.save",
            return_value={},
        ),
        mock.patch(
            "streamkeep.workers.finalize.verify_recording_dir",
            return_value=(STATUS_OK, "valid media", "new.mp4"),
        ),
    ):
        worker.run()

    assert results[0]["upgrade_activated"]
    assert results[0]["out_dir"] == str(paths.final)
    assert old.read_text(encoding="utf-8") == "old"
    assert (paths.final / "new.mp4").read_bytes() == b"new media"
    assert (paths.final / ".streamkeep_manifest.json").is_file()
    assert not paths.staging.exists()
