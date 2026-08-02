"""URL-pattern download profiles used by Smart Mode (V16).

Smart Mode is deliberately a small, data-only resolver.  Profiles contain
ordered URL globs and a bounded set of per-job overrides; they never contain
commands or arbitrary yt-dlp arguments.  The same pure functions are used by
the desktop queue, the CLI, and the local REST service so a pasted URL has
the same behavior everywhere.
"""

from __future__ import annotations

import fnmatch
import re
from urllib.parse import urlsplit


CONFIG_KEY = "smart_profiles"
MODE_KEY = "smart_mode"
MAX_PROFILES = 128
MAX_PATTERNS = 32
MAX_PATTERN_CHARS = 2048
MAX_NAME_CHARS = 128

OVERRIDE_KEYS = (
    "output_dir",
    "folder_template",
    "file_template",
    "quality",
    "ytdlp_template_name",
    "proxy",
    "auth_profile_id",
    "pp_preset",
    "priority",
    "auto_start",
)
_STRING_OVERRIDES = frozenset(OVERRIDE_KEYS) - {"priority", "auto_start"}
_ALLOWED_PROFILE_KEYS = frozenset({
    "name", "enabled", "patterns", "url_patterns", "overrides",
})
_ALLOWED_OVERRIDE_KEYS = frozenset(OVERRIDE_KEYS) | {"arg_template"}
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _text(value, *, limit=4096):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > limit or _CONTROL_CHARS.search(value):
        return ""
    return value


def normalize_pattern(value):
    """Return a safe case-insensitive URL pattern, or ``""``.

    Patterns are shell-free ``fnmatch`` globs.  A ``re:`` prefix is accepted
    for users who need a narrow regular expression, but malformed expressions
    are discarded and therefore fail closed.
    """
    pattern = _text(value, limit=MAX_PATTERN_CHARS)
    if not pattern:
        return ""
    pattern = pattern.lower()
    if pattern.startswith("re:"):
        expression = pattern[3:].strip()
        if not expression or len(expression) > 1024:
            return ""
        try:
            re.compile(expression, re.IGNORECASE)
        except re.error:
            return ""
        return "re:" + expression
    return pattern.rstrip("/") or pattern


def _normalize_patterns(raw):
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        return []
    patterns = []
    for value in raw[:MAX_PATTERNS]:
        pattern = normalize_pattern(value)
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    return patterns


def _normalize_overrides(raw, top_level):
    values = {}
    if isinstance(raw, dict):
        values.update(raw)
    # Accepting these at profile level keeps hand-edited config concise while
    # the canonical representation remains nested under ``overrides``.
    for key in OVERRIDE_KEYS:
        if key not in values and key in top_level:
            values[key] = top_level[key]
    if "ytdlp_template_name" not in values and "arg_template" in values:
        values["ytdlp_template_name"] = values["arg_template"]

    result = {}
    for key in OVERRIDE_KEYS:
        if key not in values:
            continue
        value = values[key]
        if key in _STRING_OVERRIDES:
            value = _text(value)
            if value:
                result[key] = value
        elif key == "priority":
            try:
                result[key] = max(-100, min(100, int(value)))
            except (TypeError, ValueError):
                continue
        elif key == "auto_start" and isinstance(value, bool):
            result[key] = value
    return result


def normalize_profile(raw, index=0):
    """Normalize one profile into a JSON-safe canonical dict.

    Invalid entries return ``None``.  Normalization never raises for user
    config, which keeps a single damaged profile from stopping all downloads.
    """
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("name"), limit=MAX_NAME_CHARS)
    if not name:
        name = f"Profile {int(index) + 1}"
    patterns = _normalize_patterns(
        raw.get("patterns", raw.get("url_patterns", []))
    )
    if not patterns:
        return None
    return {
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "patterns": patterns,
        "overrides": _normalize_overrides(raw.get("overrides"), raw),
    }


def normalize_profiles(raw):
    """Return valid profiles in their configured order."""
    if not isinstance(raw, list):
        return []
    profiles = []
    for index, value in enumerate(raw[:MAX_PROFILES]):
        profile = normalize_profile(value, index)
        if profile is not None:
            profiles.append(profile)
    return profiles


