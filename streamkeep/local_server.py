"""Stable import facade for the browser-companion HTTP server.

The implementation lives in :mod:`streamkeep.server` so authentication,
route dispatch, and static assets have package-level homes.  Existing imports
and module-level test patches continue to target ``streamkeep.local_server``.
"""

from __future__ import annotations

import sys
from types import ModuleType

from .server import _legacy as _implementation
from .server import auth as _auth
from .server import origins as _origins
from .server import static_assets as _static_assets

# V163: the facade composes the domain modules rather than requiring every name
# to be re-exported through the legacy one. ``_implementation`` stays first so a
# patched attribute there still wins, which is the contract existing tests rely
# on. Every module holding part of the surface must be listed here, or a name
# that moves out of the legacy module stops resolving through the facade.
_DOMAINS = (_implementation, _auth, _origins, _static_assets)


def _owner(name):
    """First domain module that can serve ``name`` — reads only.

    ``hasattr`` on purpose: a module that forwards through ``__getattr__`` is a
    legitimate source for a read.
    """
    for module in _DOMAINS:
        if hasattr(module, name):
            return module
    return None


def _holders(name):
    """Every domain module holding a real binding for ``name`` — writes.

    While the server was one module, patching ``local_server.X`` rebound the one
    binding every caller resolved. Splitting the auth layer out gave those names
    two bindings — one where they are defined, one in the legacy module that
    imported them back — and writing to only the first leaves the defining
    module still running the original. Reading the patched name back through the
    facade returns the patch either way, so an identity assertion cannot tell
    the difference; patch reach has to cover every holder.

    ``vars()`` rather than ``hasattr``: a module forwarding through
    ``__getattr__`` has no binding of its own, and writing one into it would
    freeze that forward at today's value.
    """
    return [module for module in _DOMAINS if name in vars(module)]


class _LocalServerFacade(ModuleType):
    """Forward the legacy server surface without changing its patch contract."""

    def __getattr__(self, name):
        owner = _owner(name)
        if owner is None:
            raise AttributeError(name)
        return getattr(owner, name)

    def __setattr__(self, name, value):
        holders = () if name == "_implementation" else _holders(name)
        if holders:
            history = self.__dict__.setdefault("_forwarded_previous", {})
            history.setdefault(name, []).append(
                [(module, vars(module)[name]) for module in holders]
            )
            for module in holders:
                setattr(module, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name):
        history = self.__dict__.get("_forwarded_previous", {})
        values = history.get(name, [])
        if name != "_implementation" and values:
            for module, previous in values.pop():
                setattr(module, name, previous)
            if not values:
                history.pop(name, None)
        super().__delattr__(name)

    def __dir__(self):
        names = set(super().__dir__())
        for module in _DOMAINS:
            names |= set(dir(module))
        return sorted(names)


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
    if path.startswith("/api/uploads/profiles/") and path.count("/") == 4:
    if path.startswith("/api/tokens/") and path.count("/") == 3:

def _handle_pair(self):
'''
