"""Side-effect-free startup dependency check.

Startup never installs or upgrades packages. Source users get repair guidance
from the runtime capability registry and dependency manifest; frozen builds are
expected to contain their complete Python dependency set.
"""

import importlib
import sys


def _is_frozen():
    return getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")


def bootstrap(include_optional=True):
    """Report import availability without mutating the Python environment."""
    modules = {
        "PyQt6": {"required": True, "repair": "Install requirements.txt."},
    }
    if include_optional:
        modules.update({
            "yt_dlp": {
                "required": False,
                "repair": 'Install a supported "yt-dlp[default]" release.',
            },
            "send2trash": {"required": False, "repair": "Install send2trash."},
            "websocket": {"required": False, "repair": "Install websocket-client."},
            "keyring": {"required": False, "repair": "Install keyring."},
        })
    status = {}
    for module, metadata in modules.items():
        try:
            importlib.import_module(module)
            available = True
        except ImportError:
            available = False
        status[module] = {**metadata, "available": available}
    return status
