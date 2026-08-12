import json
from io import StringIO
from unittest import mock
from pathlib import Path

import pytest

from streamkeep import cli, config, declarative
from streamkeep.har import (
    har_entry_ytdlp_headers,
    normalize_replay_headers,
    parse_har,
    source_adapter_draft,
)


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


def test_normalize_replay_headers_accepts_extension_shape_and_drops_noise():
    assert normalize_replay_headers([
        {"name": "Cookie", "value": "session=abc"},
        {"name": "Authorization", "value": "Bearer xyz"},
        {"name": "Accept", "value": "*/*"},
        {"name": "X-Bad", "value": "line\nbreak"},
    ]) == {
        "Cookie": "session=abc",
        "Authorization": "Bearer xyz",
    }
    assert normalize_replay_headers({
        "Referer": "https://player.example/",
        "Host": "cdn.example",
    }) == {"Referer": "https://player.example/"}


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


def test_har_adapter_draft_round_trips_and_waits_for_review(
    tmp_path, monkeypatch,
):
    document = _har(_entry(
        "https://cdn.example/master.m3u8?token=media-secret",
        mime="application/x-mpegurl",
        headers={
            "Referer": "https://video.example/watch/42?session=page-secret",
            "User-Agent": "StreamKeep test browser",
            "Cookie": "session=cookie-secret",
            "Authorization": "Bearer auth-secret",
        },
    ))

    text = source_adapter_draft(document, adapter_id="captured-video")
    definition = declarative.parse_definition_text(text, "test HAR draft")

    assert definition.adapter_id == "captured-video"
    assert definition.hosts == ("video.example",)
    assert definition.resolve["request"]["method"] == "HEAD"
    assert "media-secret" not in text
    assert "page-secret" not in text
    assert "cookie-secret" not in text
    assert "auth-secret" not in text

    with pytest.raises(
        declarative.DeclarativeAdapterError,
        match="stored approval",
    ):
        declarative.write_source_adapter_draft(
            text,
            tmp_path / "refused",
            config={
                declarative.REVIEW_CONFIG_KEY: {
                    "captured-video": definition.contract_fingerprint,
                },
            },
        )

    path, written = declarative.write_source_adapter_draft(
        text, tmp_path, config={},
    )
    assert path.name == "captured-video.yaml"
    assert written.review_contract() == definition.review_contract()
    active, errors = declarative.discover_source_adapters(tmp_path, config={})
    pending = declarative.pending_source_adapters(tmp_path, config={})
    assert active == []
    assert errors == []
    assert [item.adapter_id for item in pending] == ["captured-video"]

    approved_config = {
        declarative.REVIEW_CONFIG_KEY: {
            "captured-video": definition.contract_fingerprint,
        },
    }
    active, errors = declarative.discover_source_adapters(
        tmp_path, config=approved_config,
    )
    assert errors == []
    assert [item.adapter_id for item in active] == ["captured-video"]
    monkeypatch.setattr(
        declarative, "validate_remote_url", lambda url: mock.Mock(url=url),
    )
    monkeypatch.setattr(
        declarative, "_validate_mapped_url", lambda url: str(url),
    )
    monkeypatch.setattr(
        declarative,
        "_guarded_request",
        lambda *_args, **_kwargs: (b"", "text/html"),
    )
    info = active[0].resolve_stream("https://video.example/watch/42")
    assert info.qualities[0].url == "https://cdn.example/master.m3u8"


def test_cli_har_adapter_draft_revokes_stale_approval(
    tmp_path, monkeypatch,
):
    adapter_dir = tmp_path / "source_adapters"
    har_path = tmp_path / "capture.har"
    har_path.write_text(_har(_entry(
        "https://cdn.example/master.m3u8",
        mime="application/x-mpegurl",
        headers={"Referer": "https://video.example/watch/42"},
    )), encoding="utf-8")
    cfg = {
        declarative.REVIEW_CONFIG_KEY: {"captured-video": "old-approval"},
    }
    saved = []
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    monkeypatch.setattr(
        config, "save_config", lambda value: saved.append(dict(value)) or True,
    )

    output = StringIO()
    args = cli.build_parser().parse_args([
        "import-har", str(har_path),
        "--draft-adapter", "captured-video",
        "--adapter-name", "Captured Video",
    ])
    with mock.patch.object(cli, "_get_output_stream", return_value=output):
        cli._run_har_import(args)

    assert saved
    assert "captured-video" not in cfg[declarative.REVIEW_CONFIG_KEY]
    assert (adapter_dir / "captured-video.yaml").is_file()
    assert "inert until reviewed" in output.getvalue()
    assert [
        item.adapter_id
        for item in declarative.pending_source_adapters(adapter_dir, cfg)
    ] == ["captured-video"]


def test_reference_adapter_is_valid_and_packaged():
    reference = (
        Path(declarative.__file__).parent
        / "reference_adapters"
        / "reference_json_video.yaml"
    )
    definition = declarative.parse_definition_text(
        reference.read_text(encoding="utf-8"), str(reference),
    )
    assert definition.adapter_id == "reference-json-video"
    spec = Path("StreamKeep.spec").read_text(encoding="utf-8")
    assert "streamkeep/reference_adapters" in spec


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
