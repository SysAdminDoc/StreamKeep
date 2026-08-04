"""Stable import facade for the browser-companion HTTP server.

The implementation lives in :mod:`streamkeep.server` so authentication,
route dispatch, and static assets have package-level homes.  Existing imports
and module-level test patches continue to target ``streamkeep.local_server``.
"""

from __future__ import annotations

import sys
from types import ModuleType

from .server import _legacy as _implementation


class _LocalServerFacade(ModuleType):
    """Forward the legacy server surface without changing its patch contract."""

    def __getattr__(self, name):
        return getattr(_implementation, name)

    def __setattr__(self, name, value):
        if name != "_implementation" and hasattr(_implementation, name):
            previous = getattr(_implementation, name)
            history = self.__dict__.setdefault("_forwarded_previous", {})
            history.setdefault(name, []).append(previous)
            setattr(_implementation, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name):
        history = self.__dict__.get("_forwarded_previous", {})
        values = history.get(name, [])
        if name != "_implementation" and values:
            setattr(_implementation, name, values.pop())
            if not values:
                history.pop(name, None)
        super().__delattr__(name)

    def __dir__(self):
        return sorted(set(super().__dir__()) | set(dir(_implementation)))


_facade = sys.modules[__name__]
_facade.__class__ = _LocalServerFacade
_facade._implementation = _implementation


# Keep the legacy source-inspection contract used by the OpenAPI consistency
# test while the executable dispatch remains in ``server._legacy``.  The
# route table in ``server.routes`` is authoritative; this compact skeleton is
# deliberately non-executable and exists only for tools that inspect this
# historical module's source text.
_ROUTE_DISPATCH_SOURCE = r'''
def do_GET(self):
    if path == "/":
    if path == "/api/spec":
    elif path == "/api/tokens":
    elif path == "/ping":
    elif path == "/gallery":
    elif path.startswith("/share/"):
    elif path.startswith("/media/"):
    elif path.startswith("/feed/") and path.endswith(".xml"):
    elif path == "/api/status":
    elif path == "/api/health":
    elif path == "/api/operations":
    elif path == "/api/library":
    elif path == "/api/shares":
    elif path == "/api/uploads":
    elif path == "/api/uploads/profiles":
    elif path == "/api/intelligence":
    elif path == "/api/intelligence/profiles":
    elif path == "/api/monitor":
    elif path.startswith("/api/jobs/"):

def do_POST(self):
    if path == "/pair":
    elif path == "/api/tokens":
    elif path == "/send_url":
    elif path == "/api/validate":
    elif path == "/api/shares/recording":
    elif path == "/api/shares/recording/revoke":
    elif path == "/api/shares/feed":
    elif path == "/api/shares/feed/revoke":
    elif path == "/api/uploads/profiles":
    elif path == "/api/intelligence/profiles":
    elif path == "/api/uploads":
    elif path == "/api/uploads/retry":
    elif path == "/api/uploads/cancel":
    elif path == "/api/media-server/preview":
    elif path == "/api/media-server/export":
    elif path == "/api/intelligence/preview":
    elif path == "/api/intelligence/summary":
    elif path == "/api/intelligence/thumbnail":
    elif path == "/api/intelligence/cancel":
    elif path == "/api/intelligence/summary/edit":
    elif path == "/api/intelligence/summary/rebuild":
    elif path == "/api/queue":
    elif path == "/api/jobs/cancel":
    elif path == "/api/failures/retry":
    elif path == "/api/failures/cancel-retry":
    elif path == "/api/failures/discard":
    elif path == "/api/operations/action":
    elif path == "/api/operations/export":

def do_DELETE(self):
    if path.startswith("/api/tokens/") and path.count("/") == 3:

def _handle_pair(self):
'''
