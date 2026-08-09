"""Shared paths and platform-specific constants.

Separated from `config` so modules that just need the config directory
don't have to import the whole persistence layer.

Portable mode (F43): if ``portable.txt`` exists next to the main script
or frozen exe, all config/data goes into a ``data/`` subdirectory alongside
the executable instead of ``%APPDATA%\\StreamKeep``.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

# Windows-only: hide console windows that subprocess would otherwise spawn
_CREATE_NO_WINDOW = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)

# FFmpeg safety flags for trusted local post-processing and staging. Local
# concat/filter workflows intentionally need ``file`` and occasionally
# ``pipe``; remote downloads use the stricter policy below.
FFMPEG_LOCAL_SAFETY = [
    "-nostdin",
    "-protocol_whitelist", "file,pipe,http,https,tcp,tls,crypto",
]
FFMPEG_SAFETY = FFMPEG_LOCAL_SAFETY

# Remote manifests are untrusted documents. They may reference only network
# transports, and every connection is additionally forced through the
# address-validating proxy in ``net_guard.GuardedHTTPProxy``.
# ``-tls_verify 1`` is stated rather than inherited. FFmpeg 8.x defaults it to
# 0 (`TLS_VERIFY_DEFAULT 0` in n8.1.2's libavformat/tls.h) and FFmpeg 9.0
# hardcodes it to 1, so a build swap on the user's PATH would otherwise decide
# silently whether remote certificates are checked at all. Stating it means
# both majors verify, and the only way to skip verification is the explicit
# per-source opt-in on a raw capture.
FFMPEG_REMOTE_INPUT_SAFETY = [
    "-tls_verify", "1",
    "-protocol_whitelist", "http,https,httpproxy,tcp,tls,crypto",
    "-protocol_blacklist", "file,pipe,concat,concatf,subfile,unix,data",
]
FFMPEG_REMOTE_SAFETY = ["-nostdin", *FFMPEG_REMOTE_INPUT_SAFETY]

# Twitch SSAI filtering stages a generated media playlist locally while its
# segment/key/map URLs remain remote and are still forced through the guarded
# child environment. Only the generated file protocol is added to the remote
# allow-list; shell-like and local discovery protocols remain blocked.
FFMPEG_FILTERED_HLS_INPUT_SAFETY = [
    "-tls_verify", "1",
    "-protocol_whitelist", "file,http,https,httpproxy,tcp,tls,crypto",
    "-protocol_blacklist", "pipe,concat,concatf,subfile,unix,data",
]

# Raw-protocol capture jobs intentionally support a wider, explicit FFmpeg
# input set than remote HTTP manifests. These sources are operator-selected
# camera/listener/radio endpoints, never URLs discovered inside untrusted
# manifests; the list still excludes local-file and shell-like protocols.
FFMPEG_RAW_CAPTURE_SAFETY = [
    "-nostdin",
    "-protocol_whitelist",
    "http,https,httpproxy,rtsp,rtmp,rtmps,srt,udp,rtp,tcp,tls,crypto",
    "-protocol_blacklist",
    "file,pipe,concat,concatf,subfile,unix,data",
]

# ── Portable mode detection (F43) ──────────────────────────────────
# Check for a ``portable.txt`` marker next to the exe/script.
_exe_dir = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.argv[0] or "."))).resolve()
# For PyInstaller one-file builds, _MEIPASS is a temp dir — use the exe's
# actual location instead.
if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).resolve().parent
PORTABLE = (_exe_dir / "portable.txt").is_file()

def _default_config_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "StreamKeep"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "StreamKeep"
    # Linux/BSD — XDG Base Directory spec
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    xdg_dir = base / "StreamKeep"
    # Auto-migrate legacy ~/StreamKeep/ if the XDG path doesn't exist yet
    legacy = Path.home() / "StreamKeep"
    if not xdg_dir.exists() and legacy.is_dir():
        try:
            legacy.rename(xdg_dir)
        except OSError:
            return legacy
    return xdg_dir


if PORTABLE:
    CONFIG_DIR = _exe_dir / "data"
else:
    CONFIG_DIR = _default_config_dir()

CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "streamkeep.log"
LOG_FILE_BACKUP = CONFIG_DIR / "streamkeep.log.1"
LOG_FILE_MAX_BYTES = 2_000_000  # rotate at ~2 MB
CRASH_LOG = CONFIG_DIR / "crash.log"
SERVER_REQUEST_LOG = CONFIG_DIR / "server-requests.jsonl"
SERVER_REQUEST_LOG_MAX_BYTES = 2_000_000
SERVER_REQUEST_LOG_BACKUP_COUNT = 3


def bind_config_dir(path):
    """Bind every config-derived path before stateful modules are imported.

    CLI entry points call this immediately after argument parsing.  Modules
    such as :mod:`streamkeep.config` and :mod:`streamkeep.db` intentionally
    capture these values at import time, so rebinding must happen before those
    imports rather than mutating only ``CONFIG_DIR`` later.
    """
    global CONFIG_DIR, CONFIG_FILE, LOG_FILE, LOG_FILE_BACKUP, CRASH_LOG
    global SERVER_REQUEST_LOG

    config_dir = Path(path).expanduser().resolve()
    CONFIG_DIR = config_dir
    CONFIG_FILE = config_dir / "config.json"
    LOG_FILE = config_dir / "streamkeep.log"
    LOG_FILE_BACKUP = config_dir / "streamkeep.log.1"
    CRASH_LOG = config_dir / "crash.log"
    SERVER_REQUEST_LOG = config_dir / "server-requests.jsonl"
    return config_dir


def source_archive_path(source_url, *, create=True):
    """Return the private stable yt-dlp archive path for a source URL."""
    from .utils import canonical_webpage_url

    canonical = canonical_webpage_url(source_url)
    identity = str(canonical or source_url or "").strip().encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    archive_dir = CONFIG_DIR / "download-archives"
    if create:
        archive_dir.mkdir(parents=True, exist_ok=True)
    return str(archive_dir / f"{digest}.txt")
