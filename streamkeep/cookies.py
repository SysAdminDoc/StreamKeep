"""Browser cookie import — extract cookies to Netscape cookies.txt (F47).

Supports Chrome, Firefox, Edge, Brave, Chromium, Vivaldi, LibreWolf.
Uses ``rookiepy`` (preferred) or ``browser_cookie3`` for decryption.
Falls back to manual cookies.txt import.

The exported file lives at ``%APPDATA%/StreamKeep/cookies.txt`` and is
referenced by ``http._build_curl_cmd()`` and ``DownloadWorker`` (yt-dlp
``--cookies``).
"""

import os
import sys
import time
from pathlib import Path

from .paths import CONFIG_DIR


def _restrict_file_permissions(path):
    """Set owner-only permissions on POSIX systems."""
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

COOKIES_FILE = CONFIG_DIR / "cookies.txt"

# Domains we care about — filter to reduce file size and surface area
PLATFORM_DOMAINS = {
    ".twitch.tv", ".kick.com", ".youtube.com", ".google.com",
    ".rumble.com", ".soundcloud.com", ".reddit.com",
}


def cookies_file_path():
    """Return the path to the Netscape cookies.txt, or '' if none exists."""
    if COOKIES_FILE.is_file() and COOKIES_FILE.stat().st_size > 0:
        return str(COOKIES_FILE)
    return ""


def cookies_file_age_secs():
    """Return seconds since the cookies file was last written, or -1."""
    return int(cookie_file_metadata().get("age_secs", -1))


def cookie_file_metadata(path=None):
    """Return safe provenance and freshness metadata for a Netscape jar.

    The exporter writes its source into a comment rather than into cookie
    values. Reading only that bounded header keeps the Settings surface useful
    without exposing authentication material or the file contents.
    """
    destination = Path(path) if path else COOKIES_FILE
    result = {
        "path": str(destination),
        "exists": False,
        "size": 0,
        "age_secs": -1,
        "updated_at": 0.0,
        "source": "manual file import",
    }
    try:
        stat = destination.stat()
    except (OSError, ValueError):
        return result
    result.update({
        "exists": bool(destination.is_file() and stat.st_size > 0),
        "size": int(stat.st_size),
        "updated_at": float(stat.st_mtime),
        "age_secs": max(0, int(time.time() - stat.st_mtime)),
    })
    if not result["exists"]:
        return result
    try:
        with destination.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(12):
                line = handle.readline()
                if not line:
                    break
                marker = "# exported by streamkeep from "
                if line.casefold().startswith(marker):
                    source = _sanitize_cookie_field(line[len(marker):].strip())
                    if source:
                        result["source"] = source[:80]
                    break
    except (OSError, UnicodeError):
        pass
    return result


_BROWSER_LABELS = {
    "chrome": "Google Chrome",
    "chromium": "Chromium",
    "edge": "Microsoft Edge",
    "brave": "Brave",
    "vivaldi": "Vivaldi",
    "opera": "Opera",
    "opera_gx": "Opera GX",
    "firefox": "Firefox",
    "librewolf": "LibreWolf",
    "safari": "Safari",
}

# Chromium bound its cookie encryption key to the browser process in 127, so a
# third-party reader gets a decryption failure rather than a permission error.
# The wording differs per loader, hence matching on substrings.
_APP_BOUND_MARKERS = (
    "app-bound", "app bound", "dpapi", "decrypt", "cryptunprotect",
    "v20", "elevation service",
)
_LOCKED_MARKERS = (
    "being used by another process", "locked", "database is locked",
    "permission denied", "access is denied", "sharing violation",
)
_MISSING_MARKERS = (
    "no such file", "cannot find the file", "does not exist",
    "no cookies file", "could not find",
)


def _browser_label(browser_name):
    key = str(browser_name or "").strip().lower()
    return _BROWSER_LABELS.get(key, key or "the selected browser")


