
"""Published-recording and RSS-feed table family (V163).

Owns how a published recording's opaque id and its public text fields are
produced; the rest of the family is still forwarded to the legacy module,
which is imported lazily so this module can own definitions without making
the package cyclic.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

_PUBLISHING_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PUBLISHING_TEXT_LIMIT = 256

_EXPORTED = frozenset({
    "publish_recording", "unpublish_recording", "published_recording",
    "published_recording_for_history", "published_recordings", "publish_feed",
    "published_feed", "published_feeds", "unpublish_feed",
    "published_recordings_for_feed",
})

__all__ = sorted(_EXPORTED)


def __getattr__(name):
    # Imported lazily: this module owns definitions of its own now, and a
    # module-scope import of the connection-owning module would make the
    # package cyclic. The forwarding below keeps the rest of the surface.
    from . import _legacy as _implementation

    if name not in _EXPORTED:
        raise AttributeError(name)
    return getattr(_implementation, name)


def _publishing_id(value: Any, field: str = "share_id") -> str:
    candidate = str(value or "").strip().lower()
    if not _PUBLISHING_ID_RE.fullmatch(candidate):
        raise ValueError(f"{field} is invalid")
    return candidate


def _publishing_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if len(text) > _PUBLISHING_TEXT_LIMIT:
        raise ValueError(f"{field} is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(f"{field} contains control characters")
    return text


def _new_publishing_id(db) -> str:
    """Generate an unguessable id without trusting caller-provided ids."""
    while True:
        candidate = secrets.token_hex(16)
        if not db.execute(
            "SELECT 1 FROM published_recordings WHERE share_id=? "
            "UNION ALL SELECT 1 FROM published_feeds WHERE feed_id=? LIMIT 1",
            (candidate, candidate),
        ).fetchone():
            return candidate
