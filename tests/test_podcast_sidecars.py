import json
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from streamkeep import cli, podcast_sidecars
from streamkeep.extractors.podcast import parse_podcast_chapters_json
from streamkeep.podcast_sidecars import (
    download_podcast_sidecars,
    find_feed_sidecars,
    parse_podcast_sidecar_refs,
    read_manifest,
    sync_podcast_sidecars,
)


VTT_BODY = b"WEBVTT\n\n00:00.000 --> 00:02.000\nHello world\n"
CHAPTERS_BODY = json.dumps({
    "version": "1.2.0",
    "chapters": [
        {"startTime": 0, "title": "Intro"},
        {"startTime": 30, "title": "Topic"},
    ],
}).encode("utf-8")


ITEM_XML = """
<item>
  <title>Episode 1</title>
  <enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg" length="1"/>
  <podcast:transcript url="https://cdn.example.com/ep1.en.vtt" type="text/vtt" language="en" rel="captions"/>
  <podcast:transcript url="https://cdn.example.com/ep1.es.vtt" type="text/vtt" language="es"/>
  <podcast:chapters url="https://cdn.example.com/ep1.chapters.json" type="application/json+chapters"/>
</item>
"""

FEED_XML = f"""<?xml version="1.0"?>
<rss xmlns:podcast="https://podcastindex.org/namespace/1.0">
<channel>
{ITEM_XML}
<item>
  <title>Episode 2</title>
  <enclosure url="https://cdn.example.com/ep2.mp3" type="audio/mpeg" length="1"/>
</item>
</channel></rss>
"""


def _fake_fetch(url, **_kwargs):
    if url.endswith(".vtt"):
        return VTT_BODY
    if url.endswith("chapters.json"):
        return CHAPTERS_BODY
    raise AssertionError(f"unexpected fetch: {url}")


# --- Discovery ---------------------------------------------------------------


def test_parse_refs_extracts_transcripts_and_chapters():
    refs = parse_podcast_sidecar_refs(ITEM_XML)
    kinds = [(r["kind"], r["language"], r["type"]) for r in refs]
    assert ("transcript", "en", "text/vtt") in kinds
    assert ("transcript", "es", "text/vtt") in kinds
    assert ("chapters", "", "application/json+chapters") in kinds
    assert len(refs) == 3


def test_parse_refs_rejects_non_http_and_dedupes():
    xml = """
    <item>
      <podcast:transcript url="file:///etc/passwd" type="text/vtt"/>
      <podcast:transcript url="https://x/a.vtt" type="text/vtt"/>
      <podcast:transcript url="https://x/a.vtt" type="text/vtt"/>
    </item>
    """
    refs = parse_podcast_sidecar_refs(xml)
    assert [r["url"] for r in refs] == ["https://x/a.vtt"]


def test_find_feed_sidecars_matches_enclosure():
    refs = find_feed_sidecars(FEED_XML, "https://cdn.example.com/ep1.mp3")
    assert len(refs) == 3
    # Episode 2 has no sidecars.
    assert find_feed_sidecars(FEED_XML, "https://cdn.example.com/ep2.mp3") == []
    # Unknown enclosure yields nothing, never raises.
    assert find_feed_sidecars(FEED_XML, "https://cdn.example.com/none.mp3") == []


def test_find_feed_sidecars_path_fallback_on_query_mismatch():
    refs = find_feed_sidecars(
        FEED_XML, "https://cdn.example.com/ep1.mp3?token=abc"
    )
    assert len(refs) == 3


def test_find_feed_sidecars_tolerates_garbage():
    assert find_feed_sidecars("not xml at all", "https://x/y.mp3") == []
    assert find_feed_sidecars(None, "https://x/y.mp3") == []


# --- Download / hashing / refresh -------------------------------------------


def test_download_writes_hashed_sidecars_and_names(tmp_path):
    refs = parse_podcast_sidecar_refs(ITEM_XML)
    with mock.patch.object(podcast_sidecars, "fetch_url_bytes", _fake_fetch):
        manifest = download_podcast_sidecars(refs, str(tmp_path), "ep1")
    files = {e["file"] for e in manifest}
    assert files == {"ep1.en.vtt", "ep1.es.vtt", "ep1.chapters.json"}
    for entry in manifest:
        assert (tmp_path / entry["file"]).is_file()
        assert len(entry["sha256"]) == 64
    # The written VTT feeds the existing parser.
    from streamkeep.search import _parse_vtt
    cues = _parse_vtt(str(tmp_path / "ep1.en.vtt"))
    assert cues and "Hello world" in cues[0][2]
    # The written chapters JSON feeds the existing parser.
    chapters = parse_podcast_chapters_json(
        (tmp_path / "ep1.chapters.json").read_text(encoding="utf-8")
    )
    assert [c["title"] for c in chapters] == ["Intro", "Topic"]


