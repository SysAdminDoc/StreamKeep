"""Live credential and cookie-profile validation (non-downloading probes).

Each platform probe makes at most one lightweight, read-only API request and
returns a structured :class:`ProbeResult`. Probes record only *redacted*
metadata (status, timing, counts) — never the token, cookie values, or any
signed URL. Cookie-profile validation is fully local (parses the Netscape
file); no request is made.

Statuses map to the roadmap acceptance vocabulary: valid, expired/revoked,
insufficient scope, rate-limited, unsupported, network failure — plus
``no_credential`` and ``cancelled`` for the empty/aborted cases.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass, field

from . import accounts

# ── Status vocabulary ───────────────────────────────────────────────
VALID = "valid"
INVALID = "invalid"
EXPIRED = "expired"
INSUFFICIENT_SCOPE = "insufficient_scope"
RATE_LIMITED = "rate_limited"
UNSUPPORTED = "unsupported"
NETWORK_ERROR = "network_error"
NO_CREDENTIAL = "no_credential"
CANCELLED = "cancelled"

_LABELS = {
    VALID: "Valid",
    INVALID: "Invalid credential",
    EXPIRED: "Expired or revoked",
    INSUFFICIENT_SCOPE: "Insufficient scope / API not enabled",
    RATE_LIMITED: "Rate limited",
    UNSUPPORTED: "Validation not supported",
    NETWORK_ERROR: "Network error",
    NO_CREDENTIAL: "No credential stored",
    CANCELLED: "Cancelled",
}

# Tone hint for UI status rendering (maps to update_accessible_status tones).
_TONES = {
    VALID: "success",
    NO_CREDENTIAL: "info",
    UNSUPPORTED: "info",
    RATE_LIMITED: "warning",
    NETWORK_ERROR: "warning",
    CANCELLED: "info",
}


@dataclass(frozen=True)
class ProbeResult:
    platform: str
    status: str
    detail: str = ""
    http_status: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == VALID

    @property
    def label(self) -> str:
        return _LABELS.get(self.status, self.status)

    @property
    def tone(self) -> str:
        return _TONES.get(self.status, "error")

    def as_dict(self) -> dict:
        """Redacted, serializable summary — safe for logs/diagnostics."""
        return {
            "platform": self.platform,
            "status": self.status,
            "detail": self.detail,
            "http_status": self.http_status,
            "metadata": dict(self.metadata),
        }


def _fetch(url, headers=None, timeout=15):
    """Return ``(http_status:int, body:str)`` for a read-only GET.

    Uses the shared curl path (proxy/cookies/redirect-safe). The status code
    is captured with curl's ``-w %{http_code}`` sentinel so it is available
    even for non-2xx responses. Returns ``(-1, "")`` on transport failure.
    """
    from .http import _build_curl_cmd, run_capture_interruptible

    try:
        cmd = _build_curl_cmd(url, headers=headers, timeout=timeout)
    except Exception:
        return -1, ""
    sentinel = "\n__SK_HTTP_CODE__:"
    cmd[1:1] = ["-w", f"{sentinel}%{{http_code}}"]
    result = run_capture_interruptible(cmd, timeout=timeout + 2)
    body = result.stdout or ""
    if result.returncode != 0 and not body:
        return -1, ""
    status = 0
    idx = body.rfind(sentinel)
    if idx != -1:
        tail = body[idx + len(sentinel):].strip()
        body = body[:idx]
        if tail.isdigit():
            status = int(tail)
    return status, body


# ── Per-platform probes ─────────────────────────────────────────────

def probe_twitch(token, *, timeout=15, cancel_check=None):
    """Validate a Twitch OAuth token via the official introspection endpoint."""
    if cancel_check and cancel_check():
        return ProbeResult("twitch", CANCELLED)
    status, body = _fetch(
        "https://id.twitch.tv/oauth2/validate",
        headers={"Authorization": f"OAuth {token}"},
        timeout=timeout,
    )
    if status == 200:
        meta = {}
        try:
            data = json.loads(body or "{}")
            scopes = data.get("scopes") or []
            meta = {
                "scope_count": len(scopes),
                "expires_in": int(data.get("expires_in") or 0),
                "has_client_id": bool(data.get("client_id")),
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        exp = meta.get("expires_in", 0)
        detail = "Token valid"
        if exp and exp < 3600:
            detail = f"Token valid but expires in {exp}s"
        return ProbeResult("twitch", VALID, detail, status, meta)
    if status == 401:
        return ProbeResult("twitch", EXPIRED, "Token expired or revoked", status)
    if status == 429:
        return ProbeResult("twitch", RATE_LIMITED, "Rate limited by Twitch", status)
    if status <= 0:
        return ProbeResult("twitch", NETWORK_ERROR, "Could not reach id.twitch.tv", status)
    return ProbeResult("twitch", INVALID, f"Unexpected response ({status})", status)


_YT_PROBE_VIDEO = "dQw4w9WgXcQ"


def probe_youtube(api_key, *, timeout=15, cancel_check=None):
    """Validate a YouTube Data API key with a minimal read-only videos.list call."""
    if cancel_check and cancel_check():
        return ProbeResult("youtube", CANCELLED)
    url = (
        "https://www.googleapis.com/youtube/v3/videos?part=id&id="
        + _YT_PROBE_VIDEO + "&key=" + urllib.parse.quote(api_key, safe="")
    )
    status, body = _fetch(url, timeout=timeout)
    reason = ""
    try:
        data = json.loads(body or "{}")
        errors = (data.get("error") or {}).get("errors") or []
        if errors:
            reason = str(errors[0].get("reason") or "")
    except (json.JSONDecodeError, TypeError):
        pass
    if status == 200:
        return ProbeResult("youtube", VALID, "API key accepted", status)
    if status in (400, 403):
        r = reason.lower()
        if r in ("keyinvalid", "badrequest"):
            return ProbeResult(
                "youtube", INVALID, "API key is not valid", status, {"reason": reason}
            )
        if r in ("quotaexceeded", "ratelimitexceeded",
                 "dailylimitexceeded", "userratelimitexceeded"):
            return ProbeResult(
                "youtube", RATE_LIMITED, "Quota or rate limit exceeded",
                status, {"reason": reason},
            )
        if r in ("accessnotconfigured", "forbidden", "servicenotenabled"):
            return ProbeResult(
                "youtube", INSUFFICIENT_SCOPE,
                "YouTube Data API not enabled for this key",
                status, {"reason": reason},
            )
        return ProbeResult(
            "youtube", INVALID, reason or f"Rejected ({status})",
            status, {"reason": reason} if reason else {},
        )
    if status == 429:
        return ProbeResult("youtube", RATE_LIMITED, "Rate limited by Google", status)
    if status <= 0:
        return ProbeResult("youtube", NETWORK_ERROR, "Could not reach googleapis.com", status)
    return ProbeResult("youtube", INVALID, f"Unexpected response ({status})", status)


def probe_kick(token, *, timeout=15, cancel_check=None):
    """Kick has no public token-introspection endpoint and its authenticated
    API is unofficial and Cloudflare-gated, so a reliable non-downloading
    probe would risk a false negative. Report the credential as unsupported —
    it is still sent as-is when downloading."""
    return ProbeResult(
        "kick", UNSUPPORTED,
        "Kick has no public token-validation endpoint; the token is used "
        "as-is when downloading.",
    )


def probe_cookies(*, path=None):
    """Validate the imported Netscape cookies.txt locally (no request).

    Reports valid/expired/invalid/no_credential based on parseable rows and
    per-cookie expiry. Session cookies (expiry 0) count as live.
    """
    if path is None:
        from .cookies import cookies_file_path
        path = cookies_file_path()
    if not path:
        return ProbeResult("cookies", NO_CREDENTIAL, "No cookies.txt imported")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as e:
        return ProbeResult("cookies", NETWORK_ERROR, f"Could not read cookies file: {e}")

    now = time.time()
    total = 0
    live = 0
    domains = set()
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            # Netscape HttpOnly rows are prefixed with "#HttpOnly_".
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            else:
                continue
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        total += 1
        domain = fields[0].lstrip(".").lower()
        if domain:
            domains.add(domain)
        try:
            expires = int(fields[4])
        except (ValueError, IndexError):
            expires = 0
        if expires == 0 or expires > now:  # 0 == session cookie
            live += 1

    if total == 0:
        return ProbeResult("cookies", INVALID, "No valid Netscape cookie rows found")
    meta = {"total": total, "live": live, "domains": sorted(domains)[:12]}
    if live == 0:
        return ProbeResult("cookies", EXPIRED, f"All {total} cookies have expired", 0, meta)
    detail = f"{live} of {total} cookies live across {len(domains)} domain(s)"
    return ProbeResult("cookies", VALID, detail, 0, meta)


# ── Dispatch ────────────────────────────────────────────────────────

_PLATFORM_PROBES = {
    "twitch": probe_twitch,
    "youtube": probe_youtube,
    "kick": probe_kick,
}


def probe_platform(platform, *, timeout=15, cancel_check=None):
    """Probe a configured *platform* ('twitch'/'youtube'/'kick'/'cookies').

    Reads the stored credential (never exposing it) and dispatches to the
    matching probe. Returns ``no_credential`` when nothing is stored.
    """
    platform = str(platform or "").lower()
    if platform == "cookies":
        return probe_cookies()
    if cancel_check and cancel_check():
        return ProbeResult(platform, CANCELLED)
    cred = accounts.get_credential(platform)
    if not cred:
        return ProbeResult(platform, NO_CREDENTIAL, "No credential stored")
    fn = _PLATFORM_PROBES.get(platform)
    if fn is None:
        return ProbeResult(platform, UNSUPPORTED, "No validator for this platform")
    return fn(cred, timeout=timeout, cancel_check=cancel_check)


def probe_all(*, timeout=15, cancel_check=None):
    """Probe every configured platform plus the cookie profile.

    Returns a list of :class:`ProbeResult`. Platforms without a stored
    credential are included as ``no_credential`` so callers see full coverage.
    """
    results = []
    for platform in ("twitch", "youtube", "kick"):
        if cancel_check and cancel_check():
            results.append(ProbeResult(platform, CANCELLED))
            continue
        results.append(probe_platform(platform, timeout=timeout, cancel_check=cancel_check))
    results.append(probe_cookies())
    return results
