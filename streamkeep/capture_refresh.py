"""Bounded mid-capture delivery refresh helpers.

Live HLS and DASH delivery URLs are often short-lived even when the public
page URL is stable.  This module keeps the policy for recognizing an expired
delivery response, choosing bounded jittered backoff, and describing the seam
between two resolved representations independent of the Qt download worker.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass


MAX_REFRESH_ATTEMPTS = 3
MAX_REFRESH_BACKOFF_SECONDS = 8.0
_STATUS_RE = re.compile(r"\b(403|410)\b")


@dataclass(frozen=True)
class ManifestExpiry:
    """A terminal HTTP response that is safe to retry through re-resolution."""

    status: int
    line: str


def detect_manifest_expiry(lines) -> ManifestExpiry | None:
    """Return a 403/410 diagnostic found in downloader output.

    A bare status code is not sufficient: ffmpeg and yt-dlp can print numeric
    media metadata beside the error stream.  Requiring an HTTP/transport or
    manifest-related marker keeps ordinary format diagnostics from triggering
    a new source resolution.
    """
    markers = (
        "http", "forbidden", "gone", "playlist", "manifest", "segment",
        "server returned", "access denied", "status code",
    )
    for raw_line in lines or ():
        line = str(raw_line or "").strip()
        lowered = line.casefold()
        match = _STATUS_RE.search(lowered)
        if not match or not any(marker in lowered for marker in markers):
            continue
        return ManifestExpiry(status=int(match.group(1)), line=line[:512])
    return None


def jittered_refresh_delay(attempt: int, *, random_fn=None) -> float:
    """Return bounded exponential backoff with stable, testable jitter."""
    try:
        ordinal = max(1, int(attempt))
    except (TypeError, ValueError):
        ordinal = 1
    base = min(MAX_REFRESH_BACKOFF_SECONDS, 0.5 * (2 ** (ordinal - 1)))
    chooser = random_fn or random.uniform
    try:
        factor = float(chooser(0.75, 1.25))
    except (TypeError, ValueError):
        factor = 1.0
    return min(MAX_REFRESH_BACKOFF_SECONDS, max(0.0, base * factor))


def _track_signature(track) -> tuple[str, ...]:
    if isinstance(track, dict):
        get = track.get
    else:
        def get(key, default=""):
            return getattr(track, key, default)
    return (
        str(get("kind", "") or "").casefold(),
        str(get("codec", "") or "").casefold(),
        str(get("resolution", "") or "").casefold(),
        str(get("language", "") or "").casefold(),
        str(get("group_id", "") or "").casefold(),
    )


def track_signatures(tracks) -> tuple[tuple[str, ...], ...]:
    """Return bounded, credential-free representation signatures."""
    values = [_track_signature(track) for track in (tracks or [])]
    return tuple(sorted(values)[:32])


def describe_transition(
    *,
    previous_tracks=(),
    fresh_tracks=(),
    previous_media_sequence=0,
    fresh_media_sequence=0,
    previous_discontinuity_sequence=0,
    fresh_discontinuity_sequence=0,
) -> dict[str, object]:
    """Describe changes that the final seam/remux needs to know about."""
    try:
        old_media = int(previous_media_sequence or 0)
    except (TypeError, ValueError):
        old_media = 0
    try:
        new_media = int(fresh_media_sequence or 0)
    except (TypeError, ValueError):
        new_media = 0
    try:
        old_disc = int(previous_discontinuity_sequence or 0)
    except (TypeError, ValueError):
        old_disc = 0
    try:
        new_disc = int(fresh_discontinuity_sequence or 0)
    except (TypeError, ValueError):
        new_disc = 0
    old_signature = track_signatures(previous_tracks)
    fresh_signature = track_signatures(fresh_tracks)
    codec_changed = bool(old_signature and fresh_signature)
    if codec_changed:
        old_codecs = tuple(item[1] for item in old_signature)
        fresh_codecs = tuple(item[1] for item in fresh_signature)
        codec_changed = old_codecs != fresh_codecs
    discontinuity = new_disc > old_disc
    return {
        "media_sequence_before": old_media,
        "media_sequence_after": new_media,
        "discontinuity_sequence_before": old_disc,
        "discontinuity_sequence_after": new_disc,
        "media_window_advanced": new_media > old_media,
        "discontinuity": discontinuity,
        "codec_changed": codec_changed,
        "previous_tracks": list(old_signature),
        "fresh_tracks": list(fresh_signature),
    }
