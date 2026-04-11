"""Download tab — URL input, VOD picker, segments table, queue, log."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSplitter, QTableWidget, QTextEdit, QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QStringListModel

from ...utils import default_output_dir as _default_output_dir
from ..widgets import make_field_block, make_metric_card, path_label, style_table


def build_download_tab(win):
    """Build the Download tab page. Stashes widget refs on `win.*`."""
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
    win.download_hero_title = QLabel("Capture streams and VODs with cleaner control")
    win.download_hero_title.setObjectName("heroTitle")
    win.download_hero_title.setWordWrap(True)
    win.download_hero_body = QLabel(
        "Paste a source URL to inspect quality options, split recordings "
        "into segments, and keep output folders tidy."
    )
    win.download_hero_body.setObjectName("heroBody")
    win.download_hero_body.setWordWrap(True)
    hero_copy.addWidget(hero_kicker)
    hero_copy.addWidget(win.download_hero_title)
    hero_copy.addWidget(win.download_hero_body)
    hero_top.addLayout(hero_copy, 1)

    source_card, win.download_platform_value, win.download_platform_sub = make_metric_card(
        "Source", "Auto detect", "Waiting for a supported URL"
    )
    source_card.setMaximumWidth(190)
    hero_top.addWidget(source_card)
    hero_lay.addLayout(hero_top)

    metrics_row = QHBoxLayout()
    metrics_row.setSpacing(12)
    duration_card, win.download_duration_value, win.download_duration_sub = make_metric_card(
        "Duration", "Waiting", "Metadata not loaded yet"
    )
    selection_card, win.download_selection_value, win.download_selection_sub = make_metric_card(
        "Selection", "Not ready", "segments appear after fetch"
    )
    output_card, win.download_output_value, win.download_output_sub = make_metric_card(
        "Output", path_label(str(_default_output_dir())), ""
    )
    finalize_card, win.download_finalize_value, win.download_finalize_sub = make_metric_card(
        "Finalize", "Idle", "Metadata and post-processing will queue here"
    )
    metrics_row.addWidget(duration_card)
    metrics_row.addWidget(selection_card)
    metrics_row.addWidget(output_card, 1)
    metrics_row.addWidget(finalize_card)
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
        "Inspect a channel, VOD, or direct media URL to unlock quality "
        "choices and segment controls."
    )
    sec1_body.setObjectName("sectionBody")
    sec1_body.setWordWrap(True)
    url_header.addWidget(sec1)
    url_header.addWidget(sec1_body)
    url_lay.addLayout(url_header)

    url_row = QHBoxLayout()
    url_row.setSpacing(10)
    win.url_input = QLineEdit()
    win.url_input.setPlaceholderText(
        "Paste a URL: kick.com/user, twitch.tv/user, rumble.com/v..., or any video URL"
    )
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
    win.platform_badge.setFixedHeight(36)
    win.platform_badge.setMinimumWidth(96)
    win.platform_badge.setVisible(False)
    win.platform_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    url_row.addWidget(win.platform_badge)

    win.fetch_btn = QPushButton("Fetch")
    win.fetch_btn.setObjectName("primary")
    win.fetch_btn.setFixedWidth(100)
    win.fetch_btn.clicked.connect(win._on_fetch)
    url_row.addWidget(win.fetch_btn)

    win.expand_btn = QPushButton("Expand Playlist")
    win.expand_btn.setObjectName("secondary")
    win.expand_btn.setFixedWidth(130)
    win.expand_btn.setToolTip("For playlist/channel URLs, queue every video via yt-dlp")
    win.expand_btn.clicked.connect(win._on_expand_playlist)
    url_row.addWidget(win.expand_btn)

    win.scan_btn = QPushButton("Scan Page")
    win.scan_btn.setObjectName("secondary")
    win.scan_btn.setFixedWidth(110)
    win.scan_btn.setToolTip("Fetch the URL as HTML and extract all video/media links it references")
    win.scan_btn.clicked.connect(win._on_scan_page)
    url_row.addWidget(win.scan_btn)

    win.clip_btn = QPushButton("Clipboard Watch")
    win.clip_btn.setObjectName("toggleAccent")
    win.clip_btn.setCheckable(True)
    win.clip_btn.setFixedWidth(148)
    win.clip_btn.clicked.connect(win._on_toggle_clipboard)
    url_row.addWidget(win.clip_btn)
    url_lay.addLayout(url_row)

    url_hint = QLabel("Press Enter to fetch. Clipboard watch auto-loads the next copied URL.")
    url_hint.setObjectName("subtleText")
    url_lay.addWidget(url_hint)

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
    win.vod_table.setColumnCount(5)
    win.vod_table.setHorizontalHeaderLabels(["", "Platform", "Title", "Date", "Duration"])
    vh = win.vod_table.horizontalHeader()
    vh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    vh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    vh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    vh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
    vh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    win.vod_table.setColumnWidth(0, 36)
    win.vod_table.setColumnWidth(1, 84)
    win.vod_table.setColumnWidth(3, 160)
    win.vod_table.setColumnWidth(4, 96)
    win.vod_table.verticalHeader().setVisible(False)
    win.vod_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.vod_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    win.vod_table.setMaximumHeight(220)
    style_table(win.vod_table, 42)
    vod_main_lay.addWidget(win.vod_table)

    vod_btn_row = QHBoxLayout()
    vod_btn_row.addStretch(1)
    win.vod_load_btn = QPushButton("Load Selected")
    win.vod_load_btn.setObjectName("secondary")
    win.vod_load_btn.clicked.connect(win._on_vod_load_single)
    vod_btn_row.addWidget(win.vod_load_btn)
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
    controls_card.setObjectName("card")
    controls_lay = QGridLayout(controls_card)
    controls_lay.setContentsMargins(18, 18, 18, 18)
    controls_lay.setHorizontalSpacing(12)
    controls_lay.setVerticalSpacing(12)

    quality_block, quality_lay = make_field_block(
        "Quality", "Choose the best available rendition after metadata loads."
    )
    win.quality_combo = QComboBox()
    win.quality_combo.setEnabled(False)
    win.quality_combo.currentIndexChanged.connect(win._on_quality_changed)
    quality_lay.addWidget(win.quality_combo)
    controls_lay.addWidget(quality_block, 0, 0)

    segment_block, segment_lay = make_field_block(
        "Segment Length", "Split long recordings into predictable export chunks."
    )
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
    controls_lay.addWidget(segment_block, 0, 1)

    output_block, output_lay = make_field_block(
        "Output Folder", "Downloads are saved exactly where you point the app."
    )
    output_row = QHBoxLayout()
    output_row.setSpacing(8)
    win.output_input = QLineEdit(str(_default_output_dir()))
    win.output_input.textChanged.connect(win._refresh_download_summary)
    output_row.addWidget(win.output_input, 1)
    browse_btn = QPushButton("Browse")
    browse_btn.setObjectName("secondary")
    browse_btn.clicked.connect(win._on_browse)
    output_row.addWidget(browse_btn)
    output_lay.addLayout(output_row)
    controls_lay.addWidget(output_block, 1, 0, 1, 2)
    controls_lay.setColumnStretch(0, 1)
    controls_lay.setColumnStretch(1, 1)
    root.addWidget(controls_card)

    # Splitter: segments table + runtime log
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
    clear_log_btn.clicked.connect(lambda: win.log_text.clear())
    log_header.addWidget(clear_log_btn)
    log_lay.addLayout(log_header)

    win.log_text = QTextEdit()
    win.log_text.setObjectName("log")
    win.log_text.setReadOnly(True)
    log_lay.addWidget(win.log_text)
    splitter.addWidget(log_frame)
    splitter.setSizes([450, 220])
    root.addWidget(splitter, 1)

    # Download action row
    dl_row = QHBoxLayout()
    dl_hint = QLabel("Download the selected segments after the source is ready.")
    dl_hint.setObjectName("subtleText")
    dl_row.addWidget(dl_hint)
    dl_row.addStretch(1)
    win.queue_btn = QPushButton("Queue URL")
    win.queue_btn.setObjectName("secondary")
    win.queue_btn.setToolTip("Add the current URL to the download queue instead of downloading now")
    win.queue_btn.clicked.connect(win._on_queue_url)
    dl_row.addWidget(win.queue_btn)
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
    win.queue_table.setMaximumHeight(180)
    style_table(win.queue_table, 36)
    qcard_lay.addWidget(win.queue_table)
    root.addWidget(queue_card)

    win._refresh_download_summary()
    win._refresh_queue_table()

    return page
