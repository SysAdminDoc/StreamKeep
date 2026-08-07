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


def _approve_adapters(monkeypatch, directory=None, config=None):
    """Approve every parseable adapter so it becomes active (V147).

    A definition is inert until its contract is reviewed, so tests that
    exercise a working adapter have to stand in for that operator decision.
    """
    approvals = {}
    monkeypatch.setattr(
        declarative, "_reviewed_fingerprints", lambda cfg=None: approvals,
    )
    declarative.invalidate_registry_cache()
    for definition in declarative.pending_source_adapters(directory, config):
        approvals[definition.adapter_id] = definition.contract_fingerprint
    declarative.invalidate_registry_cache()
    return approvals


def test_declarative_definition_resolves_and_lists_models(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "example.yaml").write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    _approve_adapters(monkeypatch, adapter_dir)

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
    _approve_adapters(monkeypatch, adapter_dir)
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
    _approve_adapters(monkeypatch, adapter_dir)
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

    approvals = _approve_adapters(monkeypatch, adapter_dir)
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
    # The new file is a new contract, so it needs its own approval.
    for definition in declarative.pending_source_adapters(adapter_dir, None):
        approvals[definition.adapter_id] = definition.contract_fingerprint
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
    _approve_adapters(monkeypatch, adapter_dir, populated)
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
    _approve_adapters(monkeypatch, adapter_dir)

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
    _approve_adapters(monkeypatch, adapter_dir)

    definition, _errors = declarative.discover_source_adapters()
    definition = definition[0]
    assert definition.match("https://example.com/watch/abc") is not None
    huge = "a" * (declarative.MAX_MATCH_PATH_CHARS + 1)
    assert definition.match(f"https://example.com/watch/{huge}") is None


# ── V150: loader hardening ──────────────────────────────────────────

def test_merge_keys_are_flattened_not_stored_literally():
    """The custom construct_mapping replaced PyYAML's, which dropped
    flatten_mapping — so `<<:` silently became a field named '<<'."""
    text = """
base: &base
  timeout: 5
resolve:
  <<: *base
  extra: 1
"""
    result = declarative._parse_yaml(text, "merge.yaml")
    assert result["resolve"] == {"timeout": 5, "extra": 1}
    assert "<<" not in result["resolve"]


def test_an_unhashable_key_is_an_adapter_error_not_a_typeerror():
    """`key in mapping` raised a bare TypeError that escaped every handler."""
    text = "? [1, 2]\n: value\n"
    with pytest.raises(declarative.DeclarativeAdapterError, match="unhashable"):
        declarative._parse_yaml(text, "unhashable.yaml")


def test_duplicate_keys_are_still_refused():
    with pytest.raises(declarative.DeclarativeAdapterError, match="duplicate"):
        declarative._parse_yaml("id: a\nid: b\n", "dupe.yaml")


def test_an_unhashable_key_in_a_file_becomes_a_diagnostic_not_a_traceback():
    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as raw_dir:
        adapter_dir = _Path(raw_dir)
        (adapter_dir / "bad.yaml").write_text("? [1, 2]\n: value\n", encoding="utf-8")
        report = declarative.declarative_adapter_diagnostics(adapter_dir, config={})
    assert report["adapters"] == []
    assert any("unhashable" in item["error"] for item in report["errors"])


