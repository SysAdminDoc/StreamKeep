"""Tests for the immutable DownloadJobSpec contract."""

import json
import pytest

from streamkeep.job_spec import DownloadJobSpec, SCHEMA_VERSION


def test_default_spec_roundtrips():
    spec = DownloadJobSpec()
    d = spec.to_dict()
    restored = DownloadJobSpec.from_dict(d)
    assert restored.schema_version == SCHEMA_VERSION
    assert restored.format_type == "hls"
    assert restored.parallel_connections == 4


def test_spec_is_frozen():
    spec = DownloadJobSpec()
    with pytest.raises(AttributeError):
        spec.format_type = "dash"


def test_to_dict_excludes_secrets():
    spec = DownloadJobSpec(hls_key_override="DEADBEEF", hls_key_iv="0x01")
    d = spec.to_dict()
    assert "hls_key_override" not in d
    assert "hls_key_iv" not in d


def test_to_dict_serializes_tuples_as_lists():
    spec = DownloadJobSpec(
        segments=(("url1", "label1"), ("url2", "label2")),
        ytdlp_template_args=("--no-part", "-N", "4"),
    )
    d = spec.to_dict()
    assert isinstance(d["segments"], list)
    assert isinstance(d["segments"][0], list)
    assert isinstance(d["ytdlp_template_args"], list)


def test_from_dict_rejects_future_schema():
    with pytest.raises(ValueError, match="not supported"):
        DownloadJobSpec.from_dict({"schema_version": 999})


def test_from_dict_ignores_unknown_keys():
    d = {"schema_version": 1, "unknown_future_field": True}
    spec = DownloadJobSpec.from_dict(d)
    assert spec.schema_version == 1


def test_full_roundtrip_with_all_fields():
    spec = DownloadJobSpec(
        playlist_url="https://cdn.example.com/master.m3u8",
        segments=(("https://cdn.example.com/seg1.ts", "ep1"),),
        output_dir="/tmp/test",
        format_type="ytdlp_direct",
        ytdlp_source="https://youtube.com/watch?v=abc",
        ytdlp_format="bv*+ba/b",
        ytdlp_container="mkv",
        cookies_browser="firefox",
        rate_limit="2M",
        download_subs=True,
        subtitle_languages="en,es",
        sponsorblock=True,
        sponsorblock_mark="intro,outro",
        sponsorblock_remove="sponsor",
        ytdlp_concurrent_fragments=4,
        ytdlp_retries="10",
        ytdlp_live_from_start=True,
        ytdlp_embed_chapters=True,
        ytdlp_template_args=("--no-part",),
        ytdlp_external_downloader="aria2c",
        ytdlp_aria2c_connections=8,
        download_sections="*0:00-5:00",
        max_retries=5,
        parallel_connections=8,
        chunk_length_secs=7200,
    )
    d = spec.to_dict()
    json_text = json.dumps(d)
    restored = DownloadJobSpec.from_dict(json.loads(json_text))
    assert restored.playlist_url == spec.playlist_url
    assert restored.ytdlp_format == spec.ytdlp_format
    assert restored.ytdlp_concurrent_fragments == 4
    assert restored.sponsorblock_mark == "intro,outro"
    assert restored.ytdlp_template_args == ("--no-part",)
    assert restored.chunk_length_secs == 7200


def test_apply_to_worker():
    spec = DownloadJobSpec(
        playlist_url="https://cdn.example.com/master.m3u8",
        output_dir="/tmp",
        format_type="hls",
        rate_limit="5M",
        sponsorblock=True,
        ytdlp_concurrent_fragments=4,
    )

    class _Worker:
        pass

    w = _Worker()
    spec.apply_to_worker(w)
    assert w.playlist_url == "https://cdn.example.com/master.m3u8"
    assert w.rate_limit == "5M"
    assert w.sponsorblock is True
    assert w.ytdlp_concurrent_fragments == 4


def test_from_worker_captures_state():
    class _Worker:
        playlist_url = "https://x.com/m.m3u8"
        segments = [["url1", "label1"]]
        output_dir = "/out"
        format_type = "hls"
        audio_url = ""
        selected_tracks = []
        ytdlp_source = ""
        ytdlp_format = "bv+ba"
        ytdlp_format_sort = ""
        ytdlp_container = "mp4"
        ytdlp_audio_format = ""
        ytdlp_audio_quality = ""
        cookies_browser = "chrome"
        rate_limit = "1M"
        proxy = ""
        download_subs = True
        subtitle_languages = "en"
        subtitle_auto = True
        subtitle_convert = "srt"
        subtitle_embed = False
        capture_youtube_chat = False
        sponsorblock = False
        sponsorblock_mark = ""
        sponsorblock_remove = ""
        sponsorblock_api = ""
        download_archive = ""
        break_on_existing = False
        ytdlp_concurrent_fragments = 4
        ytdlp_retries = ""
        ytdlp_fragment_retries = ""
        ytdlp_retry_sleep = ""
        ytdlp_unavailable_fragments = ""
        ytdlp_throttled_rate = ""
        ytdlp_live_from_start = False
        ytdlp_wait_for_video = ""
        ytdlp_embed_chapters = None
        ytdlp_embed_metadata = None
        ytdlp_embed_thumbnail = None
        ytdlp_template_name = ""
        ytdlp_template_args = ()
        ytdlp_external_downloader = ""
        ytdlp_aria2c_connections = 0
        ytdlp_aria2c_splits = 0
        ytdlp_aria2c_min_split_size = ""
        hls_key_override = ""
        hls_key_iv = ""
        download_sections = ""
        max_retries = 3
        parallel_connections = 8
        chunk_length_secs = 3600

    w = _Worker()
    spec = DownloadJobSpec.from_worker(w)
    assert spec.playlist_url == "https://x.com/m.m3u8"
    assert spec.segments == (("url1", "label1"),)
    assert spec.cookies_browser == "chrome"
    assert spec.ytdlp_format == "bv+ba"
    assert spec.ytdlp_concurrent_fragments == 4
    assert spec.subtitle_convert == "srt"
    assert spec.max_retries == 3
    assert spec.chunk_length_secs == 3600


def test_from_worker_then_apply_roundtrips():
    class _Worker:
        pass

    original = DownloadJobSpec(
        playlist_url="https://cdn.example.com/m.m3u8",
        segments=(("u", "l"),),
        output_dir="/tmp",
        format_type="ytdlp_direct",
        ytdlp_source="https://youtube.com/watch?v=x",
        ytdlp_format="bv*+ba",
        rate_limit="10M",
        download_subs=True,
        sponsorblock=True,
    )
    w1 = _Worker()
    original.apply_to_worker(w1)
    captured = DownloadJobSpec.from_worker(w1)
    assert captured.playlist_url == original.playlist_url
    assert captured.ytdlp_format == original.ytdlp_format
    assert captured.rate_limit == original.rate_limit
    assert captured.sponsorblock == original.sponsorblock
