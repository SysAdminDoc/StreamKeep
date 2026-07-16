"""``streamkeep://`` protocol handler, bookmarklet, and OS registration (V23).

Lets a browser or shortcut hand a page/media URL to StreamKeep with one
click. The URI is parsed into a validated download request; the browser
integration is a self-contained bookmarklet (no extension required); and on
Windows the scheme is registered per-user under ``HKCU\\Software\\Classes``
(reversible, no elevation). URI parsing and the registry plan are separated
from any OS mutation so they can be unit-tested.
"""

from __future__ import annotations

import sys
import urllib.parse


PROTOCOL_SCHEME = "streamkeep"
_MAX_URI_LEN = 8192


def is_protocol_uri(text):
    """True when ``text`` looks like a ``streamkeep:`` URI."""
    if not isinstance(text, str):
        return False
    return text.strip().lower().startswith(PROTOCOL_SCHEME + ":")


def _validate_inner_url(url):
    """Return a safe HTTP(S) target URL, or raise ValueError."""
    url = str(url or "").strip()
    if not url:
        raise ValueError("streamkeep URI carried no target URL")
    if len(url) > _MAX_URI_LEN:
        raise ValueError("streamkeep URI target is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("streamkeep URI target contains control characters")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError("streamkeep URI target must be an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("streamkeep URI target cannot embed credentials")
    return url


def parse_streamkeep_uri(uri):
    """Parse a ``streamkeep:`` URI into a download request.

    Accepted forms (the inner URL may be percent-encoded)::

        streamkeep://download?url=<URL>&quality=best
        streamkeep://download/<URL>
        streamkeep://<URL>
        streamkeep:<URL>

    Returns ``{"action": "download", "url": <str>, "quality": <str>}``.
    Raises ``ValueError`` on anything that is not a safe HTTP(S) target.
    """
    if not is_protocol_uri(uri):
        raise ValueError("Not a streamkeep:// URI")
    raw = uri.strip()
    if len(raw) > _MAX_URI_LEN:
        raise ValueError("streamkeep URI is too long")

    body = raw[len(PROTOCOL_SCHEME) + 1:]  # strip "streamkeep:"
    body = body.lstrip("/")  # tolerate "//" authority form

    quality = ""
    inner = ""
    # "download?url=..." / "download/..." host-action form
    lower = body.lower()
    if lower.startswith("download"):
        rest = body[len("download"):]
        if rest.startswith("?"):
            params = urllib.parse.parse_qs(rest[1:], keep_blank_values=False)
            inner = (params.get("url", [""])[0] or "").strip()
            quality = (params.get("quality", [""])[0] or "").strip()
        elif rest.startswith("/"):
            inner = rest[1:].strip()
        elif rest == "":
            raise ValueError("streamkeep://download carried no target URL")
        else:
            inner = rest.strip()
    else:
        # Bare-URL form: everything after the scheme is the target.
        inner = body.strip()

    # The URL may itself be percent-encoded (bookmarklet uses encodeURIComponent).
    if inner and "%" in inner and "://" not in inner:
        inner = urllib.parse.unquote(inner)
    url = _validate_inner_url(inner)

    quality = quality.strip().lower()
    if quality and quality not in ("best", "worst", "audio"):
        # Unknown quality hints are dropped rather than rejected — the URL is
        # what matters; the quality is advisory.
        quality = ""
    return {"action": "download", "url": url, "quality": quality}


def build_bookmarklet():
    """Return a ``javascript:`` bookmarklet that hands the page to StreamKeep."""
    return (
        "javascript:(function(){location.href='%s://download?url='"
        "+encodeURIComponent(location.href);})();" % PROTOCOL_SCHEME
    )


def _default_launch_command():
    """Return the argv template used to launch StreamKeep for a URI.

    Uses the frozen executable when present, otherwise the interpreter plus
    launcher script. The ``%1`` placeholder is substituted by the OS.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    from pathlib import Path
    launcher = Path(__file__).resolve().parent.parent / "StreamKeep.py"
    return f'"{sys.executable}" "{launcher}" "%1"'


def windows_protocol_registry_plan(command=None):
    """Return the ordered ``(subkey, value_name, value)`` registry writes.

    Values are placed under ``HKCU\\Software\\Classes\\streamkeep``. Kept
    pure so the exact registration can be asserted without touching the
    registry.
    """
    command = command or _default_launch_command()
    root = "Software\\Classes\\" + PROTOCOL_SCHEME
    return [
        (root, "", f"URL:{PROTOCOL_SCHEME} Protocol"),
        (root, "URL Protocol", ""),
        (root + "\\shell\\open\\command", "", command),
    ]


def register_windows_protocol(command=None):
    """Register the per-user ``streamkeep://`` handler on Windows.

    Reversible and non-elevated (writes under HKCU). Returns
    ``(ok, message)``.
    """
    if sys.platform != "win32":
        return False, "Protocol registration is only supported on Windows."
    import winreg

    plan = windows_protocol_registry_plan(command)
    try:
        for subkey, name, value in plan:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    except OSError as error:
        return False, f"Could not register streamkeep:// handler: {error}"
    return True, "Registered streamkeep:// for the current user."


def unregister_windows_protocol():
    """Remove the per-user ``streamkeep://`` handler on Windows."""
    if sys.platform != "win32":
        return False, "Protocol registration is only supported on Windows."
    import winreg

    root = "Software\\Classes\\" + PROTOCOL_SCHEME
    # Delete deepest keys first.
    subkeys = [
        root + "\\shell\\open\\command",
        root + "\\shell\\open",
        root + "\\shell",
        root,
    ]
    removed = False
    for subkey in subkeys:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
            removed = True
        except FileNotFoundError:
            continue
        except OSError as error:
            return False, f"Could not remove streamkeep:// handler: {error}"
    if removed:
        return True, "Removed the streamkeep:// handler."
    return True, "No streamkeep:// handler was registered."


def is_windows_protocol_registered():
    """True when a per-user ``streamkeep://`` command is registered."""
    if sys.platform != "win32":
        return False
    import winreg

    subkey = "Software\\Classes\\" + PROTOCOL_SCHEME + "\\shell\\open\\command"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            value, _type = winreg.QueryValueEx(key, "")
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False
