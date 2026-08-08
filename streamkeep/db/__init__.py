"""Stable database facade.

The implementation is organized under this package so table-family modules
can evolve without forcing callers to change from ``streamkeep.db``.  The
facade deliberately forwards attribute reads and writes to the implementation
module: tests, profile switching, and maintenance tools have historically
patched module-level database policy values and that contract remains valid.
"""

from __future__ import annotations

import sys
from types import ModuleType

from . import _legacy as _implementation
from . import connection as _connection
from . import history_actions as _history_actions
from . import monitor as _monitor
from . import primitives as _primitives
from . import projections as _projections
from . import publishing as _publishing
from . import queue as _queue
from . import schema as _schema
from . import tombstones as _tombstones

# V163: the facade composes the domain modules rather than requiring every
# name to be re-exported through the legacy one. ``_implementation`` stays
# first so a patched attribute there still wins, which is the contract the
# existing tests and profile-switching tools rely on.
_DOMAINS = (
    _implementation, _connection, _schema, _history_actions, _tombstones,
    _publishing, _monitor, _queue, _projections, _primitives,
)


def _owner(name):
    """First domain module that can serve ``name`` — reads only.

    ``hasattr`` on purpose: a shim that forwards through ``__getattr__`` is a
    legitimate source for a read.
    """
    for module in _DOMAINS:
        if hasattr(module, name):
            return module
    return None


def _holders(name):
    """Every domain module holding a real binding for ``name`` — writes.

    Before V163 the package was a single module, so patching ``db.X`` rebound
    the one binding every caller resolved against. The split gave a moved name
    several bindings — one where it is defined, one in each module that
    imported it — and writing to only the first left the defining module and
    its importers still calling the original. Patch reach has to cover all of
    them or the patch silently does nothing to the code under test.

    ``vars()`` rather than ``hasattr``: a shim forwarding through
    ``__getattr__`` has no binding of its own, and writing one into it would
    freeze that forward at today's value.
    """
    return [module for module in _DOMAINS if name in vars(module)]


class _DatabaseFacade(ModuleType):
    """Forward the legacy module surface while retaining patch compatibility."""

    def __getattr__(self, name):
        owner = _owner(name)
        if owner is None:
            raise AttributeError(name)
        return getattr(owner, name)

    def __setattr__(self, name, value):
        holders = () if name.startswith("_implementation") else _holders(name)
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
_facade.__class__ = _DatabaseFacade
_facade._implementation = _implementation
