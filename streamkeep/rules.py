"""Ordered rules engine (V15, Packagizer-class).

A list of user-defined rules is evaluated top-to-bottom against a *context*
describing a pending download (site, uploader, title, duration, type, url).
Every enabled rule whose match criteria are satisfied contributes its actions
to an accumulated result; later rules override earlier ones for the same
action key, and a rule with ``stop: true`` halts evaluation once it matches.

Actions steer how the job is handled:

    output_dir          — destination folder override
    arg_template        — named yt-dlp / structured argv template
    folder_template     — output folder template override
    file_template       — output filename template override
    pp_preset           — named post-processing preset
    quality             — quality preference ("best"/"1080p"/"audio"/...)
    proxy               — per-job proxy URL
    auth_profile        — named site-bound authentication profile (V50)
    priority            — integer; higher sorts earlier in the queue
    auto_start          — begin immediately vs. hold in queue

The engine is pure and serialization-friendly (rules are plain dicts stored in
config under the ``rules`` key). All matching is fail-closed: a malformed regex
or bad rule never raises and never spuriously matches.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

CONFIG_KEY = "rules"

# Recognised action keys and the type each is coerced to when applied.
ACTION_KEYS = (
    "output_dir",
    "arg_template",
    "folder_template",
    "file_template",
    "pp_preset",
    "quality",
    "proxy",
    "auth_profile",
    "priority",
    "auto_start",
)

_STRING_ACTIONS = frozenset({
    "output_dir", "arg_template", "folder_template", "file_template",
    "pp_preset", "quality", "proxy", "auth_profile",
})

_LEGACY_ACTION_ALIASES = {"filename_template": "arg_template"}

_MATCH_MODES = ("all", "any")
_KNOWN_TYPES = frozenset({"video", "audio", "live", "playlist", "image", ""})
MAX_RULES = 128
MAX_RULE_NAME_CHARS = 128
MAX_RULE_TEXT_CHARS = 4096
_ALLOWED_RULE_KEYS = frozenset({
    "name", "enabled", "match", "match_mode", "actions", "stop",
})
_ALLOWED_MATCH_KEYS = frozenset({
    "site", "url_regex", "uploader", "title_regex", "type",
    "duration_min", "duration_max",
})
_ALLOWED_ACTION_KEYS = frozenset(ACTION_KEYS) | set(_LEGACY_ACTION_ALIASES)


def site_from_url(url):
    """Return the bare registrable-ish host of a URL (``www.`` stripped)."""
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def context_from_job(job):
    """Build a match context dict from a queue-job / request dict.

    Missing fields default empty; ``site`` is derived from the URL when the
    caller did not supply one. ``duration`` is coerced to a float (seconds).
    """
    job = dict(job or {})
    url = str(job.get("url", "") or "")
    duration = job.get("duration", job.get("total_secs", 0))
    try:
        duration = float(duration or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "url": url,
        "site": str(job.get("site") or site_from_url(url)),
        "uploader": str(job.get("uploader", job.get("channel", "")) or ""),
        "title": str(job.get("title", "") or ""),
        "duration": duration,
        "type": str(job.get("type", "") or "").lower(),
    }


def _safe_search(pattern, value):
    if not pattern:
        return True
    try:
        return re.search(pattern, value, re.IGNORECASE) is not None
    except re.error:
        # A malformed user regex must never match and never crash the queue.
        return False


def _normalize_site(value):
    """Normalize a host-shaped site value for boundary-safe comparison."""
    site = str(value or "").strip().lower().rstrip(".")
    if site.startswith("www."):
        site = site[4:]
    return site


def _criterion_results(match, context):
    """Yield (satisfied, present) for each declared match criterion."""
    if not isinstance(match, dict):
        return

    site = _normalize_site(match.get("site", ""))
    context_site = _normalize_site(context.get("site", ""))
    if site:
        yield bool(context_site) and (
            context_site == site or context_site.endswith("." + site)
        ), True

    url_regex = str(match.get("url_regex", "") or "")
    if url_regex:
        yield _safe_search(url_regex, context["url"]), True

    uploader = str(match.get("uploader", "") or "")
    if uploader:
        yield uploader.lower() in context["uploader"].lower(), True

    title_regex = str(match.get("title_regex", "") or "")
    if title_regex:
        yield _safe_search(title_regex, context["title"]), True

    mtype = str(match.get("type", "") or "").lower()
    if mtype:
        yield mtype == context["type"], True

    dmin = match.get("duration_min")
    if dmin not in (None, ""):
        try:
            yield context["duration"] >= float(dmin), True
        except (TypeError, ValueError):
            yield False, True

    dmax = match.get("duration_max")
    if dmax not in (None, ""):
        try:
            yield context["duration"] <= float(dmax), True
        except (TypeError, ValueError):
            yield False, True


def rule_matches(rule, context):
    """True when ``rule`` matches ``context``.

    ``match_mode`` ``all`` (default) requires every declared criterion;
    ``any`` requires at least one. A rule with no criteria never matches
    (an empty rule is treated as inert rather than matching everything).
    """
    if not isinstance(rule, dict):
        return False
    results = [ok for ok, _present in _criterion_results(rule.get("match"), context)]
    if not results:
        return False
    mode = str(rule.get("match_mode", "all") or "all").lower()
    if mode == "any":
        return any(results)
    return all(results)


def _coerce_actions(actions):
    """Return a cleaned action dict limited to known keys and coerced types."""
    out = {}
    if not isinstance(actions, dict):
        return out
    source = dict(actions)
    # V15 called the named yt-dlp argument template ``filename_template``.
    # Keep old configs working while exposing the truthful action name; a
    # canonical key wins when a config contains both spellings.
    for legacy_key, canonical_key in _LEGACY_ACTION_ALIASES.items():
        if canonical_key not in source and legacy_key in source:
            source[canonical_key] = source[legacy_key]
    for key in ACTION_KEYS:
        if key not in source:
            continue
        val = source[key]
        if key in _STRING_ACTIONS:
            text = str(val or "").strip()
            if text:
                out[key] = text
        elif key == "priority":
            try:
                out[key] = int(val)
            except (TypeError, ValueError):
                continue
        elif key == "auto_start":
            out[key] = bool(val)
    return out


def evaluate(context, rules):
    """Evaluate ``rules`` against ``context``; return accumulated actions.

    Rules are processed in list order. Each matching enabled rule merges its
    coerced actions over the accumulator (last write wins). Evaluation stops
    after a matching rule that declares ``stop: true``.
    """
    result = {}
    matched = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        if not rule.get("enabled", True):
            continue
        if not rule_matches(rule, context):
            continue
        matched.append(str(rule.get("name", "") or ""))
        result.update(_coerce_actions(rule.get("actions")))
        if rule.get("stop"):
            break
    return {"actions": result, "matched": matched}


def normalize_rule(rule):
    """Validate/normalize a rule dict into a safe canonical form."""
    rule = dict(rule or {})
    match = rule.get("match") if isinstance(rule.get("match"), dict) else {}
    mtype = str(match.get("type", "") or "").lower()
    norm_match = {}
    for key in ("site", "url_regex", "uploader", "title_regex"):
        val = str(match.get(key, "") or "").strip()
        if val:
            norm_match[key] = val
    if mtype in _KNOWN_TYPES and mtype:
        norm_match["type"] = mtype
    for key in ("duration_min", "duration_max"):
        raw = match.get(key)
        if raw in (None, ""):
            continue
        try:
            norm_match[key] = float(raw)
        except (TypeError, ValueError):
            # Retain malformed criteria so matching fails closed instead of
            # silently dropping the condition from an ``all`` rule.
            norm_match[key] = str(raw)
    mode = str(rule.get("match_mode", "all") or "all").lower()
    if mode not in _MATCH_MODES:
        mode = "all"
    return {
        "name": str(rule.get("name", "") or "").strip(),
        "enabled": bool(rule.get("enabled", True)),
        "match": norm_match,
        "match_mode": mode,
        "actions": _coerce_actions(rule.get("actions")),
        "stop": bool(rule.get("stop", False)),
    }


def validate_rules(raw):
    """Validate the config/import schema for ``rules``.

    Runtime normalization is intentionally forgiving so one old hand-edited
    rule cannot stop a queue. Imports are a different boundary: rejecting a
    malformed ruleset with its exact path is safer than accepting it and then
    silently dropping it in :func:`load_rules`.
    """
    if not isinstance(raw, list):
        raise ValueError("rules must be a list")
    if len(raw) > MAX_RULES:
        raise ValueError(f"rules contains more than {MAX_RULES} entries")

    def _short_text(value, path, *, allow_empty=True):
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a string")
        if len(value) > MAX_RULE_TEXT_CHARS:
            raise ValueError(f"{path} is too long")
        if not allow_empty and not value.strip():
            raise ValueError(f"{path} must not be empty")
        if any(ord(char) < 32 for char in value):
            raise ValueError(f"{path} contains control characters")

    for index, rule in enumerate(raw):
        path = f"rules[{index}]"
        if not isinstance(rule, dict):
            raise ValueError(f"{path} must be an object")
        unknown = set(rule) - _ALLOWED_RULE_KEYS
        if unknown:
            raise ValueError(
                f"{path} has unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        if "name" in rule:
            name = rule["name"]
            if not isinstance(name, str) or len(name) > MAX_RULE_NAME_CHARS:
                raise ValueError(f"{path}.name must be a short string")
            if any(ord(char) < 32 for char in name):
                raise ValueError(f"{path}.name contains control characters")
        for key in ("enabled", "stop"):
            if key in rule and not isinstance(rule[key], bool):
                raise ValueError(f"{path}.{key} must be boolean")
        mode = rule.get("match_mode", "all")
        if not isinstance(mode, str) or mode.lower() not in _MATCH_MODES:
            raise ValueError(f"{path}.match_mode must be all or any")

        match = rule.get("match", {})
        if not isinstance(match, dict):
            raise ValueError(f"{path}.match must be an object")
        unknown = set(match) - _ALLOWED_MATCH_KEYS
        if unknown:
            raise ValueError(
                f"{path}.match has unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        for key in ("site", "url_regex", "uploader", "title_regex"):
            if key in match:
                _short_text(match[key], f"{path}.match.{key}")
                if key in {"url_regex", "title_regex"} and match[key]:
                    try:
                        re.compile(match[key])
                    except re.error as error:
                        raise ValueError(
                            f"{path}.match.{key} is invalid: {error}"
                        ) from error
        if "type" in match:
            mtype = match["type"]
            if not isinstance(mtype, str) or mtype.lower() not in _KNOWN_TYPES:
                raise ValueError(
                    f"{path}.match.type must be one of: "
                    + ", ".join(sorted(value for value in _KNOWN_TYPES if value))
                )
        for key in ("duration_min", "duration_max"):
            if key not in match:
                continue
            value = match[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{path}.match.{key} must be a number")

        actions = rule.get("actions", {})
        if not isinstance(actions, dict):
            raise ValueError(f"{path}.actions must be an object")
        unknown = set(actions) - _ALLOWED_ACTION_KEYS
        if unknown:
            raise ValueError(
                f"{path}.actions has unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        for key in _STRING_ACTIONS | {"filename_template"}:
            if key in actions:
                _short_text(actions[key], f"{path}.actions.{key}", allow_empty=False)
        if "priority" in actions:
            value = actions["priority"]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{path}.actions.priority must be an integer")
            if not -100 <= value <= 100:
                raise ValueError(f"{path}.actions.priority must be between -100 and 100")
        if "auto_start" in actions and not isinstance(actions["auto_start"], bool):
            raise ValueError(f"{path}.actions.auto_start must be boolean")
    return True


def load_rules(config):
    """Return the normalized rule list from a config dict (never raises)."""
    raw = (config or {}).get(CONFIG_KEY)
    if not isinstance(raw, list):
        return []
    return [normalize_rule(r) for r in raw if isinstance(r, dict)]


def apply_rules_to_job(job, config):
    """Evaluate config rules for a job dict and fold matching actions in.

    Returns a new dict; the original is not mutated. Only action keys that map
    onto job fields are written, and existing explicit overrides on the job are
    preserved (rules fill gaps, they do not clobber a caller-set value). The
    accumulated action set is recorded under ``_rule_actions`` for transparency.
    """
    rules = load_rules(config)
    if not rules:
        return dict(job)
    context = context_from_job(job)
    outcome = evaluate(context, rules)
    actions = outcome["actions"]
    result = dict(job)
    if not actions:
        return result

    def _fill(job_key, action_key):
        val = actions.get(action_key)
        if val in (None, ""):
            return
        if not str(result.get(job_key, "") or "").strip():
            result[job_key] = val

    _fill("output_dir", "output_dir")
    _fill("quality", "quality")
    _fill("arg_template", "arg_template")
    _fill("folder_template", "folder_template")
    _fill("file_template", "file_template")
    _fill("override_pp_preset", "pp_preset")
    _fill("proxy", "proxy")
    _fill("auth_profile_id", "auth_profile")
    if "priority" in actions and "priority" not in result:
        result["priority"] = actions["priority"]
    if "auto_start" in actions and "auto_start" not in result:
        result["auto_start"] = actions["auto_start"]
    result["_rule_actions"] = actions
    result["_rule_matched"] = outcome["matched"]
    return result
