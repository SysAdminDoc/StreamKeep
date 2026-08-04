"""``streamkeep://`` protocol handler, bookmarklet, and OS registration (V23).

Lets a browser or shortcut hand a page/media URL to StreamKeep with one
click. The URI is parsed into a validated download request; the browser
integration is a self-contained bookmarklet (no extension required); and the
scheme is registered per-user through Windows classes, Linux XDG metadata, or
macOS LaunchServices (reversible, no elevation). URI parsing and each
platform's registration plan are separated from OS mutation so they can be
unit-tested.
"""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.parse


PROTOCOL_SCHEME = "streamkeep"
_MAX_URI_LEN = 8192
LINUX_MIME_TYPE = "x-scheme-handler/streamkeep"
LINUX_DESKTOP_ID = "streamkeep.desktop"
MACOS_BUNDLE_ID = "com.github.SysAdminDoc.StreamKeep"


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


def _atomic_write(path, content, mode=0o644):
    """Write *content* beside *path*, then replace it without partial files."""
    path = Path(path)
    payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _run_command(command):
    """Run a registration helper without invoking a shell."""
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except OSError as error:
        return False, str(error)
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or "").strip()
    return False, detail or f"exit code {result.returncode}"


def linux_protocol_desktop_path():
    """Return the per-user XDG desktop-entry path for Linux."""
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return root / "applications" / LINUX_DESKTOP_ID


def linux_protocol_desktop_entry(command=None):
    """Return a user desktop entry that passes a URI as the ``%u`` field."""
    command = command or _default_launch_command()
    command = command.replace("%1", "%u")
    if "%u" not in command:
        command += " %u"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=StreamKeep\n"
        "Comment=Download VODs, live streams, and podcasts\n"
        f"Exec={command}\n"
        "Icon=streamkeep\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        f"MimeType={LINUX_MIME_TYPE};\n"
        "Categories=AudioVideo;Video;Network;\n"
    )


def register_linux_protocol(command=None):
    """Install a per-user Linux desktop entry and ``xdg-mime`` association."""
    if not sys.platform.startswith("linux"):
        return False, "Protocol registration is only supported on Linux."
    path = linux_protocol_desktop_path()
    previous = path.read_bytes() if path.is_file() else None
    try:
        _atomic_write(path, linux_protocol_desktop_entry(command))
    except OSError as error:
        return False, f"Could not write the Linux streamkeep:// handler: {error}"

    ok, detail = _run_command([
        "xdg-mime", "install", "--mode", "user", "--novendor", str(path),
    ])
    if ok:
        ok, detail = _run_command([
            "xdg-mime", "default", path.name, LINUX_MIME_TYPE,
        ])
    if not ok:
        try:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, previous)
        except OSError:
            pass
        return False, f"Could not associate streamkeep:// with xdg-mime: {detail}"
    return True, "Registered streamkeep:// for the current Linux user."


def unregister_linux_protocol():
    """Remove the per-user Linux desktop entry and MIME association."""
    if not sys.platform.startswith("linux"):
        return False, "Protocol registration is only supported on Linux."
    path = linux_protocol_desktop_path()
    if not path.exists():
        return True, "No Linux streamkeep:// handler was registered."

    ok, detail = _run_command([
        "xdg-mime", "uninstall", "--mode", "user", str(path),
    ])
    try:
        path.unlink()
    except OSError as error:
        return False, f"Could not remove the Linux streamkeep:// handler: {error}"
    if not ok:
        return False, f"Could not remove the xdg-mime association: {detail}"
    return True, "Removed the Linux streamkeep:// handler."


def macos_protocol_bundle_path():
    """Return the per-user macOS application bundle used by LaunchServices."""
    return (
        Path.home() / "Library" / "Application Support" / "StreamKeep"
        / "StreamKeep.app"
    )


def _launch_argv():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    launcher = Path(__file__).resolve().parent.parent / "StreamKeep.py"
    return [sys.executable, str(launcher)]


def macos_protocol_info_plist():
    """Return the CFBundle metadata that claims the ``streamkeep`` URL scheme."""
    return {
        "CFBundleDisplayName": "StreamKeep",
        "CFBundleExecutable": "streamkeep",
        "CFBundleIdentifier": MACOS_BUNDLE_ID,
        "CFBundleName": "StreamKeep",
        "CFBundlePackageType": "APPL",
        "CFBundleURLTypes": [{
            "CFBundleURLName": "StreamKeep URL handler",
            "CFBundleURLSchemes": [PROTOCOL_SCHEME],
        }],
    }


def macos_protocol_launcher(command=None):
    """Return the POSIX launcher script stored inside the protocol bundle."""
    argv = list(command or _launch_argv())
    quoted = " ".join(shlex.quote(str(value)) for value in argv)
    return f"#!/bin/sh\nexec {quoted} \"$@\"\n"


def _macos_lsregister():
    candidates = (
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
        "LaunchServices.framework/Support/lsregister",
        "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/"
        "LaunchServices.framework/Support/lsregister",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("lsregister")


def register_macos_protocol(command=None):
    """Install and register a per-user macOS CFBundleURLTypes handler."""
    if sys.platform != "darwin":
        return False, "Protocol registration is only supported on macOS."
    bundle = macos_protocol_bundle_path()
    contents = bundle / "Contents"
    try:
        _atomic_write(contents / "Info.plist", plistlib.dumps(macos_protocol_info_plist()))
        _atomic_write(
            contents / "MacOS" / "streamkeep",
            macos_protocol_launcher(command),
            mode=0o755,
        )
    except OSError as error:
        return False, f"Could not write the macOS streamkeep:// bundle: {error}"

    lsregister = _macos_lsregister()
    if not lsregister:
        return False, "Could not register streamkeep://: LaunchServices is unavailable."
    ok, detail = _run_command([lsregister, "-f", str(bundle)])
    if not ok:
        return False, f"Could not register streamkeep:// with LaunchServices: {detail}"
    return True, "Registered streamkeep:// for the current macOS user."


def unregister_macos_protocol():
    """Unregister and remove the per-user macOS URL-handler bundle."""
    if sys.platform != "darwin":
        return False, "Protocol registration is only supported on macOS."
    bundle = macos_protocol_bundle_path()
    if not bundle.exists():
        return True, "No macOS streamkeep:// handler was registered."
    lsregister = _macos_lsregister()
    if not lsregister:
        return False, "Could not remove streamkeep://: LaunchServices is unavailable."
    ok, detail = _run_command([lsregister, "-u", str(bundle)])
    if not ok:
        return False, f"Could not unregister streamkeep:// from LaunchServices: {detail}"
    try:
        shutil.rmtree(bundle)
    except OSError as error:
        return False, f"Could not remove the macOS streamkeep:// bundle: {error}"
    return True, "Removed the macOS streamkeep:// handler."


def register_protocol(command=None):
    """Register the current user's handler on a supported desktop platform."""
    if sys.platform == "win32":
        return register_windows_protocol(command)
    if sys.platform.startswith("linux"):
        return register_linux_protocol(command)
    if sys.platform == "darwin":
        return register_macos_protocol(command)
    return False, f"Protocol registration is not supported on {sys.platform}."


def unregister_protocol():
    """Remove the current user's handler on a supported desktop platform."""
    if sys.platform == "win32":
        return unregister_windows_protocol()
    if sys.platform.startswith("linux"):
        return unregister_linux_protocol()
    if sys.platform == "darwin":
        return unregister_macos_protocol()
    return False, f"Protocol registration is not supported on {sys.platform}."


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