def test_an_oversized_definition_is_rejected_before_it_is_read(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    path = adapter_dir / "huge.yaml"
    path.write_text("x" * (declarative.MAX_DEFINITION_BYTES + 1024), encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()

    reads = []
    real_read = type(path).read_text

    def counting_read(self, *args, **kwargs):
        reads.append(str(self))
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", counting_read)
    definitions, errors = declarative.discover_source_adapters()
    assert definitions == []
    assert any("256 KiB" in item["error"] for item in errors)
    # The cap is applied from stat(), so the body is never pulled into memory.
    assert reads == []


# ── V151: adapter load errors are visible ───────────────────────────

def test_a_broken_adapter_is_logged_once_per_registry_change(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    path = adapter_dir / "broken.yaml"
    path.write_text("schema_version: 99\n", encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()
    declarative.reset_adapter_error_reporting()

    lines = []
    for _ in range(10):
        declarative.report_adapter_load_errors(lines.append)
    assert len(lines) == 1, lines
    assert str(path) in lines[0]

    # Editing the file is a new registry signature, so it is reported again.
    path.write_text("schema_version: 98\n", encoding="utf-8")
    declarative.invalidate_registry_cache()
    declarative.report_adapter_load_errors(lines.append)
    assert len(lines) == 2


def test_a_fixed_adapter_reports_an_empty_error_list(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    path = adapter_dir / "example.yaml"
    path.write_text("schema_version: 99\n", encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()
    declarative.reset_adapter_error_reporting()
    assert declarative.take_new_adapter_errors()
    assert declarative.take_new_adapter_errors() is None

    path.write_text(DEFINITION, encoding="utf-8")
    declarative.invalidate_registry_cache()
    assert declarative.take_new_adapter_errors() == []


def test_url_detection_still_works_when_an_adapter_is_broken(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "broken.yaml").write_text("schema_version: 99\n", encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()
    declarative.reset_adapter_error_reporting()
    assert Extractor.detect("https://www.youtube.com/watch?v=abc") is not None


def test_health_raises_and_clears_a_condition_for_a_broken_adapter():
    from streamkeep import health

    conditions = health._source_adapter_conditions("2026-08-06T00:00:00+00:00", {
        "adapters": [],
        "errors": [{"source": "C:/adapters/broken.yaml", "error": "schema_version"}],
    })
    assert len(conditions) == 1
    assert conditions[0]["id"] == "source_adapter:C:/adapters/broken.yaml"
    assert "broken.yaml" in conditions[0]["title"]
    assert conditions[0]["category"] == "extractor"

    # A registry that loads cleanly raises nothing, which is how the standing
    # condition clears on the next health run.
    assert health._source_adapter_conditions(
        "2026-08-06T00:00:00+00:00", {"adapters": [], "errors": []},
    ) == []


# ── V152: adapter responses cannot drive unbounded DNS ──────────────

def test_a_wide_response_resolves_each_host_once(monkeypatch):
    """500 items x 4 URLs used to mean up to 2,000 serialized getaddrinfo
    calls in a fetch worker."""
    resolved = []

    def guard(url, **_kwargs):
        from streamkeep.net_guard import normalize_remote_url
        _normalized, host, port = normalize_remote_url(url)
        resolved.append(host)
        return mock.Mock(url=url)

    monkeypatch.setattr(declarative, "validate_remote_url", guard)
    with declarative.bounded_url_validation():
        for index in range(500):
            declarative._validate_mapped_url(
                f"https://cdn.example.com/video/{index}.mp4"
            )
            declarative._safe_remote_url(
                f"https://cdn.example.com/thumb/{index}.jpg"
            )
    assert resolved == ["cdn.example.com"], resolved


def test_too_many_distinct_hosts_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(
        declarative, "validate_remote_url",
        lambda url, **_kw: mock.Mock(url=url),
    )
    with declarative.bounded_url_validation(limit=3):
        for index in range(3):
            declarative._validate_mapped_url(f"https://cdn{index}.example.com/a.mp4")
        with pytest.raises(
            declarative.RemoteURLPolicyError, match="more than 3 distinct hosts",
        ):
            declarative._validate_mapped_url("https://cdn99.example.com/a.mp4")


def test_a_refused_host_stays_refused_without_re_resolving(monkeypatch):
    calls = []

    def guard(url, **_kwargs):
        calls.append(url)
        raise declarative.RemoteURLPolicyError("Address class is not allowed")

    monkeypatch.setattr(declarative, "validate_remote_url", guard)
    with declarative.bounded_url_validation():
        for index in range(50):
            assert declarative._safe_remote_url(
                f"https://internal.example.com/{index}.mp4"
            ) == ""
    assert len(calls) == 1, calls


def test_a_syntactically_invalid_url_is_still_refused(monkeypatch):
    monkeypatch.setattr(
        declarative, "validate_remote_url",
        lambda url, **_kw: mock.Mock(url=url),
    )
    with declarative.bounded_url_validation():
        # No DNS involved: the scheme check happens before any resolution.
        with pytest.raises(declarative.RemoteURLPolicyError):
            declarative._validate_mapped_url("file:///etc/passwd")
        assert declarative._safe_remote_url("not a url at all") == ""


def test_the_budget_is_scoped_to_one_operation(monkeypatch):
    monkeypatch.setattr(
        declarative, "validate_remote_url",
        lambda url, **_kw: mock.Mock(url=url),
    )
    with declarative.bounded_url_validation(limit=1):
        declarative._validate_mapped_url("https://a.example.com/x.mp4")
    # A fresh operation starts with a fresh budget rather than inheriting one.
    with declarative.bounded_url_validation(limit=1):
        declarative._validate_mapped_url("https://b.example.com/x.mp4")


def test_operations_are_wrapped_in_a_budget():
    for name in ("resolve_stream", "list_vod_items", "check_live_value"):
        method = getattr(declarative.DeclarativeDefinition, name)
        assert getattr(method, "__wrapped__", None) is not None, name


# ── V156: the declarative HTTP response is closed ───────────────────

def test_the_guarded_response_is_closed_on_every_path(monkeypatch):
    class _Headers(dict):
        def get(self, key, default=""):
            return dict.get(self, key, default)

    class _Response:
        def __init__(self, status, headers, body=b""):
            self.status = status
            self.headers = _Headers(headers)
            self._body = body
            self.closed = False

        def getcode(self):
            return self.status

        def read(self, size=-1):
            return self._body if size < 0 else self._body[:size]

        def close(self):
            self.closed = True

    responses = [
        _Response(302, {"Location": "https://example.com/final"}),
        _Response(200, {"Content-Type": "application/json"}, b'{"ok": true}'),
    ]
    opened = []

    class _Opener:
        def open(self, _request, timeout=None):
            response = responses[len(opened)]
            opened.append(response)
            return response

    monkeypatch.setattr(
        declarative, "validate_remote_url",
        lambda url, **_kw: mock.Mock(url=str(url)),
    )
    monkeypatch.setattr(
        declarative.urllib.request, "build_opener", lambda *_a: _Opener(),
    )
    monkeypatch.setattr(
        declarative, "GuardedHTTPProxy",
        lambda **_kw: mock.MagicMock(
            __enter__=lambda self: mock.Mock(url="http://127.0.0.1:1"),
            __exit__=lambda self, *a: False,
        ),
    )
    body, _content_type = declarative._guarded_request(
        "https://example.com/start", method="GET", headers={},
        timeout=1, max_response_bytes=1024,
    )
    assert body == b'{"ok": true}'
    # Both the redirect hop and the final response are closed, not left to GC.
    assert [response.closed for response in opened] == [True, True]


# ── V147: an adapter is inert until its contract is reviewed ─────────

def test_a_new_adapter_is_inert_until_reviewed(tmp_path, monkeypatch):
    """A .yaml dropped in the directory used to go live on the next URL
    detection despite describing outbound requests."""
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "example.yaml").write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    approvals = {}
    monkeypatch.setattr(
        declarative, "_reviewed_fingerprints", lambda cfg=None: approvals,
    )
    declarative.invalidate_registry_cache()

    assert declarative.discover_source_adapters()[0] == []
    assert declarative.detect_declarative_extractor(
        "https://example.com/watch/vod-1"
    ) is None
    pending = declarative.pending_source_adapters()
    assert [item.adapter_id for item in pending] == ["example-source"]

    approvals["example-source"] = pending[0].contract_fingerprint
    assert declarative.declarative_adapter_names() == ["Example Source"]
    assert declarative.detect_declarative_extractor(
        "https://example.com/watch/vod-1"
    ) is not None


def test_an_unreviewed_adapter_never_issues_a_request(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "example.yaml").write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    monkeypatch.setattr(declarative, "_reviewed_fingerprints", lambda cfg=None: {})
    declarative.invalidate_registry_cache()

    requests = []
    monkeypatch.setattr(
        declarative, "_guarded_request",
        lambda *a, **k: requests.append(a) or (b"{}", "application/json"),
    )
    extractor = Extractor.detect("https://example.com/watch/vod-1")
    # Falls through to the yt-dlp catch-all rather than the unreviewed adapter.
    assert extractor is None or extractor.NAME != "Example Source"
    assert requests == []


def test_the_review_contract_names_hosts_methods_urls_and_headers():
    definition = declarative.parse_definition_text(DEFINITION, "example.yaml")
    contract = definition.review_contract()
    assert contract["hosts"] == ["example.com"]
    operations = {item["operation"]: item for item in contract["operations"]}
    assert set(operations) == {"resolve", "list_vods", "check_live"}
    assert operations["list_vods"]["method"] == "GET"
    assert operations["list_vods"]["url"] == (
        "https://api.example.com/channels/{channel}/videos"
    )
    assert isinstance(operations["resolve"]["headers"], list)


def test_editing_the_request_surface_requires_a_fresh_approval(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    path = adapter_dir / "example.yaml"
    path.write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    approvals = _approve_adapters(monkeypatch, adapter_dir)
    assert declarative.declarative_adapter_names() == ["Example Source"]

    # Repointing the adapter at another host invalidates the approval.
    path.write_text(
        DEFINITION.replace("api.example.com", "api.attacker.example"),
        encoding="utf-8",
    )
    assert declarative.declarative_adapter_names() == []
    assert [item.adapter_id for item in declarative.pending_source_adapters()] == [
        "example-source"
    ]
    assert approvals  # the stale approval is still recorded, just not matching


def test_a_cosmetic_edit_keeps_the_approval(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    path = adapter_dir / "example.yaml"
    path.write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    _approve_adapters(monkeypatch, adapter_dir)

    path.write_text(
        DEFINITION.replace("name: Example Source", "name: Renamed Source"),
        encoding="utf-8",
    )
    assert declarative.declarative_adapter_names() == ["Renamed Source"]


def test_approve_and_revoke_round_trip():
    config = {}
    config = declarative.approve_source_adapter("abc", "fingerprint-1", config)
    assert config[declarative.REVIEW_CONFIG_KEY] == {"abc": "fingerprint-1"}
    config = declarative.revoke_source_adapter("abc", config)
    assert config[declarative.REVIEW_CONFIG_KEY] == {}


def test_diagnostics_expose_what_needs_review(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "example.yaml").write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    monkeypatch.setattr(declarative, "_reviewed_fingerprints", lambda cfg=None: {})
    declarative.invalidate_registry_cache()

    report = declarative.declarative_adapter_diagnostics(adapter_dir, config={})
    assert report["adapters"] == []
    assert len(report["pending_review"]) == 1
    entry = report["pending_review"][0]
    assert entry["id"] == "example-source"
    assert entry["hosts"] == ["example.com"]
    assert entry["contract_fingerprint"]
    assert entry["source"].endswith("example.yaml")


def test_an_imported_config_cannot_pre_approve_an_adapter():
    from streamkeep import config as config_module

    quarantined, held = config_module._quarantine_import_capabilities({
        "source_adapters": [{"id": "cfg", "content": DEFINITION}],
        "reviewed_source_adapters": {"cfg": "whatever"},
    })
    assert quarantined["reviewed_source_adapters"] == {}
    assert quarantined["source_adapters"] == []
    assert "declarative_adapters" in held


# ── V155: bounded HTML parsing depth and selector cost ───────────────

def test_a_deeply_nested_document_does_not_exhaust_the_stack():
    """The walker and ``text()`` both recursed, so a nested body raised
    RecursionError — which is not in the exception set the request path
    handles, so it escaped the adapter as a crash."""
    depth = declarative.MAX_HTML_DEPTH * 20
    body = ("<div>" * depth) + "payload" + ("</div>" * depth)
    root = declarative._HTMLDocumentParser.parse(body.encode("utf-8"))

    assert root.text() == "payload"
    assert sum(1 for _ in declarative._walk_html(root)) == depth


def test_parse_depth_is_capped_but_no_content_is_dropped():
    depth = declarative.MAX_HTML_DEPTH + 50
    body = ("<div>" * depth) + "deep" + ("</div>" * depth)
    root = declarative._HTMLDocumentParser.parse(body.encode("utf-8"))

    # Every element is still recorded...
    assert sum(1 for _ in declarative._walk_html(root)) == depth
    # ...and the text past the cap is still reachable.
    assert "deep" in root.text()

    def measure(node, level=0):
        if not node.children:
            return level
        return max(measure(child, level + 1) for child in node.children)

    assert measure(root) <= declarative.MAX_HTML_DEPTH + 1


def test_a_selector_does_not_return_the_same_node_more_than_once():
    """Descendant combinators overlap: a node under two matched ancestors was
    collected once per ancestor, so each token multiplied the candidate set."""
    body = (
        "<div class='a'><div class='a'><div class='a'>"
        "<span class='t'>x</span>"
        "</div></div></div>"
    ).encode("utf-8")
    root = declarative._HTMLDocumentParser.parse(body)

    nodes = declarative._select_html_nodes(root, ".a .t")
    assert len(nodes) == 1
    assert nodes[0].tag == "span"


def test_a_wide_nested_document_matches_in_bounded_time():
    import time

    body = "<div class='a'>" * 200 + "<span class='t'>x</span>" + "</div>" * 200
    root = declarative._HTMLDocumentParser.parse(body.encode("utf-8"))

    started = time.monotonic()
    nodes = declarative._select_html_nodes(root, ".a .a .a .t")
    elapsed = time.monotonic() - started

    assert len(nodes) == 1
    # Without per-token dedupe this is combinatorial in the token count.
    assert elapsed < 2.0, f"selector matching took {elapsed:.1f}s"


def test_selector_matching_stops_once_nothing_matches():
    body = b"<div class='a'><span class='t'>x</span></div>"
    root = declarative._HTMLDocumentParser.parse(body)
    assert declarative._select_html_nodes(root, ".a .missing .t") == []


def test_the_iterative_walk_reproduces_the_recursive_text_order_exactly():
    """A node's own text has always come before any child's, so mixed content
    does not read in document order — `<p>one<b>two</b>three</p>` yields
    "one three two". That is pre-existing behaviour which adapters may map
    against, and making the walk iterative deliberately does not change it."""
    body = b"<p>one<b>two</b>three<i>four</i></p>"
    root = declarative._HTMLDocumentParser.parse(body)
    assert root.text() == "one three two four"


def test_nested_text_is_still_gathered_depth_first():
    body = b"<div><section><h1>title</h1><p>body</p></section></div>"
    root = declarative._HTMLDocumentParser.parse(body)
    assert root.text() == "title body"
