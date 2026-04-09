"""
StreamKeep v1.0.0
Multi-platform stream/VOD downloader with native extractors and yt-dlp fallback.
Supports: Kick, Twitch, Rumble, and any yt-dlp-compatible site.
"""

import sys, os, subprocess, re, json, math, time, uuid, urllib.parse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

def _bootstrap():
    """Auto-install dependencies before imports."""
    required = {"PyQt6": "PyQt6"}
    optional = {"yt_dlp": "yt-dlp", "deno": "deno"}
    import importlib
    for mod, pkg in {**required}.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            for cmd in (
                [sys.executable, "-m", "pip", "install", pkg],
                [sys.executable, "-m", "pip", "install", "--user", pkg],
                [sys.executable, "-m", "pip", "install", "--break-system-packages", pkg],
            ):
                try:
                    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except Exception:
                pass  # yt-dlp is optional

_bootstrap()

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QTextEdit, QProgressBar, QComboBox, QFileDialog,
    QCheckBox, QFrame, QSplitter, QAbstractItemView, QStackedWidget,
    QSpinBox, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl, QObject
from PyQt6.QtGui import QColor, QDesktopServices

VERSION = "3.0.0"
CURL_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "StreamKeep"
CONFIG_FILE = CONFIG_DIR / "config.json"


# ── Crash Logging ─────────────────────────────────────────────────────────

CRASH_LOG = CONFIG_DIR / "crash.log"

def _setup_crash_logging():
    """Install global exception handler that logs to file + shows MessageBox."""
    def handler(exc_type, exc_value, exc_tb):
        import traceback
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n")
                f.write(f"StreamKeep v{VERSION} crash at {datetime.now().isoformat()}\n")
                f.write(tb_str)
        except Exception:
            pass
        # Show MessageBox if QApplication exists
        app = QApplication.instance()
        if app:
            QMessageBox.critical(
                None, "StreamKeep — Crash",
                f"An unexpected error occurred:\n\n{exc_value}\n\nDetails logged to:\n{CRASH_LOG}"
            )
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = handler


# ── Config Persistence ────────────────────────────────────────────────────

def _load_config():
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
    except Exception:
        pass
    return {}

def _save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


# ── Catppuccin Mocha ──────────────────────────────────────────────────────
CAT = {
    "base": "#1e1e2e", "mantle": "#181825", "crust": "#11111b",
    "surface0": "#313244", "surface1": "#45475a", "surface2": "#585b70",
    "overlay0": "#6c7086", "overlay1": "#7f849c",
    "text": "#cdd6f4", "subtext0": "#a6adc8", "subtext1": "#bac2de",
    "lavender": "#b4befe", "blue": "#89b4fa", "sapphire": "#74c7ec",
    "sky": "#89dceb", "teal": "#94e2d5", "green": "#a6e3a1",
    "yellow": "#f9e2af", "peach": "#fab387", "maroon": "#eba0ac",
    "red": "#f38ba8", "mauve": "#cba6f7", "pink": "#f5c2e7",
    "flamingo": "#f2cdcd", "rosewater": "#f5e0dc",
}

STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {CAT['base']};
    color: {CAT['text']};
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}}
QFrame#card {{
    background-color: {CAT['mantle']};
    border: 1px solid {CAT['surface0']};
    border-radius: 10px;
    padding: 12px;
}}
QLabel {{
    color: {CAT['text']};
    border: none;
}}
QLabel#title {{
    font-size: 20px;
    font-weight: bold;
    color: {CAT['green']};
}}
QLabel#subtitle {{
    font-size: 12px;
    color: {CAT['overlay1']};
}}
QLabel#sectionTitle {{
    font-size: 14px;
    font-weight: bold;
    color: {CAT['lavender']};
}}
QLabel#streamInfo {{
    font-size: 12px;
    color: {CAT['subtext0']};
    padding: 4px 8px;
    background-color: {CAT['surface0']};
    border-radius: 6px;
}}
QLineEdit {{
    background-color: {CAT['surface0']};
    color: {CAT['text']};
    border: 1px solid {CAT['surface1']};
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
    selection-background-color: {CAT['surface2']};
}}
QLineEdit:focus {{
    border: 1px solid {CAT['lavender']};
}}
QPushButton {{
    background-color: {CAT['surface0']};
    color: {CAT['text']};
    border: 1px solid {CAT['surface1']};
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: {CAT['surface1']};
    border-color: {CAT['lavender']};
}}
QPushButton:pressed {{
    background-color: {CAT['surface2']};
}}
QPushButton:disabled {{
    background-color: {CAT['surface0']};
    color: {CAT['overlay0']};
    border-color: {CAT['surface0']};
}}
QPushButton#primary {{
    background-color: {CAT['green']};
    color: {CAT['crust']};
    border: none;
    padding: 10px 24px;
    font-size: 14px;
}}
QPushButton#primary:hover {{
    background-color: {CAT['teal']};
}}
QPushButton#primary:disabled {{
    background-color: {CAT['surface1']};
    color: {CAT['overlay0']};
}}
QPushButton#danger {{
    background-color: {CAT['red']};
    color: {CAT['crust']};
    border: none;
}}
QPushButton#danger:hover {{
    background-color: {CAT['maroon']};
}}
QComboBox {{
    background-color: {CAT['surface0']};
    color: {CAT['text']};
    border: 1px solid {CAT['surface1']};
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 13px;
}}
QComboBox:hover {{
    border-color: {CAT['lavender']};
}}
QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {CAT['surface0']};
    color: {CAT['text']};
    selection-background-color: {CAT['surface2']};
    border: 1px solid {CAT['surface1']};
    border-radius: 4px;
}}
QTableWidget {{
    background-color: {CAT['mantle']};
    color: {CAT['text']};
    border: 1px solid {CAT['surface0']};
    border-radius: 8px;
    gridline-color: {CAT['surface0']};
    selection-background-color: {CAT['surface1']};
    font-size: 13px;
}}
QTableWidget::item {{
    padding: 6px 8px;
    border-bottom: 1px solid {CAT['surface0']};
}}
QTableWidget::item:selected {{
    background-color: {CAT['surface1']};
}}
QHeaderView::section {{
    background-color: {CAT['surface0']};
    color: {CAT['subtext1']};
    border: none;
    border-bottom: 2px solid {CAT['surface1']};
    padding: 8px;
    font-weight: 600;
    font-size: 12px;
}}
QTextEdit#log {{
    background-color: {CAT['crust']};
    color: {CAT['subtext0']};
    border: 1px solid {CAT['surface0']};
    border-radius: 8px;
    padding: 8px;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 11px;
}}
QProgressBar {{
    background-color: {CAT['surface0']};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {CAT['green']};
    border-radius: 4px;
}}
QCheckBox {{
    color: {CAT['text']};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {CAT['surface2']};
    background-color: {CAT['surface0']};
}}
QCheckBox::indicator:checked {{
    background-color: {CAT['green']};
    border-color: {CAT['green']};
}}
QScrollBar:vertical {{
    background-color: {CAT['mantle']};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background-color: {CAT['surface2']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {CAT['overlay0']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QSplitter::handle {{
    background-color: {CAT['surface0']};
    height: 2px;
}}
"""


# ── Data Classes ──────────────────────────────────────────────────────────

@dataclass
class QualityInfo:
    name: str = ""
    url: str = ""
    resolution: str = ""
    bandwidth: int = 0
    format_type: str = "hls"  # hls, mp4, dash, ytdlp
    audio_url: str = ""  # If set, video is video-only and needs audio merge

@dataclass
class StreamInfo:
    platform: str = ""
    channel: str = ""
    title: str = ""
    url: str = ""
    qualities: list = field(default_factory=list)
    total_secs: float = 0
    duration_str: str = ""
    start_time: str = ""
    is_live: bool = False
    is_master: bool = False
    segment_count: int = 0
    thumbnail_url: str = ""

@dataclass
class VODInfo:
    title: str = ""
    date: str = ""
    source: str = ""
    is_live: bool = False
    viewers: int = 0
    duration: str = ""
    duration_ms: int = 0
    platform: str = ""
    channel: str = ""


# ── Utility ───────────────────────────────────────────────────────────────

def _curl(url, headers=None, timeout=30):
    """Run curl and return stdout or None."""
    cmd = ["curl", "-s", "-L"]
    for k, v in (headers or {}).items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=_CREATE_NO_WINDOW)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None

def _curl_json(url, headers=None, timeout=30):
    """Run curl and parse JSON response."""
    body = _curl(url, headers, timeout)
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None
    return None

def _curl_post_json(url, data, headers=None, timeout=30):
    """POST JSON and parse response."""
    cmd = ["curl", "-s", "-L", "-X", "POST",
           "-H", "Content-Type: application/json",
           "-d", json.dumps(data)]
    for k, v in (headers or {}).items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=_CREATE_NO_WINDOW)
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        pass
    return None

def _parse_hls_master(body, base_url):
    """Parse an HLS master playlist into a list of QualityInfo."""
    qualities = []
    res, bw = "?", 0
    for line in body.splitlines():
        if line.startswith("#EXT-X-STREAM-INF"):
            attrs = line.split(":", 1)[1]
            res_m = re.search(r'RESOLUTION=(\d+x\d+)', attrs)
            bw_m = re.search(r'BANDWIDTH=(\d+)', attrs)
            res = res_m.group(1) if res_m else "?"
            bw = int(bw_m.group(1)) if bw_m else 0
        elif not line.startswith("#") and line.strip():
            q_url = line.strip()
            if not q_url.startswith("http"):
                q_url = f"{base_url}/{q_url}"
            name = line.strip().split("/")[0]
            qualities.append(QualityInfo(
                name=name, url=q_url, resolution=res,
                bandwidth=bw, format_type="hls"
            ))
    return qualities

def _parse_hls_duration(body):
    """Parse HLS playlist for duration metadata. Returns (total_secs, start_time, segment_count)."""
    total_secs = 0.0
    start_time = ""
    m = re.search(r'TOTAL-SECS[=:](\d+\.?\d*)', body)
    if m:
        total_secs = float(m.group(1))
    m2 = re.search(r'PROGRAM-DATE-TIME:(.+)', body)
    if m2:
        start_time = m2.group(1).strip()
    seg_count = len(re.findall(r'#EXTINF:', body))
    return total_secs, start_time, seg_count

def _fmt_duration(secs):
    """Format seconds as Xh Ym Zs."""
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    return f"{s}s"

def _fmt_size(b):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def _safe_filename(s, max_len=60):
    """Sanitize a string for use as a filename."""
    return re.sub(r'[<>:"/\\|?*]', '', s)[:max_len].strip()


def _scan_browser_cookies():
    """Scan for installed browsers with cookie stores. Returns list of (display_name, ytdlp_name, path)."""
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    browsers = [
        ("Chrome", "chrome", [
            os.path.join(local, "Google", "Chrome", "User Data", "Default", "Cookies"),
            os.path.join(local, "Google", "Chrome", "User Data", "Default", "Network", "Cookies"),
        ]),
        ("Chromium", "chromium", [
            os.path.join(local, "Chromium", "User Data", "Default", "Cookies"),
            os.path.join(local, "Chromium", "User Data", "Default", "Network", "Cookies"),
        ]),
        ("Firefox", "firefox", [
            os.path.join(roaming, "Mozilla", "Firefox", "Profiles"),
        ]),
        ("Edge", "edge", [
            os.path.join(local, "Microsoft", "Edge", "User Data", "Default", "Cookies"),
            os.path.join(local, "Microsoft", "Edge", "User Data", "Default", "Network", "Cookies"),
        ]),
        ("Brave", "brave", [
            os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Cookies"),
            os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Network", "Cookies"),
        ]),
        ("Opera", "opera", [
            os.path.join(roaming, "Opera Software", "Opera Stable", "Cookies"),
            os.path.join(roaming, "Opera Software", "Opera Stable", "Network", "Cookies"),
        ]),
        ("Opera GX", "opera", [
            os.path.join(roaming, "Opera Software", "Opera GX Stable", "Cookies"),
            os.path.join(roaming, "Opera Software", "Opera GX Stable", "Network", "Cookies"),
        ]),
        ("Vivaldi", "vivaldi", [
            os.path.join(local, "Vivaldi", "User Data", "Default", "Cookies"),
            os.path.join(local, "Vivaldi", "User Data", "Default", "Network", "Cookies"),
        ]),
        ("LibreWolf", "firefox", [
            os.path.join(roaming, "librewolf", "Profiles"),
        ]),
        ("Waterfox", "firefox", [
            os.path.join(roaming, "Waterfox", "Profiles"),
        ]),
    ]
    found = []
    seen_ytdlp = set()
    for display, ytdlp_name, paths in browsers:
        for p in paths:
            if os.path.exists(p):
                # For Firefox-based browsers with non-standard profile paths,
                # generate a yt-dlp arg that includes the profile path
                actual_ytdlp = ytdlp_name
                if ytdlp_name == "firefox" and display != "Firefox":
                    # Find the actual profile dir (contains cookies.sqlite)
                    if os.path.isdir(p):
                        for entry in os.listdir(p):
                            profile_dir = os.path.join(p, entry)
                            if os.path.isdir(profile_dir) and os.path.exists(
                                os.path.join(profile_dir, "cookies.sqlite")
                            ):
                                actual_ytdlp = f"firefox:{profile_dir}"
                                break
                key = actual_ytdlp if actual_ytdlp.startswith("firefox:") else ytdlp_name
                if key not in seen_ytdlp:
                    found.append((display, actual_ytdlp, p))
                    seen_ytdlp.add(key)
                break
    return found


# ── Extractor Base & Registry ─────────────────────────────────────────────

class Extractor:
    """Abstract base. Subclasses auto-register via __init_subclass__."""
    NAME = ""
    ICON = ""
    COLOR = ""
    URL_PATTERNS = []
    _registry = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.NAME:
            Extractor._registry.append(cls)

    @classmethod
    def detect(cls, url):
        """Return an instance of the matching extractor, or None."""
        url = url.strip()
        for ext_cls in cls._registry:
            for pattern in ext_cls.URL_PATTERNS:
                if pattern.match(url):
                    return ext_cls()
        return None

    @classmethod
    def all_names(cls):
        return [e.NAME for e in cls._registry]

    def resolve(self, url, log_fn=None):
        """Resolve a URL to a StreamInfo with qualities. Returns StreamInfo or None."""
        raise NotImplementedError

    def list_vods(self, url, log_fn=None):
        """List available VODs for a channel. Returns list[VODInfo]."""
        return []

    def supports_vod_listing(self):
        return False

    def supports_live_check(self):
        return False

    def check_live(self, url):
        """Check if channel is live. Returns bool or None."""
        return None

    def extract_channel_id(self, url):
        """Extract channel name/slug for folder naming."""
        return None

    def _log(self, log_fn, msg):
        if log_fn:
            log_fn(msg)


# ── Kick Extractor ────────────────────────────────────────────────────────

class KickExtractor(Extractor):
    NAME = "Kick"
    ICON = "K"
    COLOR = "green"
    URL_PATTERNS = [
        re.compile(r'(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)/?$'),
    ]

    def extract_channel_id(self, url):
        for p in self.URL_PATTERNS:
            m = p.match(url.strip())
            if m:
                return m.group(1)
        return None

    def supports_vod_listing(self):
        return True

    def supports_live_check(self):
        return True

    def check_live(self, url):
        slug = self.extract_channel_id(url)
        if not slug:
            return None
        data = _curl_json(
            f"https://kick.com/api/v2/channels/{slug}/livestream",
            headers={"User-Agent": CURL_UA, "Accept": "application/json"}
        )
        if data and isinstance(data, dict):
            ls = data.get("data", data)
            return ls.get("playback_url") is not None
        return None

    def list_vods(self, url, log_fn=None):
        slug = self.extract_channel_id(url)
        if not slug:
            return []
        self._log(log_fn, f"Fetching VODs for Kick channel: {slug}")

        data = _curl_json(
            f"https://kick.com/api/v2/channels/{slug}/videos",
            headers={"User-Agent": CURL_UA, "Accept": "application/json"}
        )
        if not data or not isinstance(data, list):
            self._log(log_fn, "No VODs found or API error")
            return []

        vods = []
        for v in data:
            source = v.get("source", "")
            if not source:
                continue
            dur_ms = v.get("duration", 0)
            dur_str = ""
            if dur_ms:
                dur_str = _fmt_duration(dur_ms / 1000)
            vods.append(VODInfo(
                title=v.get("session_title", "Untitled"),
                date=v.get("created_at", ""),
                source=source,
                is_live=v.get("is_live", False),
                viewers=v.get("viewer_count", 0),
                duration=dur_str,
                duration_ms=dur_ms,
                platform="Kick",
                channel=slug,
            ))
        self._log(log_fn, f"Found {len(vods)} VOD(s)")
        return vods

    def resolve(self, url, log_fn=None):
        """Resolve Kick m3u8 URL to StreamInfo."""
        # If URL is already an m3u8, resolve directly
        if ".m3u8" in url:
            return self._resolve_m3u8(url, log_fn)

        # Channel URL — get first VOD
        vods = self.list_vods(url, log_fn)
        if len(vods) == 1:
            return self._resolve_m3u8(vods[0].source, log_fn)
        return None  # Multiple VODs handled by UI

    def _resolve_m3u8(self, url, log_fn=None):
        self._log(log_fn, f"Fetching playlist: {url}")
        body = _curl(url)
        if not body or not body.startswith("#EXTM3U"):
            return None

        info = StreamInfo(platform="Kick", url=url)

        if "#EXT-X-STREAM-INF" in body:
            info.is_master = True
            base = url.rsplit("/", 1)[0]
            info.qualities = _parse_hls_master(body, base)
            if info.qualities:
                sub_body = _curl(info.qualities[0].url)
                if sub_body:
                    info.total_secs, info.start_time, info.segment_count = _parse_hls_duration(sub_body)
        else:
            info.total_secs, info.start_time, info.segment_count = _parse_hls_duration(body)
            # Try finding master
            base = url.rsplit("/", 2)[0]
            master_url = f"{base}/master.m3u8"
            master_body = _curl(master_url)
            if master_body and master_body.startswith("#EXTM3U"):
                mbase = master_url.rsplit("/", 1)[0]
                info.qualities = _parse_hls_master(master_body, mbase)
                info.is_master = True
                info.url = master_url

        info.duration_str = _fmt_duration(info.total_secs)
        return info


# ── Twitch Extractor ──────────────────────────────────────────────────────

class TwitchExtractor(Extractor):
    NAME = "Twitch"
    ICON = "T"
    COLOR = "mauve"
    URL_PATTERNS = [
        re.compile(r'(?:https?://)?(?:www\.)?twitch\.tv/videos/(\d+)'),
        re.compile(r'(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]+)/?$'),
    ]
    CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

    def _gql(self, query, log_fn=None):
        return _curl_post_json(
            "https://gql.twitch.tv/gql",
            {"query": query},
            headers={"Client-Id": self.CLIENT_ID}
        )

    def extract_channel_id(self, url):
        url = url.strip()
        # VOD URL
        m = re.match(r'(?:https?://)?(?:www\.)?twitch\.tv/videos/(\d+)', url)
        if m:
            return f"vod_{m.group(1)}"
        # Channel URL
        m = re.match(r'(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]+)/?$', url)
        if m:
            return m.group(1)
        return None

    def supports_vod_listing(self):
        return True

    def supports_live_check(self):
        return True

    def check_live(self, url):
        login = self.extract_channel_id(url)
        if not login or login.startswith("vod_"):
            return None
        data = self._gql(f'{{ user(login: "{login}") {{ stream {{ id type }} }} }}')
        if data and data.get("data", {}).get("user", {}).get("stream"):
            return data["data"]["user"]["stream"]["type"] == "live"
        return False

    def list_vods(self, url, log_fn=None):
        login = self.extract_channel_id(url)
        if not login or login.startswith("vod_"):
            return []
        self._log(log_fn, f"Fetching VODs for Twitch channel: {login}")

        data = self._gql(
            f'{{ user(login: "{login}") {{ displayName '
            f'videos(first: 20, type: ARCHIVE, sort: TIME) {{ edges {{ node {{ '
            f'id title createdAt lengthSeconds viewCount '
            f'previewThumbnailURL(width: 320, height: 180) '
            f'}} }} }} }} }}'
        )
        if not data:
            self._log(log_fn, "GraphQL request failed")
            return []

        user = data.get("data", {}).get("user", {})
        edges = user.get("videos", {}).get("edges", [])
        vods = []
        for edge in edges:
            node = edge["node"]
            secs = node.get("lengthSeconds", 0)
            vods.append(VODInfo(
                title=node.get("title", "Untitled"),
                date=node.get("createdAt", ""),
                source=node["id"],  # VOD ID — resolved to m3u8 in resolve()
                is_live=False,
                viewers=node.get("viewCount", 0),
                duration=_fmt_duration(secs) if secs else "",
                duration_ms=secs * 1000,
                platform="Twitch",
                channel=login,
            ))
        self._log(log_fn, f"Found {len(vods)} VOD(s)")
        return vods

    def _get_access_token(self, vod_id=None, channel=None, log_fn=None):
        """Get playback access token for a VOD or live channel."""
        if vod_id:
            data = self._gql(
                f'{{ videoPlaybackAccessToken(id: "{vod_id}", params: '
                f'{{ platform: "web", playerBackend: "mediaplayer", playerType: "site" }}) '
                f'{{ value signature }} }}'
            )
            token_key = "videoPlaybackAccessToken"
        else:
            data = self._gql(
                f'{{ streamPlaybackAccessToken(channelName: "{channel}", params: '
                f'{{ platform: "web", playerBackend: "mediaplayer", playerType: "site" }}) '
                f'{{ value signature }} }}'
            )
            token_key = "streamPlaybackAccessToken"

        if not data:
            return None, None
        tok = data.get("data", {}).get(token_key, {})
        return tok.get("value"), tok.get("signature")

    def resolve(self, url, log_fn=None):
        url = url.strip()
        # Check if it's a VOD URL
        m = re.match(r'(?:https?://)?(?:www\.)?twitch\.tv/videos/(\d+)', url)
        if m:
            return self._resolve_vod(m.group(1), log_fn)

        # Channel URL — check if live
        login = self.extract_channel_id(url)
        if not login:
            return None

        # Try live stream
        if self.check_live(url):
            return self._resolve_live(login, log_fn)

        # Not live — list VODs
        return None  # UI handles VOD listing

    def _resolve_vod(self, vod_id, log_fn=None):
        self._log(log_fn, f"Resolving Twitch VOD: {vod_id}")
        token, sig = self._get_access_token(vod_id=vod_id, log_fn=log_fn)
        if not token or not sig:
            self._log(log_fn, "Failed to get access token")
            return None

        m3u8_url = (
            f"https://usher.ttvnw.net/vod/{vod_id}.m3u8"
            f"?client_id={self.CLIENT_ID}"
            f"&token={urllib.parse.quote(token)}"
            f"&sig={sig}"
            f"&allow_source=true&allow_audio_only=true"
        )
        body = _curl(m3u8_url)
        if not body or not body.startswith("#EXTM3U"):
            self._log(log_fn, "Failed to fetch m3u8 playlist")
            return None

        info = StreamInfo(platform="Twitch", url=m3u8_url, is_master=True)
        base = m3u8_url.rsplit("/", 1)[0]
        # Twitch master playlists have full URLs, not relative
        info.qualities = []
        res, bw = "?", 0
        name = "unknown"
        for line in body.splitlines():
            if line.startswith("#EXT-X-MEDIA"):
                nm = re.search(r'NAME="([^"]+)"', line)
                if nm:
                    name = nm.group(1)
            elif line.startswith("#EXT-X-STREAM-INF"):
                attrs = line.split(":", 1)[1]
                res_m = re.search(r'RESOLUTION=(\d+x\d+)', attrs)
                bw_m = re.search(r'BANDWIDTH=(\d+)', attrs)
                res = res_m.group(1) if res_m else "?"
                bw = int(bw_m.group(1)) if bw_m else 0
            elif line.startswith("http"):
                info.qualities.append(QualityInfo(
                    name=name, url=line.strip(), resolution=res,
                    bandwidth=bw, format_type="hls"
                ))

        # Get duration from first quality
        if info.qualities:
            sub = _curl(info.qualities[0].url)
            if sub:
                info.total_secs, info.start_time, info.segment_count = _parse_hls_duration(sub)
                # Twitch VODs may not have TOTAL-SECS — sum EXTINF instead
                if info.total_secs == 0:
                    total = sum(float(m.group(1)) for m in re.finditer(r'#EXTINF:([\d.]+)', sub))
                    info.total_secs = total

        info.duration_str = _fmt_duration(info.total_secs)
        self._log(log_fn, f"Twitch VOD: {info.duration_str}, {len(info.qualities)} qualities")
        return info

    def _resolve_live(self, login, log_fn=None):
        self._log(log_fn, f"Resolving Twitch live stream: {login}")
        token, sig = self._get_access_token(channel=login, log_fn=log_fn)
        if not token or not sig:
            self._log(log_fn, "Failed to get access token")
            return None

        m3u8_url = (
            f"https://usher.ttvnw.net/api/channel/hls/{login}.m3u8"
            f"?client_id={self.CLIENT_ID}"
            f"&token={urllib.parse.quote(token)}"
            f"&sig={sig}"
            f"&allow_source=true&allow_audio_only=true&fast_bread=true"
        )
        body = _curl(m3u8_url)
        if not body or not body.startswith("#EXTM3U"):
            self._log(log_fn, "Failed to fetch live m3u8")
            return None

        info = StreamInfo(platform="Twitch", url=m3u8_url, is_master=True, is_live=True)
        info.qualities = []
        res, bw, name = "?", 0, "unknown"
        for line in body.splitlines():
            if line.startswith("#EXT-X-MEDIA"):
                nm = re.search(r'NAME="([^"]+)"', line)
                if nm:
                    name = nm.group(1)
            elif line.startswith("#EXT-X-STREAM-INF"):
                attrs = line.split(":", 1)[1]
                res_m = re.search(r'RESOLUTION=(\d+x\d+)', attrs)
                bw_m = re.search(r'BANDWIDTH=(\d+)', attrs)
                res = res_m.group(1) if res_m else "?"
                bw = int(bw_m.group(1)) if bw_m else 0
            elif line.startswith("http"):
                info.qualities.append(QualityInfo(
                    name=name, url=line.strip(), resolution=res,
                    bandwidth=bw, format_type="hls"
                ))

        info.duration_str = "Live"
        self._log(log_fn, f"Twitch live: {len(info.qualities)} qualities")
        return info


# ── Rumble Extractor ──────────────────────────────────────────────────────

class RumbleExtractor(Extractor):
    NAME = "Rumble"
    ICON = "R"
    COLOR = "green"
    URL_PATTERNS = [
        re.compile(r'(?:https?://)?(?:www\.)?rumble\.com/(v[a-z0-9]+)'),
        re.compile(r'(?:https?://)?(?:www\.)?rumble\.com/embed/(v[a-z0-9]+)'),
    ]

    def extract_channel_id(self, url):
        m = re.match(r'(?:https?://)?(?:www\.)?rumble\.com/(?:embed/)?(v[a-z0-9]+)', url.strip())
        return m.group(1) if m else None

    def _get_embed_id(self, url, log_fn=None):
        """Extract the embed video ID from a Rumble page URL."""
        # If it's already an embed URL, extract directly
        m = re.match(r'(?:https?://)?(?:www\.)?rumble\.com/embed/(v[a-z0-9]+)', url.strip())
        if m:
            return m.group(1)

        # Page URL — fetch page and find embed ID
        self._log(log_fn, f"Fetching Rumble page to find embed ID...")
        body = _curl(url, headers={"User-Agent": CURL_UA, "Accept": "text/html"})
        if body:
            m = re.search(r'embed/(v[a-z0-9]+)', body)
            if m:
                return m.group(1)
        return None

    def resolve(self, url, log_fn=None):
        embed_id = self._get_embed_id(url, log_fn)
        if not embed_id:
            self._log(log_fn, "Could not find Rumble embed ID")
            return None

        self._log(log_fn, f"Fetching Rumble video data: {embed_id}")
        data = _curl_json(
            f"https://rumble.com/embedJS/u3/?request=video&ver=2&v={embed_id}",
            headers={"User-Agent": CURL_UA, "Referer": "https://rumble.com/"}
        )
        if not data or not isinstance(data, dict):
            self._log(log_fn, "Failed to fetch Rumble video data")
            return None

        info = StreamInfo(
            platform="Rumble",
            url=url,
            title=data.get("title", ""),
            is_live=data.get("duration", 0) == 0,
        )

        ua = data.get("ua", {})

        # HLS streams
        hls = ua.get("hls", {})
        for key, val in hls.items():
            if isinstance(val, dict) and "url" in val:
                meta = val.get("meta", {})
                h = meta.get("h", "?")
                w = meta.get("w", "?")
                info.qualities.append(QualityInfo(
                    name=f"hls_{key}",
                    url=val["url"],
                    resolution=f"{w}x{h}" if w != "?" else "auto",
                    bandwidth=meta.get("bitrate", 0),
                    format_type="hls",
                ))

        # MP4 direct downloads
        mp4 = ua.get("mp4", {})
        for key, val in mp4.items():
            if isinstance(val, dict) and "url" in val:
                meta = val.get("meta", {})
                h = meta.get("h", "?")
                w = meta.get("w", "?")
                info.qualities.append(QualityInfo(
                    name=f"mp4_{key}",
                    url=val["url"],
                    resolution=f"{w}x{h}",
                    bandwidth=meta.get("bitrate", 0),
                    format_type="mp4",
                ))

        # Duration
        dur = data.get("duration", 0)
        if dur:
            info.total_secs = dur
            info.duration_str = _fmt_duration(dur)
        elif info.is_live:
            info.duration_str = "Live"

        # Try to get duration from HLS playlist
        if info.total_secs == 0 and info.qualities:
            for q in info.qualities:
                if q.format_type == "hls":
                    sub = _curl(q.url)
                    if sub:
                        ts, _, sc = _parse_hls_duration(sub)
                        if ts > 0:
                            info.total_secs = ts
                            info.duration_str = _fmt_duration(ts)
                            info.segment_count = sc
                            break

        self._log(log_fn, f"Rumble: {info.title}, {len(info.qualities)} qualities, {info.duration_str}")
        return info


# ── SoundCloud Extractor ───────────────────────────────────────────────────

class SoundCloudExtractor(Extractor):
    NAME = "SoundCloud"
    ICON = "S"
    COLOR = "peach"
    URL_PATTERNS = [
        re.compile(r'(?:https?://)?(?:www\.)?soundcloud\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)'),
        re.compile(r'(?:https?://)?(?:www\.)?soundcloud\.com/([a-zA-Z0-9_-]+)/sets/([a-zA-Z0-9_-]+)'),
    ]
    _client_id = None

    def extract_channel_id(self, url):
        m = re.match(r'(?:https?://)?(?:www\.)?soundcloud\.com/([a-zA-Z0-9_-]+)', url.strip())
        return m.group(1) if m else None

    def _get_client_id(self, log_fn=None):
        if self._client_id:
            return self._client_id
        self._log(log_fn, "Extracting SoundCloud client_id...")
        page = _curl("https://soundcloud.com/", headers={"User-Agent": CURL_UA})
        if not page:
            return None
        scripts = re.findall(r'src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', page)
        for js_url in scripts:
            js = _curl(js_url)
            if js:
                m = re.search(r'client_id:"([a-zA-Z0-9]+)"', js)
                if m:
                    SoundCloudExtractor._client_id = m.group(1)
                    self._log(log_fn, f"Got client_id: {self._client_id[:8]}...")
                    return self._client_id
        return None

    def resolve(self, url, log_fn=None):
        cid = self._get_client_id(log_fn)
        if not cid:
            self._log(log_fn, "Could not get SoundCloud client_id")
            return None

        self._log(log_fn, f"Resolving SoundCloud: {url}")
        data = _curl_json(
            f"https://api-v2.soundcloud.com/resolve?url={urllib.parse.quote(url, safe='')}&client_id={cid}"
        )
        if not data or not isinstance(data, dict):
            self._log(log_fn, "Failed to resolve SoundCloud URL")
            return None

        info = StreamInfo(
            platform="SoundCloud",
            url=url,
            title=data.get("title", ""),
            total_secs=data.get("duration", 0) / 1000,
        )
        info.duration_str = _fmt_duration(info.total_secs)

        for t in data.get("media", {}).get("transcodings", []):
            fmt = t.get("format", {})
            protocol = fmt.get("protocol", "")
            mime = fmt.get("mime_type", "")
            trans_url = t.get("url", "")
            if not trans_url:
                continue
            # Resolve the actual stream URL
            stream_data = _curl_json(f"{trans_url}?client_id={cid}")
            if stream_data and stream_data.get("url"):
                ft = "mp4" if protocol == "progressive" else "hls"
                name = f"{protocol} ({mime.split('/')[-1].split(';')[0]})"
                info.qualities.append(QualityInfo(
                    name=name, url=stream_data["url"],
                    resolution="audio", bandwidth=0, format_type=ft,
                ))

        self._log(log_fn, f"SoundCloud: {info.title}, {len(info.qualities)} formats, {info.duration_str}")
        return info


# ── Reddit Extractor ──────────────────────────────────────────────────────

class RedditExtractor(Extractor):
    NAME = "Reddit"
    ICON = "R"
    COLOR = "peach"
    URL_PATTERNS = [
        re.compile(r'(?:https?://)?(?:www\.|old\.)?reddit\.com/r/\w+/comments/\w+'),
        re.compile(r'(?:https?://)?v\.redd\.it/\w+'),
    ]

    def extract_channel_id(self, url):
        m = re.search(r'/r/(\w+)/comments/(\w+)', url)
        if m:
            return f"r_{m.group(1)}_{m.group(2)}"
        m = re.search(r'v\.redd\.it/(\w+)', url)
        return m.group(1) if m else None

    def resolve(self, url, log_fn=None):
        self._log(log_fn, f"Resolving Reddit: {url}")

        # Normalize v.redd.it URLs — need to follow redirect to get the post
        if "v.redd.it" in url:
            body = _curl(url, headers={"User-Agent": "StreamKeep/2.0"})
            if body:
                m = re.search(r'reddit\.com/r/\w+/comments/\w+', body)
                if m:
                    url = "https://www." + m.group(0)

        json_url = url.rstrip("/") + ".json"
        data = _curl_json(json_url, headers={"User-Agent": "StreamKeep/2.0"})
        if not data or not isinstance(data, list) or len(data) == 0:
            self._log(log_fn, "Failed to fetch Reddit post data")
            return None

        post = data[0].get("data", {}).get("children", [{}])[0].get("data", {})
        if not post.get("is_video"):
            self._log(log_fn, "Reddit post is not a video")
            return None

        rv = post.get("secure_media", {}).get("reddit_video", {})
        if not rv:
            rv = post.get("media", {}).get("reddit_video", {})
        if not rv:
            return None

        info = StreamInfo(
            platform="Reddit",
            url=url,
            title=post.get("title", ""),
            total_secs=rv.get("duration", 0),
        )
        info.duration_str = _fmt_duration(info.total_secs)

        # Fallback URL (video+audio muxed, lower quality)
        fallback = rv.get("fallback_url", "")
        if fallback:
            h = rv.get("height", "?")
            info.qualities.append(QualityInfo(
                name=f"fallback ({h}p)", url=fallback,
                resolution=f"?x{h}", bandwidth=0, format_type="mp4",
            ))

        # DASH URL (higher quality, needs audio merge — ffmpeg handles this)
        dash = rv.get("dash_url", "")
        if dash:
            info.qualities.insert(0, QualityInfo(
                name="DASH (best)", url=dash,
                resolution=f"{rv.get('width', '?')}x{rv.get('height', '?')}",
                bandwidth=rv.get("bitrate_kbps", 0) * 1000,
                format_type="hls",  # ffmpeg handles DASH via -i
            ))

        self._log(log_fn, f"Reddit: {info.title[:50]}, {len(info.qualities)} formats, {info.duration_str}")
        return info


# ── Audius Extractor ──────────────────────────────────────────────────────

class AudiusExtractor(Extractor):
    NAME = "Audius"
    ICON = "A"
    COLOR = "mauve"
    URL_PATTERNS = [
        re.compile(r'(?:https?://)?(?:www\.)?audius\.co/([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)'),
    ]
    API_BASE = "https://discoveryprovider.audius.co/v1"

    def extract_channel_id(self, url):
        m = re.match(r'(?:https?://)?(?:www\.)?audius\.co/([a-zA-Z0-9_-]+)', url.strip())
        return m.group(1) if m else None

    def resolve(self, url, log_fn=None):
        self._log(log_fn, f"Resolving Audius: {url}")

        # Resolve the URL to a track
        data = _curl_json(
            f"{self.API_BASE}/resolve?url={urllib.parse.quote(url, safe='')}&app_name=StreamKeep"
        )
        if not data or not data.get("data"):
            self._log(log_fn, "Failed to resolve Audius URL")
            return None

        track = data["data"]
        track_id = track.get("id", "")

        info = StreamInfo(
            platform="Audius",
            url=url,
            title=track.get("title", ""),
            total_secs=track.get("duration", 0),
        )
        info.duration_str = _fmt_duration(info.total_secs)

        stream_url = f"{self.API_BASE}/tracks/{track_id}/stream?app_name=StreamKeep"
        info.qualities.append(QualityInfo(
            name="stream (mp3)", url=stream_url,
            resolution="audio", bandwidth=0, format_type="mp4",
        ))

        self._log(log_fn, f"Audius: {info.title}, {info.duration_str}")
        return info


# ── Podcast RSS Extractor ─────────────────────────────────────────────────

class PodcastRSSExtractor(Extractor):
    NAME = "Podcast"
    ICON = "P"
    COLOR = "yellow"
    URL_PATTERNS = [
        re.compile(r'(?:https?://).+\.(rss|xml)(\?.*)?$'),
        re.compile(r'(?:https?://).+/feed/?(\?.*)?$'),
        re.compile(r'(?:https?://).+/rss/?(\?.*)?$'),
    ]

    def extract_channel_id(self, url):
        try:
            parsed = urllib.parse.urlparse(url.strip())
            return parsed.netloc.replace(".", "_")
        except Exception:
            return "podcast"

    def supports_vod_listing(self):
        return True

    def list_vods(self, url, log_fn=None):
        self._log(log_fn, f"Fetching podcast RSS: {url}")
        body = _curl(url, headers={"User-Agent": CURL_UA, "Accept": "application/rss+xml, application/xml, text/xml"})
        if not body:
            self._log(log_fn, "Failed to fetch RSS feed")
            return []

        vods = []
        # Simple XML parsing without external deps
        items = re.findall(r'<item>(.*?)</item>', body, re.DOTALL)
        for item in items:
            title_m = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item)
            title = title_m.group(1).strip() if title_m else "Untitled"

            date_m = re.search(r'<pubDate>(.*?)</pubDate>', item)
            date = date_m.group(1).strip() if date_m else ""

            enc_m = re.search(r'<enclosure[^>]+url="([^"]+)"', item)
            if not enc_m:
                enc_m = re.search(r"<enclosure[^>]+url='([^']+)'", item)
            if not enc_m:
                continue
            enc_url = enc_m.group(1)

            dur_m = re.search(r'<itunes:duration>([\d:]+)</itunes:duration>', item)
            dur_str = ""
            if dur_m:
                parts = dur_m.group(1).split(":")
                if len(parts) == 3:
                    dur_str = f"{parts[0]}h {parts[1]}m"
                elif len(parts) == 2:
                    dur_str = f"{parts[0]}m {parts[1]}s"
                else:
                    dur_str = f"{parts[0]}s"

            vods.append(VODInfo(
                title=title, date=date, source=enc_url,
                is_live=False, viewers=0, duration=dur_str,
                duration_ms=0, platform="Podcast", channel="",
            ))

        self._log(log_fn, f"Found {len(vods)} episode(s)")
        return vods

    def resolve(self, url, log_fn=None):
        # If it's a direct media URL from a podcast
        if any(url.endswith(ext) for ext in (".mp3", ".m4a", ".ogg", ".wav", ".aac")):
            info = StreamInfo(platform="Podcast", url=url, title=url.split("/")[-1])
            info.qualities.append(QualityInfo(
                name="audio", url=url, resolution="audio", format_type="mp4",
            ))
            return info
        return None  # list_vods handles the RSS feed


# ── yt-dlp Fallback Extractor ─────────────────────────────────────────────

class YtDlpExtractor(Extractor):
    NAME = "yt-dlp"
    ICON = "Y"
    COLOR = "overlay1"
    URL_PATTERNS = [
        re.compile(r'https?://.+'),  # Catch-all — must be registered last
    ]
    # Set by Settings tab — browser name for --cookies-from-browser
    cookies_browser = ""
    # Set by Settings tab — path to Netscape cookies.txt file for --cookies
    cookies_file = ""

    def _has_ytdlp(self):
        try:
            subprocess.run(
                ["yt-dlp", "--version"], capture_output=True, timeout=5,
                creationflags=_CREATE_NO_WINDOW
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def extract_channel_id(self, url):
        try:
            parsed = urllib.parse.urlparse(url.strip())
            parts = parsed.path.strip("/").split("/")
            if parts and parts[-1]:
                return f"{parsed.netloc}_{parts[-1]}"
            return parsed.netloc
        except Exception:
            return "download"

    def _build_cmd(self, url):
        cmd = ["yt-dlp", "--dump-json", "--no-download"]
        if self.cookies_file and os.path.isfile(self.cookies_file):
            cmd.extend(["--cookies", self.cookies_file])
        elif self.cookies_browser:
            cmd.extend(["--cookies-from-browser", self.cookies_browser])
        cmd.append(url)
        return cmd

    # Errors that indicate cookies/auth are needed
    _AUTH_ERRORS = ["Sign in", "age", "confirm your age", "login", "cookies", "authentication",
                    "members-only", "private video", "This video is available to this channel"]

    def _is_auth_error(self, stderr):
        lower = stderr.lower()
        return any(phrase.lower() in lower for phrase in self._AUTH_ERRORS)

    def _try_with_browser(self, url, browser_name, log_fn=None):
        """Attempt yt-dlp extraction with a specific browser's cookies. Returns (data_dict, None) or (None, error_str)."""
        cmd = ["yt-dlp", "--dump-json", "--no-download",
               "--cookies-from-browser", browser_name, url]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                               creationflags=_CREATE_NO_WINDOW)
            if r.returncode == 0:
                return json.loads(r.stdout), None
            err = r.stderr.strip().split("\n")[-1] if r.stderr else "Unknown error"
            return None, err
        except subprocess.TimeoutExpired:
            return None, "Timed out"
        except json.JSONDecodeError:
            return None, "Bad JSON"
        except Exception as e:
            return None, str(e)

    def _copy_locked_cookie_db(self, cookie_db_path, log_fn=None):
        """Copy a locked Chromium cookie DB using Volume Shadow Copy (VSS).
        Requires a UAC admin elevation prompt. Creates a temp profile
        directory that yt-dlp can read.
        Returns the temp profile path, or None."""
        import tempfile, shutil
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
            if os.path.exists(tmp_base):
                shutil.rmtree(tmp_base, ignore_errors=True)
            tmp_profile = os.path.join(tmp_base, "Default")
            tmp_network = os.path.join(tmp_profile, "Network")
            os.makedirs(tmp_network, exist_ok=True)

            # Copy Local State (not locked — contains decryption key)
            shutil.copy2(local_state_path, os.path.join(tmp_base, "Local State"))

            if "Network" in cookie_db_path:
                dst_cookies = os.path.join(tmp_network, "Cookies")
            else:
                dst_cookies = os.path.join(tmp_profile, "Cookies")

            # Try direct copy first (works if browser is closed)
            try:
                shutil.copy2(cookie_db_path, dst_cookies)
                self._log(log_fn, "  Copied cookie DB directly")
                return tmp_profile
            except (PermissionError, OSError):
                pass

            # Browser has exclusive lock — use Volume Shadow Copy via elevated PowerShell
            self._log(log_fn, "  Cookie DB locked — using Volume Shadow Copy (admin required)...")

            drive = cookie_db_path[:3]  # e.g. "C:\"
            rel_path = cookie_db_path[3:]  # path relative to drive root
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
                None, 0
            )
            if ret <= 32:
                self._log(log_fn, "  UAC elevation denied")
                return None

            # Wait for VSS copy (can take 5-10s)
            for _ in range(40):
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
                self._log(log_fn, f"  Copied cookie DB via VSS shadow copy")
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

    def _auto_retry_with_browsers(self, url, original_stderr, log_fn=None):
        """Scan for installed browsers and try each one's cookies until one works."""
        err_line = original_stderr.strip().split("\n")[-1] if original_stderr else ""
        self._log(log_fn, f"Auth required: {err_line}")
        self._log(log_fn, "Auto-scanning for browser cookies...")

        browsers = _scan_browser_cookies()
        if not browsers:
            self._log(log_fn, "No browsers found on this system.")
            return None

        self._log(log_fn, f"Found {len(browsers)} browser(s) to try: {', '.join(d for d, _, _ in browsers)}")

        for display, ytdlp_name, path in browsers:
            self._log(log_fn, f"Trying cookies from {display} ({ytdlp_name})...")
            data, err = self._try_with_browser(url, ytdlp_name, log_fn)
            if data:
                self._log(log_fn, f"Success with {display}! Saving as default.")
                YtDlpExtractor.cookies_browser = ytdlp_name
                return data

            # If "Could not copy" error — browser has the DB locked.
            # Copy it ourselves using sqlite3 backup API and retry.
            if err and "could not copy" in err.lower():
                self._log(log_fn, f"  DB locked — copying with sqlite3 backup...")
                db_path = self._find_cookie_db_path(ytdlp_name)
                if db_path:
                    tmp_profile = self._copy_locked_cookie_db(db_path, log_fn)
                    if tmp_profile:
                        # Retry with the temp profile copy
                        browser_arg = f"{ytdlp_name}:{tmp_profile}"
                        self._log(log_fn, f"  Retrying with copied profile...")
                        data2, err2 = self._try_with_browser(url, browser_arg, log_fn)
                        if data2:
                            self._log(log_fn, f"Success with {display} (copied profile)! Saving as default.")
                            YtDlpExtractor.cookies_browser = browser_arg
                            return data2
                        else:
                            self._log(log_fn, f"  {display} (copied) failed: {err2}")
            else:
                self._log(log_fn, f"  {display} failed: {err}")

        self._log(log_fn, "All browsers tried — none had valid cookies for this URL.")
        return None

    def resolve(self, url, log_fn=None):
        if not self._has_ytdlp():
            self._log(log_fn, "yt-dlp not found. Install with: pip install yt-dlp")
            return None

        self._log(log_fn, f"Running yt-dlp extraction for: {url}")

        # First attempt — use configured cookies (if any) or plain
        if self.cookies_browser:
            self._log(log_fn, f"Using cookies from: {self.cookies_browser}")
        try:
            r = subprocess.run(
                self._build_cmd(url),
                capture_output=True, text=True, timeout=60,
                creationflags=_CREATE_NO_WINDOW
            )
            if r.returncode == 0:
                data = json.loads(r.stdout)
            elif self._is_auth_error(r.stderr):
                # Auth/age error — auto-scan browsers and retry each one
                data = self._auto_retry_with_browsers(url, r.stderr, log_fn)
                if data is None:
                    return None
            else:
                err = r.stderr.strip().split("\n")[-1] if r.stderr else "Unknown error"
                self._log(log_fn, f"yt-dlp error: {err}")
                return None
        except subprocess.TimeoutExpired:
            self._log(log_fn, "yt-dlp timed out")
            return None
        except json.JSONDecodeError:
            self._log(log_fn, "Failed to parse yt-dlp output")
            return None

        info = StreamInfo(
            platform="yt-dlp",
            url=url,
            title=data.get("title", ""),
            is_live=data.get("is_live", False),
        )

        # First pass — identify the best audio-only format
        best_audio_url = ""
        best_audio_abr = 0
        for fmt in data.get("formats", []):
            if fmt.get("vcodec") != "none":
                continue
            if fmt.get("acodec") == "none":
                continue
            abr = fmt.get("abr", 0) or 0
            fmt_url = fmt.get("url", "")
            if fmt_url and abr > best_audio_abr:
                best_audio_abr = abr
                best_audio_url = fmt_url

        # Second pass — parse video formats, pair video-only with best audio
        for fmt in data.get("formats", []):
            if fmt.get("vcodec") == "none":
                continue
            ext = fmt.get("ext", "?")
            w = fmt.get("width", 0)
            h = fmt.get("height", 0)
            note = fmt.get("format_note", fmt.get("format_id", "?"))
            fmt_url = fmt.get("url", "")
            if not fmt_url:
                continue

            # Determine format type
            if "m3u8" in fmt_url or fmt.get("protocol", "") == "m3u8_native":
                ft = "hls"
            elif ext in ("mp4", "webm"):
                ft = "mp4"
            else:
                ft = "ytdlp"

            # Detect video-only format and pair with best audio for merge
            audio_url = ""
            if fmt.get("acodec") == "none" and best_audio_url:
                audio_url = best_audio_url
                note = f"{note} +audio"

            info.qualities.append(QualityInfo(
                name=f"{note} ({ext})",
                url=fmt_url,
                resolution=f"{w}x{h}" if w and h else "?",
                bandwidth=int((fmt.get("tbr", 0) or 0) * 1000),
                format_type=ft,
                audio_url=audio_url,
            ))

        # Duration
        dur = data.get("duration", 0)
        if dur:
            info.total_secs = float(dur)
            info.duration_str = _fmt_duration(info.total_secs)

        # Filter out invalid resolutions and sort by bandwidth desc
        info.qualities = [q for q in info.qualities if q.resolution != "0x0"]
        info.qualities.sort(key=lambda q: q.bandwidth, reverse=True)

        # Deduplicate by resolution
        seen = set()
        unique = []
        for q in info.qualities:
            key = q.resolution
            if key not in seen:
                seen.add(key)
                unique.append(q)
        info.qualities = unique

        self._log(log_fn, f"yt-dlp: {info.title}, {len(info.qualities)} formats, {info.duration_str}")
        if best_audio_url:
            self._log(log_fn, f"  Audio merge enabled (best audio: {best_audio_abr:.0f} kbps)")
        return info


# ── Worker Threads ────────────────────────────────────────────────────────

class FetchWorker(QThread):
    """Resolves URLs using the extractor system."""
    finished = pyqtSignal(object)        # StreamInfo
    vods_found = pyqtSignal(list, str)   # list[VODInfo], platform_name
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, url, vod_source=None, vod_platform=None):
        super().__init__()
        self.url = url.strip()
        self.vod_source = vod_source
        self.vod_platform = vod_platform

    def run(self):
        try:
            if self.vod_source:
                # Direct m3u8/source URL from VOD picker
                info = self._resolve_direct(self.vod_source)
                if info:
                    self.finished.emit(info)
                else:
                    self.error.emit("Failed to resolve VOD source")
                return

            ext = Extractor.detect(self.url)
            if not ext:
                # Try direct media URL detection before giving up
                direct = _detect_direct_media(self.url, log_fn=self.log.emit)
                if direct:
                    self.finished.emit(direct)
                    return
                self.error.emit("No extractor found for this URL")
                return

            # If yt-dlp fallback matched, try direct media detection first (faster)
            if ext.NAME == "yt-dlp":
                direct = _detect_direct_media(self.url, log_fn=self.log.emit)
                if direct:
                    self.finished.emit(direct)
                    return

            self.log.emit(f"Detected platform: {ext.NAME}")

            # If extractor supports VOD listing, try that first
            if ext.supports_vod_listing():
                vods = ext.list_vods(self.url, log_fn=self.log.emit)
                if len(vods) > 1:
                    self.vods_found.emit(vods, ext.NAME)
                    return
                elif len(vods) == 1:
                    self.log.emit(f"Auto-selecting only VOD: {vods[0].title}")
                    info = self._resolve_source(vods[0], ext)
                    if info:
                        self.finished.emit(info)
                        return

            # Direct resolve
            info = ext.resolve(self.url, log_fn=self.log.emit)
            if info:
                self.finished.emit(info)
            else:
                # Maybe there were VODs but none to auto-select
                if ext.supports_vod_listing():
                    vods = ext.list_vods(self.url, log_fn=self.log.emit)
                    if vods:
                        self.vods_found.emit(vods, ext.NAME)
                        return
                self.error.emit("Failed to resolve stream URL")

        except Exception as e:
            self.error.emit(str(e))

    def _resolve_direct(self, source):
        """Resolve a direct source URL (m3u8 or VOD ID)."""
        # Twitch VOD IDs are numeric strings
        if source.isdigit():
            ext = TwitchExtractor()
            return ext._resolve_vod(source, log_fn=self.log.emit)

        # Try as m3u8 URL — use Kick extractor's generic m3u8 resolver
        ext = KickExtractor()
        return ext._resolve_m3u8(source, log_fn=self.log.emit)

    def _resolve_source(self, vod, ext):
        """Resolve a VODInfo to StreamInfo."""
        if vod.platform == "Twitch" and vod.source.isdigit():
            return TwitchExtractor()._resolve_vod(vod.source, log_fn=self.log.emit)
        if ".m3u8" in vod.source or "stream.kick.com" in vod.source:
            return KickExtractor()._resolve_m3u8(vod.source, log_fn=self.log.emit)
        return ext.resolve(vod.source, log_fn=self.log.emit)


class DownloadWorker(QThread):
    """Downloads segments using ffmpeg with speed/ETA tracking."""
    progress = pyqtSignal(int, int, str)   # seg_idx, percent, status (includes speed/ETA)
    segment_done = pyqtSignal(int, str)
    log = pyqtSignal(str)
    error = pyqtSignal(int, str)
    all_done = pyqtSignal()

    def __init__(self, playlist_url, segments, output_dir, format_type="hls"):
        super().__init__()
        self.playlist_url = playlist_url
        self.segments = segments
        self.output_dir = output_dir
        self.format_type = format_type
        self.audio_url = ""  # Set externally for video+audio merge
        self._cancel = False

    def cancel(self):
        self._cancel = True
        if hasattr(self, '_proc') and self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def run(self):
        for seg_idx, label, start, duration in self.segments:
            if self._cancel:
                self.log.emit("Download cancelled.")
                return

            outfile = os.path.join(self.output_dir, f"{label}.mp4")
            if os.path.exists(outfile):
                size = os.path.getsize(outfile)
                if size > 1024:
                    self.log.emit(f"[SKIP] {label} ({_fmt_size(size)})")
                    self.segment_done.emit(seg_idx, _fmt_size(size))
                    continue

            self.log.emit(f"[DL] {label} — start: {start}s, duration: {duration}s")
            self.progress.emit(seg_idx, 0, "Starting...")

            # Build ffmpeg command with optional audio merge
            if self.audio_url:
                # Video + audio merge: two inputs, map both, copy codecs
                if self.format_type == "mp4":
                    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info",
                           "-i", self.playlist_url, "-i", self.audio_url,
                           "-map", "0:v:0", "-map", "1:a:0",
                           "-c", "copy", "-y", outfile]
                else:
                    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info",
                           "-ss", str(start), "-i", self.playlist_url,
                           "-ss", str(start), "-i", self.audio_url,
                           "-t", str(duration),
                           "-map", "0:v:0", "-map", "1:a:0",
                           "-c", "copy", "-y", outfile]
            elif self.format_type == "mp4":
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info",
                       "-i", self.playlist_url, "-c", "copy", "-y", outfile]
            else:
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info",
                       "-ss", str(start), "-i", self.playlist_url,
                       "-t", str(duration), "-c", "copy", "-y", outfile]

            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1, creationflags=_CREATE_NO_WINDOW
                )
                output_lines = []
                for line in self._proc.stderr:
                    line = line.strip()
                    if not line:
                        continue
                    output_lines.append(line)
                    time_match = re.search(r'time=(\d+):(\d+):(\d+)\.(\d+)', line)
                    if time_match:
                        h, m, s = int(time_match.group(1)), int(time_match.group(2)), int(time_match.group(3))
                        elapsed = h * 3600 + m * 60 + s
                        pct = min(99, int((elapsed / max(duration, 1)) * 100))
                        # Parse speed and size
                        speed_m = re.search(r'speed=\s*([\d.]+)x', line)
                        size_m = re.search(r'size=\s*(\d+\w+)', line)
                        extra = ""
                        if speed_m:
                            spd = float(speed_m.group(1))
                            extra += f" | {spd:.1f}x"
                            if spd > 0 and duration > 0:
                                remaining = (duration - elapsed) / spd
                                extra += f" | ETA {_fmt_duration(remaining)}"
                        if size_m:
                            extra += f" | {size_m.group(1)}"
                        self.progress.emit(seg_idx, pct, f"{elapsed}s / {duration}s{extra}")

                self._proc.wait()
                if self._cancel:
                    if os.path.exists(outfile):
                        os.remove(outfile)
                    return

                if self._proc.returncode == 0 and os.path.exists(outfile):
                    size = os.path.getsize(outfile)
                    self.progress.emit(seg_idx, 100, "Complete")
                    self.segment_done.emit(seg_idx, _fmt_size(size))
                    self.log.emit(f"[DONE] {label} — {_fmt_size(size)}")
                else:
                    err = "\n".join(output_lines[-5:])
                    self.error.emit(seg_idx, f"ffmpeg exit {self._proc.returncode}")
                    self.log.emit(f"[FAIL] {label}\n{err}")

            except Exception as e:
                self.error.emit(seg_idx, str(e))
                self.log.emit(f"[ERROR] {label}: {e}")

        if not self._cancel:
            self.all_done.emit()


