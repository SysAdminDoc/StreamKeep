import json
from pathlib import Path

import pytest

from streamkeep import translation
from streamkeep.models import StreamInfo
from streamkeep.metadata import MetadataSaver
from streamkeep.translation import (
    TRANSLATION_SCHEMA,
    TranslationConsentRequired,
    translate_payload,
    translate_recording,
)


def _query(_prompt, **_kwargs):
    return json.dumps({
        "title": "Título traducido",
        "description": "Descripción traducida",
        "chapters": [{"title": "Introducción traducida"}],
    })


def test_translation_preserves_original_fields_and_timing():
    payload = translate_payload(
        {"title": "Original title", "description": "Original description"},
        [{"title": "Intro", "start": 2, "end": 8}],
        "es",
        query_fn=_query,
    )

    assert payload["schema"] == TRANSLATION_SCHEMA
    assert payload["target_language"] == "es"
    assert payload["original"]["title"] == "Original title"
    assert payload["translated"]["title"] == "Título traducido"
    assert payload["translated"]["chapters"] == [{
        "title": "Introducción traducida", "start": 2.0, "end": 8.0,
    }]


def test_cloud_translation_requires_explicit_per_run_consent():
    called = []

    def query(*_args, **_kwargs):
        called.append(True)
        return "{}"

    with pytest.raises(TranslationConsentRequired):
        translate_payload(
            {"title": "Title"}, [], "es", provider="openai",
            query_fn=query,
        )
    assert called == []


def test_translation_writes_localized_sidecars_and_keeps_originals(tmp_path):
    recording = Path(tmp_path)
    MetadataSaver.save(
        str(recording),
        StreamInfo(
            platform="yt-dlp", title="Original title",
            description="Original description", source_id="video-1",
            webpage_url="https://example.com/video",
            chapters=[{"title": "Intro", "start": 2, "end": 8}],
        ),
    )
    MetadataSaver.write_chapters(
        str(recording),
        StreamInfo(chapters=[{"title": "Intro", "start": 2, "end": 8}]),
        file_base="Original title",
    )

    result = translate_recording(
        str(recording), file_base="Original title", target_language="es",
        query_fn=_query,
    )

    assert result["status"] == "translated"
    assert Path(result["metadata_path"]).is_file()
    assert Path(result["chapters_path"]).is_file()
    assert Path(result["nfo_path"]).is_file()
    assert (recording / "metadata.json").is_file()
    assert (recording / "Original title.chapters.json").is_file()
    localized = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
    assert localized["original"]["description"] == "Original description"
    assert localized["translated"]["title"] == "Título traducido"
    nfo = Path(result["nfo_path"]).read_text(encoding="utf-8")
    assert "Título traducido" in nfo
    assert "Original title" in nfo


def test_provider_url_must_be_https():
    with pytest.raises(translation.TranslationError, match="https://"):
        translation.normalize_provider_api_url("http://10.0.0.5:8080")
    with pytest.raises(translation.TranslationError, match="https://"):
        translation.normalize_provider_api_url("ftp://example.com")


def test_provider_url_is_subject_to_network_policy_at_request_time():
    with pytest.raises(translation.TranslationError, match="network policy"):
        translation.normalize_provider_api_url("https://127.0.0.1:8443")
    with pytest.raises(translation.TranslationError, match="network policy"):
        translation.normalize_provider_api_url("https://192.168.1.10")


def test_shape_only_validation_does_not_need_the_network():
    """Config import must not fail because the machine is offline."""
    assert translation.normalize_provider_api_url(
        "https://api.does-not-resolve.invalid/", resolve=False,
    ) == "https://api.does-not-resolve.invalid"
    assert translation.normalize_provider_api_url("", resolve=False) == ""
    with pytest.raises(translation.TranslationError, match="https://"):
        translation.normalize_provider_api_url("http://x.invalid", resolve=False)
    with pytest.raises(translation.TranslationError, match="credentials"):
        translation.normalize_provider_api_url(
            "https://user:pw@x.invalid", resolve=False,
        )


def test_openai_backend_refuses_a_plaintext_base_url():
    with pytest.raises(translation.TranslationError):
        translation._query_openai("prompt", "http://10.0.0.5:8080", "sk-secret")


def test_config_import_rejects_a_plaintext_translation_endpoint():
    from streamkeep import config as config_module

    with pytest.raises(config_module.ConfigImportError, match="translation_api_url"):
        config_module._validate_config_schema(
            {"translation_api_url": "http://10.0.0.5:8080"}
        )
    config_module._validate_config_schema({"translation_api_url": ""})


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.closed = False

    def read(self, size=-1):
        if size is None or size < 0:
            return self._payload
        return self._payload[:size]

    def close(self):
        self.closed = True


def test_provider_responses_are_bounded():
    limit = translation.MAX_PROVIDER_RESPONSE_BYTES
    oversized = _Response(b"x" * (limit + 10))
    with pytest.raises(translation.TranslationError, match="larger than"):
        translation._read_bounded(oversized)

    exact = _Response(b"y" * limit)
    assert len(translation._read_bounded(exact)) == limit


def test_local_ollama_response_is_bounded(monkeypatch):
    payload = b'{"response": "' + b"z" * (
        translation.MAX_PROVIDER_RESPONSE_BYTES + 64
    ) + b'"}'

    class _Ctx(_Response):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()
            return False

    monkeypatch.setattr(
        translation.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _Ctx(payload),
    )
    with pytest.raises(translation.TranslationError, match="larger than"):
        translation._query_ollama("prompt")
