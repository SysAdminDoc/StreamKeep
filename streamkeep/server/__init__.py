"""Browser-companion server package.

``_legacy`` remains the compatibility implementation for this structural
move.  New route, policy, and asset code belongs in the sibling modules;
``streamkeep.local_server`` remains the public facade for existing callers.
"""

from . import _legacy

__all__ = ["_legacy"]
