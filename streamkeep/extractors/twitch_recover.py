"""Deleted VOD Recovery — reconstruct CDN URLs for expired / deleted Twitch VODs.

Approach:
  1. Scrape TwitchTracker for stream metadata (stream ID + timestamps) given
     a channel name + date range.
  2. Construct CDN URL candidates using known Twitch VOD URL patterns.
  3. Test candidates with HEAD requests to see if segments are still cached.
  4. Return valid M3U8 URLs that can be fed to the normal download pipeline.

CDN URL format (may rotate):
  https://d1m7jfoe9zdc1j.cloudfront.net/{hash}_{channel}_{stream_id}_{timestamp}/
    chunked/index-dvr.m3u8

The hash is SHA1(f"{channel}_{stream_id}_{timestamp}") truncated to 20 chars.
"""

import hashlib
import re
import urllib.request
import urllib.error

from ..models import QualityInfo, StreamInfo


# Known Twitch CDN domains — Twitch rotates these periodically.
CDN_DOMAINS = [
    "https://d1m7jfoe9zdc1j.cloudfront.net",
    "https://d2nvs31859zcd8.cloudfront.net",
    "https://d2aba1wr3818hz.cloudfront.net",
    "https://dqrpb9wgowsf5.cloudfront.net",
    "https://ds0h3roq6wcgc.cloudfront.net",
    "https://dgeft87wbj63p.cloudfront.net",
]

QUALITIES = ["chunked", "720p60", "720p30", "480p30", "360p30", "160p30"]

# Twitch login names: 3-25 chars, letters/digits/underscore only. Validating
# up front keeps a malformed channel out of the request paths and avoids
# firing hundreds of junk HEAD probes (6 domains x 6 qualities x ~13 stamps).
_VALID_CHANNEL = re.compile(r"^[A-Za-z0-9_]{3,25}$")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _compute_hash(channel, stream_id, timestamp):
    """Compute the CDN path hash."""
    body = f"{channel}_{stream_id}_{timestamp}"
    return hashlib.sha1(body.encode()).hexdigest()[:20]


#: Outcomes a single CDN probe can have. ``forbidden`` is deliberately distinct
#: from ``missing``: a 401/403 means the platform is refusing unauthenticated
#: access to those segments, which is an access control and not a rotated
#: domain. Recovery reconstructs URLs for content the CDN still serves
#: unauthenticated; when it does not, the answer is to stop, not to keep
#: probing until something answers.
PROBE_HIT = "hit"
PROBE_MISSING = "missing"
PROBE_FORBIDDEN = "forbidden"
PROBE_ERROR = "error"

#: Status codes that mean "you are not allowed", as opposed to "not here".
#: A stream-listing page is a few hundred kilobytes at most (V188).
MAX_RECOVERY_PAGE_BYTES = 4 * 1024 * 1024

_GATED_STATUSES = frozenset({401, 403})


class RecoveryRefused(Exception):
    """Raised when the platform gates the segments a recovery would need.

    Carried rather than swallowed so the caller reports *why* it stopped. The
    recovery path must never respond to an access control by trying another
    domain or quality — that would be working around it.
    """


def _probe_url(url, timeout=8):
    """Probe one candidate URL and name the outcome.

    Returns ``(outcome, detail)``. The old boolean told a caller nothing about
    *why* a candidate failed, so a rotated CDN domain, a VOD that was really
    deleted, and a segment set the platform refuses to serve unauthenticated all
    looked identical — and the last of those must stop the attempt.
    """
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", _UA)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        status = int(getattr(error, "code", 0) or 0)
        if status in _GATED_STATUSES:
            return PROBE_FORBIDDEN, f"HTTP {status} (access is gated)"
        if status == 404:
            return PROBE_MISSING, "HTTP 404 (not on this domain)"
        return PROBE_ERROR, f"HTTP {status}"
    except urllib.error.URLError as error:
        return PROBE_ERROR, f"unreachable: {getattr(error, 'reason', error)}"
    except OSError as error:
        return PROBE_ERROR, f"probe failed: {error}"
    status = int(getattr(resp, "status", 0) or 0)
    if status in (200, 206):
        return PROBE_HIT, f"HTTP {status}"
    if status in _GATED_STATUSES:
        return PROBE_FORBIDDEN, f"HTTP {status} (access is gated)"
    return PROBE_MISSING, f"HTTP {status}"


