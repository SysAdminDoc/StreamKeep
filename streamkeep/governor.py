"""Per-host adaptive rate governance (V162).

Bulk archiving trips 429s and soft blocks, and the only answer used to be for
the operator to hand-tune sleep, rate limits, and fragment concurrency by
guesswork — settings that are global, so slowing down for one strict host
slowed down every other host too.

StreamKeep owns the queue, so it can watch what hosts actually say and react
per host. This module is that reaction, and nothing else: it is pure, has no
Qt, no network, and no config reads, so the policy can be tested against a
synthetic throttling server rather than a live site.

The policy is AIMD — multiplicative decrease, additive increase — because a
throttle means "you are already over the line" (back off hard and at once)
while a run of successes only means "not over it yet" (creep back up). A
``Retry-After`` header is authoritative and overrides the computed delay when
it asks for longer.

Nothing here ever blocks. Callers ask what the current allowance is and honour
it themselves, so the governor cannot become the thing that hangs a queue.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

#: Concurrency a host is allowed before anything has gone wrong.
DEFAULT_CONCURRENCY = 4
#: Never drop below this: one job at a time still makes progress.
MIN_CONCURRENCY = 1
#: Delay ceiling. Past this the host is not rate-limiting, it is refusing.
MAX_DELAY_SECONDS = 300.0
# A server-provided Retry-After may legitimately exceed the computed local
# ceiling. Keep a finite safety bound for malformed/untrusted input while
# preserving the value the service actually requested.
MAX_RETRY_AFTER_SECONDS = 7 * 24 * 60 * 60
#: First delay applied when a host throttles with no Retry-After.
BASE_DELAY_SECONDS = 5.0
#: Consecutive successes before the governor gives back one step.
SUCCESSES_TO_RECOVER = 5
#: A host silent for this long is assumed healthy again.
IDLE_RESET_SECONDS = 1800.0

_LOCK = threading.RLock()
_HOSTS: dict[str, "HostGovernor"] = {}
_ENABLED = True
_DEFAULT_CONCURRENCY = DEFAULT_CONCURRENCY


@dataclass(frozen=True)
class HostGovernor:
    """What one host is currently allowed, and why."""

    host: str = ""
    concurrency: int = DEFAULT_CONCURRENCY
    delay_seconds: float = 0.0
    throttles: int = 0
    successes: int = 0
    updated_at: float = 0.0
    reason: str = ""
    classification: str = ""

    @property
    def throttled(self) -> bool:
        return self.delay_seconds > 0 or self.concurrency < _DEFAULT_CONCURRENCY


def host_key(url_or_host) -> str:
    """Fold a URL or bare hostname into the key the governor is keyed by.

    Governance is per host rather than per job: two jobs pulling the same CDN
    are the same conversation as far as that CDN is concerned.
    """
    raw = str(url_or_host or "").strip()
    if not raw:
        return ""
    if "//" in raw:
        try:
            raw = urlsplit(raw).hostname or ""
        except ValueError:
            return ""
    return raw.strip().strip(".").casefold()[:253]


def configure(*, enabled=True, default_concurrency=DEFAULT_CONCURRENCY) -> None:
    """Set the operator's policy. Disabling stops the governor advising."""
    global _ENABLED, _DEFAULT_CONCURRENCY
    with _LOCK:
        _ENABLED = bool(enabled)
        _DEFAULT_CONCURRENCY = max(
            MIN_CONCURRENCY, int(default_concurrency or DEFAULT_CONCURRENCY)
        )


def reset(host=None) -> None:
    """Forget one host's state, or all of it."""
    with _LOCK:
        if host is None:
            _HOSTS.clear()
            return
        _HOSTS.pop(host_key(host), None)


def _current(key, now) -> HostGovernor:
    state = _HOSTS.get(key)
    if state is None:
        return HostGovernor(
            host=key, concurrency=_DEFAULT_CONCURRENCY, updated_at=now,
        )
    # A host nobody has upset in a long time is assumed healthy again; without
    # this a single 429 during an overnight backfill would still be throttling
    # the next morning's queue.
    if state.throttled and now - state.updated_at >= IDLE_RESET_SECONDS:
        return HostGovernor(
            host=key, concurrency=_DEFAULT_CONCURRENCY, updated_at=now,
            reason="recovered after a quiet period",
        )
    return state


