"""Dependency auto-install. Must run before any PyQt6 imports.

Kept as a standalone module so `__main__.py` can call `bootstrap()`
before importing anything that depends on PyQt6.
"""

import sys
import subprocess


def bootstrap():
    """Auto-install dependencies on first run."""
    required = {"PyQt6": "PyQt6"}
    optional = {"yt_dlp": "yt-dlp", "deno": "deno", "playwright": "playwright"}
    import importlib

    for mod, pkg in required.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            for cmd in (
                [sys.executable, "-m", "pip", "install", pkg],
                [sys.executable, "-m", "pip", "install", "--user", pkg],
                [sys.executable, "-m", "pip", "install", "--break-system-packages", pkg],
            ):
                try:
                    subprocess.check_call(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    break
                except Exception:
                    continue

    for mod, pkg in optional.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", pkg],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass  # optional deps — graceful fallback
