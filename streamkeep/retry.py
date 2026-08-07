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


def failure_code_policy(code: object) -> tuple[str, bool, bool]:
    """Return ``(category, retryable, terminal)`` for a reason code."""
    return FAILURE_CODES.get(str(code or "").strip().casefold(), FAILURE_CODES["unknown"])


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

    # Checked ahead of the generic authentication and missing-media buckets:
    # these read as "forbidden" or "unavailable" but are permanent for this
    # URL, and retrying them forever is what poisons a queue.
    if (
        "not available in your country" in text
        or "not available in your location" in text
        or "geo-restricted" in text
        or "geo restricted" in text
        or "geoblocked" in text
        or "geo-blocked" in text
        or "blocked in your country" in text
    ):
        return decide("geo_blocked")
    if (
        "members-only" in text
        or "members only" in text
        or "subscriber-only" in text
        or "subscriber only" in text
        or "requires a channel subscription" in text
        or "join this channel" in text
    ):
        return decide("members_only")
    if (
        "has been deleted" in text
        or "removed by the uploader" in text
        or "video has been removed" in text
        or "account has been terminated" in text
        or "no longer exists" in text
    ):
        return decide("deleted")
    # A scheduled premiere or an announced stream is the canonical "come back
    # later" case. It previously fell through to ``unknown`` and was therefore
    # marked non-retryable, so the job stopped instead of waiting.
    if (
        "this live event will begin" in text
        or "premieres in" in text
        or "premiere will begin" in text
        or "scheduled to start" in text
        or "stream has not started" in text
        or "not started yet" in text
        or "waiting for the stream" in text
        or "is offline" in text
        or "channel is not live" in text
    ):
        return decide("scheduled_not_live", retry_after_seconds=retry_after)

    if (
        "no space left" in text
        or "disk full" in text
        or "not enough space" in text
        or "insufficient disk" in text
        or "low disk" in text
        or "quota exceeded" in text
    ):
        return decide("disk_full")
    if (
        "permission denied" in text
        or "access is denied" in text
        or "read-only file system" in text
        or "operation not permitted" in text
        or "winerror 5" in text
    ):
        return decide("permission_denied")
    if (
        "drm" in text
        or "content protection" in text
        or "widevine" in text
        or "fairplay" in text
        or "playready" in text
        or "encrypted media extensions" in text
    ):
        return decide("drm_protected")
    if (
        statuses.intersection({401, 403})
        or "unauthorized" in text
        or "forbidden" in text
        or "authentication required" in text
        or "login required" in text
        or "sign in to confirm" in text
        or "cookies are required" in text
        or "members-only" in text
        or "private video" in text
    ):
        return decide("login_required")
    if (
        statuses.intersection({404, 410})
        or "media is unavailable" in text
        or "video unavailable" in text
        or "has been deleted" in text
        or "removed by the uploader" in text
        or "not found" in text
        or "media is missing" in text
        or "source is missing" in text
        or "recording is missing" in text
        or "no longer available" in text
    ):
        return decide("not_found")
    if (
        "invalid configuration" in text
        or "invalid config" in text
        or "invalid url" in text
        or "unsupported url" in text
        or "no extractor found" in text
        or "no suitable extractor" in text
        or "requested format is not available" in text
        or "unsupported template" in text
        or "unknown option" in text
        or "unrecognized option" in text
    ):
        return decide("invalid_config")
    if 429 in statuses or "too many requests" in text or "rate limit" in text:
        return decide("throttled", retry_after_seconds=retry_after)
    if any(status in {500, 502, 503, 504} for status in statuses):
        return decide("server_error", retry_after_seconds=retry_after)
    if (
        "internal server error" in text
        or "bad gateway" in text
        or "service unavailable" in text
        or "gateway timeout" in text
    ):
        return decide("server_error", retry_after_seconds=retry_after)
    if (
        "timed out" in text
        or "timeout" in text
        or "deadline exceeded" in text
        or "operation timed out" in text
    ):
        return decide("timeout", retry_after_seconds=retry_after)
    if (
        "temporary failure" in text
        or "temporary network failure" in text
        or "temporarily unavailable" in text
        or "connection reset" in text
        or "connection refused" in text
        or "connection aborted" in text
        or "remote disconnected" in text
        or "network is unreachable" in text
        or "name resolution" in text
        or "getaddrinfo failed" in text
        or "network error" in text
        or "could not resolve host" in text
        or "failed to connect" in text
        or "broken pipe" in text
        or "curl: (6)" in text
        or "curl: (7)" in text
        or "curl: (18)" in text
        or "curl: (28)" in text
        or "curl: (35)" in text
        or "curl: (52)" in text
        or "curl: (56)" in text
    ):
        return decide("network_unreachable", retry_after_seconds=retry_after)
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
