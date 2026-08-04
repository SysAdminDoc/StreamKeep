"""Published-recording and RSS-feed table-family facade."""

from . import _legacy as _implementation

_EXPORTED = frozenset({
    "publish_recording", "unpublish_recording", "published_recording",
    "published_recording_for_history", "published_recordings", "publish_feed",
    "published_feed", "published_feeds", "unpublish_feed",
    "published_recordings_for_feed",
})

__all__ = sorted(_EXPORTED)


def __getattr__(name):
    if name not in _EXPORTED:
        raise AttributeError(name)
    return getattr(_implementation, name)
