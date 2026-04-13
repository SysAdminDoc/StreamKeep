"""Notification log viewer — searchable, filterable history of all events.

Reads the persistent ``notifications.jsonl`` via ``NotificationCenter.load_history()``
and presents them in a table with severity filtering and free-text search.
"""

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class NotificationLogDialog(QDialog):
    """Modal dialog showing the full notification history."""

    def __init__(self, parent, center):
        super().__init__(parent)
        self.setWindowTitle("Notification Log")
        self.setMinimumSize(700, 500)
        self.setModal(True)

        self._entries = center.load_history()
        self._filtered = list(self._entries)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # Filter bar
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("Filter:"))
        self.level_combo = QComboBox()
        self.level_combo.addItems(["All", "Info", "Success", "Warning", "Error"])
        self.level_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.level_combo)
        filter_row.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search messages\u2026")
        self.search_input.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_input, 1)
        root.addLayout(filter_row)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Time", "Level", "Message"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 70)
        root.addWidget(self.table)

        # Count + buttons
        btn_row = QHBoxLayout()
        self.count_label = QLabel()
        btn_row.addWidget(self.count_label)
        btn_row.addStretch(1)
        export_btn = QPushButton("Export\u2026")
        export_btn.setObjectName("secondary")
        export_btn.clicked.connect(self._on_export)
        btn_row.addWidget(export_btn)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._apply_filter()

    def _apply_filter(self):
        level_filter = self.level_combo.currentText().lower()
        search = self.search_input.text().strip().lower()

        self._filtered = []
        for e in self._entries:
            if level_filter != "all" and e.get("level", "info") != level_filter:
                continue
            if search and search not in e.get("text", "").lower():
                continue
            self._filtered.append(e)

        self.table.setRowCount(len(self._filtered))
        for i, e in enumerate(reversed(self._filtered)):
            self.table.setItem(i, 0, QTableWidgetItem(e.get("ts", "")))
            self.table.setItem(i, 1, QTableWidgetItem(e.get("level", "info")))
            self.table.setItem(i, 2, QTableWidgetItem(e.get("text", "")))

        self.count_label.setText(
            f"{len(self._filtered)} of {len(self._entries)} entries")

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export notifications", "", "Text files (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for e in self._filtered:
                    f.write(
                        f"{e.get('ts', '')}  [{e.get('level', 'info')}]  "
                        f"{e.get('text', '')}\n"
                    )
        except OSError:
            pass
