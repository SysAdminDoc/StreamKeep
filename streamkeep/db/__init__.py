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
from . import history_actions as _history_actions
from . import primitives as _primitives
from . import projections as _projections
from . import schema as _schema

# V163: the facade composes the domain modules rather than requiring every
# name to be re-exported through the legacy one. ``_implementation`` stays
# first so a patched attribute there still wins, which is the contract the
# existing tests and profile-switching tools rely on.
_DOMAINS = (_implementation, _schema, _history_actions, _projections, _primitives)


def _owner(name):
    for module in _DOMAINS:
        if hasattr(module, name):
            return module
    return None


class _DatabaseFacade(ModuleType):
    """Forward the legacy module surface while retaining patch compatibility."""

    def __getattr__(self, name):
        owner = _owner(name)
        if owner is None:
            raise AttributeError(name)
        return getattr(owner, name)

    def __setattr__(self, name, value):
        owner = None if name.startswith("_implementation") else _owner(name)
        if owner is not None:
            previous = getattr(owner, name)
            history = self.__dict__.setdefault("_forwarded_previous", {})
            history.setdefault(name, []).append((owner, previous))
            setattr(owner, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name):
        history = self.__dict__.get("_forwarded_previous", {})
        values = history.get(name, [])
        if name != "_implementation" and values:
            owner, previous = values.pop()
            setattr(owner, name, previous)
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
