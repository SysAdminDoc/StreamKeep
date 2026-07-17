"""Download tab — URL input, VOD picker, segments table, queue, log."""

from PyQt6.QtCore import Qt, QStringListModel
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMenu, QPushButton, QSpinBox, QSplitter, QTableWidget, QTextEdit,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...utils import default_output_dir as _default_output_dir
from ..widgets import make_field_block, path_label, style_table
from .download_queue import DownloadQueueMixin
from .download_vod import DownloadVodMixin
from .download_finalize import DownloadFinalizeMixin
from .download_controls import (
    _populate_adv_pp,
    _populate_adv_subtitles,
    _populate_adv_ytdlp_templates,
    _refresh_adv_sponsorblock_controls,
    _refresh_adv_subtitle_controls,
    _populate_track_table,
    _reset_adv_overrides,
    get_adv_overrides,
)
from .download_single import DownloadSingleMixin

__all__ = [
    "DownloadTabMixin",
    "build_download_tab",
    "_populate_adv_subtitles",
    "_populate_track_table",
    "_refresh_adv_subtitle_controls",
    "get_adv_overrides",
]




def build_download_tab(win):
    """Build the Download tab page. Stashes widget refs on `win.*`."""
    page = QWidget()
    root = QVBoxLayout(page)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(6)

    hero = QFrame()
    hero.setObjectName("pageHeader")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(2, 2, 2, 4)
    hero_lay.setSpacing(2)

    win.download_hero_title = QLabel("New download")
    win.download_hero_title.setObjectName("heroTitle")
    win.download_hero_title.setWordWrap(True)
    win.download_hero_body = QLabel("Video, stream, podcast, or direct media.")
    win.download_hero_body.setObjectName("heroBody")
    win.download_hero_body.setWordWrap(True)
    hero_lay.addWidget(win.download_hero_title)
    hero_lay.addWidget(win.download_hero_body)

    # Existing workers update these labels; they remain state holders instead
    # of becoming six competing dashboard cards above the primary action.
    # Keep them under an invisible parent: a parentless QLabel becomes a
    # top-level window when set_metric() later makes its detail text visible.
    win._download_metric_state = QWidget(page)
    win._download_metric_state.setObjectName("downloadMetricState")
    win._download_metric_state.setVisible(False)
    for key, value, detail in (
        ("platform", "Auto detect", "Waiting for a URL"),
        ("duration", "Waiting", "Metadata not loaded"),
        ("selection", "Not ready", "Segments appear after fetch"),
        ("output", path_label(str(_default_output_dir())), ""),
        ("finalize", "Idle", "No background tasks"),
        ("speed", "—", ""),
        ("eta", "—", ""),
    ):
        value_label = QLabel(value, win._download_metric_state)
        detail_label = QLabel(detail, win._download_metric_state)
        value_label.setVisible(False)
        detail_label.setVisible(False)
        setattr(win, f"download_{key}_value", value_label)
        setattr(win, f"download_{key}_sub", detail_label)
    root.addWidget(hero)

    # Update banner — shown only after a successful release-check when a
    # newer version is available. Styled like the resume banner so users
    # recognize it as a one-click actionable notice.
    win.update_banner = QFrame()
    win.update_banner.setObjectName("updateBanner")
    win.update_banner.setVisible(False)
    ub_lay = QHBoxLayout(win.update_banner)
    ub_lay.setContentsMargins(16, 12, 16, 12)
    ub_lay.setSpacing(12)
    win.update_banner_label = QLabel("A newer release is available.")
    win.update_banner_label.setWordWrap(True)
    win.update_banner_label.setObjectName("updateBannerLabel")
    ub_lay.addWidget(win.update_banner_label, 1)
    win.update_banner_install_btn = QPushButton("Download & install")
    win.update_banner_install_btn.setObjectName("primary")
    win.update_banner_install_btn.clicked.connect(win._on_update_install)
    ub_lay.addWidget(win.update_banner_install_btn)
    win.update_banner_dismiss_btn = QPushButton("Dismiss")
    win.update_banner_dismiss_btn.setObjectName("secondary")
    win.update_banner_dismiss_btn.clicked.connect(win._on_update_dismiss)
    ub_lay.addWidget(win.update_banner_dismiss_btn)
    root.addWidget(win.update_banner)

    # Resume banner — shown only when startup scan finds orphan sidecars.
    win.resume_banner = QFrame()
    win.resume_banner.setObjectName("resumeBanner")
    win.resume_banner.setVisible(False)
    rb_lay = QHBoxLayout(win.resume_banner)
    rb_lay.setContentsMargins(16, 12, 16, 12)
    rb_lay.setSpacing(12)
    win.resume_banner_label = QLabel("Interrupted download ready to resume.")
    win.resume_banner_label.setWordWrap(True)
    win.resume_banner_label.setObjectName("resumeBannerLabel")
    rb_lay.addWidget(win.resume_banner_label, 1)
    win.resume_banner_resume_btn = QPushButton("Resume")
    win.resume_banner_resume_btn.setObjectName("primary")
    win.resume_banner_resume_btn.clicked.connect(win._on_resume_all)
    rb_lay.addWidget(win.resume_banner_resume_btn)
    win.resume_banner_discard_btn = QPushButton("Discard")
    win.resume_banner_discard_btn.setObjectName("secondary")
    win.resume_banner_discard_btn.clicked.connect(win._on_resume_discard)
    rb_lay.addWidget(win.resume_banner_discard_btn)
    root.addWidget(win.resume_banner)

    # Live-chat dock — hidden until the user enables live chat capture
    # in Settings AND at least one Twitch auto-record is running.
    win.chat_dock = QFrame()
    win.chat_dock.setObjectName("card")
    win.chat_dock.setVisible(False)
    cd_lay = QVBoxLayout(win.chat_dock)
    cd_lay.setContentsMargins(16, 12, 16, 12)
    cd_lay.setSpacing(6)
    chat_hdr = QLabel("Live chat")
    chat_hdr.setObjectName("sectionTitle")
    cd_lay.addWidget(chat_hdr)
    win.chat_log_view = QTextEdit()
    win.chat_log_view.setReadOnly(True)
    win.chat_log_view.setFixedHeight(180)
    cd_lay.addWidget(win.chat_log_view)
    root.addWidget(win.chat_dock)

    url_card = QFrame()
    url_card.setObjectName("composerCard")
    url_lay = QVBoxLayout(url_card)
    url_lay.setContentsMargins(0, 4, 0, 4)
    url_lay.setSpacing(6)

    url_header = QVBoxLayout()
    url_header.setSpacing(4)
    sec1 = QLabel("Source URL")
    sec1.setObjectName("sectionTitle")
    sec1.setVisible(False)
    url_header.addWidget(sec1)
    url_lay.addLayout(url_header)

    url_row = QHBoxLayout()
    url_row.setSpacing(10)
    win.url_input = QLineEdit()
    win.url_input.setObjectName("sourceComposer")
    win.url_input.setPlaceholderText(
        "Paste a stream, channel, VOD, or direct media URL…"
    )
    win.url_input.setClearButtonEnabled(True)
    win.url_input.setMinimumHeight(38)
    win.url_input.returnPressed.connect(lambda: win._on_fetch())
    win.url_input.textChanged.connect(win._on_url_changed)
    # Recent URLs autocomplete dropdown
    win._recent_url_model = QStringListModel(win._recent_urls)
    win._recent_url_completer = QCompleter(win._recent_url_model, win)
    win._recent_url_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    win._recent_url_completer.setFilterMode(Qt.MatchFlag.MatchContains)
    win._recent_url_completer.setMaxVisibleItems(10)
    win.url_input.setCompleter(win._recent_url_completer)
    url_row.addWidget(win.url_input, 1)

    win.platform_badge = QLabel("")
    win.platform_badge.setFixedHeight(40)
    win.platform_badge.setMinimumWidth(96)
    win.platform_badge.setVisible(False)
    win.platform_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    url_row.addWidget(win.platform_badge)

    paste_btn = QPushButton("Paste")
    paste_btn.setObjectName("secondary")
    paste_btn.setToolTip("Paste a URL from the clipboard")
    paste_btn.clicked.connect(win.url_input.paste)
    url_row.addWidget(paste_btn)

    win.fetch_btn = QPushButton("Fetch")
    win.fetch_btn.setObjectName("primary")
    win.fetch_btn.setMinimumWidth(116)
    win.fetch_btn.setMinimumHeight(38)
    win.fetch_btn.clicked.connect(win._on_fetch)
    url_row.addWidget(win.fetch_btn)

    win.batch_import_btn = QPushButton("Import")
    win.batch_import_btn.setObjectName("commandGhost")
    win.batch_import_btn.setToolTip("Import URLs from a text file (one per line) and queue them all (F44)")
    win.batch_import_btn.clicked.connect(win._on_batch_url_import)

    # Keep secondary intake paths reachable without making every action
    # compete with the URL field. Hidden proxy widgets preserve the worker
    # mixins' existing enable/disable contracts.
    win.scan_btn = QPushButton("Scan page", url_card)
    win.scan_btn.setAccessibleName("Scan page for media")
    win.scan_btn.setToolTip("Fetch the URL as HTML and extract all video/media links it references")
    win.scan_btn.clicked.connect(win._on_scan_page)
    win.scan_btn.setVisible(False)

    win.scan_lan_check = QCheckBox("Allow LAN for this scan", url_card)
    win.scan_lan_check.setAccessibleName("Allow LAN for this scan")
    win.scan_lan_check.setToolTip(
        "One scan only: allow RFC1918/ULA page targets. Loopback, link-local, "
        "cloud metadata, and other special addresses remain blocked."
    )
    win.scan_lan_check.setVisible(False)

    win.queue_btn = QPushButton("Queue", url_card)
    win.queue_btn.setAccessibleName("Add URL to queue")
    win.queue_btn.setToolTip("Add the current URL to the download queue")
    win.queue_btn.clicked.connect(win._on_queue_url)
    win.queue_btn.setVisible(False)

    more_btn = QPushButton("Advanced")
    more_btn.setObjectName("commandGhost")
    win.download_advanced_btn = more_btn
    more_menu = QMenu(more_btn)
    queue_action = more_menu.addAction("Add URL to queue")
    queue_action.triggered.connect(win.queue_btn.click)
    win.scan_action = more_menu.addAction("Scan page for media")
    win.scan_action.triggered.connect(win.scan_btn.click)
    win.scan_lan_action = more_menu.addAction("Allow LAN for next scan")
    win.scan_lan_action.setCheckable(True)
    win.scan_lan_action.toggled.connect(win.scan_lan_check.setChecked)
    win.scan_lan_check.toggled.connect(win.scan_lan_action.setChecked)
    more_menu.addSeparator()
    win.expand_btn = more_menu.addAction("Expand playlist")
    win.expand_btn.setToolTip("Queue every item from a playlist or channel")
    win.expand_btn.triggered.connect(win._on_expand_playlist)
    win.recover_btn = more_menu.addAction("Recover Twitch VOD")
    win.recover_btn.triggered.connect(win._on_recover_vod)
    more_menu.addSeparator()
    win.clip_btn = more_menu.addAction("Clipboard watch")
    win.clip_btn.setCheckable(True)
    win.clip_btn.triggered.connect(win._on_toggle_clipboard)
    more_menu.addSeparator()
    win.download_settings_action = more_menu.addAction("Download settings")
    win.download_settings_action.setCheckable(True)
    win.time_range_action = more_menu.addAction("Time range")
    win.time_range_action.setCheckable(True)
    win.adv_overrides_action = more_menu.addAction("Per-download overrides")
    win.adv_overrides_action.setCheckable(True)
    more_btn.setMenu(more_menu)
    url_lay.addLayout(url_row)

    command_row = QHBoxLayout()
    command_row.setContentsMargins(2, 0, 0, 0)
    command_row.setSpacing(4)
    command_row.addWidget(win.batch_import_btn)
    command_row.addWidget(more_btn)
    command_row.addStretch(1)
    url_lay.addLayout(command_row)

    win.info_label = QLabel("")
    win.info_label.setObjectName("streamInfo")
    win.info_label.setWordWrap(True)
    win.info_label.setVisible(False)
    url_lay.addWidget(win.info_label)

    # VOD picker subwidget
    win.vod_widget = QFrame()
    win.vod_widget.setObjectName("subtleCard")
    vod_main_lay = QVBoxLayout(win.vod_widget)
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

    win.vod_summary_label = QLabel("Inspect a channel to browse available VODs.")
    win.vod_summary_label.setObjectName("tableHint")
    vod_header.addWidget(win.vod_summary_label)

    win.vod_select_all_cb = QCheckBox("Select All")
    win.vod_select_all_cb.setChecked(False)
    win.vod_select_all_cb.stateChanged.connect(win._on_vod_select_all)
    vod_header.addWidget(win.vod_select_all_cb)
    vod_main_lay.addLayout(vod_header)

    win.vod_table = QTableWidget()
    win.vod_table.setColumnCount(6)
    win.vod_table.setHorizontalHeaderLabels(["", "Platform", "Title", "Date", "Duration", "Views"])
    vh = win.vod_table.horizontalHeader()
    vh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    vh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    vh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    vh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
    vh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    vh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
    win.vod_table.setColumnWidth(0, 36)
    win.vod_table.setColumnWidth(1, 84)
    win.vod_table.setColumnWidth(3, 160)
    win.vod_table.setColumnWidth(4, 96)
    win.vod_table.setColumnWidth(5, 72)
    win.vod_table.verticalHeader().setVisible(False)
    win.vod_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.vod_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    win.vod_table.setMaximumHeight(300)
    style_table(
        win.vod_table,
        42,
        accessible_name="Available VODs",
        accessible_description="Use arrow keys to navigate and Enter to select a VOD",
    )
    win.vod_table.cellActivated.connect(
        lambda row, _column: (
            win._vod_checks[row].toggle()
            if 0 <= row < len(win._vod_checks) else None
        )
    )
    vod_main_lay.addWidget(win.vod_table)

    vod_btn_row = QHBoxLayout()
    win.vod_load_more_btn = QPushButton("Load More VODs")
    win.vod_load_more_btn.setObjectName("ghost")
    win.vod_load_more_btn.setToolTip("Fetch the next page of VODs from the platform API")
    win.vod_load_more_btn.clicked.connect(win._on_vod_load_more)
    win.vod_load_more_btn.setVisible(False)
    vod_btn_row.addWidget(win.vod_load_more_btn)
    vod_btn_row.addStretch(1)
    win.vod_load_btn = QPushButton("Load Selected")
    win.vod_load_btn.setObjectName("secondary")
    win.vod_load_btn.clicked.connect(win._on_vod_load_single)
    vod_btn_row.addWidget(win.vod_load_btn)
    win.vod_queue_btn = QPushButton("Queue Selected")
    win.vod_queue_btn.setObjectName("secondary")
    win.vod_queue_btn.setToolTip("Add checked VODs to the download queue for concurrent downloading")
    win.vod_queue_btn.clicked.connect(win._on_vod_queue_selected)
    vod_btn_row.addWidget(win.vod_queue_btn)
    win.vod_dl_all_btn = QPushButton("Download All Checked")
    win.vod_dl_all_btn.setObjectName("primary")
    win.vod_dl_all_btn.clicked.connect(win._on_vod_download_all)
    vod_btn_row.addWidget(win.vod_dl_all_btn)
    vod_main_lay.addLayout(vod_btn_row)

    win.vod_widget.setVisible(False)
    url_lay.addWidget(win.vod_widget)
    root.addWidget(url_card)

    # Controls card — quality / segment / output folder
    controls_card = QFrame()
    controls_card.setObjectName("optionsRow")
    win.download_settings_panel = controls_card
    controls_lay = QGridLayout(controls_card)
    controls_lay.setContentsMargins(4, 4, 4, 6)
    controls_lay.setHorizontalSpacing(18)
    controls_lay.setVerticalSpacing(8)

    quality_block, quality_lay = make_field_block("Quality")
    win.quality_combo = QComboBox()
    win.quality_combo.setEnabled(False)
    win.quality_combo.currentIndexChanged.connect(win._on_quality_changed)
    quality_lay.addWidget(win.quality_combo)
    controls_lay.addWidget(quality_block, 0, 2)

    segment_block, segment_lay = make_field_block("Segments")
    win.segment_combo = QComboBox()
    win._segment_options = [
        ("15 minutes", 900), ("30 minutes", 1800), ("1 hour", 3600),
        ("2 hours", 7200), ("4 hours", 14400), ("Full stream", 0),
    ]
    for label, _ in win._segment_options:
        win.segment_combo.addItem(label)
    win.segment_combo.setCurrentIndex(2)
    win.segment_combo.currentIndexChanged.connect(win._on_segment_length_changed)
    segment_lay.addWidget(win.segment_combo)
    controls_lay.addWidget(segment_block, 0, 3)

    # Time-range crop (F21) — optional start/end for partial downloads
    crop_block, crop_lay = make_field_block("Time range (optional)")
    win.time_range_panel = crop_block
    crop_row = QHBoxLayout()
    crop_row.setSpacing(8)
    crop_start_label = QLabel("Start:")
    crop_start_label.setFixedWidth(36)
    crop_row.addWidget(crop_start_label)
    win.crop_start_input = QLineEdit()
    win.crop_start_input.setPlaceholderText("HH:MM:SS")
    win.crop_start_input.setClearButtonEnabled(True)
    win.crop_start_input.setFixedWidth(100)
    crop_row.addWidget(win.crop_start_input)
    crop_end_label = QLabel("End:")
    crop_end_label.setFixedWidth(28)
    crop_row.addWidget(crop_end_label)
    win.crop_end_input = QLineEdit()
    win.crop_end_input.setPlaceholderText("HH:MM:SS")
    win.crop_end_input.setClearButtonEnabled(True)
    win.crop_end_input.setFixedWidth(100)
    crop_row.addWidget(win.crop_end_input)
    crop_row.addStretch(1)
    crop_lay.addLayout(crop_row)
    controls_lay.addWidget(crop_block, 1, 0, 1, 4)

    output_block, output_lay = make_field_block("Output folder")
    output_row = QHBoxLayout()
    output_row.setSpacing(8)
    win.output_input = QLineEdit(str(_default_output_dir()))
    win.output_input.setClearButtonEnabled(True)
    win.output_input.textChanged.connect(win._refresh_download_summary)
    output_row.addWidget(win.output_input, 1)
    browse_btn = QPushButton("…")
    browse_btn.setObjectName("secondary")
    browse_btn.setFixedWidth(42)
    browse_btn.setToolTip("Choose output folder")
    browse_btn.clicked.connect(win._on_browse)
    output_row.addWidget(browse_btn)
    output_lay.addLayout(output_row)
    controls_lay.addWidget(output_block, 0, 0, 1, 2)
    controls_lay.setColumnStretch(0, 2)
    controls_lay.setColumnStretch(1, 2)
    controls_lay.setColumnStretch(2, 2)
    controls_lay.setColumnStretch(3, 2)
    crop_block.setVisible(False)
    controls_card.setVisible(False)
    win.download_settings_action.toggled.connect(controls_card.setVisible)

    def _on_time_range_toggle(checked):
        crop_block.setVisible(checked)
        if checked:
            win.download_settings_action.setChecked(True)

    win.time_range_action.toggled.connect(_on_time_range_toggle)
    root.addWidget(controls_card)

    win.track_section = QFrame()
    win.track_section.setObjectName("workSection")
    track_lay = QVBoxLayout(win.track_section)
    track_lay.setContentsMargins(14, 12, 14, 12)
    track_lay.setSpacing(8)
    track_header = QHBoxLayout()
    track_title = QLabel("Media tracks")
    track_title.setObjectName("sectionTitle")
    track_header.addWidget(track_title)
    track_header.addStretch(1)
    win.track_summary_label = QLabel("")
    win.track_summary_label.setObjectName("tableHint")
    track_header.addWidget(win.track_summary_label)
    track_lay.addLayout(track_header)
    win.track_table = QTableWidget()
    win.track_table.setColumnCount(5)
    win.track_table.setHorizontalHeaderLabels(
        ["Use", "Type", "Language", "Track", "Codec / rate"]
    )
    track_header_view = win.track_table.horizontalHeader()
    track_header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    track_header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    track_header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    track_header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    track_header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
    win.track_table.setColumnWidth(0, 48)
    win.track_table.setColumnWidth(1, 90)
    win.track_table.setColumnWidth(2, 100)
    win.track_table.verticalHeader().setVisible(False)
    win.track_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    win.track_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.track_table.setMaximumHeight(240)
    style_table(
        win.track_table,
        36,
        accessible_name="Media tracks",
        accessible_description="Use arrow keys to navigate and Enter to toggle media tracks",
    )
    win.track_table.cellActivated.connect(
        lambda row, _column: (
            win._track_checks[row][0].toggle()
            if 0 <= row < len(win._track_checks) else None
        )
    )
    track_lay.addWidget(win.track_table)
    win.track_section.setVisible(False)
    win._track_checks = []
    root.addWidget(win.track_section)

    # ── Per-Download Settings Override (F18) ──────────────────────
    win.adv_frame = QFrame()
    win.adv_frame.setObjectName("optionsRow")
    win.adv_frame.setVisible(False)
    adv_lay = QGridLayout(win.adv_frame)
    adv_lay.setContentsMargins(14, 14, 14, 14)
    adv_lay.setHorizontalSpacing(12)
    adv_lay.setVerticalSpacing(10)

    # Override: post-processing preset
    adv_lay.addWidget(QLabel("Post-process preset:"), 0, 0)
    win.adv_pp_combo = QComboBox()
    win.adv_pp_combo.addItem("(use global setting)", userData="")
    adv_lay.addWidget(win.adv_pp_combo, 0, 1)

    # Override: rate limit
    adv_lay.addWidget(QLabel("Rate limit:"), 1, 0)
    win.adv_rate_input = QLineEdit()
    win.adv_rate_input.setPlaceholderText("e.g. 5M (blank = global)")
    win.adv_rate_input.setFixedWidth(140)
    adv_lay.addWidget(win.adv_rate_input, 1, 1)

    # Override: parallel connections
    adv_lay.addWidget(QLabel("Parallel connections:"), 2, 0)
    win.adv_parallel_spin = QSpinBox()
    win.adv_parallel_spin.setRange(0, 16)
    win.adv_parallel_spin.setSpecialValueText("(global)")
    win.adv_parallel_spin.setFixedWidth(80)
    adv_lay.addWidget(win.adv_parallel_spin, 2, 1)

    # Override: output folder template
    adv_lay.addWidget(QLabel("Folder template:"), 3, 0)
    win.adv_folder_tpl_input = QLineEdit()
    win.adv_folder_tpl_input.setPlaceholderText("(blank = global template)")
    adv_lay.addWidget(win.adv_folder_tpl_input, 3, 1)

    # Override: file template
    adv_lay.addWidget(QLabel("File template:"), 4, 0)
    win.adv_file_tpl_input = QLineEdit()
    win.adv_file_tpl_input.setPlaceholderText("(blank = global template)")
    adv_lay.addWidget(win.adv_file_tpl_input, 4, 1)

    # yt-dlp direct output controls
    adv_lay.addWidget(QLabel("Raw format spec:"), 5, 0)
    win.adv_format_input = QLineEdit()
    win.adv_format_input.setPlaceholderText("e.g. 137+251 or bv*+ba/b")
    win.adv_format_input.setToolTip(
        "Passed verbatim to yt-dlp -f for yt-dlp direct sources"
    )
    adv_lay.addWidget(win.adv_format_input, 5, 1)

    adv_lay.addWidget(QLabel("Format sort:"), 6, 0)
    win.adv_format_sort_combo = QComboBox()
    win.adv_format_sort_combo.addItem("(source default)", userData="")
    win.adv_format_sort_combo.addItem("Prefer AV1", userData="prefer-av1")
    win.adv_format_sort_combo.addItem("Cap at 2160p", userData="cap-2160p")
    win.adv_format_sort_combo.addItem("Cap at 1080p", userData="cap-1080p")
    win.adv_format_sort_combo.addItem("Cap at 720p", userData="cap-720p")
    win.adv_format_sort_combo.addItem("Smallest file", userData="smallest")
    win.adv_format_sort_combo.setToolTip(
        "Safe yt-dlp -S presets; a resolution cap prefers the best format at or below it"
    )
    adv_lay.addWidget(win.adv_format_sort_combo, 6, 1)

    adv_lay.addWidget(QLabel("Video container:"), 7, 0)
    win.adv_container_combo = QComboBox()
    win.adv_container_combo.addItem("MP4 (default)", userData="")
    win.adv_container_combo.addItem("MKV", userData="mkv")
    win.adv_container_combo.addItem("WebM", userData="webm")
    win.adv_container_combo.addItem("Original", userData="original")
    win.adv_container_combo.setToolTip(
        "Merge/remux video without re-encoding; Original keeps the source container"
    )
    adv_lay.addWidget(win.adv_container_combo, 7, 1)

    adv_lay.addWidget(QLabel("Audio extraction:"), 8, 0)
    audio_row = QHBoxLayout()
    win.adv_audio_combo = QComboBox()
    win.adv_audio_combo.addItem("Video download", userData="")
    for audio_format in ("best", "mp3", "m4a", "opus", "flac", "wav"):
        win.adv_audio_combo.addItem(audio_format.upper(), userData=audio_format)
    audio_row.addWidget(win.adv_audio_combo, 1)
    win.adv_audio_quality_input = QLineEdit()
    win.adv_audio_quality_input.setPlaceholderText("quality: 0-10 or 128K")
    win.adv_audio_quality_input.setToolTip(
        "Optional encoder quality; 0 is best, 10 is worst, or use a bitrate such as 128K"
    )
    win.adv_audio_quality_input.setEnabled(False)
    audio_row.addWidget(win.adv_audio_quality_input, 1)
    adv_lay.addLayout(audio_row, 8, 1)

    adv_lay.addWidget(QLabel("Subtitles:"), 9, 0)
    win.adv_subtitle_mode_combo = QComboBox()
    win.adv_subtitle_mode_combo.addItem("Use global setting", userData="")
    win.adv_subtitle_mode_combo.addItem("No subtitles", userData="disabled")
    win.adv_subtitle_mode_combo.addItem(
        "Choose source languages", userData="custom"
    )
    win.adv_subtitle_mode_combo.model().item(2).setEnabled(False)
    adv_lay.addWidget(win.adv_subtitle_mode_combo, 9, 1)

    adv_lay.addWidget(QLabel("Subtitle languages:"), 10, 0)
    win.adv_subtitle_list = QListWidget()
    win.adv_subtitle_list.setSelectionMode(
        QAbstractItemView.SelectionMode.MultiSelection
    )
    win.adv_subtitle_list.setMaximumHeight(105)
    win.adv_subtitle_list.setEnabled(False)
    win.adv_subtitle_list.setToolTip(
        "Fetch a yt-dlp source to list its subtitle languages"
    )
    adv_lay.addWidget(win.adv_subtitle_list, 10, 1)

    adv_lay.addWidget(QLabel("Subtitle output:"), 11, 0)
    subtitle_output_row = QHBoxLayout()
    win.adv_subtitle_auto_check = QCheckBox("Include automatic captions")
    win.adv_subtitle_auto_check.setChecked(True)
    subtitle_output_row.addWidget(win.adv_subtitle_auto_check)
    win.adv_subtitle_convert_combo = QComboBox()
    win.adv_subtitle_convert_combo.addItem("Keep format", userData="")
    for sub_format in ("srt", "vtt", "ass"):
        win.adv_subtitle_convert_combo.addItem(
            f"Convert {sub_format.upper()}", userData=sub_format
        )
    subtitle_output_row.addWidget(win.adv_subtitle_convert_combo, 1)
    win.adv_subtitle_delivery_combo = QComboBox()
    win.adv_subtitle_delivery_combo.addItem("Embed", userData="embed")
    win.adv_subtitle_delivery_combo.addItem("Sidecar", userData="sidecar")
    subtitle_output_row.addWidget(win.adv_subtitle_delivery_combo, 1)
    adv_lay.addLayout(subtitle_output_row, 11, 1)
    _refresh_adv_subtitle_controls(win)

    from ...download_options import (
        SPONSORBLOCK_CATEGORIES, SPONSORBLOCK_NON_REMOVABLE,
    )
    adv_lay.addWidget(QLabel("SponsorBlock:"), 12, 0)
    win.adv_sponsorblock_mode_combo = QComboBox()
    win.adv_sponsorblock_mode_combo.addItem(
        "Use global setting", userData=""
    )
    win.adv_sponsorblock_mode_combo.addItem("Disabled", userData="disabled")
    win.adv_sponsorblock_mode_combo.addItem(
        "Custom category actions", userData="custom"
    )
    adv_lay.addWidget(win.adv_sponsorblock_mode_combo, 12, 1)

    adv_lay.addWidget(QLabel("Category actions:"), 13, 0)
    win.adv_sponsorblock_table = QTableWidget(
        len(SPONSORBLOCK_CATEGORIES), 2
    )
    win.adv_sponsorblock_table.setHorizontalHeaderLabels(
        ["Category", "Action"]
    )
    sponsor_header = win.adv_sponsorblock_table.horizontalHeader()
    sponsor_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    sponsor_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    win.adv_sponsorblock_table.verticalHeader().setVisible(False)
    win.adv_sponsorblock_table.setEditTriggers(
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    win.adv_sponsorblock_table.setMaximumHeight(270)
    style_table(
        win.adv_sponsorblock_table,
        34,
        accessible_name="SponsorBlock categories",
        accessible_description="Choose categories to remove from the download",
    )
    win.adv_sponsorblock_action_combos = {}
    for row, (category, label) in enumerate(SPONSORBLOCK_CATEGORIES.items()):
        item = QTableWidgetItem(label)
        item.setToolTip(category)
        win.adv_sponsorblock_table.setItem(row, 0, item)
        combo = QComboBox()
        combo.addItem("Ignore", userData="")
        combo.addItem("Mark chapter", userData="mark")
        if category not in SPONSORBLOCK_NON_REMOVABLE:
            combo.addItem("Remove segment", userData="remove")
        win.adv_sponsorblock_table.setCellWidget(row, 1, combo)
        win.adv_sponsorblock_action_combos[category] = combo
    adv_lay.addWidget(win.adv_sponsorblock_table, 13, 1)

    adv_lay.addWidget(QLabel("SponsorBlock API:"), 14, 0)
    win.adv_sponsorblock_api_input = QLineEdit()
    win.adv_sponsorblock_api_input.setPlaceholderText(
        "Default API, or a custom HTTPS base URL"
    )
    adv_lay.addWidget(win.adv_sponsorblock_api_input, 14, 1)
    _refresh_adv_sponsorblock_controls(win)

    adv_lay.addWidget(QLabel("Playlist items:"), 15, 0)
    win.adv_playlist_items_input = QLineEdit()
    win.adv_playlist_items_input.setPlaceholderText(
        "e.g. 1:10,15,20:30:2 (blank = all)"
    )
    adv_lay.addWidget(win.adv_playlist_items_input, 15, 1)

    adv_lay.addWidget(QLabel("Playlist dates:"), 16, 0)
    playlist_date_row = QHBoxLayout()
    win.adv_playlist_after_input = QLineEdit()
    win.adv_playlist_after_input.setPlaceholderText("after YYYYMMDD")
    win.adv_playlist_before_input = QLineEdit()
    win.adv_playlist_before_input.setPlaceholderText("before YYYYMMDD")
    playlist_date_row.addWidget(win.adv_playlist_after_input)
    playlist_date_row.addWidget(win.adv_playlist_before_input)
    adv_lay.addLayout(playlist_date_row, 16, 1)

    adv_lay.addWidget(QLabel("Playlist filter:"), 17, 0)
    win.adv_playlist_filter_input = QLineEdit()
    win.adv_playlist_filter_input.setPlaceholderText(
        "yt-dlp match filter, e.g. duration > 60 & !is_live"
    )
    adv_lay.addWidget(win.adv_playlist_filter_input, 17, 1)

    adv_lay.addWidget(QLabel("Playlist sync:"), 18, 0)
    playlist_sync_row = QHBoxLayout()
    win.adv_playlist_max_spin = QSpinBox()
    win.adv_playlist_max_spin.setRange(0, 10000)
    win.adv_playlist_max_spin.setSpecialValueText("No maximum")
    playlist_sync_row.addWidget(win.adv_playlist_max_spin)
    win.adv_playlist_archive_check = QCheckBox(
        "Incremental archive (stop at existing)"
    )
    playlist_sync_row.addWidget(win.adv_playlist_archive_check)
    adv_lay.addLayout(playlist_sync_row, 18, 1)

    adv_lay.addWidget(QLabel("yt-dlp fragments:"), 19, 0)
    transfer_fragment_row = QHBoxLayout()
    win.adv_ytdlp_fragments_spin = QSpinBox()
    win.adv_ytdlp_fragments_spin.setRange(0, 32)
    win.adv_ytdlp_fragments_spin.setSpecialValueText("Global")
    transfer_fragment_row.addWidget(win.adv_ytdlp_fragments_spin)
    win.adv_ytdlp_retries_input = QLineEdit()
    win.adv_ytdlp_retries_input.setPlaceholderText("retries: global/infinite")
    transfer_fragment_row.addWidget(win.adv_ytdlp_retries_input)
    win.adv_ytdlp_fragment_retries_input = QLineEdit()
    win.adv_ytdlp_fragment_retries_input.setPlaceholderText(
        "fragment retries: global/infinite"
    )
    transfer_fragment_row.addWidget(win.adv_ytdlp_fragment_retries_input)
    adv_lay.addLayout(transfer_fragment_row, 19, 1)

    adv_lay.addWidget(QLabel("yt-dlp retry policy:"), 20, 0)
    transfer_retry_row = QHBoxLayout()
    win.adv_ytdlp_retry_sleep_input = QLineEdit()
    win.adv_ytdlp_retry_sleep_input.setPlaceholderText(
        "sleep, e.g. fragment:exp=1:20"
    )
    transfer_retry_row.addWidget(win.adv_ytdlp_retry_sleep_input)
    win.adv_ytdlp_unavailable_combo = QComboBox()
    win.adv_ytdlp_unavailable_combo.addItem("Unavailable: global", userData="")
    win.adv_ytdlp_unavailable_combo.addItem("Unavailable: skip", userData="skip")
    win.adv_ytdlp_unavailable_combo.addItem("Unavailable: abort", userData="abort")
    transfer_retry_row.addWidget(win.adv_ytdlp_unavailable_combo)
    adv_lay.addLayout(transfer_retry_row, 20, 1)

    adv_lay.addWidget(QLabel("yt-dlp live depth:"), 21, 0)
    transfer_live_row = QHBoxLayout()
    win.adv_ytdlp_throttled_input = QLineEdit()
    win.adv_ytdlp_throttled_input.setPlaceholderText("throttled rate: global")
    transfer_live_row.addWidget(win.adv_ytdlp_throttled_input)
    win.adv_ytdlp_wait_input = QLineEdit()
    win.adv_ytdlp_wait_input.setPlaceholderText("wait seconds or MIN-MAX")
    transfer_live_row.addWidget(win.adv_ytdlp_wait_input)
    win.adv_ytdlp_live_combo = QComboBox()
    win.adv_ytdlp_live_combo.addItem("Live start: global", userData=None)
    win.adv_ytdlp_live_combo.addItem("Live from start", userData=True)
    win.adv_ytdlp_live_combo.addItem("Live current edge", userData=False)
    transfer_live_row.addWidget(win.adv_ytdlp_live_combo)
    adv_lay.addLayout(transfer_live_row, 21, 1)

    adv_lay.addWidget(QLabel("yt-dlp embedding:"), 22, 0)
    transfer_embed_row = QHBoxLayout()
    for name, label in (
        ("chapters", "Chapters"),
        ("metadata", "Metadata"),
        ("thumbnail", "Thumbnail"),
    ):
        combo = QComboBox()
        combo.addItem(f"{label}: global", userData=None)
        combo.addItem(f"{label}: on", userData=True)
        combo.addItem(f"{label}: off", userData=False)
        setattr(win, f"adv_ytdlp_embed_{name}_combo", combo)
        transfer_embed_row.addWidget(combo)
    adv_lay.addLayout(transfer_embed_row, 22, 1)

    adv_lay.addWidget(QLabel("yt-dlp arguments:"), 23, 0)
    win.adv_ytdlp_template_combo = QComboBox()
    win.adv_ytdlp_template_combo.setToolTip(
        "Attach a named structured argv template managed in Settings"
    )
    adv_lay.addWidget(win.adv_ytdlp_template_combo, 23, 1)
    _populate_adv_ytdlp_templates(win)

    adv_lay.addWidget(QLabel("HLS clear key:"), 24, 0)
    hls_key_row = QHBoxLayout()
    win.adv_hls_key_input = QLineEdit()
    win.adv_hls_key_input.setMaxLength(4096)
    win.adv_hls_key_input.setEchoMode(QLineEdit.EchoMode.Password)
    win.adv_hls_key_input.setPlaceholderText(
        "Authorized key URI or 32-digit AES-128 key"
    )
    win.adv_hls_key_input.setToolTip(
        "Expert non-DRM recovery only. Overrides a wrong EXT-X-KEY URI/value "
        "through yt-dlp's native HLS downloader and is not persisted."
    )
    hls_key_row.addWidget(win.adv_hls_key_input, 2)
    win.adv_hls_iv_input = QLineEdit()
    win.adv_hls_iv_input.setMaxLength(66)
    win.adv_hls_iv_input.setEchoMode(QLineEdit.EchoMode.Password)
    win.adv_hls_iv_input.setPlaceholderText("Optional IV (hex)")
    win.adv_hls_iv_input.setToolTip(
        "Optional 1-32 digit hexadecimal initialization vector"
    )
    hls_key_row.addWidget(win.adv_hls_iv_input, 1)
    adv_lay.addLayout(hls_key_row, 24, 1)

    # Reset button
    adv_reset_btn = QPushButton("Reset overrides")
    adv_reset_btn.setObjectName("ghost")
    adv_reset_btn.setFixedWidth(130)
    adv_reset_btn.clicked.connect(lambda: _reset_adv_overrides(win))
    adv_lay.addWidget(adv_reset_btn, 25, 1)

    root.addWidget(win.adv_frame)

    def _on_adv_toggle(checked):
        win.adv_frame.setVisible(checked)
    win.adv_overrides_action.toggled.connect(_on_adv_toggle)

    # Populate PP preset choices and wire badge updates
    _populate_adv_pp(win)

    def _update_adv_badge():
        active = bool(get_adv_overrides(win))
        win.adv_overrides_action.setText(
            "Per-download overrides · Modified"
            if active else "Per-download overrides"
        )

    win.adv_pp_combo.currentIndexChanged.connect(lambda _: _update_adv_badge())
    win.adv_rate_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_parallel_spin.valueChanged.connect(lambda _: _update_adv_badge())
    win.adv_folder_tpl_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_file_tpl_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_format_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_format_sort_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_container_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_audio_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_audio_combo.currentIndexChanged.connect(
        lambda _: win.adv_audio_quality_input.setEnabled(
            bool(win.adv_audio_combo.currentData())
        )
    )
    win.adv_audio_quality_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_hls_key_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_hls_iv_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_subtitle_mode_combo.currentIndexChanged.connect(
        lambda _: _refresh_adv_subtitle_controls(win)
    )
    win.adv_subtitle_mode_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_subtitle_list.itemSelectionChanged.connect(_update_adv_badge)
    win.adv_subtitle_auto_check.toggled.connect(
        lambda _: _refresh_adv_subtitle_controls(win)
    )
    win.adv_subtitle_auto_check.toggled.connect(lambda _: _update_adv_badge())
    win.adv_subtitle_convert_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_subtitle_delivery_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_sponsorblock_mode_combo.currentIndexChanged.connect(
        lambda _: _refresh_adv_sponsorblock_controls(win)
    )
    win.adv_sponsorblock_mode_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    for combo in win.adv_sponsorblock_action_combos.values():
        combo.currentIndexChanged.connect(lambda _: _update_adv_badge())
    win.adv_sponsorblock_api_input.textChanged.connect(
        lambda _: _update_adv_badge()
    )
    for field in (
        win.adv_playlist_items_input, win.adv_playlist_after_input,
        win.adv_playlist_before_input, win.adv_playlist_filter_input,
    ):
        field.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_playlist_max_spin.valueChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_playlist_archive_check.toggled.connect(
        lambda _: _update_adv_badge()
    )
    for widget in (
        win.adv_ytdlp_retries_input,
        win.adv_ytdlp_fragment_retries_input,
        win.adv_ytdlp_retry_sleep_input,
        win.adv_ytdlp_throttled_input,
        win.adv_ytdlp_wait_input,
    ):
        widget.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_ytdlp_fragments_spin.valueChanged.connect(
        lambda _: _update_adv_badge()
    )
    for combo in (
        win.adv_ytdlp_unavailable_combo,
        win.adv_ytdlp_live_combo,
        win.adv_ytdlp_embed_chapters_combo,
        win.adv_ytdlp_embed_metadata_combo,
        win.adv_ytdlp_embed_thumbnail_combo,
        win.adv_ytdlp_template_combo,
    ):
        combo.currentIndexChanged.connect(lambda _: _update_adv_badge())

    # Splitter: segments table + runtime log
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.setChildrenCollapsible(False)

    table_frame = QFrame()
    table_frame.setObjectName("dataPane")
    table_lay = QVBoxLayout(table_frame)
    table_lay.setContentsMargins(4, 10, 4, 4)
    table_lay.setSpacing(8)

    table_header = QHBoxLayout()
    table_copy = QVBoxLayout()
    table_copy.setSpacing(3)
    sec2 = QLabel("Segments")
    sec2.setObjectName("sectionTitle")
    table_copy.addWidget(sec2)
    table_header.addLayout(table_copy, 1)

    win.segment_summary_label = QLabel("Segments will appear after metadata is loaded.")
    win.segment_summary_label.setObjectName("tableHint")
    table_header.addWidget(win.segment_summary_label)

    win.select_all_cb = QCheckBox("Select All")
    win.select_all_cb.setChecked(True)
    win.select_all_cb.stateChanged.connect(win._on_select_all)
    table_header.addWidget(win.select_all_cb)
    table_lay.addLayout(table_header)

    win.table = QTableWidget()
    win.table.setColumnCount(5)
    win.table.setHorizontalHeaderLabels(
        ["", "Segment", "Time Range", "Progress", "Size"]
    )
    th = win.table.horizontalHeader()
    th.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    th.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    th.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    th.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    th.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    win.table.setColumnWidth(0, 36)
    win.table.setColumnWidth(1, 140)
    win.table.setColumnWidth(4, 96)
    win.table.verticalHeader().setVisible(False)
    win.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    style_table(
        win.table,
        46,
        accessible_name="Available stream segments",
        accessible_description="Use arrow keys to navigate and Enter to toggle download segments",
    )
    win.table.cellActivated.connect(
        lambda row, _column: (
            win._segment_checks[row].toggle()
            if 0 <= row < len(win._segment_checks) else None
        )
    )
    table_lay.addWidget(win.table)
    splitter.addWidget(table_frame)

    log_frame = QFrame()
    log_frame.setObjectName("activityPane")
    log_lay = QVBoxLayout(log_frame)
    log_lay.setContentsMargins(18, 14, 14, 10)
    log_lay.setSpacing(8)

    log_header = QHBoxLayout()
    log_copy = QVBoxLayout()
    log_copy.setSpacing(3)
    sec3 = QLabel("Activity")
    sec3.setObjectName("sectionTitle")
    log_copy.addWidget(sec3)
    log_header.addLayout(log_copy, 1)
    clear_log_btn = QPushButton("Clear Log")
    clear_log_btn.setObjectName("ghost")
    clear_log_btn.clicked.connect(lambda: win.log_text.clear())
    log_header.addWidget(clear_log_btn)
    log_lay.addLayout(log_header)

    win.log_text = QTextEdit()
    win.log_text.setObjectName("log")
    win.log_text.setReadOnly(True)
    win.log_text.setPlainText("Ready")
    log_lay.addWidget(win.log_text)
    splitter.addWidget(log_frame)
    splitter.setSizes([450, 220])
    root.addWidget(splitter, 1)

    # Download action row
    dl_row = QHBoxLayout()
    dl_row.addStretch(1)
    win.schedule_btn = QPushButton("Schedule...")
    win.schedule_btn.setObjectName("secondary")
    win.schedule_btn.setToolTip("Queue the URL and start it at a future time")
    win.schedule_btn.clicked.connect(win._on_schedule_url)
    dl_row.addWidget(win.schedule_btn)
    win.copy_command_btn = QPushButton("Copy command")
    win.copy_command_btn.setObjectName("secondary")
    win.copy_command_btn.setEnabled(False)
    win.copy_command_btn.setToolTip(
        "Copy the exact standalone yt-dlp or FFmpeg command for the latest job"
    )
    win.copy_command_btn.clicked.connect(win._on_copy_download_command)
    dl_row.addWidget(win.copy_command_btn)
    win.download_btn = QPushButton("Download Selected")
    win.download_btn.setObjectName("primary")
    win.download_btn.setEnabled(False)
    win.download_btn.clicked.connect(win._on_download)
    dl_row.addWidget(win.download_btn)
    root.addLayout(dl_row)

    # Queue panel — shows pending items
    queue_card = QFrame()
    queue_card.setObjectName("queuePane")
    qcard_lay = QVBoxLayout(queue_card)
    qcard_lay.setContentsMargins(14, 14, 14, 10)
    qcard_lay.setSpacing(8)
    queue_header = QHBoxLayout()
    qt = QLabel("Queue")
    qt.setObjectName("sectionTitle")
    queue_header.addWidget(qt)
    queue_header.addStretch()
    clear_queue_btn = QPushButton("Clear Queue")
    clear_queue_btn.setObjectName("ghost")
    clear_queue_btn.clicked.connect(win._on_clear_queue)
    queue_header.addWidget(clear_queue_btn)
    qcard_lay.addLayout(queue_header)
    win.queue_table = QTableWidget()
    win.queue_table.setColumnCount(6)
    win.queue_table.setHorizontalHeaderLabels(
        ["Status", "Platform", "Title", "Added / Scheduled", "", ""]
    )
    qh = win.queue_table.horizontalHeader()
    qh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    qh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    qh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    qh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    qh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    qh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
    win.queue_table.setColumnWidth(0, 96)
    win.queue_table.setColumnWidth(1, 90)
    win.queue_table.setColumnWidth(3, 160)
    win.queue_table.setColumnWidth(4, 66)   # move up/down
    win.queue_table.setColumnWidth(5, 84)   # remove
    win.queue_table.verticalHeader().setVisible(False)
    win.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    style_table(
        win.queue_table,
        36,
        accessible_name="Download queue",
        accessible_description="Queued and active download jobs",
    )
    qcard_lay.addWidget(win.queue_table)
    win.queue_empty_state = QFrame()
    empty_lay = QVBoxLayout(win.queue_empty_state)
    empty_lay.setContentsMargins(12, 28, 12, 12)
    empty_lay.setSpacing(5)
    empty_lay.addStretch(1)
    empty_title = QLabel("No downloads in the queue")
    empty_title.setObjectName("emptyStateTitle")
    empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_lay.addWidget(empty_title)
    empty_body = QLabel("Add a URL above to get started.")
    empty_body.setObjectName("emptyStateBody")
    empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_lay.addWidget(empty_body)
    empty_lay.addStretch(2)
    qcard_lay.addWidget(win.queue_empty_state, 1)
    root.addWidget(queue_card)

    # Main working surface: queue and activity share the viewport. Segment
    # details remain available below once metadata has been fetched.
    work_index = root.indexOf(splitter)
    root.removeWidget(splitter)
    root.removeWidget(queue_card)
    table_frame.setParent(None)
    log_frame.setParent(None)
    splitter.deleteLater()

    work_surface = QSplitter(Qt.Orientation.Horizontal)
    work_surface.setObjectName("workSurface")
    work_surface.setChildrenCollapsible(False)
    queue_card.setMinimumWidth(560)
    log_frame.setMinimumWidth(320)
    log_frame.setMinimumHeight(240)
    work_surface.addWidget(queue_card)
    work_surface.addWidget(log_frame)
    work_surface.setStretchFactor(0, 2)
    work_surface.setStretchFactor(1, 1)
    work_surface.setSizes([860, 420])
    root.insertWidget(work_index, work_surface, 1)
    win.segments_section = table_frame
    win.segments_section.setVisible(False)
    root.addWidget(table_frame)

    win._refresh_download_summary()
    win._refresh_queue_table()

    return page



class DownloadTabMixin(
    DownloadSingleMixin, DownloadFinalizeMixin, DownloadVodMixin,
    DownloadQueueMixin,
):
    """Download-tab handler methods, mixed into ``StreamKeep``."""

    # ── Summary / metrics ───────────────────────────────────────
