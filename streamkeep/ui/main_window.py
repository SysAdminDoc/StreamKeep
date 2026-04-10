"""StreamKeep main window — the full QMainWindow class, tabs, and handlers.

Phase 2 of the modularization moved this out of the root-level
StreamKeep.py. The class is still a god object (~3670 lines, 235 methods);
Phase 3 will carve it into per-tab widgets. For now the split wins us a
predictable file layout and keeps the runtime unchanged.
"""

import json
import os
import re
import subprocess
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QTextEdit, QProgressBar, QComboBox, QFileDialog,
    QCheckBox, QFrame, QSplitter, QAbstractItemView, QStackedWidget,
    QSpinBox, QGridLayout, QScrollArea, QSystemTrayIcon,
    QCompleter, QInputDialog
)
from PyQt6.QtCore import QStringListModel
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPixmap, QPainter, QBrush

# Package re-exports under legacy underscore-prefixed names so the existing
# method bodies below don't need modification.
from streamkeep import VERSION
from streamkeep.paths import _CREATE_NO_WINDOW, CONFIG_FILE
from streamkeep.config import (
    load_config as _load_config,
    save_config as _save_config,
    write_log_line as _write_log_line,
)
from streamkeep.theme import CAT
from streamkeep.models import HistoryEntry
from streamkeep.utils import (
    fmt_size as _fmt_size,
    fmt_duration as _fmt_duration,
    safe_filename as _safe_filename,
    default_output_dir as _default_output_dir,
    render_template as _render_template,
    build_template_context as _build_template_context,
    scan_browser_cookies as _scan_browser_cookies,
    DEFAULT_FOLDER_TEMPLATE,
    DEFAULT_FILE_TEMPLATE,
)
from streamkeep.http import set_native_proxy as _set_native_proxy
from streamkeep.extractors import (
    Extractor,
    TwitchExtractor,
    YtDlpExtractor,
)
from streamkeep.workers import (
    FetchWorker,
    DownloadWorker,
    PlaylistExpandWorker as _PlaylistExpandWorker,
    PageScrapeWorker as _PageScrapeWorker,
)
from streamkeep.metadata import MetadataSaver
from streamkeep.postprocess import (
    PostProcessor,
    ConvertWorker,
    VIDEO_CONTAINERS,
    AUDIO_CONTAINERS,
    AUDIO_CODECS,
    VIDEO_EXTS,
    AUDIO_EXTS,
    available_video_codec_keys as _available_video_codec_keys,
)
from streamkeep.monitor import ChannelMonitor
from streamkeep.clipboard import ClipboardMonitor

