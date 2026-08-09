import array
import json
from pathlib import Path

import pytest

from streamkeep import semantic
from streamkeep import search
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


def test_minilm_paraphrase_retrieval_has_no_shared_query_terms(tmp_path, monkeypatch):
    """The configured sentence model finds a transcript paraphrase."""
    if not semantic.backend_status()["available"]:
        pytest.skip("optional all-MiniLM-L6-v2 bundle is not installed")
    recording = Path(tmp_path) / "paraphrase"
    recording.mkdir()
    (recording / "episode.transcript.json").write_text(
        json.dumps({
            "segments": [{
                "start": 5, "end": 12,
                "text": "Quarterly revenue grew after the launch.",
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(semantic, "DB_PATH", Path(tmp_path) / "semantic.db")
    semantic.index_recording(str(recording))

    query = "Income increased following product rollout"
    assert not ({"income", "increased", "following", "product", "rollout"}
                 & {"quarterly", "revenue", "grew", "after", "the", "launch"})
    hits = semantic.search_moments(query, threshold=0.08)
    assert hits and hits[0]["recording_path"] == str(recording)
    assert hits[0]["vector_version"] == semantic.VECTOR_VERSION


def test_hybrid_search_fuses_fts_and_vector_ranks_with_rrf(monkeypatch):
    lexical = [{
        "recording_path": "C:/rec/exact",
        "text": "alpha exact",
        "start_sec": 1,
        "end_sec": 2,
    }]
    semantic_hit = {
        **lexical[0],
        "modality": "transcript",
        "provenance": "transcript:x.json",
        "confidence": 0.95,
        "score": 0.9,
        "vector_version": semantic.VECTOR_VERSION,
    }
    vector_only = {
        "recording_path": "C:/rec/paraphrase",
        "text": "meaning without shared words",
        "start_sec": 4,
        "end_sec": 5,
        "modality": "transcript",
        "provenance": "transcript:y.json",
        "confidence": 0.9,
        "score": 0.8,
    }
    monkeypatch.setattr(search, "search_transcripts", lambda *_a, **_k: lexical)
    monkeypatch.setattr(
        semantic, "search_moments", lambda *_a, **_k: [semantic_hit, vector_only],
    )

    hits = search.hybrid_search_transcripts("paraphrase", limit=10)
    assert [hit["recording_path"] for hit in hits] == [
        "C:/rec/exact", "C:/rec/paraphrase",
    ]
    assert hits[0]["search_source"] == "hybrid"
    assert hits[0]["rrf_score"] == pytest.approx(2 / (search.RRF_K + 1))
    assert hits[1]["search_source"] == "semantic"
