"""Download tab — URL input, VOD picker, segments table, queue, log."""

from PyQt6.QtCore import Qt, QUrl, QStringListModel
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMenu, QPushButton, QSpinBox, QSplitter, QTableWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from ...theme import CAT
from ...utils import default_output_dir as _default_output_dir
from ..widgets import make_field_block, path_label, style_table


def _populate_adv_pp(win):
    """Populate the per-download PP preset combo from settings presets."""
    from .settings import BUILTIN_PRESETS, _get_user_presets
    combo = win.adv_pp_combo
    combo.blockSignals(True)
    current = combo.currentData() or ""
    combo.clear()
    combo.addItem("(use global setting)", userData="")
    for name in BUILTIN_PRESETS:
        combo.addItem(f"★ {name}", userData=name)
    for name in _get_user_presets(win):
        combo.addItem(name, userData=name)
    # Restore selection
    for i in range(combo.count()):
        if combo.itemData(i) == current:
            combo.setCurrentIndex(i)
            break
    combo.blockSignals(False)


def _reset_adv_overrides(win):
    """Clear all per-download override fields."""
    win.adv_pp_combo.setCurrentIndex(0)
    win.adv_rate_input.clear()
    win.adv_parallel_spin.setValue(0)
    win.adv_folder_tpl_input.clear()
    win.adv_file_tpl_input.clear()
    win.adv_override_badge.setVisible(False)


def get_adv_overrides(win):
    """Return a dict of active per-download overrides (empty keys omitted).

    Called from main_window._on_download() to merge into worker context.
    """
    overrides = {}
    preset_name = win.adv_pp_combo.currentData() or ""
    if preset_name:
        overrides["pp_preset"] = preset_name
    rate = win.adv_rate_input.text().strip()
    if rate:
        overrides["rate_limit"] = rate
    par = win.adv_parallel_spin.value()
    if par > 0:
        overrides["parallel_connections"] = par
    ftpl = win.adv_folder_tpl_input.text().strip()
    if ftpl:
        overrides["folder_template"] = ftpl
    fitpl = win.adv_file_tpl_input.text().strip()
    if fitpl:
        overrides["file_template"] = fitpl
    return overrides


def build_download_tab(win):
    """Build the Download tab page. Stashes widget refs on `win.*`."""
    page = QWidget()
    root = QVBoxLayout(page)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(12)

    hero = QFrame()
    hero.setObjectName("pageHeader")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(4, 12, 4, 4)
    hero_lay.setSpacing(3)

    win.download_hero_title = QLabel("New download")
    win.download_hero_title.setObjectName("heroTitle")
    win.download_hero_title.setWordWrap(True)
    win.download_hero_body = QLabel("Paste a stream, VOD, podcast, or media URL.")
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
    url_lay.setContentsMargins(16, 14, 16, 14)
    url_lay.setSpacing(10)

    url_header = QVBoxLayout()
    url_header.setSpacing(4)
    sec1 = QLabel("Source URL")
    sec1.setObjectName("sectionTitle")
    url_header.addWidget(sec1)
    url_lay.addLayout(url_header)

    url_row = QHBoxLayout()
    url_row.setSpacing(10)
    win.url_input = QLineEdit()
    win.url_input.setPlaceholderText(
        "Paste a stream, channel, VOD, or direct media URL…"
    )
    win.url_input.setClearButtonEnabled(True)
    win.url_input.setMinimumHeight(44)
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

    win.fetch_btn = QPushButton("Fetch")
    win.fetch_btn.setObjectName("primary")
    win.fetch_btn.setMinimumWidth(116)
    win.fetch_btn.setMinimumHeight(44)
    win.fetch_btn.clicked.connect(win._on_fetch)
    url_row.addWidget(win.fetch_btn)
    url_lay.addLayout(url_row)

    utility_bar = QFrame()
    utility_bar.setObjectName("optionsRow")
    utility_lay = QHBoxLayout(utility_bar)
    utility_lay.setContentsMargins(0, 0, 0, 0)
    utility_lay.setSpacing(8)

    win.batch_import_btn = QPushButton("Import URLs")
    win.batch_import_btn.setObjectName("secondary")
    win.batch_import_btn.setToolTip("Import URLs from a text file (one per line) and queue them all (F44)")
    win.batch_import_btn.clicked.connect(win._on_batch_url_import)
    utility_lay.addWidget(win.batch_import_btn)

    paste_btn = QPushButton("Paste")
    paste_btn.setObjectName("secondary")
    paste_btn.setToolTip("Paste a URL from the clipboard")
    paste_btn.clicked.connect(win.url_input.paste)
    utility_lay.addWidget(paste_btn)

    win.scan_btn = QPushButton("Scan page")
    win.scan_btn.setObjectName("secondary")
    win.scan_btn.setToolTip("Fetch the URL as HTML and extract all video/media links it references")
    win.scan_btn.clicked.connect(win._on_scan_page)
    utility_lay.addWidget(win.scan_btn)

    win.queue_btn = QPushButton("Queue")
    win.queue_btn.setObjectName("secondary")
    win.queue_btn.setToolTip("Add the current URL to the download queue")
    win.queue_btn.clicked.connect(win._on_queue_url)
    utility_lay.addWidget(win.queue_btn)

    utility_lay.addStretch(1)

    more_btn = QPushButton("More")
    more_btn.setObjectName("ghost")
    more_menu = QMenu(more_btn)
    win.expand_btn = more_menu.addAction("Expand playlist")
    win.expand_btn.setToolTip("Queue every item from a playlist or channel")
    win.expand_btn.triggered.connect(win._on_expand_playlist)
    win.recover_btn = more_menu.addAction("Recover Twitch VOD")
    win.recover_btn.triggered.connect(win._on_recover_vod)
    more_menu.addSeparator()
    win.clip_btn = more_menu.addAction("Clipboard watch")
    win.clip_btn.setCheckable(True)
    win.clip_btn.triggered.connect(win._on_toggle_clipboard)
    more_btn.setMenu(more_menu)
    utility_lay.addWidget(more_btn)
    url_lay.addWidget(utility_bar)

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
    style_table(win.vod_table, 42)
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
    root.addWidget(controls_card)

    # ── Per-Download Settings Override (F18) ──────────────────────
    adv_toggle_row = QHBoxLayout()
    adv_toggle_row.setSpacing(6)
    range_toggle_btn = QPushButton("Time range")
    range_toggle_btn.setObjectName("ghost")
    range_toggle_btn.setCheckable(True)
    range_toggle_btn.toggled.connect(crop_block.setVisible)
    adv_toggle_row.addWidget(range_toggle_btn)
    win.adv_toggle_btn = QPushButton("Advanced")
    win.adv_toggle_btn.setObjectName("ghost")
    win.adv_toggle_btn.setCheckable(True)
    win.adv_override_badge = QLabel("")
    win.adv_override_badge.setStyleSheet(
        f"background:transparent; color:{CAT['peach']}; border:none; "
        f"font-size:12px; font-weight:700; padding:0 4px;"
    )
    win.adv_override_badge.setVisible(False)
    adv_toggle_row.addWidget(win.adv_toggle_btn)
    adv_toggle_row.addWidget(win.adv_override_badge)
    adv_toggle_row.addStretch(1)
    root.addLayout(adv_toggle_row)

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

    # Reset button
    adv_reset_btn = QPushButton("Reset overrides")
    adv_reset_btn.setObjectName("ghost")
    adv_reset_btn.setFixedWidth(130)
    adv_reset_btn.clicked.connect(lambda: _reset_adv_overrides(win))
    adv_lay.addWidget(adv_reset_btn, 5, 1)

    root.addWidget(win.adv_frame)

    def _on_adv_toggle(checked):
        win.adv_frame.setVisible(checked)
        win.adv_toggle_btn.setText("Hide advanced" if checked else "Advanced")
    win.adv_toggle_btn.toggled.connect(_on_adv_toggle)

    # Populate PP preset choices and wire badge updates
    _populate_adv_pp(win)

    def _update_adv_badge():
        active = bool(get_adv_overrides(win))
        win.adv_override_badge.setVisible(active)
        win.adv_override_badge.setText("Modified" if active else "")

    win.adv_pp_combo.currentIndexChanged.connect(lambda _: _update_adv_badge())
    win.adv_rate_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_parallel_spin.valueChanged.connect(lambda _: _update_adv_badge())
    win.adv_folder_tpl_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_file_tpl_input.textChanged.connect(lambda _: _update_adv_badge())

    # Splitter: segments table + runtime log
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.setChildrenCollapsible(False)

    table_frame = QFrame()
    table_frame.setObjectName("workSection")
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
    style_table(win.table, 46)
    table_lay.addWidget(win.table)
    splitter.addWidget(table_frame)

    log_frame = QFrame()
    log_frame.setObjectName("workSection")
    log_lay = QVBoxLayout(log_frame)
    log_lay.setContentsMargins(18, 10, 4, 4)
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
    win.download_btn = QPushButton("Download Selected")
    win.download_btn.setObjectName("primary")
    win.download_btn.setEnabled(False)
    win.download_btn.clicked.connect(win._on_download)
    dl_row.addWidget(win.download_btn)
    root.addLayout(dl_row)

    # Queue panel — shows pending items
    queue_card = QFrame()
    queue_card.setObjectName("workSection")
    qcard_lay = QVBoxLayout(queue_card)
    qcard_lay.setContentsMargins(4, 10, 18, 4)
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
    style_table(win.queue_table, 36)
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

