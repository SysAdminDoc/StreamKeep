import pytest

from streamkeep.download_options import (
    apply_external_downloader_options,
    apply_ytdlp_transfer_options,
    format_command_argv, normalize_ytdlp_arg_templates,
    parse_ytdlp_template_text, resolve_ytdlp_arg_template,
    sanitize_download_target_url,
    validate_download_options, validate_external_downloader_options,
    validate_hls_key_override,
    validate_playlist_options,
    validate_sponsorblock_options,
    validate_subtitle_options, validate_ytdlp_template_args,
    validate_ytdlp_transfer_options,
)


def test_hls_clear_key_override_normalizes_key_and_iv():
    options = validate_hls_key_override(
        "00112233445566778899aabbccddeeff", "abc",
    )
    assert options["value"] == "00112233445566778899AABBCCDDEEFF"
    assert options["iv"] == "0x00000000000000000000000000000ABC"
    assert options["extractor_arg"] == (
        "generic:hls_key=00112233445566778899AABBCCDDEEFF,"
        "0x00000000000000000000000000000ABC"
    )


def test_hls_clear_key_override_accepts_http_key_uri():
    uri = "https://keys.example.com/aes.key?token=opaque"
    options = validate_hls_key_override(uri)
    assert options["value"] == uri
    assert options["extractor_arg"] == f"generic:hls_key={uri}"


@pytest.mark.parametrize("key,iv", [
    ("short", ""),
    ("00112233445566778899aabbccddeeff", "not-hex"),
    ("file:///tmp/key", ""),
    ("https://user:secret@keys.example/key", ""),
    ("", "01"),
])
def test_hls_clear_key_override_rejects_unsafe_or_invalid_values(key, iv):
    with pytest.raises(ValueError):
        validate_hls_key_override(key, iv)


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


def test_named_ytdlp_templates_round_trip_as_structured_argv():
    registry = normalize_ytdlp_arg_templates({
        "Authenticated archive": [
            "--add-header", "Referer: https://example.com/watch",
            "--user-agent=StreamKeep Archive",
        ],
    })
    assert resolve_ytdlp_arg_template(
        registry, "Authenticated archive"
    ) == (
        "--add-header", "Referer: https://example.com/watch",
        "--user-agent=StreamKeep Archive",
    )
    assert parse_ytdlp_template_text(
        "--add-header\nReferer: https://example.com/watch\n"
    ) == ("--add-header", "Referer: https://example.com/watch")


@pytest.mark.parametrize("args", [
    ["--exec", "calc"], ["--external-downloader-args=cmd /c whoami"], ["--"],
])
def test_named_ytdlp_templates_reject_command_boundaries(args):
    with pytest.raises(ValueError):
        normalize_ytdlp_arg_templates({"Unsafe": args})


def test_command_export_quotes_each_argv_element_for_windows():
    command = format_command_argv(
        ["yt-dlp", "--add-header", "Referer: https://example.com/a b"],
        windows=True,
    )
    assert command.startswith("yt-dlp --add-header ")
    assert '"Referer: https://example.com/a b"' in command


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


def test_ytdlp_transfer_matrix_validates_and_applies_to_worker():
    class Worker:
        pass

    worker = Worker()
    options = apply_ytdlp_transfer_options(worker, {
        "ytdlp_concurrent_fragments": 4,
        "ytdlp_retries": "infinite",
        "ytdlp_fragment_retries": "12",
        "ytdlp_retry_sleep": "fragment:exp=1:20",
        "ytdlp_unavailable_fragments": "abort",
        "ytdlp_throttled_rate": "250K",
        "ytdlp_live_from_start": True,
        "ytdlp_wait_for_video": "30-120",
        "ytdlp_embed_chapters": True,
        "ytdlp_embed_metadata": False,
        "ytdlp_embed_thumbnail": True,
    })

    assert options["concurrent_fragments"] == 4
    assert options["fragment_retries"] == "12"
    assert worker.ytdlp_retry_sleep == "fragment:exp=1:20"
    assert worker.ytdlp_embed_metadata is False


@pytest.mark.parametrize("kwargs", [
    {"concurrent_fragments": 33},
    {"retries": "forever"},
    {"fragment_retries": -1},
    {"retry_sleep": "1\n--exec=calc"},
    {"unavailable_fragments": "ignore"},
    {"throttled_rate": "fast"},
    {"wait_for_video": "120-30"},
])
def test_ytdlp_transfer_matrix_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        validate_ytdlp_transfer_options(**kwargs)


