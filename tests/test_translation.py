import json
from pathlib import Path

import pytest

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
