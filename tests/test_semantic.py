import array
import json
from pathlib import Path

from streamkeep import semantic
from streamkeep.backup import SQLITE_BACKUP_FILES
from streamkeep.metadata import COMMENTS_SCHEMA, COMMENTS_SCHEMA_VERSION


def _recording(tmp_path):
    recording = Path(tmp_path) / "recording"
    recording.mkdir()
    (recording / "episode.transcript.json").write_text(
        json.dumps({
            "segments": [{
                "start": 12, "end": 20,
                "text": "Quarterly revenue grew after the launch.",
            }],
        }),
        encoding="utf-8",
    )
    (recording / "episode.ocr.json").write_text(
        json.dumps({"segments": [{
            "start": 30, "end": 31,
            "text": "Screen title: release checklist",
            "confidence": 0.88,
        }]}),
        encoding="utf-8",
    )
    (recording / ".storyboard.json").write_text(
        json.dumps([{"time": 45, "label": "visual frame"}]),
        encoding="utf-8",
    )
    waveform = array.array("h", [20_000]) * (8_000 * 30)
    (recording / ".episode.waveform.bin").write_bytes(waveform.tobytes())
    (recording / "episode.comments.json").write_text(
        json.dumps({
            "schema": COMMENTS_SCHEMA,
            "schema_version": COMMENTS_SCHEMA_VERSION,
            "comments": [{"author": "Ada", "text": "Great launch"}],
        }),
        encoding="utf-8",
    )
    return recording


def test_local_semantic_index_is_bounded_and_returns_provenance(tmp_path, monkeypatch):
    recording = _recording(tmp_path)
    monkeypatch.setattr(semantic, "DB_PATH", Path(tmp_path) / "semantic.db")

    count, truncated = semantic.index_recording(
        str(recording), max_moments=20, max_bytes=2 * 1024 * 1024,
    )
    assert count >= 4
    assert truncated is False

    transcript_hit = semantic.search_moments("quarterly revenue")[0]
    assert transcript_hit["recording_path"] == str(recording)
    assert transcript_hit["modality"] == "transcript"
    assert transcript_hit["provenance"].startswith("transcript:")
    assert transcript_hit["confidence"] > 0
    assert semantic.search_moments("release checklist")[0]["modality"] == "ocr"
    assert semantic.search_moments("loud sound")[0]["modality"] == "audio"


def test_semantic_index_rebuild_cancellation_preserves_previous_rows(
    tmp_path, monkeypatch,
):
    recording = _recording(tmp_path)
    monkeypatch.setattr(semantic, "DB_PATH", Path(tmp_path) / "semantic.db")
    semantic.index_recording(str(recording))
    before = semantic.search_moments("quarterly revenue")

    assert semantic.index_recording(
        str(recording), cancel_check=lambda: True,
    ) == 0
    assert semantic.search_moments("quarterly revenue") == before


def test_semantic_worker_prunes_missing_paths_and_backup_excludes_index(
    tmp_path, monkeypatch,
):
    recording = _recording(tmp_path)
    monkeypatch.setattr(semantic, "DB_PATH", Path(tmp_path) / "semantic.db")
    done = []
    worker = semantic.SemanticIndexWorker([str(recording)], max_moments=10)
    worker.done.connect(done.append)
    worker.run()
    assert done and done[0]["recordings"] == 1
    assert semantic.index_status()["moments"] > 0
    assert semantic.DB_FILENAME not in SQLITE_BACKUP_FILES
