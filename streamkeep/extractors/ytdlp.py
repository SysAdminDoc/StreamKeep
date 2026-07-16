"""yt-dlp catch-all fallback extractor.

Handles anything the native extractors don't. Includes:
- Auto-scan of installed browsers for cookies on auth errors
- Volume Shadow Copy bypass for locked Chromium cookie DBs
- --flat-playlist probe for channel/playlist expansion
- Video+audio pairing via ytdlp_direct format spec
"""

import json
import importlib.util
import logging
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

from ..http import http_interrupted, run_capture_interruptible
from ..models import QualityInfo, StreamInfo
from ..utils import fmt_duration, scan_browser_cookies
from .base import Extractor

logger = logging.getLogger(__name__)


_YTDLP_INSTALL_HINT = 'Install or update with: pip install -U "yt-dlp[default]"'
_JS_RUNTIME_HINT = "Install Deno 2.3+ or Node.js 22+ for full YouTube support."


def _parse_version_parts(text):
    match = re.search(r"v?(\d+(?:[.\-]\d+){1,3})", str(text or ""))
    if not match:
        return (), ""
    version = match.group(1).replace("-", ".")
    parts = []
    for part in version.split(".")[:4]:
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts), version


def _version_at_least(parts, minimum):
    if not parts:
        return False
    max_len = max(len(parts), len(minimum))
    padded = tuple(parts) + (0,) * (max_len - len(parts))
    min_padded = tuple(minimum) + (0,) * (max_len - len(minimum))
    return padded >= min_padded


def _is_youtube_url(url):
    try:
        host = urllib.parse.urlparse(str(url or "").strip()).netloc.lower()
    except Exception:
        return False
    return host == "youtu.be" or host.endswith(".youtu.be") or host == "youtube.com" or host.endswith(".youtube.com")


