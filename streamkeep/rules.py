"""Ordered rules engine (V15, Packagizer-class).

A list of user-defined rules is evaluated top-to-bottom against a *context*
describing a pending download (site, uploader, title, duration, type, url).
Every enabled rule whose match criteria are satisfied contributes its actions
to an accumulated result; later rules override earlier ones for the same
action key, and a rule with ``stop: true`` halts evaluation once it matches.

Actions steer how the job is handled:

    output_dir          — destination folder override
    filename_template   — yt-dlp / structured argv template name
    pp_preset           — named post-processing preset
    quality             — quality preference ("best"/"1080p"/"audio"/...)
    proxy               — per-job proxy URL
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
    "filename_template",
    "pp_preset",
    "quality",
    "proxy",
    "priority",
    "auto_start",
)

_STRING_ACTIONS = frozenset({
    "output_dir", "filename_template", "pp_preset", "quality", "proxy",
})

_MATCH_MODES = ("all", "any")
_KNOWN_TYPES = frozenset({"video", "audio", "live", "playlist", "image", ""})


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


def _criterion_results(match, context):
    """Yield (satisfied, present) for each declared match criterion."""
    if not isinstance(match, dict):
        return

    site = str(match.get("site", "") or "")
    if site:
        yield site.lower() in context["site"].lower(), True

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
            pass

    dmax = match.get("duration_max")
    if dmax not in (None, ""):
        try:
            yield context["duration"] <= float(dmax), True
        except (TypeError, ValueError):
            pass


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
    for key in ACTION_KEYS:
        if key not in actions:
            continue
        val = actions[key]
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
            continue
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
    _fill("arg_template", "filename_template")
    _fill("override_pp_preset", "pp_preset")
    _fill("proxy", "proxy")
    if "priority" in actions and "priority" not in result:
        result["priority"] = actions["priority"]
    if "auto_start" in actions and "auto_start" not in result:
        result["auto_start"] = actions["auto_start"]
    result["_rule_actions"] = actions
    result["_rule_matched"] = outcome["matched"]
    return result
