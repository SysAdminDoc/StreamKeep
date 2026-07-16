import pytest

from streamkeep.download_options import (
    validate_download_options, validate_subtitle_options,
)


def test_raw_format_spec_is_preserved_verbatim():
    raw = " bv*[height<=720]+ba / b "
    options = validate_download_options(format_spec=raw)
    assert options["format_spec"] == raw


def test_named_format_sort_presets_resolve_to_ytdlp_expressions():
    options = validate_download_options(format_sort_preset="cap-720p")
    assert options["format_sort"] == "res:720"

    options = validate_download_options(format_sort_preset="smallest")
    assert options["format_sort"] == "+size,+br,+res,+fps"


@pytest.mark.parametrize("value", ["best\n--exec calc", "best\x00audio"])
def test_raw_format_spec_rejects_control_characters(value):
    with pytest.raises(ValueError, match="control characters"):
        validate_download_options(format_spec=value)


@pytest.mark.parametrize("value", ["-1", "11", "lossless", "0\n--exec x"])
def test_audio_quality_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="Audio quality|audio quality"):
        validate_download_options(audio_format="mp3", audio_quality=value)


def test_audio_quality_requires_extract_mode():
    with pytest.raises(ValueError, match="requires audio-extract"):
        validate_download_options(audio_quality="128K")


def test_subtitle_language_expression_is_preserved_verbatim():
    expression = "en.*,es,-live_chat"
    options = validate_subtitle_options(
        enabled=True, languages=expression, automatic=False,
        convert="srt", embed=False,
    )
    assert options == {
        "enabled": True,
        "languages": expression,
        "automatic": False,
        "convert": "srt",
        "embed": False,
    }


def test_enabled_subtitles_require_languages_and_known_conversion():
    with pytest.raises(ValueError, match="at least one"):
        validate_subtitle_options(enabled=True)
    with pytest.raises(ValueError, match="conversion"):
        validate_subtitle_options(
            enabled=True, languages="en", convert="ttml"
        )