def _has_python_module(module_name):
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def ytdlp_command():
    """Return the bundled yt-dlp command prefix for this runtime.

    A windowed one-file PyInstaller executable cannot use ``python -m``
    because ``sys.executable`` points back to StreamKeep itself.  Its hidden
    internal mode re-enters the executable and dispatches directly to the
    bundled ``yt_dlp`` module.  Source installs use the same module through
    the active Python interpreter and retain the external CLI as a fallback.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--internal-ytdlp"]
    if _has_python_module("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    return ["yt-dlp"]


def _bundled_ytdlp_version():
    """Return the bundled module version without spawning a second app."""
    try:
        from yt_dlp.version import __version__
        return str(__version__ or "").strip()
    except (ImportError, AttributeError):
        return ""


def _probe_command_version(command, args=None):
    result = run_capture_interruptible([command] + list(args or ["--version"]), timeout=5)
    if result.interrupted:
        return {
            "name": command,
            "available": False,
            "supported": False,
            "version": "",
            "message": "Runtime probe was interrupted.",
        }
    if result.returncode != 0 or result.timed_out:
        return {
            "name": command,
            "available": False,
            "supported": False,
            "version": "",
            "message": (result.stderr or result.error or "not found").strip(),
        }
    text = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    parts, version = _parse_version_parts(text)
    return {
        "name": command,
        "available": True,
        "supported": True,
        "version": version,
        "parts": parts,
        "raw": text,
        "message": "",
    }


def _probe_js_runtime():
    candidates = [
        ("deno", ("deno",), (2, 3, 0), None),
        ("node", ("node", "nodejs"), (22, 0, 0), None),
        ("quickjs", ("qjs",), (2023, 12, 9), None),
        ("bun", ("bun",), (1, 2, 11), (1, 3, 14)),
    ]
    unsupported = []
    for name, commands, minimum, maximum in candidates:
        for command in commands:
            probed = _probe_command_version(command)
            if not probed.get("available"):
                continue
            parts = tuple(probed.get("parts") or ())
            if name == "quickjs" and "quickjs-ng" in probed.get("raw", "").lower():
                supported = True
            else:
                supported = _version_at_least(parts, minimum)
            if maximum and parts and parts > maximum:
                supported = False
            probed.update({
                "name": name,
                "command": command,
                "supported": supported,
                "minimum": ".".join(str(x) for x in minimum),
            })
            if supported:
                if name == "bun":
                    probed["message"] = "Bun is deprecated by yt-dlp; prefer Deno or Node.js."
                return probed
            unsupported.append(probed)
    if unsupported:
        first = unsupported[0]
        return {
            "name": first.get("name", ""),
            "command": first.get("command", ""),
            "available": True,
            "supported": False,
            "version": first.get("version", ""),
            "message": (
                f"{first.get('name', 'JavaScript runtime')} {first.get('version', '')} "
                f"is below the supported minimum {first.get('minimum', '')}."
            ).strip(),
        }
    return {
        "name": "",
        "command": "",
        "available": False,
        "supported": False,
        "version": "",
        "message": _JS_RUNTIME_HINT,
    }


def ytdlp_runtime_status():
    """Return yt-dlp CLI, external-component, and JS-runtime readiness."""
    version = _bundled_ytdlp_version() if getattr(sys, "frozen", False) else ""
    if not version:
        result = run_capture_interruptible(ytdlp_command() + ["--version"], timeout=5)
        if result.interrupted:
            return {
                "state": "missing",
                "summary": "Interrupted",
                "detail": "yt-dlp readiness probe was interrupted.",
                "yt_dlp_version": "",
                "ejs_available": False,
                "js_runtime": _probe_js_runtime(),
                "problems": ["yt-dlp readiness probe was interrupted."],
            }
        if result.returncode != 0 or result.timed_out:
            return {
                "state": "missing",
                "summary": "Missing",
                "detail": f"yt-dlp was not found. {_YTDLP_INSTALL_HINT}.",
                "yt_dlp_version": "",
                "ejs_available": False,
                "js_runtime": _probe_js_runtime(),
                "problems": ["yt-dlp was not found."],
            }

        _parts, version = _parse_version_parts(result.stdout)
        version = version or result.stdout.strip().splitlines()[0][:32]
    yt_dlp_module = _has_python_module("yt_dlp")
    ejs_available = _has_python_module("yt_dlp_ejs") or not yt_dlp_module
    runtime = _probe_js_runtime()
    problems = []
    if not ejs_available:
        problems.append('yt-dlp-ejs is missing; install with pip install -U "yt-dlp[default]".')
    if not runtime.get("supported"):
        problems.append(runtime.get("message") or _JS_RUNTIME_HINT)

    if problems:
        return {
            "state": "limited",
            "summary": "Limited",
            "detail": f"yt-dlp {version} found. " + " ".join(problems),
            "yt_dlp_version": version,
            "ejs_available": ejs_available,
            "js_runtime": runtime,
            "problems": problems,
        }

    runtime_label = runtime.get("name") or "JavaScript runtime"
    runtime_version = runtime.get("version") or "available"
    return {
        "state": "ready",
        "summary": "Ready",
        "detail": f"yt-dlp {version} with yt-dlp-ejs and {runtime_label} {runtime_version}.",
        "yt_dlp_version": version,
        "ejs_available": ejs_available,
        "js_runtime": runtime,
        "problems": [],
    }


def ytdlp_runtime_args(status=None):
    status = status or ytdlp_runtime_status()
    runtime = status.get("js_runtime") or {}
    name = runtime.get("name", "")
    if not runtime.get("supported") or not name or name == "deno":
        return []
    return ["--js-runtimes", name]


def format_ytdlp_runtime_warning(status):
    if status.get("state") == "ready":
        return ""
    return "yt-dlp runtime support is limited: " + status.get("detail", "")


class YtDlpExtractor(Extractor):
    NAME = "yt-dlp"
    ICON = "Y"
    COLOR = "overlay1"
    URL_PATTERNS = [
        re.compile(r'https?://.+'),  # Catch-all — must register last
    ]
    # Set by Settings tab
    cookies_browser = ""
    cookies_file = ""
    rate_limit = ""
    proxy = ""
    download_subs = False
    sponsorblock = False

    def _has_ytdlp(self):
        if getattr(sys, "frozen", False):
            return bool(_bundled_ytdlp_version())
        result = run_capture_interruptible(ytdlp_command() + ["--version"], timeout=5)
        return result.returncode == 0

    def extract_channel_id(self, url):
        try:
            parsed = urllib.parse.urlparse(url.strip())
            parts = parsed.path.strip("/").split("/")
            if parts and parts[-1]:
                return f"{parsed.netloc}_{parts[-1]}"
            return parsed.netloc
        except Exception as e:
            logger.debug("extract_channel_id failed for %r: %s", url, e)
            return "download"

    def _build_cmd(self, url, include_runtime=False, runtime_status=None):
        cmd = ytdlp_command() + ["--dump-json", "--no-download"]
        if include_runtime and _is_youtube_url(url):
            cmd.extend(ytdlp_runtime_args(runtime_status))
        if self.cookies_file and os.path.isfile(self.cookies_file):
            cmd.extend(["--cookies", self.cookies_file])
        elif self.cookies_browser:
            cmd.extend(["--cookies-from-browser", self.cookies_browser])
        if self.proxy:
            cmd.extend(["--proxy", self.proxy])
        cmd.extend(["--", url])
        return cmd

    _AUTH_ERRORS = [
        "Sign in", "age", "confirm your age", "login", "cookies",
        "authentication", "members-only", "private video",
        "This video is available to this channel",
    ]

    def _is_auth_error(self, stderr):
        lower = stderr.lower()
        return any(phrase.lower() in lower for phrase in self._AUTH_ERRORS)

    def _try_with_browser(self, url, browser_name, log_fn=None, runtime_status=None):
        """Attempt yt-dlp extraction with a specific browser's cookies.
        Returns (data_dict, None) or (None, error_str)."""
        cmd = ytdlp_command() + ["--dump-json", "--no-download"]
        if log_fn and _is_youtube_url(url):
            cmd.extend(ytdlp_runtime_args(runtime_status))
        cmd.extend(["--cookies-from-browser", browser_name, "--", url])
        try:
            result = run_capture_interruptible(cmd, timeout=60)
            if result.interrupted:
                return None, "Interrupted"
            if result.timed_out:
                return None, "Timed out"
            if result.returncode == 0:
                return json.loads(result.stdout), None
            err = result.stderr.strip().split("\n")[-1] if result.stderr else "Unknown error"
            return None, err
        except json.JSONDecodeError:
            return None, "Bad JSON"
        except Exception as e:
            return None, str(e)

    def _copy_locked_cookie_db(self, cookie_db_path, log_fn=None):
        """Copy a locked Chromium cookie DB using Volume Shadow Copy (VSS).
        Requires a UAC admin elevation prompt. Returns the temp profile
        path, or None."""
        import tempfile
        import shutil
        try:
            parts = Path(cookie_db_path).parts
            user_data_idx = None
            for i, p in enumerate(parts):
                if p == "User Data":
                    user_data_idx = i
                    break
            if user_data_idx is None:
                return None

            user_data_dir = str(Path(*parts[:user_data_idx + 1]))
            local_state_path = os.path.join(user_data_dir, "Local State")
            if not os.path.exists(local_state_path):
                return None

            tmp_base = os.path.join(tempfile.gettempdir(), "streamkeep_cookies")
            try:
                if os.path.exists(tmp_base):
                    shutil.rmtree(tmp_base, ignore_errors=True)
                tmp_profile = os.path.join(tmp_base, "Default")
                tmp_network = os.path.join(tmp_profile, "Network")
                os.makedirs(tmp_network, exist_ok=True)
            except OSError as e:
                self._log(log_fn, f"  Cannot create temp profile dir: {e}")
                return None

            try:
                shutil.copy2(local_state_path, os.path.join(tmp_base, "Local State"))
            except OSError as e:
                self._log(log_fn, f"  Cannot copy Local State: {e}")
                return None

            if "Network" in cookie_db_path:
                dst_cookies = os.path.join(tmp_network, "Cookies")
            else:
                dst_cookies = os.path.join(tmp_profile, "Cookies")

            try:
                shutil.copy2(cookie_db_path, dst_cookies)
                self._log(log_fn, "  Copied cookie DB directly")
                return tmp_profile
            except (PermissionError, OSError):
                pass

            self._log(
                log_fn,
                "  Cookie DB locked — using Volume Shadow Copy (admin required)...",
            )

            drive = cookie_db_path[:3]  # e.g. "C:\"
            rel_path = cookie_db_path[3:]
            done_flag = os.path.join(tempfile.gettempdir(), "sk_copy_done")
            err_log = os.path.join(tempfile.gettempdir(), "sk_copy_err.txt")
            for f in [done_flag, err_log]:
                if os.path.exists(f):
                    os.remove(f)

            ps_file = os.path.join(tempfile.gettempdir(), "sk_vss.ps1")
            with open(ps_file, "w", encoding="utf-8") as f:
                lines = [
                    "try {",
                    f"  $s = (Get-WmiObject -List Win32_ShadowCopy).Create('{drive}','ClientAccessible')",
                    "  $sc = Get-WmiObject Win32_ShadowCopy | Sort-Object InstallDate -Descending | Select-Object -First 1",
                    "  $dev = $sc.DeviceObject",
                    '  $src = "$dev\\' + rel_path + '"',
                    '  Copy-Item -LiteralPath $src -Destination "' + dst_cookies + '" -Force',
                    "  $sc.Delete()",
                    '  "done" | Out-File "' + done_flag + '"',
                    "} catch {",
                    '  $_.Exception.Message | Out-File "' + err_log + '"',
                    '  "error" | Out-File "' + done_flag + '"',
                    "}",
                ]
                f.write("\n".join(lines))

            import ctypes
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "powershell.exe",
                f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ps_file}"',
                None, 0,
            )
            if ret <= 32:
                self._log(log_fn, "  UAC elevation denied")
                return None

            for _ in range(40):
                if http_interrupted():
                    return None
                time.sleep(0.5)
                if os.path.exists(done_flag):
                    break
            else:
                self._log(log_fn, "  VSS copy timed out")
                return None

            if os.path.exists(err_log):
                with open(err_log, encoding="utf-8") as f:
                    self._log(log_fn, f"  VSS error: {f.read().strip()}")
                return None

            if os.path.exists(dst_cookies) and os.path.getsize(dst_cookies) > 0:
                self._log(log_fn, "  Copied cookie DB via VSS shadow copy")
                return tmp_profile

            self._log(log_fn, "  VSS copy produced no output")
            return None

        except Exception as e:
            self._log(log_fn, f"  Cookie DB copy failed: {e}")
            return None

    def _find_cookie_db_path(self, ytdlp_name):
        """Find the actual Cookies file path for a browser."""
        local = os.environ.get("LOCALAPPDATA", "")
        roaming = os.environ.get("APPDATA", "")
        candidates = {
            "chrome": [
                os.path.join(local, "Google", "Chrome", "User Data", "Default", "Network", "Cookies"),
                os.path.join(local, "Google", "Chrome", "User Data", "Default", "Cookies"),
            ],
            "chromium": [
                os.path.join(local, "Chromium", "User Data", "Default", "Network", "Cookies"),
                os.path.join(local, "Chromium", "User Data", "Default", "Cookies"),
            ],
            "edge": [
                os.path.join(local, "Microsoft", "Edge", "User Data", "Default", "Network", "Cookies"),
                os.path.join(local, "Microsoft", "Edge", "User Data", "Default", "Cookies"),
            ],
            "brave": [
                os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Network", "Cookies"),
                os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Cookies"),
            ],
            "opera": [
                os.path.join(roaming, "Opera Software", "Opera Stable", "Network", "Cookies"),
                os.path.join(roaming, "Opera Software", "Opera Stable", "Cookies"),
            ],
            "vivaldi": [
                os.path.join(local, "Vivaldi", "User Data", "Default", "Network", "Cookies"),
                os.path.join(local, "Vivaldi", "User Data", "Default", "Cookies"),
            ],
        }
        for p in candidates.get(ytdlp_name, []):
            if os.path.exists(p):
                return p
        return None

    def _auto_retry_with_browsers(self, url, original_stderr, log_fn=None, runtime_status=None):
        """Scan for installed browsers and try each one's cookies until one works."""
        err_line = original_stderr.strip().split("\n")[-1] if original_stderr else ""
        self._log(log_fn, f"Auth required: {err_line}")
        self._log(log_fn, "Auto-scanning for browser cookies...")

        browsers = scan_browser_cookies()
        if not browsers:
            self._log(log_fn, "No browsers found on this system.")
            return None

        self._log(
            log_fn,
            f"Found {len(browsers)} browser(s) to try: "
            f"{', '.join(d for d, _, _ in browsers)}",
        )

        for display, ytdlp_name, path in browsers:
            if http_interrupted():
                return None
            self._log(log_fn, f"Trying cookies from {display} ({ytdlp_name})...")
            data, err = self._try_with_browser(url, ytdlp_name, log_fn, runtime_status)
            if http_interrupted():
                return None
            if data:
                self._log(log_fn, f"Success with {display}! Saving as default.")
                YtDlpExtractor.cookies_browser = ytdlp_name
                return data

            if err and "could not copy" in err.lower():
                self._log(log_fn, "  DB locked — copying with sqlite3 backup...")
                db_path = self._find_cookie_db_path(ytdlp_name)
                if db_path:
                    tmp_profile = self._copy_locked_cookie_db(db_path, log_fn)
                    if tmp_profile:
                        browser_arg = f"{ytdlp_name}:{tmp_profile}"
                        self._log(log_fn, "  Retrying with copied profile...")
                        data2, err2 = self._try_with_browser(url, browser_arg, log_fn, runtime_status)
                        if data2:
                            self._log(
                                log_fn,
                                f"Success with {display} (copied profile)! "
                                "Saving as default.",
                            )
                            YtDlpExtractor.cookies_browser = browser_arg
                            return data2
                        else:
                            self._log(log_fn, f"  {display} (copied) failed: {err2}")
            else:
                self._log(log_fn, f"  {display} failed: {err}")

        self._log(log_fn, "All browsers tried — none had valid cookies for this URL.")
        return None

    def list_playlist_entries(self, url, log_fn=None, limit=200):
        """Probe URL for a playlist/channel. If it's a list container,
        return a list of entries (empty list for single videos)."""
        has_ytdlp = self._has_ytdlp()
        if http_interrupted():
            return []
        if not has_ytdlp:
            return []
        self._log(log_fn, f"Probing for playlist/channel: {url}")
        cmd = ytdlp_command() + [
            "--flat-playlist", "--dump-single-json",
            "--playlist-end", str(limit), "--no-warnings",
        ]
        runtime_status = None
        if log_fn and _is_youtube_url(url):
            runtime_status = ytdlp_runtime_status()
            warning = format_ytdlp_runtime_warning(runtime_status)
            if warning:
                self._log(log_fn, warning)
            cmd.extend(ytdlp_runtime_args(runtime_status))
        if self.cookies_browser:
            cmd.extend(["--cookies-from-browser", self.cookies_browser])
        if self.proxy:
            cmd.extend(["--proxy", self.proxy])
        cmd.extend(["--", url])
        try:
            result = run_capture_interruptible(cmd, timeout=60)
            if result.interrupted or result.returncode != 0:
                return []
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, dict):
            return []
        if data.get("_type") not in ("playlist", "multi_video"):
            return []
        entries = data.get("entries") or []
        results = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            entry_url = e.get("url") or e.get("webpage_url") or ""
            if not entry_url:
                continue
            if entry_url and not entry_url.startswith("http"):
                entry_url = f"https://www.youtube.com/watch?v={entry_url}"
            results.append({
                "title": (e.get("title") or "Untitled")[:120],
                "url": entry_url,
                "duration": e.get("duration"),
            })
        return results

    def resolve(self, url, log_fn=None):
        has_ytdlp = self._has_ytdlp()
        if http_interrupted():
            return None
        if not has_ytdlp:
            self._log(log_fn, "yt-dlp not found. Install with: pip install yt-dlp")
            return None

        self._log(log_fn, f"Running yt-dlp extraction for: {url}")
        runtime_status = None
        if log_fn and _is_youtube_url(url):
            runtime_status = ytdlp_runtime_status()
            warning = format_ytdlp_runtime_warning(runtime_status)
            if warning:
                self._log(log_fn, warning)

        if self.cookies_browser:
            self._log(log_fn, f"Using cookies from: {self.cookies_browser}")
        try:
            result = run_capture_interruptible(
                self._build_cmd(url, include_runtime=bool(log_fn), runtime_status=runtime_status),
                timeout=60,
            )
            if result.interrupted:
                return None
            if result.timed_out:
                self._log(log_fn, "yt-dlp timed out")
                return None
            if result.returncode == 0:
                data = json.loads(result.stdout)
            elif self._is_auth_error(result.stderr):
                data = self._auto_retry_with_browsers(url, result.stderr, log_fn, runtime_status)
                if data is None:
                    return None
            else:
                err = result.stderr.strip().split("\n")[-1] if result.stderr else "Unknown error"
                self._log(log_fn, f"yt-dlp error: {err}")
                return None
        except json.JSONDecodeError:
            self._log(log_fn, "Failed to parse yt-dlp output")
            return None
        except Exception as e:
            if http_interrupted():
                return None
            logger.debug("yt-dlp resolve error for %r: %s", url, e)
            self._log(log_fn, "yt-dlp timed out")
            return None

        info = StreamInfo(
            platform="yt-dlp",
            url=url,
            title=data.get("title", ""),
            channel=(
                data.get("channel")
                or data.get("uploader_id")
                or data.get("uploader")
                or data.get("channel_id")
                or ""
            ),
            is_live=data.get("is_live", False),
        )

        raw_chapters = data.get("chapters") or []
        if isinstance(raw_chapters, list):
            for ch in raw_chapters:
                if not isinstance(ch, dict):
                    continue
                try:
                    info.chapters.append({
                        "title": str(ch.get("title") or "Chapter"),
                        "start": float(ch.get("start_time") or 0),
                        "end": float(ch.get("end_time") or 0),
                    })
                except (TypeError, ValueError):
                    continue

        # First pass — identify best audio-only format ID for pairing
        best_audio_id = ""
        best_audio_abr = 0
        audio_only_fmts = []
        for fmt in data.get("formats", []):
            if fmt.get("vcodec") != "none":
                continue
            if fmt.get("acodec") == "none":
                continue
            abr = fmt.get("abr") or 0
            fid = fmt.get("format_id", "")
            if fid and abr > best_audio_abr:
                best_audio_abr = abr
                best_audio_id = fid
            if fid:
                audio_only_fmts.append(fmt)

        # Second pass — video QualityInfo entries with ytdlp_direct spec
        for fmt in data.get("formats", []):
            if fmt.get("vcodec") == "none":
                continue
            ext = fmt.get("ext", "?")
            w = fmt.get("width") or 0
            h = fmt.get("height") or 0
            note = fmt.get("format_note", fmt.get("format_id", "?"))
            fid = fmt.get("format_id", "")
            if not fid:
                continue

            if fmt.get("acodec") == "none" and best_audio_id:
                format_spec = f"{fid}+{best_audio_id}"
                note = f"{note} +audio"
            else:
                format_spec = fid

            info.qualities.append(QualityInfo(
                name=f"{note} ({ext})",
                url=fmt.get("url", ""),
                resolution=f"{w}x{h}" if w and h else "?",
                bandwidth=int((fmt.get("tbr", 0) or 0) * 1000),
                format_type="ytdlp_direct",
                ytdlp_source=url,
                ytdlp_format=format_spec,
            ))

        dur = data.get("duration", 0)
        if dur:
            info.total_secs = float(dur)
            info.duration_str = fmt_duration(info.total_secs)

        info.qualities = [q for q in info.qualities if q.resolution != "0x0"]
        info.qualities.sort(key=lambda q: q.bandwidth, reverse=True)

        seen = set()
        unique = []
        for q in info.qualities:
            key = q.resolution
            if key not in seen:
                seen.add(key)
                unique.append(q)
        info.qualities = unique

        # Third pass — audio-only entries at the end of the list
        audio_only_fmts.sort(key=lambda f: f.get("abr") or 0, reverse=True)
        seen_audio = set()
        for fmt in audio_only_fmts:
            fid = fmt.get("format_id", "")
            abr = fmt.get("abr") or 0
            acodec = fmt.get("acodec", "?")
            key = f"{acodec}:{int(abr // 16) * 16}"
            if key in seen_audio:
                continue
            seen_audio.add(key)
            codec_short = acodec.split(".")[0] if "." in acodec else acodec
            if "mp4a" in acodec:
                codec_short = "aac"
            elif "opus" in acodec:
                codec_short = "opus"
            info.qualities.append(QualityInfo(
                name=f"Audio only ({codec_short}, {abr:.0f}k)",
                url=fmt.get("url", ""),
                resolution="audio",
                bandwidth=int(abr * 1000),
                format_type="ytdlp_direct",
                ytdlp_source=url,
                ytdlp_format=fid,
            ))

        self._log(
            log_fn,
            f"yt-dlp: {info.title}, {len(info.qualities)} formats, "
            f"{info.duration_str}",
        )
        if best_audio_id:
            self._log(
                log_fn,
                f"  Audio merge enabled (best audio id={best_audio_id}, "
                f"{best_audio_abr:.0f} kbps)",
            )
        audio_count = sum(1 for q in info.qualities if q.resolution == "audio")
        if audio_count:
            self._log(log_fn, f"  {audio_count} audio-only format(s) available")
        return info
