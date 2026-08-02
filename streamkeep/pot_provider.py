"""YouTube PO-token provider lifecycle (V33).

yt-dlp only *uses* a PO-token provider; it cannot start one. StreamKeep already
detected whether a provider plugin was importable but could not tell the
operator whether the provider was actually answering, nor point yt-dlp at it.
This module closes that loop:

* probe the local provider endpoint (loopback only, never a remote host),
* inject its ``base_url`` extractor argument into every YouTube job when it is
  answering, and
* offer one explicit "set up provider" action that either installs and launches
  a local sidecar or hands back copy-paste steps.

Nothing here installs anything on its own. Frozen builds refuse to install
entirely — a packaged exe must have its dependencies baked in, and shelling out
to ``sys.executable -m pip`` inside PyInstaller re-runs the app itself.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from .paths import _CREATE_NO_WINDOW

# bgutil's documented default. Only loopback is ever accepted: a PO-token
# provider handles account-bound tokens, so pointing it at a remote host would
# hand those to a third party.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4416
DEFAULT_BASE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"

CONFIG_BASE_URL_KEY = "youtube_pot_base_url"
CONFIG_COMMAND_KEY = "youtube_pot_server_command"

PROVIDER_PACKAGE = "bgutil-ytdlp-pot-provider"
INSTALL_COMMAND = f"python -m pip install -U {PROVIDER_PACKAGE}"
SERVER_HINT = (
    "docker run --name bgutil-provider -d -p 4416:4416 "
    "brainicism/bgutil-ytdlp-pot-provider"
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_sidecar_lock = threading.Lock()
_sidecar_proc: subprocess.Popen | None = None


def normalize_base_url(value="") -> str:
    """Return a loopback provider base URL, or '' when the value is unusable.

    A non-loopback host is rejected rather than silently downgraded, so a
    mistyped or hostile config value can never send tokens off the machine.
    """
    text = str(value or "").strip().rstrip("/")
    if not text:
        return DEFAULT_BASE_URL
    if "://" not in text:
        text = f"http://{text}"
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        return ""
    port = parts.port or DEFAULT_PORT
    return f"{parts.scheme}://{parts.hostname}:{port}"


def base_url_from_config(config=None) -> str:
    """Return the configured provider base URL, falling back to the default."""
    configured = (config or {}).get(CONFIG_BASE_URL_KEY, "")
    return normalize_base_url(configured)


def probe_provider(base_url="", *, timeout=1.0) -> tuple[bool, str]:
    """Return whether the provider endpoint accepts a connection.

    A TCP connect only — no request is sent, so probing cannot consume a token
    or perturb the provider. Loopback-only, so this is safe offline.
    """
    url = normalize_base_url(base_url)
    if not url:
        return False, "The PO-token provider URL must be a loopback address."
    parts = urlsplit(url)
    host = parts.hostname or DEFAULT_HOST
    port = parts.port or DEFAULT_PORT
    try:
        with socket.create_connection((host, port), timeout=max(0.1, float(timeout))):
            return True, f"PO-token provider is answering on {url}."
    except OSError:
        return False, f"No PO-token provider is answering on {url}."


def provider_extractor_args(base_url="", *, url=None) -> list[str]:
    """Return the yt-dlp extractor argument that points at the provider.

    Empty when the URL is not YouTube or the base URL is not a usable loopback
    address, so a caller can splice this into any command unconditionally.
    """
    if url is not None:
        from .extractors.ytdlp import _is_youtube_url
        if not _is_youtube_url(url):
            return []
    normalized = normalize_base_url(base_url)
    if not normalized:
        return []
    return [
        "--extractor-args",
        f"youtube:getpot_bgutil_baseurl={normalized}",
    ]


def provider_status(config=None, *, probe=True, timeout=1.0) -> dict:
    """Report the full provider picture: plugin, endpoint, and usability."""
    from .extractors.ytdlp import youtube_pot_provider_status

    plugin = youtube_pot_provider_status()
    base_url = base_url_from_config(config)
    reachable, detail = (False, "")
    if probe and base_url:
        reachable, detail = probe_provider(base_url, timeout=timeout)
    elif not base_url:
        detail = "The configured PO-token provider URL is not a loopback address."
    return {
        "plugin_installed": bool(plugin.get("available")),
        "plugin": str(plugin.get("provider", "") or ""),
        "base_url": base_url,
        "reachable": bool(reachable),
        "sidecar_running": sidecar_running(),
        "detail": detail or str(plugin.get("detail", "") or ""),
        "usable": bool(plugin.get("available")) and bool(reachable),
    }


# Command builders run per job; a fresh socket probe on each one would add up
# to a second of latency per resolve when nothing is listening.
_STATUS_TTL = 60.0
_status_cache: dict = {"at": 0.0, "key": None, "value": None}


def cached_status(config=None, *, refresh=False, timeout=0.5) -> dict:
    """Return ``provider_status`` behind a short time-to-live cache."""
    import time

    key = base_url_from_config(config)
    now = time.monotonic()
    with _sidecar_lock:
        fresh = (
            not refresh
            and _status_cache["value"] is not None
            and _status_cache["key"] == key
            and (now - _status_cache["at"]) < _STATUS_TTL
        )
        if fresh:
            return dict(_status_cache["value"])
    value = provider_status(config, timeout=timeout)
    with _sidecar_lock:
        _status_cache.update({"at": now, "key": key, "value": value})
    return dict(value)


def invalidate_status_cache() -> None:
    """Drop the cached probe so the next job re-checks the provider."""
    with _sidecar_lock:
        _status_cache.update({"at": 0.0, "key": None, "value": None})


def active_extractor_args(url=None, config=None) -> list[str]:
    """Return the provider extractor-arg for a YouTube job, or [].

    Injected into every YouTube command. Returns nothing unless a provider
    plugin is installed *and* the endpoint is answering, so absence degrades to
    exactly the previous behaviour.
    """
    try:
        if url is not None:
            from .extractors.ytdlp import _is_youtube_url
            if not _is_youtube_url(url):
                return []
        if config is None:
            from .config import load_config
            config = load_config()
        status = cached_status(config)
        if not status.get("usable"):
            return []
        return provider_extractor_args(status["base_url"])
    except Exception:
        # A provider is an optimization; it must never break a download.
        return []


def setup_steps(config=None) -> list[str]:
    """Return copy-paste setup instructions for the current machine state."""
    status = provider_status(config)
    steps: list[str] = []
    if not status["plugin_installed"]:
        steps.append("Install the yt-dlp PO-token provider plugin:")
        steps.append(f"    {INSTALL_COMMAND}")
    if not status["reachable"]:
        steps.append(
            f"Start the provider server so it answers on {status['base_url'] or DEFAULT_BASE_URL}:"
        )
        steps.append(f"    {SERVER_HINT}")
        steps.append(
            "Or set a launch command in Settings "
            f"({CONFIG_COMMAND_KEY}) and use \"Set up provider\"."
        )
    if not steps:
        steps.append(
            "A PO-token provider is installed and answering; YouTube jobs "
            "already use it."
        )
    return steps


def can_install_locally() -> bool:
    """Return whether installing the plugin from inside the app is allowed.

    Never in a frozen build: ``sys.executable`` is the app itself there, so a
    pip call would re-launch StreamKeep instead of installing anything.
    """
    return not (getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))


def install_plugin(log_fn=None, *, timeout=300) -> tuple[bool, str]:
    """Install the provider plugin with pip. Explicit user action only."""
    if not can_install_locally():
        return False, (
            "This packaged build cannot install plugins. Install "
            f"{PROVIDER_PACKAGE} into a Python environment on PATH instead."
        )
    cmd = [sys.executable, "-m", "pip", "install", "-U", PROVIDER_PACKAGE]
    if callable(log_fn):
        log_fn(f"[POT] {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=max(10, int(timeout)),
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"Could not run pip: {error}"
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        return False, f"Install failed: {' '.join(tail)}"
    return True, f"Installed {PROVIDER_PACKAGE}."


def server_command(config=None) -> list[str]:
    """Return the configured provider launch argv, or [] when unset.

    Only an explicit operator-configured command is ever run; StreamKeep does
    not guess at a server binary.
    """
    import shlex

    raw = str((config or {}).get(CONFIG_COMMAND_KEY, "") or "").strip()
    if not raw:
        return []
    try:
        parts = shlex.split(raw, posix=os.name != "nt")
    except ValueError:
        return []
    if not parts:
        return []
    resolved = shutil.which(parts[0])
    if not resolved:
        return []
    return [resolved, *parts[1:]]


def sidecar_running() -> bool:
    """Return whether this process is currently supervising a provider."""
    with _sidecar_lock:
        return _sidecar_proc is not None and _sidecar_proc.poll() is None


def launch_sidecar(config=None, log_fn=None) -> tuple[bool, str]:
    """Start the configured provider server as a supervised child process."""
    global _sidecar_proc

    if sidecar_running():
        return True, "A PO-token provider is already running."
    reachable, _detail = probe_provider(base_url_from_config(config))
    if reachable:
        return True, "A PO-token provider is already answering."
    cmd = server_command(config)
    if not cmd:
        return False, (
            "No PO-token provider launch command is configured. Set "
            f"{CONFIG_COMMAND_KEY} in Settings, or start the server yourself: "
            f"{SERVER_HINT}"
        )
    if callable(log_fn):
        log_fn(f"[POT] Launching provider: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, ValueError) as error:
        return False, f"Could not start the PO-token provider: {error}"
    with _sidecar_lock:
        _sidecar_proc = proc
    return True, "Started the PO-token provider."


def stop_sidecar(timeout=5) -> bool:
    """Stop a provider this process started. Never touches a foreign server."""
    global _sidecar_proc

    with _sidecar_lock:
        proc = _sidecar_proc
        _sidecar_proc = None
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.terminate()
        proc.wait(timeout=max(1, int(timeout)))
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass
    return True


def ensure_provider(config=None, log_fn=None) -> tuple[bool, str]:
    """One explicit "set up PO-token provider" action.

    Installs the plugin when that is possible, launches a configured server,
    then re-probes. Returns ``(usable, message)``; when it cannot get there it
    says exactly what the operator has to do instead.
    """
    messages = []
    status = provider_status(config)
    if not status["plugin_installed"] and can_install_locally():
        ok, message = install_plugin(log_fn=log_fn)
        messages.append(message)
        if not ok:
            return False, " ".join(messages)
    if not status["reachable"]:
        ok, message = launch_sidecar(config, log_fn=log_fn)
        messages.append(message)
    final = provider_status(config)
    if final["usable"]:
        messages.append(f"YouTube jobs will use {final['base_url']}.")
        return True, " ".join(m for m in messages if m)
    messages.extend(setup_steps(config))
    return False, " ".join(m for m in messages if m)
