"""Named, site-bound authentication profiles (V50).

A profile is an opaque ID plus an operator label, a declared set of hosts and
platforms it is allowed to authenticate, and its own cookie material. Jobs,
rules, and monitors persist only the ID; the cookie file never leaves this
module's directory and is never copied into a job, a log line, a backup, a
diagnostics bundle, or a config export.

The central safety property is **no cross-site fallback**: resolving a profile
for a URL returns nothing unless the URL's host (or the job's platform) is
inside that profile's declared scope. A profile bound to ``youtube.com`` can
never be attached to a Twitch request, even if it is the only profile that
exists and even if a caller names it explicitly.

Layout under the profile directory::

    auth/profiles.json          metadata only (no secrets)
    auth/<profile_id>.txt       Netscape cookie jar, owner-only permissions
"""

from __future__ import annotations

import json
import os
import re
import secrets as _secrets
import shutil
import sys
import threading
import time
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from .paths import CONFIG_DIR

AUTH_DIR = CONFIG_DIR / "auth"
INDEX_FILE = AUTH_DIR / "profiles.json"
INDEX_VERSION = 1
DEFAULT_PROFILE_NAME = "Default"

_write_lock = threading.RLock()
_ID_RE = re.compile(r"^ap_[0-9a-f]{16}$")
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


class AuthProfileError(ValueError):
    """Raised for an invalid profile definition or an unknown profile."""


@dataclass(frozen=True)
class AuthProfile:
    """One named credential scope. Contains no secret material."""

    profile_id: str
    name: str = ""
    hosts: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    source: str = ""          # "browser" | "file" | ""
    browser: str = ""         # yt-dlp browser name when source == "browser"
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "hosts": list(self.hosts),
            "platforms": list(self.platforms),
            "source": self.source,
            "browser": self.browser,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data) -> "AuthProfile":
        data = dict(data or {})
        return cls(
            profile_id=str(data.get("profile_id", "") or ""),
            name=str(data.get("name", "") or ""),
            hosts=normalize_hosts(data.get("hosts")),
            platforms=normalize_platforms(data.get("platforms")),
            source=str(data.get("source", "") or ""),
            browser=str(data.get("browser", "") or ""),
            created_at=float(data.get("created_at", 0) or 0),
            updated_at=float(data.get("updated_at", 0) or 0),
        )


# ── Normalization ───────────────────────────────────────────────────

