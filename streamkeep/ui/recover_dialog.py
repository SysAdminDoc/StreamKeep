"""Deleted VOD Recovery Wizard — dialog for recovering expired Twitch VODs (F23).

User enters a channel name and month/year. The wizard scrapes tracker sites
for stream metadata, brute-forces CDN URL patterns, and lists recoverable
VODs that can be sent to the download pipeline.
"""

import datetime

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class _RecoverWorker(QThread):
    """Run recovery in background thread."""
    progress = pyqtSignal(int, str)
    done = pyqtSignal(list)  # list of StreamInfo

    def __init__(self, channel, year, month, log_fn=None):
        super().__init__()
        self.channel = channel
        self.year = year
        self.month = month
        self._log_fn = log_fn

    def run(self):
        from ..extractors.twitch_recover import recover_channel_vods
        results = recover_channel_vods(
            self.channel, self.year, self.month,
            log_fn=self._log_fn,
            progress_fn=lambda pct, msg: self.progress.emit(pct, msg),
        )
        self.done.emit(results)


class RecoverDialog(QDialog):
    """VOD Recovery Wizard dialog."""

    # Emitted when user selects a recovered VOD to download
    download_requested = pyqtSignal(str)  # M3U8 URL

    def __init__(self, parent, log_fn=None):
        super().__init__(parent)
        self.setWindowTitle("Deleted VOD Recovery Wizard")
        self.setMinimumSize(700, 450)
        self.setModal(True)
        self._log_fn = log_fn
        self._results = []
        self._worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        root.addWidget(QLabel(
            "<b>Recover Deleted / Expired Twitch VODs</b><br>"
            "<span style='color:#6c7086;'>Searches TwitchTracker for stream "
            "metadata and tests if CDN segments are still cached. "
            "Recovery is not guaranteed — Twitch purges CDN caches over time.</span>"
        ))

        # Input row
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        input_row.addWidget(QLabel("Channel:"))
        self.channel_input = QLineEdit()
        self.channel_input.setPlaceholderText("streamer_name")
        self.channel_input.setMaximumWidth(200)
        input_row.addWidget(self.channel_input)

        input_row.addWidget(QLabel("Month:"))
        self.month_combo = QComboBox()
        now = datetime.datetime.now()
        for i in range(1, 13):
            self.month_combo.addItem(datetime.date(2000, i, 1).strftime("%B"), i)
        self.month_combo.setCurrentIndex(max(0, now.month - 1))
        input_row.addWidget(self.month_combo)

        input_row.addWidget(QLabel("Year:"))
        self.year_spin = QSpinBox()
        self.year_spin.setRange(2016, now.year)
        self.year_spin.setValue(now.year)
        input_row.addWidget(self.year_spin)

        self.search_btn = QPushButton("Search")
        self.search_btn.setObjectName("primary")
        self.search_btn.clicked.connect(self._on_search)
        input_row.addWidget(self.search_btn)
        input_row.addStretch(1)
        root.addLayout(input_row)

        # Status
        self.status_label = QLabel("")
        root.addWidget(self.status_label)

        # Results table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Date", "Stream ID", "Quality", "Action"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(2, 100)
        root.addWidget(self.table, 1)

        # Bottom row
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _on_search(self):
        channel = self.channel_input.text().strip().lower()
        if not channel:
            self.status_label.setText("Enter a channel name.")
            return
        month = self.month_combo.currentData()
        year = self.year_spin.value()

        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("Search already in progress...")
            return

        self.search_btn.setEnabled(False)
        self.status_label.setText(f"Searching for {channel} VODs from {year}-{month:02d}...")
        self.table.setRowCount(0)
        self._results = []

        self._worker = _RecoverWorker(channel, year, month, log_fn=self._log_fn)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_progress(self, pct, msg):
        self.status_label.setText(f"({pct}%) {msg}")

    def _on_done(self, results):
        self.search_btn.setEnabled(True)
        self._results = results
        if not results:
            self.status_label.setText("No recoverable VODs found. CDN may have been purged.")
            return

        self.status_label.setText(f"Found {len(results)} recoverable VOD(s)!")
        self.table.setRowCount(len(results))
        for i, info in enumerate(results):
            date_item = QTableWidgetItem(info.title.replace("Recovered VOD — ", ""))
            self.table.setItem(i, 0, date_item)

            sid_item = QTableWidgetItem(info.url.split("/")[-3] if "/" in info.url else "")
            self.table.setItem(i, 1, sid_item)

            q_count = len(info.qualities)
            q_item = QTableWidgetItem(f"{q_count} stream(s)")
            q_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            q_item.setForeground(QColor("#a6e3a1"))
            self.table.setItem(i, 2, q_item)

            dl_btn = QPushButton("Download")
            dl_btn.setObjectName("primary")
            dl_btn.setFixedHeight(28)
            dl_btn.clicked.connect(lambda checked, url=info.url: self._on_download(url))
            self.table.setCellWidget(i, 3, dl_btn)

    def _on_download(self, url):
        self.download_requested.emit(url)
        self.status_label.setText(f"Sent to download: {url[:80]}...")
