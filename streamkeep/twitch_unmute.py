"""Twitch VOD copyright-muted fragment recovery.

Twitch marks some VOD fragment paths with a ``-muted`` suffix when audio was
muted for a copyright claim.  For recent VODs the corresponding unmuted CDN
fragment may still be available at the same path with that suffix removed.
This module only rewrites a VOD media playlist after a caller-provided probe
confirms that replacement URL; failed probes leave the original fragment in
place.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit


_MUTED_PATH_TOKEN = re.compile(r"-muted(?=[._/-]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class TwitchUnmuteResult:
    """Accounting for one attempted VOD playlist rewrite."""

    rewritten_body: str
    muted_segment_count: int = 0
    restored_segment_count: int = 0
    unavailable_segment_count: int = 0
    probe_count: int = 0
    probe_limit_reached: bool = False
    is_endlist: bool = False

    @property
    def changed(self) -> bool:
        """Whether at least one muted fragment was replaced."""
        return self.restored_segment_count > 0


def _unmuted_url(raw_uri: str, base_url: str) -> str:
    """Return a same-origin candidate with one Twitch mute token removed."""
    resolved = urljoin(str(base_url or ""), str(raw_uri or "").strip())
    parsed = urlsplit(resolved)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    path, count = _MUTED_PATH_TOKEN.subn("", parsed.path, count=1)
    if not count:
        return ""
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        path,
        parsed.query,
        parsed.fragment,
    ))


def rewrite_twitch_vod_playlist(
    body: str,
    base_url: str,
    *,
    probe: Callable[[str], bool],
    max_probes: int = 256,
) -> TwitchUnmuteResult:
    """Replace reachable ``-muted`` VOD fragments with unmuted candidates.

    Only a media playlist carrying ``#EXT-X-ENDLIST`` is eligible.  The
    playlist is scanned by HLS segment blocks, so URI attributes and ordinary
    comments cannot accidentally be rewritten.  Probe results are cached by
    candidate URL because repeated fragment references are possible in edited
    VODs.
    """
    text = str(body or "")
    lines = text.splitlines()
    is_endlist = any(line.strip().upper() == "#EXT-X-ENDLIST" for line in lines)
    if not is_endlist:
        return TwitchUnmuteResult(text, is_endlist=False)

    try:
        probe_limit = max(0, int(max_probes))
    except (TypeError, ValueError):
        probe_limit = 256

    rewritten = list(lines)
    pending_segment = False
    muted_count = 0
    restored_count = 0
    unavailable_count = 0
    probe_count = 0
    probe_limit_reached = False
    availability: dict[str, bool] = {}

    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        upper = line.upper()
        if upper.startswith("#EXTINF:"):
            pending_segment = True
            continue
        if not line.startswith("#") and pending_segment:
            pending_segment = False
            candidate = _unmuted_url(line, base_url)
            if not candidate:
                continue
            muted_count += 1
            if candidate not in availability:
                if probe_count >= probe_limit:
                    probe_limit_reached = True
                    availability[candidate] = False
                else:
                    probe_count += 1
                    try:
                        availability[candidate] = bool(probe(candidate))
                    except Exception:
                        # A transient HEAD/proxy failure is a no-source
                        # result, never a reason to abort the VOD download.
                        availability[candidate] = False
            if availability[candidate]:
                rewritten[index] = candidate
                restored_count += 1
            else:
                unavailable_count += 1
            continue
        if line and not line.startswith("#"):
            pending_segment = False

    return TwitchUnmuteResult(
        "\n".join(rewritten) + ("\n" if text.endswith(("\n", "\r")) else ""),
        muted_segment_count=muted_count,
        restored_segment_count=restored_count,
        unavailable_segment_count=unavailable_count,
        probe_count=probe_count,
        probe_limit_reached=probe_limit_reached,
        is_endlist=True,
    )
