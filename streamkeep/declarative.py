"""Safe, hot-reloadable YAML source adapters.

Declarative adapters describe URL matching, a guarded HTTP request, and a
small response-to-model mapping.  They deliberately have no Python entry
point, filesystem primitive, subprocess hook, cookie access, or arbitrary
request method.  Definitions are parsed afresh when the registry signature
changes, so editing a YAML file takes effect without restarting StreamKeep.
"""

from __future__ import annotations

import contextlib
import copy
import functools
import hashlib
import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

from . import CURL_UA
from .models import QualityInfo, StreamInfo, VODInfo
from .net_guard import (
    GuardedHTTPProxy,
    RemoteURLPolicyError,
    normalize_remote_url,
    validate_remote_url,
)
from .paths import CONFIG_DIR

logger = logging.getLogger(__name__)

SOURCE_ADAPTERS_DIR = CONFIG_DIR / "source_adapters"

# Registry memoisation. Keyed on ``registry_signature`` so an edited, added or
# removed definition takes effect without a restart while a URL field that is
# being typed into does not reparse the whole directory per character.
_REGISTRY_LOCK = threading.Lock()
_REGISTRY_CACHE: dict[tuple, tuple] = {}
# Load errors are announced once per registry signature rather than once per
# detection call, so a broken definition produces one log line when it changes
# instead of one per keystroke in the URL field.
_ERROR_REPORT_LOCK = threading.Lock()
_LAST_REPORTED_SIGNATURE = None
DECLARATIVE_SCHEMA_VERSION = 1
MAX_DEFINITION_BYTES = 256 * 1024
MAX_DEFINITIONS = 128
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# Nesting depth a response document may reach before further elements are
# recorded without being descended into. Real pages sit far below this; the
# cap exists so a hostile or broken body cannot build a tree deep enough to
# exhaust the stack or make selector matching quadratic.
MAX_HTML_DEPTH = 256
_VOID_HTML_TAGS = frozenset({
    "area", "base", "br", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})
DEFAULT_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 8.0
MAX_TIMEOUT_SECONDS = 30.0
MAX_REDIRECTS = 5
MAX_HEADERS = 32
MAX_FIELDS = 64
MAX_QUALITIES = 32
MAX_ITEMS = 500
# Distinct hosts one adapter operation may make StreamKeep resolve. A single
# site legitimately spreads media across a handful of CDN names; a response
# naming hundreds is using the app as a DNS oracle.
MAX_HOST_RESOLUTIONS = 8

_SAFE_HEADER_NAMES = frozenset({
    "accept", "accept-language", "cache-control", "if-modified-since",
    "if-none-match", "referer", "user-agent",
})
_REQUEST_KEYS = frozenset({
    "url", "method", "headers", "params", "timeout_seconds",
    "max_response_bytes",
})
_RESPONSE_KEYS = frozenset({
    "format", "fields", "qualities", "items", "next_cursor",
})
_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "id", "name", "version", "enabled", "platform",
    "direct", "match", "request", "response", "resolve", "list_vods",
    "check_live",
})
_MATCH_KEYS = frozenset({"hosts", "path_regex", "channel_group"})
_QUALITY_KEYS = frozenset({
    "name", "url", "resolution", "bandwidth", "average_bandwidth",
    "frame_rate", "video_range", "format_type", "audio_url", "ytdlp_format",
})
_VOD_FIELDS = frozenset({
    "title", "date", "source", "is_live", "viewers", "duration",
    "duration_ms", "platform", "channel", "source_id", "webpage_url",
    "media_type", "thumbnail_url", "feed_url",
})
_STREAM_FIELDS = frozenset({
    "platform", "channel", "title", "url", "total_secs", "duration_str",
    "start_time", "is_live", "thumbnail_url", "source_id", "webpage_url",
    "feed_url", "chapters", "media_url", "qualities",
})
_FORMAT_TYPES = frozenset({"hls", "dash", "mp4", "ytdlp_direct"})
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+].*)?$")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_HTML_ATTR_RE = re.compile(
    r"^(?P<selector>.*?)(?:(?:::attr\((?P<colon>[A-Za-z_:][-A-Za-z0-9_:]*)\))|"
    r"@(?P<at>[A-Za-z_:][-A-Za-z0-9_:]*))?$"
)
_HTML_TOKEN_ATTR_RE = re.compile(
    r"\[(?P<name>[A-Za-z_:][-A-Za-z0-9_:]*)(?:\s*=\s*"
    r"(?P<value>[^\]]+))?\]"
)


class DeclarativeAdapterError(ValueError):
    """Raised when a YAML source adapter violates the data-only contract."""


_VALIDATION_STATE = threading.local()


@contextlib.contextmanager
def bounded_url_validation(limit=MAX_HOST_RESOLUTIONS):
    """Bound the DNS a single adapter response can make StreamKeep perform.

    A list response may carry ``MAX_ITEMS`` entries with several URLs each, and
    the address policy resolves synchronously. Validating each URL separately
    therefore meant up to ``MAX_ITEMS * 4`` serialized ``getaddrinfo`` calls
    per operation: hours of stalled fetch worker, and StreamKeep acting as a
    DNS oracle for whatever names the remote site chose to return.

    Inside this scope every URL is still normalized and every distinct host is
    still resolved and address-checked — but each host only once, and at most
    ``limit`` distinct hosts. The per-host decision is scoped to the one
    operation, and anything that actually opens a socket re-validates at
    connect time, so this narrows work rather than trust.
    """
    previous = getattr(_VALIDATION_STATE, "budget", None)
    _VALIDATION_STATE.budget = {"limit": max(1, int(limit)), "hosts": {}}
    try:
        yield _VALIDATION_STATE.budget
    finally:
        _VALIDATION_STATE.budget = previous


def _validate_mapped_url(value: Any) -> str:
    """Return a policy-approved URL that came out of an adapter response.

    Raises :class:`RemoteURLPolicyError` like ``validate_remote_url`` does, so
    existing callers keep their behaviour; the difference is that repeated
    hosts cost no extra resolution and an over-wide response is refused by
    name instead of grinding.
    """
    # ``normalize_remote_url`` applies the same syntactic policy as
    # ``validate_remote_url`` without touching DNS, so every URL is still
    # screened; the full check (which resolves) is what gets bounded.
    normalized, host, port = normalize_remote_url(value)
    budget = getattr(_VALIDATION_STATE, "budget", None)
    if budget is None:
        return validate_remote_url(normalized).url
    key = (host, port)
    if key not in budget["hosts"]:
        if len(budget["hosts"]) >= budget["limit"]:
            raise RemoteURLPolicyError(
                "adapter response referenced more than "
                f"{budget['limit']} distinct hosts"
            )
        try:
            validate_remote_url(normalized)
            budget["hosts"][key] = ""
        except RemoteURLPolicyError as error:
            budget["hosts"][key] = str(error) or "address policy refused the host"
    reason = budget["hosts"][key]
    if reason:
        raise RemoteURLPolicyError(reason)
    return normalized


def bounded_operation_urls(method):
    """Scope one adapter operation's DNS budget across its whole mapping pass."""
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        with bounded_url_validation():
            return method(*args, **kwargs)
    return wrapper



# Adapter patterns are matched on whatever thread handed us a URL — on the
# desktop that is the GUI thread, once per keystroke — and Python's ``re`` has
# no timeout and cannot be interrupted. A pattern is therefore rejected at
# validation time when its *shape* permits catastrophic backtracking, rather
# than being accepted and hoped about at match time.
MAX_REGEX_REPEATS = 32
MAX_REGEX_GROUP_DEPTH = 8
MAX_MATCH_PATH_CHARS = 4096