def describe_cookie_read_failure(browser_name, error):
    """Return a sentence naming the browser, the file, and the remedy.

    The loader exception used to be discarded, so a locked or app-bound
    encrypted cookie store produced "No cookie loader found" — a diagnosis
    that was both wrong and unactionable, and the single highest-reaction
    open complaint of this kind upstream (yt-dlp #7271).
    """
    label = _browser_label(browser_name)
    text = f"{type(error).__name__}: {error}".lower()
    path = getattr(error, "filename", "") or ""
    where = f" ({path})" if path else ""
    detail = str(error).strip()

    if isinstance(error, PermissionError) or any(
        marker in text for marker in _LOCKED_MARKERS
    ):
        return (
            f"{label}'s cookie store{where} could not be read because it is "
            f"locked. Close {label} completely (check the tray/background "
            "processes) and import again, or export a cookies.txt from the "
            f"browser and import the file instead. [{detail}]"
        )
    if any(marker in text for marker in _APP_BOUND_MARKERS):
        return (
            f"{label}'s cookies{where} are encrypted with a key bound to the "
            "browser itself, so StreamKeep cannot decrypt them. Export a "
            "cookies.txt from the browser (a cookie-export extension writes "
            f"one) and import the file instead. [{detail}]"
        )
    if isinstance(error, FileNotFoundError) or any(
        marker in text for marker in _MISSING_MARKERS
    ):
        return (
            f"No {label} cookie store was found{where}. Check that {label} is "
            "installed and that you have signed in with the default profile, "
            f"or import an exported cookies.txt instead. [{detail}]"
        )
    return (
        f"Could not read {label}'s cookies{where}: {detail}. Close {label} and "
        "try again, or export a cookies.txt from the browser and import the "
        "file instead."
    )


def import_from_browser(browser_name, *, domains=None, target=None):
    """Extract cookies from *browser_name* and write a Netscape jar.

    *browser_name* is one of the yt-dlp-style names: chrome, firefox,
    edge, brave, chromium, vivaldi, opera. *domains* narrows the cookie
    filter (defaults to the built-in platform set) and *target* writes to
    a specific jar instead of the shared file, which is how site-bound
    authentication profiles keep their material separate.

    Returns ``(ok, message)`` tuple.
    """
    wanted = set(domains) if domains else set(PLATFORM_DOMAINS)
    cj = None
    failures = []
    loader_available = False

    # Prefer rookiepy — lighter, better maintained
    try:
        import rookiepy
    except Exception as error:
        failures.append(("rookiepy", error, True))
    else:
        load_fn = getattr(rookiepy, browser_name, None)
        if load_fn is None:
            failures.append(("rookiepy", None, True))
        else:
            loader_available = True
            try:
                cj = load_fn(domains=list(wanted))
            except Exception as error:
                failures.append(("rookiepy", error, False))
                cj = None

    # Fallback to browser_cookie3
    if cj is None:
        try:
            import browser_cookie3 as bc3
        except Exception as error:
            failures.append(("browser_cookie3", error, True))
        else:
            load_fn = getattr(bc3, browser_name, None)
            if load_fn is None:
                failures.append(("browser_cookie3", None, True))
            else:
                loader_available = True
                try:
                    jar = load_fn()
                    cj = [
                        {
                            "domain": c.domain,
                            "name": c.name,
                            "value": c.value,
                            "path": c.path or "/",
                            "expires": int(c.expires or 0),
                            "secure": bool(c.secure),
                            "http_only": c.has_nonstandard_attr("httponly") if hasattr(c, "has_nonstandard_attr") else False,
                        }
                        for c in jar
                        if any(c.domain.endswith(d) or d.endswith(c.domain) for d in wanted)
                    ]
                except Exception as error:
                    failures.append(("browser_cookie3", error, False))
                    cj = None

    if cj is None:
        # "No loader found" is only true when no loader could even be asked.
        # Reporting it for a locked or encrypted store told the user to install
        # a package they already have, and hid the actual remedy.
        read_errors = [
            (loader, error) for loader, error, unavailable in failures
            if not unavailable and error is not None
        ]
        if read_errors:
            return False, describe_cookie_read_failure(
                browser_name, read_errors[0][1],
            )
        if not loader_available:
            return False, (
                f"No cookie loader found for '{browser_name}'. "
                "Install rookiepy (`pip install rookiepy`) or browser_cookie3."
            )
        return False, (
            f"{_browser_label(browser_name)} returned no cookies. Sign in to "
            "the site in that browser, then import again — or export a "
            "cookies.txt and import the file."
        )

    return _write_cookies(cj, browser_name, target=target)