# ── Metadata Saver ────────────────────────────────────────────────────────

class MetadataSaver:
    @staticmethod
    def save(output_dir, stream_info, vod_info=None):
        """Save metadata.json alongside downloads."""
        meta = {
            "platform": stream_info.platform,
            "title": stream_info.title or (vod_info.title if vod_info else ""),
            "url": stream_info.url,
            "duration": stream_info.duration_str,
            "total_secs": stream_info.total_secs,
            "start_time": stream_info.start_time,
            "is_live": stream_info.is_live,
            "qualities": [{"name": q.name, "resolution": q.resolution,
                           "bandwidth": q.bandwidth, "format": q.format_type}
                          for q in stream_info.qualities],
            "downloaded_at": datetime.now().isoformat(),
        }
        if vod_info:
            meta["vod_date"] = vod_info.date
            meta["vod_channel"] = vod_info.channel
            meta["vod_viewers"] = vod_info.viewers
        try:
            p = os.path.join(output_dir, "metadata.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        # Download thumbnail if available
        if stream_info.thumbnail_url:
            try:
                thumb_path = os.path.join(output_dir, "thumbnail.jpg")
                subprocess.run(
                    ["curl", "-s", "-L", "-o", thumb_path, stream_info.thumbnail_url],
                    timeout=15, creationflags=_CREATE_NO_WINDOW
                )
            except Exception:
                pass


# ── Channel Monitor ───────────────────────────────────────────────────────

@dataclass
class MonitorEntry:
    url: str = ""
    platform: str = ""
    channel_id: str = ""
    interval_secs: int = 120
    auto_record: bool = False
    last_check: float = 0
    last_status: str = "unknown"  # live, offline, error
    is_recording: bool = False

class ChannelMonitor(QObject):
    """Polls channels for live status via round-robin."""
    status_changed = pyqtSignal()
    channel_went_live = pyqtSignal(str)  # channel_id
    log = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.entries = []
        self._poll_idx = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._poll_tick)
        self._timer.start(15_000)  # check one channel every 15s

    def add_channel(self, url, interval=120, auto_record=False):
        ext = Extractor.detect(url)
        if not ext or not ext.supports_live_check():
            return False
        ch_id = ext.extract_channel_id(url) or url
        # Don't add duplicates
        for e in self.entries:
            if e.channel_id == ch_id:
                return False
        self.entries.append(MonitorEntry(
            url=url, platform=ext.NAME, channel_id=ch_id,
            interval_secs=interval, auto_record=auto_record
        ))
        self.status_changed.emit()
        return True

    def remove_channel(self, idx):
        if 0 <= idx < len(self.entries):
            self.entries.pop(idx)
            self.status_changed.emit()

    def _poll_tick(self):
        if not self.entries:
            return
        entry = self.entries[self._poll_idx % len(self.entries)]
        self._poll_idx += 1
        now = time.time()
        if now - entry.last_check < entry.interval_secs:
            return
        entry.last_check = now
        ext = Extractor.detect(entry.url)
        if not ext:
            entry.last_status = "error"
            self.status_changed.emit()
            return
        try:
            is_live = ext.check_live(entry.url)
            prev = entry.last_status
            entry.last_status = "live" if is_live else "offline"
            if is_live and prev != "live":
                self.channel_went_live.emit(entry.channel_id)
                self.log.emit(f"[LIVE] {entry.platform}/{entry.channel_id} went live!")
            self.status_changed.emit()
        except Exception:
            entry.last_status = "error"
            self.status_changed.emit()

    def save_to_config(self, cfg):
        cfg["monitor_channels"] = [
            {"url": e.url, "interval": e.interval_secs, "auto_record": e.auto_record}
            for e in self.entries
        ]

    def load_from_config(self, cfg):
        for ch in cfg.get("monitor_channels", []):
            self.add_channel(ch["url"], ch.get("interval", 120), ch.get("auto_record", False))


