import json
from unittest import mock

import pytest

from streamkeep import config, declarative
from streamkeep.extractors import Extractor


DEFINITION = """
schema_version: 1
id: example-source
name: Example Source
version: 1.0.0
platform: Example
direct: true
match:
  hosts: [example.com]
  path_regex: '^/watch/(?P<id>[A-Za-z0-9_-]+)$'
  channel_group: id
resolve:
  request:
    url: 'https://api.example.com/videos/{id}'
    method: GET
  response:
    format: json
    fields:
      title: '$.title'
      channel: '$.channel'
      source_id: '$.id'
      webpage_url: '$.page'
      is_live: '$.live'
      media_url: '$.media'
list_vods:
  request:
    url: 'https://api.example.com/channels/{channel}/videos'
  response:
    format: json
    items: '$.items[*]'
    next_cursor: '$.next'
    fields:
      title: '$.title'
      date: '$.date'
      source: '$.media'
      source_id: '$.id'
      webpage_url: '$.page'
      channel: '$.channel'
check_live:
  request:
    url: 'https://api.example.com/channels/{channel}/live'
  response:
    format: json
    fields:
      live: '$.live'
"""


def test_declarative_definition_resolves_and_lists_models(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "example.yaml").write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)

    def request(url, **_kwargs):
        if "/channels/" in url and url.endswith("/videos"):
            return json.dumps({
                "items": [{
                    "id": "vod-1", "title": "First", "date": "2026-08-04",
                    "media": "https://cdn.example.com/first.mp4",
                    "page": "https://example.com/watch/vod-1",
                    "channel": "demo",
                }],
                "next": "cursor-2",
            }).encode(), "application/json"
        if url.endswith("/live"):
            return b'{"live": true}', "application/json"
        return json.dumps({
            "id": "vod-1", "title": "First", "channel": "demo",
            "page": "https://example.com/watch/vod-1", "live": False,
            "media": "https://cdn.example.com/first.mp4",
        }).encode(), "application/json"

    monkeypatch.setattr(declarative, "_guarded_request", request)
    monkeypatch.setattr(
        declarative, "validate_remote_url", lambda url: mock.Mock(url=url)
    )

    extractor = Extractor.detect("https://example.com/watch/vod-1")
    assert extractor is not None
    assert extractor.NAME == "Example Source"
    assert extractor.is_direct_url("") is True
    info = extractor.resolve("https://example.com/watch/vod-1")
    assert info.title == "First"
    assert info.source_id == "vod-1"
    assert info.qualities[0].url == "https://cdn.example.com/first.mp4"
    vods, cursor = extractor.list_vods("https://example.com/watch/demo")
    assert vods[0].title == "First"
    assert vods[0].source_id == "vod-1"
    assert cursor == "cursor-2"
    assert extractor.check_live("https://example.com/watch/demo") is True


def test_declarative_files_hot_reload_without_restarting(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    path = adapter_dir / "example.yaml"
    path.write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    assert declarative.declarative_adapter_names() == ["Example Source"]

    changed = DEFINITION.replace("name: Example Source", "name: Renamed Source")
    path.write_text(changed, encoding="utf-8")
    assert declarative.declarative_adapter_names() == ["Renamed Source"]


def test_declarative_validation_rejects_code_and_unsafe_headers():
    raw = {
        "schema_version": 1,
        "id": "bad",
        "name": "Bad",
        "version": "1.0.0",
        "match": {"hosts": ["example.com"], "path_regex": ".*"},
        "resolve": {
            "request": {
                "url": "https://example.com/{__import__}",
                "headers": {"Cookie": "secret"},
            },
            "response": {"format": "json", "fields": {}},
        },
        "exec": "calc.exe",
    }
    errors = declarative.validate_definition(raw)
    assert any("unsupported fields" in error for error in errors)
    assert any("forbidden header" in error for error in errors)


def test_declarative_request_rejects_private_targets(monkeypatch):
    monkeypatch.setattr(
        declarative,
        "validate_remote_url",
        lambda _url: (_ for _ in ()).throw(
            declarative.RemoteURLPolicyError("private address")
        ),
    )
    with pytest.raises(declarative.DeclarativeAdapterError, match="blocked"):
        declarative._guarded_request(
            "http://127.0.0.1/secret",
            method="GET", headers={}, timeout=1, max_response_bytes=1024,
        )


def test_declarative_media_urls_are_rechecked_by_net_guard(monkeypatch):
    def guard(url):
        if "private" in str(url):
            raise declarative.RemoteURLPolicyError("private address")
        return mock.Mock(url=url)

    monkeypatch.setattr(declarative, "validate_remote_url", guard)
    qualities = declarative._build_qualities(
        {"media": "https://cdn.example.com/video.mp4"},
        {
            "qualities": [{
                "url": "$.media",
                "audio_url": "http://private.example/audio.m4a",
            }],
        },
        {},
        "json",
    )
    assert len(qualities) == 1
    assert qualities[0].url == "https://cdn.example.com/video.mp4"
    assert qualities[0].audio_url == ""


def test_config_import_quarantines_declarative_adapter_content():
    payload = {
        "source_adapters": [{"id": "example", "content": DEFINITION}],
    }
    envelope = json.dumps({
        "format": config.CONFIG_EXPORT_FORMAT,
        "schema_version": config.CONFIG_EXPORT_SCHEMA_VERSION,
        "exported_by": "test",
        "config": payload,
    })
    preview = config.prepare_config_import(envelope, {})
    assert preview.capabilities == ("declarative_adapters",)
    assert preview.quarantined_config["source_adapters"] == []
    activated = config.finalize_config_import(
        preview, {"declarative_adapters"}
    )
    assert activated["source_adapters"][0]["id"] == "example"


def test_declarative_diagnostics_reports_invalid_file(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("schema_version: 99\n", encoding="utf-8")
    report = declarative.declarative_adapter_diagnostics(tmp_path, config={})
    assert report["adapters"] == []
    assert report["errors"][0]["source"] == str(path)