def _head_check(url, timeout=8):
    """Return True if the URL responds with 200 or 206.

    Kept as the boolean shape existing callers use; ``_probe_url`` is what
    carries the reason.
    """
    outcome, _detail = _probe_url(url, timeout=timeout)
    return outcome == PROBE_HIT


def _scrape_twitchtracker(channel, year, month, log_fn=None):
    """Scrape TwitchTracker for stream IDs in a given month.

    Returns list of dicts: [{stream_id, timestamp, date_str, title}]
    """
    url = f"https://twitchtracker.com/{channel}/streams/{year}/{month:02d}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", _UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            # A stream-listing page is well under this; reading without a cap
            # let a rotated or hostile host stream an unbounded body (V188).
            html = resp.read(MAX_RECOVERY_PAGE_BYTES).decode(
                "utf-8", errors="replace",
            )
    except Exception as e:
        if log_fn:
            log_fn(f"[RECOVER] Failed to fetch TwitchTracker: {e}")
        return []

    streams = []
    # TwitchTracker embeds stream IDs in links like /streams/{stream_id}
    for m in re.finditer(
        r'/streams/(\d{8,})"[^>]*>.*?'
        r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})',
        html, re.DOTALL,
    ):
        sid = m.group(1)
        date_str = m.group(2).strip()
        streams.append({
            "stream_id": sid,
            "date_str": date_str,
        })

    # Also try sullygnome pattern as fallback
    if not streams:
        for m in re.finditer(r'data-sid="(\d+)".*?data-date="([^"]+)"', html, re.DOTALL):
            streams.append({
                "stream_id": m.group(1),
                "date_str": m.group(2).strip(),
            })

    if log_fn:
        log_fn(f"[RECOVER] Found {len(streams)} stream(s) on TwitchTracker for {channel} ({year}-{month:02d})")
    return streams


def _unix_timestamp_variants(date_str):
    """Generate plausible Unix timestamps from a date string.

    TwitchTracker dates are approximate — we try a range of offsets.
    """
    import datetime
    ts_list = []
    text = str(date_str or "").strip()
    if not text:
        return ts_list
    # The TwitchTracker pattern captures minutes, but the sullygnome fallback
    # takes ``data-date`` verbatim and that arrives with seconds and sometimes an
    # ISO ``T``. Those formats used to parse as nothing at all, which skipped the
    # stream with no probe and no explanation -- a silent no-op, not a miss.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(text.replace("Z", ""), fmt).replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            continue
        base = int(dt.timestamp())
        # Try the exact time and +/- 1 hour in 10-minute increments
        for offset in range(-3600, 3600 + 1, 600):
            ts_list.append(base + offset)
        return ts_list
    return ts_list


def probe_vod(channel, stream_id, timestamp, log_fn=None):
    """Probe every candidate CDN domain and report what each one said.

    Returns ``(urls, report)`` where ``report`` is one entry per domain:
    ``{"domain", "outcome", "detail", "quality", "url"}``. Enumerating the
    domains is the point — Twitch rotates them, so "nothing found" without a
    per-domain answer cannot distinguish a rotated domain list from a VOD that
    is genuinely gone.

    Raises ``RecoveryRefused`` the moment a probe comes back gated. Continuing
    past a 403 to try other domains and qualities is precisely the behaviour
    this must not have.
    """
    channel_lower = channel.lower().strip()
    digest = _compute_hash(channel_lower, stream_id, timestamp)
    found = []
    report = []
    for domain in CDN_DOMAINS:
        entry = {"domain": domain, "outcome": PROBE_MISSING,
                 "detail": "", "quality": "", "url": ""}
        for quality in QUALITIES:
            url = (
                f"{domain}/{digest}_{channel_lower}_{stream_id}_{timestamp}"
                f"/{quality}/index-dvr.m3u8"
            )
            outcome, detail = _probe_url(url)
            entry["outcome"], entry["detail"] = outcome, detail
            if outcome == PROBE_FORBIDDEN:
                if log_fn:
                    log_fn(
                        f"[RECOVER] REFUSED at {domain}: {detail}. Recovery "
                        "reconstructs URLs for content the CDN still serves "
                        "unauthenticated and will not attempt to bypass an "
                        "access control."
                    )
                report.append(entry)
                raise RecoveryRefused(
                    f"{domain} returned {detail}; the platform is gating these "
                    "segments, so there is nothing to recover without "
                    "circumventing that"
                )
            if outcome == PROBE_HIT:
                entry["quality"], entry["url"] = quality, url
                if log_fn:
                    log_fn(f"[RECOVER] HIT: {quality} @ {domain} ({detail})")
                found.append(url)
                break  # Found on this domain, skip lower qualities
        else:
            if log_fn:
                log_fn(
                    f"[RECOVER] miss: {domain} - "
                    f"{entry['detail'] or 'no quality matched'}"
                )
        report.append(entry)
    return found, report


