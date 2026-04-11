"""StreamKeep main window — the full QMainWindow class, tabs, and handlers.

Phase 2 of the modularization moved this out of the root-level
StreamKeep.py. The class is still a god object (~3670 lines, 235 methods);
Phase 3 will carve it into per-tab widgets. For now the split wins us a
predictable file layout and keeps the runtime unchanged.
"""

import copy
import json
import os
import re
import subprocess
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidgetItem, QProgressBar,
    QFileDialog, QFrame, QStackedWidget, QSystemTrayIcon,
    QInputDialog, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPixmap, QPainter, QBrush

# Package re-exports under legacy underscore-prefixed names so the existing
# method bodies below don't need modification.
from streamkeep import VERSION
from streamkeep.paths import _CREATE_NO_WINDOW
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
    FinalizeWorker,
    PlaylistExpandWorker as _PlaylistExpandWorker,
    PageScrapeWorker as _PageScrapeWorker,
    SeedArchiveWorker,
    AutoRecordResolveWorker,
)
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

# Platform badges, tab CSS, and widget builders live in streamkeep.ui.widgets.
# Imported here under legacy underscored names so the method bodies below
# (which still use `self._make_field_block(...)` etc. in 50+ places) keep
# working until a future pass switches each call site.
from .widgets import (
    PLATFORM_BADGES,
    TAB_STYLE,
    path_label as _path_label,
    make_metric_card,
    make_field_block,
    wrap_scroll_page,
    style_table,
    set_metric,
)
from .tabs.download import build_download_tab
from .tabs.history import build_history_tab
from .tabs.monitor import build_monitor_tab
from .tabs.settings import build_settings_tab


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
        self._last_fetch_request = {
            "url": "",
            "vod_source": "",
            "vod_platform": "",
            "vod_title": "",
            "vod_channel": "",
        }
        self._active_output_dir = ""
        self._active_quality_name = ""
        self._active_history_url = ""
        self._active_stream_info = None
        self._download_had_errors = False
        self._queue_active_item = None
        self._queue_autostart = False
        self._pending_auto_records = []
        self._batch_active = False
        self._monitor_seed_workers = {}
        self._auto_record_resolve_worker = None
        self._active_auto_record_channel = ""
        self._finalize_tasks = []
        self._finalize_worker = None
        self._finalize_active_title = ""
        self._finalize_active_label = ""
        self._finalize_active_step = 0
        self._finalize_active_total = 0
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
        self._config_save_timer = QTimer(self)
        self._config_save_timer.setSingleShot(True)
        self._config_save_timer.timeout.connect(self._persist_config)
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
        queue_items = []
        for q in cfg.get("download_queue", []):
            normalized = self._normalize_queue_item(q)
            if normalized is not None:
                queue_items.append(normalized)
        self._download_queue = queue_items
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
                    channel=str(h.get("channel", "")),
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
                           "channel": h.channel,
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

    def _schedule_persist_config(self, delay_ms=500):
        timer = getattr(self, "_config_save_timer", None)
        if timer is None:
            self._persist_config()
            return
        timer.start(max(0, int(delay_ms)))

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
                if not dw.wait(3000):
                    dw.terminate()
                    dw.wait(1000)
            except Exception:
                pass
        fw = getattr(self, "_fetch_worker", None)
        if fw is not None and fw.isRunning():
            try:
                fw.requestInterruption()
                fw.wait(1500)
            except Exception:
                pass
        bfw = getattr(self, "_batch_fetch_worker", None)
        if bfw is not None and bfw.isRunning():
            try:
                bfw.requestInterruption()
                bfw.wait(1500)
            except Exception:
                pass
        arw = getattr(self, "_auto_record_resolve_worker", None)
        if arw is not None and arw.isRunning():
            try:
                arw.requestInterruption()
                arw.wait(1500)
            except Exception:
                pass
        for key, worker in list(getattr(self, "_monitor_seed_workers", {}).items()):
            try:
                if worker is not None and worker.isRunning():
                    worker.requestInterruption()
                    worker.wait(1500)
            except Exception:
                pass
            self._monitor_seed_workers.pop(key, None)
        fwk = getattr(self, "_finalize_worker", None)
        if fwk is not None and fwk.isRunning():
            try:
                fwk.cancel()
                if not fwk.wait(1500):
                    fwk.terminate()
                    fwk.wait(500)
            except Exception:
                pass
        ew = getattr(self, "_expand_worker", None)
        if ew is not None and ew.isRunning():
            try:
                ew.requestInterruption()
                ew.wait(1500)
            except Exception:
                pass
        sw = getattr(self, "_scan_worker", None)
        if sw is not None and sw.isRunning():
            try:
                sw.requestInterruption()
                sw.wait(1500)
            except Exception:
                pass
        self._finalize_tasks = []
        # Stop convert worker if running (standalone file/folder batch)
        cw = getattr(self, "_convert_worker", None)
        if cw is not None and cw.isRunning():
            try:
                cw.cancel()
                if not cw.wait(3000):
                    cw.terminate()
                    cw.wait(1000)
            except Exception:
                pass
        # Stop monitor timer
        try:
            self.monitor._timer.stop()
        except Exception:
            pass
        try:
            self._scheduler_timer.stop()
        except Exception:
            pass
        try:
            self._config_save_timer.stop()
        except Exception:
            pass
        # Stop clipboard monitor
        try:
            self.clipboard_monitor.stop()
        except Exception:
            pass
        # Hide + remove tray icon so Windows doesn't leak a dead icon slot
        # until explorer.exe is restarted.
        try:
            if self._tray_icon is not None:
                self._tray_icon.hide()
                self._tray_icon.deleteLater()
                self._tray_icon = None
        except Exception:
            pass
        self._persist_config()
        super().closeEvent(event)

    # Widget builders are thin forwarders to streamkeep.ui.widgets so the
    # 50+ `self._make_*` call sites below don't need to be rewritten.
    def _make_metric_card(self, *a, **kw):
        return make_metric_card(*a, **kw)

    def _make_field_block(self, *a, **kw):
        return make_field_block(*a, **kw)

    def _wrap_scroll_page(self, *a, **kw):
        return wrap_scroll_page(*a, **kw)

    def _style_table(self, *a, **kw):
        return style_table(*a, **kw)

    def _set_metric(self, *a, **kw):
        return set_metric(*a, **kw)

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
            "processing": ("Finalizing", CAT["accentSoft"], CAT["crust"], CAT["accent"]),
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
        finalize_active = bool(self._finalize_worker is not None and self._finalize_worker.isRunning())
        finalize_queued = len(self._finalize_tasks)
        finalize_total = finalize_queued + (1 if finalize_active else 0)
        if finalize_active:
            if self._finalize_active_total:
                finalize_value = f"{self._finalize_active_step}/{self._finalize_active_total}"
            else:
                finalize_value = "Starting"
            finalize_parts = []
            if self._finalize_active_title:
                finalize_parts.append(self._finalize_active_title[:42])
            if self._finalize_active_label:
                finalize_parts.append(self._finalize_active_label)
            finalize_sub = " | ".join(finalize_parts) or "Preparing background cleanup"
            if finalize_queued:
                finalize_sub = f"{finalize_sub} | {finalize_queued} queued"
        elif finalize_queued:
            finalize_value = f"{finalize_queued} queued"
            finalize_sub = "Waiting for the current background cleanup"
        else:
            finalize_value = "Idle"
            finalize_sub = "Metadata and post-processing will queue here"

        self._set_metric(self.download_platform_value, self.download_platform_sub, platform_value, platform_sub)
        self._set_metric(self.download_duration_value, self.download_duration_sub, duration_value, duration_sub)
        self._set_metric(self.download_selection_value, self.download_selection_sub, selection_value, selection_sub)
        self._set_metric(
            self.download_output_value,
            self.download_output_sub,
            _path_label(output_path),
            output_sub or "Choose a destination folder",
        )
        if hasattr(self, "download_finalize_value"):
            self._set_metric(
                self.download_finalize_value,
                self.download_finalize_sub,
                finalize_value,
                finalize_sub,
            )
        self.download_output_value.setToolTip(output_path)
        self.download_output_sub.setToolTip(output_path)
        if hasattr(self, "download_finalize_sub"):
            self.download_finalize_sub.setToolTip(finalize_sub)

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
                # Top channel: prefer stored creator/channel metadata, then fall back
                # to a conservative URL-derived guess for older history rows.
                ch_counts = Counter()
                for h in self._history:
                    key = self._history_channel_label(h)
                    if key:
                        ch_counts[key] += 1
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
        self._stack.addWidget(self._wrap_scroll_page(build_download_tab(self)))
        self._stack.addWidget(self._wrap_scroll_page(build_monitor_tab(self)))
        self._stack.addWidget(self._wrap_scroll_page(build_history_tab(self)))
        self._stack.addWidget(self._wrap_scroll_page(build_settings_tab(self)))
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
        req = getattr(self, "_last_fetch_request", {})
        vod_source = str(req.get("vod_source", "") or "")
        vod_platform = str(req.get("vod_platform", "") or "")
        vod_title = str(req.get("vod_title", "") or "")
        vod_channel = str(req.get("vod_channel", "") or "")
        title = ""
        platform = ""
        if self.stream_info:
            title = self.stream_info.title or ""
            platform = self.stream_info.platform or ""
        queue_url = vod_source or url
        added = self._queue_add(
            queue_url,
            title=title,
            platform=platform,
            vod_source=vod_source,
            vod_platform=vod_platform,
            vod_title=vod_title or title,
            vod_channel=vod_channel or (self.stream_info.channel if self.stream_info else ""),
        )
        if added:
            self._set_status(f"Queued: {title or queue_url[:60]}", "success")
        else:
            self._set_status("URL already in the queue.", "warning")

    def _on_schedule_url(self):
        """Queue the current URL with a deferred start time."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a URL first.", "warning")
            return
        req = getattr(self, "_last_fetch_request", {})
        vod_source = str(req.get("vod_source", "") or "")
        vod_platform = str(req.get("vod_platform", "") or "")
        vod_title = str(req.get("vod_title", "") or "")
        vod_channel = str(req.get("vod_channel", "") or "")
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
        queue_url = vod_source or url
        added = self._queue_add(
            queue_url,
            title=title,
            platform=platform,
            start_at=start_at.isoformat(),
            vod_source=vod_source,
            vod_platform=vod_platform,
            vod_title=vod_title or title,
            vod_channel=vod_channel or (self.stream_info.channel if self.stream_info else ""),
        )
        if added:
            self._set_status(
                f"Scheduled for {start_at.strftime('%Y-%m-%d %H:%M')}: {title or queue_url[:60]}",
                "success",
            )
        else:
            self._set_status("URL already in the queue.", "warning")

    def _on_clear_queue(self):
        active = self._queue_active_item
        self._download_queue = [q for q in self._download_queue if q is active]
        self._persist_config()
        self._refresh_queue_table()
        self._set_status("Queue cleared.", "success")

    # ── Monitor Tab ───────────────────────────────────────────────────

    # ── Settings Tab ──────────────────────────────────────────────────

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

    # Soft cap on in-memory log panel lines. A long monitor/queue session
    # can accumulate hundreds of MB of text over time — trim from the top
    # in batches to keep append+scroll O(1) amortized.
    _LOG_SOFT_MAX_BLOCKS = 5000
    _LOG_TRIM_BLOCKS = 1000

    def _log(self, msg):
        self.log_text.append(msg)
        doc = self.log_text.document()
        if doc.blockCount() > self._LOG_SOFT_MAX_BLOCKS:
            try:
                from PyQt6.QtGui import QTextCursor
                cursor = QTextCursor(doc)
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                for _ in range(self._LOG_TRIM_BLOCKS):
                    cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                    cursor.removeSelectedText()
                    cursor.deleteChar()  # drop the now-empty block separator
            except Exception:
                pass
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
        previous = list(self._recent_urls)
        # Dedup: move to front if already present
        if url in self._recent_urls:
            self._recent_urls.remove(url)
        self._recent_urls.insert(0, url)
        # Keep the last 30
        self._recent_urls = self._recent_urls[:30]
        if hasattr(self, "_recent_url_model"):
            self._recent_url_model.setStringList(self._recent_urls)
        if self._recent_urls != previous:
            self._schedule_persist_config()

    def _infer_history_channel(self, url="", platform="", channel=""):
        channel = (channel or "").strip()
        if channel and not channel.lower().startswith("vod_"):
            return channel
        if not url:
            return ""
        try:
            ext = Extractor.detect(url)
            if ext is not None:
                detected = (ext.extract_channel_id(url) or "").strip()
                if (
                    detected
                    and not detected.lower().startswith("vod_")
                    and not detected.isdigit()
                    and getattr(ext, "NAME", "") in {"Kick", "Twitch", "SoundCloud", "Audius", "Podcast"}
                ):
                    return detected
            parsed = urllib.parse.urlparse(url)
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            blocked = {
                "videos", "video", "embed", "watch", "shorts",
                "playlist", "live", "vod", "v",
            }
            for part in parts:
                if part.lower() not in blocked and not part.isdigit():
                    return part
        except Exception:
            return ""
        return ""

    def _history_channel_label(self, entry):
        channel = self._infer_history_channel(
            url=getattr(entry, "url", ""),
            platform=getattr(entry, "platform", ""),
            channel=getattr(entry, "channel", ""),
        )
        if not channel:
            return ""
        platform = (getattr(entry, "platform", "") or "").strip()
        if platform:
            return f"{platform}/{channel}"
        return channel

    def _find_duplicate(self, url, title="", platform=""):
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
                if (platform and h.platform
                        and h.platform.strip().lower() != platform.strip().lower()):
                    continue
                if h.title and h.title.strip().lower() == norm and h.path:
                    return h
        return None

    def _set_download_context(self, out_dir="", quality_name="", history_url="", info=None):
        self._active_output_dir = out_dir
        self._active_quality_name = quality_name
        self._active_history_url = history_url
        self._active_stream_info = info or self.stream_info

    def _output_contains_media(self, out_dir):
        if not out_dir or not os.path.isdir(out_dir):
            return False
        media_exts = {ext.lower() for ext in (VIDEO_EXTS | AUDIO_EXTS)}
        try:
            for entry in os.scandir(out_dir):
                if entry.is_file() and Path(entry.name).suffix.lower() in media_exts:
                    return True
        except OSError:
            return False
        return False

    def _output_size_label(self, out_dir):
        if not out_dir or not os.path.isdir(out_dir):
            return ""
        total = 0
        try:
            for root, _dirs, files in os.walk(out_dir):
                for name in files:
                    path = os.path.join(root, name)
                    try:
                        total += os.path.getsize(path)
                    except OSError:
                        continue
        except OSError:
            return ""
        return _fmt_size(total) if total > 0 else ""

    def _postprocess_snapshot(self):
        keys = [
            "extract_audio",
            "normalize_loudness",
            "reencode_h265",
            "contact_sheet",
            "split_by_chapter",
            "convert_video",
            "convert_video_format",
            "convert_video_codec",
            "convert_video_scale",
            "convert_video_fps",
            "convert_audio",
            "convert_audio_format",
            "convert_audio_codec",
            "convert_audio_bitrate",
            "convert_audio_samplerate",
            "convert_delete_source",
        ]
        return {k: getattr(PostProcessor, k) for k in keys}

    def _foreground_busy(self):
        if getattr(self, "_batch_active", False):
            return True
        for name in (
            "download_worker",
            "_fetch_worker",
            "_batch_fetch_worker",
            "_auto_record_resolve_worker",
            "_expand_worker",
            "_scan_worker",
        ):
            worker = getattr(self, name, None)
            if worker is not None and worker.isRunning():
                return True
        return False

    def _queue_status_changed(self):
        self._persist_config()
        if hasattr(self, "queue_table"):
            self._refresh_queue_table()

    def _set_queue_item_status(self, item, status, note=""):
        if item is None:
            return
        item["status"] = status
        item["note"] = note
        self._queue_status_changed()

    def _release_queue_item(self, status=None, note=""):
        item = self._queue_active_item
        self._queue_active_item = None
        self._queue_autostart = False
        if item is None:
            return
        if status == "done":
            try:
                self._download_queue.remove(item)
            except ValueError:
                pass
            self._queue_status_changed()
            return
        if status:
            self._set_queue_item_status(item, status, note)

    def _resolve_history_url(self):
        if self._active_history_url:
            return self._active_history_url
        req = getattr(self, "_last_fetch_request", {})
        vod_source = req.get("vod_source", "")
        if vod_source:
            return vod_source
        if self._queue_active_item is not None:
            return self._queue_active_item.get("url", "")
        if hasattr(self, "url_input"):
            return self.url_input.text().strip()
        return ""

    def _start_next_background_job(self):
        resolver = getattr(self, "_auto_record_resolve_worker", None)
        if resolver is not None and resolver.isRunning():
            return
        if self._drain_pending_auto_records():
            return
        self._advance_queue()

    def _start_finalize_worker(self):
        worker = getattr(self, "_finalize_worker", None)
        if worker is not None and worker.isRunning():
            return
        if not self._finalize_tasks:
            self._finalize_active_title = ""
            self._finalize_active_label = ""
            self._finalize_active_step = 0
            self._finalize_active_total = 0
            self._refresh_download_summary()
            return
        task = self._finalize_tasks.pop(0)
        self._finalize_active_title = task.get("title", "")
        self._finalize_active_label = "Preparing background cleanup"
        self._finalize_active_step = 0
        self._finalize_active_total = 0
        worker = FinalizeWorker(task)
        worker.log.connect(self._log)
        worker.progress.connect(self._on_finalize_progress)
        worker.done.connect(self._on_finalize_done)
        self._finalize_worker = worker
        worker.start()
        self._refresh_download_summary()
        if not self._foreground_busy():
            extra = f" {len(self._finalize_tasks)} more queued." if self._finalize_tasks else ""
            self._set_status(
                f"Finalizing {task.get('title', 'download')[:60]} in the background.{extra}",
                "processing",
            )

    def _enqueue_finalize_task(self, task):
        self._finalize_tasks.append(task)
        if len(self._finalize_tasks) > 1 or (
                self._finalize_worker is not None and self._finalize_worker.isRunning()):
            self._log(f"[FINALIZE] Queued background finalization for {task.get('title', 'download')[:60]}")
        self._refresh_download_summary()
        self._start_finalize_worker()

    def _on_finalize_progress(self, label, step_no, total_steps):
        self._finalize_active_label = label or ""
        self._finalize_active_step = max(0, int(step_no or 0))
        self._finalize_active_total = max(self._finalize_active_step, int(total_steps or 0))
        self._refresh_download_summary()
        if not self._foreground_busy():
            count = ""
            if self._finalize_active_total:
                count = f" ({self._finalize_active_step}/{self._finalize_active_total})"
            extra = f" {len(self._finalize_tasks)} more queued." if self._finalize_tasks else ""
            title = self._finalize_active_title[:60] or "download"
            step_text = f"{self._finalize_active_label}{count}" if self._finalize_active_label else f"step{count}"
            self._set_status(f"Finalizing {title}: {step_text}.{extra}", "processing")

    def _on_finalize_done(self, result):
        worker = getattr(self, "_finalize_worker", None)
        if worker is not None and not worker.isRunning():
            try:
                worker.wait(200)
            except Exception:
                pass
        self._finalize_worker = None
        self._finalize_active_title = ""
        self._finalize_active_label = ""
        self._finalize_active_step = 0
        self._finalize_active_total = 0
        finished_title = result.get("title", "download")
        if not result.get("cancelled"):
            self._add_history(
                result.get("platform", "?"),
                result.get("title", "?"),
                result.get("quality_name", ""),
                result.get("size_label", self._output_size_label(result.get("out_dir", ""))),
                result.get("out_dir", ""),
                channel=result.get("channel", ""),
                url=result.get("history_url", ""),
            )
        remaining = len(self._finalize_tasks)
        self._refresh_download_summary()
        if not self._foreground_busy():
            if result.get("cancelled"):
                self._set_status("Background finalization was cancelled.", "warning")
            elif remaining:
                self._set_status(
                    f"Finished finalizing {finished_title[:60]}. {remaining} background job(s) remaining.",
                    "processing",
                )
            else:
                self._set_status(
                    f"Background finalization complete for {finished_title[:60]}.",
                    "success",
                )
        self._start_finalize_worker()

    def _cancel_batch_fetch_worker(self):
        worker = getattr(self, "_batch_fetch_worker", None)
        if worker is None:
            return
        try:
            worker.requestInterruption()
        except Exception:
            pass
        if worker.isRunning():
            try:
                worker.wait(1500)
            except Exception:
                pass
        self._batch_fetch_worker = None

    def _start_monitor_seed_worker(self, url, channel_id):
        existing = self._monitor_seed_workers.get(channel_id)
        if existing is not None and existing.isRunning():
            return
        worker = SeedArchiveWorker(url, channel_id)
        worker.log.connect(self._log)
        worker.finished.connect(self._on_monitor_seed_done)
        worker.error.connect(self._on_monitor_seed_error)
        self._monitor_seed_workers[channel_id] = worker
        worker.start()

    def _clear_monitor_seed_worker(self, channel_id):
        worker = self._monitor_seed_workers.pop(channel_id, None)
        if worker is not None and not worker.isRunning():
            try:
                worker.wait(200)
            except Exception:
                pass

    def _on_fetch(self, vod_source=None, vod_platform=None, vod_title=None, vod_channel=None):
        url = self.url_input.text().strip()
        if not url:
            return
        self._last_fetch_request = {
            "url": url,
            "vod_source": vod_source or "",
            "vod_platform": vod_platform or "",
            "vod_title": vod_title or "",
            "vod_channel": vod_channel or "",
        }
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

        self._fetch_worker = FetchWorker(
            url,
            vod_source=vod_source,
            vod_platform=vod_platform,
            vod_title=vod_title,
            vod_channel=vod_channel,
        )
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
        dup = self._find_duplicate("", info.title, platform=info.platform)
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

        if self._queue_autostart and self._queue_active_item is not None:
            self._set_queue_item_status(self._queue_active_item, "downloading")
            if not self._on_download():
                self._release_queue_item("failed", "Could not start the queued download")
                self._start_next_background_job()

    def _on_fetch_error(self, err):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self._log(f"[ERROR] {err}")
        self._refresh_download_summary()
        self._set_status(f"Fetch failed: {err}", "error")
        if self._queue_active_item is not None:
            self._release_queue_item("failed", err[:120])
            self._start_next_background_job()

    def _on_vods_found(self, vod_list, platform_name):
        if self._queue_autostart and self._queue_active_item is not None:
            added = 0
            for vod in vod_list:
                if self._queue_add(
                        vod.source,
                        title=vod.title,
                        platform=vod.platform or platform_name,
                        vod_source=vod.source,
                        vod_platform=vod.platform or platform_name,
                        vod_title=vod.title,
                        vod_channel=vod.channel):
                    added += 1
            source_label = self._queue_active_item.get("title") or self._queue_active_item.get("url", "")
            self._release_queue_item("done")
            self._log(f"[QUEUE] Expanded {source_label[:60]} into {added} queued VOD(s)")
            tone = "success" if added else "warning"
            self._set_status(
                f"Expanded queued source into {added} VOD(s).",
                tone,
            )
            self._start_next_background_job()
            return

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
                self._on_fetch(
                    vod_source=vod.source,
                    vod_platform=vod.platform,
                    vod_title=vod.title,
                    vod_channel=vod.channel,
                )
                return
        self._log("No VOD checked.")
        self._set_status("Select at least one VOD before loading it.", "warning")

    def _on_vod_download_all(self):
        checked = [self._vod_list[i] for i, cb in enumerate(self._vod_checks) if cb.isChecked()]
        if not checked:
            self._log("No VODs checked.")
            self._set_status("Select at least one VOD before starting a batch download.", "warning")
            return

        self._cancel_batch_fetch_worker()
        self._batch_vods = checked
        self._batch_idx = 0
        self._batch_total = len(checked)
        self._batch_failed_count = 0
        self._batch_active = True
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
        if not self._batch_active:
            return
        if self._batch_idx >= self._batch_total:
            self._batch_done()
            return
        vod = self._batch_vods[self._batch_idx]
        self._log(f"\n--- VOD {self._batch_idx + 1}/{self._batch_total}: {vod.title} ---")
        self._set_status(
            f"Preparing VOD {self._batch_idx + 1} of {self._batch_total}: {vod.title}",
            "working",
        )

        worker = FetchWorker(
            self.url_input.text().strip(),
            vod_source=vod.source,
            vod_platform=vod.platform,
            vod_title=vod.title,
            vod_channel=vod.channel,
        )
        worker.log.connect(self._log)
        worker.finished.connect(self._batch_on_fetched)
        worker.error.connect(self._batch_on_fetch_error)
        self._batch_fetch_worker = worker
        worker.start()

    def _batch_on_fetched(self, info):
        self._batch_fetch_worker = None
        if not self._batch_active or self._batch_idx >= self._batch_total:
            return
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
        self._download_had_errors = False
        self.overall_progress.setVisible(True)
        self.overall_progress.setValue(0)
        self.overall_progress.setMaximum(len(segments))
        self._refresh_download_summary()
        self._set_status(
            f"Downloading VOD {self._batch_idx + 1} of {self._batch_total}.",
            "working",
        )
        self._set_download_context(
            out_dir=out_dir,
            quality_name=selected_q.name if selected_q else "batch",
            history_url=vod.source or self.url_input.text().strip(),
            info=info,
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
        self._batch_fetch_worker = None
        if not self._batch_active or self._batch_idx >= self._batch_total:
            return
        self._log(f"[ERROR] {err}")
        self._set_status(f"Batch fetch error: {err}", "error")
        if hasattr(self, "_batch_failed_count"):
            self._batch_failed_count += 1
        self._batch_idx += 1
        self._batch_next()

    def _batch_vod_done(self):
        if not self._batch_active or self._batch_idx >= self._batch_total:
            return
        vod = self._batch_vods[self._batch_idx]
        if self._download_had_errors:
            self._log(f"[WARN] {vod.title} finished with errors")
            if hasattr(self, "_batch_failed_count"):
                self._batch_failed_count += 1
        else:
            self._log(f"[DONE] {vod.title}")
            self._save_metadata(
                self._active_output_dir,
                self._active_quality_name or "batch",
                history_url=self._active_history_url or vod.source,
                info=self._active_stream_info or self.stream_info,
            )
        self._batch_idx += 1
        self._batch_next()

    def _batch_done(self):
        self._batch_active = False
        self._batch_fetch_worker = None
        failed = getattr(self, "_batch_failed_count", 0)
        done = self._batch_total - failed
        self._log(f"\n{'=' * 50}")
        if failed:
            self._log(
                f"Batch finished with {failed} failed VOD(s). Completed {done} of {self._batch_total}."
            )
        else:
            self._log(f"Batch complete! {self._batch_total} VOD(s) downloaded.")
        self._log(f"{'=' * 50}")
        if failed:
            self._set_status(
                f"Batch finished with {failed} failed VOD(s). Completed {done} of {self._batch_total}.",
                "warning",
            )
        else:
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
            return False
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
            return False

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
            return False

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
        self._download_had_errors = False
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

        self._set_download_context(
            out_dir=out_dir,
            quality_name=self.quality_combo.currentText(),
            history_url=self._resolve_history_url(),
            info=self.stream_info,
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
        return True

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
        self._download_had_errors = True
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
        active_info = self._active_stream_info or self.stream_info
        out_dir = self._active_output_dir or self.output_input.text().strip()
        q_name = self._active_quality_name or (
            self.quality_combo.currentText() if self.quality_combo.count() else ""
        )
        title = active_info.title if active_info and active_info.title else "Download"

        self._log(f"\n{'=' * 50}")
        if self._download_had_errors:
            self._log("Download finished with one or more failed segments.")
            self._log(f"{'=' * 50}")
            self._set_status(
                "Download finished with one or more failed segments. Review the log before retrying.",
                "warning",
            )
            if self._queue_active_item is not None:
                note = f"{self._completed_segments}/{self._total_segments} segments completed"
                self._release_queue_item("failed", note)
        else:
            self._log("All downloads complete!")
            self._log(f"{'=' * 50}")
            if active_info and (active_info.is_live or active_info.total_secs <= 0):
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
            self._save_metadata(
                out_dir,
                q_name,
                history_url=self._active_history_url,
                info=active_info,
            )
            if self._queue_active_item is not None:
                self._release_queue_item("done")
        self._persist_config()
        self._start_next_background_job()

    def _on_stop(self):
        worker = self.download_worker
        resume_background_jobs = bool(
            self._queue_active_item is not None
            or self._active_auto_record_channel
            or self._pending_auto_records
        )
        live_capture = bool(
            worker and any(len(seg) >= 4 and seg[3] <= 0 for seg in getattr(worker, "segments", []))
        )
        # Halt any in-progress batch by marking it done
        if hasattr(self, '_batch_vods') and hasattr(self, '_batch_total'):
            self._batch_active = False
            self._batch_idx = self._batch_total
            self._cancel_batch_fetch_worker()
        if self.download_worker is not None:
            try:
                self.download_worker.cancel()
                if not self.download_worker.wait(5000):
                    self.download_worker.terminate()
                    self.download_worker.wait(1000)
            except Exception:
                pass
            self.download_worker = None
        # Clear any green/red chunk overrides left on segment bars so the
        # next download starts from a neutral style instead of inheriting
        # the previous run's success/fail colors.
        for pbar in getattr(self, "_segment_progress", []):
            try:
                pbar.setStyleSheet("")
                pbar.setValue(0)
            except Exception:
                pass
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.overall_progress.setVisible(False)
        if hasattr(self, 'vod_dl_all_btn'):
            self.vod_dl_all_btn.setEnabled(True)
            self.vod_load_btn.setEnabled(True)
        for entry in self.monitor.entries:
            entry.is_recording = False
        self._active_auto_record_channel = ""
        self._refresh_monitor_summary()
        self._log("[CANCELLED] Download stopped by user.")
        if self._queue_active_item is not None:
            self._release_queue_item("cancelled", "Stopped by user")
        if live_capture:
            has_media = self._output_contains_media(self._active_output_dir)
            self.open_folder_btn.setVisible(has_media)
            if has_media and self._active_output_dir and self._active_stream_info:
                self._save_metadata(
                    self._active_output_dir,
                    self._active_quality_name,
                    history_url=self._active_history_url,
                    info=self._active_stream_info,
                )
                self._set_status("Recording stopped. Any captured portion was kept on disk.", "warning")
            else:
                self._set_status("Recording stopped before any media was saved.", "warning")
        else:
            self._set_status("Download cancelled. You can adjust the selection and try again.", "warning")
        if resume_background_jobs:
            self._start_next_background_job()

    def _on_open_folder(self):
        out_dir = self._active_output_dir or self.output_input.text().strip()
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
            ext = Extractor.detect(url)
            channel_id = ext.extract_channel_id(url) if ext else url
            self.monitor_url_input.clear()
            self._log(
                f"[MONITOR] Added: {url} (every {interval}s, "
                f"auto-record: {auto}, subscribe: {subscribe})"
            )
            # Seed the archive with current VODs so we don't download the backlog
            if subscribe and ext and ext.supports_vod_listing():
                self._log(f"[SUBSCRIBE] Seeding archive for {channel_id} in the background...")
                self._start_monitor_seed_worker(url, channel_id)
            self._persist_config()
            if subscribe and ext and ext.supports_vod_listing():
                self._set_status("Channel added. Existing VODs are being seeded in the background.", "success")
            else:
                self._set_status("Channel added to the watch list.", "success")
        else:
            self._log(f"[MONITOR] Cannot add: unsupported or duplicate")
            self._set_status("Channel could not be added. It may already exist or be unsupported.", "error")

    def _on_monitor_seed_done(self, channel_id, sources):
        self._clear_monitor_seed_worker(channel_id)
        if not any(e.channel_id == channel_id for e in self.monitor.entries):
            return
        self.monitor.seed_archive(channel_id, sources)
        self._persist_config()
        self._log(
            f"[SUBSCRIBE] Seeded archive with {len(sources)} existing VOD(s). "
            f"Only new VODs will be queued from now on."
        )

    def _on_monitor_seed_error(self, channel_id, err):
        self._clear_monitor_seed_worker(channel_id)
        if any(e.channel_id == channel_id for e in self.monitor.entries):
            self._log(f"[SUBSCRIBE] Seed failed for {channel_id}: {err}")

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

            rm_btn = QPushButton("Stop + Remove" if e.is_recording else "Remove")
            rm_btn.setObjectName("ghost")
            rm_btn.setFixedHeight(28)
            if e.is_recording:
                rm_btn.setToolTip("Stops the active auto-recording first, then removes this channel.")
            rm_btn.clicked.connect(lambda checked, idx=i: self._on_monitor_remove(idx))
            self.monitor_table.setCellWidget(i, 5, rm_btn)
        self._refresh_monitor_summary()

    def _on_monitor_remove(self, idx):
        channel_id = None
        is_recording = False
        if 0 <= idx < len(self.monitor.entries):
            channel_id = self.monitor.entries[idx].channel_id
            is_recording = bool(self.monitor.entries[idx].is_recording)
        if channel_id:
            self._pending_auto_records = [cid for cid in self._pending_auto_records if cid != channel_id]
            seed_worker = self._monitor_seed_workers.pop(channel_id, None)
            if seed_worker is not None and seed_worker.isRunning():
                try:
                    seed_worker.requestInterruption()
                    seed_worker.wait(500)
                except Exception:
                    pass
            resolve_worker = getattr(self, "_auto_record_resolve_worker", None)
            if resolve_worker is not None and resolve_worker.isRunning():
                try:
                    if getattr(resolve_worker, "channel_id", "") == channel_id:
                        resolve_worker.requestInterruption()
                        resolve_worker.wait(500)
                        self._auto_record_resolve_worker = None
                except Exception:
                    pass
            if (
                is_recording
                and self._active_auto_record_channel == channel_id
                and self.download_worker is not None
                and self.download_worker.isRunning()
            ):
                self._log(f"[AUTO-RECORD] Stopping active recording before removing {channel_id}")
                self._on_stop()
        self.monitor.remove_channel(idx)
        self._persist_config()
        self._set_status("Channel removed from the watch list.", "success")

    def _queue_auto_record_retry(self, channel_id):
        if channel_id not in self._pending_auto_records:
            self._pending_auto_records.append(channel_id)
        self._refresh_monitor_summary()

    def _drain_pending_auto_records(self):
        resolver = getattr(self, "_auto_record_resolve_worker", None)
        if resolver is not None and resolver.isRunning():
            return True
        worker = getattr(self, "download_worker", None)
        if worker is not None and worker.isRunning():
            return False
        while self._pending_auto_records:
            channel_id = self._pending_auto_records.pop(0)
            if self._try_start_auto_record(channel_id):
                self._refresh_monitor_summary()
                return True
        self._refresh_monitor_summary()
        return False

    def _auto_record_error(self, channel_id, err):
        self._download_had_errors = True
        self._log(f"[AUTO-RECORD] {channel_id}: {err}")

    def _try_start_auto_record(self, channel_id):
        target = None
        for e in self.monitor.entries:
            if e.channel_id == channel_id and e.auto_record and not e.is_recording:
                target = e
                break
        if target is None:
            return False

        existing = getattr(self, "download_worker", None)
        if existing is not None and existing.isRunning():
            return False
        resolver = getattr(self, "_auto_record_resolve_worker", None)
        if resolver is not None and resolver.isRunning():
            return False
        if existing is not None:
            try:
                existing.wait(500)
            except Exception:
                pass
            self.download_worker = None

        target.is_recording = True
        self._refresh_monitor_summary()
        self._log(f"[AUTO-RECORD] Preparing recording for {target.platform}/{channel_id}")
        base_out = self.output_input.text().strip() or str(_default_output_dir())
        worker = AutoRecordResolveWorker(channel_id, target.url, base_out)
        worker.log.connect(self._log)
        worker.resolved.connect(self._on_auto_record_resolved)
        worker.error.connect(self._on_auto_record_resolve_error)
        self._auto_record_resolve_worker = worker
        worker.start()
        return True

    def _on_auto_record_resolved(self, channel_id, info, q, out_dir):
        worker = getattr(self, "_auto_record_resolve_worker", None)
        if worker is not None and not worker.isRunning():
            try:
                worker.wait(200)
            except Exception:
                pass
        self._auto_record_resolve_worker = None

        target = None
        for e in self.monitor.entries:
            if e.channel_id == channel_id and e.auto_record and e.is_recording:
                target = e
                break
        if target is None:
            self._start_next_background_job()
            return

        existing = getattr(self, "download_worker", None)
        if existing is not None and existing.isRunning():
            target.is_recording = False
            self._queue_auto_record_retry(channel_id)
            self._start_next_background_job()
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            self._log(f"[AUTO-RECORD] Cannot create output folder: {e}")
            target.is_recording = False
            self._refresh_monitor_summary()
            self._start_next_background_job()
            return

        segments = [(0, "live_recording", 0, 0)]
        self._download_had_errors = False
        self._set_download_context(
            out_dir=out_dir,
            quality_name=q.name or "Live Capture",
            history_url=target.url,
            info=info,
        )
        worker = DownloadWorker(q.url, segments, out_dir, q.format_type)
        worker.audio_url = q.audio_url
        worker.parallel_connections = self._parallel_connections
        self._active_auto_record_channel = channel_id
        worker.log.connect(self._log)
        worker.error.connect(lambda _idx, err, ch=channel_id: self._auto_record_error(ch, err))
        worker.all_done.connect(lambda: self._auto_record_done(channel_id))
        self.download_worker = worker
        worker.start()
        self._set_status(f"Auto-record started for {channel_id}.", "working")

    def _on_auto_record_resolve_error(self, channel_id, err):
        worker = getattr(self, "_auto_record_resolve_worker", None)
        if worker is not None and not worker.isRunning():
            try:
                worker.wait(200)
            except Exception:
                pass
        self._auto_record_resolve_worker = None
        for e in self.monitor.entries:
            if e.channel_id == channel_id:
                e.is_recording = False
        self._refresh_monitor_summary()
        self._log(f"[AUTO-RECORD] Error: {err}")
        self._set_status(f"Auto-record could not start for {channel_id}: {err}", "warning")
        self._start_next_background_job()

    def _on_channel_live(self, channel_id):
        """Called when a monitored channel goes live."""
        self._set_status(f"{channel_id} went live.", "warning")
        if self._try_start_auto_record(channel_id):
            return
        worker = getattr(self, "download_worker", None)
        if worker is not None and worker.isRunning():
            self._queue_auto_record_retry(channel_id)
            self._log(
                f"[AUTO-RECORD] Waiting for the current download to finish before retrying {channel_id}"
            )
            self._set_status(
                f"{channel_id} is live. Auto-record will retry when the current job finishes.",
                "warning",
            )

    def _auto_record_done(self, channel_id):
        self._active_auto_record_channel = ""
        for e in self.monitor.entries:
            if e.channel_id == channel_id:
                e.is_recording = False
        # Release the download_worker reference so the next manual/auto
        # download doesn't orphan signals on the finished QThread. wait()
        # is cheap here — run() has already returned.
        dw = getattr(self, "download_worker", None)
        if dw is not None and not dw.isRunning():
            try:
                dw.wait(500)
            except Exception:
                pass
            self.download_worker = None
        self._log(f"[AUTO-RECORD] Recording ended for {channel_id}")
        self._refresh_monitor_summary()
        if self._download_had_errors:
            self._set_status(f"Auto-record for {channel_id} ended with errors. Check the log.", "warning")
        elif not self._output_contains_media(self._active_output_dir):
            self._set_status(f"Auto-record for {channel_id} finished without saving media.", "warning")
        else:
            self._save_metadata(
                self._active_output_dir,
                self._active_quality_name or "Live Capture",
                history_url=self._active_history_url,
                info=self._active_stream_info,
            )
            self._set_status(f"Auto-record finished for {channel_id}.", "success")
        self._start_next_background_job()

    # ── Download Queue ────────────────────────────────────────────────

    def _normalize_queue_item(self, item):
        if not isinstance(item, dict):
            return None
        url = str(item.get("url", "") or "").strip()
        if not url:
            return None
        title = str(item.get("title", "") or "")
        platform = str(item.get("platform", "") or "?")
        vod_source = str(item.get("vod_source", "") or "").strip()
        vod_platform = str(item.get("vod_platform", "") or "").strip()
        vod_title = str(item.get("vod_title", "") or "").strip()
        vod_channel = str(item.get("vod_channel", "") or "").strip()
        if not vod_source and url.isdigit() and platform.lower() == "twitch":
            # Older queue entries stored Twitch VOD IDs as plain URLs, which
            # breaks auto-start because extractor detection expects an actual URL.
            vod_source = url
        if vod_source and not vod_platform:
            vod_platform = platform
        if not vod_title:
            vod_title = title
        normalized = {
            "url": url,
            "title": title or vod_title or url,
            "platform": platform,
            "status": str(item.get("status", "queued") or "queued"),
            "added": str(item.get("added", "") or ""),
            "note": str(item.get("note", "") or ""),
            "start_at": str(item.get("start_at", "") or ""),
            "vod_source": vod_source,
            "vod_platform": vod_platform,
            "vod_title": vod_title,
            "vod_channel": vod_channel,
        }
        vod_date = str(item.get("vod_date", "") or "")
        if vod_date:
            normalized["vod_date"] = vod_date
        return normalized

    def _queue_add(
        self,
        url,
        title="",
        platform="",
        note="",
        start_at="",
        vod_source="",
        vod_platform="",
        vod_title="",
        vod_channel="",
    ):
        """Append a URL to the persistent download queue.
        If start_at (ISO timestamp) is set, the item will only be picked
        up by _advance_queue after that time."""
        if not url:
            return False
        item_key = str(vod_source or url)
        if any(
            (q.get("vod_source") or q.get("url")) == item_key
            and q.get("status") not in ("failed", "cancelled")
            for q in self._download_queue
        ):
            return False
        self._download_queue.append(self._normalize_queue_item({
            "url": url,
            "title": title or vod_title or url,
            "platform": platform or "?",
            "status": "queued",
            "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "note": note,
            "start_at": start_at,
            "vod_source": vod_source,
            "vod_platform": vod_platform,
            "vod_title": vod_title or title,
            "vod_channel": vod_channel,
        }))
        self._persist_config()
        if hasattr(self, "queue_table"):
            self._refresh_queue_table()
        return True

    def _queue_remove(self, idx):
        if 0 <= idx < len(self._download_queue):
            item = self._download_queue[idx]
            if item is self._queue_active_item or item.get("status") in ("fetching", "downloading"):
                self._set_status("The active queue job cannot be removed while it is running.", "warning")
                return None
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
        resolve_worker = getattr(self, "_auto_record_resolve_worker", None)
        if resolve_worker is not None and resolve_worker.isRunning():
            return
        fetch_worker = getattr(self, "_fetch_worker", None)
        if fetch_worker is not None and fetch_worker.isRunning():
            return
        if self._queue_active_item is not None:
            return
        now = datetime.now()
        next_item = None
        for q in self._download_queue:
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
            next_item = q
            break
        if not next_item:
            return
        self._queue_active_item = next_item
        self._queue_autostart = True
        self._set_queue_item_status(next_item, "fetching")
        self._log(f"[QUEUE] Auto-starting: {next_item.get('title', '')[:60]}")
        self.url_input.setText(next_item["url"])
        self._switch_tab(0)
        self._on_fetch(
            vod_source=next_item.get("vod_source") or None,
            vod_platform=next_item.get("vod_platform") or None,
            vod_title=next_item.get("vod_title") or next_item.get("title") or None,
            vod_channel=next_item.get("vod_channel") or None,
        )

    def _refresh_queue_table(self):
        if not hasattr(self, "queue_table"):
            return
        self.queue_table.setRowCount(len(self._download_queue))
        now = datetime.now()
        for i, q in enumerate(self._download_queue):
            # Compute effective status: "scheduled" if start_at is in the future
            status = q.get("status", "queued")
            locked = q is self._queue_active_item or status in ("fetching", "downloading")
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
            elif status in ("fetching", "downloading"):
                status_item.setForeground(QColor(CAT["blue"]))
            elif status == "queued":
                status_item.setForeground(QColor(CAT["yellow"]))
            elif status in ("failed", "cancelled"):
                status_item.setForeground(QColor(CAT["red"]))
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
            if i == 0 or locked:
                up_btn.setEnabled(False)
            if i == len(self._download_queue) - 1 or locked:
                down_btn.setEnabled(False)
            if locked:
                tip = "Active jobs stay pinned until the current fetch/download finishes."
                up_btn.setToolTip(tip)
                down_btn.setToolTip(tip)
            move_lay.addWidget(up_btn)
            move_lay.addWidget(down_btn)
            self.queue_table.setCellWidget(i, 4, move_widget)
            # Remove button
            rm_btn = QPushButton("Remove")
            rm_btn.setObjectName("secondary")
            rm_btn.clicked.connect(lambda _c=False, row=i: self._queue_remove(row))
            if locked:
                rm_btn.setEnabled(False)
                rm_btn.setToolTip("Stop the current job before removing it from the queue.")
            self.queue_table.setCellWidget(i, 5, rm_btn)

    def _queue_move(self, idx, direction):
        """Move a queue item up (-1) or down (+1)."""
        if idx < 0 or idx >= len(self._download_queue):
            return
        target = idx + direction
        if target < 0 or target >= len(self._download_queue):
            return
        item = self._download_queue[idx]
        other = self._download_queue[target]
        locked_statuses = {"fetching", "downloading"}
        if (
            item is self._queue_active_item
            or other is self._queue_active_item
            or item.get("status") in locked_statuses
            or other.get("status") in locked_statuses
        ):
            self._set_status("The active queue job cannot be reordered while it is running.", "warning")
            return
        self._download_queue[idx], self._download_queue[target] = (
            self._download_queue[target], self._download_queue[idx]
        )
        self._persist_config()
        self._refresh_queue_table()

    def _on_new_vods_found(self, channel_id, vods):
        """New VODs from a subscribed channel — queue their source URLs
        so they get downloaded in the background."""
        added = 0
        for v in vods:
            # Skip if already in history (prevents re-downloading on seed)
            if self._find_duplicate("", v.title, platform=v.platform):
                continue
            if self._queue_add(
                v.source,
                title=v.title,
                platform=v.platform,
                vod_source=v.source,
                vod_platform=v.platform,
                vod_title=v.title,
                vod_channel=v.channel,
            ):
                if self._download_queue:
                    self._download_queue[-1]["vod_date"] = v.date
                added += 1
                self._log(f"[SUBSCRIBE] Queued: {v.title[:60]}")
        if added and hasattr(self, "queue_table"):
            self._refresh_queue_table()
        # Kick off the queue if nothing is downloading
        if getattr(self.download_worker, "isRunning", lambda: False)() is False:
            self._advance_queue()

    # ── History Actions ───────────────────────────────────────────────

    def _add_history(self, platform, title, quality, size, path, url="", channel=""):
        entry = HistoryEntry(
            date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            platform=platform, title=title[:60],
            channel=self._infer_history_channel(url=url, platform=platform, channel=channel),
            quality=quality, size=size, path=path, url=url,
        )
        self._history.append(entry)
        self._refresh_history_table()
        self._schedule_persist_config()

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
                or query in (self._history_channel_label(h) or "").lower()
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

    def _save_metadata(self, out_dir, quality_name="", history_url="", info=None):
        info = info or self.stream_info
        url = history_url or self._resolve_history_url()
        info_copy = copy.deepcopy(info) if info else None
        fallback_title = ""
        if out_dir:
            fallback_title = os.path.basename(out_dir.rstrip("\\/"))
        display_title = (
            (info_copy.title if info_copy else "")
            or fallback_title
            or "Download"
        )
        file_base = _safe_filename(info_copy.title) if info_copy and info_copy.title else ""
        task = {
            "out_dir": out_dir,
            "quality_name": quality_name,
            "history_url": url,
            "info": info_copy,
            "file_base": file_base,
            "write_nfo": bool(self._write_nfo and info_copy),
            "download_chat": bool(TwitchExtractor.download_chat_enabled and info_copy),
            "postprocess_snapshot": self._postprocess_snapshot() if info_copy else {},
            "platform": (info_copy.platform if info_copy and info_copy.platform else "?"),
            "channel": self._infer_history_channel(
                url=url,
                platform=(info_copy.platform if info_copy and info_copy.platform else "?"),
                channel=(info_copy.channel if info_copy else ""),
            ),
            "title": display_title,
        }
        self._enqueue_finalize_task(task)
