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
import time
import urllib.parse
from pathlib import Path

from ..http import http_interrupted, run_capture_interruptible
from ..models import QualityInfo, StreamInfo, SubtitleInfo
from ..utils import fmt_duration, scan_browser_cookies
from .base import Extractor

logger = logging.getLogger(__name__)


# Signatures of a Cloudflare / anti-bot interstitial. An increasing number of
# video hosts (Rumble, Bitchute, many PeerTube mirrors) gate their pages this
# way; the fix is to repeat the request through curl_cffi TLS impersonation.
_CLOUDFLARE_HINTS = (
    "cloudflare", "just a moment", "enable javascript and cookies",
    "http error 403", "403: forbidden", "403 forbidden",
    "attention required", "checking your browser", "impersonate",
    "cf-ray", "ddos-guard",
)


def _looks_like_cloudflare(text):
    low = (text or "").lower()
    return any(hint in low for hint in _CLOUDFLARE_HINTS)


def _impersonation_available():
    """True when yt-dlp can impersonate a browser TLS fingerprint."""
    return _has_python_module("curl_cffi")


def ytdlp_impersonate_args():
    """CLI args that make yt-dlp impersonate a real Chrome client.

    ``--impersonate chrome`` matches whatever Chrome target curl_cffi ships,
    so it stays valid as the pinned versions move. Empty when curl_cffi is
    absent, so callers can append unconditionally.
    """
    if not _impersonation_available():
        return []
    return ["--impersonate", "chrome"]


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
    """Return the exact security-approved yt-dlp command prefix."""
    from ..capabilities import resolve_command_prefix
    return resolve_command_prefix("yt_dlp")


def ytdlp_runtime_status():
    """Return yt-dlp/EJS/JavaScript readiness from the shared registry."""
    from ..capabilities import format_capability_problem, get_runtime_capabilities
    registry = get_runtime_capabilities()
    yt_dlp = registry["yt_dlp"]
    ejs = registry["yt_dlp_ejs"]
    runtime_record = registry["javascript"]
    youtube = registry["youtube"]
    runtime = {
        "name": runtime_record.get("runtime", ""),
        "command": (runtime_record.get("command") or [""])[0],
        "path": runtime_record.get("path", ""),
        "available": runtime_record.get("available", False),
        "supported": runtime_record.get("supported", False),
        "version": runtime_record.get("version", ""),
        "minimum": runtime_record.get("minimum", ""),
        "message": "" if runtime_record.get("supported") else format_capability_problem(runtime_record),
        "provenance": runtime_record.get("provenance", ""),
    }
    components = (yt_dlp, ejs, runtime_record)
    problems = [
        format_capability_problem(record)
        for record in components if not record.get("supported")
    ]
    if youtube.get("supported"):
        state, summary = "ready", "Ready"
    elif not yt_dlp.get("available"):
        state, summary = "missing", "Missing"
    elif not yt_dlp.get("supported"):
        state, summary = "blocked", "Blocked"
    else:
        state, summary = "limited", "Limited"
    detail = youtube.get("detail", "")
    if yt_dlp.get("available"):
        detail += (
            f" yt-dlp path: {yt_dlp.get('path')} "
            f"({yt_dlp.get('provenance')})."
        )
    return {
        "state": state,
        "summary": summary,
        "detail": detail.strip(),
        "yt_dlp_version": yt_dlp.get("version", ""),
        "yt_dlp_path": yt_dlp.get("path", ""),
        "ejs_available": ejs.get("supported", False),
        "ejs_version": ejs.get("version", ""),
        "ejs_requirement": ejs.get("required_by_ytdlp", ""),
        "js_runtime": runtime,
        "problems": problems,
    }


def ytdlp_runtime_args(status=None):
    status = status or ytdlp_runtime_status()
    runtime = status.get("js_runtime") or {}
    name = runtime.get("name", "")
    path = runtime.get("path") or runtime.get("command") or ""
    if not runtime.get("supported") or not name or not path:
        return []
    return ["--no-js-runtimes", "--js-runtimes", f"{name}:{path}"]


def format_ytdlp_runtime_warning(status):
    if status.get("state") == "ready":
        return ""
    return "yt-dlp runtime support is not ready: " + status.get("detail", "")


