"""StreamKeep main window — the full QMainWindow class, tabs, and handlers.

Phase 2 of the modularization moved this out of the root-level
StreamKeep.py. The class is still a god object (~3670 lines, 235 methods);
Phase 3 will carve it into per-tab widgets. For now the split wins us a
predictable file layout and keeps the runtime unchanged.
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QProgressBar,
    QFrame, QStackedWidget, QSystemTrayIcon,
    QMenu, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QKeySequence, QPixmap, QPainter,
    QBrush, QShortcut,
)

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
from streamkeep.notifications import NotificationCenter
from streamkeep.i18n import tr, tr_format, translate_widget_tree
from streamkeep.utils import (
    default_output_dir as _default_output_dir,
    DEFAULT_FOLDER_TEMPLATE,
    DEFAULT_FILE_TEMPLATE,
)
from streamkeep.extractors import (
    TwitchExtractor,
    YtDlpExtractor,
)
from streamkeep.postprocess import (
    PostProcessor,
    VIDEO_CONTAINERS,
    AUDIO_CONTAINERS,
    AUDIO_CODECS,
    VIDEO_EXTS,
    AUDIO_EXTS,
    available_video_codec_keys as _available_video_codec_keys,
)
from streamkeep.monitor import ChannelMonitor
from streamkeep.clipboard import ClipboardMonitor
from streamkeep import db as _db
from .main_window_jobs import MainWindowJobsMixin

# Legacy NATIVE_PROXY compatibility — some UI code below assigns this.
NATIVE_PROXY = ""


# ── Main Window ───────────────────────────────────────────────────────────

# Platform badges, tab CSS, and widget builders live in streamkeep.ui.widgets.
# Imported here under legacy underscored names so the method bodies below
# (which still use `self._make_field_block(...)` etc. in 50+ places) keep
# working until a future pass switches each call site.
from .widgets import (
    TAB_STYLE,
    configure_accessibility,
    make_metric_card,
    make_field_block,
    set_accessible,
    wrap_scroll_page,
    style_table,
    set_metric,
    update_accessible_status,
)
from .tabs.download import build_download_tab, DownloadTabMixin
from .tabs.history import build_history_tab, HistoryTabMixin
from .tabs.monitor import build_monitor_tab, MonitorTabMixin
from .tabs.settings import build_settings_tab, SettingsTabMixin
from .tabs.storage import (
    build_storage_tab, StorageTabMixin,
)


class StreamKeep(
    MainWindowJobsMixin, HistoryTabMixin, MonitorTabMixin, SettingsTabMixin,
    DownloadTabMixin, StorageTabMixin, QMainWindow,
):
    def __init__(self, *, startup_check=False):
        super().__init__()
        self._startup_check = bool(startup_check)
        self.setWindowTitle(f"StreamKeep v{VERSION}")
        self.setMinimumSize(1020, 800)
        self.resize(1120, 900)
        self.setAcceptDrops(True)  # Enable drag-and-drop URL support
        self.stream_info = None
        self.download_worker = None
        self._vod_list = []
        self._vod_checks = []
        self._vod_next_cursor = None
        self._vod_source_url = ""
        self._vod_page_worker = None
        self._segment_checks = []
        self._segment_progress = []
        self._last_auto_output = ""
        self._history = []
        self._config = _load_config()
        try:
            from streamkeep.i18n import install_translator
            install_translator(self._config.get("language", "en"))
        except Exception:
            pass
        self._tray_icon = None
        self._folder_template = DEFAULT_FOLDER_TEMPLATE
        self._file_template = DEFAULT_FILE_TEMPLATE
        self._webhook_url = ""
        self._check_duplicates = True
        self._download_queue = []  # list of dicts: url, title, platform, status
        self._write_nfo = False
        self._dl_overrides = {}  # Per-download settings overrides (F18)
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
        # Concurrent queue workers (F1). Each active queue item gets its own
        # fetch + download pipeline, keyed by id(item).
        self._queue_workers = {}          # id(item) -> DownloadWorker
        self._queue_fetch_workers = {}    # id(item) -> FetchWorker
        self._queue_contexts = {}         # id(item) -> dict
        self._max_concurrent_downloads = 3
        self._pending_auto_records = []
        self._batch_active = False
        self._monitor_seed_workers = {}
        # Legacy singletons retained for back-compat with the closeEvent
        # teardown path; the new parallel auto-record dicts below are the
        # source of truth.
        self._auto_record_resolve_worker = None
        self._active_auto_record_channel = ""
        # Parallel auto-record pool (v4.15.0). Foreground DownloadWorker
        # remains the singular `self.download_worker`; auto-records live
        # here keyed by channel_id so multiple lives can be captured at
        # the same time without blocking each other on the foreground.
        self._autorecord_resolvers = {}     # channel_id -> AutoRecordResolveWorker
        self._autorecord_workers = {}       # channel_id -> DownloadWorker
        self._autorecord_contexts = {}      # channel_id -> dict (out_dir, info, q_name, history_url)
        self._chat_workers = {}              # channel_id -> ChatWorker (live capture)
        self._companion_server = None        # LocalCompanionServer instance
        self._companion_last_error = ""
        self._notifications = NotificationCenter(capacity=50)
        self._parallel_autorecords = 2      # cap; overridden from config below
        self._chunk_long_captures = False
        self._chunk_length_secs = 7200      # 2 hours default
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
        # The monitor repository is read before _apply_config() performs its
        # broader migration pass.  Initialize the schema first so a genuinely
        # empty or legacy-only config root can start safely.
        _db.init_db()
        self.monitor = ChannelMonitor()
        self.monitor.status_changed.connect(self._refresh_monitor_table)
        self.monitor.channel_went_live.connect(self._on_channel_live)
        self.monitor.new_vods_found.connect(self._on_new_vods_found)
        self.monitor.log.connect(self._log)
        # Load monitor channels from DB first; fall back to config for
        # pre-migration installs (F41).
        self.monitor.load_from_db()
        if not self.monitor.entries:
            self.monitor.load_from_config(self._config)
        self.clipboard_monitor = ClipboardMonitor()
        self.clipboard_monitor.url_detected.connect(self._on_clipboard_url)
        self._init_ui()
        translate_widget_tree(self)
        # History is restored by _apply_config(), which immediately refreshes
        # the table and may queue thumbnails for existing recordings.  Create
        # every loader before applying persisted state so non-empty libraries
        # are safe on startup.
        from .thumb_loader import ThumbLoader, PreviewLoader
        self._history_thumb_loader = ThumbLoader(self, max_concurrent=2, size=(160, 90))
        self._history_thumb_loader.thumb_ready.connect(self._on_history_thumb_ready)
        self._storage_thumb_loader = ThumbLoader(self, max_concurrent=2, size=(160, 90))
        self._storage_thumb_loader.thumb_ready.connect(self._on_storage_thumb_ready)
        self._preview_loader = PreviewLoader(self)
        self._preview_loader.frame_ready.connect(self._on_preview_frame)
        self._config_save_timer = QTimer(self)
        self._config_save_timer.setSingleShot(True)
        self._config_save_timer.timeout.connect(self._persist_config)
        self._apply_config()
        self._refresh_companion_ui()
        self._init_tray_icon()
        # Scheduler tick: checks scheduled queue items and bandwidth rules every 30s
        self._scheduler_timer = QTimer(self)
        self._scheduler_timer.timeout.connect(self._scheduler_tick)
        if not self._startup_check:
            self._scheduler_timer.start(30_000)
            self._scheduler_tick()  # Apply bandwidth rule immediately on startup
        # Hook history-table context menu (right-click → Trim / Open / Remove).
        try:
            self.history_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.history_table.customContextMenuRequested.connect(self._on_history_context_menu)
        except Exception:
            pass
        try:
            if hasattr(self, "queue_table"):
                self.queue_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                self.queue_table.customContextMenuRequested.connect(self._on_queue_context_menu)
        except Exception:
            pass
        try:
            if hasattr(self, "storage_table"):
                self.storage_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                self.storage_table.customContextMenuRequested.connect(self._on_storage_context_menu)
        except Exception:
            pass
        try:
            if hasattr(self, "monitor_table"):
                self.monitor_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
                self.monitor_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
                self.monitor_table.setDragEnabled(True)
                self.monitor_table.setAcceptDrops(True)
                self.monitor_table.setDropIndicatorShown(True)
                self.monitor_table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
                self.monitor_table.setDefaultDropAction(Qt.DropAction.MoveAction)
                self.monitor_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                self.monitor_table.customContextMenuRequested.connect(self._on_monitor_context_menu)
                self.monitor_table.model().rowsMoved.connect(self._on_monitor_rows_moved)
        except Exception:
            pass
        self._resume_candidates = []
        # Deferred startup scan for orphan resume sidecars — run on the
        # Qt event loop so the main window paints before we hit disk I/O.
        if not self._startup_check:
            QTimer.singleShot(800, self._scan_for_resumable_downloads)
        # Auto-update check — opt-in, deferred so the UI paints first and
        # so the release-API call doesn't block startup.
        self._update_check_worker = None
        self._update_download_worker = None
        self._latest_update_payload = None
        if not self._startup_check:
            QTimer.singleShot(2500, self._maybe_check_for_updates)
        # Start the browser-companion local server if the user opted in.
        # Deferred so the UI paints before we open a socket.
        if not self._startup_check:
            QTimer.singleShot(1000, self._maybe_start_companion_server)
        from streamkeep.config import install_gui_logging
        self._gui_log_handler = install_gui_logging(self._log)
        # Keyboard shortcuts (F11)
        self._setup_shortcuts()

    # ── Keyboard Shortcuts ────────────────────────────────────────────

    def _setup_shortcuts(self):
        """Register global keyboard shortcuts for power-user operation."""
        # Tab switching: Ctrl+1..6. Keyboard switches move focus into the
        # selected workflow; pointer-driven switches preserve pointer focus.
        for i in range(self._stack.count()):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(
                lambda idx=i: self._switch_tab(idx, focus_page=True)
            )
        # Fetch / download: Enter triggers fetch when URL input has focus,
        # otherwise starts download if the button is enabled.
        sc_enter = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        sc_enter.activated.connect(self._shortcut_enter)
        # Stop: Escape
        sc_esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        sc_esc.activated.connect(self._on_stop)
        # Focus search on History: Ctrl+F
        sc_find = QShortcut(QKeySequence("Ctrl+F"), self)
        sc_find.activated.connect(self._shortcut_focus_search)
        sc_url = QShortcut(QKeySequence("Ctrl+L"), self)
        sc_url.activated.connect(self._shortcut_focus_url)
        # Delete selected items (tab-aware): Delete key
        sc_del = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        sc_del.activated.connect(self._shortcut_delete_selected)
        # Select all rows in the active table: Ctrl+A
        sc_sel = QShortcut(QKeySequence("Ctrl+A"), self)
        sc_sel.activated.connect(self._shortcut_select_all)
        # Update tooltips to show shortcut hints
        self._annotate_shortcut_tooltips()

    def _annotate_shortcut_tooltips(self):
        for i, btn in enumerate(self._tab_btns):
            existing = btn.toolTip() or btn.text()
            btn.setToolTip(f"{existing}  (Ctrl+{i + 1})")
        if hasattr(self, "fetch_btn"):
            self.fetch_btn.setToolTip("Fetch stream info  (Enter)")
        if hasattr(self, "download_btn"):
            self.download_btn.setToolTip("Start download  (Enter)")
        if hasattr(self, "stop_btn"):
            self.stop_btn.setToolTip("Stop  (Esc)")
        if hasattr(self, "history_search"):
            self.history_search.setToolTip("Search history  (Ctrl+F)")
        if hasattr(self, "url_input"):
            self.url_input.setToolTip("Source URL  (Ctrl+L)")

    def _shortcut_enter(self):
        """Enter key: fetch if URL input focused/non-empty, else start download."""
        if hasattr(self, "url_input") and self.url_input.hasFocus():
            self._on_fetch()
            return
        tab = self._stack.currentIndex()
        if tab == 0:
            if hasattr(self, "url_input") and self.url_input.text().strip():
                if hasattr(self, "download_btn") and self.download_btn.isEnabled():
                    self._on_download()
                elif hasattr(self, "fetch_btn") and self.fetch_btn.isEnabled():
                    self._on_fetch()

    def _shortcut_focus_search(self):
        """Ctrl+F: switch to History tab and focus the search box."""
        self._switch_tab(2)  # History is tab index 2
        if hasattr(self, "history_search"):
            self.history_search.setFocus()
            self.history_search.selectAll()

    def _shortcut_focus_url(self):
        """Ctrl+L: move directly to the primary URL workflow."""
        self._switch_tab(0)
        self.url_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.url_input.selectAll()

    def _shortcut_delete_selected(self):
        """Delete key: remove selected items from the active tab's table."""
        tab = self._stack.currentIndex()
        if tab == 1 and hasattr(self, "monitor_table"):
            # Monitor tab: remove selected channels
            sel = sorted(
                {idx.row() for idx in self.monitor_table.selectionModel().selectedRows()},
                reverse=True,
            )
            for row in sel:
                if 0 <= row < len(self.monitor.entries):
                    self._on_monitor_remove(row)
        elif tab == 2 and hasattr(self, "history_table"):
            # History tab: remove selected history entries
            sel = sorted(
                {idx.row() for idx in self.history_table.selectionModel().selectedRows()},
                reverse=True,
            )
            db_ids_to_delete = []
            for row in sel:
                h = self.history_model.entry_at(row)
                if h is not None and getattr(h, "db_id", 0):
                    db_ids_to_delete.append(h.db_id)
            if db_ids_to_delete:
                _db.delete_history_entries(db_ids_to_delete)
            if sel:
                self._refresh_history_table()
                self._persist_config()
        elif tab == 0 and hasattr(self, "queue_table"):
            # Download tab queue: remove selected queue items
            sel = sorted(
                {idx.row() for idx in self.queue_table.selectionModel().selectedRows()},
                reverse=True,
            )
            for row in sel:
                if 0 <= row < len(self._download_queue):
                    item = self._download_queue[row]
                    if item.get("status") != "downloading":
                        self._download_queue.pop(row)
            if sel:
                self._refresh_queue_table()
                self._persist_config()

    def _shortcut_select_all(self):
        """Ctrl+A: select all rows in the current tab's table."""
        tab = self._stack.currentIndex()
        table = None
        if tab == 0 and hasattr(self, "queue_table"):
            table = self.queue_table
        elif tab == 1 and hasattr(self, "monitor_table"):
            table = self.monitor_table
        elif tab == 2 and hasattr(self, "history_table"):
            table = self.history_table
        elif tab == 3 and hasattr(self, "storage_table"):
            table = self.storage_table
        if table:
            table.selectAll()

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
        # F51 speed schedule override
        try:
            from streamkeep.scheduler import get_active_limit
            sched_limit = get_active_limit()
            if sched_limit and sched_limit != YtDlpExtractor.rate_limit:
                YtDlpExtractor.rate_limit = sched_limit
        except Exception:
            pass

    # ── Drag and Drop ─────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        _media_exts = VIDEO_EXTS | AUDIO_EXTS
        # ── Collect URLs and local file paths from the drop ──────────
        http_url = ""
        local_file = ""
        if mime.hasUrls():
            for u in mime.urls():
                if u.isLocalFile():
                    path = u.toLocalFile()
                    if Path(path).suffix.lower() in _media_exts:
                        local_file = path
                        break
                else:
                    s = u.toString()
                    if s.startswith("http"):
                        http_url = s
                        break
        if not http_url and not local_file and mime.hasText():
            text = mime.text().strip().splitlines()[0].strip() if mime.text().strip() else ""
            if text.startswith("http") and len(text) <= 2048:
                http_url = text
            elif os.path.isfile(text) and Path(text).suffix.lower() in _media_exts:
                local_file = text
        # ── Route: local media file → Clip / Trim dialog ────────────
        if local_file:
            self._log(f"[DRAG] Opened local file: {local_file}")
            from .clip_dialog import ClipDialog
            dlg = ClipDialog(self, local_file)
            dlg.exec()
            event.acceptProposedAction()
            return
        # ── Route: HTTP(S) URL → fetch pipeline ─────────────────────
        if http_url:
            if "\n" in http_url or "\r" in http_url or len(http_url) > 2048:
                self._set_status("Dropped content is not a valid URL.", "warning")
                event.ignore()
                return
            self._log(f"[DRAG] Loaded URL: {http_url[:120]}")
            self.url_input.setText(http_url)
            self._switch_tab(0)
            self._on_fetch()
            event.acceptProposedAction()
            return
        # ── Reject everything else ──────────────────────────────────
        if mime.hasUrls():
            exts = ", ".join(sorted(e.lstrip(".") for e in _media_exts)[:8]) + " ..."
            self._set_status(
                f"Dropped file is not a recognized media type ({exts}).",
                "warning",
            )
        event.ignore()

    def _apply_config(self):
        cfg = self._config
        from streamkeep.theme import apply_visual_system
        from PyQt6.QtWidgets import QApplication
        apply_visual_system(
            cfg.get("theme", "dark"),
            cfg.get("visual_density", "cozy"),
            cfg.get("visual_accent", ""),
            app=QApplication.instance(),
        )
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
        from streamkeep.download_options import (
            normalize_ytdlp_arg_templates, validate_ytdlp_transfer_options,
        )
        try:
            transfer_options = validate_ytdlp_transfer_options(
                concurrent_fragments=cfg.get("ytdlp_concurrent_fragments", 0),
                retries=cfg.get("ytdlp_retries", ""),
                fragment_retries=cfg.get("ytdlp_fragment_retries", ""),
                retry_sleep=cfg.get("ytdlp_retry_sleep", ""),
                unavailable_fragments=cfg.get(
                    "ytdlp_unavailable_fragments", ""
                ),
                throttled_rate=cfg.get("ytdlp_throttled_rate", ""),
                live_from_start=cfg.get("ytdlp_live_from_start", False),
                wait_for_video=cfg.get("ytdlp_wait_for_video", ""),
                embed_chapters=cfg.get("ytdlp_embed_chapters"),
                embed_metadata=cfg.get("ytdlp_embed_metadata"),
                embed_thumbnail=cfg.get("ytdlp_embed_thumbnail"),
            )
        except ValueError:
            transfer_options = validate_ytdlp_transfer_options()
        for name, value in transfer_options.items():
            setattr(YtDlpExtractor, f"ytdlp_{name}", value)
        from streamkeep.download_options import (
            validate_external_downloader_options,
        )
        try:
            validate_external_downloader_options(
                downloader=cfg.get("ytdlp_external_downloader", ""),
                connections=cfg.get("ytdlp_aria2c_connections", 0),
                splits=cfg.get("ytdlp_aria2c_splits", 0),
                min_split_size=cfg.get("ytdlp_aria2c_min_split_size", ""),
            )
            YtDlpExtractor.ytdlp_external_downloader = str(
                cfg.get("ytdlp_external_downloader", "") or ""
            ).strip().lower()
            YtDlpExtractor.ytdlp_aria2c_connections = int(
                cfg.get("ytdlp_aria2c_connections", 0) or 0
            )
            YtDlpExtractor.ytdlp_aria2c_splits = int(
                cfg.get("ytdlp_aria2c_splits", 0) or 0
            )
            YtDlpExtractor.ytdlp_aria2c_min_split_size = str(
                cfg.get("ytdlp_aria2c_min_split_size", "") or ""
            ).strip()
        except (ValueError, TypeError):
            YtDlpExtractor.ytdlp_external_downloader = ""
            YtDlpExtractor.ytdlp_aria2c_connections = 0
            YtDlpExtractor.ytdlp_aria2c_splits = 0
            YtDlpExtractor.ytdlp_aria2c_min_split_size = ""
        try:
            self._config["ytdlp_arg_templates"] = normalize_ytdlp_arg_templates(
                cfg.get("ytdlp_arg_templates", {})
            )
        except ValueError:
            self._config["ytdlp_arg_templates"] = {}
        if hasattr(self, "adv_ytdlp_template_combo"):
            from streamkeep.ui.tabs.download import _populate_adv_ytdlp_templates
            _populate_adv_ytdlp_templates(self)
        # v4.15.0: parallel auto-records + chunked live captures.
        try:
            par_ar = int(cfg.get("parallel_autorecords", 2))
        except (TypeError, ValueError):
            par_ar = 2
        self._parallel_autorecords = max(1, min(4, par_ar))
        if hasattr(self, "parallel_autorecords_spin"):
            self.parallel_autorecords_spin.setValue(self._parallel_autorecords)
        # Concurrent queue downloads (v4.19.0 — F1).
        try:
            cq = int(cfg.get("max_concurrent_downloads", 3))
        except (TypeError, ValueError):
            cq = 3
        self._max_concurrent_downloads = max(1, min(8, cq))
        if hasattr(self, "concurrent_queue_spin"):
            self.concurrent_queue_spin.setValue(self._max_concurrent_downloads)
        self._chunk_long_captures = bool(cfg.get("chunk_long_captures", False))
        try:
            self._chunk_length_secs = int(cfg.get("chunk_length_secs", 7200) or 7200)
        except (TypeError, ValueError):
            self._chunk_length_secs = 7200
        if hasattr(self, "chunk_check"):
            self.chunk_check.setChecked(self._chunk_long_captures)
        if hasattr(self, "chunk_length_spin"):
            self.chunk_length_spin.setValue(self._chunk_length_secs)
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
        PostProcessor.remove_silence = bool(cfg.get("pp_remove_silence", False))
        try:
            PostProcessor.silence_noise_db = int(cfg.get("pp_silence_noise_db", -30))
        except (TypeError, ValueError):
            PostProcessor.silence_noise_db = -30
        try:
            PostProcessor.silence_min_duration = float(cfg.get("pp_silence_min_duration", 3.0))
        except (TypeError, ValueError):
            PostProcessor.silence_min_duration = 3.0
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
        PostProcessor.bilingual_subs = bool(cfg.get("pp_bilingual_subs", False))
        PostProcessor.bilingual_primary_lang = cfg.get("pp_bilingual_primary_lang") or "en"
        PostProcessor.bilingual_secondary_lang = cfg.get("pp_bilingual_secondary_lang") or ""
        PostProcessor.bilingual_format = cfg.get("pp_bilingual_format") or "srt"
        PostProcessor.lrc_export = bool(cfg.get("pp_lrc_export", False))
        PostProcessor.lrc_lang = cfg.get("pp_lrc_lang") or "en"
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
        if hasattr(self, "pp_bilingual_check"):
            self.pp_bilingual_check.setChecked(bool(PostProcessor.bilingual_subs))
            self.pp_bilingual_primary.setText(PostProcessor.bilingual_primary_lang or "en")
            self.pp_bilingual_secondary.setText(PostProcessor.bilingual_secondary_lang or "")
            _bfmt = (PostProcessor.bilingual_format or "srt").lower()
            _bfmt_idx = self.pp_bilingual_format.findText(_bfmt)
            self.pp_bilingual_format.setCurrentIndex(max(0, _bfmt_idx))
            self.pp_lrc_check.setChecked(bool(PostProcessor.lrc_export))
            self.pp_lrc_lang.setText(PostProcessor.lrc_lang or "en")
        # ── SQLite library init + migration (F41) ──
        _db.init_db()
        migrated = _db.migrate_from_config(cfg)
        if migrated:
            _save_config(cfg)
            self._log("[DB] Migrated history/monitor/queue from config.json to library.db")
        # Restore queue from DB
        queue_items = []
        for q in _db.load_queue():
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
        # History stays in SQLite and is fetched incrementally by the Qt model.
        # Do not materialize the full archive during startup.
        self._refresh_history_table()
        self._refresh_download_summary()
        self._refresh_monitor_summary()
        self._refresh_history_summary()
        # Build transcript search index in background (F27)
        if not self._startup_check:
            from ..search import index_all_async
            index_all_async(None, log_fn=self._log)

    def _persist_config(self):
        cfg = self._config
        cfg["output_dir"] = self.output_input.text().strip()
        cfg["segment_idx"] = self.segment_combo.currentIndex()
        # History is persisted to SQLite (F41) — not saved to config.json.
        # Per-entry updates (favorite, watched, bookmarks, path) are written
        # incrementally via _db.update_history_entry(); full list save is only
        # needed on clear/bulk-delete.
        cfg["folder_template"] = self._folder_template
        cfg["file_template"] = self._file_template
        cfg["webhook_url"] = self._webhook_url
        cfg["check_duplicates"] = self._check_duplicates
        # Queue is persisted to SQLite (F41)
        _db.save_queue(list(self._download_queue))
        cfg["write_nfo"] = self._write_nfo
        cfg["parallel_connections"] = self._parallel_connections
        cfg["max_concurrent_downloads"] = self._max_concurrent_downloads
        cfg["download_twitch_chat"] = TwitchExtractor.download_chat_enabled
        cfg["pp_extract_audio"] = PostProcessor.extract_audio
        cfg["pp_normalize_loudness"] = PostProcessor.normalize_loudness
        cfg["pp_reencode_h265"] = PostProcessor.reencode_h265
        cfg["pp_contact_sheet"] = PostProcessor.contact_sheet
        cfg["pp_split_by_chapter"] = PostProcessor.split_by_chapter
        cfg["pp_remove_silence"] = PostProcessor.remove_silence
        cfg["pp_silence_noise_db"] = PostProcessor.silence_noise_db
        cfg["pp_silence_min_duration"] = PostProcessor.silence_min_duration
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
        cfg["pp_bilingual_subs"] = PostProcessor.bilingual_subs
        cfg["pp_bilingual_primary_lang"] = PostProcessor.bilingual_primary_lang
        cfg["pp_bilingual_secondary_lang"] = PostProcessor.bilingual_secondary_lang
        cfg["pp_bilingual_format"] = PostProcessor.bilingual_format
        cfg["pp_lrc_export"] = PostProcessor.lrc_export
        cfg["pp_lrc_lang"] = PostProcessor.lrc_lang
        cfg["recent_urls"] = list(self._recent_urls)
        cfg["bandwidth_rule"] = dict(self._bandwidth_rule)
        self.monitor.save_to_config(cfg)
        # Persist monitor channels + queue to SQLite (F41)
        self.monitor.save_to_db()
        return _save_config(cfg)

    def _schedule_persist_config(self, delay_ms=500):
        timer = getattr(self, "_config_save_timer", None)
        if timer is None:
            self._persist_config()
            return
        timer.start(max(0, int(delay_ms)))

    def _init_tray_icon(self):
        """Create a system tray icon with badge overlay and live dropdown (F28).
        Falls back gracefully if tray isn't supported."""
        if self._startup_check:
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray_base_pix = QPixmap(32, 32)
        self._tray_base_pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self._tray_base_pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(CAT["green"])))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
        painter.end()
        self._tray_icon = QSystemTrayIcon(QIcon(self._tray_base_pix), self)
        self._tray_icon.setToolTip(f"StreamKeep v{VERSION}")
        self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.show()
        self._update_tray_badge()

    def _update_tray_badge(self):
        """Redraw the tray icon with a badge showing the count of live
        channels + active downloads. Called whenever monitor status or
        download state changes."""
        if self._tray_icon is None:
            return
        live = sum(1 for e in self.monitor.entries if e.last_status == "live")
        active_dl = 1 if (self.download_worker and self.download_worker.isRunning()) else 0
        active_dl += len([w for w in self._autorecord_workers.values() if w.isRunning()])
        count = live + active_dl
        pix = self._tray_base_pix.copy()
        if count > 0:
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # Red badge circle in the top-right corner
            painter.setBrush(QBrush(QColor(CAT["red"])))
            painter.setPen(Qt.PenStyle.NoPen)
            badge_size = 16
            painter.drawEllipse(pix.width() - badge_size, 0, badge_size, badge_size)
            # White count text
            painter.setPen(QColor("#ffffff"))
            font = QFont("Arial", 8, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(
                pix.width() - badge_size, 0, badge_size, badge_size,
                Qt.AlignmentFlag.AlignCenter, str(min(count, 9)),
            )
            painter.end()
        self._tray_icon.setIcon(QIcon(pix))
        parts = []
        if live:
            parts.append(f"{live} live")
        if active_dl:
            parts.append(f"{active_dl} downloading")
        tip = f"StreamKeep v{VERSION}"
        if parts:
            tip += " — " + ", ".join(parts)
        self._tray_icon.setToolTip(tip)

    def _present_main_window(self, tab_idx=None):
        """Reveal the main window and optionally switch to a specific tab."""
        self.showNormal()
        if tab_idx is not None:
            self._switch_tab(tab_idx)
        self.activateWindow()
        self.raise_()

    def _build_tray_context_menu(self):
        """Build the tray icon right-click dropdown with actionable sections."""
        menu = QMenu(self)
        action_map = {}
        live_entries = [e for e in self.monitor.entries if e.last_status == "live"]
        dl_lines = []
        if self.download_worker and self.download_worker.isRunning():
            done = getattr(self, "_completed_segments", 0)
            total = getattr(self, "_total_segments", 0)
            info = getattr(self, "_active_stream_info", None)
            name = info.title[:40] if info and info.title else "Download"
            pct_str = f"{done}/{total}" if total else "Working"
            dl_lines.append(f"{name} | {pct_str}")
        for ch_id, w in self._autorecord_workers.items():
            if w.isRunning():
                dl_lines.append(f"Auto-record | {ch_id}")
        recent = self._notifications.items()[:5]
        unread = getattr(self._notifications, "unread", 0)

        title_act = menu.addAction(f"StreamKeep v{VERSION}")
        title_act.setEnabled(False)
        summary_act = menu.addAction(
            f"{len(live_entries)} live | {len(dl_lines)} active | {unread} unread"
        )
        summary_act.setEnabled(False)
        menu.addSeparator()

        open_downloads = menu.addAction("Open Downloads")
        action_map[open_downloads] = ("tab", 0)
        open_monitor = menu.addAction("Open Monitor")
        action_map[open_monitor] = ("tab", 1)
        open_history = menu.addAction("Open Archive")
        action_map[open_history] = ("tab", 2)
        open_alerts = menu.addAction("Open Alert Log")
        action_map[open_alerts] = ("alerts", None)
        if self._companion_local_url():
            open_remote = menu.addAction("Open Web Remote")
            action_map[open_remote] = ("remote", None)
        menu.addSeparator()

        if live_entries:
            header = menu.addAction("Live Now")
            header.setEnabled(False)
            for e in live_entries[:8]:
                action = menu.addAction(f"{e.channel_id} | {e.platform}")
                action_map[action] = ("tab", 1)
            menu.addSeparator()

        if dl_lines:
            header = menu.addAction("Active Captures")
            header.setEnabled(False)
            for line in dl_lines[:5]:
                action = menu.addAction(line)
                action_map[action] = ("tab", 0)
            menu.addSeparator()

        if recent:
            header = menu.addAction("Recent Alerts")
            header.setEnabled(False)
            for item in recent:
                ts = item.get("time", "")
                text = item.get("text", "")[:50]
                action = menu.addAction(f"{ts} | {text}")
                action_map[action] = ("alerts", None)
            menu.addSeparator()

        show_act = menu.addAction("Show StreamKeep")
        action_map[show_act] = ("show", None)
        quit_act = menu.addAction("Quit")
        action_map[quit_act] = ("quit", None)
        return menu, action_map

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._present_main_window()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            menu, action_map = self._build_tray_context_menu()
            chosen = menu.exec(self._tray_icon.geometry().bottomLeft())
            if chosen is None:
                return
            action = action_map.get(chosen)
            if not action:
                return
            kind, value = action
            if kind == "tab":
                self._present_main_window(value)
            elif kind == "alerts":
                self._present_main_window()
                self._on_show_notification_log()
            elif kind == "remote":
                self._on_open_companion_remote()
            elif kind == "show":
                self._present_main_window()
            elif kind == "quit":
                self.close()

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
        """POST a webhook notification. Auto-detects Discord, Slack,
        Telegram, and ntfy URLs and formats the payload accordingly."""
        url = (self._webhook_url or "").strip()
        if not url:
            return
        platform = self.stream_info.platform if self.stream_info else "unknown"
        src_url = self.url_input.text().strip() if hasattr(self, "url_input") else ""

        if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
            color_map = {"complete": 5763719, "failed": 15548997, "started": 3447003}
            payload = {
                "username": "StreamKeep",
                "embeds": [{
                    "title": f"StreamKeep: {event}",
                    "description": f"**{title[:200]}**",
                    "color": color_map.get(event, 5763719),
                    "fields": [
                        {"name": "Platform", "value": platform or "\u2014", "inline": True},
                        {"name": "Source", "value": (src_url or "\u2014")[:1000], "inline": False},
                    ] + ([{"name": "Details", "value": details[:1000]}] if details else []),
                    "footer": {"text": f"StreamKeep v{VERSION}"},
                }],
            }
            self._fire_webhook_json(url, payload)

        elif "hooks.slack.com" in url:
            blocks = [
                {"type": "header", "text": {"type": "plain_text",
                                            "text": f"StreamKeep: {event}"}},
                {"type": "section", "text": {"type": "mrkdwn",
                                             "text": f"*{title[:200]}*"}},
            ]
            if details:
                blocks.append({"type": "context", "elements": [
                    {"type": "mrkdwn", "text": details[:2000]}]})
            blocks.append({"type": "context", "elements": [
                {"type": "mrkdwn",
                 "text": f"Platform: {platform} | v{VERSION}"}]})
            payload = {"text": f"StreamKeep: {event} \u2014 {title}",
                       "blocks": blocks}
            self._fire_webhook_json(url, payload)

        elif "api.telegram.org/bot" in url:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            chat_id = (qs.get("chat_id") or [""])[0]
            base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            text = f"*StreamKeep: {event}*\n{title}"
            if details:
                text += f"\n_{details}_"
            text += f"\n`{platform} | v{VERSION}`"
            payload = {"chat_id": chat_id, "text": text,
                       "parse_mode": "Markdown"}
            self._fire_webhook_json(base_url, payload)

        elif "ntfy.sh" in url or "/ntfy/" in url:
            body = f"{title}\n{details}".strip() if details else title
            try:
                from ..capabilities import resolve_tool_command
                cmd = [resolve_tool_command("curl"), "-s", "-X", "POST",
                       "-H", f"Title: StreamKeep: {event}",
                       "-H", "Priority: default",
                       "-H", f"Tags: {platform}",
                       "-d", body, "--max-time", "10", url]
                subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=_CREATE_NO_WINDOW)
            except Exception as e:
                self._log(f"[WEBHOOK] Send failed: {e}")

        else:
            payload = {"app": "StreamKeep", "version": VERSION,
                       "event": event, "title": title,
                       "platform": platform, "source": src_url,
                       "details": details}
            self._fire_webhook_json(url, payload)

    def _fire_hook(self, event, **context):
        """Fire configured event hook, if any."""
        hooks_cfg = self._config.get("hooks", {})
        if hooks_cfg:
            from ..hooks import fire_hook
            fire_hook(event, context, hooks_cfg, log_fn=self._log)

    def _media_server_import(self, out_dir, info=None):
        """Auto-import recording into media server library (F33)."""
        ms_cfg = self._config.get("media_server", {})
        if ms_cfg.get("enabled") and out_dir:
            from ..integrations.media_server import import_to_media_server
            import_to_media_server(ms_cfg, out_dir, info=info, log_fn=self._log)

    def _fire_webhook_json(self, url, payload):
        """Fire-and-forget JSON POST via curl."""
        try:
            from ..capabilities import resolve_tool_command
            cmd = [resolve_tool_command("curl"), "-s", "-X", "POST",
                   "-H", "Content-Type: application/json",
                   "-d", json.dumps(payload),
                   "--max-time", "10", url]
            subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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
        vpw = getattr(self, "_vod_page_worker", None)
        if vpw is not None and vpw.isRunning():
            try:
                vpw.requestInterruption()
                vpw.wait(1500)
            except Exception:
                pass
        bfw = getattr(self, "_batch_fetch_worker", None)
        if bfw is not None and bfw.isRunning():
            try:
                bfw.requestInterruption()
                bfw.wait(1500)
            except Exception:
                pass
        storage_worker = getattr(self, "_storage_scan_worker", None)
        if storage_worker is not None and storage_worker.isRunning():
            try:
                storage_worker.requestInterruption()
                storage_worker.wait(1500)
            except Exception:
                pass
        arw = getattr(self, "_auto_record_resolve_worker", None)
        if arw is not None and arw.isRunning():
            try:
                arw.requestInterruption()
                arw.wait(1500)
            except Exception:
                pass
        # Tear down every parallel auto-record resolver + worker.
        for key, worker in list(getattr(self, "_autorecord_resolvers", {}).items()):
            try:
                if worker is not None and worker.isRunning():
                    worker.requestInterruption()
                    worker.wait(1500)
            except Exception:
                pass
            self._autorecord_resolvers.pop(key, None)
        for key, worker in list(getattr(self, "_autorecord_workers", {}).items()):
            try:
                if worker is not None and worker.isRunning():
                    worker.cancel()
                    if not worker.wait(3000):
                        worker.terminate()
                        worker.wait(500)
            except Exception:
                pass
            self._autorecord_workers.pop(key, None)
        # Tear down concurrent queue fetch workers.
        for key, worker in list(getattr(self, "_queue_fetch_workers", {}).items()):
            try:
                if worker is not None and worker.isRunning():
                    worker.requestInterruption()
                    worker.wait(1500)
            except Exception:
                pass
            self._queue_fetch_workers.pop(key, None)
        # Tear down concurrent queue download workers.
        for key, worker in list(getattr(self, "_queue_workers", {}).items()):
            try:
                if worker is not None and worker.isRunning():
                    worker.cancel()
                    if not worker.wait(3000):
                        worker.terminate()
                        worker.wait(500)
            except Exception:
                pass
            self._queue_workers.pop(key, None)
        self._queue_contexts.clear()
        for key, worker in list(getattr(self, "_chat_workers", {}).items()):
            try:
                if worker is not None and worker.isRunning():
                    worker.cancel()
                    worker.wait(2000)
            except Exception:
                pass
            self._chat_workers.pop(key, None)
        # Tear down the browser-companion HTTP server.
        srv = getattr(self, "_companion_server", None)
        if srv is not None:
            try:
                srv.stop()
            except Exception:
                pass
            self._companion_server = None
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
        # Stop transcribe / chat-render / bundle workers (F27/F22 audit fix)
        for attr in ("_transcribe_worker", "_chat_render_worker", "_bundle_worker"):
            w = getattr(self, attr, None)
            if w is not None and w.isRunning():
                try:
                    if hasattr(w, "cancel"):
                        w.cancel()
                    if not w.wait(2000):
                        w.terminate()
                        w.wait(500)
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
            "idle": ("Ready", CAT["muted"]),
            "working": ("Working", CAT["accent"]),
            "processing": ("Finalizing", CAT["accent"]),
            "success": ("Ready", CAT["accentSoft"]),
            "warning": ("Attention", CAT["gold"]),
            "error": ("Error", CAT["red"]),
        }
        label, color = tones.get(tone, tones["idle"])
        translated_label = tr(label, context="Status")
        translated_message = tr(message, context="Status")
        self.status_pill._streamkeep_i18n_source = {"text": label}
        self.status_pill._streamkeep_i18n_last = {"text": translated_label}
        self.status_pill.setText(translated_label)
        self.status_pill.setStyleSheet(
            f"color: {color}; background: transparent; border: none; "
            "padding: 0; font-size: 13px; font-weight: 700;"
        )
        self.status_label._streamkeep_i18n_source = {
            "text": message,
            "toolTip": message,
        }
        self.status_label._streamkeep_i18n_last = {
            "text": translated_message,
            "toolTip": translated_message,
        }
        self.status_label.setText(translated_message)
        self.status_label.setToolTip(translated_message)
        set_accessible(
            self.status_pill,
            tr_format(
                "Application state: {state}",
                context="Accessibility",
                state=translated_label,
            ),
        )
        update_accessible_status(
            self.status_label,
            translated_message,
            tone=tone,
            label="Application status",
        )
        self._refresh_shell_overview()

    def _refresh_shell_overview(self):
        """Update the compact shell snapshot in the app header."""
        if not hasattr(self, "shell_snapshot_value"):
            return

        queued = len(self._download_queue)
        archived = _db.history_count()
        active_jobs = sum(
            1 for q in self._download_queue
            if q.get("status") in ("fetching", "downloading")
        )
        worker = getattr(self, "download_worker", None)
        if worker is not None and worker.isRunning():
            active_jobs += 1
        live_now = sum(
            1 for entry in getattr(self.monitor, "entries", [])
            if getattr(entry, "last_status", "") == "live"
        )

        if active_jobs:
            headline = "Capture in progress"
            detail = f"{active_jobs} active job(s) running right now."
        elif queued:
            headline = "Queue ready"
            detail = f"{queued} item(s) lined up for the next pass."
        elif live_now:
            headline = "Live channels detected"
            detail = f"{live_now} monitored channel(s) are live now."
        elif archived:
            headline = "Archive in shape"
            detail = f"{archived} saved download(s) are ready to revisit."
        else:
            headline = "Ready to capture"
            detail = "Paste a URL, monitor a channel, or scan the archive."

        self.shell_snapshot_value.setText(headline)
        self.shell_snapshot_detail.setText(detail)
        self.shell_snapshot_meta.setText(f"{active_jobs} active  •  {queued} queued")


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _refresh_download_summary, _refresh_vod_summary,

    def _init_ui(self):
        central = QWidget()
        central.setObjectName("chrome")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 8, 20, 6)
        root.setSpacing(8)

        header_card = QFrame()
        header_card.setObjectName("appHeader")
        header_lay = QVBoxLayout(header_card)
        header_lay.setContentsMargins(0, 0, 0, 0)
        header_lay.setSpacing(0)

        header_top = QHBoxLayout()
        header_top.setContentsMargins(0, 0, 0, 6)
        header_top.setSpacing(14)
        title = QLabel("StreamKeep")
        title.setObjectName("appBrand")
        title.setMinimumWidth(180)
        header_top.addWidget(title)

        self._global_search = QLineEdit()
        self._global_search.setPlaceholderText("Search downloads, URLs, channels, or podcasts…")
        self._global_search.setClearButtonEnabled(True)
        self._global_search.setObjectName("globalSearch")
        set_accessible(
            self._global_search,
            "Search StreamKeep",
            "Search downloads, URLs, monitored channels, and podcasts",
        )
        self._global_search.setMinimumHeight(36)
        self._global_search.setMaximumWidth(760)
        self._global_search_timer = QTimer(self)
        self._global_search_timer.setSingleShot(True)
        self._global_search_timer.setInterval(300)
        self._global_search_timer.timeout.connect(self._on_global_search)
        self._global_search.textChanged.connect(
            lambda: self._global_search_timer.start()
        )
        self._global_search.returnPressed.connect(self._on_global_search)
        header_top.addWidget(self._global_search, 1)

        self.notif_button = QPushButton("Alerts 0")
        self.notif_button.setObjectName("ghost")
        self.notif_button.setMinimumWidth(86)
        self.notif_button.setToolTip("Recent notifications")
        self.notif_button.clicked.connect(self._on_show_notifications)
        header_top.addWidget(self.notif_button)

        settings_quick = QPushButton("Settings")
        settings_quick.setObjectName("ghost")
        settings_quick.clicked.connect(lambda: self._switch_tab(5))
        header_top.addWidget(settings_quick)
        header_lay.addLayout(header_top)

        # These labels retain the existing overview state contract without
        # turning operational counts into a dashboard card.
        self.shell_snapshot_value = QLabel("Ready to capture", header_card)
        self.shell_snapshot_value.setVisible(False)
        self.shell_snapshot_detail = QLabel(f"Desktop build v{VERSION}", header_card)
        self.shell_snapshot_detail.setVisible(False)
        self.shell_snapshot_meta = QLabel("0 active  •  0 queued")
        self.shell_snapshot_meta.setObjectName("footerMeta")

        tab_shell = QFrame()
        tab_shell.setObjectName("appNav")
        tab_lay = QHBoxLayout(tab_shell)
        tab_lay.setContentsMargins(0, 0, 0, 0)
        tab_lay.setSpacing(26)

        self._tab_btns = []
        self._tab_names = ["Download", "Monitor", "History", "Storage", "Analytics", "Settings"]
        for i, name in enumerate(self._tab_names):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            set_accessible(
                btn,
                f"{name} tab",
                f"Switch to {name}; keyboard shortcut Ctrl+{i + 1}",
            )
            btn.setObjectName("tabActive" if i == 0 else "tab")
            btn.setStyleSheet(TAB_STYLE())
            btn.clicked.connect(lambda checked, idx=i: self._switch_tab(idx))
            tab_lay.addWidget(btn)
            self._tab_btns.append(btn)
        tab_lay.addStretch(1)
        header_lay.addWidget(tab_shell)
        root.addWidget(header_card)

        # Global search results dropdown (hidden until needed)
        from PyQt6.QtWidgets import QListWidget
        self._global_results = QListWidget(self)
        self._global_results.setObjectName("globalResults")
        self._global_results.setMaximumHeight(280)
        self._global_results.setVisible(False)
        self._global_results.itemActivated.connect(self._on_global_result_click)
        root.addWidget(self._global_results)

        self._stack = QStackedWidget()
        self._download_scroll = self._wrap_scroll_page(build_download_tab(self))
        self._stack.addWidget(self._download_scroll)
        self._stack.addWidget(self._wrap_scroll_page(build_monitor_tab(self)))
        self._stack.addWidget(self._wrap_scroll_page(build_history_tab(self)))
        self._stack.addWidget(self._wrap_scroll_page(build_storage_tab(self)))
        from .tabs.analytics import build_analytics_tab
        self._stack.addWidget(self._wrap_scroll_page(build_analytics_tab(self)))
        self._stack.addWidget(self._wrap_scroll_page(build_settings_tab(self)))
        root.addWidget(self._stack, 1)

        footer = QFrame()
        footer.setObjectName("statusBar")
        footer_lay = QHBoxLayout(footer)
        footer_lay.setContentsMargins(0, 5, 0, 0)
        footer_lay.setSpacing(10)

        self.status_pill = QLabel("Standby")
        set_accessible(self.status_pill, "Application state: Standby")
        footer_lay.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignTop)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        footer_lay.addWidget(self.status_label, 1)

        self.overall_progress = QProgressBar()
        set_accessible(
            self.overall_progress,
            "Current operation progress",
            "Progress for the active download or processing operation",
        )
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

        self.trim_btn = QPushButton("Trim...")
        self.trim_btn.setObjectName("secondary")
        self.trim_btn.setVisible(False)
        self.trim_btn.clicked.connect(self._on_trim_last)
        footer_lay.addWidget(self.trim_btn)

        footer_lay.addWidget(self.shell_snapshot_meta)
        root.addWidget(footer)

        configure_accessibility(
            self,
            owner=self,
            page_name="StreamKeep main window",
            names={
                "url_input": ("Source URL", "Paste a stream, VOD, podcast, or media URL"),
                "table": (
                    "Available stream segments",
                    "Use arrow keys to navigate and Enter to toggle download segments",
                ),
                "queue_table": ("Download queue", "Queued and active download jobs"),
                "monitor_table": ("Monitored channels", "Channels and their current live state"),
                "history_table": ("Download history", "Completed downloads; use arrow keys to navigate rows"),
                "storage_table": ("Archive storage", "Recording folders; use Space to select rows"),
            },
        )
        self._set_status("Paste a URL to begin.", "idle")
        QTimer.singleShot(0, lambda: self._download_scroll.verticalScrollBar().setValue(0))

    def _switch_tab(self, idx, *, focus_page=False):
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tab_btns):
            btn.setObjectName("tabActive" if i == idx else "tab")
            btn.setChecked(i == idx)
            btn.setAccessibleDescription(
                f"{'Current tab' if i == idx else 'Switch to ' + self._tab_names[i]}; "
                f"keyboard shortcut Ctrl+{i + 1}"
            )
            btn.setStyleSheet(TAB_STYLE())
        if focus_page:
            targets = (
                "url_input",
                "monitor_url_input",
                "history_search",
                "storage_table",
                "analytics_range",
                "theme_combo",
            )
            target = getattr(self, targets[idx], None) if idx < len(targets) else None
            if target is not None and target.isEnabled() and not target.isHidden():
                target.setFocus(Qt.FocusReason.ShortcutFocusReason)
        # Auto-scan Storage the first time the user opens the tab in a
        # session, and on every subsequent visit (cheap on small archives,
        # user can stop by switching away). Deferred so the tab paints
        # before the scan runs.
        try:
            storage_idx = self._tab_names.index("Storage")
        except ValueError:
            storage_idx = -1
        if idx == storage_idx and storage_idx >= 0:
            QTimer.singleShot(200, self._on_storage_rescan)
        # Refresh Analytics tab on visit (F63)
        try:
            analytics_idx = self._tab_names.index("Analytics")
        except ValueError:
            analytics_idx = -1
        if idx == analytics_idx and analytics_idx >= 0:
            from .tabs.analytics import _refresh_analytics
            QTimer.singleShot(100, lambda: _refresh_analytics(self))

    # ── Download Tab ──────────────────────────────────────────────────


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _on_batch_url_import, _on_expand_playlist, _on_expand_done, _on_expand_error,
    #   _on_scan_page, _on_scan_done, _on_scan_error, _on_recover_vod,
    #   _on_recover_download, _on_queue_url, _on_schedule_url, _on_clear_queue,

    # ── Monitor Tab ───────────────────────────────────────────────────

    # ── Settings Tab ──────────────────────────────────────────────────
    # Settings-tab handlers moved to SettingsTabMixin in
    # streamkeep.ui.tabs.settings:
    #   _on_convert_files_clicked, _on_convert_folder_clicked,
    #   _start_convert_worker, _on_convert_progress,
    #   _on_convert_file_done, _on_convert_all_done, _on_convert_cancel,
    #   _on_export_config, _on_import_config, _settings_browse,
    #   _scan_browsers, _scan_browsers_silent, _on_scan_browsers,
    #   _on_browse_cookies_file, _on_import_browser_cookies,
    #   _on_clear_cookies, _update_cookies_status,
    #   _on_save_account_tokens, _on_clear_account_tokens,
    #   _save_proxy_pool, _on_test_proxies, _on_save_settings

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


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _update_badge, _on_url_changed, _on_toggle_clipboard, _on_clipboard_url,
    #   _remember_url, _infer_history_channel, _history_channel_label, _title_token_overlap,
    #   _find_duplicate, _set_download_context,

    # ── Resume sidecar integration ───────────────────────────────────


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _attach_resume_to_worker,



    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _start_finalize_worker, _enqueue_finalize_task, _on_finalize_progress, _on_finalize_done,
    #   _cancel_batch_fetch_worker, _on_fetch, _on_fetch_done, _on_fetch_error,
    #   _on_vods_found, _on_vod_cb_toggled, _on_vod_select_all, _on_vod_queue_selected,
    #   _on_vod_load_single, _on_vod_load_more, _on_vod_page_ready, _on_vod_page_error,
    #   _on_vod_download_all, _batch_next, _batch_on_fetched, _batch_on_fetch_error,
    #   _batch_vod_done, _batch_done,

    # ── Segment Management ────────────────────────────────────────────


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _get_segment_secs, _parse_crop_secs, _fmt_crop_time, _is_audio_only,
    #   _content_label, _build_segments, _estimate_size_bytes, _on_select_all,
    #   _on_segment_length_changed, _on_quality_changed, _on_browse,

    # ── Download ──────────────────────────────────────────────────────


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _on_download, _on_dl_progress,

    # ── Speed & ETA Tracking (F16) ──────────────────────────────────


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _init_speed_tracking, _update_speed_from_status, _reset_speed_dashboard, _on_segment_done,
    #   _on_dl_error, _on_all_done, _on_stop, _on_open_folder,

    # ── Queue context menu: recurrence editor ────────────────────────


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _on_queue_context_menu,

    # ── Notifications Center ─────────────────────────────────────────

    def _notify_center(self, text, level="info"):
        """Push an entry onto the in-app notifications ring buffer and
        refresh the header bell's unread badge. Optionally play a sound
        when the user has enabled audio cues in Settings."""
        self._notifications.push(text, level=level)
        self._refresh_notif_badge()
        if level in ("success", "warning", "error") and bool(self._config.get("notif_sound", False)):
            try:
                from PyQt6.QtWidgets import QApplication
                QApplication.beep()
            except Exception:
                pass

    def _refresh_notif_badge(self):
        if not hasattr(self, "notif_button"):
            return
        unread = self._notifications.unread
        if unread > 0:
            self.notif_button.setText(f"Alerts {unread}")
            self.notif_button.setStyleSheet("font-weight: 700;")
        else:
            self.notif_button.setText("Alerts 0")
            self.notif_button.setStyleSheet("")

    def _on_show_notifications(self):
        """Build a transient QMenu popping down from the bell with the
        most recent events. Marks all read on display."""
        menu = QMenu(self)
        items = self._notifications.items()
        if not items:
            empty = menu.addAction("No notifications yet")
            empty.setEnabled(False)
        else:
            for n in items[:30]:
                prefix = {
                    "success": "\u2714",
                    "warning": "\u26A0",
                    "error": "\u2716",
                    "info": "\u2022",
                }.get(n.level, "\u2022")
                act = menu.addAction(f"{n.ts}  {prefix}  {n.text}")
                act.setEnabled(False)
            menu.addSeparator()
            clear_act = menu.addAction("Clear all")
            clear_act.triggered.connect(self._on_clear_notifications)
        menu.addSeparator()
        log_act = menu.addAction("View full log\u2026")
        log_act.triggered.connect(self._on_show_notification_log)
        self._notifications.mark_all_read()
        self._refresh_notif_badge()
        # Drop down from the button.
        pos = self.notif_button.mapToGlobal(self.notif_button.rect().bottomLeft())
        menu.exec(pos)

    def _on_clear_notifications(self):
        self._notifications.clear()
        self._refresh_notif_badge()

    def _on_show_notification_log(self):
        from .notification_log_dialog import NotificationLogDialog
        dlg = NotificationLogDialog(self, self._notifications)
        dlg.exec()

    # _on_lifecycle_preview, _run_lifecycle_cleanup
    #   -> streamkeep.ui.tabs.settings.SettingsTabMixin

    # _remove_history_for_paths → streamkeep.ui.tabs.history.HistoryTabMixin


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _choose_default_quality_index,

    # ── Browser companion local server ───────────────────────────────
    # _maybe_start_companion_server, _api_state_snapshot
    #   -> streamkeep.ui.tabs.settings.SettingsTabMixin


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _on_companion_url,

    # ── Auto-update checker ──────────────────────────────────────────
    # _maybe_check_for_updates, _on_update_check_result,
    # _on_update_install, _on_update_download_progress,
    # _on_update_download_done, _on_update_dismiss
    #   -> streamkeep.ui.tabs.settings.SettingsTabMixin


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _preflight_disk_space, _on_trim_last,

    # Storage tab handlers → streamkeep.ui.tabs.storage.StorageTabMixin

    # Download queue handlers → streamkeep.ui.tabs.download.DownloadTabMixin

    # Monitor tab handlers → streamkeep.ui.tabs.monitor.MonitorTabMixin

    # History tab handlers → streamkeep.ui.tabs.history.HistoryTabMixin


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _save_metadata,

    # ── Global Search (F45) ─────────────────────────────────────────

    def _on_global_search(self):
        """Run a unified search across History, Monitor, Queue, and transcripts."""
        query = self._global_search.text().strip().lower()
        results_widget = self._global_results
        results_widget.clear()

        if not query or len(query) < 2:
            results_widget.setVisible(False)
            return

        from PyQt6.QtWidgets import QListWidgetItem
        items = []
        cap = 15  # max results per source

        # History search
        for row in _db.search_history(query, limit=cap):
            h = HistoryEntry.from_dict(row)
            item = QListWidgetItem(
                f"[History] {h.platform}: {h.channel} - {h.title[:50]}"
            )
            item.setData(Qt.ItemDataRole.UserRole, ("history", h))
            items.append(item)

        # Monitor search
        count = 0
        for e in self.monitor.entries:
            if count >= cap:
                break
            if (query in (e.channel_id or "").lower()
                    or query in (e.url or "").lower()
                    or query in (e.platform or "").lower()):
                item = QListWidgetItem(
                    f"[Monitor] {e.platform}: {e.channel_id} ({e.url})"
                )
                item.setData(Qt.ItemDataRole.UserRole, ("monitor", e))
                items.append(item)
                count += 1

        # Queue search
        count = 0
        for q in self._download_queue:
            if count >= cap:
                break
            if (query in (q.get("title", "") or "").lower()
                    or query in (q.get("url", "") or "").lower()):
                item = QListWidgetItem(
                    f"[Queue] {q.get('title', q.get('url', ''))[:60]}"
                )
                item.setData(Qt.ItemDataRole.UserRole, ("queue", q))
                items.append(item)
                count += 1

        # Transcript FTS search (F27)
        try:
            from ..search import search_transcripts
            hits = search_transcripts(query, limit=cap)
            for hit in hits:
                snippet = (hit.get("text", "") or "")[:80]
                item = QListWidgetItem(
                    f"[Transcript] {snippet}... ({hit.get('recording_path', '')[-40:]})"
                )
                item.setData(Qt.ItemDataRole.UserRole, ("transcript", hit))
                items.append(item)
        except Exception:
            pass

        if items:
            for it in items:
                results_widget.addItem(it)
            results_widget.setVisible(True)
        else:
            no_result = QListWidgetItem("No results found.")
            no_result.setData(Qt.ItemDataRole.UserRole, None)
            results_widget.addItem(no_result)
            results_widget.setVisible(True)

    def _on_global_result_click(self, item):
        """Navigate to the source tab when a global search result is clicked."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        source, entry = data
        self._global_results.setVisible(False)

        if source == "history":
            self._switch_tab(2)
            # Try to select the row in history
            if hasattr(self, "history_search"):
                self.history_search.setText(
                    getattr(entry, "title", "")[:30]
                )
        elif source == "monitor":
            self._switch_tab(1)
        elif source == "queue":
            self._switch_tab(0)
        elif source == "transcript":
            self._switch_tab(2)
            if hasattr(self, "history_search") and hasattr(self, "transcript_search_check"):
                self.transcript_search_check.setChecked(True)
                self.history_search.setText(
                    self._global_search.text().strip()[:30]
                )
