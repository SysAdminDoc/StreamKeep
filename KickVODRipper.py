"""
KickVODRipper v0.1.0
GUI tool for downloading Kick.com VOD/DVR streams in hourly segments.
"""

import sys, os, subprocess, re, json, math, time
from pathlib import Path
from datetime import datetime, timedelta, timezone

def _bootstrap():
    """Auto-install dependencies before imports."""
    required = {"PyQt6": "PyQt6"}
    import importlib
    for mod, pkg in required.items():
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

_bootstrap()

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QTextEdit, QProgressBar, QComboBox, QFileDialog,
    QCheckBox, QFrame, QSplitter, QAbstractItemView, QStyle
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QUrl
from PyQt6.QtGui import QFont, QColor, QIcon, QPalette, QDesktopServices

VERSION = "0.4.0"

KICK_CHANNEL_RE = re.compile(r'(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)/?$')
CURL_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

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


# ── Worker Threads ────────────────────────────────────────────────────────

class FetchWorker(QThread):
    """Fetches and parses m3u8 playlist info. Accepts Kick channel URLs or direct m3u8 URLs."""
    finished = pyqtSignal(dict)
    vods_found = pyqtSignal(list)  # list of VOD dicts from Kick API
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, url, vod_source=None):
        super().__init__()
        self.url = url.strip()
        self.vod_source = vod_source  # If set, skip API lookup and use this m3u8 directly

    def _curl(self, url, timeout=30):
        r = subprocess.run(
            ["curl", "-s", "-L", "-H", f"User-Agent: {CURL_UA}",
             "-H", "Accept: application/json", url],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        return r.stdout if r.returncode == 0 else None

    def _curl_raw(self, url, timeout=30):
        r = subprocess.run(
            ["curl", "-s", "-L", url],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        return r.stdout if r.returncode == 0 else None

    def run(self):
        try:
            m3u8_url = self.vod_source

            if not m3u8_url:
                # Check if this is a Kick channel URL
                match = KICK_CHANNEL_RE.match(self.url)
                if match:
                    slug = match.group(1)
                    m3u8_url = self._resolve_kick_channel(slug)
                    if m3u8_url is None:
                        return  # error already emitted
                else:
                    m3u8_url = self.url

            # Now fetch and parse the m3u8
            self._fetch_m3u8(m3u8_url)

        except Exception as e:
            self.error.emit(str(e))

    def _resolve_kick_channel(self, slug):
        """Resolve a Kick channel slug to a DVR/VOD m3u8 URL via the API."""
        self.log.emit(f"Detected Kick channel: {slug}")
        self.log.emit(f"Fetching VOD list from Kick API...")

        body = self._curl(f"https://kick.com/api/v2/channels/{slug}/videos")
        if not body:
            self.error.emit(f"Failed to fetch Kick API for channel '{slug}'")
            return None

        try:
            vods = json.loads(body)
        except json.JSONDecodeError:
            self.error.emit("Invalid JSON from Kick API (may be rate-limited)")
            return None

        if not isinstance(vods, list) or len(vods) == 0:
            self.error.emit(f"No videos found for channel '{slug}'")
            return None

        # Emit full VOD list for the UI to show a picker
        vod_list = []
        for v in vods:
            source = v.get("source", "")
            if not source:
                continue
            dur_ms = v.get("duration", 0)
            dur_str = ""
            if dur_ms:
                dur_s = dur_ms / 1000
                dur_str = f"{int(dur_s // 3600)}h {int((dur_s % 3600) // 60)}m"
            vod_list.append({
                "title": v.get("session_title", "Untitled"),
                "date": v.get("created_at", ""),
                "source": source,
                "is_live": v.get("is_live", False),
                "viewers": v.get("viewer_count", 0),
                "duration": dur_str,
                "duration_ms": dur_ms,
            })

        self.log.emit(f"Found {len(vod_list)} VOD(s) for {slug}")
        for i, vod in enumerate(vod_list):
            live_tag = " [LIVE]" if vod["is_live"] else ""
            dur_tag = f" ({vod['duration']})" if vod["duration"] else ""
            self.log.emit(f"  {i + 1}. {vod['title']}{live_tag}{dur_tag} — {vod['date']}")

        if len(vod_list) == 1:
            # Only one VOD — use it directly
            self.log.emit(f"Auto-selecting only VOD: {vod_list[0]['title']}")
            return vod_list[0]["source"]
        else:
            # Multiple VODs — emit signal for UI picker, use most recent for now
            self.vods_found.emit(vod_list)
            return None  # UI will re-trigger with selected VOD

    def _fetch_m3u8(self, url):
        """Fetch an m3u8 URL and parse it."""
        self.log.emit(f"Fetching playlist: {url}")
        body = self._curl_raw(url)
        if not body or not body.startswith("#EXTM3U"):
            self.error.emit("Not a valid m3u8 playlist")
            return

        info = {"url": url, "qualities": [], "is_master": False}

        if "#EXT-X-STREAM-INF" in body:
            info["is_master"] = True
            self.log.emit("Detected master playlist — parsing qualities...")
            base = url.rsplit("/", 1)[0]
            res, bw = "?", 0
            for line in body.splitlines():
                if line.startswith("#EXT-X-STREAM-INF"):
                    attrs = line.split(":", 1)[1]
                    res_m = re.search(r'RESOLUTION=(\d+x\d+)', attrs)
                    bw_m = re.search(r'BANDWIDTH=(\d+)', attrs)
                    res = res_m.group(1) if res_m else "?"
                    bw = int(bw_m.group(1)) if bw_m else 0
                elif not line.startswith("#") and line.strip():
                    quality_url = f"{base}/{line.strip()}"
                    name = line.strip().split("/")[0]
                    info["qualities"].append({
                        "name": name, "url": quality_url,
                        "resolution": res, "bandwidth": bw
                    })

            if info["qualities"]:
                best = info["qualities"][0]
                self.log.emit(f"Fetching {best['name']} playlist for duration info...")
                r2 = self._curl_raw(best["url"])
                if r2:
                    self._parse_duration(r2, info)
        else:
            info["is_master"] = False
            self._parse_duration(body, info)
            base = url.rsplit("/", 2)[0]
            master_url = f"{base}/master.m3u8"
            self.log.emit(f"Checking for master playlist at {master_url}")
            r3 = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", master_url],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            if r3.stdout.strip() == "200":
                self.log.emit("Found master playlist — fetching qualities...")
                r4 = self._curl_raw(master_url)
                if r4:
                    mbase = master_url.rsplit("/", 1)[0]
                    res, bw = "?", 0
                    for line in r4.splitlines():
                        if line.startswith("#EXT-X-STREAM-INF"):
                            attrs = line.split(":", 1)[1]
                            res_m = re.search(r'RESOLUTION=(\d+x\d+)', attrs)
                            bw_m = re.search(r'BANDWIDTH=(\d+)', attrs)
                            res = res_m.group(1) if res_m else "?"
                            bw = int(bw_m.group(1)) if bw_m else 0
                        elif not line.startswith("#") and line.strip() and "RESOLUTION" not in line:
                            quality_url = f"{mbase}/{line.strip()}"
                            name = line.strip().split("/")[0]
                            info["qualities"].append({
                                "name": name, "url": quality_url,
                                "resolution": res, "bandwidth": bw
                            })

        self.log.emit(f"Stream duration: {info.get('duration_str', '?')}")
        self.log.emit(f"Qualities: {', '.join(q['name'] for q in info['qualities'])}")
        self.finished.emit(info)

    def _parse_duration(self, body, info):
        m = re.search(r'TOTAL-SECS[=:](\d+\.?\d*)', body)
        if m:
            total = float(m.group(1))
            info["total_secs"] = total
            hours = int(total // 3600)
            mins = int((total % 3600) // 60)
            secs = int(total % 60)
            info["duration_str"] = f"{hours}h {mins}m {secs}s"

        m2 = re.search(r'PROGRAM-DATE-TIME:(.+)', body)
        if m2:
            info["start_time"] = m2.group(1).strip()

        m3 = re.search(r'MEDIA-SEQUENCE:(\d+)', body)
        if m3:
            info["media_sequence"] = int(m3.group(1))

        seg_count = len(re.findall(r'#EXTINF:', body))
        info["segment_count"] = seg_count
        self.log.emit(f"Segments: {seg_count}")


class DownloadWorker(QThread):
    """Downloads segments using ffmpeg."""
    progress = pyqtSignal(int, int, str)  # segment_index, percent, status_text
    segment_done = pyqtSignal(int, str)   # segment_index, file_size
    log = pyqtSignal(str)
    error = pyqtSignal(int, str)          # segment_index, error_msg
    all_done = pyqtSignal()

    def __init__(self, playlist_url, segments, output_dir):
        super().__init__()
        self.playlist_url = playlist_url
        self.segments = segments  # list of (index, label, start_sec, duration_sec)
        self.output_dir = output_dir
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
                    self.log.emit(f"[SKIP] {label} already exists ({self._fmt_size(size)})")
                    self.segment_done.emit(seg_idx, self._fmt_size(size))
                    continue

            self.log.emit(f"[DL] {label} — start: {start}s, duration: {duration}s")
            self.progress.emit(seg_idx, 0, "Starting...")

            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "info",
                "-ss", str(start),
                "-i", self.playlist_url,
                "-t", str(duration),
                "-c", "copy",
                "-y", outfile
            ]

            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )

                # Parse ffmpeg stderr for progress
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
                        self.progress.emit(seg_idx, pct, f"{elapsed}s / {duration}s")

                self._proc.wait()

                if self._cancel:
                    if os.path.exists(outfile):
                        os.remove(outfile)
                    return

                if self._proc.returncode == 0 and os.path.exists(outfile):
                    size = os.path.getsize(outfile)
                    self.progress.emit(seg_idx, 100, "Complete")
                    self.segment_done.emit(seg_idx, self._fmt_size(size))
                    self.log.emit(f"[DONE] {label} — {self._fmt_size(size)}")
                else:
                    err = "\n".join(output_lines[-5:])
                    self.error.emit(seg_idx, f"ffmpeg exit {self._proc.returncode}")
                    self.log.emit(f"[FAIL] {label}\n{err}")

            except Exception as e:
                self.error.emit(seg_idx, str(e))
                self.log.emit(f"[ERROR] {label}: {e}")

        if not self._cancel:
            self.all_done.emit()

    @staticmethod
    def _fmt_size(b):
        for unit in ('B', 'KB', 'MB', 'GB'):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"


# ── Main Window ───────────────────────────────────────────────────────────

class KickVODRipper(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"KickVODRipper v{VERSION}")
        self.setMinimumSize(820, 700)
        self.resize(900, 780)
        self.stream_info = None
        self.download_worker = None
        self._init_ui()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        # ── Header ────────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("KickVODRipper")
        title.setObjectName("title")
        ver = QLabel(f"v{VERSION}")
        ver.setObjectName("subtitle")
        header.addWidget(title)
        header.addWidget(ver)
        header.addStretch()
        root.addLayout(header)

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
        self.url_input.setPlaceholderText("Paste Kick channel URL (kick.com/username) or m3u8 URL...")
        self.url_input.returnPressed.connect(lambda: self._on_fetch())
        self.url_input.textChanged.connect(self._on_url_changed)
        url_row.addWidget(self.url_input)

        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setObjectName("primary")
        self.fetch_btn.setFixedWidth(90)
        self.fetch_btn.clicked.connect(self._on_fetch)
        url_row.addWidget(self.fetch_btn)
        url_lay.addLayout(url_row)

        self.info_label = QLabel("")
        self.info_label.setObjectName("streamInfo")
        self.info_label.setVisible(False)
        url_lay.addWidget(self.info_label)

        # VOD picker (shown when VODs found for a channel)
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
        self.vod_table.setColumnCount(4)
        self.vod_table.setHorizontalHeaderLabels(["", "Title", "Date", "Duration"])
        self.vod_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.vod_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.vod_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.vod_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.vod_table.setColumnWidth(0, 36)
        self.vod_table.setColumnWidth(2, 160)
        self.vod_table.setColumnWidth(3, 90)
        self.vod_table.verticalHeader().setVisible(False)
        self.vod_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.vod_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.vod_table.setMaximumHeight(180)
        vod_main_lay.addWidget(self.vod_table)

        vod_btn_row = QHBoxLayout()
        vod_btn_row.addStretch()
        self.vod_load_btn = QPushButton("Load Selected")
        self.vod_load_btn.setToolTip("Load a single VOD to preview its segments")
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
        self.quality_combo.setFixedWidth(180)
        self.quality_combo.setEnabled(False)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        settings_lay.addWidget(self.quality_combo)

        settings_lay.addSpacing(16)
        settings_lay.addWidget(QLabel("Segment:"))
        self.segment_combo = QComboBox()
        self.segment_combo.setFixedWidth(150)
        self._segment_options = [
            ("15 minutes", 900),
            ("30 minutes", 1800),
            ("1 hour", 3600),
            ("2 hours", 7200),
            ("4 hours", 14400),
            ("Full stream", 0),
        ]
        for label, _ in self._segment_options:
            self.segment_combo.addItem(label)
        self.segment_combo.setCurrentIndex(2)  # Default: 1 hour
        self.segment_combo.currentIndexChanged.connect(self._on_segment_length_changed)
        settings_lay.addWidget(self.segment_combo)

        settings_lay.addSpacing(16)
        settings_lay.addWidget(QLabel("Output:"))
        self.output_input = QLineEdit(str(Path.home() / "Desktop" / "fishtank"))
        self.output_input.setMinimumWidth(200)
        settings_lay.addWidget(self.output_input, 1)

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._on_browse)
        settings_lay.addWidget(browse_btn)

        root.addWidget(settings_card)

        # ── Splitter: Segments Table + Log ────────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Segments table
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

        # Log panel
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

        # ── Bottom Bar ────────────────────────────────────────────────
        bottom = QHBoxLayout()
        self.status_label = QLabel("Paste a stream URL and click Fetch")
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

        self.download_btn = QPushButton("Download Selected")
        self.download_btn.setObjectName("primary")
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._on_download)
        bottom.addWidget(self.download_btn)

        self.open_folder_btn = QPushButton("Open Folder")
        self.open_folder_btn.setVisible(False)
        self.open_folder_btn.clicked.connect(self._on_open_folder)
        bottom.addWidget(self.open_folder_btn)

        root.addLayout(bottom)

    # ── Actions ───────────────────────────────────────────────────────

    def _log(self, msg):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_fetch(self, vod_source=None):
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
        self.vod_widget.setVisible(False)
        self.status_label.setText("Fetching stream info...")

        self._fetch_worker = FetchWorker(url, vod_source=vod_source)
        self._fetch_worker.log.connect(self._log)
        self._fetch_worker.finished.connect(self._on_fetch_done)
        self._fetch_worker.vods_found.connect(self._on_vods_found)
        self._fetch_worker.error.connect(self._on_fetch_error)
        self._fetch_worker.start()

    def _on_fetch_done(self, info):
        self.stream_info = info
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")

        # Populate qualities
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        selected_idx = 0
        for i, q in enumerate(info.get("qualities", [])):
            bw_mbps = q["bandwidth"] / 1_000_000 if q["bandwidth"] else 0
            label = f"{q['name']} ({q['resolution']}, {bw_mbps:.1f} Mbps)"
            self.quality_combo.addItem(label, q)
            if "1080" in q["name"]:
                selected_idx = i
        self.quality_combo.setCurrentIndex(selected_idx)
        self.quality_combo.setEnabled(len(info.get("qualities", [])) > 0)
        self.quality_combo.blockSignals(False)

        # Stream info
        duration_str = info.get("duration_str", "Unknown")
        total_secs = info.get("total_secs", 0)
        start_time = info.get("start_time", "")
        info_parts = [f"Duration: {duration_str}"]
        if start_time:
            try:
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                info_parts.append(f"Started: {dt.strftime('%Y-%m-%d %I:%M %p UTC')}")
            except Exception:
                info_parts.append(f"Started: {start_time}")
        if info.get("segment_count"):
            info_parts.append(f"Segments: {info['segment_count']}")
        self.info_label.setText("  |  ".join(info_parts))
        self.info_label.setVisible(True)

        # Build hour segments
        self._build_segments(total_secs)

        self.download_btn.setEnabled(True)
        self.status_label.setText("Ready — select segments and click Download")

    def _on_fetch_error(self, err):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self._log(f"[ERROR] {err}")
        self.status_label.setText(f"Error: {err}")

    def _on_vods_found(self, vod_list):
        """Show VOD table when VODs are available."""
        self._vod_list = vod_list
        self._vod_checks = []
        self.vod_table.setRowCount(len(vod_list))

        for i, v in enumerate(vod_list):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(False)
            cb_widget = QWidget()
            cb_lay = QHBoxLayout(cb_widget)
            cb_lay.addWidget(cb)
            cb_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_lay.setContentsMargins(0, 0, 0, 0)
            self.vod_table.setCellWidget(i, 0, cb_widget)
            self._vod_checks.append(cb)

            # Title
            live = " [LIVE]" if v["is_live"] else ""
            title_item = QTableWidgetItem(f"{v['title']}{live}")
            if v["is_live"]:
                title_item.setForeground(QColor(CAT["green"]))
            self.vod_table.setItem(i, 1, title_item)

            # Date
            date_item = QTableWidgetItem(v["date"])
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 2, date_item)

            # Duration
            dur_item = QTableWidgetItem(v["duration"] if v["duration"] else "Live")
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 3, dur_item)

        self.vod_widget.setVisible(True)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self.status_label.setText(
            f"Found {len(vod_list)} VOD(s) — check the ones you want, "
            f"then Load one to preview or Download All Checked"
        )

    def _on_vod_select_all(self, state):
        checked = state == Qt.CheckState.Checked.value
        for cb in self._vod_checks:
            cb.setChecked(checked)

    def _on_vod_load_single(self):
        """Load the first checked VOD into the segments preview."""
        for i, cb in enumerate(self._vod_checks):
            if cb.isChecked():
                vod = self._vod_list[i]
                self._log(f"\nLoading VOD: {vod['title']} ({vod['date']})")
                self._log(f"Source: {vod['source']}")
                self._on_fetch(vod_source=vod["source"])
                return
        self._log("No VOD checked — check at least one to load.")

    def _on_vod_download_all(self):
        """Download all checked VODs sequentially."""
        checked_vods = []
        for i, cb in enumerate(self._vod_checks):
            if cb.isChecked():
                checked_vods.append(self._vod_list[i])

        if not checked_vods:
            self._log("No VODs checked — check at least one to download.")
            return

        self._batch_vods = checked_vods
        self._batch_idx = 0
        self._batch_total = len(checked_vods)
        self._log(f"\n{'=' * 50}")
        self._log(f"Batch downloading {self._batch_total} VOD(s)")
        self._log(f"{'=' * 50}")

        # Disable controls during batch
        self.download_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.vod_dl_all_btn.setEnabled(False)
        self.vod_load_btn.setEnabled(False)
        self.stop_btn.setVisible(True)

        self._batch_next()

    def _batch_next(self):
        """Fetch and download the next VOD in the batch queue."""
        if self._batch_idx >= self._batch_total:
            self._batch_done()
            return

        vod = self._batch_vods[self._batch_idx]
        self._log(f"\n--- VOD {self._batch_idx + 1}/{self._batch_total}: {vod['title']} ---")
        self._log(f"Source: {vod['source']}")
        self.status_label.setText(
            f"VOD {self._batch_idx + 1}/{self._batch_total}: Fetching {vod['title']}..."
        )

        worker = FetchWorker(self.url_input.text().strip(), vod_source=vod["source"])
        worker.log.connect(self._log)
        worker.finished.connect(self._batch_on_fetched)
        worker.error.connect(self._batch_on_fetch_error)
        self._batch_fetch_worker = worker
        worker.start()

    def _batch_on_fetched(self, info):
        """A batch VOD's m3u8 was fetched — now download it."""
        vod = self._batch_vods[self._batch_idx]
        total_secs = info.get("total_secs", 0)

        # Pick quality (prefer 1080p)
        playlist_url = None
        for q in info.get("qualities", []):
            if "1080" in q["name"]:
                playlist_url = q["url"]
                break
        if not playlist_url and info.get("qualities"):
            playlist_url = info["qualities"][0]["url"]
        if not playlist_url:
            playlist_url = info["url"]

        # Build segments
        seg_secs = self._get_segment_secs()
        if seg_secs == 0 or total_secs <= 0:
            segments = [(0, "full_stream", 0, int(total_secs))]
        else:
            segments = []
            pos = 0
            idx = 0
            while pos < total_secs:
                end = min(pos + seg_secs, total_secs)
                segments.append((idx, f"part_{idx + 1}", pos, int(end - pos)))
                pos = end
                idx += 1

        # Create a subfolder per VOD: date_title
        date_part = vod["date"].replace(" ", "_").replace(":", "-")[:16]
        safe_title = re.sub(r'[<>:"/\\|?*]', '', vod["title"])[:60].strip()
        vod_folder = f"{date_part}_{safe_title}"
        out_dir = os.path.join(self.output_input.text().strip(), vod_folder)
        os.makedirs(out_dir, exist_ok=True)

        # Update the segments table to show this VOD's segments
        self._build_segments(total_secs)
        self.stream_info = info

        # Show quality info
        q_name = "best available"
        for q in info.get("qualities", []):
            if q["url"] == playlist_url:
                q_name = q["name"]
                break
        self._log(f"Quality: {q_name} | Segments: {len(segments)} | Output: {out_dir}")
        self.status_label.setText(
            f"VOD {self._batch_idx + 1}/{self._batch_total}: "
            f"Downloading {len(segments)} segment(s)..."
        )

        self._total_segments = len(segments)
        self._completed_segments = 0
        self.overall_progress.setVisible(True)
        self.overall_progress.setValue(0)
        self.overall_progress.setMaximum(len(segments))

        worker = DownloadWorker(playlist_url, segments, out_dir)
        worker.progress.connect(self._on_dl_progress)
        worker.segment_done.connect(self._on_segment_done)
        worker.error.connect(self._on_dl_error)
        worker.log.connect(self._log)
        worker.all_done.connect(self._batch_vod_done)
        self.download_worker = worker
        worker.start()

    def _batch_on_fetch_error(self, err):
        vod = self._batch_vods[self._batch_idx]
        self._log(f"[ERROR] Failed to fetch VOD '{vod['title']}': {err}")
        self._batch_idx += 1
        self._batch_next()

    def _batch_vod_done(self):
        vod = self._batch_vods[self._batch_idx]
        self._log(f"[DONE] VOD complete: {vod['title']}")
        self._batch_idx += 1
        self._batch_next()

    def _batch_done(self):
        self._log(f"\n{'=' * 50}")
        self._log(f"Batch complete! {self._batch_total} VOD(s) downloaded.")
        self._log(f"{'=' * 50}")
        self.status_label.setText(f"Batch complete — {self._batch_total} VOD(s) downloaded")
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.vod_dl_all_btn.setEnabled(True)
        self.vod_load_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(True)

    def _get_segment_secs(self):
        """Return the selected segment duration in seconds, or 0 for full stream."""
        idx = self.segment_combo.currentIndex()
        return self._segment_options[idx][1]

    def _build_segments(self, total_secs):
        if total_secs <= 0:
            return

        seg_secs = self._get_segment_secs()

        if seg_secs == 0:
            # Full stream — single segment
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

            # Checkbox
            cb = QCheckBox()
            cb.setChecked(True)
            cb_widget = QWidget()
            cb_lay = QHBoxLayout(cb_widget)
            cb_lay.addWidget(cb)
            cb_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_lay.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(i, 0, cb_widget)
            self._segment_checks.append(cb)

            # Label
            if seg_secs == 0:
                label = "Full Stream"
            elif seg_secs < 3600:
                label = f"Part {i + 1}"
            else:
                label = f"Hour {i + 1}"
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 1, item)

            # Time range
            start_str = f"{int(start // 3600):02d}:{int((start % 3600) // 60):02d}:{int(start % 60):02d}"
            end_str = f"{int(end // 3600):02d}:{int((end % 3600) // 60):02d}:{int(end % 60):02d}"
            dur_min = int(duration // 60)
            dur_sec = int(duration % 60)
            time_item = QTableWidgetItem(f"{start_str} - {end_str}  ({dur_min}m {dur_sec}s)")
            time_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 2, time_item)

            # Progress bar
            pbar = QProgressBar()
            pbar.setValue(0)
            self.table.setCellWidget(i, 3, pbar)
            self._segment_progress.append(pbar)

            # Size placeholder
            size_item = QTableWidgetItem("—")
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 4, size_item)

    def _on_select_all(self, state):
        checked = state == Qt.CheckState.Checked.value
        for cb in self._segment_checks:
            cb.setChecked(checked)

    def _on_url_changed(self, text):
        """Auto-update output folder name when a Kick URL is pasted."""
        match = KICK_CHANNEL_RE.match(text.strip())
        if match:
            slug = match.group(1)
            self.output_input.setText(str(Path.home() / "Desktop" / slug))

    def _on_quality_changed(self, idx):
        pass  # Quality selection is read at download time

    def _on_segment_length_changed(self, idx):
        if self.stream_info and self.stream_info.get("total_secs", 0) > 0:
            self._build_segments(self.stream_info["total_secs"])

    def _on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_input.text())
        if d:
            self.output_input.setText(d)

    def _on_download(self):
        if not self.stream_info:
            return

        total_secs = self.stream_info.get("total_secs", 0)
        if total_secs <= 0:
            self._log("[ERROR] No duration info")
            return

        # Get selected quality URL
        q_data = self.quality_combo.currentData()
        if q_data:
            playlist_url = q_data["url"]
        elif not self.stream_info.get("is_master"):
            playlist_url = self.stream_info["url"]
        else:
            self._log("[ERROR] No quality selected")
            return

        # Gather selected segments
        seg_secs = self._get_segment_secs()
        segments = []
        for i, cb in enumerate(self._segment_checks):
            if cb.isChecked():
                if seg_secs == 0:
                    start = 0
                    duration = int(total_secs)
                    label = "full_stream"
                else:
                    start = i * seg_secs
                    end = min((i + 1) * seg_secs, total_secs)
                    duration = int(end - start)
                    label = f"part_{i + 1}"
                segments.append((i, label, start, duration))

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

        self.download_worker = DownloadWorker(playlist_url, segments, out_dir)
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

    def _on_stop(self):
        # Cancel any active batch
        if hasattr(self, '_batch_vods'):
            self._batch_idx = self._batch_total  # prevent _batch_next from continuing
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


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    # Check ffmpeg
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except FileNotFoundError:
        from PyQt6.QtWidgets import QMessageBox
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "KickVODRipper", "ffmpeg not found in PATH.\nInstall ffmpeg and try again.")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    win = KickVODRipper()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
