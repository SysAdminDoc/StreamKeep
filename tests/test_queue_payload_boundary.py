from unittest import mock

import pytest

from streamkeep.headless_service import HeadlessJobService
from streamkeep.job_spec import split_remote_queue_fields
from streamkeep.preflight import filter_remote_queue_payload


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("is_upgrade", True),
        ("upgrade_profile", {"version_keep": 1}),
        ("upgrade_min_quality", "720p"),
        ("version_keep", 1),
        ("download_archive", "C:/outside/archive.txt"),
        ("future_executor_field", "surprise"),
    ],
)
def test_remote_queue_rejects_executor_fields_without_failing(tmp_path, key, value):
    filtered, rejected = filter_remote_queue_payload(
        {"url": "https://example.com/video", key: value},
        output_root=str(tmp_path / "library"),
    )

    assert filtered == {"url": "https://example.com/video"}
    assert key in rejected


def test_remote_output_dir_is_confined_to_configured_root(tmp_path):
    root = tmp_path / "library"
    inside = root / "nested"
    outside = tmp_path / "outside"

    filtered, rejected = filter_remote_queue_payload(
        {"url": "https://example.com/video", "output_dir": str(inside)},
        output_root=str(root),
    )
    assert filtered["output_dir"] == str(inside)
    assert rejected == ()

    filtered, rejected = filter_remote_queue_payload(
        {"url": "https://example.com/video", "output_dir": str(outside)},
        output_root=str(root),
    )
    assert "output_dir" not in filtered
    assert rejected == ("output_dir",)


def test_unknown_remote_field_stays_out_of_download_job_boundary():
    accepted, rejected = split_remote_queue_fields(
        {
            "url": "https://example.com/video",
            "future_executor_field": "must not reach DownloadJobSpec",
        }
    )

    assert accepted == {"url": "https://example.com/video"}
    assert rejected == ("future_executor_field",)


def test_headless_enqueue_logs_and_drops_rejected_fields(tmp_path):
    submitted = {}

    def capture(item):
        submitted.update(item)
        return {**item, "job_id": "job-1"}

    service = HeadlessJobService(output_dir=str(tmp_path / "library"))
    with (
        mock.patch("streamkeep.headless_service.db.enqueue_queue_job", side_effect=capture),
        mock.patch("streamkeep.headless_service.write_log_line") as log,
    ):
        service.enqueue(
            {
                "url": "https://example.com/video",
                "is_upgrade": True,
                "upgrade_profile": {"version_keep": 1},
                "download_archive": str(tmp_path / "archive.txt"),
                "future_executor_field": "ignored",
                "output_dir": str(tmp_path / "outside"),
            }
        )

    for key in (
        "is_upgrade",
        "upgrade_profile",
        "download_archive",
        "future_executor_field",
    ):
        assert key not in submitted
    assert submitted["output_dir"] == str(tmp_path / "library")
    messages = [call.args[0] for call in log.call_args_list]
    assert any(
        message.startswith("[QUEUE] Ignored remote queue fields:")
        and "is_upgrade" in message
        and "download_archive" in message
        for message in messages
    )