# ── Direct URL Detection ──────────────────────────────────────────────────

def _detect_direct_media(url, log_fn=None):
    """Sniff a URL via HEAD request. Returns StreamInfo if it's a direct media file, else None."""
    MEDIA_TYPES = {
        "video/mp4": "mp4", "video/webm": "mp4", "video/x-matroska": "mp4",
        "video/quicktime": "mp4", "video/x-msvideo": "mp4", "video/x-flv": "mp4",
        "audio/mpeg": "mp4", "audio/mp3": "mp4", "audio/mp4": "mp4",
        "audio/ogg": "mp4", "audio/flac": "mp4", "audio/wav": "mp4",
        "audio/x-wav": "mp4", "audio/aac": "mp4",
        "application/vnd.apple.mpegurl": "hls", "audio/mpegurl": "hls",
        "application/x-mpegurl": "hls", "application/dash+xml": "hls",
        "application/octet-stream": "mp4",
    }
    MEDIA_EXTS = {
        ".mp4", ".webm", ".mkv", ".avi", ".mov", ".flv", ".wmv",
        ".mp3", ".m4a", ".ogg", ".flac", ".wav", ".aac", ".opus",
        ".m3u8", ".mpd", ".ts",
    }

    # Check by extension first (fast)
    parsed = urllib.parse.urlparse(url)
    ext = os.path.splitext(parsed.path)[1].lower()
    if ext in MEDIA_EXTS:
        fmt = "hls" if ext in (".m3u8", ".mpd") else "mp4"
        if log_fn:
            log_fn(f"Direct media URL detected by extension: {ext}")
        info = StreamInfo(platform="Direct", url=url, title=parsed.path.split("/")[-1])
        info.qualities.append(QualityInfo(name=f"direct ({ext})", url=url, format_type=fmt))
        return info

    # HEAD request to sniff Content-Type
    try:
        cmd = ["curl", "-sI", "-L", "-o", "/dev/null",
               "-w", "%{content_type}\\n%{url_effective}\\n%{size_download}",
               "-H", f"User-Agent: {CURL_UA}", url]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                           creationflags=_CREATE_NO_WINDOW)
        if r.returncode == 0:
            lines = r.stdout.strip().split("\n")
            ct = lines[0].split(";")[0].strip().lower() if lines else ""
            if ct in MEDIA_TYPES:
                fmt = MEDIA_TYPES[ct]
                if log_fn:
                    log_fn(f"Direct media URL detected: {ct}")
                info = StreamInfo(platform="Direct", url=url, title=parsed.path.split("/")[-1])
                info.qualities.append(QualityInfo(name=f"direct ({ct})", url=url, format_type=fmt))
                return info
    except Exception:
        pass

    return None


