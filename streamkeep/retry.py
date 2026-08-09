"""Persistent retry policy for failed media jobs.

The policy is deliberately independent from Qt and SQLite.  Callers can pass a
clock value, making classification, delay, and ``Retry-After`` handling
deterministic in tests and across process restarts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .metadata import scrub_public_text

BASE_DELAY_SECONDS = 60
MAX_DELAY_SECONDS = 6 * 60 * 60
MAX_RETRY_AFTER_SECONDS = 7 * 24 * 60 * 60
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_WINDOW_SECONDS = 15 * 60
CIRCUIT_OPEN_SECONDS = 15 * 60

_URL_RE = re.compile(
    r"(?i)\b(?:https?|rtmps?|rtsp|srt|ftp)://[^\s<>'\"]+"
)
_RETRY_AFTER_RE = re.compile(
    r"(?im)\bretry[- ]after\s*(?::|=|\bis\b)?\s*"
    r"([^\r\n]+)"
)
_HTTP_STATUS_RE = re.compile(
    r"(?i)(?:http(?:\s+error)?|status(?:\s+code)?|response)"
    r"\D{0,12}([1-5]\d\d)\b"
)


def _remediation_text(source: str) -> str:
    """Mark operator guidance for extraction into the shared i18n catalog."""
    return source


_FAILURE_REMEDIATIONS = {
    "disk": {
        "message": _remediation_text(
            "Free space in the archive destination, then retry the job."
        ),
        "action": _remediation_text("Open Storage settings"),
        "target": "storage",
    },
    "permission": {
        "message": _remediation_text(
            "Choose a writable archive destination or fix its permissions, then retry."
        ),
        "action": _remediation_text("Open Storage settings"),
        "target": "storage",
    },
    "drm": {
        "message": _remediation_text(
            "This source is protected; use an allowed DRM-free source or skip the job."
        ),
        "action": "",
        "target": "",
    },
    "authentication": {
        "message": _remediation_text(
            "Refresh the saved cookies or credentials, then retry the job."
        ),
        "action": _remediation_text("Open Credentials settings"),
        "target": "settings.credentials",
    },
    "missing_media": {
        "message": _remediation_text(
            "Confirm the source is still available; removed media cannot be retried."
        ),
        "action": "",
        "target": "",
    },
    "invalid_config": {
        "message": _remediation_text(
            "Review the download and source settings, then retry the job."
        ),
        "action": _remediation_text("Open Download settings"),
        "target": "settings.downloads",
    },
    "rate_limit": {
        "message": _remediation_text(
            "Wait for the service rate limit to clear, then retry the job."
        ),
        "action": "",
        "target": "",
    },
    "scheduled": {
        "message": _remediation_text(
            "The broadcast has not started yet; the job retries itself once it does."
        ),
        "action": _remediation_text("Open Monitor"),
        "target": "monitor",
    },
    "server": {
        "message": _remediation_text(
            "Wait for the source service to recover, then retry the job."
        ),
        "action": "",
        "target": "",
    },
    "timeout": {
        "message": _remediation_text(
            "Check the connection and retry; the source may need more time to respond."
        ),
        "action": "",
        "target": "",
    },
    "network": {
        "message": _remediation_text(
            "Check the network connection or proxy, then retry the job."
        ),
        "action": _remediation_text("Open Network settings"),
        "target": "settings.network",
    },
    "unknown": {
        "message": _remediation_text(
            "No safe remediation is known; inspect the reason before retrying."
        ),
        "action": "",
        "target": "",
    },
}

_YOUTUBE_CAPABILITY_HINTS = (
    "yt-dlp", "yt_dlp", "youtube", "sabr", "po-token", "po_token",
    "deno", "javascript runtime", "js runtime",
)


def failure_remediation(category: object, *, reason: object = "") -> dict[str, str]:
    """Return bounded, URL/path-free operator guidance for a failure category."""
    normalized = str(category or "unknown").strip().casefold()
    if normalized not in _FAILURE_REMEDIATIONS:
        normalized = "unknown"
    source = _FAILURE_REMEDIATIONS[normalized]
    if (
        normalized == "invalid_config"
        and any(
            marker in str(reason or "").casefold()
            for marker in _YOUTUBE_CAPABILITY_HINTS
        )
    ):
        source = {
            "message": _remediation_text(
                "Check YouTube health and its required runtime, then retry the job."
            ),
            "action": _remediation_text("Open YouTube health in Settings"),
            "target": "settings.youtube",
        }
    return {
        "message": str(source["message"]),
        "action": str(source["action"]),
        "target": str(source["target"]),
    }


# Stable machine-readable reason codes (V154). The broad ``category`` drives
# operator guidance; the code names the specific condition, which is what
# separates "come back later" from "this will never work". Codes are API
# surface — the local REST failure view exposes them — so they are appended
# to, never renamed or repurposed.
#
# ``terminal`` marks a condition no amount of retrying or operator action can
# change for this URL. It is deliberately narrower than "not retryable": a
# members-only video becomes downloadable once the operator supplies a
# subscribed session, so that is intervention, not terminal.
FAILURE_CODES = {
    # code: (category, retryable, terminal)
    "disk_full": ("disk", False, False),
    "permission_denied": ("permission", False, False),
    "drm_protected": ("drm", False, True),
    "bot_check": ("authentication", False, False),
    "geo_blocked": ("authentication", False, True),
    "members_only": ("authentication", False, False),
    "login_required": ("authentication", False, False),
    "deleted": ("missing_media", False, True),
    "not_found": ("missing_media", False, False),
    "invalid_config": ("invalid_config", False, False),
    "scheduled_not_live": ("scheduled", True, False),
    "throttled": ("rate_limit", True, False),
    "server_error": ("server", True, False),
    "timeout": ("timeout", True, False),
    "network_unreachable": ("network", True, False),
    "unknown": ("unknown", False, False),
}

# The wording and status combinations emitted by remote services change more
# often than the retry state machine. Keep those signals in one ordered data
# table so a new bot wall or a renamed availability message is an edit to the
# table, not another branch hidden in ``classify_failure``. Earlier rows win:
# a deleted video with a Retry-After is still gone, and a members-only wall is
# intervention rather than a generic 403/login failure.
FAILURE_SIGNAL_RULES = (
    {
        "code": "geo_blocked",
        "signals": (
            "not available in your country", "not available in your location",
            "geo-restricted", "geo restricted", "geoblocked", "geo-blocked",
            "blocked in your country",
        ),
    },
    {
        "code": "members_only",
        "signals": (
            "members-only", "members only", "subscriber-only",
            "subscriber only", "requires a channel subscription",
            "join this channel",
        ),
    },
    {
        "code": "deleted",
        "signals": (
            "has been deleted", "removed by the uploader",
            "video has been removed", "account has been terminated",
            "no longer exists",
        ),
    },
    {
        "code": "scheduled_not_live",
        "signals": (
            "this live event will begin", "premieres in", "premiere will begin",
            "scheduled to start", "stream has not started", "not started yet",
            "waiting for the stream", "is offline", "channel is not live",
        ),
        "honor_retry_after": True,
    },
    {
        "code": "disk_full",
        "signals": (
            "no space left", "disk full", "not enough space",
            "insufficient disk", "low disk", "quota exceeded",
        ),
    },
    {
        "code": "permission_denied",
        "signals": (
            "permission denied", "access is denied", "read-only file system",
            "operation not permitted", "winerror 5",
        ),
    },
    {
        "code": "drm_protected",
        "signals": (
            "drm", "content protection", "widevine", "fairplay", "playready",
            "encrypted media extensions",
        ),
    },
    {
        "code": "bot_check",
        "signals": (
            "captcha", "verify you are human", "are you a human",
            "bot detected", "bot check", "automated queries",
            "automated request", "unusual traffic", "cloudflare",
            "cf-chl-", "challenge required", "anti-bot", "antibot",
            "turnstile",
        ),
    },
    {
        "code": "login_required",
        "statuses": (401, 403),
        "signals": (
            "unauthorized", "forbidden", "authentication required",
            "login required", "sign in to confirm", "cookies are required",
            "private video",
        ),
    },
    {
        "code": "not_found",
        "statuses": (404, 410),
        "signals": (
            "media is unavailable", "video unavailable", "not found",
            "media is missing", "source is missing", "recording is missing",
            "no longer available",
        ),
    },
    {
        "code": "invalid_config",
        "signals": (
            "invalid configuration", "invalid config", "invalid url",
            "unsupported url", "no extractor found", "no suitable extractor",
            "requested format is not available", "unsupported template",
            "unknown option", "unrecognized option",
        ),
    },
    {
        "code": "throttled",
        "statuses": (429,),
        "signals": ("too many requests", "rate limit"),
        "honor_retry_after": True,
    },
    {
        "code": "server_error",
        "statuses": (500, 502, 503, 504),
        "signals": (
            "internal server error", "bad gateway", "service unavailable",
            "gateway timeout",
        ),
        "honor_retry_after": True,
    },
    {
        "code": "timeout",
        "signals": (
            "timed out", "timeout", "deadline exceeded", "operation timed out",
        ),
        "honor_retry_after": True,
    },
    {
        "code": "network_unreachable",
        "signals": (
            "temporary failure", "temporary network failure",
            "temporarily unavailable", "connection reset", "connection refused",
            "connection aborted", "remote disconnected", "network is unreachable",
            "name resolution", "getaddrinfo failed", "network error",
            "could not resolve host", "failed to connect", "broken pipe",
            "curl: (6)", "curl: (7)", "curl: (18)", "curl: (28)",
            "curl: (35)", "curl: (52)", "curl: (56)",
        ),
        "honor_retry_after": True,
    },
)

# Human/API vocabulary for the cross-cutting failure classes in V201. The
# original reason codes remain stable for existing queue/API consumers.
FAILURE_CLASSIFICATIONS = {
    "bot_check": "bot-check",
    "throttled": "rate-limited",
    "geo_blocked": "geo-blocked",
    "members_only": "members-only",
    "deleted": "genuinely-gone",
    "not_found": "genuinely-gone",
    "server_error": "server-error",
    "network_unreachable": "network-unreachable",
    "scheduled_not_live": "scheduled",
    "disk_full": "disk-full",
    "permission_denied": "permission-denied",
    "drm_protected": "drm-protected",
    "login_required": "login-required",
    "invalid_config": "invalid-config",
    "timeout": "timeout",
    "unknown": "unknown",
}

# These failures should cool a host even when the individual job is not safe
# to retry automatically. In particular, repeatedly probing a bot wall or a
# known-gone URL makes the host look like a queue storm and hides the standing
# condition from the operator.
HOST_BACKOFF_CODES = frozenset({
    "bot_check", "throttled", "server_error", "timeout", "network_unreachable",
})


def failure_code_policy(code: object) -> tuple[str, bool, bool]:
    """Return ``(category, retryable, terminal)`` for a reason code."""
    return FAILURE_CODES.get(str(code or "").strip().casefold(), FAILURE_CODES["unknown"])


def failure_classification(code: object) -> str:
    """Return the stable operator vocabulary for a reason code."""
    normalized = str(code or "").strip().casefold()
    return FAILURE_CLASSIFICATIONS.get(normalized, normalized or "unknown")


def should_backoff_host(code: object) -> bool:
    """Whether a failure should cool the source host in the governor."""
    return str(code or "").strip().casefold() in HOST_BACKOFF_CODES


def apply_host_backoff(url: object, decision: "RetryDecision") -> bool:
    """Apply a classified host cooldown without coupling the DB to Qt policy."""
    if not should_backoff_host(decision.code):
        return False
    from .governor import record_throttle

    record_throttle(
        url,
        retry_after=decision.retry_after_seconds,
        reason=f"{decision.classification} failure",
        classification=decision.classification,
    )
    return True


@dataclass(frozen=True)
class RetryDecision:
    """One normalized failure classification."""

    category: str
    retryable: bool
    retry_after_seconds: int
    reason: str
    # Stable identifier for the specific condition. ``category`` stays as the
    # coarse bucket the remediation table is keyed by.
    code: str = "unknown"
    # True only when no retry and no operator action can make this URL work.
    terminal: bool = False

    @property
    def classification(self) -> str:
        """Return the V201 class without changing the legacy reason code."""
        return failure_classification(self.code)


def sanitize_failure_reason(error: object, *, limit: int = 1000) -> str:
    """Return a bounded operator-facing reason without URLs or credentials."""
    text = scrub_public_text(error)
    text = _URL_RE.sub("[URL removed]", text)
    text = re.sub(
        r"(?i)--(?:cookies(?:-from-browser)?|username|password|"
        r"http-header|add-header|proxy)\s+\S+",
        "***REDACTED***",
        text,
    )
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", text)
    text = " ".join(text.split())
    return (text or "Unknown failure")[:max(1, int(limit))]


def parse_retry_after(value: object, *, now: float | None = None) -> int:
    """Parse a Retry-After delay or HTTP date, returning whole seconds."""
    text = str(value or "").strip()
    if not text:
        return 0
    match = _RETRY_AFTER_RE.search(text)
    candidate = match.group(1).strip() if match else text
    candidate = candidate.strip("\"'")
    seconds_match = re.match(r"^(\d+)(?:\s*(?:s|sec|secs|seconds))?\b", candidate, re.I)
    if seconds_match:
        return min(
            MAX_RETRY_AFTER_SECONDS,
            max(0, int(seconds_match.group(1))),
        )
    try:
        target = parsedate_to_datetime(candidate)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = (
            datetime.now(timezone.utc).timestamp()
            if now is None
            else float(now)
        )
        return min(
            MAX_RETRY_AFTER_SECONDS,
            max(0, int(target.timestamp() - current + 0.999)),
        )
    except (TypeError, ValueError, OverflowError):
        return 0


def classify_failure(error: object, *, now: float | None = None) -> RetryDecision:
    """Classify a worker error into retry or intervention policy."""
    raw = str(error or "")
    text = raw.casefold()
    reason = sanitize_failure_reason(raw)
    retry_after = parse_retry_after(raw, now=now)
    statuses = {int(value) for value in _HTTP_STATUS_RE.findall(raw)}

    def decide(code, *, retry_after_seconds=0):
        category, retryable, terminal = failure_code_policy(code)
        return RetryDecision(
            category, retryable,
            retry_after_seconds if retryable else 0,
            reason, code, terminal,
        )

    for rule in FAILURE_SIGNAL_RULES:
        statuses_match = bool(statuses.intersection(rule.get("statuses", ())))
        signal_match = any(signal in text for signal in rule.get("signals", ()))
        if not statuses_match and not signal_match:
            continue
        delay = retry_after if rule.get("honor_retry_after") else 0
        return decide(rule["code"], retry_after_seconds=delay)
    return decide("unknown")


def retry_delay_seconds(
    attempt: int,
    source_key: str,
    *,
    retry_after_seconds: int = 0,
    base_seconds: int = BASE_DELAY_SECONDS,
    cap_seconds: int = MAX_DELAY_SECONDS,
) -> int:
    """Return capped exponential backoff with stable per-source jitter."""
    normalized_attempt = max(1, int(attempt or 1))
    base = max(1, int(base_seconds))
    cap = max(base, int(cap_seconds))
    exponential = min(cap, base * (2 ** min(30, normalized_attempt - 1)))
    digest = hashlib.sha256(
        f"{source_key}:{normalized_attempt}".encode("utf-8", "replace")
    ).digest()
    fraction = int.from_bytes(digest[:8], "big") / float((1 << 64) - 1)
    jittered = max(1, int(round(exponential * (0.8 + (0.4 * fraction)))))
    return max(jittered, max(0, int(retry_after_seconds or 0)))


def retry_source(url: object, platform: object = "", source_id: object = "") -> tuple[str, str]:
    """Return an opaque circuit key and a non-sensitive operator label."""
    platform_text = str(platform or "").strip()
    source_text = str(source_id or "").strip()
    host = ""
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower().strip(".")
    except ValueError:
        host = ""
    identity = host or source_text or platform_text.casefold() or "unknown"
    namespace = host or platform_text.casefold() or "source"
    key = hashlib.sha256(
        f"{namespace}\0{identity}".encode("utf-8", "replace")
    ).hexdigest()
    label = sanitize_failure_reason(platform_text or host or "Unknown source", limit=120)
    return key, label


def utc_iso(timestamp: float) -> str:
    """Format a UTC epoch for lexically sortable SQLite storage."""
    return (
        datetime.fromtimestamp(float(timestamp), timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def iso_timestamp(value: object) -> float:
    """Parse a stored ISO timestamp, returning zero for invalid values."""
    try:
        return datetime.fromisoformat(
            str(value or "").replace("Z", "+00:00")
        ).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0