def recover_vod(channel, stream_id, timestamp, log_fn=None):
    """Try CDN domains * qualities for a single stream.

    Returns the list of valid M3U8 URLs. ``probe_vod`` is the reporting form;
    this stays the plain list callers already use.
    """
    urls, _report = probe_vod(channel, stream_id, timestamp, log_fn=log_fn)
    return urls


def format_recovery_report(report):
    """One human-readable line per candidate domain."""
    lines = []
    for entry in report or []:
        domain = entry.get("domain", "?")
        outcome = entry.get("outcome", PROBE_MISSING)
        detail = entry.get("detail") or ""
        if outcome == PROBE_HIT:
            lines.append(
                f"{domain}: resolved at {entry.get('quality') or 'unknown'} "
                f"({detail})"
            )
        else:
            lines.append(f"{domain}: {outcome} - {detail or 'no detail'}")
    return lines


def recover_channel_vods(channel, year, month, log_fn=None, progress_fn=None):
    """Full recovery pipeline: scrape tracker -> brute-force CDN -> return StreamInfo list."""
    channel = (channel or "").strip()
    if not _VALID_CHANNEL.match(channel):
        if log_fn:
            log_fn(f"[RECOVER] Invalid Twitch channel name: {channel!r}")
        return []
    streams = _scrape_twitchtracker(channel, year, month, log_fn)
    if not streams:
        return []

    results = []
    total = len(streams)
    for i, s in enumerate(streams):
        if progress_fn:
            progress_fn(int((i / total) * 100), f"Testing stream {s['stream_id']}...")

        timestamps = _unix_timestamp_variants(s.get("date_str", ""))
        if not timestamps:
            # Never silent: without a timestamp there is nothing to hash, so the
            # stream is unrecoverable for a stated reason rather than skipped.
            if log_fn:
                log_fn(
                    f"[RECOVER] skipped {s.get('stream_id')}: unreadable date "
                    f"{s.get('date_str', '')!r} - no timestamp to reconstruct from"
                )
            continue
        for ts in timestamps:
            try:
                urls, report = probe_vod(channel, s["stream_id"], ts, log_fn)
            except RecoveryRefused as refusal:
                # An access control applies to the channel's segments, not to
                # this one timestamp guess, so trying more of them is both
                # pointless and the wrong thing to do.
                if log_fn:
                    log_fn(f"[RECOVER] Stopping: {refusal}")
                if progress_fn:
                    progress_fn(100, f"Refused - {refusal}")
                return results
            if not urls and log_fn:
                for line in format_recovery_report(report):
                    log_fn(f"[RECOVER]   {line}")
            if urls:
                # Use the highest quality (first hit)
                info = StreamInfo(
                    platform="twitch",
                    channel=channel,
                    title=f"Recovered VOD — {s.get('date_str', 'unknown date')}",
                    url=urls[0],
                    source_id=f"vod:{s['stream_id']}",
                    webpage_url=(
                        f"https://www.twitch.tv/videos/{s['stream_id']}"
                    ),
                    qualities=[
                        QualityInfo(name="recovered", url=u, format_type="hls")
                        for u in urls
                    ],
                )
                results.append(info)
                break  # Found a working timestamp for this stream

    if progress_fn:
        progress_fn(100, f"Done — {len(results)} recoverable VOD(s)")
    return results
