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


class _DatabaseFacade(ModuleType):
    """Forward the legacy module surface while retaining patch compatibility."""

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
_facade.__class__ = _DatabaseFacade
_facade._implementation = _implementation
