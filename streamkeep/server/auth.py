"""Authentication and replay-policy facade for the companion server."""

from . import _legacy as _implementation

_EXPORTED = frozenset({
    "TokenGrant", "TokenStore", "PairingStore", "ReplayStore",
    "generate_bearer_token", "valid_bearer_token", "ALL_SCOPES",
    "SCOPE_STATUS", "SCOPE_QUEUE", "SCOPE_RECOVERY", "TOKEN_TTL_MAX_SECONDS",
})

__all__ = sorted(_EXPORTED)


def __getattr__(name):
    if name not in _EXPORTED:
        raise AttributeError(name)
    return getattr(_implementation, name)
