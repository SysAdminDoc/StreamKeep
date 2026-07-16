import pytest

from streamkeep.download_options import (
    validate_download_options, validate_playlist_options,
    validate_sponsorblock_options,
    validate_subtitle_options, validate_ytdlp_template_args,
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


def test_ytdlp_template_args_remain_structured_and_preserve_safe_values():
    assert validate_ytdlp_template_args([
        "--concurrent-fragments", "4", "--retry-sleep=fragment:exp=1:20",
    ]) == (
        "--concurrent-fragments", "4", "--retry-sleep=fragment:exp=1:20",
    )


@pytest.mark.parametrize("option", [
    "--write-link", "--write-url-link=yes", "--write-desktop-link",
    "--write-webloc-link", "--exec=calc", "--external-downloader-args",
    "--netrc-cmd", "--config-locations", "-afile.txt",
])
def test_ytdlp_template_args_reject_link_and_command_boundaries(option):
    with pytest.raises(ValueError, match="not allowed"):
        validate_ytdlp_template_args([option])


def test_ytdlp_template_args_reject_shell_strings():
    with pytest.raises(ValueError, match="structured list"):
        validate_ytdlp_template_args("--retries 3")


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


def test_sponsorblock_categories_are_validated_and_deduplicated():
    options = validate_sponsorblock_options(
        enabled=True,
        mark="intro,chapter,intro",
        remove="sponsor,selfpromo",
        api_url="https://sponsor.example/api/",
    )
    assert options == {
        "enabled": True,
        "mark": "intro,chapter",
        "remove": "sponsor,selfpromo",
        "api_url": "https://sponsor.example/api",
    }


@pytest.mark.parametrize("category", ["chapter", "poi_highlight"])
def test_sponsorblock_mark_only_categories_cannot_be_removed(category):
    with pytest.raises(ValueError, match="can only be marked"):
        validate_sponsorblock_options(enabled=True, remove=category)


def test_sponsorblock_api_requires_https_except_on_loopback():
    with pytest.raises(ValueError, match="must use HTTPS"):
        validate_sponsorblock_options(
            enabled=True, mark="sponsor", api_url="http://example.com"
        )
    assert validate_sponsorblock_options(
        enabled=True, mark="sponsor", api_url="http://127.0.0.1:8080"
    )["api_url"] == "http://127.0.0.1:8080"


def test_enabled_sponsorblock_requires_at_least_one_action():
    with pytest.raises(ValueError, match="at least one"):
        validate_sponsorblock_options(enabled=True)


def test_playlist_options_preserve_ranges_filters_and_archive():
    options = validate_playlist_options(
        items="1:5,9", date_after="20260101", date_before="20261231",
        match_filter="duration > 60 & !is_live", max_downloads=12,
        archive_path="C:/archives/channel.txt", break_on_existing=True,
    )
    assert options["items"] == "1:5,9"
    assert options["match_filter"] == "duration > 60 & !is_live"
    assert options["max_downloads"] == 12
    assert options["break_on_existing"] is True


@pytest.mark.parametrize("date", ["2026-01-01", "20260230", "tomorrow"])
def test_playlist_options_reject_invalid_dates(date):
    with pytest.raises(ValueError, match="YYYYMMDD"):
        validate_playlist_options(date_after=date)


def test_break_on_existing_requires_archive():
    with pytest.raises(ValueError, match="requires a download archive"):
        validate_playlist_options(break_on_existing=True)