def _regex_parse_tree(pattern):
    """Return the parsed pattern, or ``None`` when the parser is unavailable."""
    try:
        import re._parser as parser
    except ImportError:  # pragma: no cover - CPython 3.11+ always provides it
        return None
    try:
        return parser.parse(pattern)
    except re.error:
        return None


def _describe_unsafe_regex(pattern):
    """Return why *pattern* may backtrack catastrophically, or ``""``.

    Rejected shapes: a backreference, and an unbounded repeat whose body holds
    another unbounded repeat or an alternation. Those are the constructs that
    turn a URL path into exponential work — ``(a+)+``, ``(a|aa)*``. Ordinary
    path patterns (character classes, named groups, single quantifiers) are
    unaffected.
    """
    tree = _regex_parse_tree(pattern)
    if tree is None:
        # Fall back to refusing the two textual markers we can recognise
        # without a parser rather than silently accepting everything.
        if re.search(r"\\[1-9]", pattern) or "(?P=" in pattern:
            return "backreferences are not allowed"
        if re.search(r"[+*}]\s*\)\s*[+*]", pattern):
            return "nested quantifiers are not allowed"
        return ""

    repeats = 0

    def unbounded(arg):
        _minimum, maximum, _body = arg
        return maximum >= _MAXREPEAT_SENTINEL

    def walk(subpattern, depth, inside_unbounded_repeat):
        nonlocal repeats
        if depth > MAX_REGEX_GROUP_DEPTH:
            return "the pattern nests groups too deeply"
        for op, arg in subpattern:
            name = str(op)
            if name in {"GROUPREF", "GROUPREF_EXISTS", "GROUPREF_IGNORE"}:
                return "backreferences are not allowed"
            if name in {"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"}:
                repeats += 1
                if repeats > MAX_REGEX_REPEATS:
                    return "the pattern uses too many quantifiers"
                is_unbounded = unbounded(arg)
                if inside_unbounded_repeat and is_unbounded:
                    return "nested unbounded quantifiers are not allowed"
                problem = walk(
                    arg[2], depth + 1,
                    inside_unbounded_repeat or is_unbounded,
                )
                if problem:
                    return problem
                continue
            if name == "BRANCH":
                if inside_unbounded_repeat:
                    return "an alternation inside an unbounded quantifier is not allowed"
                for branch in arg[1]:
                    problem = walk(branch, depth + 1, inside_unbounded_repeat)
                    if problem:
                        return problem
                continue
            if name == "SUBPATTERN":
                problem = walk(arg[3], depth + 1, inside_unbounded_repeat)
                if problem:
                    return problem
                continue
            if name in {"ASSERT", "ASSERT_NOT"}:
                problem = walk(arg[1], depth + 1, inside_unbounded_repeat)
                if problem:
                    return problem
                continue
            if name == "ATOMIC_GROUP":
                problem = walk(arg, depth + 1, inside_unbounded_repeat)
                if problem:
                    return problem
        return ""

    try:
        return walk(tree, 0, False)
    except (TypeError, ValueError, IndexError):
        # An unrecognised node shape must not be treated as proven safe.
        return "the pattern uses an unsupported construct"


def _max_repeat_sentinel():
    try:
        import re._constants as constants
        return int(constants.MAXREPEAT)
    except (ImportError, AttributeError, TypeError, ValueError):
        return 4294967295


_MAXREPEAT_SENTINEL = _max_repeat_sentinel()