def normalize_host(value) -> str:
    """Return a bare lowercase host, or '' when the value is not a host.

    Accepts a bare host, a leading-dot cookie domain, or a full URL so an
    operator can paste whatever they have in front of them.
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        try:
            text = urlsplit(text).hostname or ""
        except ValueError:
            return ""
    text = text.strip().strip(".")
    if text.startswith("*."):
        text = text[2:]
    if text.startswith("www."):
        text = text[4:]
    text = text.split("/", 1)[0].split(":", 1)[0]
    if not text or not _HOST_RE.match(text):
        return ""
    return text


def normalize_hosts(values) -> tuple[str, ...]:
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values)
    seen: list[str] = []
    for value in values or ():
        host = normalize_host(value)
        if host and host not in seen:
            seen.append(host)
    return tuple(sorted(seen))


def normalize_platforms(values) -> tuple[str, ...]:
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values)
    seen: list[str] = []
    for value in values or ():
        text = str(value or "").strip().lower()
        if text and text not in seen:
            seen.append(text)
    return tuple(sorted(seen))


def host_in_scope(host: str, allowed: tuple[str, ...]) -> bool:
    """Return whether *host* is the allowed host or a subdomain of it.

    Suffix matching is label-aware, so ``evil-youtube.com`` never matches an
    allowed ``youtube.com``.
    """
    host = normalize_host(host)
    if not host:
        return False
    for candidate in allowed:
        if host == candidate or host.endswith("." + candidate):
            return True
    return False


# ── Storage ─────────────────────────────────────────────────────────

def _restrict(path) -> None:
    """Best-effort owner-only permissions for credential material."""
    if sys.platform == "win32":
        return
    try:
        os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)
    except OSError:
        pass


def _ensure_dir():
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    _restrict(AUTH_DIR)
    return AUTH_DIR


def _read_index() -> dict:
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"version": INDEX_VERSION, "profiles": []}
    if not isinstance(data, dict):
        return {"version": INDEX_VERSION, "profiles": []}
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        profiles = []
    return {"version": INDEX_VERSION, "profiles": profiles}


def _write_index(index: dict) -> None:
    _ensure_dir()
    tmp = INDEX_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    _restrict(tmp)
    os.replace(tmp, INDEX_FILE)
    _restrict(INDEX_FILE)


def list_profiles() -> list[AuthProfile]:
    """Return every stored profile, oldest first. Never raises."""
    with _write_lock:
        index = _read_index()
    profiles = []
    for entry in index["profiles"]:
        try:
            profile = AuthProfile.from_dict(entry)
        except (TypeError, ValueError):
            continue
        if _ID_RE.match(profile.profile_id):
            profiles.append(profile)
    return sorted(profiles, key=lambda p: (p.created_at, p.profile_id))


def get_profile(profile_id) -> AuthProfile | None:
    """Return one profile by opaque ID, or ``None``."""
    wanted = str(profile_id or "").strip()
    if not wanted:
        return None
    for profile in list_profiles():
        if profile.profile_id == wanted:
            return profile
    return None


def find_profile(name_or_id) -> AuthProfile | None:
    """Resolve a profile by opaque ID or by exact, case-insensitive name."""
    text = str(name_or_id or "").strip()
    if not text:
        return None
    profile = get_profile(text)
    if profile is not None:
        return profile
    lowered = text.casefold()
    for candidate in list_profiles():
        if candidate.name.casefold() == lowered:
            return candidate
    return None


def cookies_path(profile_id) -> str:
    """Return the profile's cookie-jar path, or '' when it holds no material."""
    wanted = str(profile_id or "").strip()
    if not _ID_RE.match(wanted):
        return ""
    path = AUTH_DIR / f"{wanted}.txt"
    try:
        if path.is_file() and path.stat().st_size > 0:
            return str(path)
    except OSError:
        return ""
    return ""


def cookies_age_secs(profile_id) -> int:
    """Return seconds since the profile's cookies were written, or -1."""
    path = cookies_path(profile_id)
    if not path:
        return -1
    try:
        return int(time.time() - os.stat(path).st_mtime)
    except (OSError, ValueError):
        return -1


def create_profile(
    name, hosts=(), platforms=(), *, source="", browser="",
) -> AuthProfile:
    """Create and persist a new, empty profile.

    A profile must declare at least one host or platform; an unscoped profile
    would be exactly the global-cookie-file problem this replaces.
    """
    label = str(name or "").strip()
    if not label:
        raise AuthProfileError("A profile needs a name")
    scoped_hosts = normalize_hosts(hosts)
    scoped_platforms = normalize_platforms(platforms)
    if not scoped_hosts and not scoped_platforms:
        raise AuthProfileError(
            "A profile must declare at least one allowed host or platform"
        )
    now = time.time()
    with _write_lock:
        index = _read_index()
        existing = {
            str(entry.get("name", "")).casefold()
            for entry in index["profiles"]
            if isinstance(entry, dict)
        }
        if label.casefold() in existing:
            raise AuthProfileError(f"A profile named {label!r} already exists")
        profile = AuthProfile(
            profile_id=f"ap_{_secrets.token_hex(8)}",
            name=label,
            hosts=scoped_hosts,
            platforms=scoped_platforms,
            source=str(source or ""),
            browser=str(browser or ""),
            created_at=now,
            updated_at=now,
        )
        index["profiles"].append(profile.to_dict())
        _write_index(index)
    return profile


