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


@dataclass(frozen=True)
class RetryDecision:
    """One normalized failure classification."""

    category: str
    retryable: bool
    retry_after_seconds: int
    reason: str


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

    if (
        "no space left" in text
        or "disk full" in text
        or "not enough space" in text
        or "insufficient disk" in text
        or "low disk" in text
        or "quota exceeded" in text
    ):
        return RetryDecision("disk", False, 0, reason)
    if (
        "permission denied" in text
        or "access is denied" in text
        or "read-only file system" in text
        or "operation not permitted" in text
        or "winerror 5" in text
    ):
        return RetryDecision("permission", False, 0, reason)
    if (
        "drm" in text
        or "content protection" in text
        or "widevine" in text
        or "fairplay" in text
        or "playready" in text
        or "encrypted media extensions" in text
    ):
        return RetryDecision("drm", False, 0, reason)
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
        return RetryDecision("authentication", False, 0, reason)
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
        return RetryDecision("missing_media", False, 0, reason)
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
        return RetryDecision("invalid_config", False, 0, reason)
    if 429 in statuses or "too many requests" in text or "rate limit" in text:
        return RetryDecision("rate_limit", True, retry_after, reason)
    if any(status in {500, 502, 503, 504} for status in statuses):
        return RetryDecision("server", True, retry_after, reason)
    if (
        "internal server error" in text
        or "bad gateway" in text
        or "service unavailable" in text
        or "gateway timeout" in text
    ):
        return RetryDecision("server", True, retry_after, reason)
    if (
        "timed out" in text
        or "timeout" in text
        or "deadline exceeded" in text
        or "operation timed out" in text
    ):
        return RetryDecision("timeout", True, retry_after, reason)
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
        return RetryDecision("network", True, retry_after, reason)
    return RetryDecision("unknown", False, 0, reason)


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
