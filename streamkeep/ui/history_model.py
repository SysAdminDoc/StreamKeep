"""Paged Qt model for the SQLite-backed download archive."""

from __future__ import annotations

import os

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor, QPixmap

from .. import db
from ..i18n import tr
from ..models import HistoryEntry
from ..theme import CAT


class HistoryTableModel(QAbstractTableModel):
    """Snapshot-stable, newest-first, incrementally fetched history model."""

    PAGE_SIZE = 100
    HEADERS = ("Preview", "Date", "Platform", "Title", "Quality", "Size", "Path")

    def __init__(self, parent=None, *, page_size=None):
        super().__init__(parent)
        self.page_size = max(10, int(page_size or self.PAGE_SIZE))
        self._rows: list[HistoryEntry] = []
        self._orphans: dict[int, bool] = {}
        self._thumbnails: dict[int, QPixmap] = {}
        self._transcript_hits: dict[str, list[dict]] = {}
        self._query = ""
        self._recording_paths: tuple[str, ...] | None = None
        self._snapshot_id = 0
        self._total = 0
        self.refresh()

    @property
    def total_count(self):
        return self._total

    @property
    def loaded_count(self):
        return len(self._rows)

    @property
    def query(self):
        return self._query

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return tr(self.HEADERS[section])
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        entry = self._rows[index.row()]
        column = index.column()
        orphan = self._orphans.get(entry.db_id, False)

        if role == Qt.ItemDataRole.UserRole:
            return entry
        if role == Qt.ItemDataRole.DecorationRole and column == 0:
            return self._thumbnails.get(entry.db_id)
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return tr("Missing") if orphan else tr("Loading")
            if column == 1:
                return entry.date
            if column == 2:
                return entry.platform
            if column == 3:
                prefix = ""
                if entry.favorite:
                    prefix += "★ "
                if entry.watched:
                    prefix += "✓ "
                elif entry.watch_position_secs > 0:
                    prefix += "▶ "
                return prefix + entry.title
            if column == 4:
                return entry.quality
            if column == 5:
                return entry.size
            if column == 6:
                return f"{entry.path}  (missing)" if orphan and entry.path else entry.path
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (0, 1, 2, 4, 5):
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            if orphan:
                return QColor(CAT["overlay0"])
            if entry.watched and column == 3:
                return QColor(CAT["overlay0"])
        if role == Qt.ItemDataRole.ToolTipRole and column == 3:
            hits = self._transcript_hits.get(entry.path, [])[:3]
            if hits:
                lines = []
                for hit in hits:
                    mins = int(hit["start_sec"]) // 60
                    secs = int(hit["start_sec"]) % 60
                    lines.append(f"[{mins}:{secs:02d}] {hit['text']}")
                return "\n".join(lines)
        if role == Qt.ItemDataRole.AccessibleTextRole:
            value = self.data(index, Qt.ItemDataRole.DisplayRole)
            return str(value or self.HEADERS[column])
        return None

    def canFetchMore(self, parent=QModelIndex()):
        return not parent.isValid() and len(self._rows) < self._total

    def fetchMore(self, parent=QModelIndex()):
        if parent.isValid() or not self.canFetchMore(parent):
            return
        before_id = self._rows[-1].db_id if self._rows else self._snapshot_id + 1
        page = db.query_history_page(
            query=self._query,
            limit=self.page_size,
            before_id=before_id,
            snapshot_id=self._snapshot_id,
            recording_paths=self._recording_paths,
        )
        entries = [HistoryEntry.from_dict(row) for row in page]
        if not entries:
            self._total = len(self._rows)
            return
        first = len(self._rows)
        last = first + len(entries) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self._rows.extend(entries)
        self._cache_orphan_state(entries)
        self.endInsertRows()

    def refresh(self, *, query=None, recording_paths=None):
        if query is not None:
            self._query = str(query or "").strip()
        if recording_paths is not None:
            self._recording_paths = tuple(str(path) for path in recording_paths if path)
        self.beginResetModel()
        self._rows = []
        self._orphans = {}
        self._thumbnails = {}
        self._snapshot_id = db.history_snapshot_id()
        self._total = db.count_history_query(
            query=self._query,
            snapshot_id=self._snapshot_id,
            recording_paths=self._recording_paths,
        )
        page = db.query_history_page(
            query=self._query,
            limit=self.page_size,
            snapshot_id=self._snapshot_id,
            recording_paths=self._recording_paths,
        )
        self._rows = [HistoryEntry.from_dict(row) for row in page]
        self._cache_orphan_state(self._rows)
        self.endResetModel()

    def set_filter(self, query="", *, recording_paths=None):
        self._recording_paths = (
            None if recording_paths is None
            else tuple(str(path) for path in recording_paths if path)
        )
        self.refresh(query=query)

    def set_transcript_hits(self, hits):
        grouped = {}
        for hit in hits or []:
            grouped.setdefault(hit.get("recording_path", ""), []).append(hit)
        self._transcript_hits = grouped
        if self._rows:
            self.dataChanged.emit(
                self.index(0, 3),
                self.index(len(self._rows) - 1, 3),
                [Qt.ItemDataRole.ToolTipRole],
            )

    def entry_at(self, row):
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_for_id(self, entry_id):
        for row, entry in enumerate(self._rows):
            if entry.db_id == entry_id:
                return row
        return -1

    def set_thumbnail(self, entry_id, pixmap):
        row = self.row_for_id(entry_id)
        if row < 0 or not isinstance(pixmap, QPixmap) or pixmap.isNull():
            return False
        self._thumbnails[int(entry_id)] = pixmap
        index = self.index(row, 0)
        self.dataChanged.emit(
            index,
            index,
            [Qt.ItemDataRole.DecorationRole, Qt.ItemDataRole.DisplayRole],
        )
        return True

    def loaded_entries(self):
        return tuple(self._rows)

    def _cache_orphan_state(self, entries):
        for entry in entries:
            self._orphans[entry.db_id] = bool(entry.path) and not os.path.isdir(entry.path)