def update_profile(profile_id, **changes) -> AuthProfile:
    """Update a profile's label or scope. Its ID and material do not move."""
    with _write_lock:
        index = _read_index()
        for position, entry in enumerate(index["profiles"]):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("profile_id", "")) != str(profile_id):
                continue
            profile = AuthProfile.from_dict(entry)
            if "name" in changes:
                label = str(changes["name"] or "").strip()
                if not label:
                    raise AuthProfileError("A profile needs a name")
                profile = replace(profile, name=label)
            if "hosts" in changes:
                profile = replace(profile, hosts=normalize_hosts(changes["hosts"]))
            if "platforms" in changes:
                profile = replace(
                    profile, platforms=normalize_platforms(changes["platforms"]),
                )
            if "source" in changes:
                profile = replace(profile, source=str(changes["source"] or ""))
            if "browser" in changes:
                profile = replace(profile, browser=str(changes["browser"] or ""))
            if not profile.hosts and not profile.platforms:
                raise AuthProfileError(
                    "A profile must declare at least one allowed host or platform"
                )
            profile = replace(profile, updated_at=time.time())
            index["profiles"][position] = profile.to_dict()
            _write_index(index)
            return profile
    raise AuthProfileError(f"Unknown authentication profile {profile_id!r}")


def delete_profile(profile_id) -> bool:
    """Remove a profile and shred its cookie material."""
    wanted = str(profile_id or "").strip()
    if not _ID_RE.match(wanted):
        return False
    with _write_lock:
        index = _read_index()
        remaining = [
            entry for entry in index["profiles"]
            if isinstance(entry, dict)
            and str(entry.get("profile_id", "")) != wanted
        ]
        if len(remaining) == len(index["profiles"]):
            return False
        index["profiles"] = remaining
        _write_index(index)
        path = AUTH_DIR / f"{wanted}.txt"
        try:
            path.unlink()
        except OSError:
            pass
    return True


# ── Credential material ─────────────────────────────────────────────