def test_refresh_skips_unchanged_but_rewrites_changed(tmp_path):
    refs = parse_podcast_sidecar_refs(ITEM_XML)
    with mock.patch.object(podcast_sidecars, "fetch_url_bytes", _fake_fetch):
        first = download_podcast_sidecars(refs, str(tmp_path), "ep1")
        # Second pass with the SAME bytes: every entry is reused unchanged.
        second = download_podcast_sidecars(
            refs, str(tmp_path), "ep1", existing=first
        )
    assert second == first

    # Now the remote transcript changes → the file is rewritten.
    changed = b"WEBVTT\n\n00:00.000 --> 00:03.000\nUpdated\n"

    def _changed_fetch(url, **_kwargs):
        if url == "https://cdn.example.com/ep1.en.vtt":
            return changed
        return _fake_fetch(url)

    with mock.patch.object(podcast_sidecars, "fetch_url_bytes", _changed_fetch):
        third = download_podcast_sidecars(
            refs, str(tmp_path), "ep1", existing=second
        )
    en = next(e for e in third if e["file"] == "ep1.en.vtt")
    en_before = next(e for e in second if e["file"] == "ep1.en.vtt")
    assert en["sha256"] != en_before["sha256"]
    assert "Updated" in (tmp_path / "ep1.en.vtt").read_text(encoding="utf-8")


def test_download_is_non_fatal_on_fetch_failure(tmp_path):
    refs = parse_podcast_sidecar_refs(ITEM_XML)

    def _flaky(url, **_kwargs):
        if url.endswith(".es.vtt"):
            from streamkeep.image_fetch import ImageFetchError
            raise ImageFetchError("boom")
        return _fake_fetch(url)

    logs = []
    with mock.patch.object(podcast_sidecars, "fetch_url_bytes", _flaky):
        manifest = download_podcast_sidecars(
            refs, str(tmp_path), "ep1", log_fn=logs.append
        )
    files = {e["file"] for e in manifest}
    assert files == {"ep1.en.vtt", "ep1.chapters.json"}
    assert any("Skipped" in line for line in logs)


def test_sync_writes_manifest_and_is_idempotent(tmp_path):
    with mock.patch.object(podcast_sidecars, "fetch_url_bytes", _fake_fetch):
        manifest = sync_podcast_sidecars(
            FEED_XML, "https://cdn.example.com/ep1.mp3", str(tmp_path), "ep1"
        )
    assert len(manifest) == 3
    on_disk = read_manifest(str(tmp_path), "ep1")
    assert len(on_disk) == 3

    # A second sync with identical remote content reuses every entry.
    with mock.patch.object(podcast_sidecars, "fetch_url_bytes", _fake_fetch):
        again = sync_podcast_sidecars(
            FEED_XML, "https://cdn.example.com/ep1.mp3", str(tmp_path), "ep1"
        )
    assert {e["sha256"] for e in again} == {e["sha256"] for e in manifest}


def test_sync_absent_metadata_returns_empty(tmp_path):
    with mock.patch.object(podcast_sidecars, "fetch_url_bytes", _fake_fetch):
        assert sync_podcast_sidecars(
            FEED_XML, "https://cdn.example.com/ep2.mp3", str(tmp_path), "ep2"
        ) == []


# --- CLI ---------------------------------------------------------------------


def test_cli_podcast_sidecars_downloads_and_reports(tmp_path):
    def _fetch(url, **_kwargs):
        if url == "https://feed.example.com/rss":
            return FEED_XML.encode("utf-8")
        return _fake_fetch(url)

    out = StringIO()
    args = cli.build_parser().parse_args([
        "podcast-sidecars",
        "https://feed.example.com/rss",
        "https://cdn.example.com/ep1.mp3",
        str(tmp_path),
        "--base", "ep1",
    ])
    with mock.patch("streamkeep.image_fetch.fetch_url_bytes", _fetch), \
            mock.patch.object(podcast_sidecars, "fetch_url_bytes", _fetch), \
            mock.patch.object(cli, "_get_output_stream", return_value=out):
        cli._run_podcast_sidecars(args)
    text = out.getvalue()
    assert "ep1.en.vtt" in text
    assert "ep1.chapters.json" in text
    assert (tmp_path / "ep1.en.vtt").is_file()
    assert (Path(str(tmp_path)) / "ep1.sidecars.json").is_file()


def test_cli_podcast_sidecars_reports_empty(tmp_path):
    def _fetch(url, **_kwargs):
        return FEED_XML.encode("utf-8")

    out = StringIO()
    args = cli.build_parser().parse_args([
        "podcast-sidecars",
        "https://feed.example.com/rss",
        "https://cdn.example.com/ep2.mp3",
        str(tmp_path),
    ])
    with mock.patch("streamkeep.image_fetch.fetch_url_bytes", _fetch), \
            mock.patch.object(podcast_sidecars, "fetch_url_bytes", _fetch), \
            mock.patch.object(cli, "_get_output_stream", return_value=out):
        cli._run_podcast_sidecars(args)
    assert "No transcript or chapter sidecars" in out.getvalue()
