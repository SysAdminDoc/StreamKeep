"""GitHub-release auto-update checker.

Hits the releases API once per launch (when enabled), compares semver
against the running `VERSION`, and surfaces a banner in the Download tab
if something newer is available.

Self-replace on Windows: download the new `StreamKeep.exe` next to the
current one as `StreamKeep.exe.new`, then spawn a tiny batch file that
waits for the current process to exit, renames the new exe into place,
and relaunches. A one-deep backup `StreamKeep.exe.old` is kept so a
corrupted update is recoverable.

Strictly opt-in: the check only runs when the user has ticked the
"Check for updates on startup" box in Settings, and an available update
is never installed without an explicit confirm click.

Network call runs on a background QThread so the UI never blocks.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

RELEASES_URL = "https://api.github.com/repos/SysAdminDoc/StreamKeep/releases/latest"
USER_AGENT = "StreamKeep-updater"


def _coerce_nonnegative_int(value, default=0):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return max(0, int(default or 0))


def _parse_semver(s):
    """Parse 'v4.15.0' / '4.15.0-rc1' into (4, 15, 0) ignoring any
    pre-release tail. Returns (0, 0, 0) on anything unparseable — the
    caller treats that as "equal or older" to avoid false positives."""
    if not s:
        return (0, 0, 0)
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", str(s).strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(m.group(i)) for i in (1, 2, 3))


def is_newer(remote, local):
    return _parse_semver(remote) > _parse_semver(local)


class UpdateCheckWorker(QThread):
    """Hits the GitHub releases API. Never raises — emits an empty
    result on error so the UI code can be simple."""

    result = pyqtSignal(dict)   # {available, tag, notes, asset_url, asset_size}

    def __init__(self, current_version):
        super().__init__()
        self.current_version = current_version

    def run(self):
        payload = {
            "available": False,
            "tag": "",
            "notes": "",
            "asset_url": "",
            "asset_size": 0,
        }
        try:
            req = urllib.request.Request(
                RELEASES_URL,
                headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError, ValueError):
            self.result.emit(payload)
            return
        tag = str(data.get("tag_name", "") or "")
        if not is_newer(tag, self.current_version):
            self.result.emit(payload)
            return
        # Find a Windows .exe asset.
        asset_url = ""
        asset_size = 0
        for asset in data.get("assets") or []:
            name = str(asset.get("name", "") or "")
            if name.lower().endswith(".exe"):
                asset_url = str(asset.get("browser_download_url", "") or "")
                try:
                    asset_size = int(asset.get("size", 0) or 0)
                except (TypeError, ValueError):
                    asset_size = 0
                break
        payload.update({
            "available": True,
            "tag": tag,
            "notes": (str(data.get("body", "") or "")).strip(),
            "asset_url": asset_url,
            "asset_size": asset_size,
        })
        self.result.emit(payload)


class DownloadUpdateWorker(QThread):
    """Downloads the attached exe to `<exe>.new` next to the running
    process, verifies size, then arms a self-replace batch (Windows) or
    shell script (POSIX) that the UI invokes on user confirm."""

    progress = pyqtSignal(int, str)     # percent, status
    done = pyqtSignal(bool, str)        # success, error_or_path

    def __init__(self, asset_url, expected_size):
        super().__init__()
        self.asset_url = asset_url
        self.expected_size = _coerce_nonnegative_int(expected_size)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _target_path(self):
        """Only meaningful in a frozen build — where `sys.executable`
        actually is StreamKeep.exe. In a source checkout the update
        path is "git pull" territory, not our concern."""
        return sys.executable

    def run(self):
        exe_path = self._target_path()
        if not exe_path or not os.path.exists(exe_path):
            self.done.emit(False, "Running executable path not found.")
            return
        if not str(self.asset_url or "").strip():
            self.done.emit(False, "Update asset URL was missing.")
            return
        frozen = bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))
        if not frozen:
            self.done.emit(False, "Auto-update only supported for the packaged exe.")
            return
        new_path = exe_path + ".new"
        cancelled = False
        try:
            req = urllib.request.Request(
                self.asset_url,
                headers={"User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(new_path, "wb") as out:
                total = self.expected_size or _coerce_nonnegative_int(
                    resp.headers.get("Content-Length", 0)
                )
                written = 0
                chunk = 64 * 1024
                while True:
                    if self._cancel:
                        cancelled = True
                        break
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    out.write(buf)
                    written += len(buf)
                    if total > 0:
                        pct = min(99, int((written / total) * 100))
                        self.progress.emit(pct, f"{written // 1024} KB / {total // 1024} KB")
                    else:
                        self.progress.emit(0, f"{written // 1024} KB")
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError) as e:
            try:
                if os.path.exists(new_path):
                    os.remove(new_path)
            except OSError:
                pass
            self.done.emit(False, f"Download failed: {e}")
            return
        if cancelled:
            try:
                if os.path.exists(new_path):
                    os.remove(new_path)
            except OSError:
                pass
            self.done.emit(False, "Download cancelled.")
            return
        # Size sanity check — if GitHub served an HTML error page we'd
        # otherwise install a corrupt binary.
        if (self.expected_size > 0
                and os.path.getsize(new_path) < int(self.expected_size * 0.9)):
            try:
                os.remove(new_path)
            except OSError:
                pass
            self.done.emit(False, "Downloaded file is smaller than expected.")
            return
        self.progress.emit(100, "Downloaded")
        self.done.emit(True, new_path)


def arm_self_replace(new_exe_path):
    """Spawn a detached Windows .bat (or POSIX sh) that waits for the
    parent process to exit, renames the new exe into place with a
    one-deep backup, and relaunches. Returns True on success.

    The batch detaches via START so terminating this process doesn't
    kill it. Uses `ping -n 3` as a portable 2-second sleep on Windows.
    """
    target = sys.executable
    if not target or not os.path.exists(new_exe_path):
        return False
    backup = target + ".old"
    if os.name == "nt":
        script = target + ".update.bat"
        try:
            with open(script, "w", encoding="utf-8") as f:
                f.write(
                    "@echo off\r\n"
                    "ping -n 3 127.0.0.1 >nul\r\n"
                    f'if exist "{backup}" del /f /q "{backup}"\r\n'
                    f'move /y "{target}" "{backup}" >nul\r\n'
                    f'move /y "{new_exe_path}" "{target}" >nul\r\n'
                    f'start "" "{target}"\r\n'
                    'del "%~f0"\r\n'
                )
            subprocess.Popen(
                ["cmd", "/c", "start", "", "/min", script],
                creationflags=0x00000008,   # DETACHED_PROCESS
                close_fds=True,
            )
            return True
        except OSError:
            return False
    # POSIX fallback — not the shipping target today, but doesn't hurt.
    script = target + ".update.sh"
    try:
        with open(script, "w", encoding="utf-8") as f:
            f.write(
                "#!/bin/sh\nsleep 2\n"
                f'rm -f "{backup}"\n'
                f'mv "{target}" "{backup}" || exit 1\n'
                f'mv "{new_exe_path}" "{target}" || exit 1\n'
                f'chmod +x "{target}"\n'
                f'"{target}" &\n'
                'rm -- "$0"\n'
            )
        os.chmod(script, 0o755)
        subprocess.Popen(["/bin/sh", script], close_fds=True)
        return True
    except OSError:
        return False