@dataclass(frozen=True)
class DeclarativeDefinition:
    """Compiled, immutable source adapter definition."""

    adapter_id: str
    name: str
    version: str
    platform: str
    enabled: bool
    hosts: tuple[str, ...]
    path_pattern: re.Pattern[str]
    channel_group: str
    direct: bool
    resolve: dict[str, Any]
    list_vods: dict[str, Any] | None
    check_live: dict[str, Any] | None
    source: str = ""

    def review_contract(self) -> dict:
        """Describe, in plain terms, everything this adapter would request.

        This is what an operator is asked to approve before the definition is
        allowed to issue anything: which hosts it matches, and for each
        operation the method, the URL template, the header names and the query
        parameter names it sends. Response mapping is excluded — it shapes what
        StreamKeep does with an answer, not who it talks to.
        """
        operations = []
        for name, spec in (
            ("resolve", self.resolve),
            ("list_vods", self.list_vods),
            ("check_live", self.check_live),
        ):
            if not spec:
                continue
            request = spec.get("request", {}) or {}
            headers = request.get("headers", {}) or {}
            params = request.get("params", {}) or {}
            operations.append({
                "operation": name,
                "method": str(request.get("method", "GET") or "GET").upper(),
                "url": str(request.get("url", "") or ""),
                "headers": sorted(
                    f"{key}: {value}" for key, value in headers.items()
                ),
                "params": sorted(str(key) for key in params),
            })
        return {
            "id": self.adapter_id,
            "name": self.name,
            "version": self.version,
            "hosts": list(self.hosts),
            "operations": operations,
        }

    @property
    def contract_fingerprint(self) -> str:
        """Stable digest of exactly what ``review_contract`` shows.

        Keyed to the reviewed surface rather than to the file's bytes, so a
        comment, a display-name edit or a response-mapping tweak does not
        invalidate an approval — but any change to the hosts, method, URL,
        headers or parameters does, and the adapter goes inert until the
        operator reads the new contract.
        """
        contract = self.review_contract()
        return hashlib.sha256(
            json.dumps(
                {
                    "hosts": contract["hosts"],
                    "operations": contract["operations"],
                },
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def match(self, url: str):
        try:
            parsed = urllib.parse.urlsplit(str(url or "").strip())
        except ValueError:
            return None
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname.rstrip(".").lower()
        if not any(_host_matches(host, pattern) for pattern in self.hosts):
            return None
        path = parsed.path or "/"
        if len(path) > MAX_MATCH_PATH_CHARS:
            # Defence in depth behind the shape check: no adapter needs a path
            # this long, and a linear-time pattern over one is wasted work.
            return None
        match = self.path_pattern.fullmatch(path)
        if match is None:
            return None
        values = {key: str(value or "") for key, value in match.groupdict().items()}
        values.setdefault("url", str(url or "").strip())
        values.setdefault("host", host)
        values.setdefault("path", parsed.path or "/")
        values.setdefault("query", parsed.query or "")
        values.setdefault("channel", values.get(self.channel_group, ""))
        return values

    def _operation_context(self, url: str, operation: str, cursor: str = ""):
        try:
            source_url = validate_remote_url(url).url
        except RemoteURLPolicyError as error:
            raise DeclarativeAdapterError(
                f"source URL blocked: {error}"
            ) from error
        values = self.match(source_url)
        if values is None:
            raise DeclarativeAdapterError(
                f"URL does not match declarative adapter {self.adapter_id}"
            )
        values["url"] = source_url
        values["cursor"] = str(cursor or "")
        spec = self.resolve
        if operation == "list_vods":
            spec = self.list_vods
        elif operation == "check_live":
            spec = self.check_live
        if not isinstance(spec, dict):
            raise DeclarativeAdapterError(
                f"Adapter {self.adapter_id} does not support {operation}"
            )
        return spec, values

    @staticmethod
    def _render(value: str, variables: Mapping[str, Any]) -> str:
        text = str(value or "")
        allowed = {
            key: urllib.parse.quote(str(item or ""), safe="-._~")
            for key, item in variables.items()
        }
        allowed["url"] = str(variables.get("url", "") or "")
        allowed["query"] = str(variables.get("query", "") or "")
        unknown = {
            name for name in _PLACEHOLDER_RE.findall(text)
            if name not in allowed
        }
        if unknown:
            raise DeclarativeAdapterError(
                "Unknown request placeholder(s): " + ", ".join(sorted(unknown))
            )
        try:
            return text.format_map(_MissingPlaceholderMap(allowed))
        except (KeyError, ValueError) as error:
            raise DeclarativeAdapterError("Request URL template is malformed") from error

    def _request(self, url: str, operation: str, cursor: str = ""):
        spec, variables = self._operation_context(url, operation, cursor)
        request_spec = spec.get("request", {})
        response_spec = spec.get("response", {})
        target = self._render(str(request_spec.get("url", "")), variables)
        params = request_spec.get("params", {})
        if params:
            rendered_params = {
                str(key): self._render(str(value), variables)
                for key, value in params.items()
            }
            parsed = urllib.parse.urlsplit(target)
            query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            query.extend(rendered_params.items())
            target = urllib.parse.urlunsplit((
                parsed.scheme, parsed.netloc, parsed.path,
                urllib.parse.urlencode(query), "",
            ))
        body, _content_type = _guarded_request(
            target,
            method=str(request_spec.get("method", "GET")),
            headers=request_spec.get("headers", {}),
            timeout=float(request_spec.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
            max_response_bytes=int(
                request_spec.get("max_response_bytes", DEFAULT_RESPONSE_BYTES)
            ),
        )
        if str(response_spec.get("format", "json")) == "html":
            document = _HTMLDocumentParser.parse(body)
            return document, response_spec, variables
        try:
            payload = json.loads(body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DeclarativeAdapterError(
                f"Adapter {self.adapter_id} returned invalid JSON"
            ) from error
        return payload, response_spec, variables

    @bounded_operation_urls
    def resolve_stream(self, url: str, log_fn=None) -> StreamInfo | None:
        payload, response, variables = self._request(url, "resolve")
        fields = response.get("fields", {})
        values = _mapped_fields(payload, fields, response.get("format", "json"))
        qualities = _build_qualities(
            payload, response, values, response.get("format", "json"),
        )
        if not qualities:
            if log_fn:
                log_fn(f"[DECLARATIVE] {self.adapter_id}: no media quality found")
            return None
        info = StreamInfo(
            platform=str(values.get("platform") or self.platform),
            channel=str(values.get("channel") or variables.get("channel", "")),
            title=str(values.get("title") or ""),
            url=str(variables["url"]),
            qualities=qualities,
            total_secs=_as_float(values.get("total_secs")),
            duration_str=str(values.get("duration_str") or ""),
            start_time=str(values.get("start_time") or ""),
            is_live=_as_bool(values.get("is_live")),
            thumbnail_url=_safe_remote_url(values.get("thumbnail_url")),
            source_id=str(values.get("source_id") or ""),
            webpage_url=_safe_remote_url(
                values.get("webpage_url") or variables["url"]
            ),
            feed_url=_safe_remote_url(values.get("feed_url")),
        )
        chapters = values.get("chapters")
        if isinstance(chapters, list):
            info.chapters = [item for item in chapters if isinstance(item, dict)]
        from .extractors.base import Extractor
        return Extractor._canonicalize_stream_info(info, source_url=url)

    @bounded_operation_urls
    def list_vod_items(self, url: str, log_fn=None, cursor: str | None = None):
        payload, response, variables = self._request(
            url, "list_vods", cursor or "",
        )
        fmt = str(response.get("format", "json"))
        item_spec = response.get("items")
        if fmt == "html":
            if not isinstance(item_spec, dict):
                return [], None
            selector = str(item_spec.get("selector", ""))
            item_fields = item_spec.get("fields", {})
            roots = _select_html_nodes(payload, selector)
            records = [
                _mapped_fields(root, item_fields, fmt, html_root=root)
                for root in roots[:MAX_ITEMS]
            ]
        else:
            expression = item_spec if isinstance(item_spec, str) else "$.items[*]"
            raw_items = _extract_value(payload, expression, fmt)
            if isinstance(raw_items, list):
                raw_items = raw_items[:MAX_ITEMS]
            elif raw_items is None:
                raw_items = []
            else:
                raw_items = [raw_items]
            item_fields = response.get("fields", {})
            records = [
                _mapped_fields(item, item_fields, fmt)
                for item in raw_items
            ]
        result = []
        from .extractors.base import Extractor
        for values in records:
            source = str(values.get("source") or "")
            if not source:
                continue
            try:
                source = _validate_mapped_url(source)
            except RemoteURLPolicyError:
                continue
            item = VODInfo(
                title=str(values.get("title") or ""),
                date=str(values.get("date") or ""),
                source=source,
                is_live=_as_bool(values.get("is_live")),
                viewers=_as_int(values.get("viewers")),
                duration=str(values.get("duration") or ""),
                duration_ms=_as_int(values.get("duration_ms")),
                platform=str(values.get("platform") or self.platform),
                channel=str(values.get("channel") or variables.get("channel", "")),
                source_id=str(values.get("source_id") or ""),
                webpage_url=_safe_remote_url(
                    values.get("webpage_url") or source
                ),
                media_type=str(values.get("media_type") or "video"),
                thumbnail_url=_safe_remote_url(values.get("thumbnail_url")),
                feed_url=_safe_remote_url(values.get("feed_url")),
            )
            result.append(Extractor._canonicalize_vod_info(item, source_url=url))
        next_cursor = _extract_value(
            payload, str(response.get("next_cursor", "")), fmt,
            html_root=payload if fmt == "html" else None,
        ) if response.get("next_cursor") else None
        if isinstance(next_cursor, (dict, list)):
            next_cursor = ""
        return result, str(next_cursor) if next_cursor else None

    @bounded_operation_urls
    def check_live_value(self, url: str) -> bool | None:
        payload, response, _variables = self._request(url, "check_live")
        fields = response.get("fields", {})
        values = _mapped_fields(payload, fields, response.get("format", "json"))
        if "live" not in values:
            return None
        return _as_bool(values.get("live"))


class DeclarativeExtractor:
    """Extractor-compatible wrapper around one compiled YAML definition."""

    def __init__(self, definition: DeclarativeDefinition):
        self.definition = definition
        self.NAME = definition.name
        self.URL_PATTERNS = ()

    def resolve(self, url: str, log_fn=None):
        return self.definition.resolve_stream(url, log_fn)

    def list_vods(self, url, log_fn=None, cursor=None):
        return self.definition.list_vod_items(url, log_fn, cursor)

    def is_direct_url(self, _url):
        return self.definition.direct

    def supports_vod_listing(self):
        return self.definition.list_vods is not None

    def supports_live_check(self):
        return self.definition.check_live is not None

    def check_live(self, url):
        if self.definition.check_live is None:
            return None
        return self.definition.check_live_value(url)

    def extract_channel_id(self, url):
        values = self.definition.match(url)
        if values is None:
            return None
        return values.get("channel") or values.get("id") or None


class _MissingPlaceholderMap(dict):
    def __missing__(self, key):
        raise KeyError(key)


def _host_matches(host: str, pattern: str) -> bool:
    normalized = str(pattern or "").strip().lower().rstrip(".")
    if normalized.startswith("*."):
        base = normalized[2:]
        return host != base and host.endswith("." + base)
    return host == normalized


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "live"}
    return bool(value)


def _as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _safe_remote_url(value: Any) -> str:
    """Return a net-guard-approved optional URL, or an empty value."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return _validate_mapped_url(text)
    except RemoteURLPolicyError:
        return ""


def _json_path(value: Any, expression: str):
    text = str(expression or "")
    if text == "$":
        return value
    if not text.startswith("$"):
        return None
    tokens = []
    index = 1
    while index < len(text):
        if text[index] == ".":
            match = re.match(r"\.([A-Za-z_][A-Za-z0-9_-]*)", text[index:])
            if not match:
                return None
            tokens.append(match.group(1))
            index += len(match.group(0))
        elif text[index] == "[":
            end = text.find("]", index + 1)
            if end < 0:
                return None
            token = text[index + 1:end].strip()
            if token == "*":
                tokens.append("*")
            elif token.isdigit():
                tokens.append(int(token))
            else:
                return None
            index = end + 1
        else:
            return None
    values = [value]
    for token in tokens:
        next_values = []
        for current in values:
            if token == "*":
                if isinstance(current, list):
                    next_values.extend(current)
            elif isinstance(token, int):
                if isinstance(current, list) and token < len(current):
                    next_values.append(current[token])
            elif isinstance(current, Mapping) and token in current:
                next_values.append(current[token])
        values = next_values
        if not values:
            return None
    return values if any(token == "*" for token in tokens) else values[0]


def _mapped_fields(payload, fields, fmt, *, html_root=None):
    if not isinstance(fields, Mapping):
        return {}
    return {
        str(name): _extract_value(
            payload, str(expression), fmt, html_root=html_root,
        )
        for name, expression in fields.items()
    }


def _extract_value(payload, expression: str, fmt: str, *, html_root=None):
    text = str(expression or "")
    if text.startswith("literal:"):
        return text[8:]
    if fmt == "html":
        return _extract_html_value(
            html_root if html_root is not None else payload, text,
        )
    return _json_path(payload, text)


def _build_qualities(payload, response, values, fmt):
    qualities = []
    raw_specs = response.get("qualities", [])
    if isinstance(raw_specs, str):
        raw_specs = _extract_value(payload, raw_specs, fmt)
    if isinstance(raw_specs, list):
        for raw in raw_specs[:MAX_QUALITIES]:
            if not isinstance(raw, Mapping):
                continue
            qualities.append(_quality_from_mapping(payload, raw, fmt))
    if not qualities:
        raw_url = values.get("media_url") or values.get("url")
        urls = raw_url if isinstance(raw_url, list) else [raw_url]
        for index, raw_url in enumerate(urls[:MAX_QUALITIES]):
            if not raw_url:
                continue
            qualities.append(QualityInfo(
                name="source" if index == 0 else f"source_{index + 1}",
                url=str(raw_url), format_type="mp4",
            ))
    valid = []
    for quality in qualities:
        if not quality.url:
            continue
        try:
            quality.url = _validate_mapped_url(quality.url)
        except RemoteURLPolicyError:
            continue
        quality.audio_url = _safe_remote_url(quality.audio_url)
        valid.append(quality)
    return valid


def _quality_from_mapping(payload, raw, fmt):
    def get(name, default=""):
        expression = raw.get(name, "")
        if isinstance(expression, str) and expression.startswith(("$", "literal:")):
            value = _extract_value(payload, expression, fmt)
        else:
            value = expression if expression != "" else default
        return value

    format_type = str(get("format_type", "mp4") or "mp4")
    if format_type not in _FORMAT_TYPES:
        format_type = "mp4"
    return QualityInfo(
        name=str(get("name", "source") or "source"),
        url=str(get("url", "") or ""),
        resolution=str(get("resolution", "") or ""),
        bandwidth=_as_int(get("bandwidth")),
        average_bandwidth=_as_int(get("average_bandwidth")),
        frame_rate=_as_float(get("frame_rate")),
        video_range=str(get("video_range", "") or ""),
        format_type=format_type,
        audio_url=str(get("audio_url", "") or ""),
        ytdlp_format=str(get("ytdlp_format", "") or ""),
    )


@dataclass
class _HTMLNode:
    tag: str
    attrs: dict[str, str]
    children: list[Any]
    text_parts: list[str]

    def text(self):
        # Iterative pre-order: a recursive walk here raises RecursionError on
        # a deeply nested document, and RecursionError is not in the exception
        # set the request path handles, so it escaped as a crash.
        chunks = []
        stack = [self]
        while stack:
            node = stack.pop()
            chunks.extend(node.text_parts)
            stack.extend(reversed(node.children))
        return " ".join(item for item in chunks if item).strip()


class _HTMLDocumentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _HTMLNode("__root__", {}, [], [])
        self.stack = [self.root]

    @classmethod
    def parse(cls, body: bytes):
        parser = cls()
        parser.feed(body.decode("utf-8", errors="replace"))
        parser.close()
        return parser.root

    def handle_starttag(self, tag, attrs):
        node = _HTMLNode(
            str(tag).lower(),
            {str(key).lower(): str(value or "") for key, value in attrs},
            [], [],
        )
        self.stack[-1].children.append(node)
        if tag.lower() in _VOID_HTML_TAGS:
            return
        # Past the depth cap the element is still recorded as a child, so no
        # text or attribute is lost, but it is not descended into. An 8 MB
        # body of nothing but opening tags would otherwise build a tree as
        # deep as it is long.
        if len(self.stack) < MAX_HTML_DEPTH:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == str(tag).lower():
            self.stack.pop()

    def handle_endtag(self, tag):
        wanted = str(tag).lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == wanted:
                del self.stack[index:]
                return

    def handle_data(self, data):
        text = str(data).strip()
        if text:
            self.stack[-1].text_parts.append(text)


def _walk_html(root):
    """Yield every descendant in pre-order, without recursing."""
    stack = list(reversed(root.children))
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _selector_matches(node: _HTMLNode, token: str) -> bool:
    tag_match = re.match(r"^(?P<tag>[A-Za-z_*][A-Za-z0-9_-]*)?", token)
    tag = (tag_match.group("tag") if tag_match else "") or "*"
    if tag != "*" and node.tag != tag.lower():
        return False
    for class_name in re.findall(r"\.([A-Za-z_][A-Za-z0-9_-]*)", token):
        if class_name not in node.attrs.get("class", "").split():
            return False
    for id_name in re.findall(r"#([A-Za-z_][A-Za-z0-9_-]*)", token):
        if node.attrs.get("id", "") != id_name:
            return False
    for match in _HTML_TOKEN_ATTR_RE.finditer(token):
        expected = match.group("value")
        actual = node.attrs.get(match.group("name").lower())
        if actual is None:
            return False
        if expected is not None:
            expected = expected.strip().strip("'\"")
            if actual != expected:
                return False
    return True


def _select_html_nodes(root: _HTMLNode, selector: str):
    tokens = [item for item in str(selector or "").strip().split() if item]
    if not tokens:
        return []
    found = []
    # Track which selector prefixes are satisfied by the current ancestor
    # chain. A descendant selector can then be evaluated in one tree walk;
    # walking every matched ancestor's subtree separately is quadratic even
    # when duplicate result nodes are discarded afterwards.
    active_prefixes = [0] * len(tokens)
    stack = [
        (node, False, ()) for node in reversed(root.children)
    ]
    while stack:
        node, leaving, matched_prefixes = stack.pop()
        if leaving:
            for index in matched_prefixes:
                active_prefixes[index] -= 1
            continue

        matched = tuple(
            index for index, token in enumerate(tokens)
            if (index == 0 or active_prefixes[index - 1] > 0)
            and _selector_matches(node, token)
        )
        if matched:
            for index in matched:
                active_prefixes[index] += 1
            if len(tokens) - 1 in matched:
                found.append(node)
            stack.append((node, True, matched))
        stack.extend(
            (child, False, ()) for child in reversed(node.children)
        )
    return found


def _extract_html_value(root: _HTMLNode, selector: str):
    match = _HTML_ATTR_RE.match(str(selector or "").strip())
    if not match:
        return None
    base = str(match.group("selector") or "").strip()
    attr = match.group("colon") or match.group("at")
    nodes = _select_html_nodes(root, base)
    if not nodes:
        return None
    if attr:
        return nodes[0].attrs.get(attr.lower())
    return nodes[0].text()


def _parse_yaml(text: str, source: str):
    try:
        import yaml
    except ImportError as error:
        raise DeclarativeAdapterError(
            "PyYAML is required for declarative source adapters"
        ) from error

    class UniqueSafeLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            # PyYAML's own construct_mapping calls flatten_mapping first (which
            # expands `<<:` merge keys) and raises ConstructorError on an
            # unhashable key. Replacing it wholesale dropped both guards, so a
            # merge key became a literal field named "<<" and a list key
            # escaped as a bare TypeError.
            self.flatten_mapping(node)
            mapping = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                try:
                    duplicate = key in mapping
                except TypeError as error:
                    raise DeclarativeAdapterError(
                        f"unhashable YAML key in {source or 'definition'}: "
                        f"{error}"
                    ) from error
                if duplicate:
                    raise DeclarativeAdapterError(
                        f"duplicate YAML key {key!r} in {source or 'definition'}"
                    )
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping

    try:
        result = yaml.load(text, Loader=UniqueSafeLoader)
    except DeclarativeAdapterError:
        raise
    except yaml.YAMLError as error:
        raise DeclarativeAdapterError(
            f"invalid YAML in {source or 'definition'}: {error}"
        ) from error
    except RecursionError as error:
        # A deeply self-nesting document is a malformed definition, not a
        # StreamKeep crash.
        raise DeclarativeAdapterError(
            f"definition in {source or 'definition'} is nested too deeply"
        ) from error
    if not isinstance(result, dict):
        raise DeclarativeAdapterError("definition root must be a YAML mapping")
    return result


def _validate_request(spec, path, errors):
    if not isinstance(spec, dict):
        errors.append(f"{path} must be a mapping")
        return
    unknown = set(spec) - _REQUEST_KEYS
    if unknown:
        errors.append(f"{path} has unsupported fields: {', '.join(sorted(unknown))}")
    url = spec.get("url", "")
    if not isinstance(url, str) or not re.match(r"^https?://", url, re.IGNORECASE):
        errors.append(f"{path}.url must be an absolute HTTP(S) template")
    method = spec.get("method", "GET")
    if str(method).upper() not in {"GET", "HEAD"}:
        errors.append(f"{path}.method must be GET or HEAD")
    headers = spec.get("headers", {})
    if not isinstance(headers, dict) or len(headers) > MAX_HEADERS:
        errors.append(f"{path}.headers must contain at most {MAX_HEADERS} fields")
    else:
        for name, value in headers.items():
            if str(name).lower() not in _SAFE_HEADER_NAMES:
                errors.append(f"{path}.headers contains forbidden header {name!r}")
            if not isinstance(value, str) or len(value) > 8192:
                errors.append(f"{path}.headers values must be short strings")
            elif any(char in value for char in "\r\n"):
                errors.append(f"{path}.headers values cannot contain newlines")
    params = spec.get("params", {})
    if not isinstance(params, dict) or len(params) > 32:
        errors.append(f"{path}.params must be a small mapping")
    elif not all(isinstance(key, str) and isinstance(value, str) for key, value in params.items()):
        errors.append(f"{path}.params keys and values must be strings")
    try:
        timeout = float(spec.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        timeout = -1
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        errors.append(f"{path}.timeout_seconds must be between 0 and {MAX_TIMEOUT_SECONDS:g}")
    try:
        response_bytes = int(spec.get("max_response_bytes", DEFAULT_RESPONSE_BYTES))
    except (TypeError, ValueError):
        response_bytes = -1
    if response_bytes <= 0 or response_bytes > MAX_RESPONSE_BYTES:
        errors.append(f"{path}.max_response_bytes exceeds the safe response limit")


def _validate_response(spec, path, errors):
    if not isinstance(spec, dict):
        errors.append(f"{path} must be a mapping")
        return
    unknown = set(spec) - _RESPONSE_KEYS
    if unknown:
        errors.append(f"{path} has unsupported fields: {', '.join(sorted(unknown))}")
    fmt = str(spec.get("format", "json"))
    if fmt not in {"json", "html"}:
        errors.append(f"{path}.format must be json or html")
    fields = spec.get("fields", {})
    if not isinstance(fields, dict) or len(fields) > MAX_FIELDS:
        errors.append(f"{path}.fields must contain at most {MAX_FIELDS} fields")
    elif not all(isinstance(key, str) and isinstance(value, str) for key, value in fields.items()):
        errors.append(f"{path}.fields keys and values must be strings")
    raw_qualities = spec.get("qualities", [])
    if not isinstance(raw_qualities, (list, str)):
        errors.append(f"{path}.qualities must be a list or JSON path")
    elif isinstance(raw_qualities, list):
        if len(raw_qualities) > MAX_QUALITIES:
            errors.append(f"{path}.qualities contains too many entries")
        for index, quality in enumerate(raw_qualities):
            if not isinstance(quality, dict):
                errors.append(f"{path}.qualities[{index}] must be a mapping")
                continue
            unknown_quality = set(quality) - _QUALITY_KEYS
            if unknown_quality:
                errors.append(
                    f"{path}.qualities[{index}] has unsupported fields: "
                    + ", ".join(sorted(unknown_quality))
                )
            for key, value in quality.items():
                if not isinstance(value, (str, int, float)):
                    errors.append(f"{path}.qualities[{index}].{key} must be scalar")
            fmt_value = quality.get("format_type", "mp4")
            if str(fmt_value) not in _FORMAT_TYPES and not str(fmt_value).startswith(("$", "literal:")):
                errors.append(f"{path}.qualities[{index}].format_type is unsupported")
    items = spec.get("items")
    if items is not None and not isinstance(items, (str, dict)):
        errors.append(f"{path}.items must be a JSON path or HTML item mapping")
    if isinstance(items, dict):
        if set(items) - {"selector", "fields"}:
            errors.append(f"{path}.items has unsupported fields")
        if not isinstance(items.get("selector", ""), str) or not isinstance(items.get("fields", {}), dict):
            errors.append(f"{path}.items requires selector and fields")
    next_cursor = spec.get("next_cursor", "")
    if next_cursor and not isinstance(next_cursor, str):
        errors.append(f"{path}.next_cursor must be a string expression")


def validate_definition(raw: Mapping[str, Any], source: str = "") -> list[str]:
    """Return clear validation errors without importing or executing code."""
    errors: list[str] = []
    if not isinstance(raw, Mapping):
        return ["definition root must be a mapping"]
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        errors.append("unsupported fields: " + ", ".join(sorted(unknown)))
    if raw.get("schema_version") != DECLARATIVE_SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {DECLARATIVE_SCHEMA_VERSION}"
        )
    for key in ("id", "name", "version"):
        value = raw.get(key, "")
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            errors.append(f"{key} must be a short non-empty string")
    if raw.get("version") and not _SEMVER_RE.fullmatch(str(raw.get("version"))):
        errors.append("version must use X.Y.Z format")
    if not isinstance(raw.get("enabled", True), bool):
        errors.append("enabled must be boolean")
    if not isinstance(raw.get("direct", False), bool):
        errors.append("direct must be boolean")
    if raw.get("platform", "") and not isinstance(raw.get("platform"), str):
        errors.append("platform must be a string")
    match = raw.get("match", {})
    if not isinstance(match, dict):
        errors.append("match must be a mapping")
    else:
        unknown_match = set(match) - _MATCH_KEYS
        if unknown_match:
            errors.append("match has unsupported fields")
        hosts = match.get("hosts", [])
        if not isinstance(hosts, list) or not hosts or len(hosts) > 32:
            errors.append("match.hosts must contain 1 to 32 hosts")
        elif not all(isinstance(host, str) and host.strip() for host in hosts):
            errors.append("match.hosts must contain non-empty strings")
        path_regex = match.get("path_regex", r".*")
        if not isinstance(path_regex, str) or len(path_regex) > 512:
            errors.append("match.path_regex must be a short regex")
        else:
            try:
                compiled_path = re.compile(path_regex)
                unsafe = _describe_unsafe_regex(path_regex)
                if unsafe:
                    errors.append(f"match.path_regex is unsafe: {unsafe}")
                channel_group = match.get("channel_group", "")
                if (
                    isinstance(channel_group, str)
                    and channel_group
                    and channel_group not in compiled_path.groupindex
                ):
                    errors.append(
                        "match.channel_group must name a path_regex capture group"
                    )
            except re.error as error:
                errors.append(f"match.path_regex is invalid: {error}")
        if match.get("channel_group", "") and not isinstance(match.get("channel_group"), str):
            errors.append("match.channel_group must be a string")

    def operation(name, required=False):
        value = raw.get(name)
        if value is None and name == "resolve" and (
            "request" in raw or "response" in raw
        ):
            value = {"request": raw.get("request", {}), "response": raw.get("response", {})}
        if value is None:
            if required:
                errors.append(f"{name} operation is required")
            return
        if not isinstance(value, dict):
            errors.append(f"{name} must be a mapping")
            return
        if set(value) - {"request", "response"}:
            errors.append(f"{name} has unsupported fields")
        _validate_request(value.get("request", {}), f"{name}.request", errors)
        _validate_response(value.get("response", {}), f"{name}.response", errors)

    operation("resolve", required=True)
    operation("list_vods")
    operation("check_live")
    return errors


def parse_definition(raw: Mapping[str, Any], source: str = "") -> DeclarativeDefinition:
    errors = validate_definition(raw, source)
    if errors:
        raise DeclarativeAdapterError(
            f"{source or raw.get('id', 'definition')}: " + "; ".join(errors)
        )
    match = raw["match"]
    resolve = raw.get("resolve")
    if resolve is None:
        resolve = {
            "request": raw.get("request", {}),
            "response": raw.get("response", {}),
        }
    path_regex = str(match.get("path_regex", r".*"))
    unsafe = _describe_unsafe_regex(path_regex)
    if unsafe:
        # validate_definition already reports this; refuse here too so a
        # definition can never reach the registry through another entry point.
        raise DeclarativeAdapterError(f"match.path_regex is unsafe: {unsafe}")
    path_pattern = re.compile(path_regex)
    return DeclarativeDefinition(
        adapter_id=str(raw["id"]),
        name=str(raw["name"]),
        version=str(raw["version"]),
        platform=str(raw.get("platform", "") or raw["name"]),
        enabled=bool(raw.get("enabled", True)),
        hosts=tuple(str(host).strip().lower().rstrip(".") for host in match["hosts"]),
        path_pattern=path_pattern,
        channel_group=str(match.get("channel_group", "channel") or "channel"),
        direct=bool(raw.get("direct", False)),
        resolve=copy.deepcopy(resolve),
        list_vods=copy.deepcopy(raw.get("list_vods")) if raw.get("list_vods") is not None else None,
        check_live=copy.deepcopy(raw.get("check_live")) if raw.get("check_live") is not None else None,
        source=str(source or ""),
    )


def parse_definition_text(text: str, source: str = "") -> DeclarativeDefinition:
    if not isinstance(text, str):
        raise DeclarativeAdapterError("definition must be UTF-8 YAML text")
    if len(text.encode("utf-8")) > MAX_DEFINITION_BYTES:
        raise DeclarativeAdapterError("definition exceeds the 256 KiB limit")
    return parse_definition(_parse_yaml(text, source), source)


def serialize_definition(raw: Mapping[str, Any]) -> str:
    """Return deterministic YAML that round-trips through the safe parser."""
    errors = validate_definition(raw, "generated definition")
    if errors:
        raise DeclarativeAdapterError(
            "generated definition: " + "; ".join(errors)
        )
    try:
        import yaml
    except ImportError as error:
        raise DeclarativeAdapterError(
            "PyYAML is required for declarative source adapters"
        ) from error
    text = yaml.safe_dump(
        copy.deepcopy(dict(raw)),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )
    parse_definition_text(text, "generated definition")
    return text


def write_source_adapter_draft(text: str, directory=None, config=None):
    """Create one new adapter file without overwriting an existing draft.

    The helper fails closed if the id still has any stored approval. Merely
    writing the definition never approves it; normal registry discovery places
    it in the pending-review collection.
    """
    definition = parse_definition_text(text, "generated definition")
    if not definition.enabled:
        raise DeclarativeAdapterError(
            "a disabled definition cannot enter the pending review queue"
        )
    if config is None:
        try:
            from .config import load_config
            config = load_config()
        except Exception as error:
            raise DeclarativeAdapterError(
                f"cannot verify adapter review state: {error}"
            ) from error
    if definition.adapter_id in _reviewed_fingerprints(config):
        raise DeclarativeAdapterError(
            "adapter id still has a stored approval; revoke it before "
            "writing a new draft"
        )
    target_dir = Path(directory or SOURCE_ADAPTERS_DIR)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        existing = [
            path for path in target_dir.iterdir()
            if not path.is_symlink()
            and path.is_file()
            and path.suffix.lower() in {".yaml", ".yml"}
        ]
    except OSError as error:
        raise DeclarativeAdapterError(
            f"cannot prepare source adapter directory: {error}"
        ) from error
    if len(existing) >= MAX_DEFINITIONS:
        raise DeclarativeAdapterError(
            f"source adapter directory already has the {MAX_DEFINITIONS} definition limit"
        )
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", definition.adapter_id).strip(".-")
    if not safe_id:
        raise DeclarativeAdapterError("adapter id cannot form a safe file name")
    target = target_dir / f"{safe_id}.yaml"
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as error:
        raise DeclarativeAdapterError(
            f"adapter draft already exists: {target}"
        ) from error
    except OSError as error:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise DeclarativeAdapterError(
            f"cannot write adapter draft: {error}"
        ) from error
    invalidate_registry_cache()
    return target, definition


def validate_definition_text(text: str, source: str = "") -> list[str]:
    try:
        if not isinstance(text, str):
            return ["definition must be UTF-8 YAML text"]
        if len(text.encode("utf-8")) > MAX_DEFINITION_BYTES:
            return ["definition exceeds the 256 KiB limit"]
        return validate_definition(_parse_yaml(text, source), source)
    except DeclarativeAdapterError as error:
        return [str(error)]


def validate_config_source_adapters(entries) -> None:
    if not isinstance(entries, list) or len(entries) > MAX_DEFINITIONS:
        raise DeclarativeAdapterError(
            f"source_adapters must contain at most {MAX_DEFINITIONS} entries"
        )
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise DeclarativeAdapterError(f"source_adapters[{index}] must be an object")
        if set(entry) - {"id", "content", "enabled"}:
            raise DeclarativeAdapterError(
                f"source_adapters[{index}] has unsupported fields"
            )
        content = entry.get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise DeclarativeAdapterError(
                f"source_adapters[{index}].content must be YAML text"
            )
        if entry.get("id", "") and not isinstance(entry.get("id"), str):
            raise DeclarativeAdapterError(
                f"source_adapters[{index}].id must be a string"
            )
        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            raise DeclarativeAdapterError(
                f"source_adapters[{index}].enabled must be boolean"
            )
        definition = parse_definition_text(content, f"source_adapters[{index}]")
        if definition.adapter_id in seen:
            raise DeclarativeAdapterError(
                f"duplicate source adapter id: {definition.adapter_id}"
            )
        seen.add(definition.adapter_id)


def _config_entries(config):
    if not isinstance(config, dict):
        try:
            from .config import load_config
            config = load_config()
        except Exception:
            config = {}
    entries = config.get("source_adapters", [])
    if not isinstance(entries, list):
        return []
    result = []
    for index, entry in enumerate(entries[:MAX_DEFINITIONS]):
        if not isinstance(entry, dict):
            continue
        content = entry.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        source = f"config:source_adapters[{index}]"
        if entry.get("id"):
            source += f" ({entry['id']})"
        try:
            definition = parse_definition_text(content, source)
            if entry.get("enabled", True) is False:
                definition = replace(definition, enabled=False)
            result.append(definition)
        except DeclarativeAdapterError as error:
            yield None, {"source": source, "error": str(error)}
            continue
        yield definition, None


REVIEW_CONFIG_KEY = "reviewed_source_adapters"


def _reviewed_fingerprints(config=None) -> dict:
    """Return the operator's approved ``{adapter_id: contract_fingerprint}``."""
    if not isinstance(config, dict):
        try:
            from .config import load_config
            config = load_config()
        except Exception:
            # safe: an unreadable config approves nothing, which fails closed.
            return {}
    raw = config.get(REVIEW_CONFIG_KEY, {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in list(raw.items())[:MAX_DEFINITIONS]
        if key and isinstance(value, str)
    }


def adapter_review_state(definition, config=None) -> bool:
    """Return whether *definition*'s current contract has been approved."""
    approved = _reviewed_fingerprints(config).get(definition.adapter_id, "")
    return bool(approved) and approved == definition.contract_fingerprint


def approve_source_adapter(adapter_id, fingerprint, config=None):
    """Record an operator approval for one adapter contract.

    Returns the updated config dict. The caller persists it; keeping the write
    out of here lets the GUI batch it with the rest of a settings save and lets
    the CLI save immediately.
    """
    if not isinstance(config, dict):
        from .config import load_config
        config = load_config()
    reviews = config.get(REVIEW_CONFIG_KEY)
    if not isinstance(reviews, dict):
        reviews = {}
    reviews = dict(reviews)
    reviews[str(adapter_id)] = str(fingerprint)
    config[REVIEW_CONFIG_KEY] = reviews
    invalidate_registry_cache()
    return config


def revoke_source_adapter(adapter_id, config=None):
    """Withdraw an approval so the adapter goes inert again."""
    if not isinstance(config, dict):
        from .config import load_config
        config = load_config()
    reviews = config.get(REVIEW_CONFIG_KEY)
    if isinstance(reviews, dict) and str(adapter_id) in reviews:
        reviews = dict(reviews)
        reviews.pop(str(adapter_id), None)
        config[REVIEW_CONFIG_KEY] = reviews
        invalidate_registry_cache()
    return config


def registry_signature(directory=None, config=None):
    """Return a cheap fingerprint of everything the registry is built from.

    Each definition contributes its name, size, and a content digest, so any
    edit forces a reparse even when it leaves the byte count unchanged. Reading
    the bytes is still far cheaper than parsing, validating, and fingerprinting
    them. A change to the set of files or to the config entries also produces a
    different signature.
    """
    directory = Path(directory or SOURCE_ADAPTERS_DIR)
    entries = []
    try:
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() not in {".yaml", ".yml"}:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    entries.append((path.name, -1, -1))
                    continue
                if not path.is_file():
                    continue
                # Size and mtime alone are not a content identity. Windows
                # file timestamps advance on a coarse tick, so an edit that
                # keeps the byte count the same — swapping a host for another
                # of equal length — can land inside one tick and produce a
                # signature identical to the previous content. The definition
                # then stays cached and the operator's edit silently does
                # nothing. Reading the bytes is still far cheaper than the
                # YAML parse, validation, and fingerprinting this guards.
                try:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    digest = "unreadable"
                entries.append((path.name, stat.st_size, digest))
    except OSError as error:
        entries.append((str(error), -1, -1))
    if isinstance(config, dict):
        raw = config.get("source_adapters", [])
    else:
        try:
            from .config import load_config
            raw = load_config().get("source_adapters", [])
        except Exception:
            # safe: an unreadable config yields an empty adapter set below, and
            # discover_source_adapters records the real error.
            raw = []
    config_key = ()
    if isinstance(raw, list):
        config_key = tuple(
            (
                str(entry.get("id", "")),
                bool(entry.get("enabled", True)),
                hashlib.sha256(
                    str(entry.get("content", "")).encode("utf-8", "replace")
                ).hexdigest(),
            )
            for entry in raw[:MAX_DEFINITIONS]
            if isinstance(entry, dict)
        )
    reviews = tuple(sorted(_reviewed_fingerprints(config).items()))
    return (str(directory), tuple(entries), config_key, reviews)


def discover_source_adapters(directory=None, config=None):
    """Return ``(definitions, errors)`` from files and config entries.

    Results are memoised on :func:`registry_signature`. ``Extractor.detect``
    calls this for every URL it is handed — which on the desktop means every
    keystroke in the URL field — so an uncached implementation re-read and
    re-parsed up to ``MAX_DEFINITIONS`` YAML files, recompiled their regexes,
    and reloaded the config on each character typed.
    """
    definitions, errors, _pending = _discover_all(directory, config)
    return definitions, errors


def pending_source_adapters(directory=None, config=None):
    """Return definitions that parse cleanly but await an operator review."""
    _definitions, _errors, pending = _discover_all(directory, config)
    return pending


def _discover_all(directory=None, config=None):
    signature = registry_signature(directory, config)
    with _REGISTRY_LOCK:
        cached = _REGISTRY_CACHE.get(signature)
    if cached is not None:
        definitions, errors, pending = cached
        return (
            list(definitions),
            [dict(error) for error in errors],
            list(pending),
        )
    definitions, errors, pending = _build_source_adapters(directory, config)
    with _REGISTRY_LOCK:
        # Definitions are frozen dataclasses with precompiled patterns, so the
        # cached tuple is safe to share; callers get fresh list copies.
        _REGISTRY_CACHE.clear()
        _REGISTRY_CACHE[signature] = (
            tuple(definitions), tuple(errors), tuple(pending),
        )
    return definitions, errors, pending


def invalidate_registry_cache():
    """Drop the memoised registry. Intended for tests and explicit reloads."""
    with _REGISTRY_LOCK:
        _REGISTRY_CACHE.clear()


def _build_source_adapters(directory=None, config=None):
    """Parse every adapter definition from disk and config.

    Returns ``(active, errors, pending)``. A definition only reaches *active*
    once the operator has approved its current contract: a ``.yaml`` file
    dropped into the adapters directory describes outbound requests and
    response mapping, so it stays inert until reviewed, exactly as an imported
    yt-dlp template does (V147).
    """
    directory = Path(directory or SOURCE_ADAPTERS_DIR)
    definitions: list[DeclarativeDefinition] = []
    pending: list[DeclarativeDefinition] = []
    reviewed = _reviewed_fingerprints(config)
    errors: list[dict[str, str]] = []
    seen: set[str] = set()

    def admit(definition):
        if not definition.enabled:
            return
        approved = reviewed.get(definition.adapter_id, "")
        if approved and approved == definition.contract_fingerprint:
            definitions.append(definition)
        else:
            pending.append(definition)
    paths = []
    try:
        if directory.is_dir():
            paths = sorted(
                path for path in directory.iterdir()
                # ``is_file()`` follows symlinks, so a link pointing at a
                # multi-gigabyte file used to be read whole before the byte cap
                # was applied to its text. Judge the entry itself.
                if not path.is_symlink()
                and path.is_file()
                and path.suffix.lower() in {".yaml", ".yml"}
            )[:MAX_DEFINITIONS]
    except OSError as error:
        errors.append({"source": str(directory), "error": str(error)})
    for path in paths:
        try:
            size = path.stat(follow_symlinks=False).st_size
            if size > MAX_DEFINITION_BYTES:
                raise DeclarativeAdapterError(
                    f"definition is {size} bytes, over the 256 KiB limit"
                )
            definition = parse_definition_text(
                path.read_text(encoding="utf-8"), str(path),
            )
            if definition.adapter_id in seen:
                raise DeclarativeAdapterError(
                    f"duplicate source adapter id: {definition.adapter_id}"
                )
            seen.add(definition.adapter_id)
            admit(definition)
        except (OSError, UnicodeError, DeclarativeAdapterError) as error:
            errors.append({"source": str(path), "error": str(error)})
        except Exception as error:
            # One malformed definition must never take the whole registry
            # down, and diagnostics must never show a raw traceback. Anything
            # the parsers did not classify is reported against its own file.
            errors.append({
                "source": str(path),
                "error": f"unhandled {type(error).__name__}: {error}",
            })
    for definition, error in _config_entries(config):
        if error is not None:
            errors.append(error)
            continue
        if definition.adapter_id in seen:
            errors.append({
                "source": definition.source,
                "error": f"duplicate source adapter id: {definition.adapter_id}",
            })
            continue
        seen.add(definition.adapter_id)
        admit(definition)
    if len(definitions) > MAX_DEFINITIONS:
        definitions = definitions[:MAX_DEFINITIONS]
    return definitions, errors, pending[:MAX_DEFINITIONS]


def declarative_adapter_diagnostics(directory=None, config=None):
    definitions, errors, pending = _discover_all(directory, config)
    return {
        "pending_review": [
            {
                **definition.review_contract(),
                "source": definition.source,
                "contract_fingerprint": definition.contract_fingerprint,
            }
            for definition in pending
        ],
        "adapters": [
            {
                "id": definition.adapter_id,
                "name": definition.name,
                "version": definition.version,
                "platform": definition.platform,
                "source": definition.source,
                "hosts": list(definition.hosts),
                "supports_vod_listing": definition.list_vods is not None,
                "supports_live_check": definition.check_live is not None,
                "direct": definition.direct,
                # The approved contract travels with the active adapter so a
                # review surface can show what was approved, and revoke it,
                # without re-parsing the definition itself.
                "operations": definition.review_contract()["operations"],
                "contract_fingerprint": definition.contract_fingerprint,
            }
            for definition in definitions
        ],
        "errors": errors,
    }


def declarative_adapter_names(directory=None, config=None):
    definitions, _errors = discover_source_adapters(directory, config)
    return [definition.name for definition in definitions]


def take_new_adapter_errors(directory=None, config=None):
    """Return load errors once per changed registry signature.

    URL detection calls into the registry for every URL it is handed — on the
    desktop, every keystroke in the URL field — so reporting the error list on
    each call would bury the log. The signature changes exactly when a
    definition is added, edited or removed, which is when the operator needs
    to hear about it again.

    Returns ``None`` when nothing has changed since the last report, and a
    (possibly empty) list otherwise; an empty list means the definitions now
    load cleanly and any standing notice can be cleared.
    """
    global _LAST_REPORTED_SIGNATURE
    signature = registry_signature(directory, config)
    with _ERROR_REPORT_LOCK:
        if signature == _LAST_REPORTED_SIGNATURE:
            return None
        _LAST_REPORTED_SIGNATURE = signature
    _definitions, errors = discover_source_adapters(directory, config)
    return errors


def reset_adapter_error_reporting():
    """Forget the last reported signature (used by tests and diagnostics)."""
    global _LAST_REPORTED_SIGNATURE
    with _ERROR_REPORT_LOCK:
        _LAST_REPORTED_SIGNATURE = None


def report_adapter_load_errors(log_fn=None, directory=None, config=None):
    """Log one line per newly-broken adapter definition.

    A typo'd adapter used to fall through to the yt-dlp catch-all silently:
    the error list was discarded here and the caller wrapped the whole call in
    ``except Exception: pass``. The project's rule is that background failures
    must be visible.
    """
    errors = take_new_adapter_errors(directory, config)
    if not errors:
        return errors
    for entry in errors:
        message = (
            f"[ADAPTERS] {entry.get('source', 'source adapter')}: "
            f"{entry.get('error', 'could not be loaded')}"
        )
        logger.warning("%s", message)
        if log_fn is not None:
            try:
                log_fn(message)
            except Exception as error:
                logger.debug("[ADAPTERS] Could not forward error line: %s", error)
    return errors


def detect_declarative_extractor(url: str):
    definitions, _errors = discover_source_adapters()
    for definition in definitions:
        if definition.match(url) is not None:
            return DeclarativeExtractor(definition)
    return None


def _guarded_request(url, *, method, headers, timeout, max_response_bytes):
    try:
        target = validate_remote_url(url)
    except RemoteURLPolicyError as error:
        raise DeclarativeAdapterError(f"request URL blocked: {error}") from error
    clean_headers = {str(key): str(value) for key, value in dict(headers or {}).items()}
    clean_headers.setdefault("User-Agent", CURL_UA)
    opener = None
    current = target.url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            with GuardedHTTPProxy(connect_timeout=timeout) as proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({
                        "http": proxy.url,
                        "https": proxy.url,
                    }),
                    _NoRedirectHandler(),
                )
                request = urllib.request.Request(
                    current, headers=clean_headers, method=str(method).upper(),
                )
                try:
                    response = opener.open(request, timeout=timeout)
                except urllib.error.HTTPError as error:
                    response = error
                # Closed on every path — returned body, redirect `continue`,
                # and raised error alike. Without this the socket to the
                # guarded proxy was only reclaimed at GC, and a redirect chain
                # leaked one per hop.
                with contextlib.closing(response):
                    status = int(getattr(response, "status", response.getcode()))
                    if status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location", "")
                        if not location:
                            raise DeclarativeAdapterError("redirect has no Location")
                        current = validate_remote_url(
                            urllib.parse.urljoin(current, location)
                        ).url
                        continue
                    if status < 200 or status >= 300:
                        raise DeclarativeAdapterError(f"request returned HTTP {status}")
                    length = response.headers.get("Content-Length", "")
                    try:
                        if length and int(length) > max_response_bytes:
                            raise DeclarativeAdapterError("response exceeds configured byte limit")
                    except ValueError:
                        pass
                    body = response.read(max_response_bytes + 1)
                    if len(body) > max_response_bytes:
                        raise DeclarativeAdapterError("response exceeds configured byte limit")
                    return body, str(response.headers.get("Content-Type", ""))
        except DeclarativeAdapterError:
            raise
        except (OSError, urllib.error.URLError, RemoteURLPolicyError) as error:
            raise DeclarativeAdapterError(f"guarded request failed: {error}") from error
    raise DeclarativeAdapterError("too many redirects")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _new):
        return None