def refresh_from_browser(browser_name, *, domains=None, target=None):
    """Explicit refresh action used by Settings and automation callers."""
    return import_from_browser(browser_name, domains=domains, target=target)


def import_from_file(source_path):
    """Copy a Netscape cookies.txt file into the config dir.

    Returns ``(ok, message)``.
    """
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return False, f"Failed to read {source_path}: {e}"

    # Basic validation — Netscape format starts with comments or domain lines
    lines = [ln for ln in content.strip().splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        return False, "File appears empty (no cookie lines found)."
    # Check that lines have ~7 tab-separated fields
    valid = sum(1 for ln in lines if len(ln.split("\t")) >= 6)
    if valid < 1:
        return False, "File doesn't look like Netscape cookies.txt format (expected tab-separated fields)."

    ok, message = restore_cookie_text(content)
    return (True, f"Imported {valid} cookie(s) from file.") if ok else (ok, message)


def clear_cookies():
    """Delete the cookies.txt file."""
    try:
        if COOKIES_FILE.exists():
            COOKIES_FILE.unlink()
        return True, "Cookies cleared."
    except OSError as e:
        return False, f"Failed to clear cookies: {e}"


def _write_cookies(cookie_list, source, *, target=None):
    """Write a list of cookie dicts to a Netscape cookie jar."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    source_label = _sanitize_cookie_field(_browser_label(source))[:80] or "unknown source"
    lines = ["# Netscape HTTP Cookie File",
             f"# Exported by StreamKeep from {source_label}",
             ""]
    count = 0

    for c in cookie_list:
        # rookiepy returns dicts; browser_cookie3 returns our own dicts
        domain = c.get("domain", "") if isinstance(c, dict) else ""
        if not domain:
            continue

        domain = _sanitize_cookie_field(domain)
        name = _sanitize_cookie_field(c.get("name", ""))
        value = _sanitize_cookie_field(c.get("value", ""))
        path = _sanitize_cookie_field(c.get("path", "/") or "/")
        if not domain or not name:
            continue
        expires = int(c.get("expires", 0) or 0)
        secure = "TRUE" if c.get("secure", False) else "FALSE"

        # Netscape format: domain  include_subdomains  path  secure  expires  name  value
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append(f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
        count += 1

    if count == 0:
        return False, f"No relevant cookies found in {source_label} for supported platforms."

    try:
        _write_cookie_text_atomic("\n".join(lines) + "\n", target=target)
    except OSError as e:
        return False, f"Failed to write cookies: {e}"

    return True, f"Exported {count} cookie(s) from {source}."


def _sanitize_cookie_field(value):
    """Strip control characters that would corrupt Netscape cookie rows."""
    # Remove NUL bytes and all C0 control chars (0x00-0x1F) except space
    cleaned = str(value or "")
    cleaned = "".join(c if c >= " " or c == "\t" else " " for c in cleaned)
    cleaned = cleaned.replace("\t", " ")
    return " ".join(cleaned.split())


def export_cookie_text():
    """Return cookie content for an explicit encrypted portable backup."""
    try:
        return COOKIES_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def restore_cookie_text(content):
    """Validate and atomically restore Netscape cookie text."""
    content = str(content or "")
    if not content:
        return True, "No cookies in backup."
    if len(content.encode("utf-8")) > 10 * 1024 * 1024:
        return False, "Cookie payload exceeds 10 MB."
    lines = [
        line for line in content.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not lines or not any(len(line.split("\t")) >= 6 for line in lines):
        return False, "Cookie payload is not Netscape format."
    try:
        _write_cookie_text_atomic(content)
        return True, f"Restored {len(lines)} cookie row(s)."
    except OSError as error:
        return False, f"Failed to restore cookies: {error}"


def _write_cookie_text_atomic(content, *, target=None):
    destination = Path(target) if target else COOKIES_FILE
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(".txt.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, destination)
    _restrict_file_permissions(destination)