def record_throttle(
    url_or_host, *, retry_after=None, now=None,
    reason="throttled by the host", classification="rate-limited",
) -> HostGovernor:
    """Register a host pushback and carry its operator-facing class."""
    key = host_key(url_or_host)
    if not key:
        return HostGovernor()
    moment = float(time.time() if now is None else now)
    with _LOCK:
        state = _current(key, moment)
        concurrency = max(MIN_CONCURRENCY, state.concurrency // 2)
        # Each further throttle doubles the wait rather than re-applying the
        # base: a host that says no twice is not asking for the same pause.
        delay = max(BASE_DELAY_SECONDS, state.delay_seconds * 2)
        try:
            requested = float(retry_after) if retry_after is not None else 0.0
        except (TypeError, ValueError):
            requested = 0.0
        requested = min(MAX_RETRY_AFTER_SECONDS, max(0.0, requested))
        if requested > 0:
            # The host named a number; it outranks our guess when it is larger.
            delay = max(delay, requested)
        # Keep a literal server directive even when it exceeds the normal
        # computed ceiling. Without this exception a Retry-After: 600 was
        # silently shortened to 300 and the next request could be rejected.
        ceiling = max(MAX_DELAY_SECONDS, requested)
        updated = HostGovernor(
            host=key,
            concurrency=concurrency,
            delay_seconds=min(ceiling, delay),
            throttles=state.throttles + 1,
            successes=0,
            updated_at=moment,
            reason=str(reason or "throttled by the host"),
            classification=str(classification or "rate-limited"),
        )
        _HOSTS[key] = updated
        return updated


def record_success(url_or_host, *, now=None) -> HostGovernor:
    """Register a clean transfer. Recovers one step per run of successes."""
    key = host_key(url_or_host)
    if not key:
        return HostGovernor()
    moment = float(time.time() if now is None else now)
    with _LOCK:
        state = _current(key, moment)
        if not state.throttled:
            updated = replace(
                state, host=key, successes=state.successes + 1,
                updated_at=moment,
            )
            _HOSTS[key] = updated
            return updated
        successes = state.successes + 1
        if successes < SUCCESSES_TO_RECOVER:
            updated = replace(
                state, host=key, successes=successes, updated_at=moment,
            )
            _HOSTS[key] = updated
            return updated
        # Additive increase: give back one slot, and halve the delay rather
        # than dropping it, so a host that only tolerates a slow pace is not
        # immediately hammered back to where it complained.
        delay = 0.0 if state.delay_seconds <= BASE_DELAY_SECONDS else (
            state.delay_seconds / 2
        )
        concurrency = min(_DEFAULT_CONCURRENCY, state.concurrency + 1)
        updated = HostGovernor(
            host=key,
            concurrency=concurrency,
            delay_seconds=delay,
            throttles=state.throttles,
            successes=0,
            updated_at=moment,
            reason="recovering after sustained success",
        )
        _HOSTS[key] = updated
        return updated


def state_for(url_or_host, *, now=None) -> HostGovernor:
    """Return what this host is currently allowed."""
    key = host_key(url_or_host)
    moment = float(time.time() if now is None else now)
    if not key:
        return HostGovernor(concurrency=_DEFAULT_CONCURRENCY)
    with _LOCK:
        return _current(key, moment)


def concurrency_for(url_or_host, *, ceiling=None, now=None) -> int:
    """How many jobs may run against this host, never above *ceiling*."""
    limit = _DEFAULT_CONCURRENCY if ceiling is None else int(ceiling)
    if not _ENABLED:
        return max(MIN_CONCURRENCY, limit)
    advised = state_for(url_or_host, now=now).concurrency
    return max(MIN_CONCURRENCY, min(limit, advised))


def delay_for(url_or_host, *, now=None) -> float:
    """Seconds to wait before the next request to this host."""
    if not _ENABLED:
        return 0.0
    return float(state_for(url_or_host, now=now).delay_seconds)


def active_states(*, now=None) -> list[HostGovernor]:
    """Every host currently under governance, worst first — for the UI."""
    moment = float(time.time() if now is None else now)
    with _LOCK:
        keys = list(_HOSTS)
    live = [_current(key, moment) for key in keys]
    throttled = [state for state in live if state.throttled]
    throttled.sort(key=lambda s: (-s.delay_seconds, s.concurrency, s.host))
    return throttled


def summary(*, now=None) -> str:
    """One line an operator can read in the operations header."""
    states = active_states(now=now)
    if not states:
        return ""
    worst = states[0]
    detail = f"{worst.host}: {worst.concurrency} at once"
    if worst.delay_seconds > 0:
        detail += f", {worst.delay_seconds:g}s between requests"
    if len(states) > 1:
        detail += f" (+{len(states) - 1} more)"
    return f"Backing off — {detail}"


def public_view(*, now=None) -> dict:
    """Governor state for the operations surface and the REST snapshot.

    Host names are already public (they are the sites being archived) and no
    URL, path, or credential is included.
    """
    states = active_states(now=now)
    return {
        "enabled": bool(_ENABLED),
        "default_concurrency": int(_DEFAULT_CONCURRENCY),
        "summary": summary(now=now),
        "hosts": [
            {
                "host": state.host,
                "concurrency": int(state.concurrency),
                "delay_seconds": round(float(state.delay_seconds), 3),
                "throttles": int(state.throttles),
                "reason": state.reason,
                "classification": state.classification,
            }
            for state in states
        ],
    }
