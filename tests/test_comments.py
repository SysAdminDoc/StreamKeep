import json
from pathlib import Path
from types import SimpleNamespace

from streamkeep.metadata import (
    COMMENTS_SCHEMA,
    COMMENTS_SCHEMA_VERSION,
    MetadataSaver,
    normalize_ytdlp_comments,
)
from streamkeep.workers.finalize import FinalizeWorker
from streamkeep.workers.download import DownloadWorker
from streamkeep import search


def test_ytdlp_comment_capture_is_opt_in_and_youtube_bound(tmp_path):
    worker = DownloadWorker(
        "", [(0, "Episode", 0, 1)], str(tmp_path), "ytdlp_direct",
    )
    worker.ytdlp_source = "https://www.youtube.com/watch?v=episode"
    worker.ytdlp_format = "bv"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.capture_comments = True
    cmd = worker._build_ytdlp_download_cmd(
        str(tmp_path / "Episode.%(ext)s")
    )
    assert "--write-comments" in cmd
    assert "--write-info-json" in cmd

    worker.ytdlp_source = "https://example.com/video"
    cmd = worker._build_ytdlp_download_cmd(
        str(tmp_path / "Other.%(ext)s")
    )
    assert "--write-comments" not in cmd


def test_normalize_ytdlp_comments_keeps_published_fields_and_bounds_count():
    payload = normalize_ytdlp_comments(
        {
            "id": "video-1",
            "webpage_url": "https://www.youtube.com/watch?v=video-1",
            "comments": [
                {
                    "id": "c1",
                    "parent": "",
                    "author": "Ada",
                    "author_id": "private-profile-id",
                    "text": "First comment",
                    "timestamp": 1_735_689_600,
                    "like_count": 3,
                },
                {
                    "id": "c2",
                    "author": "Grace",
                    "text": "Second comment",
                },
            ],
        },
        max_count=1,
        max_bytes=4 * 1024 * 1024,
    )

    assert payload["schema"] == COMMENTS_SCHEMA
    assert payload["schema_version"] == COMMENTS_SCHEMA_VERSION
    assert payload["status"] == "captured"
    assert payload["count"] == 1
    assert payload["truncated"] is True
    assert payload["comments"][0]["author"] == "Ada"
    assert payload["comments"][0]["published_at"]
    assert "author_id" not in payload["comments"][0]


def test_write_comments_emits_versioned_sidecar_and_unavailable_status(tmp_path):
    info_path = tmp_path / "Episode.info.json"
    info_path.write_text(
        json.dumps({
            "id": "ep-1",
            "webpage_url": "https://youtube.com/watch?v=ep-1",
            "comments": [
                {"id": "c1", "author": "Lin", "text": "Hello archive"},
            ],
        }),
        encoding="utf-8",
    )
    result = MetadataSaver.write_comments(
        str(tmp_path), "Episode", info_path=str(info_path), max_count=10,
    )
    sidecar = json.loads(
        (tmp_path / "Episode.comments.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "captured"
    assert sidecar["comments"][0]["text"] == "Hello archive"

    missing = MetadataSaver.write_comments(str(tmp_path), "Missing")
    missing_payload = json.loads(
        (tmp_path / "Missing.comments.json").read_text(encoding="utf-8")
    )
    assert missing["status"] == "unavailable"
    assert missing_payload["comments"] == []
    assert "refuse or rate-limit" in missing_payload["reason"]


def test_finalize_comment_failure_is_nonfatal_and_logs_reason(tmp_path):
    (tmp_path / "Episode.info.json").write_text(
        json.dumps({"comments": None}), encoding="utf-8"
    )
    worker = FinalizeWorker({
        "info": SimpleNamespace(
            platform="yt-dlp", title="Episode", url="https://youtube.com/watch?v=1",
            webpage_url="https://youtube.com/watch?v=1", source_id="1",
            thumbnail_url="", chapters=[], markers=[], marker_schedules=[],
            qualities=[], tags=[], channel="", duration_str="", total_secs=1,
            start_time="", is_live=False, podcast_metadata=None,
        ),
        "out_dir": str(tmp_path),
        "file_base": "Episode",
        "history_url": "https://youtube.com/watch?v=1",
        "capture_comments": True,
        "record_manifest": False,
    })
    done = []
    logs = []
    worker.done.connect(done.append)
    worker.log.connect(logs.append)
    worker.run()

    assert done and done[0]["finalize_error"] == ""
    assert any("[COMMENTS] Unavailable" in line for line in logs)
    sidecar = json.loads(
        (tmp_path / "Episode.comments.json").read_text(encoding="utf-8")
    )
    assert sidecar["status"] == "unavailable"


def test_comment_sidecars_are_indexed_by_author_and_text(tmp_path, monkeypatch):
    recording = Path(tmp_path) / "recording"
    recording.mkdir()
    (recording / "Episode.comments.json").write_text(
        json.dumps({
            "schema": COMMENTS_SCHEMA,
            "schema_version": COMMENTS_SCHEMA_VERSION,
            "comments": [
                {
                    "id": "c1", "author": "Lin", "text": "Searchable phrase",
                    "published_at": "2026-08-03T12:00:00+00:00",
                },
            ],
        }),
        encoding="utf-8",
    )
    db_path = Path(tmp_path) / "search.db"
    monkeypatch.setattr(search, "DB_PATH", db_path)

    assert search.index_recording(str(recording)) == 0
    assert search.search_comments("Lin")[0]["text"] == "Searchable phrase"
    assert search.search_comments("phrase")[0]["author"] == "Lin"

    (recording / "Episode.comments.json").unlink()
    search.index_recording(str(recording))
    assert search.search_comments("phrase") == []


def test_comment_index_skips_unsupported_schema_and_bad_like_count(
    tmp_path, monkeypatch,
):
    recording = Path(tmp_path) / "recording"
    recording.mkdir()
    sidecar = recording / "Episode.comments.json"
    sidecar.write_text(
        json.dumps({
            "schema": COMMENTS_SCHEMA,
            "schema_version": COMMENTS_SCHEMA_VERSION,
            "comments": [{
                "author": "Lin", "text": "Resilient phrase",
                "like_count": "not-a-number",
            }],
        }),
        encoding="utf-8",
    )
    db_path = Path(tmp_path) / "search.db"
    monkeypatch.setattr(search, "DB_PATH", db_path)

    assert search.index_recording(str(recording)) == 0
    hit = search.search_comments("Resilient")
    assert hit[0]["like_count"] == 0

    sidecar.write_text(
        json.dumps({
            "schema": COMMENTS_SCHEMA,
            "schema_version": COMMENTS_SCHEMA_VERSION + 1,
            "comments": [{"author": "Lin", "text": "Future phrase"}],
        }),
        encoding="utf-8",
    )
    search.index_recording(str(recording))
    assert search.search_comments("Future") == []
