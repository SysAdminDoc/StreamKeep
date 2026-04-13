"""Monitor tab — channel watch list with live detection + auto-record."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTableWidget, QVBoxLayout, QWidget,
)

from ..widgets import make_field_block, make_metric_card, style_table


def build_monitor_tab(win):
    """Build the Monitor tab page. Stashes widget refs on `win.*`."""
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
    body = QLabel(
        "Track supported channels, watch live state changes, and "
        "automatically start recording when they go live."
    )
    body.setObjectName("heroBody")
    body.setWordWrap(True)
    hero_copy.addWidget(kicker)
    hero_copy.addWidget(title)
    hero_copy.addWidget(body)
    hero_lay.addLayout(hero_copy)

    monitor_metrics = QHBoxLayout()
    monitor_metrics.setSpacing(12)
    count_card, win.monitor_count_value, win.monitor_count_sub = make_metric_card(
        "Channels", "0", "active entries"
    )
    auto_card, win.monitor_auto_value, win.monitor_auto_sub = make_metric_card(
        "Auto Record", "0", "auto-record enabled"
    )
    live_card, win.monitor_live_value, win.monitor_live_sub = make_metric_card(
        "Live Now", "0", "currently live"
    )
    monitor_metrics.addWidget(count_card)
    monitor_metrics.addWidget(auto_card)
    monitor_metrics.addWidget(live_card)
    hero_lay.addLayout(monitor_metrics)
    lay.addWidget(hero)

    # Active Recordings panel — hidden when empty. Populated by
    # StreamKeep._refresh_active_recordings_panel as workers start/finish.
    win.active_recordings_panel = QFrame()
    win.active_recordings_panel.setObjectName("activeRecordings")
    win.active_recordings_panel.setVisible(False)
    ar_lay = QVBoxLayout(win.active_recordings_panel)
    ar_lay.setContentsMargins(16, 12, 16, 12)
    ar_lay.setSpacing(6)
    win.active_recordings_header = QLabel("Active recordings")
    win.active_recordings_header.setObjectName("sectionTitle")
    ar_lay.addWidget(win.active_recordings_header)
    win.active_recordings_rows_layout = QVBoxLayout()
    win.active_recordings_rows_layout.setSpacing(4)
    ar_lay.addLayout(win.active_recordings_rows_layout)
    lay.addWidget(win.active_recordings_panel)

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

    url_block, url_block_lay = make_field_block(
        "Channel URL", "Paste the channel link you want StreamKeep to poll."
    )
    win.monitor_url_input = QLineEdit()
    win.monitor_url_input.setPlaceholderText("Channel URL (kick.com/user, twitch.tv/user)")
    url_block_lay.addWidget(win.monitor_url_input)
    controls_row.addWidget(url_block, 1)

    interval_block, interval_block_lay = make_field_block(
        "Check Every", "Polling interval"
    )
    win.monitor_interval_spin = QSpinBox()
    win.monitor_interval_spin.setRange(30, 600)
    win.monitor_interval_spin.setValue(120)
    win.monitor_interval_spin.setSuffix("s")
    interval_block_lay.addWidget(win.monitor_interval_spin)
    controls_row.addWidget(interval_block)

    auto_block, auto_block_lay = make_field_block(
        "Automation", "Live auto-record + VOD subscription"
    )
    win.monitor_auto_cb = QCheckBox("Enable auto-record (live)")
    auto_block_lay.addWidget(win.monitor_auto_cb)
    win.monitor_subscribe_cb = QCheckBox("Subscribe — queue new VODs")
    auto_block_lay.addWidget(win.monitor_subscribe_cb)
    auto_block_lay.addStretch(1)
    controls_row.addWidget(auto_block)

    add_btn = QPushButton("Add Channel")
    add_btn.setObjectName("primary")
    add_btn.clicked.connect(win._on_monitor_add)
    controls_row.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignBottom)
    manage_lay.addLayout(controls_row)

    win.monitor_summary_label = QLabel(
        "Add a channel URL to start passive live monitoring."
    )
    win.monitor_summary_label.setObjectName("subtleText")
    manage_lay.addWidget(win.monitor_summary_label)

    # Import/Export row (F10)
    io_row = QHBoxLayout()
    io_row.setSpacing(8)
    export_btn = QPushButton("Export Channels...")
    export_btn.setObjectName("secondary")
    export_btn.setFixedWidth(140)
    export_btn.clicked.connect(win._on_monitor_export)
    io_row.addWidget(export_btn)
    import_btn = QPushButton("Import Channels...")
    import_btn.setObjectName("secondary")
    import_btn.setFixedWidth(140)
    import_btn.clicked.connect(win._on_monitor_import)
    io_row.addWidget(import_btn)
    io_row.addStretch(1)
    manage_lay.addLayout(io_row)

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
    table_hint = QLabel(
        "Entries refresh automatically and can trigger auto-recording "
        "when a stream goes live."
    )
    table_hint.setObjectName("sectionBody")
    table_hint.setWordWrap(True)
    table_header.addWidget(table_title)
    table_header.addWidget(table_hint)
    table_lay.addLayout(table_header)

    win.monitor_table = QTableWidget()
    win.monitor_table.setColumnCount(6)
    win.monitor_table.setHorizontalHeaderLabels(
        ["Platform", "Channel", "Status", "Interval", "Auto-Record", ""]
    )
    mh = win.monitor_table.horizontalHeader()
    mh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    mh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    mh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    mh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    mh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    mh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
    win.monitor_table.setColumnWidth(0, 84)
    win.monitor_table.setColumnWidth(2, 90)
    win.monitor_table.setColumnWidth(3, 84)
    win.monitor_table.setColumnWidth(4, 108)
    win.monitor_table.setColumnWidth(5, 110)
    win.monitor_table.verticalHeader().setVisible(False)
    win.monitor_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.monitor_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    style_table(win.monitor_table, 44)
    table_lay.addWidget(win.monitor_table)

    lay.addWidget(table_card, 1)
    win._refresh_monitor_summary()
    return page