import copy
import os
import re
import time
import urllib.parse
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtWidgets import (
    QFileDialog, QProgressBar, QTableWidgetItem,
)
from PyQt6.QtGui import QColor, QDesktopServices

from ...models import ResumeState
from ...extractors import (
    Extractor,
    TwitchExtractor,
    YtDlpExtractor,
)
from ...workers import (
    FetchWorker,
    VodPageWorker,
    DownloadWorker,
    FinalizeWorker,
    PlaylistExpandWorker as _PlaylistExpandWorker,
    PageScrapeWorker as _PageScrapeWorker,
)
from ...postprocess import (
    PostProcessor,
    VIDEO_EXTS,
    AUDIO_EXTS,
)
from ...utils import (
    fmt_size as _fmt_size,
    fmt_duration as _fmt_duration,
    safe_filename as _safe_filename,
    render_template as _render_template,
    build_template_context as _build_template_context,
    free_space_bytes as _free_space_bytes,
    estimate_download_bytes as _estimate_download_bytes,
)
from ..widgets import (
    PLATFORM_BADGES,
    ask_premium_confirmation,
    ask_premium_text_input,
    path_label as _path_label,
)
from ... import db as _db


class DownloadTabMixin:
    """Download-tab handler methods, mixed into ``StreamKeep``."""

    # ── Summary / metrics ───────────────────────────────────────

    def _refresh_download_summary(self):
        if not hasattr(self, "download_hero_title"):
            return

        url = self.url_input.text().strip() if hasattr(self, "url_input") else ""
        if self.stream_info:
            title = self.stream_info.title or "Ready to download"
            summary_parts = []
            if self.stream_info.platform:
                summary_parts.append(self.stream_info.platform)
            if self.stream_info.channel:
                summary_parts.append(self.stream_info.channel)
            if self.stream_info.duration_str:
                summary_parts.append(self.stream_info.duration_str)
            if self.stream_info.is_live:
                summary_parts.append("Live capture")
            body = "  •  ".join(summary_parts) if summary_parts else "Metadata loaded."
        elif url:
            ext = Extractor.detect(url)
            title = "Source detected" if ext else "New download"
            if ext:
                body = f"{ext.NAME} link recognized. Fetch when ready."
            else:
                body = "Paste a supported stream, VOD, podcast, or media URL."
        else:
            title = "New download"
            body = "Paste a stream, VOD, podcast, or media URL."

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
        # Append free-disk hint to the output card subline so users see
        # what they have to work with before starting a long download.
        free_bytes = _free_space_bytes(output_path) if output_path else None
        free_label = f"{_fmt_size(free_bytes)} free" if free_bytes else ""
        output_sub_with_free = output_sub or "Choose a destination folder"
        if free_label:
            output_sub_with_free = f"{free_label} \u2022 {output_sub_with_free}"
        self._set_metric(
            self.download_output_value,
            self.download_output_sub,
            _path_label(output_path),
            output_sub_with_free,
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
        checked_indices = [i for i, cb in enumerate(self._vod_checks) if cb.isChecked()]
        checked = len(checked_indices)
        if total and checked:
            # Sum duration of selected VODs
            total_ms = 0
            for i in checked_indices:
                if i < len(self._vod_list):
                    total_ms += self._vod_list[i].duration_ms or 0
            dur_str = ""
            if total_ms > 0:
                secs = total_ms // 1000
                h, m = divmod(secs // 60, 60)
                dur_str = f" · {h}h {m}m" if h else f" · {m}m"
            self.vod_summary_label.setText(f"{checked} of {total} selected{dur_str}")
        elif total:
            self.vod_summary_label.setText(f"0 of {total} selected")
        else:
            self.vod_summary_label.setText("Inspect a channel to browse available VODs.")

    # Monitor tab handlers → streamkeep.ui.tabs.monitor.MonitorTabMixin

    # History tab handlers → streamkeep.ui.tabs.history.HistoryTabMixin

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


    # ── URL input / clipboard ───────────────────────────────────

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
            self._log("[CLIPBOARD] Rejected malformed URL")
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


    # ── Fetch / resolve ─────────────────────────────────────────

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
        if hasattr(self, "trim_btn"):
            self.trim_btn.setVisible(False)
        self.overall_progress.setVisible(False)
        self.quality_combo.clear()
        self.quality_combo.setEnabled(False)
        self.table.setRowCount(0)
        if hasattr(self, "segments_section"):
            self.segments_section.setVisible(False)
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
        qualities = info.qualities or []
        for q in qualities:
            bw_mbps = q.bandwidth / 1_000_000 if q.bandwidth else 0
            ft_tag = f" [{q.format_type.upper()}]" if q.format_type != "hls" else ""
            label = f"{q.name} ({q.resolution}, {bw_mbps:.1f} Mbps){ft_tag}"
            self.quality_combo.addItem(label, q)
        if qualities:
            selected_idx = self._choose_default_quality_index(
                qualities, info.platform or ""
            )
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

        # Metadata-based duplicate check after resolve (F40 — fuzzy matching)
        dup = self._find_duplicate(
            "", info.title, platform=info.platform,
            duration_secs=info.total_secs,
        )
        if dup:
            self._log(f"[DUPLICATE] Match: already downloaded {dup.date} to {dup.path}")
            self._set_status(
                f"Possible duplicate of \"{dup.title}\" ({dup.date}). Download anyway if intentional.",
                "warning",
            )
            # Advisory dialog — non-blocking for queue/batch, shown for manual fetches
            if not self._queue_autostart:
                details = (
                    f"Title: {dup.title}\n"
                    f"Downloaded: {dup.date}\n"
                    f"Quality: {dup.quality}\n"
                    f"Size: {dup.size}\n"
                    f"Location: {dup.path}"
                )
                if not ask_premium_confirmation(
                    self,
                    title="Possible duplicate found",
                    body="StreamKeep found a recording in your library that closely matches what you are about to download.",
                    eyebrow="DOWNLOAD",
                    badge_text="Potential match",
                    tone="warning",
                    summary_title="Downloading again may waste storage and clutter history.",
                    summary_body="Continue only if you intentionally want another copy or a better variant.",
                    details_title="Existing recording",
                    details_body=details,
                    primary_label="Download anyway",
                    secondary_label="Skip download",
                    default_action="secondary",
                    min_width=640,
                ):
                    self._set_status("Download skipped — duplicate detected.", "idle")
                    return
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
        self._record_failed_job(
            stage="fetch",
            error=err,
            item=self._queue_active_item,
            out_dir=self.output_input.text().strip() if hasattr(self, "output_input") else "",
        )
        self._refresh_download_summary()
        self._set_status(f"Fetch failed: {err}", "error")
        if self._queue_active_item is not None:
            self._release_queue_item("failed", err[:120])
            self._start_next_background_job()


    # ── VOD listing ─────────────────────────────────────────────

    def _on_vods_found(self, vod_list, platform_name, next_cursor=None):
        self._vod_next_cursor = next_cursor
        self._vod_source_url = self.url_input.text().strip()
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
            cb.stateChanged.connect(lambda _state, row=i: self._on_vod_cb_toggled(row))
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

            views_str = f"{v.viewers:,}" if v.viewers else "—"
            views_item = QTableWidgetItem(views_str)
            views_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 5, views_item)

        self._vod_last_checked_row = -1  # shift-click anchor
        self.vod_widget.setVisible(True)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self._refresh_vod_summary()
        if hasattr(self, "vod_load_more_btn"):
            self.vod_load_more_btn.setVisible(bool(next_cursor))
            self.vod_load_more_btn.setEnabled(bool(next_cursor))
            self.vod_load_more_btn.setText("Load More VODs")
        self._set_status(f"Found {len(vod_list)} VOD(s). Select one to inspect or batch download.", "success")

    def _on_vod_cb_toggled(self, row):
        """Handle a VOD checkbox toggle — supports shift-click range select."""
        from PyQt6.QtWidgets import QApplication
        modifiers = QApplication.keyboardModifiers()
        anchor = getattr(self, "_vod_last_checked_row", -1)
        if modifiers & Qt.KeyboardModifier.ShiftModifier and 0 <= anchor < len(self._vod_checks):
            lo, hi = min(anchor, row), max(anchor, row)
            target_state = self._vod_checks[row].isChecked()
            for r in range(lo, hi + 1):
                if r < len(self._vod_checks):
                    self._vod_checks[r].blockSignals(True)
                    self._vod_checks[r].setChecked(target_state)
                    self._vod_checks[r].blockSignals(False)
        self._vod_last_checked_row = row
        self._refresh_vod_summary()

    def _on_vod_select_all(self, state):
        checked = state == Qt.CheckState.Checked.value
        for cb in self._vod_checks:
            cb.setChecked(checked)
        self._refresh_vod_summary()

    def _on_vod_queue_selected(self):
        """Add all checked VODs to the download queue."""
        checked = [self._vod_list[i] for i, cb in enumerate(self._vod_checks) if cb.isChecked()]
        if not checked:
            self._set_status("Select at least one VOD to queue.", "warning")
            return
        added = 0
        for vod in checked:
            ok = self._queue_add(
                vod.source,
                title=vod.title,
                platform=vod.platform,
                vod_source=vod.source,
                vod_platform=vod.platform,
                vod_title=vod.title,
                vod_channel=vod.channel,
            )
            if ok:
                added += 1
        self._log(f"[QUEUE] Added {added} of {len(checked)} checked VOD(s) to queue")
        self._set_status(
            f"Queued {added} VOD(s) for download." if added else "All selected VODs are already queued.",
            "success" if added else "info",
        )
        if added:
            self._advance_queue()

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

    def _on_vod_load_more(self):
        """Fetch the next page of VODs and append to the table."""
        cursor = getattr(self, "_vod_next_cursor", None)
        url = getattr(self, "_vod_source_url", "")
        if not cursor or not url:
            return
        # Cancel any previous page worker
        pw = getattr(self, "_vod_page_worker", None)
        if pw is not None and pw.isRunning():
            pw.requestInterruption()
            pw.wait(1500)
        if hasattr(self, "vod_load_more_btn"):
            self.vod_load_more_btn.setEnabled(False)
            self.vod_load_more_btn.setText("Loading…")
        self._log(f"[VOD] Fetching next page (cursor: {str(cursor)[:20]}…)")
        worker = VodPageWorker(url, cursor)
        worker.log.connect(self._log)
        worker.page_ready.connect(self._on_vod_page_ready)
        worker.error.connect(self._on_vod_page_error)
        self._vod_page_worker = worker
        worker.start()

    def _on_vod_page_ready(self, new_vods, next_cursor):
        """Append a page of VODs to the existing table."""
        self._vod_next_cursor = next_cursor
        if not new_vods:
            self._log("[VOD] No more VODs on the next page.")
            self._set_status("No additional VODs found.", "info")
            if hasattr(self, "vod_load_more_btn"):
                self.vod_load_more_btn.setVisible(False)
            return
        # Append to the existing list
        start_row = len(self._vod_list)
        self._vod_list.extend(new_vods)
        self.vod_table.setRowCount(len(self._vod_list))
        for i, v in enumerate(new_vods, start=start_row):
            cb = QCheckBox()
            cb.stateChanged.connect(lambda _state, row=i: self._on_vod_cb_toggled(row))
            cb_widget = QWidget()
            cb_lay = QHBoxLayout(cb_widget)
            cb_lay.addWidget(cb)
            cb_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_lay.setContentsMargins(0, 0, 0, 0)
            self.vod_table.setCellWidget(i, 0, cb_widget)
            self._vod_checks.append(cb)
            badge = PLATFORM_BADGES.get(v.platform, {})
            plat_item = QTableWidgetItem(v.platform)
            plat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if badge.get("color"):
                plat_item.setForeground(QColor(badge["color"]))
            self.vod_table.setItem(i, 1, plat_item)
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
            views_str = f"{v.viewers:,}" if v.viewers else "—"
            views_item = QTableWidgetItem(views_str)
            views_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 5, views_item)
        self._refresh_vod_summary()
        if hasattr(self, "vod_load_more_btn"):
            self.vod_load_more_btn.setVisible(bool(next_cursor))
            self.vod_load_more_btn.setEnabled(bool(next_cursor))
            self.vod_load_more_btn.setText("Load More VODs")
        total = len(self._vod_list)
        self._log(f"[VOD] Loaded {len(new_vods)} more — {total} total")
        self._set_status(f"{total} VOD(s) loaded. {len(new_vods)} new from this page.", "success")

    def _on_vod_page_error(self, err):
        self._log(f"[VOD] Pagination error: {err}")
        self._set_status(f"Failed to load more VODs: {err}", "error")
        if hasattr(self, "vod_load_more_btn"):
            self.vod_load_more_btn.setEnabled(True)
            self.vod_load_more_btn.setText("Load More VODs")

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


    # ── Batch VOD download ──────────────────────────────────────

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
        self._attach_resume_to_worker(worker)
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
                               "Batch download finished")
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.vod_dl_all_btn.setEnabled(True)
        self.vod_load_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(True)
        self._persist_config()

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


    # ── Segment management ──────────────────────────────────────

    def _get_segment_secs(self):
        idx = self.segment_combo.currentIndex()
        return self._segment_options[idx][1]

    @staticmethod
    def _parse_crop_secs(text):
        """Parse a HH:MM:SS, MM:SS, or plain seconds string into total
        seconds. Returns 0 if the text is empty or invalid."""
        text = (text or "").strip()
        if not text:
            return 0
        parts = text.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(float(text))
        except (ValueError, IndexError):
            return 0

    @staticmethod
    def _fmt_crop_time(secs):
        """Format seconds as HH:MM:SS for log output."""
        s = int(secs)
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"

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
        if hasattr(self, "segments_section"):
            self.segments_section.setVisible(bool(segments))
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


    # ── Download core ───────────────────────────────────────────

    def _on_download(self):
        if not self.stream_info:
            return False
        src_url = self.url_input.text().strip() if hasattr(self, "url_input") else ""
        if src_url and not self.stream_info.is_live:
            prev = _db.find_history_by_url(src_url)
            if prev:
                from PyQt6.QtWidgets import QMessageBox
                ans = QMessageBox.question(
                    self, "Already Downloaded",
                    f"This URL was downloaded on {prev.get('date', '?')}\n"
                    f"to: {prev.get('path', '?')[:80]}\n\n"
                    "Download again?",
                )
                if ans != QMessageBox.StandardButton.Yes:
                    return False
        # Disk-space preflight — catches "no more room on device" before
        # ffmpeg runs for three hours and exits with a muxing error. Only
        # warns when we have a meaningful estimate; lives / unknown-duration
        # streams skip the check.
        if not self._preflight_disk_space():
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

        # Per-download overrides (F18)
        _dl_overrides = get_adv_overrides(self)

        # Render filename + folder from templates (templates can produce
        # nested paths like "{channel}/{date} - {title}")
        ctx = _build_template_context(self.stream_info)
        _folder_tpl = _dl_overrides.get("folder_template") or self._folder_template
        _file_tpl = _dl_overrides.get("file_template") or self._file_template
        folder_parts = _render_template(_folder_tpl, ctx)
        file_parts = _render_template(_file_tpl, ctx)
        title_safe = file_parts[-1] if file_parts else (
            _safe_filename(self.stream_info.title)
            or f"{self.stream_info.platform}_download"
        )

        # Time-range crop (F21) — parse optional start/end bounds
        crop_start = self._parse_crop_secs(
            self.crop_start_input.text() if hasattr(self, "crop_start_input") else ""
        )
        crop_end = self._parse_crop_secs(
            self.crop_end_input.text() if hasattr(self, "crop_end_input") else ""
        )
        if crop_end and crop_start and crop_end <= crop_start:
            self._set_status("Time range end must be after start.", "warning")
            return False

        seg_secs = self._get_segment_secs()
        single_segment = (is_live_capture or fmt_type in ("mp4", "ytdlp_direct")
                          or seg_secs == 0 or total_secs <= seg_secs)
        segments = []
        for i, cb in enumerate(self._segment_checks):
            if cb.isChecked():
                if single_segment:
                    seg_start = crop_start or 0
                    seg_dur = (crop_end or (0 if is_live_capture else int(total_secs))) - seg_start
                    segments.append((0, title_safe, seg_start, max(0, seg_dur)))
                    break
                else:
                    start = i * seg_secs
                    end = min((i + 1) * seg_secs, total_secs)
                    # Skip segments entirely outside the crop window
                    if crop_end and start >= crop_end:
                        continue
                    if crop_start and end <= crop_start:
                        continue
                    # Clamp segment bounds to the crop window
                    if crop_start and start < crop_start:
                        start = crop_start
                    if crop_end and end > crop_end:
                        end = crop_end
                    label = f"{title_safe}_part{i + 1:02d}"
                    segments.append((i, label, start, int(end - start)))

        if not segments:
            self._log("No segments selected.")
            self._set_status("Select at least one segment before downloading.", "warning")
            return False

        if crop_start or crop_end:
            self._log(f"[CROP] Time range: {self._fmt_crop_time(crop_start)} → {self._fmt_crop_time(crop_end or total_secs)}")

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
        self._init_speed_tracking()
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
        self.download_worker.rate_limit = _dl_overrides.get("rate_limit") or YtDlpExtractor.rate_limit
        self.download_worker.proxy = YtDlpExtractor.proxy
        self.download_worker.download_subs = YtDlpExtractor.download_subs
        self.download_worker.sponsorblock = YtDlpExtractor.sponsorblock
        self.download_worker.parallel_connections = _dl_overrides.get("parallel_connections") or self._parallel_connections
        # Pass time-range crop to yt-dlp via --download-sections (F21)
        if fmt_type == "ytdlp_direct" and (crop_start or crop_end):
            cs = self._fmt_crop_time(crop_start) if crop_start else "0:00:00"
            ce = self._fmt_crop_time(crop_end) if crop_end else ""
            self.download_worker.download_sections = f"*{cs}-{ce}" if ce else f"*{cs}-"
        if audio_url:
            self._log("Audio merge: enabled (video-only format detected)")
        if fmt_type == "ytdlp_direct":
            self._log("Download mode: yt-dlp direct (handles URL refresh + format merge)")
        self.download_worker.progress.connect(self._on_dl_progress)
        self.download_worker.segment_done.connect(self._on_segment_done)
        self.download_worker.error.connect(self._on_dl_error)
        self.download_worker.log.connect(self._log)
        self.download_worker.all_done.connect(self._on_all_done)
        self.download_worker.finished.connect(self._on_download_worker_finished)
        self._attach_resume_to_worker(self.download_worker)
        # Store overrides for postprocess snapshot merge (F18)
        self._dl_overrides = _dl_overrides
        if _dl_overrides:
            self._log(f"[OVERRIDE] Per-download overrides active: {', '.join(_dl_overrides.keys())}")
        _reset_adv_overrides(self)
        self.download_worker.start()
        return True

    def _on_download_worker_finished(self):
        if not getattr(self, "_download_had_errors", False):
            return
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(self._output_contains_media(self._active_output_dir))
        if self._queue_active_item is not None:
            note = f"{getattr(self, '_completed_segments', 0)}/{getattr(self, '_total_segments', 0)} segments completed"
            self._release_queue_item("failed", note)
        self._set_status(
            "Download stopped after failed segment(s). Resume sidecar was kept for retry.",
            "warning",
        )
        self._persist_config()
        self._update_tray_badge()
        self._reset_speed_dashboard()
        self._start_next_background_job()

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
        # Parse speed from the status text (F16 speed dashboard)
        self._update_speed_from_status(status)

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
        self._record_failed_job(
            stage="download",
            error=err,
            item=self._queue_active_item,
            info=self._active_stream_info or self.stream_info,
            out_dir=self._active_output_dir,
        )
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
        if hasattr(self, "trim_btn"):
            self.trim_btn.setVisible(True)
        active_info_n = self._active_stream_info or self.stream_info
        title_n = (active_info_n.title if active_info_n and active_info_n.title else "Download")[:80]
        self._notify_center(
            f"Download complete: {title_n}",
            "success" if not self._download_had_errors else "warning",
        )
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
            self._record_failed_job(
                stage="download",
                error=f"{self._completed_segments}/{self._total_segments} segments completed",
                item=self._queue_active_item,
                info=active_info,
                out_dir=out_dir,
            )
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
                self._fire_hook(
                    "download_complete", title=title,
                    path=out_dir,
                    platform=active_info.platform if active_info else "")
                _db.mark_failed_jobs_resolved_for_url(self._active_history_url)
            self._save_metadata(
                out_dir,
                q_name,
                history_url=self._active_history_url,
                info=active_info,
            )
            self._media_server_import(out_dir, active_info)
            if self._queue_active_item is not None:
                self._release_queue_item("done")
        self._persist_config()
        self._run_lifecycle_cleanup()
        self._update_tray_badge()
        self._reset_speed_dashboard()
        self._start_next_background_job()

    def _on_stop(self):
        worker = self.download_worker
        resume_background_jobs = bool(
            self._queue_active_item is not None
            or self._autorecord_workers
            or self._autorecord_resolvers
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
        # Also stop any parallel auto-records. The stop button is a global
        # "halt everything the user is actively watching" — parallel lives
        # included. (Use the Monitor tab's per-row Stop+Remove for selective
        # stops.)
        for ch_id in list(self._autorecord_workers.keys()):
            w = self._autorecord_workers.get(ch_id)
            if w is not None and w.isRunning():
                try:
                    w.cancel()
                    if not w.wait(3000):
                        w.terminate()
                        w.wait(500)
                except Exception:
                    pass
        self._autorecord_workers.clear()
        self._autorecord_contexts.clear()
        for ch_id in list(self._autorecord_resolvers.keys()):
            w = self._autorecord_resolvers.get(ch_id)
            if w is not None and w.isRunning():
                try:
                    w.requestInterruption()
                    w.wait(1500)
                except Exception:
                    pass
        self._autorecord_resolvers.clear()
        # Stop any paired live-chat captures.
        for ch_id in list(self._chat_workers.keys()):
            w = self._chat_workers.get(ch_id)
            if w is not None and w.isRunning():
                try:
                    w.cancel()
                    w.wait(2000)
                except Exception:
                    pass
        self._chat_workers.clear()
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


    # ── Speed / ETA tracking ────────────────────────────────────

    def _init_speed_tracking(self):
        """Reset speed tracking state at the start of a download."""
        self._speed_samples = deque(maxlen=60)  # (timestamp, speed_bytes_per_sec)
        self._dl_start_time = time.monotonic()
        if hasattr(self, "download_speed_value"):
            self.download_speed_value.setText("—")
            self.download_speed_sub.setText("Waiting for data")
        if hasattr(self, "download_eta_value"):
            self.download_eta_value.setText("—")
            self.download_eta_sub.setText("Estimating...")

    def _update_speed_from_status(self, status):
        """Parse speed info from the progress status string and update the
        speed/ETA dashboard cards."""
        if not hasattr(self, "download_speed_value"):
            return
        # Try to extract a speed like "12.4MB/s" or "3.2MiB/s" from the status
        m = re.search(r'([\d.]+)\s*(B|KB|KiB|MB|MiB|GB|GiB)/s', status, re.IGNORECASE)
        if not m:
            return
        val = float(m.group(1))
        unit = m.group(2).upper().replace("I", "")
        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
        bps = val * multipliers.get(unit, 1)
        now = time.monotonic()
        self._speed_samples.append((now, bps))
        # Compute 5-second smoothed average
        cutoff = now - 5.0
        recent = [(t, s) for t, s in self._speed_samples if t >= cutoff]
        if recent:
            avg_speed = sum(s for _, s in recent) / len(recent)
        else:
            avg_speed = bps
        # Display speed
        self.download_speed_value.setText(_fmt_size(int(avg_speed)) + "/s")
        self.download_speed_sub.setText(f"5-sec avg ({len(recent)} samples)")
        # Calculate ETA from remaining segments
        total = getattr(self, "_total_segments", 0)
        done = getattr(self, "_completed_segments", 0)
        if total > 0 and done < total and avg_speed > 0:
            # Estimate bytes remaining from elapsed speed and segment ratio
            elapsed = now - getattr(self, "_dl_start_time", now)
            if elapsed > 0 and done > 0:
                est_total_time = elapsed * total / done
                remaining = est_total_time - elapsed
                if remaining > 0:
                    self.download_eta_value.setText(_fmt_duration(remaining))
                    self.download_eta_sub.setText(
                        f"{done}/{total} segments done"
                    )
                    return
            self.download_eta_value.setText("Estimating...")
        elif total > 0 and done >= total:
            self.download_eta_value.setText("Done")
            self.download_eta_sub.setText("Finalizing...")

    def _reset_speed_dashboard(self):
        """Clear speed/ETA cards after download completes."""
        if hasattr(self, "download_speed_value"):
            self.download_speed_value.setText("—")
            self.download_speed_sub.setText("Starts during download")
        if hasattr(self, "download_eta_value"):
            self.download_eta_value.setText("—")
            self.download_eta_sub.setText("Estimated time remaining")


    # ── Playlist / page scrape ──────────────────────────────────

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


    # ── Recover VOD ─────────────────────────────────────────────

    def _on_recover_vod(self):
        """Open the Deleted VOD Recovery Wizard dialog (F23)."""
        from ..recover_dialog import RecoverDialog
        dlg = RecoverDialog(self, log_fn=self._log)
        dlg.download_requested.connect(self._on_recover_download)
        dlg.exec()

    def _on_recover_download(self, url):
        """Handle a recovered VOD URL — paste into input and trigger fetch."""
        self.url_input.setText(url)
        self._on_fetch()


    # ── Batch URL import ────────────────────────────────────────

    def _on_batch_url_import(self):
        """Import URLs from a text file or clipboard paste and queue them (F44)."""
        from PyQt6.QtWidgets import (
            QDialog, QDialogButtonBox, QFileDialog, QPlainTextEdit,
            QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        )
        import re as _re

        dlg = QDialog(self)
        dlg.setWindowTitle("Batch URL Import")
        dlg.setMinimumSize(600, 400)
        layout = QVBoxLayout(dlg)

        hint = QLabel(
            "Paste URLs below (one per line) or load from a text file.\n"
            "Lines starting with # are comments and will be skipped."
        )
        layout.addWidget(hint)

        text_edit = QPlainTextEdit()
        text_edit.setPlaceholderText("https://twitch.tv/videos/123456\nhttps://kick.com/channel\n# this is a comment")
        layout.addWidget(text_edit)

        btn_row = QHBoxLayout()
        load_btn = QPushButton("Load from file...")
        load_btn.setObjectName("secondary")

        def _on_load_file():
            path, _ = QFileDialog.getOpenFileName(
                dlg, "Open URL list", "",
                "Text files (*.txt);;All files (*)",
            )
            if path:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        text_edit.setPlainText(f.read())
                except Exception as e:
                    self._set_status(f"Failed to read file: {e}", "error")

        load_btn.clicked.connect(_on_load_file)
        btn_row.addWidget(load_btn)

        status_label = QLabel("")
        btn_row.addWidget(status_label, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

        _url_re = _re.compile(r"^https?://\S+$", _re.IGNORECASE)

        def _update_count():
            lines = text_edit.toPlainText().strip().splitlines()
            valid = sum(1 for ln in lines if _url_re.match(ln.strip()))
            total = sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#"))
            invalid = total - valid
            parts = [f"{valid} valid URL(s)"]
            if invalid:
                parts.append(f"{invalid} invalid")
            status_label.setText("  ".join(parts))

        text_edit.textChanged.connect(_update_count)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        lines = text_edit.toPlainText().strip().splitlines()
        added = 0
        skipped = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not _url_re.match(line):
                skipped += 1
                continue
            ok = self._queue_add(url=line)
            if ok:
                added += 1
            else:
                skipped += 1

        self._refresh_queue_table()
        self._persist_config()
        msg = f"Queued {added} URL(s)"
        if skipped:
            msg += f", skipped {skipped} (invalid or duplicate)"
        self._set_status(msg, "success" if added else "warning")
        self._log(f"[BATCH] {msg}")


    # ── Queue URL / schedule ────────────────────────────────────

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

        def _validate_offset(value):
            try:
                minutes = int(value)
            except (TypeError, ValueError):
                return False, "Enter a whole number of minutes."
            if not 1 <= minutes <= 60 * 24 * 30:
                return False, "Choose a delay between 1 minute and 30 days."
            return True, ""

        offset_text, ok = ask_premium_text_input(
            self,
            title="Schedule this download",
            body="Delay the next capture so it starts later without leaving the queue unmanaged.",
            eyebrow="DOWNLOAD",
            badge_text="Scheduled",
            tone="info",
            summary_title="Use minutes for the delay.",
            summary_body="Examples: 60 for one hour, 180 for three hours, or 1440 for one day.",
            field_label="Start delay (minutes)",
            field_hint="The item will stay queued until its scheduled start time arrives.",
            placeholder="60",
            text="60",
            primary_label="Schedule download",
            secondary_label="Cancel",
            validator=_validate_offset,
        )
        if not ok:
            return
        offset_min = int(offset_text)
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


    # ── Queue management ────────────────────────────────────────

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
            "job_id": str(item.get("job_id", "") or ""),
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
        try:
            failure_id = int(item.get("failure_id", 0) or 0)
        except (TypeError, ValueError):
            failure_id = 0
        if failure_id:
            normalized["failure_id"] = failure_id
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

    def _active_queue_download_count(self):
        """Return the number of currently active queue downloads + fetches."""
        active = len([w for w in self._queue_workers.values() if w.isRunning()])
        active += len([w for w in self._queue_fetch_workers.values() if w.isRunning()])
        # Count the legacy single-worker path too
        if self._queue_active_item is not None:
            active += 1
        return active

    def _advance_queue(self):
        """Start the next queued item(s) up to the concurrent download limit.
        Scheduled items (start_at in the future) are skipped."""
        # Legacy foreground worker blocks legacy queue path
        worker = getattr(self, "download_worker", None)
        fg_busy = worker is not None and worker.isRunning()
        # Check concurrent capacity
        cap = max(1, int(self._max_concurrent_downloads))
        active = self._active_queue_download_count()
        if active >= cap:
            return
        # Also block if legacy single-worker fetch is running and there's
        # an active queue item using the old path
        if self._queue_active_item is not None and fg_busy:
            return
        now = datetime.now()
        ready = []
        active_ids = set(self._queue_workers.keys()) | set(self._queue_fetch_workers.keys())
        for q in self._download_queue:
            if q.get("status") != "queued":
                continue
            if id(q) in active_ids:
                continue
            start_at = q.get("start_at", "")
            if start_at:
                try:
                    ts = datetime.fromisoformat(start_at)
                    if ts > now:
                        continue
                except Exception:
                    pass
            ready.append(q)
            if active + len(ready) >= cap:
                break
        if not ready:
            return
        for item in ready:
            self._start_queue_item(item)

    def _start_queue_item(self, item):
        """Launch a fetch→download pipeline for a single queue item using
        a dedicated FetchWorker (concurrent, doesn't touch the UI state)."""
        item_id = id(item)
        self._set_queue_item_status(item, "fetching")
        self._log(f"[QUEUE] Starting: {item.get('title', '')[:60]}")
        # Use a dedicated FetchWorker that doesn't share the foreground UI state
        url = item.get("vod_source") or item["url"]
        fetch = FetchWorker(url)
        fetch.finished.connect(
            lambda info, it=item: self._on_queue_fetch_done(it, info)
        )
        fetch.error.connect(
            lambda err, it=item: self._on_queue_fetch_error(it, err)
        )
        self._queue_fetch_workers[item_id] = fetch
        fetch.start()

    def _on_queue_fetch_done(self, item, info):
        """Handle fetch completion for a concurrent queue item."""
        item_id = id(item)
        fw = self._queue_fetch_workers.pop(item_id, None)
        if fw and not fw.isRunning():
            try:
                fw.wait(200)
            except Exception:
                pass
        if info is None:
            job_id = self._record_failed_job(
                stage="fetch",
                error="Fetch returned no data",
                item=item,
            )
            if job_id:
                item["failure_id"] = job_id
            self._set_queue_item_status(item, "failed", "Fetch returned no data")
            self._log(f"[QUEUE] Fetch failed: {item.get('title', '')[:60]}")
            self._advance_queue()
            return
        # Pick the best quality
        q_data = None
        if info.qualities:
            q_data = info.qualities[0]  # Highest quality (pre-sorted)
        if q_data is None and not info.url:
            job_id = self._record_failed_job(
                stage="fetch",
                error="No playable quality",
                item=item,
                info=info,
            )
            if job_id:
                item["failure_id"] = job_id
            self._set_queue_item_status(item, "failed", "No playable quality")
            self._advance_queue()
            return
        # Build segments and output path
        playlist_url = q_data.url if q_data else info.url
        fmt_type = q_data.format_type if q_data else "hls"
        audio_url = q_data.audio_url if q_data else ""
        ytdlp_source = q_data.ytdlp_source if q_data else ""
        ytdlp_format = q_data.ytdlp_format if q_data else ""
        is_live = info.is_live or info.total_secs <= 0
        title_safe = _safe_filename(info.title or item.get("title") or "download")
        segments = [(0, title_safe, 0, 0 if is_live else int(info.total_secs))]
        ctx = _build_template_context(info)
        folder_parts = _render_template(self._folder_template, ctx)
        base_out = self.output_input.text().strip() or str(_default_output_dir())
        out_dir = os.path.join(base_out, *folder_parts) if folder_parts else base_out
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            job_id = self._record_failed_job(
                stage="download",
                error=f"Cannot create dir: {e}",
                item=item,
                info=info,
                out_dir=out_dir,
            )
            if job_id:
                item["failure_id"] = job_id
            self._set_queue_item_status(item, "failed", f"Cannot create dir: {e}")
            self._advance_queue()
            return
        # Create and start the DownloadWorker
        self._set_queue_item_status(item, "downloading")
        self._log(f"[QUEUE] Downloading: {info.title or item.get('title', '')[:60]} → {out_dir}")
        worker = DownloadWorker(playlist_url or "", segments, out_dir, format_type=fmt_type)
        worker.audio_url = audio_url
        worker.ytdlp_source = ytdlp_source
        worker.ytdlp_format = ytdlp_format
        worker.cookies_browser = YtDlpExtractor.cookies_browser
        # Share bandwidth across concurrent workers
        rl = YtDlpExtractor.rate_limit
        active_count = self._active_queue_download_count() + 1
        if rl and active_count > 1:
            try:
                import re as _re
                # Parse yt-dlp rate limit format: number + optional suffix
                m = _re.match(r'^([\d.]+)\s*([KkMmGg])?', str(rl))
                if m:
                    val = float(m.group(1))
                    suffix = (m.group(2) or "").upper()
                    multiplier = {"K": 1_000, "M": 1_000_000, "G": 1_000_000_000}.get(suffix, 1)
                    rl_bytes = int(val * multiplier)
                    shared = max(100_000, rl_bytes // active_count)
                    rl = str(shared)
            except (ValueError, AttributeError):
                pass
        worker.rate_limit = rl
        worker.proxy = YtDlpExtractor.proxy
        worker.download_subs = YtDlpExtractor.download_subs
        worker.sponsorblock = YtDlpExtractor.sponsorblock
        worker.parallel_connections = self._parallel_connections
        worker.log.connect(self._log)
        worker.all_done.connect(lambda it=item, inf=info: self._on_queue_item_done(it, inf, out_dir))
        worker.error.connect(lambda _idx, err, it=item: self._on_queue_item_error(it, err))
        self._queue_workers[item_id] = worker
        self._queue_contexts[item_id] = {
            "out_dir": out_dir, "info": info,
            "q_name": q_data.name if q_data else "",
        }
        worker.start()
        self._update_tray_badge()
        self._refresh_queue_table()
        # Try to fill remaining slots
        self._advance_queue()

    def _on_queue_fetch_error(self, item, err):
        """Handle fetch error for a concurrent queue item."""
        item_id = id(item)
        fw = self._queue_fetch_workers.pop(item_id, None)
        # Join the thread to prevent resource leaks (mirrors _on_queue_fetch_done)
        if fw:
            try:
                fw.wait(500)
            except Exception:
                pass
        job_id = self._record_failed_job(
            stage="fetch",
            error=err,
            item=item,
        )
        if job_id:
            item["failure_id"] = job_id
        self._set_queue_item_status(item, "failed", str(err)[:120])
        self._log(f"[QUEUE] Fetch error for {item.get('title', '')[:60]}: {err}")
        self._advance_queue()

    def _on_queue_item_done(self, item, info, out_dir):
        """Handle download completion for a concurrent queue item."""
        item_id = id(item)
        ctx = self._queue_contexts.pop(item_id, {})
        worker = self._queue_workers.pop(item_id, None)
        if worker and not worker.isRunning():
            try:
                worker.wait(500)
            except Exception:
                pass
        title = info.title if info else item.get("title", "Download")
        self._log(f"[QUEUE] Complete: {title[:60]}")
        self._notify_center(f"Queue download complete: {title[:50]}", "success")
        self._fire_hook("download_complete", title=title)
        # Save metadata + history entry
        q_name = ctx.get("q_name", "")
        self._save_metadata(out_dir, q_name, history_url=item.get("url", ""), info=info)
        self._media_server_import(out_dir, info)
        # Handle recurrence or mark done
        rec = (item.get("recurrence") or "").strip()
        if rec:
            next_fire = self._compute_next_fire(item)
            if next_fire:
                item["status"] = "queued"
                item["start_at"] = next_fire.isoformat()
                item["note"] = f"recurring ({rec}) — next fire {next_fire.strftime('%Y-%m-%d %H:%M')}"
            else:
                item["status"] = "done"
        else:
            item["status"] = "done"
        failure_id = int(item.get("failure_id", 0) or 0)
        if failure_id:
            _db.mark_failed_job_resolved(failure_id)
        _db.mark_failed_jobs_resolved_for_url(item.get("url", ""))
        self._queue_status_changed()
        self._update_tray_badge()
        self._advance_queue()

    def _on_queue_item_error(self, item, err):
        """Handle download error for a concurrent queue item."""
        item_id = id(item)
        ctx = self._queue_contexts.pop(item_id, {})
        worker = self._queue_workers.pop(item_id, None)
        if worker and not worker.isRunning():
            try:
                worker.wait(500)
            except Exception:
                pass
        job_id = self._record_failed_job(
            stage="download",
            error=err,
            item=item,
            out_dir=ctx.get("out_dir", "") if isinstance(ctx, dict) else "",
            info=ctx.get("info") if isinstance(ctx, dict) else None,
        )
        if job_id:
            item["failure_id"] = job_id
        self._set_queue_item_status(item, "failed", str(err)[:120])
        self._log(f"[QUEUE] Error: {item.get('title', '')[:60]} — {err}")
        self._advance_queue()

    def _refresh_queue_table(self):
        if not hasattr(self, "queue_table"):
            return
        queue_count = len(self._download_queue)
        self.queue_table.setRowCount(queue_count)
        if hasattr(self, "queue_empty_state"):
            self.queue_empty_state.setVisible(queue_count == 0)
        if queue_count:
            self.queue_table.setMinimumHeight(220)
            self.queue_table.setMaximumHeight(16777215)
        else:
            self.queue_table.setMinimumHeight(48)
            self.queue_table.setMaximumHeight(58)
        now = datetime.now()
        for i, q in enumerate(self._download_queue):
            # Compute effective status: "scheduled" if start_at is in the future
            status = q.get("status", "queued")
            locked = (
                q is self._queue_active_item
                or id(q) in self._queue_workers
                or id(q) in self._queue_fetch_workers
                or status in ("fetching", "downloading")
            )
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
        self._refresh_shell_overview()

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

    @staticmethod
    def _quality_rank(quality_str):
        """Parse a quality string like '1080p', 'source', '720p60' into a
        numeric rank for comparison.  Higher is better."""
        if not quality_str:
            return 0
        q = quality_str.lower().strip()
        if q in ("source", "best", "highest"):
            return 9999
        digits = ""
        for c in q:
            if c.isdigit():
                digits += c
            elif digits:
                break
        return int(digits) if digits else 0


    # ── Queue context menu ──────────────────────────────────────

    def _on_queue_context_menu(self, pos):
        if not hasattr(self, "queue_table"):
            return
        idx = self.queue_table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        if not (0 <= row < len(self._download_queue)):
            return
        item = self._download_queue[row]
        menu = QMenu(self)
        current = (item.get("recurrence") or "").strip().lower() or "(one-shot)"
        header = menu.addAction(f"Recurrence: {current}")
        header.setEnabled(False)
        menu.addSeparator()
        retry_failure = None
        discard_failure = None
        failure_id = int(item.get("failure_id", 0) or 0)
        if item.get("status") == "failed" and failure_id:
            failure_header = menu.addAction(f"Failure #{failure_id}")
            failure_header.setEnabled(False)
            retry_failure = menu.addAction("Retry failed job")
            discard_failure = menu.addAction("Discard failure")
            menu.addSeparator()
        one_shot = menu.addAction("One-shot (no recurrence)")
        daily = menu.addAction("Daily")
        weekly = menu.addAction("Weekly")
        custom = menu.addAction("Weekday mask... (mon,tue,fri)")
        chosen = menu.exec(self.queue_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if retry_failure is not None and chosen == retry_failure:
            self._retry_failed_job(failure_id)
            return
        if discard_failure is not None and chosen == discard_failure:
            self._discard_failed_job(failure_id)
            return
        new_rec = ""
        if chosen == one_shot:
            new_rec = ""
        elif chosen == daily:
            new_rec = "daily"
        elif chosen == weekly:
            new_rec = "weekly"
        elif chosen == custom:
            valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}

            def _validate_mask(value):
                tokens = [tok.strip().lower() for tok in (value or "").split(",") if tok.strip()]
                if not tokens:
                    return False, "Enter at least one weekday code."
                invalid = [tok for tok in tokens if tok not in valid_days]
                if invalid:
                    return False, "Use day codes like mon,tue,wed,thu,fri,sat,sun."
                return True, ""

            text, ok = ask_premium_text_input(
                self,
                title="Repeat on specific weekdays",
                body="Use a weekday mask when a queued item should recur only on selected days.",
                eyebrow="QUEUE",
                badge_text="Recurrence",
                tone="info",
                summary_title="Comma-separated short codes are supported.",
                summary_body="Example: mon,wed,fri. Use one-shot or weekly if the item should not use a custom mask.",
                field_label="Weekday mask",
                field_hint="Supported values: mon, tue, wed, thu, fri, sat, sun.",
                text=item.get("recurrence", "") or "mon,wed,fri",
                primary_label="Save recurrence",
                secondary_label="Cancel",
                validator=_validate_mask,
            )
            if not ok:
                return
            tokens = [tok.strip().lower() for tok in text.split(",") if tok.strip()]
            new_rec = ",".join(tokens)
        item["recurrence"] = new_rec
        if new_rec:
            item["note"] = f"recurring ({new_rec})"
        else:
            item["note"] = ""
        self._queue_status_changed()
        self._set_status(
            f"Queue item recurrence set to '{new_rec or 'one-shot'}'.",
            "success",
        )

    # _on_theme_changed, _on_companion_toggled, _on_companion_scope_toggled,
    # _copy_text_to_clipboard, _companion_local_url, _refresh_companion_ui,
    # _on_copy_companion_url, _on_copy_companion_token,
    # _on_open_companion_remote
    #   -> streamkeep.ui.tabs.settings.SettingsTabMixin


    # ── Download context / helpers ──────────────────────────────

    def _set_download_context(self, out_dir="", quality_name="", history_url="", info=None):
        self._active_output_dir = out_dir
        self._active_quality_name = quality_name
        self._active_history_url = history_url
        self._active_stream_info = info or self.stream_info

    def _attach_resume_to_worker(self, worker, *, resume_existing=None, context=None):
        """Wire a ResumeState into a DownloadWorker just before start().

        If `resume_existing` is provided (from the startup-banner "Resume"
        action), its completed-segments list is preserved and re-used.
        Otherwise a fresh state is built from `context` (dict with
        source_url/platform/title/channel/quality_name keys) or, if None,
        from `self._active_*` for backwards-compat with the foreground path.
        Silent on failure — a resume sidecar is nice-to-have, never required.
        """
        try:
            if context is None:
                info = self._active_stream_info or self.stream_info
                source_url = self._active_history_url or ""
                platform = (info.platform if info else "") or ""
                title = (info.title if info else "") or ""
                channel = (info.channel if info else "") or ""
                quality_name = self._active_quality_name or ""
            else:
                info = context.get("info")
                source_url = context.get("source_url") or ""
                platform = context.get("platform") or (info.platform if info else "") or ""
                title = context.get("title") or (info.title if info else "") or ""
                channel = context.get("channel") or (info.channel if info else "") or ""
                quality_name = context.get("quality_name") or ""
            if resume_existing is not None:
                state = resume_existing
                # Refresh the parts of the sidecar that can have changed
                # between the interrupted run and now (new token URLs, etc.).
                state.playlist_url = worker.playlist_url
                state.format_type = worker.format_type
                state.audio_url = worker.audio_url or ""
                state.ytdlp_source = worker.ytdlp_source or ""
                state.ytdlp_format = worker.ytdlp_format or ""
                state.output_dir = worker.output_dir
                state.segments = [list(s) for s in worker.segments]
            else:
                state = ResumeState(
                    source_url=source_url,
                    platform=platform,
                    title=title,
                    channel=channel,
                    quality_name=quality_name,
                    output_dir=worker.output_dir,
                )
            worker.attach_resume_state(state)
        except Exception as e:
            self._log(f"[RESUME] Could not write sidecar: {e}")

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
        snap = {k: getattr(PostProcessor, k) for k in keys}
        # Apply per-download PP preset override (F18)
        overrides = getattr(self, "_dl_overrides", {})
        preset_name = overrides.get("pp_preset", "")
        if preset_name:
            from .settings import BUILTIN_PRESETS, _get_user_presets
            all_presets = dict(BUILTIN_PRESETS)
            all_presets.update(_get_user_presets(self))
            if preset_name in all_presets:
                snap.update(all_presets[preset_name])
        return snap

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

    def _choose_default_quality_index(self, qualities, platform):
        """Resolve the user's per-platform default to an index into the
        given quality list. Fallbacks:

          pref "1080p" -> first quality whose name/resolution contains "1080"
          pref "720p"  -> first 720
          pref "source"/"highest"/"" -> 1080-or-source heuristic (legacy)
          pref "lowest"  -> last (qualities are typically sorted high→low)
        """
        prefs = self._config.get("quality_defaults") or {}
        pkey = (platform or "").strip().lower() or "other"
        pref = (prefs.get(pkey) or prefs.get("other") or "").strip().lower()
        if not pref or pref in ("highest", "source"):
            # Legacy behaviour: prefer 1080 or "source" if present, else 0.
            for i, q in enumerate(qualities):
                if "1080" in q.name or "source" in q.name.lower():
                    return i
            return 0
        if pref == "lowest":
            return len(qualities) - 1
        for i, q in enumerate(qualities):
            cname = (q.name or "").lower()
            cres = (q.resolution or "").lower()
            if pref in cname or pref in cres:
                return i
        # No match — fall back to the legacy heuristic.
        for i, q in enumerate(qualities):
            if "1080" in q.name or "source" in q.name.lower():
                return i
        return 0

    def _preflight_disk_space(self):
        """Show a confirm dialog when the estimated download size is more
        than 80% of free space. Returns True if the user wants to proceed
        (or the check is inapplicable), False to abort.

        Lives and unknown-duration streams pass through silently since we
        can't estimate them meaningfully.
        """
        try:
            out_dir = self.output_input.text().strip() or str(_default_output_dir())
            free = _free_space_bytes(out_dir)
            estimate = _estimate_download_bytes(self.stream_info)
            if not free or not estimate or estimate <= 0:
                return True
            if estimate <= free * 0.8:
                return True
            return ask_premium_confirmation(
                self,
                title="Low free space on the output drive",
                body=(
                    f"This download may need about {_fmt_size(estimate)}, but only "
                    f"{_fmt_size(free)} is currently free in the output location."
                ),
                eyebrow="PREFLIGHT",
                badge_text="Capacity risk",
                tone="warning",
                summary_title="Continuing could leave you with a partial or failed download.",
                summary_body="Free up space first if you want the safest path.",
                details_title="Capacity estimate",
                details_body=(
                    f"Estimated download size: {_fmt_size(estimate)}\n"
                    f"Free space available: {_fmt_size(free)}\n"
                    f"Output folder: {out_dir}"
                ),
                primary_label="Continue anyway",
                secondary_label="Cancel",
                default_action="secondary",
                min_width=620,
            )
        except Exception as e:
            self._log(f"[PREFLIGHT] disk-space check failed: {e}")
            return True


    # ── Queue status helpers ────────────────────────────────────

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

    def _failure_resume_sidecar(self, out_dir):
        if not out_dir:
            return ""
        path = os.path.join(out_dir, ".streamkeep_resume.json")
        return path if os.path.isfile(path) else ""

    def _record_failed_job(
        self,
        *,
        stage,
        error,
        item=None,
        info=None,
        out_dir="",
        queue_data=None,
    ):
        """Persist one failed fetch/download/finalize job for recovery."""
        item = item or {}
        queue_seed = dict(queue_data or {})
        info = info or getattr(self, "_active_stream_info", None) or self.stream_info
        source_url = (
            item.get("url")
            or item.get("vod_source")
            or queue_seed.get("url")
            or queue_seed.get("vod_source")
            or self._resolve_history_url()
            or (self.url_input.text().strip() if hasattr(self, "url_input") else "")
        )
        platform = item.get("platform") or queue_seed.get("platform") or (info.platform if info else "")
        title = item.get("title") or queue_seed.get("title") or (info.title if info else "") or source_url
        output_dir = out_dir or getattr(self, "_active_output_dir", "") or (
            self.output_input.text().strip() if hasattr(self, "output_input") else ""
        )
        q_data = dict(queue_seed or item or {})
        if source_url and "url" not in q_data:
            q_data["url"] = source_url
        if title and "title" not in q_data:
            q_data["title"] = title
        if platform and "platform" not in q_data:
            q_data["platform"] = platform
        try:
            job_id = _db.save_failed_job(
                url=source_url,
                platform=platform,
                title=title,
                stage=stage,
                error=str(error or ""),
                output_dir=output_dir,
                resume_sidecar=self._failure_resume_sidecar(output_dir),
                queue_data=q_data,
                context={
                    "quality_name": getattr(self, "_active_quality_name", ""),
                    "completed_segments": getattr(self, "_completed_segments", 0),
                    "total_segments": getattr(self, "_total_segments", 0),
                },
            )
            if item is not None and job_id:
                item["failure_id"] = job_id
            return job_id
        except Exception as e:
            self._log(f"[RECOVERY] Could not save failed-job record: {e}")
            return 0

    def _retry_failed_job(self, job_id):
        job = _db.load_failed_job(job_id)
        if job and job.get("status") != "retrying":
            job = _db.mark_failed_job_retrying(job_id)
        if not job:
            self._set_status("Failed-job record was not found.", "warning")
            return False
        queue_data = dict(job.get("queue_data") or {})
        queue_data["status"] = "queued"
        queue_data["note"] = f"retry #{job.get('retry_count', 0)}"
        queue_data["failure_id"] = int(job.get("id", 0) or 0)
        if not queue_data.get("url"):
            queue_data["url"] = job.get("url", "")
        if not queue_data.get("title"):
            queue_data["title"] = job.get("title", "") or job.get("url", "")
        if not queue_data.get("platform"):
            queue_data["platform"] = job.get("platform", "") or "?"
        normalized = self._normalize_queue_item(queue_data)
        if normalized is None:
            self._set_status("Failed job has no retryable URL.", "warning")
            return False
        normalized["failure_id"] = int(job.get("id", 0) or 0)
        existing = None
        for q in self._download_queue:
            if int(q.get("failure_id", 0) or 0) == normalized["failure_id"]:
                existing = q
                break
        if existing is None:
            self._download_queue.append(normalized)
        else:
            existing.update(normalized)
        self._queue_status_changed()
        self._set_status(f"Retry queued: {normalized.get('title', '')[:60]}", "success")
        self._advance_queue()
        return True

    def _discard_failed_job(self, job_id):
        _db.mark_failed_job_discarded(job_id)
        removed = 0
        kept = []
        for q in self._download_queue:
            if int(q.get("failure_id", 0) or 0) == int(job_id) and q.get("status") == "failed":
                removed += 1
                continue
            kept.append(q)
        if removed:
            self._download_queue = kept
            self._queue_status_changed()
        self._set_status("Failed-job recovery item discarded.", "success")

    def _release_queue_item(self, status=None, note=""):
        item = self._queue_active_item
        self._queue_active_item = None
        self._queue_autostart = False
        if item is None:
            return
        if status == "done":
            # Recurring items re-schedule themselves for the next window
            # instead of being removed — this is what turns the queue into
            # "DVR my weekly show" instead of "one-off".
            recurrence = (item.get("recurrence") or "").strip().lower()
            next_fire = self._compute_next_fire(recurrence, item.get("start_at", ""))
            if next_fire:
                item["status"] = "queued"
                item["start_at"] = next_fire.isoformat(timespec="minutes")
                item["note"] = f"recurring ({recurrence}) — next fire {next_fire.strftime('%a %H:%M')}"
                self._log(
                    f"[QUEUE] Recurring item rescheduled for "
                    f"{next_fire.strftime('%Y-%m-%d %H:%M')}: "
                    f"{(item.get('title') or item.get('url', ''))[:60]}"
                )
                self._queue_status_changed()
                return
            try:
                self._download_queue.remove(item)
            except ValueError:
                pass
            self._queue_status_changed()
            return
        if status:
            self._set_queue_item_status(item, status, note)

    def _compute_next_fire(self, recurrence, start_at_iso):
        """Return the datetime of the next fire for a recurring queue
        item, or None for one-shot / unparseable recurrence strings.

        Accepted shapes (case-insensitive):
          "daily"          every 24h from last fire
          "weekly"         every 7 days from last fire
          "mon,wed,fri"    next occurrence on one of the named days
        Days use first 3 letters; any subset of mon/tue/wed/thu/fri/sat/sun.
        """
        if not recurrence:
            return None
        try:
            last = datetime.fromisoformat(start_at_iso) if start_at_iso else datetime.now()
        except Exception:
            last = datetime.now()
        now = datetime.now()
        # If the last fire was in the past we pivot off "now" so we never
        # backfill missed windows.
        base = max(last, now)
        if recurrence == "daily":
            return base + timedelta(days=1)
        if recurrence == "weekly":
            return base + timedelta(days=7)
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        wanted = []
        for part in recurrence.replace(" ", "").split(","):
            if part in day_map:
                wanted.append(day_map[part])
        if not wanted:
            return None
        wanted.sort()
        # Keep the wall-clock time of day from the original start_at.
        target_time = last.time() if start_at_iso else now.time()
        today_wd = now.weekday()
        for offset in range(1, 8):
            candidate_wd = (today_wd + offset) % 7
            if candidate_wd in wanted:
                target_date = now.date() + timedelta(days=offset)
                return datetime.combine(target_date, target_time)
        return None


    # ── Finalize pipeline ───────────────────────────────────────

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
            history_entry = self._add_history(
                result.get("platform", "?"),
                result.get("title", "?"),
                result.get("quality_name", ""),
                result.get("size_label", self._output_size_label(result.get("out_dir", ""))),
                result.get("out_dir", ""),
                channel=result.get("channel", ""),
                url=result.get("history_url", ""),
            )
            manifest = result.get("archive_manifest")
            if history_entry is not None and manifest:
                try:
                    _db.save_archive_manifest(
                        history_entry.db_id,
                        history_entry.path,
                        manifest,
                        status="created",
                        details=(
                            f"Captured {len(manifest.get('files', []) or [])} "
                            "file(s)"
                        ),
                    )
                except Exception as e:
                    self._log(f"[VERIFY] Could not save integrity manifest: {e}")
            elif result.get("archive_manifest_error"):
                self._log(
                    "[VERIFY] Integrity manifest was not saved: "
                    f"{result.get('archive_manifest_error')}"
                )
            finalize_error = result.get("finalize_error") or result.get("archive_manifest_error")
            if finalize_error:
                self._record_failed_job(
                    stage="finalize",
                    error=finalize_error,
                    out_dir=result.get("out_dir", ""),
                    queue_data={
                        "url": result.get("history_url", ""),
                        "title": result.get("title", ""),
                        "platform": result.get("platform", "?"),
                    },
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


    # ── History / duplicate ─────────────────────────────────────

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

    @staticmethod
    def _title_token_overlap(a, b):
        """Return the fraction of shared tokens between two titles (0..1)."""
        ta = set(a.strip().lower().split())
        tb = set(b.strip().lower().split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / max(len(ta), len(tb))

    def _find_duplicate(self, url, title="", platform="", duration_secs=0):
        """Check history for a matching URL (exact), exact title, or fuzzy
        metadata match (channel + title token overlap >= 70%). Returns
        matching HistoryEntry or None."""
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
            # Fuzzy title-token overlap (F40)
            for h in self._history:
                if not h.title or not h.path:
                    continue
                if (platform and h.platform
                        and h.platform.strip().lower() != platform.strip().lower()):
                    continue
                if self._title_token_overlap(title, h.title) >= 0.70:
                    return h
        return None


    # ── Metadata / trim ─────────────────────────────────────────

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
        # Auto-tag recording (F35)
        try:
            from ...tags import _connect, auto_tag_recording
            db = _connect()
            auto_tag_recording(db, out_dir, info=info_copy)
            db.close()
        except Exception:
            pass
        # Index transcripts for this recording (F27)
        try:
            from ...search import index_recording
            index_recording(out_dir)
        except Exception:
            pass

    def _on_trim_last(self):
        """Open the trim dialog for the most-recently-finished download."""
        out_dir = self._active_output_dir or self.output_input.text().strip()
        if not out_dir or not os.path.isdir(out_dir):
            self._set_status("No recent download folder to trim.", "warning")
            return
        self._open_clip_dialog_for_dir(out_dir)

    # Monitor actions → streamkeep.ui.tabs.monitor.MonitorTabMixin


    # ── Browser companion ───────────────────────────────────────

    def _on_companion_url(self, url, action):
        """The extension just POSTed a URL. Route it through the Fetch
        path or queue it immediately depending on action."""
        self._log(f"[COMPANION] Received {action.upper()} for {url[:80]}")
        self._present_main_window(0)
        try:
            self.url_input.setText(url)
        except Exception:
            pass
        if action == "queue":
            try:
                added = self._queue_add(url, title="", platform="")
                if added:
                    self._set_status(f"Queued via browser extension: {url[:80]}", "success")
                else:
                    self._set_status("That browser handoff is already in the queue.", "warning")
            except Exception as e:
                self._log(f"[COMPANION] Queue failed: {e}")
        else:
            self._on_fetch()
