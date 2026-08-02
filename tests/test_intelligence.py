import json
from pathlib import Path
from unittest import mock

import pytest

from streamkeep import db
from streamkeep.intelligence import runtime as intelligence_runtime
from streamkeep.intelligence import thumbnail


def _recording(tmp_path):
    recording = tmp_path / "recording"
    recording.mkdir()
    (recording / ".transcript.json").write_text(
        json.dumps([{"start": 1, "text": "hello world " * 20}]),
        encoding="utf-8",
    )
    return recording


def test_summary_runtime_records_provider_and_supports_edit(tmp_path):
    recording = _recording(tmp_path)
    with mock.patch.object(db, "DB_PATH", tmp_path / "library.db"):
        with mock.patch.object(
            intelligence_runtime,
            "summarize_recording",
            return_value="## Overview\nA local result",
        ):
            runtime = intelligence_runtime.IntelligenceRuntime()
            job = runtime.start_summary(str(recording), wait=True)
            assert job["status"] == "completed"
            assert job["provider"] == "ollama"
            assert job["provider_version"] == "ollama-local-v1"

            edited = runtime.edit_summary(job["job_id"], "## Overview\nEdited")
            assert edited["status"] == "completed"
            assert edited["edited"] is True
            assert (recording / ".summary.md").read_text(encoding="utf-8") == (
                "## Overview\nEdited"
            )


def test_cloud_preview_is_exact_and_one_use(tmp_path):
    recording = _recording(tmp_path)
    with mock.patch.object(db, "DB_PATH", tmp_path / "library.db"):
        runtime = intelligence_runtime.IntelligenceRuntime()
        preview = runtime.preview(
            str(recording), provider="openai", model="test-model",
            api_url="https://api.example.test", redact=True,
        )
        assert preview["requires_consent"] is True
        assert preview["redaction_applied"] is True
        assert preview["payload_sha256"]
        assert preview["consent_token"]
        with pytest.raises(Exception, match="API key"):
            runtime.start_summary(
                str(recording), provider="openai", model="test-model",
                api_url="https://api.example.test",
                consent_token=preview["consent_token"], wait=True,
            )


def test_cloud_profile_keeps_key_out_of_sqlite_and_requires_preview(tmp_path):
    recording = _recording(tmp_path)
    with mock.patch.object(db, "DB_PATH", tmp_path / "library.db"), \
            mock.patch.object(
                intelligence_runtime,
                "set_secret_value",
                return_value="secretref:intelligence-profile:cloud",
            ), mock.patch.object(
                intelligence_runtime,
                "get_secret_value",
                return_value="test-secret",
            ), mock.patch.object(
                intelligence_runtime,
                "summarize_recording",
                return_value="## Overview\nCloud result",
            ):
        runtime = intelligence_runtime.IntelligenceRuntime()
        intelligence_runtime.save_profile(
            "cloud", "openai", {
                "api_url": "https://api.example.test",
                "model": "test-model",
                "api_key": "test-secret",
            },
        )
        row = db.load_intelligence_profile("cloud")
        assert "test-secret" not in json.dumps(row)
        preview = runtime.preview(str(recording), profile_id="cloud")
        job = runtime.start_summary(
            str(recording), profile_id="cloud",
            consent_token=preview["consent_token"], wait=True,
        )
        assert job["status"] == "completed"


def test_smart_thumbnail_preserves_original_and_enforces_limits(tmp_path):
    pillow = pytest.importorskip("PIL.Image")
    recording = _recording(tmp_path)
    source_thumbnail = recording / "thumbnail.jpg"
    pillow.new("RGB", (64, 36), "blue").save(source_thumbnail, "JPEG")
    original = source_thumbnail.read_bytes()
    (recording / "capture.mp4").write_bytes(b"media")

    def fake_extract(_media, _at, output, _width, _height):
        Path(output).write_bytes(original)
        return True

    with mock.patch.object(thumbnail, "_probe_duration", return_value=10.0), \
            mock.patch.object(thumbnail, "_extract_frame", side_effect=fake_extract):
        path, score = thumbnail.generate_thumbnail(str(recording), num_candidates=1)

    assert Path(path).name == "smart-thumbnail.jpg"
    assert score >= 0
    assert source_thumbnail.read_bytes() == original
    assert Path(path).is_file()

    with mock.patch.object(thumbnail, "MAX_THUMBNAIL_PIXELS", 10):
        with pytest.raises(ValueError, match="Pillow limits"):
            thumbnail._open_bounded_image(str(source_thumbnail))
