"""Qt model/view adapters for storage-scan results."""

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QPixmap

from ..utils import fmt_size


class StorageTableModel(QAbstractTableModel):
    HEADERS = ("Preview", "Platform", "Channel", "Title", "Files", "Size", "Path")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups = []
        self._thumbnails = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._groups)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section] if 0 <= section < len(self.HEADERS) else None
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._groups):
            return None
        group = self._groups[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.UserRole:
            return group
        if role == Qt.ItemDataRole.DecorationRole and column == 0:
            return self._thumbnails.get(group.dir_path)
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                "" if group.dir_path in self._thumbnails else "…",
                group.platform,
                group.channel,
                group.title,
                str(len(group.files)),
                fmt_size(group.total_size),
                group.dir_path,
            )
            return values[column]
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (0, 1, 4, 5):
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return str(self.data(index, Qt.ItemDataRole.DisplayRole) or self.HEADERS[column])
        return None

    def set_groups(self, groups):
        self.beginResetModel()
        self._groups = list(groups or [])
        self._thumbnails = {}
        self.endResetModel()

    def group_at(self, row):
        return self._groups[row] if 0 <= row < len(self._groups) else None

    def set_thumbnail(self, dir_path, pixmap):
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            return False
        for row, group in enumerate(self._groups):
            if group.dir_path == dir_path:
                self._thumbnails[dir_path] = pixmap
                index = self.index(row, 0)
                self.dataChanged.emit(
                    index,
                    index,
                    [Qt.ItemDataRole.DecorationRole, Qt.ItemDataRole.DisplayRole],
                )
                return True
        return False


class StorageFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._platform = "All"
        self._channel = "All"

    def set_filters(self, platform="All", channel="All"):
        self._platform = str(platform or "All")
        self._channel = str(channel or "All")
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        group = model.group_at(source_row) if model is not None else None
        if group is None:
            return False
        if self._platform != "All" and group.platform != self._platform:
            return False
        if self._channel != "All" and group.channel != self._channel:
            return False
        return True

    def group_at(self, proxy_row):
        index = self.index(proxy_row, 0)
        if not index.isValid():
            return None
        source = self.mapToSource(index)
        return self.sourceModel().group_at(source.row())