# ── YouTube player_client strategy presets (V19) ────────────────────
#
# YouTube's SABR-only enforcement and PO-token requirements vary by the
# "player client" yt-dlp impersonates. Pinning a client set is the single
# most effective knob when YouTube suddenly caps quality, demands sign-in,
# or breaks a working download after a server-side change. Empty string ==
# let yt-dlp choose its own defaults (recommended unless there's a problem).
#
# key -> (human label, comma-joined yt-dlp player_client value)
YOUTUBE_PLAYER_CLIENT_PRESETS = {
    "": ("Automatic (yt-dlp default)", ""),
    "default": ("Automatic (yt-dlp default)", ""),
    "web_safari": ("Web Safari — full formats", "web_safari"),
    "android_vr": ("Android VR — dodges SABR-only", "android_vr"),
    "tv": ("TV — embed/age tolerant", "tv"),
    "ios": ("iOS", "ios"),
    "mweb": ("Mobile web", "mweb"),
    "resilient": (
        "Resilient (web_safari, android_vr, tv)",
        "web_safari,android_vr,tv",
    ),
}


def youtube_player_client_value(preset):
    """Return the yt-dlp player_client value for *preset*, or '' if none."""
    entry = YOUTUBE_PLAYER_CLIENT_PRESETS.get(str(preset or "").strip())
    return entry[1] if entry else ""


def youtube_player_client_args(preset, url=None):
    """Return ``['--extractor-args', 'youtube:player_client=...']`` for
    *preset*, or ``[]`` when the preset is empty/unknown or *url* (when
    given) is not a YouTube URL."""
    if url is not None and not _is_youtube_url(url):
        return []
    value = youtube_player_client_value(preset)
    if not value:
        return []
    return ["--extractor-args", f"youtube:player_client={value}"]


# Known yt-dlp PO-token provider plugin packages. A provider supplies the
# proof-of-origin tokens some YouTube formats now require; without one,
# certain qualities and age-gated videos fail even with a JS runtime.
_POT_PROVIDER_MODULES = (
    "bgutil_ytdlp_pot_provider",
    "yt_dlp_get_pot",
)


def youtube_pot_provider_status():
    """Best-effort local detection of a yt-dlp PO-token provider plugin.

    Network-free: checks whether a known provider module is importable.
    Returns ``{"available": bool, "provider": str, "detail": str}``.
    """
    for module in _POT_PROVIDER_MODULES:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if found:
            return {
                "available": True,
                "provider": module,
                "detail": f"PO-token provider '{module}' detected.",
            }
    return {
        "available": False,
        "provider": "",
        "detail": (
            "No PO-token provider plugin detected. Some YouTube formats and "
            "age-restricted videos may be unavailable. Install "
            "bgutil-ytdlp-pot-provider to enable them."
        ),
    }