def validate_profiles(raw):
    """Validate the import/config schema for ``smart_profiles``.

    This is stricter than runtime normalization: malformed imported config is
    rejected with a useful path instead of being silently rewritten.
    """
    if not isinstance(raw, list):
        raise ValueError("smart_profiles must be a list")
    if len(raw) > MAX_PROFILES:
        raise ValueError(f"smart_profiles contains more than {MAX_PROFILES} entries")
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ValueError(f"smart_profiles[{index}] must be an object")
        unknown = set(value) - _ALLOWED_PROFILE_KEYS
        # Top-level override aliases are intentionally accepted for backwards
        # compatible hand-edited profiles, but only when they are known keys.
        unknown -= set(OVERRIDE_KEYS)
        if unknown:
            raise ValueError(
                f"smart_profiles[{index}] has unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        name = value.get("name", "")
        if not isinstance(name, str) or not _text(name, limit=MAX_NAME_CHARS):
            raise ValueError(f"smart_profiles[{index}].name must be a short string")
        if not isinstance(value.get("enabled", True), bool):
            raise ValueError(f"smart_profiles[{index}].enabled must be boolean")
        patterns = value.get("patterns", value.get("url_patterns"))
        if not isinstance(patterns, list) or not patterns:
            raise ValueError(f"smart_profiles[{index}].patterns must be a non-empty list")
        if len(patterns) > MAX_PATTERNS:
            raise ValueError(
                f"smart_profiles[{index}].patterns contains more than {MAX_PATTERNS} entries"
            )
        for pattern_index, pattern in enumerate(patterns):
            if not isinstance(pattern, str) or not normalize_pattern(pattern):
                raise ValueError(
                    f"smart_profiles[{index}].patterns[{pattern_index}] is invalid"
                )
        overrides = value.get("overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError(f"smart_profiles[{index}].overrides must be an object")
        unknown = set(overrides) - _ALLOWED_OVERRIDE_KEYS
        if unknown:
            raise ValueError(
                f"smart_profiles[{index}].overrides has unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        for key, override in overrides.items():
            if key in _STRING_OVERRIDES or key == "arg_template":
                if not isinstance(override, str) or not _text(override):
                    raise ValueError(
                        f"smart_profiles[{index}].overrides.{key} must be a string"
                    )
            elif key == "priority":
                if isinstance(override, bool) or not isinstance(override, int):
                    raise ValueError(
                        f"smart_profiles[{index}].overrides.priority must be an integer"
                    )
            elif key == "auto_start" and not isinstance(override, bool):
                raise ValueError(
                    f"smart_profiles[{index}].overrides.auto_start must be boolean"
                )
    return True


def load_profiles(config):
    return normalize_profiles((config or {}).get(CONFIG_KEY))


def smart_mode_enabled(config):
    return bool((config or {}).get(MODE_KEY, False))


def _url_candidates(url):
    raw = _text(url, limit=8192).lower()
    if not raw:
        return ()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return (raw,)
    host = (parsed.hostname or "").lower()
    if not host:
        return (raw,)
    path = parsed.path or "/"
    host_path = host + path
    candidates = [raw.rstrip("/"), raw, host, host_path]
    if host.startswith("www."):
        bare = host[4:]
        candidates.extend((bare, bare + path))
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def profile_matches(profile, url):
    """Return whether a normalized/raw profile matches ``url``."""
    normalized = normalize_profile(profile)
    if normalized is None or not normalized["enabled"]:
        return False
    candidates = _url_candidates(url)
    if not candidates:
        return False
    raw = candidates[0]
    for pattern in normalized["patterns"]:
        if pattern.startswith("re:"):
            try:
                if re.search(pattern[3:], raw, re.IGNORECASE):
                    return True
            except re.error:
                continue
            continue
        if any(fnmatch.fnmatchcase(candidate, pattern) for candidate in candidates):
            return True
    return False


def resolve_profile(url, config):
    """Return the first enabled matching profile, or ``None``."""
    if not smart_mode_enabled(config):
        return None
    for profile in load_profiles(config):
        if profile_matches(profile, url):
            return profile
    return None


def _has_value(job, key):
    value = job.get(key)
    return value is not None and str(value).strip() != ""


def apply_smart_profile_to_job(job, config):
    """Fill missing job fields from the first matching Smart Mode profile."""
    result = dict(job or {})
    url = str(
        result.get("webpage_url") or result.get("url") or ""
    ).strip()
    profile = resolve_profile(url, config)
    if profile is None:
        return result
    overrides = profile["overrides"]
    mapping = {
        "output_dir": "output_dir",
        "folder_template": "folder_template",
        "file_template": "file_template",
        "quality": "quality",
        "ytdlp_template_name": "arg_template",
        "proxy": "proxy",
        "auth_profile_id": "auth_profile_id",
        "pp_preset": "override_pp_preset",
        "priority": "priority",
        "auto_start": "auto_start",
    }
    for source, target in mapping.items():
        if source in overrides and not _has_value(result, target):
            result[target] = overrides[source]
    result["_smart_profile"] = profile["name"]
    return result


def quality_index(qualities, preference):
    """Choose a quality index for a profile preference.

    Quality lists are normally sorted highest-first.  Exact name/resolution
    matches win; numeric preferences choose the closest representation at or
    below the requested height, then fall back to the best entry.
    """
    if not qualities:
        return -1
    pref = str(preference or "best").strip().lower()
    if pref in {"", "best", "source", "highest"}:
        return 0
    if pref == "lowest":
        return len(qualities) - 1
    if pref in {"audio", "audio-only", "audio_only"}:
        for index, quality in enumerate(qualities):
            if "audio" in str(getattr(quality, "name", "")).lower() or str(
                getattr(quality, "resolution", "")
            ).lower() == "audio":
                return index
    for index, quality in enumerate(qualities):
        name = str(getattr(quality, "name", "") or "").lower()
        resolution = str(getattr(quality, "resolution", "") or "").lower()
        if pref in name or pref in resolution:
            return index
    match = re.search(r"(\d{3,4})", pref)
    if match:
        requested = int(match.group(1))
        options = []
        for index, quality in enumerate(qualities):
            value = " ".join((
                str(getattr(quality, "resolution", "") or ""),
                str(getattr(quality, "name", "") or ""),
            ))
            found = re.search(r"(\d{3,4})", value)
            if found:
                height = int(found.group(1))
                options.append((index, height))
        below = [entry for entry in options if entry[1] <= requested]
        if below:
            return max(below, key=lambda entry: entry[1])[0]
        if options:
            return min(options, key=lambda entry: abs(entry[1] - requested))[0]
    return 0


# Short aliases make the resolver convenient for call sites and downstream
# integrations without exposing implementation details.
apply_profile_to_job = apply_smart_profile_to_job
choose_quality_index = quality_index