# ── Clipboard Monitor ─────────────────────────────────────────────────────

class ClipboardMonitor(QObject):
    """Monitors clipboard for new URLs and emits them."""
    url_detected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._last_clip = ""
        self._enabled = False
        self._timer = QTimer()
        self._timer.timeout.connect(self._check)

    def start(self):
        self._enabled = True
        self._last_clip = QApplication.clipboard().text() or ""
        self._timer.start(800)

    def stop(self):
        self._enabled = False
        self._timer.stop()

    @property
    def is_running(self):
        return self._enabled

    def _check(self):
        if not self._enabled:
            return
        try:
            text = QApplication.clipboard().text() or ""
            if text != self._last_clip and text.startswith("http"):
                self._last_clip = text
                # Basic URL validation
                if re.match(r'https?://[^\s]+', text):
                    self.url_detected.emit(text.strip())
        except Exception:
            pass


# ── Download History ───────────────────────────────────────────────────────

@dataclass
class HistoryEntry:
    date: str = ""
    platform: str = ""
    title: str = ""
    quality: str = ""
    size: str = ""
    path: str = ""


# ── Main Window ───────────────────────────────────────────────────────────

PLATFORM_BADGES = {
    "Kick":       {"color": CAT["green"],   "text": "Kick"},
    "Twitch":     {"color": CAT["mauve"],   "text": "Twitch"},
    "Rumble":     {"color": CAT["green"],   "text": "Rumble"},
    "SoundCloud": {"color": CAT["peach"],   "text": "SoundCloud"},
    "Reddit":     {"color": CAT["peach"],   "text": "Reddit"},
    "Audius":     {"color": CAT["mauve"],   "text": "Audius"},
    "Podcast":    {"color": CAT["yellow"],  "text": "Podcast"},
    "Direct":     {"color": CAT["blue"],    "text": "Direct"},
    "yt-dlp":     {"color": CAT["overlay1"],"text": "yt-dlp"},
}

TAB_STYLE = f"""
QPushButton#tab {{
    background-color: transparent;
    color: {CAT['overlay1']};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px 20px;
    font-weight: 600;
    font-size: 13px;
    border-radius: 0px;
}}
QPushButton#tab:hover {{
    color: {CAT['text']};
    background-color: {CAT['surface0']};
}}
QPushButton#tabActive {{
    background-color: transparent;
    color: {CAT['green']};
    border: none;
    border-bottom: 2px solid {CAT['green']};
    padding: 10px 20px;
    font-weight: 600;
    font-size: 13px;
    border-radius: 0px;
}}
"""


