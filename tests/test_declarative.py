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


def test_registry_is_parsed_once_until_the_signature_changes(tmp_path, monkeypatch):
    """Detection runs per keystroke; it must not reparse the directory each time."""
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "example.yaml").write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()

    parses = []
    real_parse = declarative.parse_definition_text

    def counting_parse(text, source):
        parses.append(source)
        return real_parse(text, source)

    monkeypatch.setattr(declarative, "parse_definition_text", counting_parse)

    url = "https://example.com/watch/abc123"
    for _ in range(25):
        assert declarative.detect_declarative_extractor(url) is not None
    assert len(parses) == 1, f"registry reparsed {len(parses)} times"


def test_registry_cache_reparses_after_a_definition_changes(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    path = adapter_dir / "example.yaml"
    path.write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()

    assert declarative.declarative_adapter_names() == ["Example Source"]
    first = declarative.registry_signature()

    # Same byte length, so only the timestamp distinguishes the two revisions.
    path.write_text(
        DEFINITION.replace("name: Example Source", "name: Renamed Source"),
        encoding="utf-8",
    )
    assert declarative.registry_signature() != first
    assert declarative.declarative_adapter_names() == ["Renamed Source"]

    (adapter_dir / "second.yaml").write_text(
        DEFINITION.replace("id: example-source", "id: second-source")
                  .replace("name: Example Source", "name: Second Source")
                  .replace("hosts: [example.com]", "hosts: [second.example]"),
        encoding="utf-8",
    )
    assert sorted(declarative.declarative_adapter_names()) == [
        "Renamed Source", "Second Source",
    ]

    path.unlink()
    assert declarative.declarative_adapter_names() == ["Second Source"]


def test_registry_cache_tracks_config_entries(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()

    empty = {"source_adapters": []}
    assert declarative.declarative_adapter_names(config=empty) == []

    populated = {"source_adapters": [{"id": "cfg", "content": DEFINITION}]}
    assert declarative.registry_signature(config=populated) != \
        declarative.registry_signature(config=empty)
    assert declarative.declarative_adapter_names(config=populated) == ["Example Source"]

    disabled = {
        "source_adapters": [
            {"id": "cfg", "content": DEFINITION, "enabled": False},
        ]
    }
    assert declarative.declarative_adapter_names(config=disabled) == []


def test_cached_definitions_are_not_shared_mutably(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "example.yaml").write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()

    definitions, errors = declarative.discover_source_adapters()
    definitions.clear()
    errors.append({"source": "x", "error": "y"})

    again, again_errors = declarative.discover_source_adapters()
    assert len(again) == 1
    assert again_errors == []


CATASTROPHIC = DEFINITION.replace(
    "path_regex: '^/watch/(?P<id>[A-Za-z0-9_-]+)$'",
    "path_regex: '^/watch/(?P<id>(a+)+)$'",
)


@pytest.mark.parametrize("pattern", [
    r"^/watch/(?P<id>[A-Za-z0-9_-]+)$",
    r"^/(?:videos|watch)/(?P<id>[\w-]+)$",
    r"^/v/(?P<id>\d{1,12})(?:/[a-z]+)?$",
    r"^(?:[a-z]+/){1,10}$",
    r".*",
])
def test_ordinary_path_patterns_are_accepted(pattern):
    assert declarative._describe_unsafe_regex(pattern) == ""


@pytest.mark.parametrize("pattern,reason", [
    (r"^(a+)+b$", "nested unbounded"),
    (r"^(a*)*$", "nested unbounded"),
    (r"^(\w+\s?)*$", "nested unbounded"),
    (r"^(a|aa)+$", "alternation inside"),
    (r"^(a)\1$", "backreferences"),
    (r"^(?P<x>a)(?P=x)$", "backreferences"),
])
def test_backtracking_prone_patterns_are_rejected(pattern, reason):
    assert reason in declarative._describe_unsafe_regex(pattern)


def test_definition_with_a_catastrophic_pattern_is_refused(tmp_path, monkeypatch):
    """A shared adapter pack must not be able to wedge the URL field."""
    raw = declarative._parse_yaml(CATASTROPHIC, "test")
    errors = declarative.validate_definition(raw, "test")
    assert any("path_regex is unsafe" in error for error in errors), errors

    with pytest.raises(declarative.DeclarativeAdapterError, match="unsafe"):
        declarative.parse_definition_text(CATASTROPHIC, "test")

    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "evil.yaml").write_text(CATASTROPHIC, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()

    definitions, discovery_errors = declarative.discover_source_adapters()
    assert definitions == []
    assert any("unsafe" in error["error"] for error in discovery_errors)
    assert declarative.detect_declarative_extractor(
        "https://example.com/watch/" + "a" * 40
    ) is None


def test_config_supplied_catastrophic_pattern_is_refused():
    config_payload = {"source_adapters": [{"id": "evil", "content": CATASTROPHIC}]}
    declarative.invalidate_registry_cache()
    definitions, errors = declarative.discover_source_adapters(config=config_payload)
    assert definitions == []
    assert any("unsafe" in error["error"] for error in errors)

    with pytest.raises(declarative.DeclarativeAdapterError):
        declarative.validate_config_source_adapters(config_payload["source_adapters"])


def test_match_refuses_absurdly_long_paths(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "example.yaml").write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()

    definition, _errors = declarative.discover_source_adapters()
    definition = definition[0]
    assert definition.match("https://example.com/watch/abc") is not None
    huge = "a" * (declarative.MAX_MATCH_PATH_CHARS + 1)
    assert definition.match(f"https://example.com/watch/{huge}") is None
