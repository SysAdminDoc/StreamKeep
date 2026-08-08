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


# ── V183: the summarize provider transport ──────────────────────────

def test_an_http_summary_endpoint_is_refused_before_the_key_is_attached():
    """A cleartext base URL must never receive the API key.

    ``intelligence/summarize.py`` used to build the URL and attach
    ``Authorization: Bearer`` with no scheme check, so a configured
    ``http://`` endpoint sent the key over a plaintext socket (V183).
    """
    from streamkeep.intelligence import summarize

    attempted = []

    def _fail_if_called(*args, **kwargs):
        attempted.append((args, kwargs))
        raise AssertionError("the request must not be issued at all")

    with mock.patch.object(summarize, "guarded_json_post", _fail_if_called):
        logged = []
        result = summarize._query_openai_compat(
            "prompt", api_url="http://10.0.0.5:8080", api_key="sk-secret",
            log_fn=logged.append,
        )

    assert result == ""
    assert not attempted, "a cleartext endpoint was contacted"
    assert any("https://" in line for line in logged), logged
    assert not any("sk-secret" in line for line in logged), (
        "the API key must not be echoed into the log"
    )


def test_the_summary_providers_go_through_the_guarded_transport():
    """Both cloud providers must post through ``net_guard.guarded_json_post``."""
    from streamkeep.intelligence import summarize

    calls = []

    def _capture(url, *, data, headers, timeout, **kwargs):
        calls.append((url, headers, kwargs.get("subject", "")))
        if "anthropic" in url:
            return {"content": [{"text": "anthropic-summary"}]}
        return {"choices": [{"message": {"content": "openai-summary"}}]}

    with mock.patch.object(summarize, "guarded_json_post", _capture):
        openai_text = summarize._query_openai_compat(
            "prompt", api_url="https://api.example.invalid",
            api_key="k", log_fn=None,
        )
        anthropic_text = summarize._query_anthropic(
            "prompt", api_key="k", log_fn=None,
        )

    assert openai_text == "openai-summary"
    assert anthropic_text == "anthropic-summary"
    assert calls[0][0] == "https://api.example.invalid/v1/chat/completions"
    assert calls[1][0] == summarize.ANTHROPIC_ENDPOINT
    assert len(calls) == 2, "every cloud provider must use the guarded transport"


def test_an_oversized_ollama_answer_is_refused_rather_than_parsed():
    """The local endpoint is unauthenticated, so its answer must be bounded."""
    from streamkeep.intelligence import summarize

    class _Response:
        def read(self, size=-1):
            # More than the cap, whatever the cap is.
            return b"x" * (summarize.MAX_PROVIDER_RESPONSE_BYTES + 64)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    logged = []
    with mock.patch.object(
        summarize.urllib.request, "urlopen", lambda *a, **k: _Response()
    ):
        result = summarize._query_ollama("prompt", log_fn=logged.append)

    assert result == ""
    assert any("larger than" in line for line in logged), logged
