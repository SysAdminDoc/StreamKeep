"""Week-view stream schedule calendar — shows upcoming scheduled streams
as colored blocks on a 7-day × 24-hour grid.

Each channel gets a consistent color from the Catppuccin Mocha palette.
Click a block to see details and configure auto-record.
"""

from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QToolTip, QVBoxLayout, QWidget,
)

from ..theme import CAT

_BLOCK_COLOR_KEYS = ["blue", "green", "peach", "mauve", "teal", "pink", "yellow", "lavender"]


def _block_colors():
    """Return current block colors from the live CAT dict (theme-safe)."""
    return [CAT[k] for k in _BLOCK_COLOR_KEYS]

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Display window: 6 AM to midnight (18 hours)
_START_HOUR = 6
_END_HOUR = 24
_HOUR_SPAN = _END_HOUR - _START_HOUR


class _GridCanvas(QWidget):
    """Custom-painted week grid with schedule blocks."""

    block_clicked = pyqtSignal(dict)  # emits the segment dict

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(360)
        self.setMouseTracking(True)
        self._segments = []   # list of (day_idx, hour_frac, seg_dict, color)
        self._channel_colors = {}

    def set_segments(self, week_segments):
        """*week_segments*: list of ``(day_idx, hour_frac, seg_dict)``."""
        colors = _block_colors()
        self._segments = []
        for day_idx, hour_frac, seg in week_segments:
            ch = seg.get("channel", "")
            if ch not in self._channel_colors:
                idx = len(self._channel_colors) % len(colors)
                self._channel_colors[ch] = colors[idx]
            color = self._channel_colors[ch]
            self._segments.append((day_idx, hour_frac, seg, color))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        label_w = 40  # left margin for hour labels
        grid_w = w - label_w
        grid_h = h - 20  # top margin for day labels
        col_w = grid_w / 7
        row_h = grid_h / _HOUR_SPAN

        # Background
        p.fillRect(0, 0, w, h, QColor(CAT["base"]))

        # Day headers
        p.setPen(QColor(CAT["subtext0"]))
        today = datetime.now(timezone.utc).astimezone()
        monday = today.date() - timedelta(days=today.weekday())
        for d in range(7):
            x = label_w + d * col_w
            dt = monday + timedelta(days=d)
            label = f"{_DAY_NAMES[d]} {dt.day}"
            p.drawText(int(x), 0, int(col_w), 18, Qt.AlignmentFlag.AlignCenter, label)

        # Hour grid lines + labels
        p.setPen(QColor(CAT["surface0"]))
        for hr in range(_HOUR_SPAN + 1):
            y = 20 + hr * row_h
            p.drawLine(label_w, int(y), w, int(y))
            if hr < _HOUR_SPAN:
                actual_hr = _START_HOUR + hr
                lbl = f"{actual_hr:02d}"
                p.setPen(QColor(CAT["subtext0"]))
                p.drawText(0, int(y), label_w - 4, int(row_h),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, lbl)
                p.setPen(QColor(CAT["surface0"]))

        # Day column separators
        for d in range(8):
            x = label_w + d * col_w
            p.drawLine(int(x), 20, int(x), h)

        # Schedule blocks
        for day_idx, hour_frac, seg, color in self._segments:
            if day_idx < 0 or day_idx > 6:
                continue
            # Parse duration from end_iso - start_iso, default 1h
            dur_hrs = 1.0
            try:
                s_iso = seg.get("start_iso", "")
                e_iso = seg.get("end_iso", "")
                if s_iso and e_iso:
                    st = datetime.fromisoformat(s_iso.replace("Z", "+00:00"))
                    et = datetime.fromisoformat(e_iso.replace("Z", "+00:00"))
                    dur_hrs = max(0.25, (et - st).total_seconds() / 3600)
            except (ValueError, TypeError):
                pass

            y_start = hour_frac - _START_HOUR
            if y_start + dur_hrs < 0 or y_start > _HOUR_SPAN:
                continue
            y_start = max(0, y_start)

            x = label_w + day_idx * col_w + 2
            y = 20 + y_start * row_h
            bh = dur_hrs * row_h
            bw = col_w - 4

            p.setBrush(QColor(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(int(x), int(y), int(bw), int(bh), 4, 4)

            # Block label
            title = seg.get("title", "") or seg.get("channel", "")
            if bh > 16:
                p.setPen(QColor(CAT["crust"]))
                p.drawText(int(x + 3), int(y + 2), int(bw - 6), int(bh - 4),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                           title[:20])

        p.end()

    def mousePressEvent(self, event):
        pos = event.position()
        label_w = 40
        grid_w = self.width() - label_w
        grid_h = self.height() - 20
        col_w = grid_w / 7
        row_h = grid_h / _HOUR_SPAN

        for day_idx, hour_frac, seg, _color in self._segments:
            dur_hrs = 1.0
            try:
                s_iso = seg.get("start_iso", "")
                e_iso = seg.get("end_iso", "")
                if s_iso and e_iso:
                    st = datetime.fromisoformat(s_iso.replace("Z", "+00:00"))
                    et = datetime.fromisoformat(e_iso.replace("Z", "+00:00"))
                    dur_hrs = max(0.25, (et - st).total_seconds() / 3600)
            except (ValueError, TypeError):
                pass

            y_start = max(0, hour_frac - _START_HOUR)
            x = label_w + day_idx * col_w + 2
            y = 20 + y_start * row_h
            bw = col_w - 4
            bh = dur_hrs * row_h

            if x <= pos.x() <= x + bw and y <= pos.y() <= y + bh:
                self.block_clicked.emit(seg)
                return

    def mouseMoveEvent(self, event):
        pos = event.position()
        label_w = 40
        grid_w = self.width() - label_w
        col_w = grid_w / 7
        row_h = (self.height() - 20) / _HOUR_SPAN

        for day_idx, hour_frac, seg, _color in self._segments:
            dur_hrs = 1.0
            try:
                s_iso = seg.get("start_iso", "")
                e_iso = seg.get("end_iso", "")
                if s_iso and e_iso:
                    st = datetime.fromisoformat(s_iso.replace("Z", "+00:00"))
                    et = datetime.fromisoformat(e_iso.replace("Z", "+00:00"))
                    dur_hrs = max(0.25, (et - st).total_seconds() / 3600)
            except (ValueError, TypeError):
                pass

            y_start = max(0, hour_frac - _START_HOUR)
            x = label_w + day_idx * col_w + 2
            y = 20 + y_start * row_h
            bw = col_w - 4
            bh = dur_hrs * row_h

            if x <= pos.x() <= x + bw and y <= pos.y() <= y + bh:
                tip = f"{seg.get('channel', '')}\n{seg.get('title', '')}"
                if seg.get("category"):
                    tip += f"\n{seg['category']}"
                tip += f"\n{seg.get('start_iso', '')[:16]}"
                QToolTip.showText(event.globalPosition().toPoint(), tip, self)
                return
        QToolTip.hideText()


class ScheduleCalendar(QWidget):
    """Week-view calendar with navigation and schedule refresh."""

    block_clicked = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # Navigation row
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.prev_btn = QPushButton("◀ Prev week")
        self.prev_btn.setObjectName("ghost")
        self.prev_btn.setFixedWidth(100)
        self.prev_btn.clicked.connect(self._prev_week)
        nav.addWidget(self.prev_btn)
        self.week_label = QLabel("")
        self.week_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.week_label, 1)
        self.next_btn = QPushButton("Next week ▶")
        self.next_btn.setObjectName("ghost")
        self.next_btn.setFixedWidth(100)
        self.next_btn.clicked.connect(self._next_week)
        nav.addWidget(self.next_btn)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.setFixedWidth(80)
        nav.addWidget(self.refresh_btn)
        root.addLayout(nav)

        self._canvas = _GridCanvas()
        self._canvas.block_clicked.connect(self.block_clicked.emit)
        root.addWidget(self._canvas, 1)

        # Start on current week
        today = datetime.now(timezone.utc).astimezone().date()
        self._week_start = today - timedelta(days=today.weekday())
        self._update_label()

    def set_cache(self, cache):
        self._cache = cache or {}
        self._refresh_grid()

    def _refresh_grid(self):
        from ..schedule import get_week_segments
        segs = get_week_segments(self._cache, self._week_start)
        self._canvas.set_segments(segs)

    def _update_label(self):
        end = self._week_start + timedelta(days=6)
        self.week_label.setText(
            f"{self._week_start.strftime('%b %d')} — {end.strftime('%b %d, %Y')}"
        )

    def _prev_week(self):
        self._week_start -= timedelta(days=7)
        self._update_label()
        self._refresh_grid()

    def _next_week(self):
        self._week_start += timedelta(days=7)
        self._update_label()
        self._refresh_grid()