def _store_cookie_text(profile_id: str, text: str) -> tuple[bool, str]:
    if not _ID_RE.match(str(profile_id or "")):
        return False, "Unknown authentication profile"
    _ensure_dir()
    path = AUTH_DIR / f"{profile_id}.txt"
    tmp = path.with_suffix(".txt.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        _restrict(tmp)
        os.replace(tmp, path)
        _restrict(path)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False, f"Could not write profile cookies: {e}"
    return True, "Stored profile cookies"


def import_from_browser(profile_id, browser_name) -> tuple[bool, str]:
    """Import a browser's cookies into one profile, scoped to its hosts."""
    from . import cookies as _cookies

    profile = get_profile(profile_id)
    if profile is None:
        return False, "Unknown authentication profile"
    _ensure_dir()
    target = AUTH_DIR / f"{profile.profile_id}.txt"
    ok, message = _cookies.import_from_browser(
        browser_name, domains=_cookie_domains(profile), target=target,
    )
    if not ok:
        return ok, message
    _restrict(target)
    update_profile(
        profile.profile_id, source="browser", browser=str(browser_name or ""),
    )
    return True, message


def import_from_file(profile_id, source_path) -> tuple[bool, str]:
    """Copy an existing Netscape cookies.txt into one profile."""
    profile = get_profile(profile_id)
    if profile is None:
        return False, "Unknown authentication profile"
    try:
        text = open(source_path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        return False, f"Could not read cookie file: {e}"
    if "# Netscape HTTP Cookie File" not in text and "\t" not in text:
        return False, "Not a Netscape-format cookies.txt"
    ok, message = _store_cookie_text(profile.profile_id, text)
    if ok:
        update_profile(profile.profile_id, source="file", browser="")
    return ok, message


def clear_credentials(profile_id) -> bool:
    """Drop a profile's cookie material, keeping the profile itself."""
    wanted = str(profile_id or "").strip()
    if not _ID_RE.match(wanted):
        return False
    try:
        (AUTH_DIR / f"{wanted}.txt").unlink()
    except OSError:
        return False
    return True


def _cookie_domains(profile: AuthProfile) -> set[str]:
    """Return the cookie-domain filter implied by a profile's host scope."""
    domains = set()
    for host in profile.hosts:
        domains.add(f".{host}")
        domains.add(host)
    return domains


# ── Resolution ──────────────────────────────────────────────────────

def profile_covers(profile: AuthProfile, url="", platform="") -> bool:
    """Return whether a profile is allowed to authenticate this request."""
    if profile is None:
        return False
    host = ""
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        host = ""
    if host and host_in_scope(host, profile.hosts):
        return True
    name = str(platform or "").strip().lower()
    if name and name in profile.platforms:
        return True
    return False


def resolve_profile(url="", platform="", *, profile_id="") -> AuthProfile | None:
    """Return the profile that may authenticate this request, or ``None``.

    When *profile_id* names a profile, that profile is used only if its scope
    covers the request — a named profile is never a licence to send credentials
    to a different site. When no ID is given, exactly one covering profile must
    exist; an ambiguous match sends no credentials rather than guessing.
    """
    named = str(profile_id or "").strip()
    if named:
        profile = find_profile(named)
        if profile is None or not profile_covers(profile, url, platform):
            return None
        return profile
    matches = [
        profile for profile in list_profiles()
        if profile_covers(profile, url, platform)
    ]
    return matches[0] if len(matches) == 1 else None


def resolve_cookies_path(url="", platform="", *, profile_id="") -> str:
    """Return the cookie jar allowed for this request, or ''."""
    profile = resolve_profile(url, platform, profile_id=profile_id)
    if profile is None:
        return ""
    return cookies_path(profile.profile_id)


# ── Presentation and redaction ──────────────────────────────────────

def public_view(profile: AuthProfile) -> dict:
    """Project a profile for logs, diagnostics, exports, and the REST API."""
    return {
        "profile_id": profile.profile_id,
        "name": profile.name,
        "hosts": list(profile.hosts),
        "platforms": list(profile.platforms),
        "source": profile.source,
        "browser": profile.browser,
        "has_credentials": bool(cookies_path(profile.profile_id)),
        "age_secs": cookies_age_secs(profile.profile_id),
    }


def describe(profile_id) -> str:
    """Return an operator label for a profile ID, safe to log."""
    profile = get_profile(profile_id)
    if profile is None:
        return ""
    return f"{profile.name} ({profile.profile_id})"


# ── Migration ───────────────────────────────────────────────────────

MIGRATED_KEY = "auth_profiles_migrated"


def migrate_global_cookies(config) -> AuthProfile | None:
    """Move the legacy global cookie setting into an explicit profile.

    The cookie file is *moved*, not copied, so credential material is never
    duplicated across the profile directory and the legacy path. Returns the
    created profile, or ``None`` when there was nothing to migrate.
    """
    from . import cookies as _cookies

    if config is None:
        config = {}
    if config.get(MIGRATED_KEY):
        return None
    legacy_path = _cookies.cookies_file_path()
    legacy_browser = str(config.get("cookies_browser", "") or "")
    if not legacy_path and not legacy_browser:
        config[MIGRATED_KEY] = True
        return None
    if list_profiles():
        # An operator already created profiles; do not invent another one.
        config[MIGRATED_KEY] = True
        return None

    hosts = _hosts_from_cookie_file(legacy_path)
    if not hosts:
        hosts = tuple(
            sorted({
                domain.lstrip(".") for domain in _cookies.PLATFORM_DOMAINS
            })
        )
    try:
        profile = create_profile(
            DEFAULT_PROFILE_NAME,
            hosts=hosts,
            source="browser" if legacy_browser else "file",
            browser=legacy_browser,
        )
    except AuthProfileError:
        return None
    if legacy_path:
        _ensure_dir()
        target = AUTH_DIR / f"{profile.profile_id}.txt"
        try:
            shutil.move(legacy_path, target)
            _restrict(target)
        except OSError:
            pass
    config[MIGRATED_KEY] = True
    config["cookies_browser"] = ""
    config["cookies_file"] = ""
    config["auth_profile_id"] = profile.profile_id
    return profile


def ensure_migrated():
    """Run the one-time legacy-cookie migration and persist the result.

    Idempotent and safe to call from every entry point (desktop, CLI, headless);
    after the first run the config flag short-circuits it.
    """
    from .config import load_config, save_config

    config = load_config()
    if config.get(MIGRATED_KEY):
        return None
    try:
        profile = migrate_global_cookies(config)
    except OSError:
        return None
    save_config(config)
    return profile


def _hosts_from_cookie_file(path) -> tuple[str, ...]:
    """Derive the host scope of a legacy jar from the domains it contains."""
    if not path:
        return ()
    hosts: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                host = normalize_host(parts[0])
                if host and host not in hosts:
                    hosts.append(host)
    except OSError:
        return ()
    return tuple(sorted(hosts))