# --- External downloader routing + URL sanitization (V21, CVE-2026-50574) ---


def test_external_downloader_unset_returns_no_argv():
    result = validate_external_downloader_options()
    assert result == {"downloader": "", "downloader_args": "", "argv": []}


def test_external_downloader_aria2c_builds_bounded_argv():
    result = validate_external_downloader_options(
        downloader="aria2c", connections=8, splits=8, min_split_size="1M",
    )
    assert result["downloader"] == "aria2c"
    assert result["downloader_args"] == (
        "aria2c:--max-connection-per-server=8 --split=8 --min-split-size=1M"
    )
    assert result["argv"] == [
        "--downloader", "aria2c",
        "--downloader-args",
        "aria2c:--max-connection-per-server=8 --split=8 --min-split-size=1M",
    ]


def test_external_downloader_aria2c_without_tuning_omits_args():
    result = validate_external_downloader_options(downloader="aria2c")
    assert result["argv"] == ["--downloader", "aria2c"]
    assert result["downloader_args"] == ""


@pytest.mark.parametrize("kwargs", [
    {"downloader": "wget"},
    {"downloader": "curl"},
    {"downloader": "aria2c", "connections": 99},
    {"downloader": "aria2c", "splits": -1},
    {"downloader": "aria2c", "min_split_size": "; rm -rf /"},
    {"downloader": "aria2c", "min_split_size": "1 --rpc-secret=x"},
])
def test_external_downloader_rejects_unsafe_values(kwargs):
    with pytest.raises(ValueError):
        validate_external_downloader_options(**kwargs)


def test_external_downloader_args_cannot_carry_injected_options():
    # Only numeric/rate knobs are exposed, so no aria2c control/exec option
    # can appear in the generated argv regardless of input.
    result = validate_external_downloader_options(
        downloader="aria2c", connections=4, splits=2,
    )
    for token in result["argv"]:
        assert "--on-download-complete" not in token
        assert "--rpc" not in token
        assert "--conf-path" not in token
        assert "--dir" not in token


def test_sanitize_download_target_url_preserves_valid_url():
    url = "https://cdn.example.com/video/master.m3u8?token=abc123"
    assert sanitize_download_target_url(url) == url


@pytest.mark.parametrize("url", [
    "",
    "   ",
    "-x8",                                   # leading dash → aria2c option
    "--max-connection-per-server=16",        # aria2c option smuggling
    "https://a.example/one\nhttps://b/two",  # newline → extra URI
    "https://a.example/space here",          # embedded whitespace
    "ftp://a.example/file",                  # non-HTTP scheme
    "file:///etc/passwd",                    # local scheme
    "https://user:secret@a.example/x",       # embedded credentials
    "notaurl",                               # no scheme/host
])
def test_sanitize_download_target_url_rejects_hostile_input(url):
    with pytest.raises(ValueError):
        sanitize_download_target_url(url)


def test_apply_external_downloader_options_sets_worker_fields():
    class _Worker:
        pass

    worker = _Worker()
    apply_external_downloader_options(worker, {
        "ytdlp_external_downloader": "aria2c",
        "ytdlp_aria2c_connections": 6,
        "ytdlp_aria2c_splits": 4,
        "ytdlp_aria2c_min_split_size": "512K",
    })
    assert worker.ytdlp_external_downloader == "aria2c"
    assert worker.ytdlp_aria2c_connections == 6
    assert worker.ytdlp_aria2c_splits == 4
    assert worker.ytdlp_aria2c_min_split_size == "512K"


def test_apply_external_downloader_options_disables_on_bad_config():
    class _Worker:
        pass

    worker = _Worker()
    # A stale/hostile config value must disable routing, never raise.
    apply_external_downloader_options(worker, {
        "ytdlp_external_downloader": "wget",
        "ytdlp_aria2c_connections": 999,
    })
    assert worker.ytdlp_external_downloader == ""
    assert worker.ytdlp_aria2c_connections == 0
    assert worker.ytdlp_aria2c_splits == 0
    assert worker.ytdlp_aria2c_min_split_size == ""