class StreamKeep(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"StreamKeep v{VERSION}")
        self.setMinimumSize(900, 750)
        self.resize(980, 830)
        self.stream_info = None
        self.download_worker = None
        self._vod_list = []
        self._vod_checks = []
        self._segment_checks = []
        self._segment_progress = []
        self._history = []
        self._config = _load_config()
        self.monitor = ChannelMonitor()
        self.monitor.status_changed.connect(self._refresh_monitor_table)
        self.monitor.channel_went_live.connect(self._on_channel_live)
        self.monitor.log.connect(self._log)
        self.monitor.load_from_config(self._config)
        self.clipboard_monitor = ClipboardMonitor()
        self.clipboard_monitor.url_detected.connect(self._on_clipboard_url)
        self._init_ui()
        self._apply_config()

    def _apply_config(self):
        cfg = self._config
        if cfg.get("output_dir"):
            self.output_input.setText(cfg["output_dir"])
        if cfg.get("segment_idx") is not None:
            self.segment_combo.setCurrentIndex(cfg["segment_idx"])
        for h in cfg.get("history", []):
            self._history.append(HistoryEntry(**h))
        self._refresh_history_table()

    def _persist_config(self):
        cfg = self._config
        cfg["output_dir"] = self.output_input.text().strip()
        cfg["segment_idx"] = self.segment_combo.currentIndex()
        cfg["history"] = [{"date": h.date, "platform": h.platform, "title": h.title,
                           "quality": h.quality, "size": h.size, "path": h.path}
                          for h in self._history[-200:]]  # keep last 200
        self.monitor.save_to_config(cfg)
        _save_config(cfg)

    def closeEvent(self, event):
        self._persist_config()
        super().closeEvent(event)

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ── Header + Tabs ─────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("StreamKeep")
        title.setObjectName("title")
        ver = QLabel(f"v{VERSION}")
        ver.setObjectName("subtitle")
        header.addWidget(title)
        header.addWidget(ver)
        header.addSpacing(24)

        self._tab_btns = []
        self._tab_names = ["Download", "Monitor", "History", "Settings"]
        for i, name in enumerate(self._tab_names):
            btn = QPushButton(name)
            btn.setObjectName("tabActive" if i == 0 else "tab")
            btn.setStyleSheet(TAB_STYLE)
            btn.clicked.connect(lambda checked, idx=i: self._switch_tab(idx))
            header.addWidget(btn)
            self._tab_btns.append(btn)

        header.addStretch()
        root.addLayout(header)

        # ── Stacked Widget ────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_download_tab())
        self._stack.addWidget(self._build_monitor_tab())
        self._stack.addWidget(self._build_history_tab())
        self._stack.addWidget(self._build_settings_tab())
        root.addWidget(self._stack, 1)

        # ── Bottom Status Bar ─────────────────────────────────────────
        bottom = QHBoxLayout()
        self.status_label = QLabel("Paste a URL and click Fetch")
        self.status_label.setStyleSheet(f"color: {CAT['overlay1']}; font-size: 12px;")
        bottom.addWidget(self.status_label)
        bottom.addStretch()
        self.overall_progress = QProgressBar()
        self.overall_progress.setFixedWidth(200)
        self.overall_progress.setFixedHeight(8)
        self.overall_progress.setVisible(False)
        bottom.addWidget(self.overall_progress)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setFixedWidth(70)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._on_stop)
        bottom.addWidget(self.stop_btn)
        self.open_folder_btn = QPushButton("Open Folder")
        self.open_folder_btn.setVisible(False)
        self.open_folder_btn.clicked.connect(self._on_open_folder)
        bottom.addWidget(self.open_folder_btn)
        root.addLayout(bottom)

    def _switch_tab(self, idx):
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tab_btns):
            btn.setObjectName("tabActive" if i == idx else "tab")
            btn.setStyleSheet(TAB_STYLE)

    # ── Download Tab ──────────────────────────────────────────────────

    def _build_download_tab(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(12)

        # ── URL Input Card ────────────────────────────────────────────
        url_card = QFrame()
        url_card.setObjectName("card")
        url_lay = QVBoxLayout(url_card)
        url_lay.setSpacing(8)

        sec1 = QLabel("Stream URL")
        sec1.setObjectName("sectionTitle")
        url_lay.addWidget(sec1)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste URL — kick.com/user, twitch.tv/user, rumble.com/v..., or any video URL")
        self.url_input.returnPressed.connect(lambda: self._on_fetch())
        self.url_input.textChanged.connect(self._on_url_changed)
        url_row.addWidget(self.url_input)

        self.platform_badge = QLabel("")
        self.platform_badge.setFixedHeight(32)
        self.platform_badge.setVisible(False)
        self.platform_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        url_row.addWidget(self.platform_badge)

        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setObjectName("primary")
        self.fetch_btn.setFixedWidth(90)
        self.fetch_btn.clicked.connect(self._on_fetch)
        url_row.addWidget(self.fetch_btn)

        self.clip_btn = QPushButton("Clipboard Watch")
        self.clip_btn.setCheckable(True)
        self.clip_btn.setFixedWidth(130)
        self.clip_btn.clicked.connect(self._on_toggle_clipboard)
        url_row.addWidget(self.clip_btn)
        url_lay.addLayout(url_row)

        self.info_label = QLabel("")
        self.info_label.setObjectName("streamInfo")
        self.info_label.setVisible(False)
        url_lay.addWidget(self.info_label)

        # VOD picker table
        self.vod_widget = QWidget()
        vod_main_lay = QVBoxLayout(self.vod_widget)
        vod_main_lay.setContentsMargins(0, 4, 0, 0)
        vod_main_lay.setSpacing(6)

        vod_header = QHBoxLayout()
        vod_title = QLabel("Available VODs")
        vod_title.setStyleSheet(f"color: {CAT['peach']}; font-weight: bold; font-size: 13px;")
        vod_header.addWidget(vod_title)
        vod_header.addStretch()
        self.vod_select_all_cb = QCheckBox("Select All")
        self.vod_select_all_cb.setChecked(False)
        self.vod_select_all_cb.stateChanged.connect(self._on_vod_select_all)
        vod_header.addWidget(self.vod_select_all_cb)
        vod_main_lay.addLayout(vod_header)

        self.vod_table = QTableWidget()
        self.vod_table.setColumnCount(5)
        self.vod_table.setHorizontalHeaderLabels(["", "Platform", "Title", "Date", "Duration"])
        self.vod_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.vod_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.vod_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.vod_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.vod_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.vod_table.setColumnWidth(0, 36)
        self.vod_table.setColumnWidth(1, 70)
        self.vod_table.setColumnWidth(3, 160)
        self.vod_table.setColumnWidth(4, 90)
        self.vod_table.verticalHeader().setVisible(False)
        self.vod_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.vod_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.vod_table.setMaximumHeight(180)
        vod_main_lay.addWidget(self.vod_table)

        vod_btn_row = QHBoxLayout()
        vod_btn_row.addStretch()
        self.vod_load_btn = QPushButton("Load Selected")
        self.vod_load_btn.clicked.connect(self._on_vod_load_single)
        vod_btn_row.addWidget(self.vod_load_btn)
        self.vod_dl_all_btn = QPushButton("Download All Checked")
        self.vod_dl_all_btn.setObjectName("primary")
        self.vod_dl_all_btn.clicked.connect(self._on_vod_download_all)
        vod_btn_row.addWidget(self.vod_dl_all_btn)
        vod_main_lay.addLayout(vod_btn_row)

        self.vod_widget.setVisible(False)
        url_lay.addWidget(self.vod_widget)
        root.addWidget(url_card)

        # ── Settings Row ──────────────────────────────────────────────
        settings_card = QFrame()
        settings_card.setObjectName("card")
        settings_lay = QHBoxLayout(settings_card)
        settings_lay.setSpacing(12)

        settings_lay.addWidget(QLabel("Quality:"))
        self.quality_combo = QComboBox()
        self.quality_combo.setFixedWidth(220)
        self.quality_combo.setEnabled(False)
        settings_lay.addWidget(self.quality_combo)

        settings_lay.addSpacing(16)
        settings_lay.addWidget(QLabel("Segment:"))
        self.segment_combo = QComboBox()
        self.segment_combo.setFixedWidth(150)
        self._segment_options = [
            ("15 minutes", 900), ("30 minutes", 1800), ("1 hour", 3600),
            ("2 hours", 7200), ("4 hours", 14400), ("Full stream", 0),
        ]
        for label, _ in self._segment_options:
            self.segment_combo.addItem(label)
        self.segment_combo.setCurrentIndex(2)
        self.segment_combo.currentIndexChanged.connect(self._on_segment_length_changed)
        settings_lay.addWidget(self.segment_combo)

        settings_lay.addSpacing(16)
        settings_lay.addWidget(QLabel("Output:"))
        self.output_input = QLineEdit(str(Path.home() / "Desktop" / "StreamKeep"))
        self.output_input.setMinimumWidth(200)
        settings_lay.addWidget(self.output_input, 1)

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._on_browse)
        settings_lay.addWidget(browse_btn)
        root.addWidget(settings_card)

        # ── Splitter: Segments Table + Log ────────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)

        table_frame = QFrame()
        table_frame.setObjectName("card")
        table_lay = QVBoxLayout(table_frame)
        table_lay.setSpacing(6)

        table_header = QHBoxLayout()
        sec2 = QLabel("Segments")
        sec2.setObjectName("sectionTitle")
        table_header.addWidget(sec2)
        table_header.addStretch()
        self.select_all_cb = QCheckBox("Select All")
        self.select_all_cb.setChecked(True)
        self.select_all_cb.stateChanged.connect(self._on_select_all)
        table_header.addWidget(self.select_all_cb)
        table_lay.addLayout(table_header)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["", "Segment", "Time Range", "Progress", "Size"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 36)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(4, 90)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table_lay.addWidget(self.table)
        splitter.addWidget(table_frame)

        log_frame = QFrame()
        log_frame.setObjectName("card")
        log_lay = QVBoxLayout(log_frame)
        log_lay.setSpacing(4)
        sec3 = QLabel("Log")
        sec3.setObjectName("sectionTitle")
        log_lay.addWidget(sec3)
        self.log_text = QTextEdit()
        self.log_text.setObjectName("log")
        self.log_text.setReadOnly(True)
        log_lay.addWidget(self.log_text)
        splitter.addWidget(log_frame)
        splitter.setSizes([400, 200])
        root.addWidget(splitter, 1)

        # Download button row
        dl_row = QHBoxLayout()
        dl_row.addStretch()
        self.download_btn = QPushButton("Download Selected")
        self.download_btn.setObjectName("primary")
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._on_download)
        dl_row.addWidget(self.download_btn)
        root.addLayout(dl_row)

        return page

    # ── Monitor Tab ───────────────────────────────────────────────────

    def _build_monitor_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)

        header = QHBoxLayout()
        sec = QLabel("Channel Monitor")
        sec.setObjectName("sectionTitle")
        header.addWidget(sec)
        header.addStretch()
        header.addWidget(QLabel("Checks channels for live status and can auto-record"))
        card_lay.addLayout(header)

        # Add channel row
        add_row = QHBoxLayout()
        self.monitor_url_input = QLineEdit()
        self.monitor_url_input.setPlaceholderText("Channel URL (kick.com/user, twitch.tv/user)")
        add_row.addWidget(self.monitor_url_input)
        self.monitor_interval_spin = QSpinBox()
        self.monitor_interval_spin.setRange(30, 600)
        self.monitor_interval_spin.setValue(120)
        self.monitor_interval_spin.setSuffix("s")
        self.monitor_interval_spin.setFixedWidth(80)
        self.monitor_interval_spin.setStyleSheet(
            f"QSpinBox {{ background: {CAT['surface0']}; color: {CAT['text']}; "
            f"border: 1px solid {CAT['surface1']}; border-radius: 6px; padding: 4px; }}"
        )
        add_row.addWidget(self.monitor_interval_spin)
        self.monitor_auto_cb = QCheckBox("Auto-Record")
        add_row.addWidget(self.monitor_auto_cb)
        add_btn = QPushButton("Add")
        add_btn.setObjectName("primary")
        add_btn.setFixedWidth(70)
        add_btn.clicked.connect(self._on_monitor_add)
        add_row.addWidget(add_btn)
        card_lay.addLayout(add_row)

        # Monitor table
        self.monitor_table = QTableWidget()
        self.monitor_table.setColumnCount(6)
        self.monitor_table.setHorizontalHeaderLabels(["Platform", "Channel", "Status", "Interval", "Auto-Record", ""])
        self.monitor_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.monitor_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.monitor_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.monitor_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.monitor_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.monitor_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.monitor_table.setColumnWidth(0, 70)
        self.monitor_table.setColumnWidth(2, 80)
        self.monitor_table.setColumnWidth(3, 70)
        self.monitor_table.setColumnWidth(4, 90)
        self.monitor_table.setColumnWidth(5, 70)
        self.monitor_table.verticalHeader().setVisible(False)
        self.monitor_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.monitor_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        card_lay.addWidget(self.monitor_table)

        lay.addWidget(card, 1)
        return page

    # ── History Tab ───────────────────────────────────────────────────

    def _build_history_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)

        header = QHBoxLayout()
        sec = QLabel("Download History")
        sec.setObjectName("sectionTitle")
        header.addWidget(sec)
        header.addStretch()
        clear_btn = QPushButton("Clear History")
        clear_btn.clicked.connect(self._on_clear_history)
        header.addWidget(clear_btn)
        card_lay.addLayout(header)

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(6)
        self.history_table.setHorizontalHeaderLabels(["Date", "Platform", "Title", "Quality", "Size", "Path"])
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.history_table.setColumnWidth(0, 140)
        self.history_table.setColumnWidth(1, 70)
        self.history_table.setColumnWidth(3, 100)
        self.history_table.setColumnWidth(4, 80)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.doubleClicked.connect(self._on_history_double_click)
        card_lay.addWidget(self.history_table)

        lay.addWidget(card, 1)
        return page

    # ── Settings Tab ──────────────────────────────────────────────────

    def _build_settings_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)
        card_lay.setSpacing(16)

        sec = QLabel("Settings")
        sec.setObjectName("sectionTitle")
        card_lay.addWidget(sec)

        info = QLabel(
            f"Config saved to: {CONFIG_FILE}\n"
            f"Supported platforms: {', '.join(Extractor.all_names())}"
        )
        info.setStyleSheet(f"color: {CAT['overlay1']}; font-size: 11px;")
        card_lay.addWidget(info)

        # Default output
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Default output directory:"))
        self.settings_output = QLineEdit(str(Path.home() / "Desktop" / "StreamKeep"))
        row1.addWidget(self.settings_output, 1)
        browse = QPushButton("Browse")
        browse.clicked.connect(lambda: self._settings_browse(self.settings_output))
        row1.addWidget(browse)
        card_lay.addLayout(row1)

        # Browser cookies section
        cookies_sec = QLabel("Browser Cookies")
        cookies_sec.setStyleSheet(f"color: {CAT['lavender']}; font-weight: bold; font-size: 13px;")
        card_lay.addWidget(cookies_sec)
        cookies_hint = QLabel(
            "For age-restricted or auth-required content (YouTube, etc.). "
            "Select a browser to use its cookies, or browse for a cookies.txt file."
        )
        cookies_hint.setStyleSheet(f"color: {CAT['overlay1']}; font-size: 11px;")
        cookies_hint.setWordWrap(True)
        card_lay.addWidget(cookies_hint)

        # Browser combo + Scan
        row_cookies = QHBoxLayout()
        row_cookies.addWidget(QLabel("Browser:"))
        self.cookies_combo = QComboBox()
        self.cookies_combo.setFixedWidth(220)
        self.cookies_combo.addItem("None")
        row_cookies.addWidget(self.cookies_combo)
        scan_btn = QPushButton("Scan for Browsers")
        scan_btn.clicked.connect(self._on_scan_browsers)
        row_cookies.addWidget(scan_btn)
        row_cookies.addStretch()
        card_lay.addLayout(row_cookies)

        # Cookies file browse
        row_cookiefile = QHBoxLayout()
        row_cookiefile.addWidget(QLabel("Or cookies file:"))
        self.cookies_file_input = QLineEdit()
        self.cookies_file_input.setPlaceholderText("Path to cookies.txt (Netscape format)")
        row_cookiefile.addWidget(self.cookies_file_input, 1)
        browse_cookies = QPushButton("Browse")
        browse_cookies.clicked.connect(self._on_browse_cookies_file)
        row_cookiefile.addWidget(browse_cookies)
        card_lay.addLayout(row_cookiefile)

        # Scan results label
        self.cookies_scan_label = QLabel("")
        self.cookies_scan_label.setStyleSheet(f"color: {CAT['subtext0']}; font-size: 11px;")
        self.cookies_scan_label.setWordWrap(True)
        card_lay.addWidget(self.cookies_scan_label)

        # Load saved settings
        saved_browser = self._config.get("cookies_browser", "")
        saved_file = self._config.get("cookies_file", "")
        if saved_file:
            self.cookies_file_input.setText(saved_file)
            YtDlpExtractor.cookies_file = saved_file
        # Auto-scan on load to populate the combo
        self._scan_browsers_silent()
        if saved_browser:
            idx = self.cookies_combo.findText(saved_browser)
            if idx >= 0:
                self.cookies_combo.setCurrentIndex(idx)
            YtDlpExtractor.cookies_browser = saved_browser

        # ffmpeg / yt-dlp info
        row2 = QHBoxLayout()
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5,
                               creationflags=_CREATE_NO_WINDOW)
            ff_ver = r.stdout.split("\n")[0] if r.returncode == 0 else "Not found"
        except Exception:
            ff_ver = "Not found"
        try:
            r = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=5,
                               creationflags=_CREATE_NO_WINDOW)
            yt_ver = f"yt-dlp {r.stdout.strip()}" if r.returncode == 0 else "Not installed"
        except Exception:
            yt_ver = "Not installed"
        tools_label = QLabel(f"ffmpeg: {ff_ver[:60]}  |  {yt_ver}")
        tools_label.setStyleSheet(f"color: {CAT['subtext0']}; font-size: 11px;")
        row2.addWidget(tools_label)
        card_lay.addLayout(row2)

        # Save button
        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._on_save_settings)
        save_row.addWidget(save_btn)
        card_lay.addLayout(save_row)

        card_lay.addStretch()
        lay.addWidget(card, 1)
        return page

    def _settings_browse(self, line_edit):
        d = QFileDialog.getExistingDirectory(self, "Select Folder", line_edit.text())
        if d:
            line_edit.setText(d)

    def _scan_browsers(self):
        """Scan for installed browsers by checking cookie database locations."""
        return _scan_browser_cookies()

    def _scan_browsers_silent(self):
        """Populate combo with scanned browsers without UI feedback."""
        found = self._scan_browsers()
        self.cookies_combo.clear()
        self.cookies_combo.addItem("None")
        seen = set()
        for display, ytdlp_name, path in found:
            label = f"{display} ({ytdlp_name})"
            if label not in seen:
                self.cookies_combo.addItem(label, ytdlp_name)
                seen.add(label)
        # Also add manual entries for common browsers not found
        for name in ["chrome", "chromium", "firefox", "edge", "brave", "opera", "vivaldi", "safari"]:
            manual_label = f"{name} (manual)"
            if not any(name == ytdlp for _, ytdlp, _ in found):
                self.cookies_combo.addItem(manual_label, name)

    def _on_scan_browsers(self):
        found = self._scan_browsers()
        self.cookies_combo.clear()
        self.cookies_combo.addItem("None")
        seen = set()
        for display, ytdlp_name, path in found:
            label = f"{display} ({ytdlp_name})"
            if label not in seen:
                self.cookies_combo.addItem(label, ytdlp_name)
                seen.add(label)
        # Manual fallbacks
        for name in ["chrome", "chromium", "firefox", "edge", "brave"]:
            manual_label = f"{name} (manual)"
            if not any(name == ytdlp for _, ytdlp, _ in found):
                self.cookies_combo.addItem(manual_label, name)

        if found:
            details = "\n".join(f"  {d} -> {p}" for d, _, p in found)
            self.cookies_scan_label.setText(f"Found {len(found)} browser(s):\n{details}")
            self._log(f"[SCAN] Found {len(found)} browser cookie stores:")
            for d, y, p in found:
                self._log(f"  {d} ({y}) -> {p}")
        else:
            self.cookies_scan_label.setText("No browser cookie stores found.")

    def _on_browse_cookies_file(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select Cookies File", str(Path.home()),
            "Cookie files (*.txt *.sqlite);;All files (*)"
        )
        if f:
            self.cookies_file_input.setText(f)

    def _on_save_settings(self):
        self.output_input.setText(self.settings_output.text())
        # Apply browser cookies setting
        browser_text = self.cookies_combo.currentText()
        browser_data = self.cookies_combo.currentData()
        if browser_text == "None":
            YtDlpExtractor.cookies_browser = ""
            self._config["cookies_browser"] = ""
        else:
            ytdlp_name = browser_data if browser_data else browser_text
            YtDlpExtractor.cookies_browser = ytdlp_name
            self._config["cookies_browser"] = ytdlp_name
        # Apply cookies file
        cookies_file = self.cookies_file_input.text().strip()
        YtDlpExtractor.cookies_file = cookies_file
        self._config["cookies_file"] = cookies_file
        self._persist_config()
        self.status_label.setText("Settings saved")

    # ── Actions ───────────────────────────────────────────────────────

    def _log(self, msg):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _update_badge(self, platform_name=None):
        if platform_name and platform_name in PLATFORM_BADGES:
            badge = PLATFORM_BADGES[platform_name]
            self.platform_badge.setText(f" {badge['text']} ")
            self.platform_badge.setStyleSheet(
                f"background-color: {badge['color']}; color: {CAT['crust']}; "
                f"border-radius: 6px; font-weight: bold; font-size: 11px; padding: 2px 8px;"
            )
            self.platform_badge.setVisible(True)
        else:
            self.platform_badge.setVisible(False)

    def _on_url_changed(self, text):
        ext = Extractor.detect(text.strip())
        if ext:
            self._update_badge(ext.NAME)
            ch = ext.extract_channel_id(text.strip())
            if ch:
                self.output_input.setText(str(Path.home() / "Desktop" / _safe_filename(ch)))
        else:
            self._update_badge(None)

    def _on_toggle_clipboard(self, checked):
        if checked:
            self.clipboard_monitor.start()
            self.clip_btn.setStyleSheet(
                f"background-color: {CAT['green']}; color: {CAT['crust']}; "
                f"border: none; border-radius: 6px; font-weight: 600;"
            )
            self._log("[CLIPBOARD] Monitoring started — copy a URL to auto-load")
            self.status_label.setText("Clipboard monitoring active")
        else:
            self.clipboard_monitor.stop()
            self.clip_btn.setStyleSheet("")
            self._log("[CLIPBOARD] Monitoring stopped")

    def _on_clipboard_url(self, url):
        self._log(f"[CLIPBOARD] Detected: {url}")
        self.url_input.setText(url)
        self._switch_tab(0)  # Switch to Download tab
        self._on_fetch()

    def _on_fetch(self, vod_source=None, vod_platform=None):
        url = self.url_input.text().strip()
        if not url:
            return
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("...")
        self.download_btn.setEnabled(False)
        self.quality_combo.clear()
        self.quality_combo.setEnabled(False)
        self.table.setRowCount(0)
        self.info_label.setVisible(False)
        if not vod_source:
            self.vod_widget.setVisible(False)
        self.status_label.setText("Fetching stream info...")

        self._fetch_worker = FetchWorker(url, vod_source=vod_source, vod_platform=vod_platform)
        self._fetch_worker.log.connect(self._log)
        self._fetch_worker.finished.connect(self._on_fetch_done)
        self._fetch_worker.vods_found.connect(self._on_vods_found)
        self._fetch_worker.error.connect(self._on_fetch_error)
        self._fetch_worker.start()

    def _on_fetch_done(self, info):
        self.stream_info = info
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self._update_badge(info.platform)

        # Populate qualities
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        selected_idx = 0
        for i, q in enumerate(info.qualities):
            bw_mbps = q.bandwidth / 1_000_000 if q.bandwidth else 0
            ft_tag = f" [{q.format_type.upper()}]" if q.format_type != "hls" else ""
            label = f"{q.name} ({q.resolution}, {bw_mbps:.1f} Mbps){ft_tag}"
            self.quality_combo.addItem(label, q)
            if "1080" in q.name or "source" in q.name.lower():
                selected_idx = i
        self.quality_combo.setCurrentIndex(selected_idx)
        self.quality_combo.setEnabled(len(info.qualities) > 0)
        self.quality_combo.blockSignals(False)

        # Stream info
        parts = [f"Platform: {info.platform}", f"Duration: {info.duration_str}"]
        if info.start_time:
            try:
                dt = datetime.fromisoformat(info.start_time.replace("Z", "+00:00"))
                parts.append(f"Started: {dt.strftime('%Y-%m-%d %I:%M %p UTC')}")
            except Exception:
                pass
        if info.segment_count:
            parts.append(f"Segments: {info.segment_count}")
        self.info_label.setText("  |  ".join(parts))
        self.info_label.setVisible(True)

        self._build_segments(info.total_secs)
        self.download_btn.setEnabled(True)
        self.status_label.setText("Ready — select segments and click Download")

    def _on_fetch_error(self, err):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self._log(f"[ERROR] {err}")
        self.status_label.setText(f"Error: {err}")

    def _on_vods_found(self, vod_list, platform_name):
        self._vod_list = vod_list
        self._vod_checks = []
        self.vod_table.setRowCount(len(vod_list))
        self._update_badge(platform_name)

        for i, v in enumerate(vod_list):
            cb = QCheckBox()
            cb_widget = QWidget()
            cb_lay = QHBoxLayout(cb_widget)
            cb_lay.addWidget(cb)
            cb_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_lay.setContentsMargins(0, 0, 0, 0)
            self.vod_table.setCellWidget(i, 0, cb_widget)
            self._vod_checks.append(cb)

            # Platform badge
            badge = PLATFORM_BADGES.get(v.platform, {})
            plat_item = QTableWidgetItem(v.platform)
            plat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if badge.get("color"):
                plat_item.setForeground(QColor(badge["color"]))
            self.vod_table.setItem(i, 1, plat_item)

            # Title
            live = " [LIVE]" if v.is_live else ""
            title_item = QTableWidgetItem(f"{v.title}{live}")
            if v.is_live:
                title_item.setForeground(QColor(CAT["green"]))
            self.vod_table.setItem(i, 2, title_item)

            date_item = QTableWidgetItem(v.date)
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 3, date_item)

            dur_item = QTableWidgetItem(v.duration if v.duration else "Live")
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 4, dur_item)

        self.vod_widget.setVisible(True)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self.status_label.setText(f"Found {len(vod_list)} VOD(s) — check and Load or Download All")

    def _on_vod_select_all(self, state):
        checked = state == Qt.CheckState.Checked.value
        for cb in self._vod_checks:
            cb.setChecked(checked)

    def _on_vod_load_single(self):
        for i, cb in enumerate(self._vod_checks):
            if cb.isChecked():
                vod = self._vod_list[i]
                self._log(f"\nLoading VOD: {vod.title} ({vod.date})")
                self._on_fetch(vod_source=vod.source, vod_platform=vod.platform)
                return
        self._log("No VOD checked.")

    def _on_vod_download_all(self):
        checked = [self._vod_list[i] for i, cb in enumerate(self._vod_checks) if cb.isChecked()]
        if not checked:
            self._log("No VODs checked.")
            return

        self._batch_vods = checked
        self._batch_idx = 0
        self._batch_total = len(checked)
        self._log(f"\n{'=' * 50}")
        self._log(f"Batch downloading {self._batch_total} VOD(s)")
        self._log(f"{'=' * 50}")
        self.download_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.vod_dl_all_btn.setEnabled(False)
        self.vod_load_btn.setEnabled(False)
        self.stop_btn.setVisible(True)
        self._batch_next()

    def _batch_next(self):
        if self._batch_idx >= self._batch_total:
            self._batch_done()
            return
        vod = self._batch_vods[self._batch_idx]
        self._log(f"\n--- VOD {self._batch_idx + 1}/{self._batch_total}: {vod.title} ---")
        self.status_label.setText(f"VOD {self._batch_idx + 1}/{self._batch_total}: Fetching...")

        worker = FetchWorker(self.url_input.text().strip(), vod_source=vod.source, vod_platform=vod.platform)
        worker.log.connect(self._log)
        worker.finished.connect(self._batch_on_fetched)
        worker.error.connect(self._batch_on_fetch_error)
        self._batch_fetch_worker = worker
        worker.start()

    def _batch_on_fetched(self, info):
        vod = self._batch_vods[self._batch_idx]

        # Pick quality (prefer 1080p/source)
        playlist_url = None
        fmt_type = "hls"
        audio_url = ""
        selected_q = None
        for q in info.qualities:
            if "1080" in q.name or "source" in q.name.lower():
                selected_q = q
                break
        if not selected_q and info.qualities:
            selected_q = info.qualities[0]
        if selected_q:
            playlist_url = selected_q.url
            fmt_type = selected_q.format_type
            audio_url = selected_q.audio_url

        if not playlist_url:
            self._log(f"[ERROR] No playback URL for {vod.title}")
            self._batch_idx += 1
            self._batch_next()
            return

        total_secs = info.total_secs
        seg_secs = self._get_segment_secs()

        if fmt_type == "mp4" or seg_secs == 0 or total_secs <= 0:
            segments = [(0, "full_stream", 0, int(total_secs) if total_secs > 0 else 0)]
        else:
            segments = []
            pos, idx = 0, 0
            while pos < total_secs:
                end = min(pos + seg_secs, total_secs)
                segments.append((idx, f"part_{idx + 1}", pos, int(end - pos)))
                pos = end
                idx += 1

        date_part = vod.date.replace(" ", "_").replace(":", "-")[:16]
        safe_title = _safe_filename(vod.title)
        vod_folder = f"{date_part}_{safe_title}"
        out_dir = os.path.join(self.output_input.text().strip(), vod_folder)
        os.makedirs(out_dir, exist_ok=True)

        self._build_segments(total_secs)
        self.stream_info = info

        self._total_segments = len(segments)
        self._completed_segments = 0
        self.overall_progress.setVisible(True)
        self.overall_progress.setValue(0)
        self.overall_progress.setMaximum(len(segments))
        self.status_label.setText(f"VOD {self._batch_idx + 1}/{self._batch_total}: Downloading...")

        worker = DownloadWorker(playlist_url, segments, out_dir, format_type=fmt_type)
        worker.audio_url = audio_url
        worker.progress.connect(self._on_dl_progress)
        worker.segment_done.connect(self._on_segment_done)
        worker.error.connect(self._on_dl_error)
        worker.log.connect(self._log)
        worker.all_done.connect(self._batch_vod_done)
        self.download_worker = worker
        worker.start()

    def _batch_on_fetch_error(self, err):
        self._log(f"[ERROR] {err}")
        self._batch_idx += 1
        self._batch_next()

    def _batch_vod_done(self):
        vod = self._batch_vods[self._batch_idx]
        self._log(f"[DONE] {vod.title}")
        self._batch_idx += 1
        self._batch_next()

    def _batch_done(self):
        self._log(f"\n{'=' * 50}")
        self._log(f"Batch complete! {self._batch_total} VOD(s) downloaded.")
        self._log(f"{'=' * 50}")
        self.status_label.setText(f"Batch complete — {self._batch_total} VOD(s)")
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.vod_dl_all_btn.setEnabled(True)
        self.vod_load_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(True)
        out_dir = self.output_input.text().strip()
        self._save_metadata(out_dir, "batch")
        self._persist_config()

    # ── Segment Management ────────────────────────────────────────────

    def _get_segment_secs(self):
        idx = self.segment_combo.currentIndex()
        return self._segment_options[idx][1]

    def _build_segments(self, total_secs):
        if total_secs <= 0:
            self.table.setRowCount(0)
            return
        seg_secs = self._get_segment_secs()
        if seg_secs == 0:
            segments = [(0, total_secs)]
        else:
            segments = []
            pos = 0
            while pos < total_secs:
                end = min(pos + seg_secs, total_secs)
                segments.append((pos, end))
                pos = end

        self.table.setRowCount(len(segments))
        self._segment_checks = []
        self._segment_progress = []

        for i, (start, end) in enumerate(segments):
            duration = end - start
            cb = QCheckBox()
            cb.setChecked(True)
            cb_w = QWidget()
            cb_l = QHBoxLayout(cb_w)
            cb_l.addWidget(cb)
            cb_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_l.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(i, 0, cb_w)
            self._segment_checks.append(cb)

            if seg_secs == 0:
                label = "Full Stream"
            elif seg_secs < 3600:
                label = f"Part {i + 1}"
            else:
                label = f"Hour {i + 1}"
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 1, item)

            s_str = f"{int(start//3600):02d}:{int((start%3600)//60):02d}:{int(start%60):02d}"
            e_str = f"{int(end//3600):02d}:{int((end%3600)//60):02d}:{int(end%60):02d}"
            t_item = QTableWidgetItem(f"{s_str} - {e_str}  ({int(duration//60)}m {int(duration%60)}s)")
            t_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 2, t_item)

            pbar = QProgressBar()
            pbar.setValue(0)
            self.table.setCellWidget(i, 3, pbar)
            self._segment_progress.append(pbar)

            sz = QTableWidgetItem("\u2014")
            sz.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 4, sz)

    def _on_select_all(self, state):
        checked = state == Qt.CheckState.Checked.value
        for cb in self._segment_checks:
            cb.setChecked(checked)

    def _on_segment_length_changed(self, idx):
        if self.stream_info and self.stream_info.total_secs > 0:
            self._build_segments(self.stream_info.total_secs)

    def _on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_input.text())
        if d:
            self.output_input.setText(d)

    # ── Download ──────────────────────────────────────────────────────

    def _on_download(self):
        if not self.stream_info:
            return
        total_secs = self.stream_info.total_secs
        if total_secs <= 0:
            self._log("[ERROR] No duration info")
            return

        q_data = self.quality_combo.currentData()
        audio_url = ""
        if q_data:
            playlist_url = q_data.url
            fmt_type = q_data.format_type
            audio_url = q_data.audio_url
        elif self.stream_info.url:
            playlist_url = self.stream_info.url
            fmt_type = "hls"
        else:
            self._log("[ERROR] No quality selected")
            return

        seg_secs = self._get_segment_secs()
        segments = []
        for i, cb in enumerate(self._segment_checks):
            if cb.isChecked():
                if fmt_type == "mp4" or seg_secs == 0:
                    segments.append((0, "full_stream", 0, int(total_secs)))
                    break
                else:
                    start = i * seg_secs
                    end = min((i + 1) * seg_secs, total_secs)
                    segments.append((i, f"part_{i + 1}", start, int(end - start)))

        if not segments:
            self._log("No segments selected.")
            return

        out_dir = self.output_input.text().strip()
        os.makedirs(out_dir, exist_ok=True)

        self._log(f"\n{'=' * 50}")
        self._log(f"Downloading {len(segments)} segments to {out_dir}")
        self._log(f"Quality: {self.quality_combo.currentText()}")
        self._log(f"{'=' * 50}")

        self._total_segments = len(segments)
        self._completed_segments = 0
        self.download_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.stop_btn.setVisible(True)
        self.overall_progress.setVisible(True)
        self.overall_progress.setValue(0)
        self.overall_progress.setMaximum(len(segments))
        self.status_label.setText(f"Downloading 0/{len(segments)}...")

        self.download_worker = DownloadWorker(playlist_url, segments, out_dir, format_type=fmt_type)
        self.download_worker.audio_url = audio_url
        if audio_url:
            self._log(f"Audio merge: enabled (video-only format detected)")
        self.download_worker.progress.connect(self._on_dl_progress)
        self.download_worker.segment_done.connect(self._on_segment_done)
        self.download_worker.error.connect(self._on_dl_error)
        self.download_worker.log.connect(self._log)
        self.download_worker.all_done.connect(self._on_all_done)
        self.download_worker.start()

    def _on_dl_progress(self, idx, pct, status):
        if idx < len(self._segment_progress):
            self._segment_progress[idx].setValue(pct)

    def _on_segment_done(self, idx, size_str):
        if idx < len(self._segment_progress):
            self._segment_progress[idx].setValue(100)
            self._segment_progress[idx].setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {CAT['green']}; border-radius: 4px; }}"
            )
        self.table.setItem(idx, 4, QTableWidgetItem(size_str))
        self._completed_segments += 1
        self.overall_progress.setValue(self._completed_segments)
        self.status_label.setText(f"Downloading {self._completed_segments}/{self._total_segments}...")

    def _on_dl_error(self, idx, err):
        if idx < len(self._segment_progress):
            self._segment_progress[idx].setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {CAT['red']}; border-radius: 4px; }}"
            )
        self.table.setItem(idx, 4, QTableWidgetItem("FAILED"))

    def _on_all_done(self):
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(True)
        self.status_label.setText(f"Complete — {self._completed_segments} segments downloaded")
        self._log(f"\n{'=' * 50}")
        self._log("All downloads complete!")
        self._log(f"{'=' * 50}")
        out_dir = self.output_input.text().strip()
        q_name = self.quality_combo.currentText() if self.quality_combo.count() else ""
        self._save_metadata(out_dir, q_name)
        self._persist_config()

    def _on_stop(self):
        if hasattr(self, '_batch_vods'):
            self._batch_idx = self._batch_total
        if self.download_worker:
            self.download_worker.cancel()
            self.download_worker.wait(5000)
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        if hasattr(self, 'vod_dl_all_btn'):
            self.vod_dl_all_btn.setEnabled(True)
            self.vod_load_btn.setEnabled(True)
        self.status_label.setText("Cancelled")
        self._log("[CANCELLED] Download stopped by user.")

    def _on_open_folder(self):
        out_dir = self.output_input.text().strip()
        if os.path.isdir(out_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(out_dir))

    # ── Monitor Actions ───────────────────────────────────────────────

    def _on_monitor_add(self):
        url = self.monitor_url_input.text().strip()
        if not url:
            return
        interval = self.monitor_interval_spin.value()
        auto = self.monitor_auto_cb.isChecked()
        if self.monitor.add_channel(url, interval, auto):
            self.monitor_url_input.clear()
            self._log(f"[MONITOR] Added: {url} (every {interval}s, auto-record: {auto})")
            self._persist_config()
        else:
            self._log(f"[MONITOR] Cannot add: unsupported or duplicate")

    def _refresh_monitor_table(self):
        entries = self.monitor.entries
        self.monitor_table.setRowCount(len(entries))
        for i, e in enumerate(entries):
            badge = PLATFORM_BADGES.get(e.platform, {})
            plat = QTableWidgetItem(e.platform)
            plat.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if badge.get("color"):
                plat.setForeground(QColor(badge["color"]))
            self.monitor_table.setItem(i, 0, plat)

            ch = QTableWidgetItem(e.channel_id)
            self.monitor_table.setItem(i, 1, ch)

            status = QTableWidgetItem(e.last_status.upper())
            status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if e.last_status == "live":
                status.setForeground(QColor(CAT["green"]))
            elif e.last_status == "error":
                status.setForeground(QColor(CAT["red"]))
            else:
                status.setForeground(QColor(CAT["overlay1"]))
            self.monitor_table.setItem(i, 2, status)

            intv = QTableWidgetItem(f"{e.interval_secs}s")
            intv.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.monitor_table.setItem(i, 3, intv)

            auto = QTableWidgetItem("Yes" if e.auto_record else "No")
            auto.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if e.auto_record:
                auto.setForeground(QColor(CAT["green"]))
            self.monitor_table.setItem(i, 4, auto)

            rm_btn = QPushButton("Remove")
            rm_btn.setFixedHeight(28)
            rm_btn.clicked.connect(lambda checked, idx=i: self._on_monitor_remove(idx))
            self.monitor_table.setCellWidget(i, 5, rm_btn)

    def _on_monitor_remove(self, idx):
        self.monitor.remove_channel(idx)
        self._persist_config()

    def _on_channel_live(self, channel_id):
        """Called when a monitored channel goes live."""
        self.status_label.setText(f"{channel_id} went LIVE!")
        # Find the entry for auto-record
        for e in self.monitor.entries:
            if e.channel_id == channel_id and e.auto_record and not e.is_recording:
                self._log(f"[AUTO-RECORD] Starting recording for {e.platform}/{channel_id}")
                e.is_recording = True
                # Resolve and start recording in background
                ext = Extractor.detect(e.url)
                if ext:
                    info = ext.resolve(e.url, log_fn=self._log)
                    if info and info.qualities:
                        q = info.qualities[0]
                        out_dir = os.path.join(
                            self.output_input.text().strip(),
                            f"auto_{channel_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        )
                        os.makedirs(out_dir, exist_ok=True)
                        segments = [(0, "live_recording", 0, 0)]
                        worker = DownloadWorker(q.url, segments, out_dir, q.format_type)
                        worker.log.connect(self._log)
                        worker.all_done.connect(lambda: self._auto_record_done(channel_id))
                        self.download_worker = worker
                        worker.start()

    def _auto_record_done(self, channel_id):
        for e in self.monitor.entries:
            if e.channel_id == channel_id:
                e.is_recording = False
        self._log(f"[AUTO-RECORD] Recording ended for {channel_id}")

    # ── History Actions ───────────────────────────────────────────────

    def _add_history(self, platform, title, quality, size, path):
        entry = HistoryEntry(
            date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            platform=platform, title=title[:60],
            quality=quality, size=size, path=path
        )
        self._history.append(entry)
        self._refresh_history_table()

    def _refresh_history_table(self):
        self.history_table.setRowCount(len(self._history))
        for i, h in enumerate(reversed(self._history)):
            row = len(self._history) - 1 - i
            for col, val in enumerate([h.date, h.platform, h.title, h.quality, h.size, h.path]):
                item = QTableWidgetItem(val)
                if col in (0, 1, 3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.history_table.setItem(i, col, item)

    def _on_clear_history(self):
        self._history.clear()
        self._refresh_history_table()
        self._persist_config()

    def _on_history_double_click(self, index):
        row = index.row()
        if row < len(self._history):
            h = list(reversed(self._history))[row]
            if os.path.isdir(h.path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(h.path))

    # ── Metadata ──────────────────────────────────────────────────────

    def _save_metadata(self, out_dir, quality_name=""):
        if self.stream_info:
            MetadataSaver.save(out_dir, self.stream_info)
        # Add to history
        platform = self.stream_info.platform if self.stream_info else "?"
        title = self.stream_info.title if self.stream_info else "?"
        self._add_history(platform, title, quality_name, "", out_dir)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    _setup_crash_logging()

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5,
                       creationflags=_CREATE_NO_WINDOW)
    except FileNotFoundError:
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "StreamKeep", "ffmpeg not found in PATH.\nInstall ffmpeg and try again.")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    win = StreamKeep()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