def youtube_health_report(player_client=""):
    """Aggregate the YouTube capability picture into one report.

    Combines the yt-dlp/EJS/JS-runtime readiness, the active player_client
    strategy, and PO-token provider presence, plus a list of plain-language
    warnings. Purely local — safe to run headless and offline.
    """
    runtime = ytdlp_runtime_status()
    pot = youtube_pot_provider_status()
    client_value = youtube_player_client_value(player_client)
    warnings = list(runtime.get("problems") or [])
    runtime_warning = format_ytdlp_runtime_warning(runtime)
    if runtime_warning and runtime_warning not in warnings:
        warnings.append(runtime_warning)
    if not pot["available"]:
        warnings.append(pot["detail"])
    healthy = runtime.get("state") == "ready"
    return {
        "healthy": healthy,
        "state": runtime.get("state", ""),
        "summary": runtime.get("summary", ""),
        "yt_dlp_version": runtime.get("yt_dlp_version", ""),
        "js_runtime": runtime.get("js_runtime", {}),
        "ejs_available": runtime.get("ejs_available", False),
        "player_client": client_value or "default",
        "pot_provider": pot,
        "warnings": warnings,
    }


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
    capture_youtube_chat = False
    subtitle_languages = "en.*,en"
    subtitle_auto = True
    subtitle_convert = ""
    subtitle_embed = True
    # YouTube player_client strategy preset (see YOUTUBE_PLAYER_CLIENT_PRESETS).
    youtube_player_client = ""
    sponsorblock = False
    sponsorblock_mark = ""
    sponsorblock_remove = "sponsor,selfpromo,interaction"
    sponsorblock_api = ""
    ytdlp_concurrent_fragments = 0
    ytdlp_retries = ""
    ytdlp_fragment_retries = ""
    ytdlp_retry_sleep = ""
    ytdlp_unavailable_fragments = ""
    ytdlp_throttled_rate = ""
    ytdlp_live_from_start = False
    ytdlp_wait_for_video = ""
    ytdlp_embed_chapters = None
    ytdlp_embed_metadata = None
    ytdlp_embed_thumbnail = None
    ytdlp_external_downloader = ""
    ytdlp_aria2c_connections = 0
    ytdlp_aria2c_splits = 0
    ytdlp_aria2c_min_split_size = ""

    def _has_ytdlp(self):
        from ..capabilities import CapabilityUnavailableError, require_capability
        try:
            require_capability("yt_dlp")
            return True
        except CapabilityUnavailableError:
            return False

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

    def _build_cmd(self, url, include_runtime=False, runtime_status=None, impersonate=False):
        cmd = ytdlp_command() + ["--dump-json", "--no-download"]
        if include_runtime and _is_youtube_url(url):
            cmd.extend(ytdlp_runtime_args(runtime_status))
        cmd.extend(youtube_player_client_args(self.youtube_player_client, url))
        if self.cookies_file and os.path.isfile(self.cookies_file):
            cmd.extend(["--cookies", self.cookies_file])
        elif self.cookies_browser:
            cmd.extend(["--cookies-from-browser", self.cookies_browser])
        if self.proxy:
            cmd.extend(["--proxy", self.proxy])
        if impersonate:
            cmd.extend(ytdlp_impersonate_args())
        cmd.extend(["--", url])
        return cmd

    # Phrases that genuinely indicate an authentication / cookie wall. Kept
    # specific on purpose: a bare "age" used to match the "age" inside
    # "webp\ **age**", so every "Unable to download webpage" error triggered
    # the full multi-browser cookie scan (60s per browser) for no reason.
    _AUTH_ERRORS = [
        "sign in", "sign-in", "confirm your age", "age-restricted",
        "age restricted", "age verification", "log in", "login",
        "cookies", "authentication", "members-only", "members only",
        "private video", "this video is available to this channel",
        "join this channel", "requires payment", "purchase to watch",
    ]
    # Transport/availability failures that are NOT auth problems — retrying
    # with browser cookies cannot fix these, so short-circuit the scan.
    _NON_AUTH_ERRORS = [
        "failed to resolve", "getaddrinfo", "temporary failure in name resolution",
        "name or service not known", "connection refused", "connection reset",
        "connection aborted", "timed out", "timeout", "network is unreachable",
        "no route to host", "bad gateway", "service unavailable",
        "gateway time-out", "http error 5",
    ]

    def _is_auth_error(self, stderr):
        lower = (stderr or "").lower()
        if any(phrase in lower for phrase in self._NON_AUTH_ERRORS):
            return False
        return any(phrase in lower for phrase in self._AUTH_ERRORS)

    def _try_with_browser(self, url, browser_name, log_fn=None, runtime_status=None):
        """Attempt yt-dlp extraction with a specific browser's cookies.
        Returns (data_dict, None) or (None, error_str)."""
        cmd = ytdlp_command() + ["--dump-json", "--no-download"]
        if log_fn and _is_youtube_url(url):
            cmd.extend(ytdlp_runtime_args(runtime_status))
        cmd.extend(youtube_player_client_args(self.youtube_player_client, url))
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

    def list_playlist_entries(
        self, url, log_fn=None, limit=200, *, playlist_items="",
        date_after="", date_before="", match_filter="", max_downloads=0,
        archive_path="", break_on_existing=False,
    ):
        """Probe URL for a playlist/channel. If it's a list container,
        return a list of entries (empty list for single videos)."""
        has_ytdlp = self._has_ytdlp()
        if http_interrupted():
            return []
        if not has_ytdlp:
            return []
        self._log(log_fn, f"Probing for playlist/channel: {url}")
        from ..download_options import validate_playlist_options
        options = validate_playlist_options(
            items=playlist_items,
            date_after=date_after,
            date_before=date_before,
            match_filter=match_filter,
            max_downloads=max_downloads,
            archive_path=archive_path,
            break_on_existing=break_on_existing,
        )
        cmd = ytdlp_command() + [
            "--flat-playlist", "--dump-single-json",
            "--playlist-end", str(limit), "--no-warnings",
        ]
        if options["items"]:
            cmd.extend(["--playlist-items", options["items"]])
        if options["date_after"]:
            cmd.extend(["--dateafter", options["date_after"]])
        if options["date_before"]:
            cmd.extend(["--datebefore", options["date_before"]])
        if options["match_filter"]:
            cmd.extend(["--match-filters", options["match_filter"]])
        if options["max_downloads"]:
            cmd.extend(["--max-downloads", str(options["max_downloads"])])
        if options["archive_path"]:
            cmd.extend(["--download-archive", options["archive_path"]])
            if options["break_on_existing"]:
                cmd.append("--break-on-existing")
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
                "id": str(e.get("id") or ""),
                "extractor_key": str(e.get("extractor_key") or ""),
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
            # Cloudflare / anti-bot wall: repeat once with TLS impersonation
            # before giving up. Only when the first try failed for that reason
            # and impersonation is actually available.
            if (result.returncode != 0 and not self._is_auth_error(result.stderr)
                    and _looks_like_cloudflare(result.stderr)
                    and _impersonation_available()):
                self._log(log_fn, "Site looks Cloudflare-protected - retrying with browser impersonation...")
                retry = run_capture_interruptible(
                    self._build_cmd(
                        url, include_runtime=bool(log_fn),
                        runtime_status=runtime_status, impersonate=True,
                    ),
                    timeout=90,
                )
                if retry.interrupted:
                    return None
                if retry.returncode == 0 and retry.stdout.strip():
                    result = retry
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

        # yt-dlp exposes manual and automatically generated subtitle tables
        # separately. Merge them by exact language code so the UI can offer
        # one source-specific multi-select while still showing provenance.
        manual_subs = data.get("subtitles")
        auto_subs = data.get("automatic_captions")
        manual_subs = manual_subs if isinstance(manual_subs, dict) else {}
        auto_subs = auto_subs if isinstance(auto_subs, dict) else {}
        language_codes = sorted(
            set(manual_subs).union(auto_subs), key=lambda value: str(value).lower()
        )[:500]
        for raw_language in language_codes:
            language = str(raw_language or "")[:64]
            if (not language or "," in language
                    or any(ord(char) < 32 or ord(char) == 127 for char in language)):
                continue
            manual_entries = manual_subs.get(raw_language)
            auto_entries = auto_subs.get(raw_language)
            manual_entries = manual_entries if isinstance(manual_entries, list) else []
            auto_entries = auto_entries if isinstance(auto_entries, list) else []
            formats = []
            name = ""
            for entry in (manual_entries + auto_entries)[:100]:
                if not isinstance(entry, dict):
                    continue
                ext = str(entry.get("ext") or "")[:16].lower()
                if ext and ext not in formats:
                    formats.append(ext)
                if not name:
                    candidate = str(entry.get("name") or "")[:128]
                    if candidate and not any(ord(char) < 32 for char in candidate):
                        name = candidate
            info.subtitles.append(SubtitleInfo(
                language=language,
                name=name,
                manual=bool(manual_entries),
                automatic=bool(auto_entries),
                formats=formats,
            ))

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
            # Use ``or`` chains, not ``dict.get`` defaults: many extractors
            # (TikTok, some DASH sites) set ``format_note``/``ext`` to an
            # explicit ``None`` value, which a default argument would not
            # replace — producing quality labels like "None (None)".
            ext = fmt.get("ext") or "?"
            w = fmt.get("width") or 0
            h = fmt.get("height") or 0
            note = fmt.get("format_note") or fmt.get("format_id") or "?"
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
        if info.subtitles:
            self._log(
                log_fn,
                f"  {len(info.subtitles)} subtitle language(s) available",
            )
        return info