# Legacy NATIVE_PROXY compatibility — some UI code below assigns this.
NATIVE_PROXY = ""


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
    background-color: {CAT['panelSoft']};
    color: {CAT['muted']};
    border: 1px solid {CAT['stroke']};
    padding: 10px 18px;
    font-weight: 600;
    font-size: 13px;
    border-radius: 999px;
}}
QPushButton#tab:hover {{
    color: {CAT['text']};
    border-color: {CAT['accent']};
    background-color: {CAT['panel']};
}}
QPushButton#tabActive {{
    background-color: {CAT['accent']};
    color: {CAT['crust']};
    border: 1px solid {CAT['accent']};
    padding: 10px 18px;
    font-weight: 600;
    font-size: 13px;
    border-radius: 999px;
}}
"""


def _path_label(path_text, fallback="Choose folder"):
    path_text = (path_text or "").strip()
    if not path_text:
        return fallback
    try:
        p = Path(path_text)
        if p.name:
            return p.name
    except Exception:
        pass
    return path_text


class StreamKeep(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"StreamKeep v{VERSION}")
        self.setMinimumSize(1020, 800)
        self.resize(1120, 900)
        self.setAcceptDrops(True)  # Enable drag-and-drop URL support
        self.stream_info = None
        self.download_worker = None
        self._vod_list = []
        self._vod_checks = []
        self._segment_checks = []
        self._segment_progress = []
        self._last_auto_output = ""
        self._history = []
        self._config = _load_config()
        self._tray_icon = None
        self._folder_template = DEFAULT_FOLDER_TEMPLATE
        self._file_template = DEFAULT_FILE_TEMPLATE
        self._webhook_url = ""
        self._check_duplicates = True
        self._download_queue = []  # list of dicts: url, title, platform, status
        self._write_nfo = False
        self._parallel_connections = 4  # Multi-connection splits per direct MP4
        self._recent_urls = []  # most-recent-first list of distinct URLs
        self._bandwidth_rule = {
            "enabled": False,
            "start_hour": 9,
            "end_hour": 17,
            "limit": "500K",
        }
        self.monitor = ChannelMonitor()
        self.monitor.status_changed.connect(self._refresh_monitor_table)
        self.monitor.channel_went_live.connect(self._on_channel_live)
        self.monitor.new_vods_found.connect(self._on_new_vods_found)
        self.monitor.log.connect(self._log)
        self.monitor.load_from_config(self._config)
        self.clipboard_monitor = ClipboardMonitor()
        self.clipboard_monitor.url_detected.connect(self._on_clipboard_url)
        self._init_ui()
        self._apply_config()
        self._init_tray_icon()
        # Scheduler tick: checks scheduled queue items and bandwidth rules every 30s
        self._scheduler_timer = QTimer(self)
        self._scheduler_timer.timeout.connect(self._scheduler_tick)
        self._scheduler_timer.start(30_000)
        self._scheduler_tick()  # Apply bandwidth rule immediately on startup

    def _scheduler_tick(self):
        """Periodic check: start any queue items whose scheduled time has
        arrived, and update rate_limit based on bandwidth scheduler rules."""
        self._apply_bandwidth_schedule()
        # Check for ready scheduled items
        worker = getattr(self, "download_worker", None)
        if worker is not None and worker.isRunning():
            return
        has_ready = any(
            q.get("status") == "queued" and self._queue_item_ready(q)
            for q in self._download_queue
        )
        if has_ready:
            self._advance_queue()

    def _queue_item_ready(self, q):
        """Return True if the queue item is ready to download now."""
        start_at = q.get("start_at", "")
        if not start_at:
            return True
        try:
            ts = datetime.fromisoformat(start_at)
            return ts <= datetime.now()
        except Exception:
            return True

    def _apply_bandwidth_schedule(self):
        """Apply the active bandwidth rule based on current time-of-day."""
        rule = self._bandwidth_rule
        if not rule or not rule.get("enabled"):
            return
        try:
            start_h = int(rule.get("start_hour", 9))
            end_h = int(rule.get("end_hour", 17))
            limit = str(rule.get("limit", "") or "")
            now = datetime.now()
            hour = now.hour
            # Handle wrap-around (e.g. 22..6 = overnight)
            if start_h <= end_h:
                active = start_h <= hour < end_h
            else:
                active = hour >= start_h or hour < end_h
            if active:
                if YtDlpExtractor.rate_limit != limit:
                    YtDlpExtractor.rate_limit = limit
                    self._log(f"[BANDWIDTH] Active window ({start_h:02d}-{end_h:02d}): limit = {limit or 'unlimited'}")
            else:
                baseline = self._config.get("rate_limit", "") or ""
                if YtDlpExtractor.rate_limit != baseline:
                    YtDlpExtractor.rate_limit = baseline
                    self._log(f"[BANDWIDTH] Outside window: baseline = {baseline or 'unlimited'}")
        except Exception as e:
            self._log(f"[BANDWIDTH] Rule error: {e}")

    # ── Drag and Drop ─────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        url = ""
        if mime.hasUrls():
            for u in mime.urls():
                s = u.toString()
                if s.startswith("http"):
                    url = s
                    break
                # Local file dropped → treat as direct media URL
                if u.isLocalFile():
                    url = u.toLocalFile()
                    break
        if not url and mime.hasText():
            text = mime.text().strip()
            if text.startswith("http") or os.path.exists(text):
                url = text
        if not url:
            event.ignore()
            return
        if "\n" in url or "\r" in url or len(url) > 2048:
            self._set_status("Dropped content is not a valid URL.", "warning")
            event.ignore()
            return
        self._log(f"[DRAG] Loaded URL: {url[:100]}")
        self.url_input.setText(url)
        self._switch_tab(0)
        self._on_fetch()
        event.acceptProposedAction()

    def _apply_config(self):
        cfg = self._config
        if cfg.get("output_dir"):
            self.output_input.setText(cfg["output_dir"])
            self.settings_output.setText(cfg["output_dir"])
        seg_idx = cfg.get("segment_idx")
        if isinstance(seg_idx, int) and 0 <= seg_idx < self.segment_combo.count():
            self.segment_combo.setCurrentIndex(seg_idx)
        # Templates
        self._folder_template = cfg.get("folder_template") or DEFAULT_FOLDER_TEMPLATE
        self._file_template = cfg.get("file_template") or DEFAULT_FILE_TEMPLATE
        if hasattr(self, "folder_template_input"):
            self.folder_template_input.setText(self._folder_template)
        if hasattr(self, "file_template_input"):
            self.file_template_input.setText(self._file_template)
        # Webhook
        self._webhook_url = cfg.get("webhook_url", "") or ""
        if hasattr(self, "webhook_input"):
            self.webhook_input.setText(self._webhook_url)
        # Duplicate detection
        self._check_duplicates = bool(cfg.get("check_duplicates", True))
        if hasattr(self, "dup_check"):
            self.dup_check.setChecked(self._check_duplicates)
        # NFO export
        self._write_nfo = bool(cfg.get("write_nfo", False))
        if hasattr(self, "nfo_check"):
            self.nfo_check.setChecked(self._write_nfo)
        # Parallel connections per direct mp4
        try:
            conns = int(cfg.get("parallel_connections", 4))
        except (TypeError, ValueError):
            conns = 4
        self._parallel_connections = max(1, min(16, conns))
        if hasattr(self, "parallel_spin"):
            self.parallel_spin.setValue(self._parallel_connections)
        # Twitch chat download
        TwitchExtractor.download_chat_enabled = bool(cfg.get("download_twitch_chat", False))
        if hasattr(self, "chat_check"):
            self.chat_check.setChecked(TwitchExtractor.download_chat_enabled)
        # Post-processing presets
        PostProcessor.extract_audio = bool(cfg.get("pp_extract_audio", False))
        PostProcessor.normalize_loudness = bool(cfg.get("pp_normalize_loudness", False))
        PostProcessor.reencode_h265 = bool(cfg.get("pp_reencode_h265", False))
        PostProcessor.contact_sheet = bool(cfg.get("pp_contact_sheet", False))
        PostProcessor.split_by_chapter = bool(cfg.get("pp_split_by_chapter", False))
        # Converter
        PostProcessor.convert_video = bool(cfg.get("pp_convert_video", False))
        PostProcessor.convert_video_format = cfg.get("pp_convert_video_format") or "mp4"
        PostProcessor.convert_video_codec = cfg.get("pp_convert_video_codec") or "h264"
        PostProcessor.convert_video_scale = cfg.get("pp_convert_video_scale") or "original"
        PostProcessor.convert_video_fps = cfg.get("pp_convert_video_fps") or "original"
        PostProcessor.convert_audio = bool(cfg.get("pp_convert_audio", False))
        PostProcessor.convert_audio_format = cfg.get("pp_convert_audio_format") or "mp3"
        PostProcessor.convert_audio_codec = cfg.get("pp_convert_audio_codec") or "mp3"
        PostProcessor.convert_audio_bitrate = cfg.get("pp_convert_audio_bitrate") or "192k"
        PostProcessor.convert_audio_samplerate = cfg.get("pp_convert_audio_samplerate") or "original"
        PostProcessor.convert_delete_source = bool(cfg.get("pp_convert_delete_source", False))
        if hasattr(self, "pp_audio_check"):
            self.pp_audio_check.setChecked(PostProcessor.extract_audio)
            self.pp_loud_check.setChecked(PostProcessor.normalize_loudness)
            self.pp_h265_check.setChecked(PostProcessor.reencode_h265)
            self.pp_contact_check.setChecked(PostProcessor.contact_sheet)
            self.pp_split_check.setChecked(PostProcessor.split_by_chapter)
        if hasattr(self, "pp_convert_video_check"):
            self.pp_convert_video_check.setChecked(PostProcessor.convert_video)
            if PostProcessor.convert_video_format in VIDEO_CONTAINERS:
                self.pp_convert_video_format.setCurrentIndex(
                    VIDEO_CONTAINERS.index(PostProcessor.convert_video_format))
            vc_keys = _available_video_codec_keys()
            if PostProcessor.convert_video_codec in vc_keys:
                self.pp_convert_video_codec.setCurrentIndex(
                    vc_keys.index(PostProcessor.convert_video_codec))
            scale_items = ["original", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
            if PostProcessor.convert_video_scale in scale_items:
                self.pp_convert_video_scale.setCurrentIndex(
                    scale_items.index(PostProcessor.convert_video_scale))
            fps_items = ["original", "60", "30", "24"]
            if PostProcessor.convert_video_fps in fps_items:
                self.pp_convert_video_fps.setCurrentIndex(
                    fps_items.index(PostProcessor.convert_video_fps))
            self.pp_convert_audio_check.setChecked(PostProcessor.convert_audio)
            if PostProcessor.convert_audio_format in AUDIO_CONTAINERS:
                self.pp_convert_audio_format.setCurrentIndex(
                    AUDIO_CONTAINERS.index(PostProcessor.convert_audio_format))
            ac_keys = list(AUDIO_CODECS.keys())
            if PostProcessor.convert_audio_codec in ac_keys:
                self.pp_convert_audio_codec.setCurrentIndex(
                    ac_keys.index(PostProcessor.convert_audio_codec))
            br_items = ["96k", "128k", "192k", "256k", "320k"]
            if PostProcessor.convert_audio_bitrate in br_items:
                self.pp_convert_audio_bitrate.setCurrentIndex(
                    br_items.index(PostProcessor.convert_audio_bitrate))
            sr_items = ["original", "48000", "44100", "22050"]
            if PostProcessor.convert_audio_samplerate in sr_items:
                self.pp_convert_audio_samplerate.setCurrentIndex(
                    sr_items.index(PostProcessor.convert_audio_samplerate))
            self.pp_convert_delete_check.setChecked(PostProcessor.convert_delete_source)
        # Restore queue
        self._download_queue = [
            q for q in cfg.get("download_queue", [])
            if isinstance(q, dict) and q.get("url")
        ]
        # Restore recent URLs
        recents = cfg.get("recent_urls", [])
        if isinstance(recents, list):
            self._recent_urls = [str(u) for u in recents if u][:30]
            if hasattr(self, "_recent_url_model"):
                self._recent_url_model.setStringList(self._recent_urls)
        # Bandwidth schedule rule
        bw = cfg.get("bandwidth_rule")
        if isinstance(bw, dict):
            self._bandwidth_rule.update({
                "enabled": bool(bw.get("enabled", False)),
                "start_hour": int(bw.get("start_hour", 9) or 9),
                "end_hour": int(bw.get("end_hour", 17) or 17),
                "limit": str(bw.get("limit", "500K") or ""),
            })
        if hasattr(self, "bw_enable_check"):
            self.bw_enable_check.setChecked(self._bandwidth_rule["enabled"])
            self.bw_start_spin.setValue(self._bandwidth_rule["start_hour"])
            self.bw_end_spin.setValue(self._bandwidth_rule["end_hour"])
            self.bw_limit_input.setText(self._bandwidth_rule["limit"])
        for h in cfg.get("history", []):
            if not isinstance(h, dict):
                continue
            try:
                entry = HistoryEntry(
                    date=str(h.get("date", "")),
                    platform=str(h.get("platform", "")),
                    title=str(h.get("title", "")),
                    quality=str(h.get("quality", "")),
                    size=str(h.get("size", "")),
                    path=str(h.get("path", "")),
                    url=str(h.get("url", "")),
                )
                self._history.append(entry)
            except Exception:
                continue
        self._refresh_history_table()
        self._refresh_download_summary()
        self._refresh_monitor_summary()
        self._refresh_history_summary()

    def _persist_config(self):
        cfg = self._config
        cfg["output_dir"] = self.output_input.text().strip()
        cfg["segment_idx"] = self.segment_combo.currentIndex()
        cfg["history"] = [{"date": h.date, "platform": h.platform, "title": h.title,
                           "quality": h.quality, "size": h.size, "path": h.path,
                           "url": h.url}
                          for h in self._history[-200:]]  # keep last 200
        cfg["folder_template"] = self._folder_template
        cfg["file_template"] = self._file_template
        cfg["webhook_url"] = self._webhook_url
        cfg["check_duplicates"] = self._check_duplicates
        cfg["download_queue"] = list(self._download_queue)
        cfg["write_nfo"] = self._write_nfo
        cfg["parallel_connections"] = self._parallel_connections
        cfg["download_twitch_chat"] = TwitchExtractor.download_chat_enabled
        cfg["pp_extract_audio"] = PostProcessor.extract_audio
        cfg["pp_normalize_loudness"] = PostProcessor.normalize_loudness
        cfg["pp_reencode_h265"] = PostProcessor.reencode_h265
        cfg["pp_contact_sheet"] = PostProcessor.contact_sheet
        cfg["pp_split_by_chapter"] = PostProcessor.split_by_chapter
        cfg["pp_convert_video"] = PostProcessor.convert_video
        cfg["pp_convert_video_format"] = PostProcessor.convert_video_format
        cfg["pp_convert_video_codec"] = PostProcessor.convert_video_codec
        cfg["pp_convert_video_scale"] = PostProcessor.convert_video_scale
        cfg["pp_convert_video_fps"] = PostProcessor.convert_video_fps
        cfg["pp_convert_audio"] = PostProcessor.convert_audio
        cfg["pp_convert_audio_format"] = PostProcessor.convert_audio_format
        cfg["pp_convert_audio_codec"] = PostProcessor.convert_audio_codec
        cfg["pp_convert_audio_bitrate"] = PostProcessor.convert_audio_bitrate
        cfg["pp_convert_audio_samplerate"] = PostProcessor.convert_audio_samplerate
        cfg["pp_convert_delete_source"] = PostProcessor.convert_delete_source
        cfg["recent_urls"] = list(self._recent_urls)
        cfg["bandwidth_rule"] = dict(self._bandwidth_rule)
        self.monitor.save_to_config(cfg)
        _save_config(cfg)

    def _init_tray_icon(self):
        """Create a system tray icon for completion notifications.
        Falls back gracefully if tray isn't supported."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        # Build a simple colored icon programmatically (no asset file needed)
        pix = QPixmap(32, 32)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(CAT["green"])))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
        painter.end()
        self._tray_icon = QSystemTrayIcon(QIcon(pix), self)
        self._tray_icon.setToolTip(f"StreamKeep v{VERSION}")
        self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.showNormal()
            self.activateWindow()
            self.raise_()

    def _notify(self, title, message, icon=QSystemTrayIcon.MessageIcon.Information):
        """Show a tray notification if the window is not focused."""
        if self._tray_icon is None:
            return
        if self.isActiveWindow():
            return  # don't bother user if they're already looking at the app
        try:
            self._tray_icon.showMessage(title, message, icon, 5000)
        except Exception:
            pass

    def _send_webhook(self, event, title, details=""):
        """POST a webhook notification. Auto-detects Discord URLs and
        formats as an embed; otherwise sends plain JSON."""
        url = (self._webhook_url or "").strip()
        if not url:
            return
        is_discord = "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url
        platform = self.stream_info.platform if self.stream_info else "unknown"
        src_url = self.url_input.text().strip() if hasattr(self, "url_input") else ""
        color_map = {"complete": 5763719, "failed": 15548997, "started": 3447003}
        color = color_map.get(event, 5763719)
        if is_discord:
            payload = {
                "username": "StreamKeep",
                "embeds": [{
                    "title": f"StreamKeep: {event}",
                    "description": f"**{title[:200]}**",
                    "color": color,
                    "fields": [
                        {"name": "Platform", "value": platform or "—", "inline": True},
                        {"name": "Source", "value": (src_url or "—")[:1000], "inline": False},
                    ] + ([{"name": "Details", "value": details[:1000]}] if details else []),
                    "footer": {"text": f"StreamKeep v{VERSION}"},
                }],
            }
        else:
            payload = {
                "app": "StreamKeep",
                "version": VERSION,
                "event": event,
                "title": title,
                "platform": platform,
                "source": src_url,
                "details": details,
            }
        # Fire-and-forget via curl — don't block the UI on webhook latency
        try:
            cmd = ["curl", "-s", "-X", "POST",
                   "-H", "Content-Type: application/json",
                   "-d", json.dumps(payload),
                   "--max-time", "10",
                   url]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=_CREATE_NO_WINDOW)
        except Exception as e:
            self._log(f"[WEBHOOK] Send failed: {e}")

    def closeEvent(self, event):
        # Stop active download worker cleanly
        dw = getattr(self, "download_worker", None)
        if dw is not None and dw.isRunning():
            try:
                dw.cancel()
                dw.wait(3000)
            except Exception:
                pass
        # Stop monitor timer
        try:
            self.monitor._timer.stop()
        except Exception:
            pass
        # Stop clipboard monitor
        try:
            self.clipboard_monitor.stop()
        except Exception:
            pass
        self._persist_config()
        super().closeEvent(event)

    def _make_metric_card(self, label_text, value_text="--", sub_text=""):
        card = QFrame()
        card.setObjectName("metricCard")
        card.setMinimumHeight(92)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)

        label = QLabel(label_text)
        label.setObjectName("metricLabel")
        value = QLabel(value_text)
        value.setObjectName("metricValue")
        value.setWordWrap(True)
        sub = QLabel(sub_text)
        sub.setObjectName("metricSubvalue")
        sub.setWordWrap(True)
        sub.setVisible(bool(sub_text))

        lay.addWidget(label)
        lay.addWidget(value)
        lay.addWidget(sub)
        lay.addStretch(1)
        return card, value, sub

    def _make_field_block(self, title, hint=""):
        card = QFrame()
        card.setObjectName("subtleCard")
        card.setMinimumHeight(108)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        label = QLabel(title)
        label.setObjectName("fieldLabel")
        lay.addWidget(label)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("fieldHint")
            hint_label.setWordWrap(True)
            lay.addWidget(hint_label)

        return card, lay

    def _wrap_scroll_page(self, page):
        page.setObjectName("chrome")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("chrome")
        scroll.setWidget(page)
        return scroll

    def _style_table(self, table, row_height=46):
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.verticalHeader().setDefaultSectionSize(row_height)
        table.horizontalHeader().setHighlightSections(False)

    def _set_metric(self, value_label, sub_label, value, sub=""):
        value_label.setText(value)
        sub_label.setText(sub)
        sub_label.setVisible(bool(sub))

    def _can_autofill_output(self):
        current = self.output_input.text().strip() if hasattr(self, "output_input") else ""
        default_output = str(_default_output_dir())
        return not current or current == default_output or current == self._last_auto_output

    def _apply_auto_output(self, path_text):
        self._last_auto_output = path_text
        self.output_input.setText(path_text)

    def _set_status(self, message, tone="idle"):
        tones = {
            "idle": ("Standby", CAT["panelSoft"], CAT["subtext1"], CAT["stroke"]),
            "working": ("Working", CAT["accent"], CAT["crust"], CAT["accent"]),
            "success": ("Ready", CAT["accentSoft"], CAT["crust"], CAT["accentSoft"]),
            "warning": ("Alert", CAT["gold"], CAT["crust"], CAT["gold"]),
            "error": ("Error", CAT["red"], CAT["crust"], CAT["red"]),
        }
        pill, bg, fg, border = tones.get(tone, tones["idle"])
        self.status_pill.setText(pill)
        self.status_pill.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border: 1px solid {border}; "
            "border-radius: 999px; padding: 6px 10px; font-size: 11px; font-weight: 700;"
        )
        self.status_label.setText(message)
        self.status_label.setToolTip(message)

    def _refresh_download_summary(self):
        if not hasattr(self, "download_hero_title"):
            return

        url = self.url_input.text().strip() if hasattr(self, "url_input") else ""
        if self.stream_info:
            title = self.stream_info.title or "Source ready"
            summary_parts = []
            if self.stream_info.platform:
                summary_parts.append(self.stream_info.platform)
            if self.stream_info.channel:
                summary_parts.append(self.stream_info.channel)
            if self.stream_info.duration_str:
                summary_parts.append(self.stream_info.duration_str)
            if self.stream_info.is_live:
                summary_parts.append("Live capture")
            body = " | ".join(summary_parts) if summary_parts else "Metadata loaded and ready for download."
        elif url:
            ext = Extractor.detect(url)
            title = "Source detected" if ext else "Paste a supported URL"
            if ext:
                body = f"{ext.NAME} recognized. Click Fetch to inspect quality options and segment timing."
            else:
                body = "Kick, Twitch, Rumble, podcasts, audio sources, and yt-dlp compatible links are supported."
        else:
            title = "Capture streams and VODs with cleaner control"
            body = "Paste a source URL to inspect quality options, split recordings into segments, and keep output folders tidy."

        self.download_hero_title.setText(title)
        self.download_hero_body.setText(body)

        platform_value = self.stream_info.platform if self.stream_info else "Auto detect"
        platform_sub = "Detected after fetch" if self.stream_info else "Waiting for a supported URL"
        duration_value = self.stream_info.duration_str if self.stream_info and self.stream_info.duration_str else "Waiting"
        duration_sub = "Stream length" if self.stream_info else "Metadata not loaded yet"

        total_segments = len(self._segment_checks)
        checked_segments = sum(1 for cb in self._segment_checks if cb.isChecked())
        if total_segments:
            selection_value = f"{checked_segments}/{total_segments}"
            selection_sub = "segments selected"
        elif self.stream_info and self.stream_info.total_secs <= 0:
            selection_value = "Live"
            selection_sub = "capture runs until you stop it"
        else:
            selection_value = "Not ready"
            selection_sub = "segments appear after fetch"

        output_path = self.output_input.text().strip() if hasattr(self, "output_input") else ""
        output_sub = output_path if len(output_path) <= 50 else f"...{output_path[-47:]}"

        self._set_metric(self.download_platform_value, self.download_platform_sub, platform_value, platform_sub)
        self._set_metric(self.download_duration_value, self.download_duration_sub, duration_value, duration_sub)
        self._set_metric(self.download_selection_value, self.download_selection_sub, selection_value, selection_sub)
        self._set_metric(
            self.download_output_value,
            self.download_output_sub,
            _path_label(output_path),
            output_sub or "Choose a destination folder",
        )
        self.download_output_value.setToolTip(output_path)
        self.download_output_sub.setToolTip(output_path)

        if hasattr(self, "segment_summary_label"):
            if total_segments:
                self.segment_summary_label.setText(f"{checked_segments} of {total_segments} segment(s) selected")
            else:
                self.segment_summary_label.setText("Segments will appear after metadata is loaded.")

    def _refresh_vod_summary(self):
        if not hasattr(self, "vod_summary_label"):
            return
        total = len(self._vod_checks)
        checked = sum(1 for cb in self._vod_checks if cb.isChecked())
        if total:
            self.vod_summary_label.setText(f"{checked} of {total} VOD(s) selected")
        else:
            self.vod_summary_label.setText("Inspect a channel to browse available VODs.")

    def _refresh_monitor_summary(self):
        if not hasattr(self, "monitor_count_value"):
            return
        entries = self.monitor.entries
        total = len(entries)
        auto = sum(1 for e in entries if e.auto_record)
        live = sum(1 for e in entries if e.last_status == "live")

        self._set_metric(self.monitor_count_value, self.monitor_count_sub, str(total), "active entries")
        self._set_metric(self.monitor_auto_value, self.monitor_auto_sub, str(auto), "auto-record enabled")
        self._set_metric(self.monitor_live_value, self.monitor_live_sub, str(live), "currently live")

        if total:
            self.monitor_summary_label.setText(
                f"Watching {total} channel(s). Auto-record is enabled on {auto} of them."
            )
        else:
            self.monitor_summary_label.setText("Add a channel URL to start passive live monitoring.")

    def _refresh_history_summary(self):
        if not hasattr(self, "history_count_value"):
            return
        total = len(self._history)
        latest = self._history[-1] if self._history else None

        self._set_metric(self.history_count_value, self.history_count_sub, str(total), "saved downloads")
        latest_value = latest.date if latest else "No entries"
        latest_sub = (latest.title if latest else "Completed downloads appear here")[:40]
        self._set_metric(self.history_latest_value, self.history_latest_sub, latest_value, latest_sub)

        # Compute top platform and top channel from history
        if hasattr(self, "history_platform_value"):
            if total:
                from collections import Counter
                plat_counts = Counter(h.platform for h in self._history if h.platform)
                top_plat, top_plat_n = (plat_counts.most_common(1) or [("—", 0)])[0]
                plat_share = f"{top_plat_n} / {total} downloads" if top_plat_n else "no data"
                self._set_metric(self.history_platform_value, self.history_platform_sub,
                                 top_plat or "—", plat_share)
                # Top channel: parse channel from URL path (last segment of domain+path)
                ch_counts = Counter()
                for h in self._history:
                    if not h.url:
                        continue
                    try:
                        parsed = urllib.parse.urlparse(h.url)
                        parts = [p for p in parsed.path.strip("/").split("/") if p]
                        # kick.com/foo or twitch.tv/foo -> "foo"
                        if parts:
                            key = f"{parsed.netloc}/{parts[0]}"
                            ch_counts[key] += 1
                    except Exception:
                        pass
                if ch_counts:
                    top_ch, top_ch_n = ch_counts.most_common(1)[0]
                    self._set_metric(self.history_channel_value, self.history_channel_sub,
                                     top_ch[:24], f"{top_ch_n} download(s)")
                else:
                    self._set_metric(self.history_channel_value, self.history_channel_sub,
                                     "—", "no channel data")
            else:
                self._set_metric(self.history_platform_value, self.history_platform_sub,
                                 "—", "appears most in history")
                self._set_metric(self.history_channel_value, self.history_channel_sub,
                                 "—", "most downloaded")

        if total:
            self.history_summary_label.setText(
                "Double-click a row to open the saved folder in Explorer. "
                "Use the search box to filter by title, platform, path, or URL."
            )
        else:
            self.history_summary_label.setText("Download history builds automatically after each completed job.")

    def _init_ui(self):
        central = QWidget()
        central.setObjectName("chrome")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(16)

        header_card = QFrame()
        header_card.setObjectName("heroCard")
        header_lay = QVBoxLayout(header_card)
        header_lay.setContentsMargins(20, 18, 20, 18)
        header_lay.setSpacing(16)

        header_top = QHBoxLayout()
        header_top.setSpacing(18)
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        eyebrow = QLabel("Capture Suite")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("StreamKeep")
        title.setObjectName("title")
        subtitle = QLabel(
            "Premium stream and VOD capture with cleaner segmentation, monitoring, and download history."
        )
        subtitle.setObjectName("heroBody")
        subtitle.setWordWrap(True)
        title_col.addWidget(eyebrow)
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header_top.addLayout(title_col, 1)

        version_card, _, _ = self._make_metric_card("Version", f"v{VERSION}", "Local desktop build")
        version_card.setMaximumWidth(170)
        header_top.addWidget(version_card)
        header_lay.addLayout(header_top)

        tab_shell = QFrame()
        tab_shell.setObjectName("toolbar")
        tab_lay = QHBoxLayout(tab_shell)
        tab_lay.setContentsMargins(12, 10, 12, 10)
        tab_lay.setSpacing(10)

        self._tab_btns = []
        self._tab_names = ["Download", "Monitor", "History", "Settings"]
        for i, name in enumerate(self._tab_names):
            btn = QPushButton(name)
            btn.setObjectName("tabActive" if i == 0 else "tab")
            btn.setStyleSheet(TAB_STYLE)
            btn.clicked.connect(lambda checked, idx=i: self._switch_tab(idx))
            tab_lay.addWidget(btn)
            self._tab_btns.append(btn)
        tab_lay.addStretch(1)
        header_lay.addWidget(tab_shell)
        root.addWidget(header_card)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._wrap_scroll_page(self._build_download_tab()))
        self._stack.addWidget(self._wrap_scroll_page(self._build_monitor_tab()))
        self._stack.addWidget(self._wrap_scroll_page(self._build_history_tab()))
        self._stack.addWidget(self._wrap_scroll_page(self._build_settings_tab()))
        root.addWidget(self._stack, 1)

        footer = QFrame()
        footer.setObjectName("footerBar")
        footer_lay = QHBoxLayout(footer)
        footer_lay.setContentsMargins(16, 12, 16, 12)
        footer_lay.setSpacing(12)

        self.status_pill = QLabel("Standby")
        footer_lay.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignTop)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        footer_lay.addWidget(self.status_label, 1)

        self.overall_progress = QProgressBar()
        self.overall_progress.setFixedWidth(220)
        self.overall_progress.setFixedHeight(10)
        self.overall_progress.setVisible(False)
        footer_lay.addWidget(self.overall_progress, 0, Qt.AlignmentFlag.AlignVCenter)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setFixedWidth(88)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._on_stop)
        footer_lay.addWidget(self.stop_btn)

        self.open_folder_btn = QPushButton("Open Folder")
        self.open_folder_btn.setObjectName("secondary")
        self.open_folder_btn.setVisible(False)
        self.open_folder_btn.clicked.connect(self._on_open_folder)
        footer_lay.addWidget(self.open_folder_btn)
        root.addWidget(footer)

        self._set_status("Paste a URL to inspect a stream or VOD.", "idle")

    def _switch_tab(self, idx):
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tab_btns):
            btn.setObjectName("tabActive" if i == idx else "tab")
            btn.setStyleSheet(TAB_STYLE)

    # ── Download Tab ──────────────────────────────────────────────────

    def _build_download_tab(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("heroCard")
        hero_lay = QVBoxLayout(hero)
        hero_lay.setContentsMargins(18, 18, 18, 18)
        hero_lay.setSpacing(14)

        hero_top = QHBoxLayout()
        hero_top.setSpacing(14)
        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(4)
        hero_kicker = QLabel("Downloader")
        hero_kicker.setObjectName("eyebrow")
        self.download_hero_title = QLabel("Capture streams and VODs with cleaner control")
        self.download_hero_title.setObjectName("heroTitle")
        self.download_hero_title.setWordWrap(True)
        self.download_hero_body = QLabel(
            "Paste a source URL to inspect quality options, split recordings into segments, and keep output folders tidy."
        )
        self.download_hero_body.setObjectName("heroBody")
        self.download_hero_body.setWordWrap(True)
        hero_copy.addWidget(hero_kicker)
        hero_copy.addWidget(self.download_hero_title)
        hero_copy.addWidget(self.download_hero_body)
        hero_top.addLayout(hero_copy, 1)

        source_card, self.download_platform_value, self.download_platform_sub = self._make_metric_card(
            "Source", "Auto detect", "Waiting for a supported URL"
        )
        source_card.setMaximumWidth(190)
        hero_top.addWidget(source_card)
        hero_lay.addLayout(hero_top)

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(12)
        duration_card, self.download_duration_value, self.download_duration_sub = self._make_metric_card(
            "Duration", "Waiting", "Metadata not loaded yet"
        )
        selection_card, self.download_selection_value, self.download_selection_sub = self._make_metric_card(
            "Selection", "Not ready", "segments appear after fetch"
        )
        output_card, self.download_output_value, self.download_output_sub = self._make_metric_card(
            "Output", _path_label(str(_default_output_dir())), ""
        )
        metrics_row.addWidget(duration_card)
        metrics_row.addWidget(selection_card)
        metrics_row.addWidget(output_card, 1)
        hero_lay.addLayout(metrics_row)
        root.addWidget(hero)

        url_card = QFrame()
        url_card.setObjectName("card")
        url_lay = QVBoxLayout(url_card)
        url_lay.setContentsMargins(18, 18, 18, 18)
        url_lay.setSpacing(12)

        url_header = QVBoxLayout()
        url_header.setSpacing(4)
        sec1 = QLabel("Stream URL")
        sec1.setObjectName("sectionTitle")
        sec1_body = QLabel(
            "Inspect a channel, VOD, or direct media URL to unlock quality choices and segment controls."
        )
        sec1_body.setObjectName("sectionBody")
        sec1_body.setWordWrap(True)
        url_header.addWidget(sec1)
        url_header.addWidget(sec1_body)
        url_lay.addLayout(url_header)

        url_row = QHBoxLayout()
        url_row.setSpacing(10)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "Paste a URL: kick.com/user, twitch.tv/user, rumble.com/v..., or any video URL"
        )
        self.url_input.returnPressed.connect(lambda: self._on_fetch())
        self.url_input.textChanged.connect(self._on_url_changed)
        # Recent URLs autocomplete dropdown
        self._recent_url_model = QStringListModel(self._recent_urls)
        self._recent_url_completer = QCompleter(self._recent_url_model, self)
        self._recent_url_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._recent_url_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._recent_url_completer.setMaxVisibleItems(10)
        self.url_input.setCompleter(self._recent_url_completer)
        url_row.addWidget(self.url_input, 1)

        self.platform_badge = QLabel("")
        self.platform_badge.setFixedHeight(36)
        self.platform_badge.setMinimumWidth(96)
        self.platform_badge.setVisible(False)
        self.platform_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        url_row.addWidget(self.platform_badge)

        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setObjectName("primary")
        self.fetch_btn.setFixedWidth(100)
        self.fetch_btn.clicked.connect(self._on_fetch)
        url_row.addWidget(self.fetch_btn)

        self.expand_btn = QPushButton("Expand Playlist")
        self.expand_btn.setObjectName("secondary")
        self.expand_btn.setFixedWidth(130)
        self.expand_btn.setToolTip("For playlist/channel URLs, queue every video via yt-dlp")
        self.expand_btn.clicked.connect(self._on_expand_playlist)
        url_row.addWidget(self.expand_btn)

        self.scan_btn = QPushButton("Scan Page")
        self.scan_btn.setObjectName("secondary")
        self.scan_btn.setFixedWidth(110)
        self.scan_btn.setToolTip("Fetch the URL as HTML and extract all video/media links it references")
        self.scan_btn.clicked.connect(self._on_scan_page)
        url_row.addWidget(self.scan_btn)

        self.clip_btn = QPushButton("Clipboard Watch")
        self.clip_btn.setObjectName("toggleAccent")
        self.clip_btn.setCheckable(True)
        self.clip_btn.setFixedWidth(148)
        self.clip_btn.clicked.connect(self._on_toggle_clipboard)
        url_row.addWidget(self.clip_btn)
        url_lay.addLayout(url_row)

        url_hint = QLabel("Press Enter to fetch. Clipboard watch auto-loads the next copied URL.")
        url_hint.setObjectName("subtleText")
        url_lay.addWidget(url_hint)

        self.info_label = QLabel("")
        self.info_label.setObjectName("streamInfo")
        self.info_label.setWordWrap(True)
        self.info_label.setVisible(False)
        url_lay.addWidget(self.info_label)

        self.vod_widget = QFrame()
        self.vod_widget.setObjectName("subtleCard")
        vod_main_lay = QVBoxLayout(self.vod_widget)
        vod_main_lay.setContentsMargins(14, 14, 14, 14)
        vod_main_lay.setSpacing(10)

        vod_header = QHBoxLayout()
        vod_header_copy = QVBoxLayout()
        vod_header_copy.setSpacing(2)
        vod_title = QLabel("Available VODs")
        vod_title.setObjectName("sectionTitle")
        vod_hint = QLabel("Select one or more VODs to load for inspection or download in a batch.")
        vod_hint.setObjectName("sectionBody")
        vod_hint.setWordWrap(True)
        vod_header_copy.addWidget(vod_title)
        vod_header_copy.addWidget(vod_hint)
        vod_header.addLayout(vod_header_copy, 1)

        self.vod_summary_label = QLabel("Inspect a channel to browse available VODs.")
        self.vod_summary_label.setObjectName("tableHint")
        vod_header.addWidget(self.vod_summary_label)

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
        self.vod_table.setColumnWidth(1, 84)
        self.vod_table.setColumnWidth(3, 160)
        self.vod_table.setColumnWidth(4, 96)
        self.vod_table.verticalHeader().setVisible(False)
        self.vod_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.vod_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.vod_table.setMaximumHeight(220)
        self._style_table(self.vod_table, 42)
        vod_main_lay.addWidget(self.vod_table)

        vod_btn_row = QHBoxLayout()
        vod_btn_row.addStretch(1)
        self.vod_load_btn = QPushButton("Load Selected")
        self.vod_load_btn.setObjectName("secondary")
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

        controls_card = QFrame()
        controls_card.setObjectName("card")
        controls_lay = QGridLayout(controls_card)
        controls_lay.setContentsMargins(18, 18, 18, 18)
        controls_lay.setHorizontalSpacing(12)
        controls_lay.setVerticalSpacing(12)

        quality_block, quality_lay = self._make_field_block(
            "Quality", "Choose the best available rendition after metadata loads."
        )
        self.quality_combo = QComboBox()
        self.quality_combo.setEnabled(False)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        quality_lay.addWidget(self.quality_combo)
        controls_lay.addWidget(quality_block, 0, 0)

        segment_block, segment_lay = self._make_field_block(
            "Segment Length", "Split long recordings into predictable export chunks."
        )
        self.segment_combo = QComboBox()
        self._segment_options = [
            ("15 minutes", 900), ("30 minutes", 1800), ("1 hour", 3600),
            ("2 hours", 7200), ("4 hours", 14400), ("Full stream", 0),
        ]
        for label, _ in self._segment_options:
            self.segment_combo.addItem(label)
        self.segment_combo.setCurrentIndex(2)
        self.segment_combo.currentIndexChanged.connect(self._on_segment_length_changed)
        segment_lay.addWidget(self.segment_combo)
        controls_lay.addWidget(segment_block, 0, 1)

        output_block, output_lay = self._make_field_block(
            "Output Folder", "Downloads are saved exactly where you point the app."
        )
        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        self.output_input = QLineEdit(str(_default_output_dir()))
        self.output_input.textChanged.connect(self._refresh_download_summary)
        output_row.addWidget(self.output_input, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.setObjectName("secondary")
        browse_btn.clicked.connect(self._on_browse)
        output_row.addWidget(browse_btn)
        output_lay.addLayout(output_row)
        controls_lay.addWidget(output_block, 1, 0, 1, 2)
        controls_lay.setColumnStretch(0, 1)
        controls_lay.setColumnStretch(1, 1)
        root.addWidget(controls_card)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        table_frame = QFrame()
        table_frame.setObjectName("card")
        table_lay = QVBoxLayout(table_frame)
        table_lay.setContentsMargins(18, 18, 18, 18)
        table_lay.setSpacing(10)

        table_header = QHBoxLayout()
        table_copy = QVBoxLayout()
        table_copy.setSpacing(3)
        sec2 = QLabel("Segments")
        sec2.setObjectName("sectionTitle")
        sec2_body = QLabel("Review the generated cuts before exporting them.")
        sec2_body.setObjectName("sectionBody")
        table_copy.addWidget(sec2)
        table_copy.addWidget(sec2_body)
        table_header.addLayout(table_copy, 1)

        self.segment_summary_label = QLabel("Segments will appear after metadata is loaded.")
        self.segment_summary_label.setObjectName("tableHint")
        table_header.addWidget(self.segment_summary_label)

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
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(4, 96)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._style_table(self.table, 46)
        table_lay.addWidget(self.table)
        splitter.addWidget(table_frame)

        log_frame = QFrame()
        log_frame.setObjectName("card")
        log_lay = QVBoxLayout(log_frame)
        log_lay.setContentsMargins(18, 18, 18, 18)
        log_lay.setSpacing(10)

        log_header = QHBoxLayout()
        log_copy = QVBoxLayout()
        log_copy.setSpacing(3)
        sec3 = QLabel("Runtime Log")
        sec3.setObjectName("sectionTitle")
        sec3_body = QLabel("Live extractor output, progress details, and troubleshooting context.")
        sec3_body.setObjectName("sectionBody")
        log_copy.addWidget(sec3)
        log_copy.addWidget(sec3_body)
        log_header.addLayout(log_copy, 1)
        clear_log_btn = QPushButton("Clear Log")
        clear_log_btn.setObjectName("ghost")
        clear_log_btn.clicked.connect(lambda: self.log_text.clear())
        log_header.addWidget(clear_log_btn)
        log_lay.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setObjectName("log")
        self.log_text.setReadOnly(True)
        log_lay.addWidget(self.log_text)
        splitter.addWidget(log_frame)
        splitter.setSizes([450, 220])
        root.addWidget(splitter, 1)

        dl_row = QHBoxLayout()
        dl_hint = QLabel("Download the selected segments after the source is ready.")
        dl_hint.setObjectName("subtleText")
        dl_row.addWidget(dl_hint)
        dl_row.addStretch(1)
        self.queue_btn = QPushButton("Queue URL")
        self.queue_btn.setObjectName("secondary")
        self.queue_btn.setToolTip("Add the current URL to the download queue instead of downloading now")
        self.queue_btn.clicked.connect(self._on_queue_url)
        dl_row.addWidget(self.queue_btn)
        self.schedule_btn = QPushButton("Schedule...")
        self.schedule_btn.setObjectName("secondary")
        self.schedule_btn.setToolTip("Queue the URL and start it at a future time")
        self.schedule_btn.clicked.connect(self._on_schedule_url)
        dl_row.addWidget(self.schedule_btn)
        self.download_btn = QPushButton("Download Selected")
        self.download_btn.setObjectName("primary")
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._on_download)
        dl_row.addWidget(self.download_btn)
        root.addLayout(dl_row)

        # Queue panel — shows pending items
        queue_card = QFrame()
        queue_card.setObjectName("card")
        qcard_lay = QVBoxLayout(queue_card)
        qcard_lay.setContentsMargins(18, 14, 18, 14)
        qcard_lay.setSpacing(8)
        queue_header = QHBoxLayout()
        qt = QLabel("Download Queue")
        qt.setObjectName("sectionTitle")
        queue_header.addWidget(qt)
        queue_header.addStretch()
        clear_queue_btn = QPushButton("Clear Queue")
        clear_queue_btn.setObjectName("ghost")
        clear_queue_btn.clicked.connect(self._on_clear_queue)
        queue_header.addWidget(clear_queue_btn)
        qcard_lay.addLayout(queue_header)
        self.queue_table = QTableWidget()
        self.queue_table.setColumnCount(6)
        self.queue_table.setHorizontalHeaderLabels(
            ["Status", "Platform", "Title", "Added / Scheduled", "", ""])
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.queue_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.queue_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.queue_table.setColumnWidth(0, 96)
        self.queue_table.setColumnWidth(1, 90)
        self.queue_table.setColumnWidth(3, 160)
        self.queue_table.setColumnWidth(4, 66)   # move up/down
        self.queue_table.setColumnWidth(5, 84)   # remove
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setMaximumHeight(180)
        self._style_table(self.queue_table, 36)
        qcard_lay.addWidget(self.queue_table)
        root.addWidget(queue_card)

        self._refresh_download_summary()
        self._refresh_queue_table()

        return page

    def _on_expand_playlist(self):
        """Probe the URL for playlist/channel entries and queue them all."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a URL first.", "warning")
            return
        self.expand_btn.setEnabled(False)
        self._set_status("Probing for playlist/channel entries...", "working")
        self._log(f"[PLAYLIST] Probing: {url}")
        # Run in a throwaway thread to avoid blocking the UI
        worker = _PlaylistExpandWorker(url)
        worker.finished.connect(lambda entries, u=url: self._on_expand_done(u, entries))
        worker.error.connect(self._on_expand_error)
        worker.log.connect(self._log)
        self._expand_worker = worker
        worker.start()

    def _on_expand_done(self, source_url, entries):
        self.expand_btn.setEnabled(True)
        if not entries:
            self._set_status(
                "No playlist entries found. This URL may be a single video — use Fetch instead.",
                "warning",
            )
            return
        added = 0
        for e in entries:
            if self._queue_add(e.get("url", ""), title=e.get("title", ""), platform="yt-dlp"):
                added += 1
        self._log(f"[PLAYLIST] Queued {added} new of {len(entries)} total entries")
        self._set_status(
            f"Playlist expanded. Queued {added} new entries "
            f"({len(entries) - added} already in the queue).",
            "success",
        )
        # Kick off the queue if nothing's downloading
        worker = getattr(self, "download_worker", None)
        if worker is None or not worker.isRunning():
            self._advance_queue()

    def _on_expand_error(self, err):
        self.expand_btn.setEnabled(True)
        self._log(f"[PLAYLIST] {err}")
        self._set_status(f"Playlist probe failed: {err}", "error")

    def _on_scan_page(self):
        """Scrape a webpage for video/media links and queue them."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a webpage URL first.", "warning")
            return
        if not url.startswith("http"):
            self._set_status("Scan Page expects a full http(s) URL.", "warning")
            return
        self.scan_btn.setEnabled(False)
        self._set_status("Scanning page for media links...", "working")
        self._log(f"[SCRAPE] Scanning {url}")
        worker = _PageScrapeWorker(url)
        worker.finished.connect(self._on_scan_done)
        worker.error.connect(self._on_scan_error)
        worker.log.connect(self._log)
        self._scan_worker = worker
        worker.start()

    def _on_scan_done(self, links):
        self.scan_btn.setEnabled(True)
        if not links:
            self._set_status(
                "No media links found. Try Fetch or Expand Playlist instead.",
                "warning",
            )
            return
        added = 0
        for url, hint in links:
            if self._queue_add(url, title=url[:80], platform=hint):
                added += 1
        self._log(f"[SCRAPE] Queued {added} new link(s) of {len(links)} found")
        self._set_status(
            f"Found {len(links)} link(s). Queued {added} new ({len(links) - added} already in queue).",
            "success",
        )
        worker = getattr(self, "download_worker", None)
        if worker is None or not worker.isRunning():
            self._advance_queue()

    def _on_scan_error(self, err):
        self.scan_btn.setEnabled(True)
        self._log(f"[SCRAPE] {err}")
        self._set_status(f"Scan failed: {err}", "error")

    def _on_queue_url(self):
        """Add the current URL input to the persistent queue."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a URL first.", "warning")
            return
        title = ""
        platform = ""
        if self.stream_info:
            title = self.stream_info.title or ""
            platform = self.stream_info.platform or ""
        added = self._queue_add(url, title=title, platform=platform)
        if added:
            self._set_status(f"Queued: {title or url[:60]}", "success")
        else:
            self._set_status("URL already in the queue.", "warning")

    def _on_schedule_url(self):
        """Queue the current URL with a deferred start time."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a URL first.", "warning")
            return
        # Ask for an offset in minutes — simple numeric input
        offset_min, ok = QInputDialog.getInt(
            self,
            "Schedule Download",
            "Start in how many minutes? (use 60 for 1 hour, 1440 for 1 day)",
            value=60, min=1, max=60 * 24 * 30,
        )
        if not ok:
            return
        start_at = (datetime.now() + timedelta(minutes=offset_min)).replace(microsecond=0)
        title = ""
        platform = ""
        if self.stream_info:
            title = self.stream_info.title or ""
            platform = self.stream_info.platform or ""
        added = self._queue_add(url, title=title, platform=platform,
                                start_at=start_at.isoformat())
        if added:
            self._set_status(
                f"Scheduled for {start_at.strftime('%Y-%m-%d %H:%M')}: {title or url[:60]}",
                "success",
            )
        else:
            self._set_status("URL already in the queue.", "warning")

    def _on_clear_queue(self):
        self._download_queue = [q for q in self._download_queue if q.get("status") != "queued"]
        self._persist_config()
        self._refresh_queue_table()
        self._set_status("Queue cleared.", "success")

    # ── Monitor Tab ───────────────────────────────────────────────────

    def _build_monitor_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("heroCard")
        hero_lay = QVBoxLayout(hero)
        hero_lay.setContentsMargins(18, 18, 18, 18)
        hero_lay.setSpacing(14)

        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(4)
        kicker = QLabel("Monitor")
        kicker.setObjectName("eyebrow")
        title = QLabel("Keep an eye on channels without babysitting them")
        title.setObjectName("heroTitle")
        title.setWordWrap(True)
        body = QLabel("Track supported channels, watch live state changes, and automatically start recording when they go live.")
        body.setObjectName("heroBody")
        body.setWordWrap(True)
        hero_copy.addWidget(kicker)
        hero_copy.addWidget(title)
        hero_copy.addWidget(body)
        hero_lay.addLayout(hero_copy)

        monitor_metrics = QHBoxLayout()
        monitor_metrics.setSpacing(12)
        count_card, self.monitor_count_value, self.monitor_count_sub = self._make_metric_card(
            "Channels", "0", "active entries"
        )
        auto_card, self.monitor_auto_value, self.monitor_auto_sub = self._make_metric_card(
            "Auto Record", "0", "auto-record enabled"
        )
        live_card, self.monitor_live_value, self.monitor_live_sub = self._make_metric_card(
            "Live Now", "0", "currently live"
        )
        monitor_metrics.addWidget(count_card)
        monitor_metrics.addWidget(auto_card)
        monitor_metrics.addWidget(live_card)
        hero_lay.addLayout(monitor_metrics)
        lay.addWidget(hero)

        manage_card = QFrame()
        manage_card.setObjectName("card")
        manage_lay = QVBoxLayout(manage_card)
        manage_lay.setContentsMargins(18, 18, 18, 18)
        manage_lay.setSpacing(12)

        manage_header = QVBoxLayout()
        manage_header.setSpacing(4)
        sec = QLabel("Add Channel")
        sec.setObjectName("sectionTitle")
        sec_body = QLabel("Supported examples: kick.com/user or twitch.tv/user")
        sec_body.setObjectName("sectionBody")
        manage_header.addWidget(sec)
        manage_header.addWidget(sec_body)
        manage_lay.addLayout(manage_header)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(12)

        url_block, url_block_lay = self._make_field_block(
            "Channel URL", "Paste the channel link you want StreamKeep to poll."
        )
        self.monitor_url_input = QLineEdit()
        self.monitor_url_input.setPlaceholderText("Channel URL (kick.com/user, twitch.tv/user)")
        url_block_lay.addWidget(self.monitor_url_input)
        controls_row.addWidget(url_block, 1)

        interval_block, interval_block_lay = self._make_field_block(
            "Check Every", "Polling interval"
        )
        self.monitor_interval_spin = QSpinBox()
        self.monitor_interval_spin.setRange(30, 600)
        self.monitor_interval_spin.setValue(120)
        self.monitor_interval_spin.setSuffix("s")
        interval_block_lay.addWidget(self.monitor_interval_spin)
        controls_row.addWidget(interval_block)

        auto_block, auto_block_lay = self._make_field_block(
            "Automation", "Live auto-record + VOD subscription"
        )
        self.monitor_auto_cb = QCheckBox("Enable auto-record (live)")
        auto_block_lay.addWidget(self.monitor_auto_cb)
        self.monitor_subscribe_cb = QCheckBox("Subscribe — queue new VODs")
        auto_block_lay.addWidget(self.monitor_subscribe_cb)
        auto_block_lay.addStretch(1)
        controls_row.addWidget(auto_block)

        add_btn = QPushButton("Add Channel")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._on_monitor_add)
        controls_row.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignBottom)
        manage_lay.addLayout(controls_row)

        self.monitor_summary_label = QLabel("Add a channel URL to start passive live monitoring.")
        self.monitor_summary_label.setObjectName("subtleText")
        manage_lay.addWidget(self.monitor_summary_label)
        lay.addWidget(manage_card)

        table_card = QFrame()
        table_card.setObjectName("card")
        table_lay = QVBoxLayout(table_card)
        table_lay.setContentsMargins(18, 18, 18, 18)
        table_lay.setSpacing(10)

        table_header = QVBoxLayout()
        table_header.setSpacing(4)
        table_title = QLabel("Watch List")
        table_title.setObjectName("sectionTitle")
        table_hint = QLabel("Entries refresh automatically and can trigger auto-recording when a stream goes live.")
        table_hint.setObjectName("sectionBody")
        table_hint.setWordWrap(True)
        table_header.addWidget(table_title)
        table_header.addWidget(table_hint)
        table_lay.addLayout(table_header)

        self.monitor_table = QTableWidget()
        self.monitor_table.setColumnCount(6)
        self.monitor_table.setHorizontalHeaderLabels(["Platform", "Channel", "Status", "Interval", "Auto-Record", ""])
        self.monitor_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.monitor_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.monitor_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.monitor_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.monitor_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.monitor_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.monitor_table.setColumnWidth(0, 84)
        self.monitor_table.setColumnWidth(2, 90)
        self.monitor_table.setColumnWidth(3, 84)
        self.monitor_table.setColumnWidth(4, 108)
        self.monitor_table.setColumnWidth(5, 110)
        self.monitor_table.verticalHeader().setVisible(False)
        self.monitor_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.monitor_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._style_table(self.monitor_table, 44)
        table_lay.addWidget(self.monitor_table)

        lay.addWidget(table_card, 1)
        self._refresh_monitor_summary()
        return page

    # ── History Tab ───────────────────────────────────────────────────

    def _build_history_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("heroCard")
        hero_lay = QVBoxLayout(hero)
        hero_lay.setContentsMargins(18, 18, 18, 18)
        hero_lay.setSpacing(14)

        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(4)
        kicker = QLabel("History")
        kicker.setObjectName("eyebrow")
        title = QLabel("Keep a clean record of what you captured")
        title.setObjectName("heroTitle")
        title.setWordWrap(True)
        body = QLabel("Completed downloads are listed here so you can quickly revisit folders, compare qualities, and confirm recent jobs.")
        body.setObjectName("heroBody")
        body.setWordWrap(True)
        hero_copy.addWidget(kicker)
        hero_copy.addWidget(title)
        hero_copy.addWidget(body)
        hero_lay.addLayout(hero_copy)

        history_metrics = QHBoxLayout()
        history_metrics.setSpacing(12)
        count_card, self.history_count_value, self.history_count_sub = self._make_metric_card(
            "Downloads", "0", "saved downloads"
        )
        latest_card, self.history_latest_value, self.history_latest_sub = self._make_metric_card(
            "Latest", "No entries", "Completed downloads appear here"
        )
        platform_card, self.history_platform_value, self.history_platform_sub = self._make_metric_card(
            "Top Platform", "—", "appears most in history"
        )
        channel_card, self.history_channel_value, self.history_channel_sub = self._make_metric_card(
            "Top Channel", "—", "most downloaded"
        )
        history_metrics.addWidget(count_card)
        history_metrics.addWidget(latest_card, 1)
        history_metrics.addWidget(platform_card)
        history_metrics.addWidget(channel_card, 1)
        hero_lay.addLayout(history_metrics)
        lay.addWidget(hero)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 18, 18, 18)
        card_lay.setSpacing(10)

        header = QHBoxLayout()
        header_copy = QVBoxLayout()
        header_copy.setSpacing(4)
        sec = QLabel("Download History")
        sec.setObjectName("sectionTitle")
        self.history_summary_label = QLabel("Download history builds automatically after each completed job.")
        self.history_summary_label.setObjectName("sectionBody")
        self.history_summary_label.setWordWrap(True)
        header_copy.addWidget(sec)
        header_copy.addWidget(self.history_summary_label)
        header.addLayout(header_copy, 1)
        clear_btn = QPushButton("Clear History")
        clear_btn.setObjectName("secondary")
        clear_btn.clicked.connect(self._on_clear_history)
        header.addWidget(clear_btn)
        card_lay.addLayout(header)

        # Search filter row
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search by title, platform, or path...")
        self.history_search.textChanged.connect(self._on_history_search)
        search_row.addWidget(self.history_search, 1)
        card_lay.addLayout(search_row)

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(6)
        self.history_table.setHorizontalHeaderLabels(["Date", "Platform", "Title", "Quality", "Size", "Path"])
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.history_table.setColumnWidth(0, 150)
        self.history_table.setColumnWidth(1, 84)
        self.history_table.setColumnWidth(3, 110)
        self.history_table.setColumnWidth(4, 88)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.doubleClicked.connect(self._on_history_double_click)
        self._style_table(self.history_table, 44)
        card_lay.addWidget(self.history_table)

        lay.addWidget(card, 1)
        self._refresh_history_summary()
        return page

    # ── Settings Tab ──────────────────────────────────────────────────

    def _build_settings_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("heroCard")
        hero_lay = QVBoxLayout(hero)
        hero_lay.setContentsMargins(18, 18, 18, 18)
        hero_lay.setSpacing(14)

        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(4)
        kicker = QLabel("Settings")
        kicker.setObjectName("eyebrow")
        title = QLabel("Tune storage, authenticated access, and tooling")
        title.setObjectName("heroTitle")
        title.setWordWrap(True)
        body = QLabel("Set default output behavior, attach browser cookies for gated content, and verify the local toolchain that powers downloads.")
        body.setObjectName("heroBody")
        body.setWordWrap(True)
        hero_copy.addWidget(kicker)
        hero_copy.addWidget(title)
        hero_copy.addWidget(body)
        hero_lay.addLayout(hero_copy)

        settings_meta = QLabel(
            f"StreamKeep v{VERSION}\n"
            f"Config file: {CONFIG_FILE}\n"
            f"Supported platforms: {', '.join(Extractor.all_names())}"
        )
        settings_meta.setObjectName("sectionBody")
        settings_meta.setWordWrap(True)
        hero_lay.addWidget(settings_meta)
        lay.addWidget(hero)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 18, 18, 18)
        card_lay.setSpacing(14)

        sections_top = QHBoxLayout()
        sections_top.setSpacing(12)

        general_block, general_lay = self._make_field_block(
            "Default Output", "New downloads will default to this folder."
        )
        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        self.settings_output = QLineEdit(str(_default_output_dir()))
        output_row.addWidget(self.settings_output, 1)
        browse = QPushButton("Browse")
        browse.setObjectName("secondary")
        browse.clicked.connect(lambda: self._settings_browse(self.settings_output))
        output_row.addWidget(browse)
        general_lay.addLayout(output_row)
        sections_top.addWidget(general_block, 1)

        tools_block, tools_lay = self._make_field_block(
            "Local Toolchain", "StreamKeep relies on these binaries for robust downloads."
        )
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
        ff_card, _, _ = self._make_metric_card("ffmpeg", "Ready" if ff_ver != "Not found" else "Missing", ff_ver[:48])
        yt_card, _, _ = self._make_metric_card("yt-dlp", "Ready" if yt_ver != "Not installed" else "Missing", yt_ver[:48])
        tools_metrics = QHBoxLayout()
        tools_metrics.setSpacing(10)
        tools_metrics.addWidget(ff_card)
        tools_metrics.addWidget(yt_card)
        tools_lay.addLayout(tools_metrics)
        sections_top.addWidget(tools_block, 1)
        card_lay.addLayout(sections_top)

        cookies_block, cookies_lay = self._make_field_block(
            "Browser Cookies",
            "Use browser cookies or a cookies.txt file for age-restricted or authenticated content."
        )

        row_cookies = QHBoxLayout()
        row_cookies.setSpacing(8)
        self.cookies_combo = QComboBox()
        self.cookies_combo.addItem("None")
        row_cookies.addWidget(self.cookies_combo, 1)
        scan_btn = QPushButton("Scan for Browsers")
        scan_btn.setObjectName("secondary")
        scan_btn.clicked.connect(self._on_scan_browsers)
        row_cookies.addWidget(scan_btn)
        cookies_lay.addLayout(row_cookies)

        row_cookiefile = QHBoxLayout()
        row_cookiefile.setSpacing(8)
        self.cookies_file_input = QLineEdit()
        self.cookies_file_input.setPlaceholderText("Path to cookies.txt (Netscape format)")
        row_cookiefile.addWidget(self.cookies_file_input, 1)
        browse_cookies = QPushButton("Browse")
        browse_cookies.setObjectName("secondary")
        browse_cookies.clicked.connect(self._on_browse_cookies_file)
        row_cookiefile.addWidget(browse_cookies)
        cookies_lay.addLayout(row_cookiefile)

        self.cookies_scan_label = QLabel("")
        self.cookies_scan_label.setObjectName("subtleText")
        self.cookies_scan_label.setWordWrap(True)
        cookies_lay.addWidget(self.cookies_scan_label)
        card_lay.addWidget(cookies_block)

        saved_browser = self._config.get("cookies_browser", "")
        saved_file = self._config.get("cookies_file", "")
        if saved_file:
            self.cookies_file_input.setText(saved_file)
            YtDlpExtractor.cookies_file = saved_file
        self._scan_browsers_silent()
        if saved_browser:
            idx = self.cookies_combo.findText(saved_browser)
            if idx >= 0:
                self.cookies_combo.setCurrentIndex(idx)
            YtDlpExtractor.cookies_browser = saved_browser

        # Network settings — rate limit and proxy
        network_block, network_lay = self._make_field_block(
            "Network",
            "Optional bandwidth throttling and proxy for geo-blocked content."
        )
        rate_row = QHBoxLayout()
        rate_row.setSpacing(8)
        rate_label = QLabel("Rate limit:")
        rate_label.setFixedWidth(100)
        rate_row.addWidget(rate_label)
        self.rate_limit_input = QLineEdit()
        self.rate_limit_input.setPlaceholderText("e.g. 500K or 2M (leave blank for unlimited)")
        rate_row.addWidget(self.rate_limit_input, 1)
        network_lay.addLayout(rate_row)

        proxy_row = QHBoxLayout()
        proxy_row.setSpacing(8)
        proxy_label = QLabel("Proxy URL:")
        proxy_label.setFixedWidth(100)
        proxy_row.addWidget(proxy_label)
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("e.g. socks5://127.0.0.1:1080 or http://proxy:8080")
        proxy_row.addWidget(self.proxy_input, 1)
        network_lay.addLayout(proxy_row)

        # Bandwidth schedule — apply a different rate limit during a time window
        self.bw_enable_check = QCheckBox(
            "Enable bandwidth schedule (overrides Rate limit within the window)"
        )
        self.bw_enable_check.setChecked(self._bandwidth_rule["enabled"])
        network_lay.addWidget(self.bw_enable_check)
        bw_row = QHBoxLayout()
        bw_row.setSpacing(8)
        bw_row.addWidget(QLabel("Window:"))
        self.bw_start_spin = QSpinBox()
        self.bw_start_spin.setRange(0, 23)
        self.bw_start_spin.setSuffix(":00")
        self.bw_start_spin.setValue(self._bandwidth_rule["start_hour"])
        bw_row.addWidget(self.bw_start_spin)
        bw_row.addWidget(QLabel("to"))
        self.bw_end_spin = QSpinBox()
        self.bw_end_spin.setRange(0, 23)
        self.bw_end_spin.setSuffix(":00")
        self.bw_end_spin.setValue(self._bandwidth_rule["end_hour"])
        bw_row.addWidget(self.bw_end_spin)
        bw_row.addSpacing(12)
        bw_row.addWidget(QLabel("Limit:"))
        self.bw_limit_input = QLineEdit(self._bandwidth_rule["limit"])
        self.bw_limit_input.setPlaceholderText("500K")
        self.bw_limit_input.setFixedWidth(100)
        bw_row.addWidget(self.bw_limit_input)
        bw_row.addStretch(1)
        network_lay.addLayout(bw_row)

        # Parallel connections per direct MP4 (HTTP Range splitting)
        par_row = QHBoxLayout()
        par_row.setSpacing(8)
        par_label = QLabel("Parallel connections:")
        par_label.setFixedWidth(140)
        par_row.addWidget(par_label)
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 16)
        self.parallel_spin.setValue(self._parallel_connections)
        self.parallel_spin.setToolTip(
            "Multi-connection HTTP Range splitting for direct MP4 files.\n"
            "Higher values can be 3-5x faster on CDN-hosted content.\n"
            "Set to 1 to disable and always use ffmpeg."
        )
        par_row.addWidget(self.parallel_spin)
        par_hint = QLabel("per direct MP4 (1 = off, default 4)")
        par_hint.setStyleSheet(f"color: {CAT['subtext0']}; font-size: 11px;")
        par_row.addWidget(par_hint)
        par_row.addStretch(1)
        network_lay.addLayout(par_row)

        # Load saved network settings
        saved_rate = self._config.get("rate_limit", "")
        saved_proxy = self._config.get("proxy", "")
        if saved_rate:
            self.rate_limit_input.setText(saved_rate)
            YtDlpExtractor.rate_limit = saved_rate
        if saved_proxy:
            self.proxy_input.setText(saved_proxy)
            YtDlpExtractor.proxy = saved_proxy
            _set_native_proxy(saved_proxy)
            global NATIVE_PROXY
            NATIVE_PROXY = saved_proxy

        card_lay.addWidget(network_block)

        # YouTube extras — subtitles + SponsorBlock
        yt_block, yt_lay = self._make_field_block(
            "YouTube Extras",
            "Optional yt-dlp features for YouTube videos."
        )
        self.subs_check = QCheckBox("Download subtitles (English) and embed in video")
        self.sponsorblock_check = QCheckBox("Skip SponsorBlock segments (sponsor / self-promo / interaction)")
        yt_lay.addWidget(self.subs_check)
        yt_lay.addWidget(self.sponsorblock_check)

        # Load saved yt-dlp extras
        if self._config.get("download_subs"):
            self.subs_check.setChecked(True)
            YtDlpExtractor.download_subs = True
        if self._config.get("sponsorblock"):
            self.sponsorblock_check.setChecked(True)
            YtDlpExtractor.sponsorblock = True

        card_lay.addWidget(yt_block)

        # Filename templates
        tpl_block, tpl_lay = self._make_field_block(
            "Filename Templates",
            "Variables: {title} {channel} {platform} {date} {year} {month} {day}. "
            "Use / to create subfolders. Each segment is sanitized."
        )
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_label = QLabel("Folder:")
        folder_label.setFixedWidth(100)
        folder_row.addWidget(folder_label)
        self.folder_template_input = QLineEdit(self._folder_template)
        self.folder_template_input.setPlaceholderText(DEFAULT_FOLDER_TEMPLATE)
        folder_row.addWidget(self.folder_template_input, 1)
        tpl_lay.addLayout(folder_row)
        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        file_label = QLabel("Filename:")
        file_label.setFixedWidth(100)
        file_row.addWidget(file_label)
        self.file_template_input = QLineEdit(self._file_template)
        self.file_template_input.setPlaceholderText(DEFAULT_FILE_TEMPLATE)
        file_row.addWidget(self.file_template_input, 1)
        tpl_lay.addLayout(file_row)
        card_lay.addWidget(tpl_block)

        # Webhook notifications
        hook_block, hook_lay = self._make_field_block(
            "Webhook Notifications",
            "POST a JSON payload when downloads complete. Discord webhook URLs are auto-detected and formatted as embeds."
        )
        self.webhook_input = QLineEdit(self._webhook_url)
        self.webhook_input.setPlaceholderText("https://discord.com/api/webhooks/... or any POST endpoint")
        hook_lay.addWidget(self.webhook_input)
        card_lay.addWidget(hook_block)

        # Duplicate detection
        dup_block, dup_lay = self._make_field_block(
            "Duplicate Detection",
            "Warn before downloading something already in your history."
        )
        self.dup_check = QCheckBox("Check history for URL and title matches before download")
        self.dup_check.setChecked(self._check_duplicates)
        dup_lay.addWidget(self.dup_check)
        card_lay.addWidget(dup_block)

        # Media library (Kodi / Jellyfin / Plex)
        lib_block, lib_lay = self._make_field_block(
            "Media Library",
            "Write Kodi/Jellyfin/Plex-compatible metadata files and chat replays for archival."
        )
        self.nfo_check = QCheckBox("Write .nfo file (movie schema) alongside each download")
        self.nfo_check.setChecked(self._write_nfo)
        lib_lay.addWidget(self.nfo_check)
        self.chat_check = QCheckBox("Download Twitch VOD chat replay (JSON + plain text)")
        self.chat_check.setChecked(TwitchExtractor.download_chat_enabled)
        lib_lay.addWidget(self.chat_check)
        card_lay.addWidget(lib_block)

        # Post-processing presets
        pp_block, pp_lay = self._make_field_block(
            "Post-Processing",
            "Automatic ffmpeg operations on each downloaded file. Originals are preserved."
        )
        self.pp_audio_check = QCheckBox("Extract audio as MP3 (libmp3lame, VBR quality 2)")
        self.pp_audio_check.setChecked(PostProcessor.extract_audio)
        pp_lay.addWidget(self.pp_audio_check)
        self.pp_loud_check = QCheckBox("Normalize loudness (EBU R128: I=-16, TP=-1.5, LRA=11)")
        self.pp_loud_check.setChecked(PostProcessor.normalize_loudness)
        pp_lay.addWidget(self.pp_loud_check)
        self.pp_h265_check = QCheckBox("Re-encode video to H.265/HEVC (libx265, CRF 23 — slow)")
        self.pp_h265_check.setChecked(PostProcessor.reencode_h265)
        pp_lay.addWidget(self.pp_h265_check)
        self.pp_contact_check = QCheckBox("Generate contact sheet (3x3 thumbnail grid .jpg)")
        self.pp_contact_check.setChecked(PostProcessor.contact_sheet)
        pp_lay.addWidget(self.pp_contact_check)
        self.pp_split_check = QCheckBox("Split by chapters into per-chapter files (for videos with chapters)")
        self.pp_split_check.setChecked(PostProcessor.split_by_chapter)
        pp_lay.addWidget(self.pp_split_check)

        # Video converter row
        self.pp_convert_video_check = QCheckBox("Convert video to:")
        self.pp_convert_video_check.setChecked(PostProcessor.convert_video)
        self.pp_convert_video_format = QComboBox()
        self.pp_convert_video_format.addItems(VIDEO_CONTAINERS)
        idx = VIDEO_CONTAINERS.index(PostProcessor.convert_video_format) \
            if PostProcessor.convert_video_format in VIDEO_CONTAINERS else 0
        self.pp_convert_video_format.setCurrentIndex(idx)
        self.pp_convert_video_format.setFixedWidth(80)
        self.pp_convert_video_codec = QComboBox()
        vc_keys = _available_video_codec_keys()
        self.pp_convert_video_codec.addItems(vc_keys)
        # Fall back to h264 if the previously-saved codec isn't available
        saved_vc = PostProcessor.convert_video_codec
        if saved_vc in vc_keys:
            self.pp_convert_video_codec.setCurrentIndex(vc_keys.index(saved_vc))
        elif "h264" in vc_keys:
            self.pp_convert_video_codec.setCurrentIndex(vc_keys.index("h264"))
        self.pp_convert_video_codec.setFixedWidth(140)
        hw_count = sum(1 for k in vc_keys if "(" in k)
        hw_note = f" ({hw_count} GPU encoder{'s' if hw_count != 1 else ''} detected)" if hw_count else ""
        self.pp_convert_video_codec.setToolTip(
            "copy = fast remux (no re-encode)\n"
            "h264/h265/vp9/av1/mpeg4 = software encoders\n"
            "(NVENC) = NVIDIA GPU (5-20x faster)\n"
            "(QSV) = Intel Quick Sync\n"
            "(AMF) = AMD GPU\n"
            "(VT) = Apple VideoToolbox\n"
            + hw_note
        )
        # Scale target
        scale_items = ["original", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
        self.pp_convert_video_scale = QComboBox()
        self.pp_convert_video_scale.addItems(scale_items)
        idx = scale_items.index(PostProcessor.convert_video_scale) \
            if PostProcessor.convert_video_scale in scale_items else 0
        self.pp_convert_video_scale.setCurrentIndex(idx)
        self.pp_convert_video_scale.setFixedWidth(90)
        self.pp_convert_video_scale.setToolTip(
            "Downscale target height. Aspect ratio is preserved.\n"
            "Forces a re-encode when not 'original' (copy codec ignored)."
        )
        # FPS cap
        fps_items = ["original", "60", "30", "24"]
        self.pp_convert_video_fps = QComboBox()
        self.pp_convert_video_fps.addItems(fps_items)
        idx = fps_items.index(PostProcessor.convert_video_fps) \
            if PostProcessor.convert_video_fps in fps_items else 0
        self.pp_convert_video_fps.setCurrentIndex(idx)
        self.pp_convert_video_fps.setFixedWidth(80)
        self.pp_convert_video_fps.setToolTip(
            "Frame rate cap. Forces a re-encode when not 'original'."
        )

        vconv_row = QHBoxLayout()
        vconv_row.setSpacing(6)
        vconv_row.addWidget(self.pp_convert_video_check)
        vconv_row.addSpacing(4)
        vconv_row.addWidget(QLabel("Container:"))
        vconv_row.addWidget(self.pp_convert_video_format)
        vconv_row.addSpacing(8)
        vconv_row.addWidget(QLabel("Codec:"))
        vconv_row.addWidget(self.pp_convert_video_codec)
        vconv_row.addSpacing(8)
        vconv_row.addWidget(QLabel("Scale:"))
        vconv_row.addWidget(self.pp_convert_video_scale)
        vconv_row.addSpacing(8)
        vconv_row.addWidget(QLabel("FPS:"))
        vconv_row.addWidget(self.pp_convert_video_fps)
        vconv_row.addStretch(1)
        pp_lay.addLayout(vconv_row)

        # Audio converter row
        self.pp_convert_audio_check = QCheckBox("Convert audio to:")
        self.pp_convert_audio_check.setChecked(PostProcessor.convert_audio)
        self.pp_convert_audio_format = QComboBox()
        self.pp_convert_audio_format.addItems(AUDIO_CONTAINERS)
        idx = AUDIO_CONTAINERS.index(PostProcessor.convert_audio_format) \
            if PostProcessor.convert_audio_format in AUDIO_CONTAINERS else 0
        self.pp_convert_audio_format.setCurrentIndex(idx)
        self.pp_convert_audio_format.setFixedWidth(80)
        self.pp_convert_audio_codec = QComboBox()
        self.pp_convert_audio_codec.addItems(list(AUDIO_CODECS.keys()))
        ac_keys = list(AUDIO_CODECS.keys())
        idx = ac_keys.index(PostProcessor.convert_audio_codec) \
            if PostProcessor.convert_audio_codec in ac_keys else 1
        self.pp_convert_audio_codec.setCurrentIndex(idx)
        self.pp_convert_audio_codec.setFixedWidth(90)
        self.pp_convert_audio_codec.setToolTip(
            "copy = remux only\n"
            "mp3 = libmp3lame (universal)\n"
            "aac = AAC-LC (Apple-friendly)\n"
            "opus = low-bitrate champion\n"
            "vorbis = open-source lossy\n"
            "flac/pcm = lossless"
        )
        self.pp_convert_audio_bitrate = QComboBox()
        self.pp_convert_audio_bitrate.addItems(["96k", "128k", "192k", "256k", "320k"])
        br_items = ["96k", "128k", "192k", "256k", "320k"]
        idx = br_items.index(PostProcessor.convert_audio_bitrate) \
            if PostProcessor.convert_audio_bitrate in br_items else 2
        self.pp_convert_audio_bitrate.setCurrentIndex(idx)
        self.pp_convert_audio_bitrate.setFixedWidth(80)
        self.pp_convert_audio_bitrate.setToolTip("Bitrate (ignored for flac/pcm)")
        # Sample rate
        sr_items = ["original", "48000", "44100", "22050"]
        self.pp_convert_audio_samplerate = QComboBox()
        self.pp_convert_audio_samplerate.addItems(sr_items)
        idx = sr_items.index(PostProcessor.convert_audio_samplerate) \
            if PostProcessor.convert_audio_samplerate in sr_items else 0
        self.pp_convert_audio_samplerate.setCurrentIndex(idx)
        self.pp_convert_audio_samplerate.setFixedWidth(90)
        self.pp_convert_audio_samplerate.setToolTip(
            "Sample rate (Hz). Forces a re-encode when not 'original'."
        )

        aconv_row = QHBoxLayout()
        aconv_row.setSpacing(6)
        aconv_row.addWidget(self.pp_convert_audio_check)
        aconv_row.addSpacing(4)
        aconv_row.addWidget(QLabel("Container:"))
        aconv_row.addWidget(self.pp_convert_audio_format)
        aconv_row.addSpacing(8)
        aconv_row.addWidget(QLabel("Codec:"))
        aconv_row.addWidget(self.pp_convert_audio_codec)
        aconv_row.addSpacing(8)
        aconv_row.addWidget(QLabel("Bitrate:"))
        aconv_row.addWidget(self.pp_convert_audio_bitrate)
        aconv_row.addSpacing(8)
        aconv_row.addWidget(QLabel("Rate:"))
        aconv_row.addWidget(self.pp_convert_audio_samplerate)
        aconv_row.addStretch(1)
        pp_lay.addLayout(aconv_row)

        self.pp_convert_delete_check = QCheckBox(
            "Delete original source file after successful conversion"
        )
        self.pp_convert_delete_check.setChecked(PostProcessor.convert_delete_source)
        pp_lay.addWidget(self.pp_convert_delete_check)

        # Standalone manual converter — runs the current settings against
        # an arbitrary file or folder the user picks.
        manual_row = QHBoxLayout()
        manual_row.setSpacing(8)
        self.convert_files_btn = QPushButton("Convert Files...")
        self.convert_files_btn.setObjectName("secondary")
        self.convert_files_btn.setToolTip(
            "Pick individual media files and convert them with the current settings.\n"
            "Saves your settings first."
        )
        self.convert_files_btn.clicked.connect(self._on_convert_files_clicked)
        manual_row.addWidget(self.convert_files_btn)

        self.convert_folder_btn = QPushButton("Convert Folder...")
        self.convert_folder_btn.setObjectName("secondary")
        self.convert_folder_btn.setToolTip(
            "Pick a folder; every video/audio file in it gets converted."
        )
        self.convert_folder_btn.clicked.connect(self._on_convert_folder_clicked)
        manual_row.addWidget(self.convert_folder_btn)

        self.convert_cancel_btn = QPushButton("Cancel")
        self.convert_cancel_btn.setObjectName("secondary")
        self.convert_cancel_btn.setVisible(False)
        self.convert_cancel_btn.clicked.connect(self._on_convert_cancel)
        manual_row.addWidget(self.convert_cancel_btn)
        manual_row.addStretch(1)
        pp_lay.addLayout(manual_row)

        card_lay.addWidget(pp_block)

        # Save / Import / Export row
        save_row = QHBoxLayout()
        import_btn = QPushButton("Import Config")
        import_btn.setObjectName("secondary")
        import_btn.setToolTip("Replace current settings with a backup file")
        import_btn.clicked.connect(self._on_import_config)
        save_row.addWidget(import_btn)
        export_btn = QPushButton("Export Config")
        export_btn.setObjectName("secondary")
        export_btn.setToolTip("Write current settings to a backup file")
        export_btn.clicked.connect(self._on_export_config)
        save_row.addWidget(export_btn)
        save_row.addStretch()
        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._on_save_settings)
        save_row.addWidget(save_btn)
        card_lay.addLayout(save_row)

        lay.addWidget(card, 1)
        return page

    def _on_convert_files_clicked(self):
        """Open a multi-select file picker and kick off the converter."""
        if getattr(self, "_convert_worker", None) is not None and self._convert_worker.isRunning():
            self._set_status("A conversion is already running.", "warning")
            return
        # Apply current settings first so the worker picks them up
        self._on_save_settings()
        exts = sorted(VIDEO_EXTS | AUDIO_EXTS)
        filter_str = "Media files (" + " ".join(f"*{e}" for e in exts) + ");;All files (*)"
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select files to convert", str(_default_output_dir()), filter_str
        )
        if not paths:
            return
        self._start_convert_worker(list(paths))

    def _on_convert_folder_clicked(self):
        """Recursively collect media files from a chosen folder and convert."""
        if getattr(self, "_convert_worker", None) is not None and self._convert_worker.isRunning():
            self._set_status("A conversion is already running.", "warning")
            return
        self._on_save_settings()
        folder = QFileDialog.getExistingDirectory(
            self, "Select folder to convert", str(_default_output_dir())
        )
        if not folder:
            return
        files = []
        try:
            for root, _dirs, fnames in os.walk(folder):
                for f in fnames:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in VIDEO_EXTS or ext in AUDIO_EXTS:
                        # Skip files we produced ourselves
                        low = f.lower()
                        if ".converted." in low:
                            continue
                        files.append(os.path.join(root, f))
        except OSError as e:
            self._set_status(f"Folder scan failed: {e}", "error")
            return
        if not files:
            self._set_status("No media files found in that folder.", "warning")
            return
        self._log(f"[CONVERT] Found {len(files)} file(s) in {folder}")
        self._start_convert_worker(files)

    def _start_convert_worker(self, files):
        """Launch a ConvertWorker for the given list and wire up signals."""
        do_video = self.pp_convert_video_check.isChecked()
        do_audio = self.pp_convert_audio_check.isChecked()
        if not (do_video or do_audio):
            self._set_status(
                "Enable 'Convert video' or 'Convert audio' in Post-Processing first.",
                "warning"
            )
            return
        self._convert_worker = ConvertWorker(files, do_video, do_audio)
        self._convert_worker.progress.connect(self._on_convert_progress)
        self._convert_worker.log.connect(self._log)
        self._convert_worker.file_done.connect(self._on_convert_file_done)
        self._convert_worker.all_done.connect(self._on_convert_all_done)
        self.convert_files_btn.setEnabled(False)
        self.convert_folder_btn.setEnabled(False)
        self.convert_cancel_btn.setVisible(True)
        self._log(f"[CONVERT] Starting batch conversion ({len(files)} file(s))")
        self._set_status(f"Converting 0/{len(files)}...", "working")
        self._convert_worker.start()

    def _on_convert_progress(self, idx, total, name):
        if total:
            status = f"Converting {idx + 1}/{total}: {name}" if name else f"Converted {total}/{total}"
            self._set_status(status, "working" if idx < total else "success")

    def _on_convert_file_done(self, path, ok):
        marker = "[OK]" if ok else "[FAIL]"
        self._log(f"[CONVERT] {marker} {os.path.basename(path)}")

    def _on_convert_all_done(self, successes, failures):
        self.convert_files_btn.setEnabled(True)
        self.convert_folder_btn.setEnabled(True)
        self.convert_cancel_btn.setVisible(False)
        total = successes + failures
        if failures == 0:
            self._set_status(f"Conversion complete: {successes}/{total} succeeded.", "success")
        else:
            self._set_status(
                f"Conversion finished: {successes} ok, {failures} failed. See log.",
                "warning"
            )
        self._notify("StreamKeep", f"Converted {successes}/{total} file(s)")

    def _on_convert_cancel(self):
        w = getattr(self, "_convert_worker", None)
        if w is not None and w.isRunning():
            w.cancel()
            self._log("[CONVERT] Cancel requested — finishing current file first")
            self._set_status("Cancelling conversion...", "warning")

    def _on_export_config(self):
        """Write current config to a user-chosen JSON file."""
        self._persist_config()  # sync latest UI state first
        default_name = f"StreamKeep-config-{datetime.now().strftime('%Y%m%d')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export StreamKeep Config",
            str(Path.home() / default_name),
            "JSON files (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
            self._log(f"[CONFIG] Exported to {path}")
            self._set_status(f"Config exported to {path}", "success")
        except Exception as e:
            self._log(f"[CONFIG] Export failed: {e}")
            self._set_status(f"Export failed: {e}", "error")

    def _on_import_config(self):
        """Replace current config with the contents of a chosen JSON file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import StreamKeep Config",
            str(Path.home()),
            "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                new_cfg = json.load(f)
            if not isinstance(new_cfg, dict):
                self._set_status("Import failed: not a valid config file.", "error")
                return
        except Exception as e:
            self._log(f"[CONFIG] Import failed: {e}")
            self._set_status(f"Import failed: {e}", "error")
            return
        # Replace config and re-apply
        self._config = new_cfg
        _save_config(new_cfg)
        # Clear mutable state that _apply_config appends to
        self._history.clear()
        self.monitor.entries.clear()
        self.monitor.load_from_config(new_cfg)
        # Re-apply config to all UI elements
        self._apply_config()
        # Refresh derived views
        self._refresh_history_table()
        self._refresh_download_summary()
        self._refresh_monitor_table()
        self._refresh_monitor_summary()
        self._refresh_history_summary()
        if hasattr(self, "queue_table"):
            self._refresh_queue_table()
        self._log(f"[CONFIG] Imported from {path}")
        self._set_status(f"Config imported from {path}. Some changes may require a restart.", "success")

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
            self._set_status(f"Found {len(found)} browser cookie store(s).", "success")
        else:
            self.cookies_scan_label.setText("No browser cookie stores found.")
            self._set_status("No browser cookie stores were found on this machine.", "warning")

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
        # Apply rate limit
        rate_limit = self.rate_limit_input.text().strip()
        YtDlpExtractor.rate_limit = rate_limit
        self._config["rate_limit"] = rate_limit
        # Apply proxy (also routes native extractor curl calls through it)
        proxy = self.proxy_input.text().strip()
        YtDlpExtractor.proxy = proxy
        self._config["proxy"] = proxy
        _set_native_proxy(proxy)
        global NATIVE_PROXY
        NATIVE_PROXY = proxy
        # Apply parallel connections (affects direct MP4 downloads)
        self._parallel_connections = max(1, min(16, self.parallel_spin.value()))
        # Apply bandwidth schedule rule
        self._bandwidth_rule = {
            "enabled": self.bw_enable_check.isChecked(),
            "start_hour": self.bw_start_spin.value(),
            "end_hour": self.bw_end_spin.value(),
            "limit": self.bw_limit_input.text().strip(),
        }
        self._apply_bandwidth_schedule()
        # Apply YouTube extras
        YtDlpExtractor.download_subs = self.subs_check.isChecked()
        self._config["download_subs"] = YtDlpExtractor.download_subs
        YtDlpExtractor.sponsorblock = self.sponsorblock_check.isChecked()
        self._config["sponsorblock"] = YtDlpExtractor.sponsorblock
        # Apply filename templates
        self._folder_template = self.folder_template_input.text().strip() or DEFAULT_FOLDER_TEMPLATE
        self._file_template = self.file_template_input.text().strip() or DEFAULT_FILE_TEMPLATE
        # Apply webhook
        self._webhook_url = self.webhook_input.text().strip()
        # Apply duplicate detection
        self._check_duplicates = self.dup_check.isChecked()
        # Apply library/NFO + chat
        self._write_nfo = self.nfo_check.isChecked()
        TwitchExtractor.download_chat_enabled = self.chat_check.isChecked()
        # Apply post-processing presets
        PostProcessor.extract_audio = self.pp_audio_check.isChecked()
        PostProcessor.normalize_loudness = self.pp_loud_check.isChecked()
        PostProcessor.reencode_h265 = self.pp_h265_check.isChecked()
        PostProcessor.contact_sheet = self.pp_contact_check.isChecked()
        PostProcessor.split_by_chapter = self.pp_split_check.isChecked()
        # Converter settings
        PostProcessor.convert_video = self.pp_convert_video_check.isChecked()
        PostProcessor.convert_video_format = self.pp_convert_video_format.currentText()
        PostProcessor.convert_video_codec = self.pp_convert_video_codec.currentText()
        PostProcessor.convert_video_scale = self.pp_convert_video_scale.currentText()
        PostProcessor.convert_video_fps = self.pp_convert_video_fps.currentText()
        PostProcessor.convert_audio = self.pp_convert_audio_check.isChecked()
        PostProcessor.convert_audio_format = self.pp_convert_audio_format.currentText()
        PostProcessor.convert_audio_codec = self.pp_convert_audio_codec.currentText()
        PostProcessor.convert_audio_bitrate = self.pp_convert_audio_bitrate.currentText()
        PostProcessor.convert_audio_samplerate = self.pp_convert_audio_samplerate.currentText()
        PostProcessor.convert_delete_source = self.pp_convert_delete_check.isChecked()
        self._persist_config()
        self._refresh_download_summary()
        self._set_status("Settings saved and applied to future downloads.", "success")

    # ── Actions ───────────────────────────────────────────────────────

    def _log(self, msg):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())
        _write_log_line(msg)

    def _update_badge(self, platform_name=None):
        if platform_name and platform_name in PLATFORM_BADGES:
            badge = PLATFORM_BADGES[platform_name]
            self.platform_badge.setText(f" {badge['text']} ")
            self.platform_badge.setStyleSheet(
                f"background-color: {badge['color']}; color: {CAT['crust']}; "
                f"border-radius: 999px; font-weight: bold; font-size: 11px; padding: 4px 12px;"
            )
            self.platform_badge.setVisible(True)
        else:
            self.platform_badge.setVisible(False)

    def _on_url_changed(self, text):
        ext = Extractor.detect(text.strip())
        if ext:
            self._update_badge(ext.NAME)
            ch = ext.extract_channel_id(text.strip())
            if ch and self._can_autofill_output():
                self._apply_auto_output(str(_default_output_dir() / _safe_filename(ch)))
        else:
            self._update_badge(None)
        self._refresh_download_summary()

    def _on_toggle_clipboard(self, checked):
        if checked:
            self.clipboard_monitor.start()
            self._log("[CLIPBOARD] Monitoring started - copy a URL to auto-load")
            self._set_status("Clipboard monitoring active. Copy a supported URL to load it automatically.", "working")
        else:
            self.clipboard_monitor.stop()
            self._log("[CLIPBOARD] Monitoring stopped")
            self._set_status("Clipboard monitoring stopped.", "idle")

    def _on_clipboard_url(self, url):
        # Don't interrupt an active download
        existing = getattr(self, "download_worker", None)
        if existing is not None and existing.isRunning():
            self._log(f"[CLIPBOARD] Ignored {url[:60]}... (download in progress)")
            return
        # Basic URL sanity — reject newlines/control chars
        if "\n" in url or "\r" in url or len(url) > 2048:
            self._log(f"[CLIPBOARD] Rejected malformed URL")
            return
        # Dedup: ignore if already in the input box (avoids re-fetching on focus switches)
        if url == self.url_input.text().strip():
            return
        # Dedup: ignore if it's the same as the last clipboard URL we accepted
        if url == getattr(self, "_last_clipboard_url", ""):
            return
        self._last_clipboard_url = url
        self._log(f"[CLIPBOARD] Detected: {url}")
        self.url_input.setText(url)
        self._switch_tab(0)  # Switch to Download tab
        self._on_fetch()

    def _remember_url(self, url):
        """Add URL to the top of the recent URLs list (most-recent-first)."""
        if not url:
            return
        # Dedup: move to front if already present
        if url in self._recent_urls:
            self._recent_urls.remove(url)
        self._recent_urls.insert(0, url)
        # Keep the last 30
        self._recent_urls = self._recent_urls[:30]
        if hasattr(self, "_recent_url_model"):
            self._recent_url_model.setStringList(self._recent_urls)

    def _find_duplicate(self, url, title=""):
        """Check history for a matching URL (exact) or title (fuzzy).
        Returns matching HistoryEntry or None."""
        if not self._check_duplicates:
            return None
        if url:
            for h in self._history:
                if h.url == url and h.path:
                    return h
        if title:
            norm = title.strip().lower()
            for h in self._history:
                if h.title and h.title.strip().lower() == norm and h.path:
                    return h
        return None

    def _on_fetch(self, vod_source=None, vod_platform=None):
        url = self.url_input.text().strip()
        if not url:
            return
        # Track recent URLs for the autocomplete dropdown
        if not vod_source:
            self._remember_url(url)
        # Check for URL-based duplicate before hitting the network
        if not vod_source:
            dup = self._find_duplicate(url)
            if dup:
                self._log(f"[DUPLICATE] Already downloaded on {dup.date} to {dup.path}")
                self._set_status(
                    f"Already downloaded {dup.date} to {dup.path}. Fetching anyway.",
                    "warning",
                )
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Fetching")
        self.download_btn.setEnabled(False)
        self.open_folder_btn.setVisible(False)
        self.overall_progress.setVisible(False)
        self.quality_combo.clear()
        self.quality_combo.setEnabled(False)
        self.table.setRowCount(0)
        self._segment_checks = []
        self._segment_progress = []
        self.info_label.setVisible(False)
        self.stream_info = None
        if not vod_source:
            self.vod_widget.setVisible(False)
            self._vod_checks = []
            self._refresh_vod_summary()
        self._refresh_download_summary()
        self._set_status("Fetching stream info and available playback options...", "working")

        # Disconnect any existing fetch worker to prevent stale signals
        prev_worker = getattr(self, "_fetch_worker", None)
        if prev_worker is not None:
            try:
                prev_worker.log.disconnect()
                prev_worker.finished.disconnect()
                prev_worker.vods_found.disconnect()
                prev_worker.error.disconnect()
            except (TypeError, RuntimeError):
                pass
            if prev_worker.isRunning():
                prev_worker.requestInterruption()

        self._fetch_worker = FetchWorker(url, vod_source=vod_source, vod_platform=vod_platform)
        self._fetch_worker.log.connect(self._log)
        self._fetch_worker.finished.connect(self._on_fetch_done)
        self._fetch_worker.vods_found.connect(self._on_vods_found)
        self._fetch_worker.error.connect(self._on_fetch_error)
        self._fetch_worker.start()

    def _on_fetch_done(self, info):
        if info is None:
            self._on_fetch_error("Extractor returned no stream info")
            return
        self.stream_info = info
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self._update_badge(info.platform)

        # Populate qualities
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        selected_idx = 0
        qualities = info.qualities or []
        for i, q in enumerate(qualities):
            bw_mbps = q.bandwidth / 1_000_000 if q.bandwidth else 0
            ft_tag = f" [{q.format_type.upper()}]" if q.format_type != "hls" else ""
            label = f"{q.name} ({q.resolution}, {bw_mbps:.1f} Mbps){ft_tag}"
            self.quality_combo.addItem(label, q)
            if "1080" in q.name or "source" in q.name.lower():
                selected_idx = i
        if qualities:
            self.quality_combo.setCurrentIndex(selected_idx)
        self.quality_combo.setEnabled(len(qualities) > 0)
        self.quality_combo.blockSignals(False)
        if not qualities:
            self._log("[WARN] No playable qualities found for this URL.")

        # Stream info
        parts = [f"Platform: {info.platform}", f"Duration: {info.duration_str}"]
        if info.title:
            parts.insert(1, f"Title: {info.title[:60]}")
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

        # Update output folder to use the title for non-channel content (yt-dlp, Direct, etc.)
        if info.title and info.platform in ("yt-dlp", "Direct", "Rumble", "SoundCloud",
                                            "Reddit", "Audius", "Podcast"):
            current_out = self.output_input.text().strip()
            parent = os.path.dirname(current_out)
            if parent and self._can_autofill_output():
                new_out = os.path.join(parent, _safe_filename(info.title))
                self._apply_auto_output(new_out)

        self._build_segments(info.total_secs)
        self.download_btn.setEnabled(True)
        self._refresh_download_summary()

        # Title-based duplicate check after resolve (more accurate than URL match)
        dup = self._find_duplicate("", info.title)
        if dup:
            self._log(f"[DUPLICATE] Title match: already downloaded {dup.date} to {dup.path}")
            self._set_status(
                f"Title matches a previous download ({dup.date}). Proceed if intentional.",
                "warning",
            )
        elif info.is_live or info.total_secs <= 0:
            self._set_status("Live source ready. Start recording and stop it when you have enough footage.", "success")
        else:
            self._set_status("Source ready. Review the segments and start the download when you are happy.", "success")

    def _on_fetch_error(self, err):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self._log(f"[ERROR] {err}")
        self._refresh_download_summary()
        self._set_status(f"Fetch failed: {err}", "error")

    def _on_vods_found(self, vod_list, platform_name):
        self._vod_list = vod_list
        self._vod_checks = []
        self.vod_table.setRowCount(len(vod_list))
        self._update_badge(platform_name)

        for i, v in enumerate(vod_list):
            cb = QCheckBox()
            cb.stateChanged.connect(lambda _state, self=self: self._refresh_vod_summary())
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
        self._refresh_vod_summary()
        self._set_status(f"Found {len(vod_list)} VOD(s). Select one to inspect or batch download.", "success")

    def _on_vod_select_all(self, state):
        checked = state == Qt.CheckState.Checked.value
        for cb in self._vod_checks:
            cb.setChecked(checked)
        self._refresh_vod_summary()

    def _on_vod_load_single(self):
        for i, cb in enumerate(self._vod_checks):
            if cb.isChecked():
                vod = self._vod_list[i]
                self._log(f"\nLoading VOD: {vod.title} ({vod.date})")
                self._on_fetch(vod_source=vod.source, vod_platform=vod.platform)
                return
        self._log("No VOD checked.")
        self._set_status("Select at least one VOD before loading it.", "warning")

    def _on_vod_download_all(self):
        checked = [self._vod_list[i] for i, cb in enumerate(self._vod_checks) if cb.isChecked()]
        if not checked:
            self._log("No VODs checked.")
            self._set_status("Select at least one VOD before starting a batch download.", "warning")
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
        self._set_status(f"Batch download queued for {self._batch_total} VOD(s).", "working")
        self._batch_next()

    def _batch_next(self):
        if self._batch_idx >= self._batch_total:
            self._batch_done()
            return
        vod = self._batch_vods[self._batch_idx]
        self._log(f"\n--- VOD {self._batch_idx + 1}/{self._batch_total}: {vod.title} ---")
        self._set_status(
            f"Preparing VOD {self._batch_idx + 1} of {self._batch_total}: {vod.title}",
            "working",
        )

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
        ytdlp_source = ""
        ytdlp_format = ""
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
            ytdlp_source = selected_q.ytdlp_source
            ytdlp_format = selected_q.ytdlp_format

        if not playlist_url and not ytdlp_source:
            self._log(f"[ERROR] No playback URL for {vod.title}")
            self._batch_idx += 1
            self._batch_next()
            return

        total_secs = info.total_secs
        seg_secs = self._get_segment_secs()

        # Render folder + filename from templates
        ctx = _build_template_context(info, vod)
        folder_parts = _render_template(
            self._folder_template, ctx
        ) or [_safe_filename(vod.title) or f"{info.platform}_download"]
        file_parts = _render_template(self._file_template, ctx)
        title_safe = file_parts[-1] if file_parts else (
            _safe_filename(info.title) or _safe_filename(vod.title) or f"{info.platform}_download"
        )

        # ytdlp_direct and mp4 formats download monolithically — no segment splitting
        if fmt_type in ("ytdlp_direct", "mp4") or seg_secs == 0 or total_secs <= 0 or total_secs <= seg_secs:
            segments = [(0, title_safe, 0, int(total_secs) if total_secs > 0 else 0)]
        else:
            segments = []
            pos, idx = 0, 0
            while pos < total_secs:
                end = min(pos + seg_secs, total_secs)
                segments.append((idx, f"{title_safe}_part{idx + 1:02d}", pos, int(end - pos)))
                pos = end
                idx += 1

        out_dir = os.path.join(self.output_input.text().strip(), *folder_parts)
        os.makedirs(out_dir, exist_ok=True)

        self._build_segments(total_secs)
        self.stream_info = info

        self._total_segments = len(segments)
        self._completed_segments = 0
        self.overall_progress.setVisible(True)
        self.overall_progress.setValue(0)
        self.overall_progress.setMaximum(len(segments))
        self._refresh_download_summary()
        self._set_status(
            f"Downloading VOD {self._batch_idx + 1} of {self._batch_total}.",
            "working",
        )

        worker = DownloadWorker(playlist_url or "", segments, out_dir, format_type=fmt_type)
        worker.audio_url = audio_url
        worker.ytdlp_source = ytdlp_source
        worker.ytdlp_format = ytdlp_format
        worker.cookies_browser = YtDlpExtractor.cookies_browser
        worker.rate_limit = YtDlpExtractor.rate_limit
        worker.proxy = YtDlpExtractor.proxy
        worker.download_subs = YtDlpExtractor.download_subs
        worker.sponsorblock = YtDlpExtractor.sponsorblock
        worker.parallel_connections = self._parallel_connections
        worker.progress.connect(self._on_dl_progress)
        worker.segment_done.connect(self._on_segment_done)
        worker.error.connect(self._on_dl_error)
        worker.log.connect(self._log)
        worker.all_done.connect(self._batch_vod_done)
        self.download_worker = worker
        worker.start()

    def _batch_on_fetch_error(self, err):
        self._log(f"[ERROR] {err}")
        self._set_status(f"Batch fetch error: {err}", "error")
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
        self._set_status(f"Batch complete. Downloaded {self._batch_total} VOD(s).", "success")
        self._notify("StreamKeep — Batch complete", f"Downloaded {self._batch_total} VOD(s)")
        self._send_webhook("batch complete", f"{self._batch_total} VODs",
                           f"Batch download finished")
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

    def _is_audio_only(self):
        """Detect if the current stream is audio-only based on its qualities."""
        if not self.stream_info:
            return False
        if not self.stream_info.qualities:
            return False
        return all(
            (q.resolution or "").lower() == "audio" or "audio" in (q.name or "").lower()
            for q in self.stream_info.qualities
        )

    def _content_label(self, idx, total_segments, seg_secs, total_secs):
        """Generate a content-aware segment label."""
        is_audio = self._is_audio_only()
        kind = "Audio" if is_audio else "Video"
        if total_secs <= 0:
            return "Live Capture" if not is_audio else "Live Audio"

        if total_segments == 1:
            # Single segment — use the content type
            if total_secs < 60:
                return f"{kind} ({int(total_secs)}s)"
            elif total_secs < 3600:
                return f"{kind} ({int(total_secs // 60)}m)"
            else:
                return f"{kind} ({_fmt_duration(total_secs)})"

        # Multi-segment naming based on segment length
        if seg_secs >= 3600:
            return f"Hour {idx + 1}"
        elif seg_secs >= 60:
            mins = seg_secs // 60
            return f"Part {idx + 1} ({mins}m)"
        else:
            return f"Part {idx + 1}"

    def _build_segments(self, total_secs):
        if total_secs <= 0:
            segments = [(0, 0)]
            seg_secs = 0
        else:
            seg_secs = self._get_segment_secs()

            # Auto-collapse: if content is shorter than segment length, use one segment
            if seg_secs == 0 or total_secs <= seg_secs:
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
            cb.stateChanged.connect(lambda _state, self=self: self._refresh_download_summary())
            cb_w = QWidget()
            cb_l = QHBoxLayout(cb_w)
            cb_l.addWidget(cb)
            cb_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_l.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(i, 0, cb_w)
            self._segment_checks.append(cb)

            label = self._content_label(i, len(segments), seg_secs, total_secs)
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 1, item)

            if total_secs <= 0:
                range_text = "Starts now - runs until stopped"
            else:
                s_str = f"{int(start//3600):02d}:{int((start%3600)//60):02d}:{int(start%60):02d}"
                e_str = f"{int(end//3600):02d}:{int((end%3600)//60):02d}:{int(end%60):02d}"
                range_text = f"{s_str} - {e_str}  ({int(duration//60)}m {int(duration%60)}s)"
            t_item = QTableWidgetItem(range_text)
            t_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 2, t_item)

            pbar = QProgressBar()
            if total_secs <= 0:
                pbar.setMaximum(0)
            else:
                pbar.setValue(0)
            self.table.setCellWidget(i, 3, pbar)
            self._segment_progress.append(pbar)

            # Estimated size: bandwidth (bits/sec) × duration / 8 = bytes
            est = self._estimate_size_bytes(duration)
            sz_text = f"~{_fmt_size(est)}" if est > 0 else "\u2014"
            sz = QTableWidgetItem(sz_text)
            sz.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sz.setForeground(QColor(CAT["muted"]))
            self.table.setItem(i, 4, sz)
        self._refresh_download_summary()

    def _estimate_size_bytes(self, duration_secs):
        """Return estimated file size in bytes using the selected quality's bandwidth."""
        if duration_secs <= 0:
            return 0
        q = self.quality_combo.currentData() if hasattr(self, "quality_combo") else None
        if not q or not getattr(q, "bandwidth", 0):
            return 0
        # bandwidth is bits/sec; convert to bytes
        return int(q.bandwidth * duration_secs / 8)

    def _on_select_all(self, state):
        checked = state == Qt.CheckState.Checked.value
        for cb in self._segment_checks:
            cb.setChecked(checked)
        self._refresh_download_summary()

    def _on_segment_length_changed(self, idx):
        if self.stream_info and self.stream_info.total_secs > 0:
            self._build_segments(self.stream_info.total_secs)
        else:
            self._refresh_download_summary()

    def _on_quality_changed(self, idx):
        """Rebuild size estimates in the segment table when quality changes."""
        if not self.stream_info or not hasattr(self, "_segment_progress"):
            return
        total_secs = self.stream_info.total_secs
        if total_secs <= 0:
            return
        seg_secs = self._get_segment_secs()
        if seg_secs == 0 or total_secs <= seg_secs:
            durations = [total_secs]
        else:
            durations = []
            pos = 0
            while pos < total_secs:
                end = min(pos + seg_secs, total_secs)
                durations.append(end - pos)
                pos = end
        for i, d in enumerate(durations):
            if i >= self.table.rowCount():
                break
            est = self._estimate_size_bytes(d)
            sz_text = f"~{_fmt_size(est)}" if est > 0 else "\u2014"
            sz = QTableWidgetItem(sz_text)
            sz.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sz.setForeground(QColor(CAT["muted"]))
            self.table.setItem(i, 4, sz)

    def _on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_input.text())
        if d:
            self.output_input.setText(d)

    # ── Download ──────────────────────────────────────────────────────

    def _on_download(self):
        if not self.stream_info:
            return
        total_secs = self.stream_info.total_secs
        is_live_capture = bool(self.stream_info.is_live or total_secs <= 0)

        q_data = self.quality_combo.currentData()
        audio_url = ""
        ytdlp_source = ""
        ytdlp_format = ""
        if q_data:
            playlist_url = q_data.url
            fmt_type = q_data.format_type
            audio_url = q_data.audio_url
            ytdlp_source = q_data.ytdlp_source
            ytdlp_format = q_data.ytdlp_format
        elif self.stream_info.url:
            playlist_url = self.stream_info.url
            fmt_type = "hls"
        else:
            self._log("[ERROR] No quality selected")
            self._set_status("Pick a quality before starting the download.", "warning")
            return

        # Render filename + folder from templates (templates can produce
        # nested paths like "{channel}/{date} - {title}")
        ctx = _build_template_context(self.stream_info)
        folder_parts = _render_template(self._folder_template, ctx)
        file_parts = _render_template(self._file_template, ctx)
        title_safe = file_parts[-1] if file_parts else (
            _safe_filename(self.stream_info.title)
            or f"{self.stream_info.platform}_download"
        )

        seg_secs = self._get_segment_secs()
        single_segment = (is_live_capture or fmt_type in ("mp4", "ytdlp_direct")
                          or seg_secs == 0 or total_secs <= seg_secs)
        segments = []
        for i, cb in enumerate(self._segment_checks):
            if cb.isChecked():
                if single_segment:
                    segments.append((0, title_safe, 0, 0 if is_live_capture else int(total_secs)))
                    break
                else:
                    start = i * seg_secs
                    end = min((i + 1) * seg_secs, total_secs)
                    label = f"{title_safe}_part{i + 1:02d}"
                    segments.append((i, label, start, int(end - start)))

        if not segments:
            self._log("No segments selected.")
            self._set_status("Select at least one segment before downloading.", "warning")
            return

        # For non-channel content, user's output box is the base; folder template
        # adds a subfolder. For channel content the template already has
        # {channel} in it, so joining still works.
        base_out = self.output_input.text().strip()
        if folder_parts:
            out_dir = os.path.join(base_out, *folder_parts)
        else:
            out_dir = base_out
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
        self.open_folder_btn.setVisible(False)
        self.overall_progress.setVisible(True)
        self.overall_progress.setValue(0)
        self.overall_progress.setMaximum(len(segments))
        if is_live_capture:
            self._set_status(
                f"Live capture started. Recording to {_path_label(out_dir)} until you stop it.",
                "working",
            )
        else:
            self._set_status(
                f"Downloading 0 of {len(segments)} segment(s) to {_path_label(out_dir)}.",
                "working",
            )

        self.download_worker = DownloadWorker(playlist_url or "", segments, out_dir, format_type=fmt_type)
        self.download_worker.audio_url = audio_url
        self.download_worker.ytdlp_source = ytdlp_source
        self.download_worker.ytdlp_format = ytdlp_format
        self.download_worker.cookies_browser = YtDlpExtractor.cookies_browser
        self.download_worker.rate_limit = YtDlpExtractor.rate_limit
        self.download_worker.proxy = YtDlpExtractor.proxy
        self.download_worker.download_subs = YtDlpExtractor.download_subs
        self.download_worker.sponsorblock = YtDlpExtractor.sponsorblock
        self.download_worker.parallel_connections = self._parallel_connections
        if audio_url:
            self._log(f"Audio merge: enabled (video-only format detected)")
        if fmt_type == "ytdlp_direct":
            self._log(f"Download mode: yt-dlp direct (handles URL refresh + format merge)")
        self.download_worker.progress.connect(self._on_dl_progress)
        self.download_worker.segment_done.connect(self._on_segment_done)
        self.download_worker.error.connect(self._on_dl_error)
        self.download_worker.log.connect(self._log)
        self.download_worker.all_done.connect(self._on_all_done)
        self.download_worker.start()

    def _on_dl_progress(self, idx, pct, status):
        if idx < len(self._segment_progress):
            if self.stream_info and (self.stream_info.is_live or self.stream_info.total_secs <= 0):
                self._segment_progress[idx].setMaximum(0)
            else:
                self._segment_progress[idx].setMaximum(100)
                self._segment_progress[idx].setValue(pct)
        if hasattr(self, "_total_segments") and self._total_segments:
            self._set_status(
                f"Downloading {self._completed_segments}/{self._total_segments}. Segment {idx + 1}: {status}",
                "working",
            )

    def _on_segment_done(self, idx, size_str):
        if idx < len(self._segment_progress):
            self._segment_progress[idx].setMaximum(100)
            self._segment_progress[idx].setValue(100)
            self._segment_progress[idx].setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {CAT['green']}; border-radius: 6px; }}"
            )
        size_item = QTableWidgetItem(size_str)
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(idx, 4, size_item)
        self._completed_segments += 1
        self.overall_progress.setValue(self._completed_segments)
        self._set_status(
            f"Downloaded {self._completed_segments} of {self._total_segments} segment(s).",
            "working",
        )

    def _on_dl_error(self, idx, err):
        if idx < len(self._segment_progress):
            self._segment_progress[idx].setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {CAT['red']}; border-radius: 6px; }}"
            )
        fail_item = QTableWidgetItem("FAILED")
        fail_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(idx, 4, fail_item)
        self._set_status(f"Segment {idx + 1} failed: {err}", "error")

    def _on_all_done(self):
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(True)
        self._log(f"\n{'=' * 50}")
        self._log("All downloads complete!")
        self._log(f"{'=' * 50}")
        title = self.stream_info.title if self.stream_info and self.stream_info.title else "Download"
        if self.stream_info and (self.stream_info.is_live or self.stream_info.total_secs <= 0):
            self._set_status("Live capture finished and was saved to the selected folder.", "success")
            self._notify("StreamKeep — Capture finished", title[:80])
            self._send_webhook("capture finished", title,
                               f"Segments: {self._completed_segments}")
        else:
            self._set_status(
                f"Download complete. Saved {self._completed_segments} segment(s) to the selected folder.",
                "success",
            )
            self._notify("StreamKeep — Download complete", title[:80])
            self._send_webhook("download complete", title,
                               f"Segments: {self._completed_segments}")
        out_dir = self.output_input.text().strip()
        q_name = self.quality_combo.currentText() if self.quality_combo.count() else ""
        self._save_metadata(out_dir, q_name)
        self._persist_config()
        # Auto-advance the queue if there's a pending item
        self._advance_queue()

    def _on_stop(self):
        worker = self.download_worker
        live_capture = bool(
            worker and any(len(seg) >= 4 and seg[3] <= 0 for seg in getattr(worker, "segments", []))
        )
        # Halt any in-progress batch by marking it done
        if hasattr(self, '_batch_vods') and hasattr(self, '_batch_total'):
            self._batch_idx = self._batch_total
        if self.download_worker is not None:
            try:
                self.download_worker.cancel()
                self.download_worker.wait(5000)
            except Exception:
                pass
            self.download_worker = None
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.overall_progress.setVisible(False)
        if hasattr(self, 'vod_dl_all_btn'):
            self.vod_dl_all_btn.setEnabled(True)
            self.vod_load_btn.setEnabled(True)
        for entry in self.monitor.entries:
            entry.is_recording = False
        self._refresh_monitor_summary()
        self._log("[CANCELLED] Download stopped by user.")
        if live_capture:
            self.open_folder_btn.setVisible(True)
            self._set_status("Recording stopped. Any captured portion was kept on disk.", "warning")
        else:
            self._set_status("Download cancelled. You can adjust the selection and try again.", "warning")

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
        subscribe = self.monitor_subscribe_cb.isChecked()
        if self.monitor.add_channel(url, interval, auto, subscribe):
            self.monitor_url_input.clear()
            self._log(
                f"[MONITOR] Added: {url} (every {interval}s, "
                f"auto-record: {auto}, subscribe: {subscribe})"
            )
            # Seed the archive with current VODs so we don't download the backlog
            if subscribe:
                ext = Extractor.detect(url)
                if ext and ext.supports_vod_listing():
                    try:
                        existing = ext.list_vods(url, log_fn=self._log)
                        ch_id = ext.extract_channel_id(url) or url
                        self.monitor.seed_archive(ch_id, [v.source for v in existing if v.source])
                        self._log(
                            f"[SUBSCRIBE] Seeded archive with {len(existing)} existing VOD(s). "
                            f"Only new VODs will be queued from now on."
                        )
                    except Exception as e:
                        self._log(f"[SUBSCRIBE] Seed failed: {e}")
            self._persist_config()
            self._set_status("Channel added to the watch list.", "success")
        else:
            self._log(f"[MONITOR] Cannot add: unsupported or duplicate")
            self._set_status("Channel could not be added. It may already exist or be unsupported.", "error")

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
            rm_btn.setObjectName("ghost")
            rm_btn.setFixedHeight(28)
            rm_btn.clicked.connect(lambda checked, idx=i: self._on_monitor_remove(idx))
            self.monitor_table.setCellWidget(i, 5, rm_btn)
        self._refresh_monitor_summary()

    def _on_monitor_remove(self, idx):
        self.monitor.remove_channel(idx)
        self._persist_config()
        self._set_status("Channel removed from the watch list.", "success")

    def _on_channel_live(self, channel_id):
        """Called when a monitored channel goes live."""
        self._set_status(f"{channel_id} went live.", "warning")

        # Guard against overlapping recording (signal can fire twice in quick succession)
        target = None
        for e in self.monitor.entries:
            if e.channel_id == channel_id and e.auto_record and not e.is_recording:
                target = e
                e.is_recording = True  # claim the slot atomically before resolve
                break
        if target is None:
            return

        # Don't start a new download while one is already running
        existing = getattr(self, "download_worker", None)
        if existing is not None and existing.isRunning():
            self._log(f"[AUTO-RECORD] Skipped {channel_id}: download already in progress")
            target.is_recording = False
            return

        self._log(f"[AUTO-RECORD] Starting recording for {target.platform}/{channel_id}")
        try:
            ext = Extractor.detect(target.url)
            if not ext:
                self._log(f"[AUTO-RECORD] No extractor for {target.url}")
                target.is_recording = False
                return
            info = ext.resolve(target.url, log_fn=self._log)
            if not info or not info.qualities:
                self._log(f"[AUTO-RECORD] Failed to resolve {target.url}")
                target.is_recording = False
                return
            q = info.qualities[0]
            try:
                base_out = self.output_input.text().strip() or str(_default_output_dir())
                out_dir = os.path.join(
                    base_out,
                    f"auto_{_safe_filename(channel_id)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                )
                os.makedirs(out_dir, exist_ok=True)
            except OSError as e:
                self._log(f"[AUTO-RECORD] Cannot create output folder: {e}")
                target.is_recording = False
                return
            segments = [(0, "live_recording", 0, 0)]
            worker = DownloadWorker(q.url, segments, out_dir, q.format_type)
            worker.audio_url = q.audio_url
            worker.parallel_connections = self._parallel_connections
            worker.log.connect(self._log)
            worker.all_done.connect(lambda: self._auto_record_done(channel_id))
            self.download_worker = worker
            worker.start()
        except Exception as ex:
            self._log(f"[AUTO-RECORD] Error: {ex}")
            target.is_recording = False

    def _auto_record_done(self, channel_id):
        for e in self.monitor.entries:
            if e.channel_id == channel_id:
                e.is_recording = False
        self._log(f"[AUTO-RECORD] Recording ended for {channel_id}")
        self._refresh_monitor_summary()
        self._set_status(f"Auto-record finished for {channel_id}.", "success")

    # ── Download Queue ────────────────────────────────────────────────

    def _queue_add(self, url, title="", platform="", note="", start_at=""):
        """Append a URL to the persistent download queue.
        If start_at (ISO timestamp) is set, the item will only be picked
        up by _advance_queue after that time."""
        if not url:
            return False
        if any(q.get("url") == url and q.get("status") == "queued"
               for q in self._download_queue):
            return False
        self._download_queue.append({
            "url": url,
            "title": title or url,
            "platform": platform or "?",
            "status": "queued",
            "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "note": note,
            "start_at": start_at,
        })
        self._persist_config()
        if hasattr(self, "queue_table"):
            self._refresh_queue_table()
        return True

    def _queue_remove(self, idx):
        if 0 <= idx < len(self._download_queue):
            removed = self._download_queue.pop(idx)
            self._persist_config()
            if hasattr(self, "queue_table"):
                self._refresh_queue_table()
            return removed
        return None

    def _advance_queue(self):
        """Start the next queued item if nothing is downloading.
        Scheduled items (start_at in the future) are skipped."""
        worker = getattr(self, "download_worker", None)
        if worker is not None and worker.isRunning():
            return
        now = datetime.now()
        # Pop the next non-scheduled queued item
        next_item = None
        for i, q in enumerate(self._download_queue):
            if q.get("status") != "queued":
                continue
            start_at = q.get("start_at", "")
            if start_at:
                try:
                    ts = datetime.fromisoformat(start_at)
                    if ts > now:
                        continue  # still scheduled, skip
                except Exception:
                    pass
            next_item = self._download_queue.pop(i)
            break
        if not next_item:
            return
        self._log(f"[QUEUE] Auto-starting: {next_item.get('title', '')[:60]}")
        self._persist_config()
        if hasattr(self, "queue_table"):
            self._refresh_queue_table()
        self.url_input.setText(next_item["url"])
        self._switch_tab(0)
        self._on_fetch()

    def _refresh_queue_table(self):
        if not hasattr(self, "queue_table"):
            return
        self.queue_table.setRowCount(len(self._download_queue))
        now = datetime.now()
        for i, q in enumerate(self._download_queue):
            # Compute effective status: "scheduled" if start_at is in the future
            status = q.get("status", "queued")
            start_at = q.get("start_at", "")
            is_scheduled = False
            if start_at and status == "queued":
                try:
                    ts = datetime.fromisoformat(start_at)
                    if ts > now:
                        is_scheduled = True
                except Exception:
                    pass
            display_status = "SCHEDULED" if is_scheduled else status.upper()
            status_item = QTableWidgetItem(display_status)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_scheduled:
                status_item.setForeground(QColor(CAT["peach"]))
            elif status == "queued":
                status_item.setForeground(QColor(CAT["yellow"]))
            self.queue_table.setItem(i, 0, status_item)
            self.queue_table.setItem(i, 1, QTableWidgetItem(q.get("platform", "?")))
            self.queue_table.setItem(i, 2, QTableWidgetItem(q.get("title", "")[:80]))
            # Added / Scheduled column
            added = q.get("added", "")
            if is_scheduled:
                added = f"@ {start_at[:16].replace('T', ' ')}"
            self.queue_table.setItem(i, 3, QTableWidgetItem(added))
            # Move up/down buttons (single cell with two stacked buttons)
            move_widget = QWidget()
            move_lay = QHBoxLayout(move_widget)
            move_lay.setContentsMargins(2, 2, 2, 2)
            move_lay.setSpacing(2)
            up_btn = QPushButton("↑")
            up_btn.setObjectName("ghost")
            up_btn.setFixedWidth(28)
            up_btn.setToolTip("Move up")
            up_btn.clicked.connect(lambda _c=False, row=i: self._queue_move(row, -1))
            down_btn = QPushButton("↓")
            down_btn.setObjectName("ghost")
            down_btn.setFixedWidth(28)
            down_btn.setToolTip("Move down")
            down_btn.clicked.connect(lambda _c=False, row=i: self._queue_move(row, 1))
            if i == 0:
                up_btn.setEnabled(False)
            if i == len(self._download_queue) - 1:
                down_btn.setEnabled(False)
            move_lay.addWidget(up_btn)
            move_lay.addWidget(down_btn)
            self.queue_table.setCellWidget(i, 4, move_widget)
            # Remove button
            rm_btn = QPushButton("Remove")
            rm_btn.setObjectName("secondary")
            rm_btn.clicked.connect(lambda _c=False, row=i: self._queue_remove(row))
            self.queue_table.setCellWidget(i, 5, rm_btn)

    def _queue_move(self, idx, direction):
        """Move a queue item up (-1) or down (+1)."""
        if idx < 0 or idx >= len(self._download_queue):
            return
        target = idx + direction
        if target < 0 or target >= len(self._download_queue):
            return
        self._download_queue[idx], self._download_queue[target] = (
            self._download_queue[target], self._download_queue[idx]
        )
        self._persist_config()
        self._refresh_queue_table()

    def _on_new_vods_found(self, channel_id, vods):
        """New VODs from a subscribed channel — queue their source URLs
        so they get downloaded in the background."""
        for v in vods:
            # Skip if already in history (prevents re-downloading on seed)
            if self._find_duplicate("", v.title):
                continue
            entry = {
                "url": v.source,
                "title": v.title,
                "platform": v.platform,
                "status": "queued",
                "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "vod_date": v.date,
            }
            if not any(q.get("url") == v.source for q in self._download_queue):
                self._download_queue.append(entry)
                self._log(f"[SUBSCRIBE] Queued: {v.title[:60]}")
        self._persist_config()
        self._refresh_queue_table() if hasattr(self, "queue_table") else None
        # Kick off the queue if nothing is downloading
        if getattr(self.download_worker, "isRunning", lambda: False)() is False:
            self._advance_queue()

    # ── History Actions ───────────────────────────────────────────────

    def _add_history(self, platform, title, quality, size, path, url=""):
        entry = HistoryEntry(
            date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            platform=platform, title=title[:60],
            quality=quality, size=size, path=path, url=url,
        )
        self._history.append(entry)
        self._refresh_history_table()

    def _refresh_history_table(self):
        query = ""
        if hasattr(self, "history_search"):
            query = self.history_search.text().strip().lower()
        ordered = list(reversed(self._history))
        if query:
            ordered = [
                h for h in ordered
                if query in (h.title or "").lower()
                or query in (h.platform or "").lower()
                or query in (h.path or "").lower()
                or query in (h.url or "").lower()
            ]
        self._history_view = ordered  # used by double-click handler
        self.history_table.setRowCount(len(ordered))
        for i, h in enumerate(ordered):
            for col, val in enumerate([h.date, h.platform, h.title, h.quality, h.size, h.path]):
                item = QTableWidgetItem(val)
                if col in (0, 1, 3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.history_table.setItem(i, col, item)
        self._refresh_history_summary()

    def _on_history_search(self, _text):
        self._refresh_history_table()

    def _on_clear_history(self):
        self._history.clear()
        self._refresh_history_table()
        self._persist_config()
        self._set_status("Download history cleared.", "success")

    def _on_history_double_click(self, index):
        row = index.row()
        view = getattr(self, "_history_view", list(reversed(self._history)))
        if row >= len(view):
            return
        h = view[row]
        # If the folder still exists, open it
        if h.path and os.path.isdir(h.path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(h.path))
            return
        # Otherwise offer to retry the download if we have the URL
        if h.url:
            self._log(f"[HISTORY] Folder missing — re-fetching {h.url}")
            self._set_status(f"Re-fetching {h.title or h.url}...", "working")
            self.url_input.setText(h.url)
            self._switch_tab(0)
            self._on_fetch()
        else:
            self._set_status("Path missing and no saved URL to retry.", "warning")

    # ── Metadata ──────────────────────────────────────────────────────

    def _save_metadata(self, out_dir, quality_name=""):
        if self.stream_info:
            MetadataSaver.save(out_dir, self.stream_info)
            file_base = _safe_filename(self.stream_info.title) if self.stream_info.title else ""
            # NFO metadata for Kodi/Jellyfin/Plex libraries
            if self._write_nfo:
                MetadataSaver.write_nfo(out_dir, self.stream_info, file_base=file_base)
                self._log(f"[NFO] Wrote {file_base or 'movie'}.nfo for media library")
            # Chapter export (YouTube + any yt-dlp source with chapters)
            if MetadataSaver.write_chapters(out_dir, self.stream_info, file_base=file_base):
                count = len(self.stream_info.chapters)
                self._log(f"[CHAPTERS] Exported {count} chapter(s) to {file_base}.chapters.txt/.json")
            # Twitch chat replay
            if (TwitchExtractor.download_chat_enabled
                    and self.stream_info.platform == "Twitch"
                    and self.stream_info.url):
                m = re.search(r'/vod/(\d+)\.m3u8', self.stream_info.url)
                if m:
                    vod_id = m.group(1)
                    file_base = _safe_filename(self.stream_info.title) if self.stream_info.title else "chat"
                    chat_base = os.path.join(out_dir, file_base)
                    self._log(f"[CHAT] Fetching chat replay for VOD {vod_id}...")
                    count, err = TwitchExtractor().download_chat(
                        vod_id, chat_base, log_fn=self._log
                    )
                    if err:
                        self._log(f"[CHAT] Failed: {err}")
                    else:
                        self._log(f"[CHAT] Saved {count} comments to {file_base}.chat.json/.txt")
            # Post-processing presets (audio extract, loudnorm, H.265, contact sheet, split)
            if PostProcessor.has_any_preset():
                PostProcessor.process_directory(
                    out_dir,
                    log_fn=self._log,
                    chapters=self.stream_info.chapters or None,
                )
        # Add to history (use the URL from the input box so we can retry later)
        platform = self.stream_info.platform if self.stream_info else "?"
        title = self.stream_info.title if self.stream_info else "?"
        url = self.url_input.text().strip() if hasattr(self, "url_input") else ""
        self._add_history(platform, title, quality_name, "", out_dir, url=url)
