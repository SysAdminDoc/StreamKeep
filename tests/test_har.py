import json
from io import StringIO
from unittest import mock

import pytest

from streamkeep import cli
from streamkeep.har import har_entry_ytdlp_headers, parse_har


def _entry(url, *, method="GET", mime="", headers=None):
    return {
        "request": {
            "method": method,
            "url": url,
            "headers": [
                {"name": name, "value": value}
                for name, value in (headers or {}).items()
            ],
        },
        "response": {"content": {"mimeType": mime}},
    }


def _har(*entries):
    return json.dumps({"log": {"version": "1.2", "entries": list(entries)}})


REPLAY_HEADERS = {
    "Referer": "https://player.example.com/watch",
    "Origin": "https://player.example.com",
    "User-Agent": "Mozilla/5.0 StreamKeep",
    "Cookie": "session=abc123",
    ":authority": "cdn.example.com",  # HTTP/2 pseudo header — must be dropped
    "Accept": "*/*",                  # not a replay header — dropped
}


def test_parse_har_extracts_manifest_with_replay_headers():
    doc = _har(
        _entry("https://a.example/style.css", mime="text/css"),
        _entry(
            "https://cdn.example.com/hls/master.m3u8",
            mime="application/vnd.apple.mpegurl",
            headers=REPLAY_HEADERS,
        ),
    )
    links = parse_har(doc)
    assert len(links) == 1
    link = links[0]
    assert link["url"] == "https://cdn.example.com/hls/master.m3u8"
    assert link["type"] == "manifest"
    assert link["headers"] == {
        "Referer": "https://player.example.com/watch",
        "Origin": "https://player.example.com",
        "User-Agent": "Mozilla/5.0 StreamKeep",
        "Cookie": "session=abc123",
    }
    # Pseudo/non-replay headers are excluded.
    assert ":authority" not in link["headers"]
    assert "Accept" not in link["headers"]


def test_parse_har_collapses_segments_when_manifest_present():
    doc = _har(
        _entry("https://cdn.example/live/index.m3u8",
               mime="application/x-mpegurl"),
        _entry("https://cdn.example/live/seg0.ts", mime="video/mp2t"),
        _entry("https://cdn.example/live/seg1.ts", mime="video/mp2t"),
    )
    links = parse_har(doc)
    assert [link["url"] for link in links] == [
        "https://cdn.example/live/index.m3u8"
    ]


def test_parse_har_keeps_segments_when_requested_and_no_manifest():
    doc = _har(
        _entry("https://cdn.example/live/seg0.ts", mime="video/mp2t"),
        _entry("https://cdn.example/live/seg1.m4s", mime="video/iso.segment"),
    )
    assert parse_har(doc, include_segments=False) == []
    kept = parse_har(doc, include_segments=True)
    assert {link["url"] for link in kept} == {
        "https://cdn.example/live/seg0.ts",
        "https://cdn.example/live/seg1.m4s",
    }
    assert all(link["type"] == "segment" for link in kept)


def test_parse_har_classifies_media_by_extension_and_mime():
    doc = _har(
        _entry("https://v.example/clip.mp4"),                    # by extension
        _entry("https://v.example/audio", mime="audio/mp4"),     # by mime
        _entry("https://v.example/page", mime="text/html"),      # neither
    )
    links = parse_har(doc)
    assert {link["url"] for link in links} == {
        "https://v.example/clip.mp4",
        "https://v.example/audio",
    }
    assert all(link["type"] == "media" for link in links)


def test_parse_har_dedupes_and_orders_manifests_before_media():
    doc = _har(
        _entry("https://v.example/clip.mp4"),
        _entry("https://v.example/master.m3u8", mime="application/x-mpegurl"),
        _entry("https://v.example/clip.mp4"),  # duplicate
    )
    links = parse_har(doc)
    assert [link["url"] for link in links] == [
        "https://v.example/master.m3u8",
        "https://v.example/clip.mp4",
    ]


def test_parse_har_ignores_non_get_and_non_http():
    doc = _har(
        _entry("https://v.example/upload.mp4", method="POST"),
        _entry("blob:https://v.example/abcd", mime="video/mp4"),
        _entry("data:video/mp4;base64,AAAA", mime="video/mp4"),
    )
    assert parse_har(doc) == []


def test_parse_har_drops_control_char_header_values():
    doc = _har(
        _entry(
            "https://v.example/master.m3u8",
            mime="application/x-mpegurl",
            headers={"Referer": "https://ok.example/\r\nInjected: 1"},
        ),
    )
    links = parse_har(doc)
    assert links[0]["headers"] == {}


@pytest.mark.parametrize("bad", [
    "not json at all",
    json.dumps({"log": {}}),
    json.dumps({"nolog": True}),
    b"\xff\xfe not har",
])
def test_parse_har_rejects_invalid_documents(bad):
    with pytest.raises(ValueError):
        parse_har(bad)


def test_har_entry_ytdlp_headers_builds_add_header_argv():
    link = {"headers": {
        "Referer": "https://x.example/w",
        "User-Agent": "SK",
    }}
    assert har_entry_ytdlp_headers(link) == [
        "--add-header", "Referer: https://x.example/w",
        "--add-header", "User-Agent: SK",
    ]


def test_cli_import_har_prints_urls(tmp_path):
    har_path = tmp_path / "capture.har"
    har_path.write_text(_har(
        _entry("https://cdn.example/master.m3u8", mime="application/x-mpegurl"),
        _entry("https://cdn.example/clip.mp4"),
    ), encoding="utf-8")

    output = StringIO()
    args = cli.build_parser().parse_args(["import-har", str(har_path)])
    with mock.patch.object(cli, "_get_output_stream", return_value=output):
        cli._run_har_import(args)
    lines = output.getvalue().splitlines()
    assert lines == [
        "https://cdn.example/master.m3u8",
        "https://cdn.example/clip.mp4",
    ]


def test_cli_import_har_json_includes_headers(tmp_path):
    har_path = tmp_path / "capture.har"
    har_path.write_text(_har(
        _entry("https://cdn.example/master.m3u8",
               mime="application/x-mpegurl",
               headers={"Referer": "https://p.example/w"}),
    ), encoding="utf-8")

    output = StringIO()
    args = cli.build_parser().parse_args(
        ["import-har", str(har_path), "--json"]
    )
    with mock.patch.object(cli, "_get_output_stream", return_value=output):
        cli._run_har_import(args)
    payload = json.loads(output.getvalue())
    assert payload[0]["headers"] == {"Referer": "https://p.example/w"}


def test_cli_import_har_reports_empty_capture(tmp_path):
    har_path = tmp_path / "empty.har"
    har_path.write_text(_har(
        _entry("https://x.example/page", mime="text/html"),
    ), encoding="utf-8")

    output = StringIO()
    args = cli.build_parser().parse_args(["import-har", str(har_path)])
    with mock.patch.object(cli, "_get_output_stream", return_value=output):
        cli._run_har_import(args)
    assert "No media" in output.getvalue()


def test_cli_import_har_rejects_unreadable_file(tmp_path):
    args = cli.build_parser().parse_args(
        ["import-har", str(tmp_path / "missing.har")]
    )
    with mock.patch.object(cli, "_get_output_stream", return_value=StringIO()):
        with pytest.raises(SystemExit) as exc:
            cli._run_har_import(args)
    assert exc.value.code == 2
