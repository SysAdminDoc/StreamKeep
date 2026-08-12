"""Download Analytics Dashboard — historical download stats with charts (F63).

QPainter-rendered charts: downloads per day (bar), platform breakdown (donut),
top channels (horizontal bar). Metric cards at top. Date range filtering.
"""

import re
from datetime import datetime, timedelta

from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt, QRect, QRectF
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen

from ...theme import CAT
from ... import db as _db
from ...workers.query import QueryWorker
from ..widgets import make_empty_state, make_metric_card


# ── Chart widgets ───────────────────────────────────────────────────

class BarChartWidget(QWidget):
    """Responsive vertical trend chart rendered with QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self._data = []   # list of (label, value)
        self._title = ""
        self.setAccessibleName("Downloads over time chart")

    def set_data(self, data, title=""):
        self._data = list(data)
        self._title = title
        points = ", ".join(f"{label}: {value}" for label, value in self._data)
        self.setAccessibleDescription(points or "No download trend data")
        self.update()

    def paintEvent(self, event):
        if not self._data:
            p = QPainter(self)
            p.setPen(QColor(CAT["muted"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No capture activity in this range")
            p.end()
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        margin_l, margin_r, margin_t, margin_b = 38, 16, 24, 30
        chart_w = max(1, w - margin_l - margin_r)
        chart_h = max(1, h - margin_t - margin_b)
        max_val = max((v for _, v in self._data), default=1) or 1
        n = len(self._data)
        slot_w = chart_w / max(n, 1)
        bar_w = max(12.0, min(44.0, slot_w * 0.48))

        p.setPen(QColor(CAT["muted"]))
        p.drawText(QRect(0, 0, w - 8, 18), Qt.AlignmentFlag.AlignRight, self._title)
        grid_pen = QPen(QColor(CAT["stroke"]))
        grid_pen.setWidthF(1.0)
        p.setPen(grid_pen)
        for step in range(4):
            y = margin_t + (chart_h * step / 3)
            p.drawLine(int(margin_l), int(y), int(w - margin_r), int(y))

        for i, (label, val) in enumerate(self._data):
            bar_h = max(3.0, val / max_val * chart_h) if val else 3.0
            x = margin_l + i * slot_w + (slot_w - bar_w) / 2
            y = margin_t + chart_h - bar_h
            gradient = QLinearGradient(0, y, 0, margin_t + chart_h)
            gradient.setColorAt(0.0, QColor(CAT["sky"]))
            gradient.setColorAt(1.0, QColor(CAT["accent"]))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(gradient)
            p.drawRoundedRect(QRectF(x, y, bar_w, bar_h), 5, 5)
            p.setPen(QColor(CAT["text"]))
            p.drawText(
                QRectF(x, max(margin_t, y - 18), bar_w, 16),
                Qt.AlignmentFlag.AlignCenter,
                str(val),
            )
            if n <= 15 or i % max(1, n // 10) == 0:
                p.setPen(QColor(CAT["subtext0"]))
                p.drawText(
                    QRectF(margin_l + i * slot_w, h - margin_b + 4, slot_w, 22),
                    Qt.AlignmentFlag.AlignCenter,
                    str(label)[:8],
                )
        p.end()


class DonutChartWidget(QWidget):
    """Simple donut/pie chart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 160)
        self._data = []   # list of (label, value, color_hex)
        self._title = ""
        self.setAccessibleName("Platform breakdown chart")

    def set_data(self, data, title=""):
        self._data = list(data)
        self._title = title
        points = ", ".join(f"{label}: {value}" for label, value, _ in self._data)
        self.setAccessibleDescription(points or "No platform data")
        self.update()

    def paintEvent(self, event):
        if not self._data:
            p = QPainter(self)
            p.setPen(QColor(CAT["muted"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No platform data yet")
            p.end()
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        size = max(80.0, min(h - 42.0, w * 0.44, 154.0))
        x0 = 18.0
        y0 = 26.0 + max(0.0, (h - 36.0 - size) / 2)
        rect = QRectF(x0, y0, size, size)
        total = sum(v for _, v, _ in self._data) or 1
        p.setPen(QColor(CAT["muted"]))
        p.drawText(QRect(0, 0, w - 8, 18), Qt.AlignmentFlag.AlignRight, self._title)
        start = 90 * 16
        for label, val, color_hex in self._data:
            span = int(val / total * 360 * 16)
            arc_pen = QPen(QColor(color_hex))
            arc_pen.setWidthF(16.0)
            arc_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(arc_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(rect.adjusted(9, 9, -9, -9), start, -span)
            start -= span

        p.setPen(QColor(CAT["text"]))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(total))
        legend_x = x0 + size + 24
        legend_y = max(30.0, y0 + 4)
        for index, (label, value, color_hex) in enumerate(self._data[:5]):
            y = legend_y + index * 25
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color_hex))
            p.drawEllipse(QRectF(legend_x, y + 4, 8, 8))
            p.setPen(QColor(CAT["subtext1"]))
            p.drawText(
                QRectF(legend_x + 15, y, max(40.0, w - legend_x - 16), 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{label}  {value}",
            )
        p.end()


class HBarChartWidget(QWidget):
    """Horizontal bar chart for ranked items (top channels)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self._data = []
        self._title = ""
        self.setAccessibleName("Top channels chart")

    def set_data(self, data, title=""):
        self._data = list(data)[:8]
        self._title = title
        points = ", ".join(f"{label}: {value}" for label, value in self._data)
        self.setAccessibleDescription(points or "No channel data")
        self.update()

    def paintEvent(self, event):
        if not self._data:
            p = QPainter(self)
            p.setPen(QColor(CAT["muted"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No channel history yet")
            p.end()
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        margin_l = 124
        usable_h = max(40, h - 24)
        slot_h = usable_h / max(len(self._data), 1)
        bar_h = max(10.0, min(16.0, slot_h * 0.55))
        max_val = max((v for _, v in self._data), default=1) or 1

        p.setPen(QColor(CAT["muted"]))
        p.drawText(QRect(0, 0, w - 8, 18), Qt.AlignmentFlag.AlignRight, self._title)

        colors = (CAT["accent"], CAT["green"], CAT["sky"], CAT["lavender"])
        for i, (label, val) in enumerate(self._data):
            y = 20 + i * slot_h + (slot_h - bar_h) / 2
            track_w = max(20.0, w - margin_l - 34.0)
            bar_w = max(4.0, val / max_val * track_w)
            p.setPen(QColor(CAT["subtext0"]))
            p.drawText(
                QRectF(0, y, margin_l - 10, bar_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(label)[:16],
            )
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(CAT["panelHi"]))
            p.drawRoundedRect(QRectF(margin_l, y, track_w, bar_h), 5, 5)
            p.setBrush(QColor(colors[i % len(colors)]))
            p.drawRoundedRect(QRectF(margin_l, y, bar_w, bar_h), 5, 5)
            p.setPen(QColor(CAT["text"]))
            p.drawText(
                QRectF(margin_l + 8, y, track_w - 16, bar_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(val),
            )
        p.end()


# ── Tab builder ─────────────────────────────────────────────────────

PLATFORM_COLORS = {
    "twitch": "#a78bfa", "kick": "#65d6a0", "youtube": "#f07186",
    "rumble": "#84cc78", "soundcloud": "#f2a56f", "reddit": "#ed8872",
    "podcast": "#f2b84b", "vimeo": "#5b8cff", "direct": "#63b5e6",
}


def build_analytics_tab(win):
    """Build the Analytics tab widget."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)

    hero = QFrame()
    hero.setObjectName("heroCard")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(2, 2, 2, 4)
    hero_lay.setSpacing(4)

    hero_copy = QVBoxLayout()
    hero_copy.setSpacing(4)
    kicker = QLabel("Analytics")
    kicker.setObjectName("eyebrow")
    kicker.setVisible(False)
    title = QLabel("Archive analytics")
    title.setObjectName("heroTitle")
    title.setWordWrap(True)
    body = QLabel(
        "Activity, storage, and source trends."
    )
    body.setObjectName("heroBody")
    body.setWordWrap(True)
    body.setVisible(False)
    hero_copy.addWidget(kicker)
    hero_copy.addWidget(title)
    hero_copy.addWidget(body)
    hero_lay.addLayout(hero_copy)

    # Metric cards row
    cards_row = QHBoxLayout()
    cards_row.setSpacing(12)
    win.analytics_total_card, win.analytics_total_val, _ = make_metric_card(
        "Total Downloads", "0", "all time")
    cards_row.addWidget(win.analytics_total_card, 1)
    win.analytics_size_card, win.analytics_size_val, _ = make_metric_card(
        "Total Size", "0 GB", "estimated")
    cards_row.addWidget(win.analytics_size_card, 1)
    win.analytics_top_card, win.analytics_top_val, _ = make_metric_card(
        "Top Channel", "-", "by count")
    cards_row.addWidget(win.analytics_top_card, 1)
    win.analytics_plat_card, win.analytics_plat_val, _ = make_metric_card(
        "Top Platform", "-", "by count")
    cards_row.addWidget(win.analytics_plat_card, 1)
    hero_lay.addLayout(cards_row)
    lay.addWidget(hero)

    # Date range filter
    filter_card = QFrame()
    filter_card.setObjectName("toolbar")
    filter_row = QHBoxLayout(filter_card)
    filter_row.setContentsMargins(14, 10, 14, 10)
    filter_row.setSpacing(10)
    filter_copy = QVBoxLayout()
    filter_copy.setSpacing(2)
    filter_title = QLabel("Range")
    filter_title.setObjectName("fieldLabel")
    filter_hint = QLabel("Choose a reporting range.")
    filter_hint.setObjectName("subtleText")
    filter_hint.setWordWrap(True)
    filter_hint.setVisible(False)
    filter_copy.addWidget(filter_title)
    filter_copy.addWidget(filter_hint)
    filter_row.addLayout(filter_copy, 1)
    win.analytics_range = QComboBox()
    win.analytics_range.addItems(["All Time", "Last 7 Days", "Last 30 Days", "Last 90 Days", "This Year"])
    win.analytics_range.currentIndexChanged.connect(lambda: _refresh_analytics(win))
    win.analytics_range.setMinimumWidth(170)
    filter_row.addWidget(win.analytics_range)
    lay.addWidget(filter_card)

    (
        win.analytics_empty_state,
        win.analytics_empty_title,
        win.analytics_empty_body,
    ) = make_empty_state(
        "No archive analytics yet",
        "Complete a download or adopt an existing library; its History entry "
        "will become the first analytics data point.",
    )
    win.analytics_empty_state.setMinimumHeight(190)
    win.analytics_empty_state.setVisible(False)
    lay.addWidget(win.analytics_empty_state)

    win.analytics_charts = QWidget()
    analytics_charts_lay = QVBoxLayout(win.analytics_charts)
    analytics_charts_lay.setContentsMargins(0, 0, 0, 0)
    analytics_charts_lay.setSpacing(12)

    # Charts row
    charts_row = QHBoxLayout()
    charts_row.setSpacing(12)
    daily_card = QFrame()
    daily_card.setObjectName("analyticsPanel")
    daily_lay = QVBoxLayout(daily_card)
    daily_lay.setContentsMargins(16, 14, 16, 12)
    daily_lay.setSpacing(6)
    daily_title = QLabel("Capture volume")
    daily_title.setObjectName("sectionTitle")
    daily_hint = QLabel("Downloads per day within the selected range.")
    daily_hint.setObjectName("sectionBody")
    daily_hint.setVisible(False)
    daily_lay.addWidget(daily_title)
    daily_lay.addWidget(daily_hint)
    win.analytics_daily_chart = BarChartWidget()
    daily_lay.addWidget(win.analytics_daily_chart)
    charts_row.addWidget(daily_card, 2)

    platform_card = QFrame()
    platform_card.setObjectName("analyticsPanel")
    platform_lay = QVBoxLayout(platform_card)
    platform_lay.setContentsMargins(16, 14, 16, 12)
    platform_lay.setSpacing(6)
    platform_title = QLabel("Platform mix")
    platform_title.setObjectName("sectionTitle")
    platform_hint = QLabel("Share of downloads by source platform.")
    platform_hint.setObjectName("sectionBody")
    platform_hint.setWordWrap(True)
    platform_hint.setVisible(False)
    platform_lay.addWidget(platform_title)
    platform_lay.addWidget(platform_hint)
    win.analytics_platform_chart = DonutChartWidget()
    platform_lay.addWidget(win.analytics_platform_chart)
    charts_row.addWidget(platform_card, 1)
    analytics_charts_lay.addLayout(charts_row)

    # Top channels
    channels_card = QFrame()
    channels_card.setObjectName("analyticsPanel")
    channels_lay = QVBoxLayout(channels_card)
    channels_lay.setContentsMargins(16, 14, 16, 12)
    channels_lay.setSpacing(6)
    channels_title = QLabel("Top channels")
    channels_title.setObjectName("sectionTitle")
    channels_hint = QLabel("Who appears most often in the active date range.")
    channels_hint.setObjectName("sectionBody")
    channels_hint.setWordWrap(True)
    channels_hint.setVisible(False)
    channels_lay.addWidget(channels_title)
    channels_lay.addWidget(channels_hint)
    win.analytics_channels_chart = HBarChartWidget()
    win.analytics_channels_chart.setMinimumHeight(160)
    channels_lay.addWidget(win.analytics_channels_chart)
    analytics_charts_lay.addWidget(channels_card)
    lay.addWidget(win.analytics_charts)

    lay.addStretch(1)
    return page


def _refresh_analytics(win):
    """Refresh analytics off-thread and discard superseded results."""
    range_idx = win.analytics_range.currentIndex() if hasattr(win, "analytics_range") else 0
    now = datetime.now()
    cutoff = None
    if range_idx == 1:
        cutoff = now - timedelta(days=7)
    elif range_idx == 2:
        cutoff = now - timedelta(days=30)
    elif range_idx == 3:
        cutoff = now - timedelta(days=90)
    elif range_idx == 4:
        cutoff = datetime(now.year, 1, 1)

    cutoff_text = cutoff.strftime("%Y-%m-%d") if cutoff else ""
    generation = int(getattr(win, "_analytics_generation", 0)) + 1
    win._analytics_generation = generation
    workers = getattr(win, "_analytics_workers", None)
    if workers is None:
        workers = {}
        win._analytics_workers = workers
    for worker in workers.values():
        worker.cancel()

    worker = QueryWorker(
        generation,
        lambda: _db.history_analytics(cutoff_text),
    )
    workers[generation] = worker
    begin_busy = getattr(win, "_begin_background_activity", None)
    busy_done = (
        begin_busy("Refreshing archive analytics…")
        if callable(begin_busy) else lambda: None
    )
    worker.succeeded.connect(
        lambda token, stats: _apply_analytics_result(
            win, token, range_idx, stats,
        )
    )
    worker.failed.connect(
        lambda token, error: _show_analytics_error(win, token, error)
    )

    def finish():
        workers.pop(generation, None)
        busy_done()

    worker.finished.connect(finish)
    worker.start()
    return worker


def _apply_analytics_result(win, generation, range_idx, stats):
    """Render a current analytics result on the UI thread."""
    if generation != getattr(win, "_analytics_generation", 0):
        return

    # Metric cards
    total = stats["total"]
    win.analytics_total_val.setText(str(total))
    has_data = bool(total)
    win.analytics_charts.setVisible(has_data)
    win.analytics_empty_state.setVisible(not has_data)
    if not has_data and range_idx:
        win.analytics_empty_title.setText("No archive activity in this range")
        win.analytics_empty_body.setText(
            "Choose All Time to see older downloads, or complete a new download "
            "to add activity to this range."
        )
    elif not has_data:
        win.analytics_empty_title.setText("No archive analytics yet")
        win.analytics_empty_body.setText(
            "Complete a download or adopt an existing library; its History entry "
            "will become the first analytics data point."
        )

    total_gb = stats["size_gb"]
    win.analytics_size_val.setText(f"{total_gb:.1f} GB")

    plat_counts = stats["platforms"]
    chan_counts = stats["channels"]

    if plat_counts:
        top_plat = plat_counts[0]
        win.analytics_plat_val.setText(f"{top_plat[0]} ({top_plat[1]})")
    else:
        win.analytics_plat_val.setText("-")

    if chan_counts:
        top_chan = chan_counts[0]
        win.analytics_top_val.setText(f"{top_chan[0][:16]} ({top_chan[1]})")
    else:
        win.analytics_top_val.setText("-")

    # Daily bar chart
    sorted_days = stats["daily"]
    win.analytics_daily_chart.set_data(
        [(d[5:], c) for d, c in sorted_days],
        title="Downloads per Day"
    )

    # Platform donut
    plat_data = []
    for plat, count in plat_counts:
        color = PLATFORM_COLORS.get(plat.lower(), CAT["overlay0"])
        plat_data.append((plat, count, color))
    win.analytics_platform_chart.set_data(plat_data, title="By Platform")

    # Top channels bar
    win.analytics_channels_chart.set_data(
        chan_counts,
        title="Top Channels"
    )


def _show_analytics_error(win, generation, error):
    """Expose a current analytics failure instead of leaving stale metrics."""
    if generation != getattr(win, "_analytics_generation", 0):
        return
    win.analytics_charts.setVisible(False)
    win.analytics_empty_state.setVisible(True)
    win.analytics_empty_title.setText("Analytics unavailable")
    win.analytics_empty_body.setText(str(error or "The archive query failed."))
    notify = getattr(win, "_notify_center", None)
    if callable(notify):
        notify(f"Analytics refresh failed: {error}", "warning")


def _parse_size_gb(s):
    """Parse a size string like '2.3 GB' or '450 MB' to float GB."""
    m = re.match(r"([\d.]+)\s*(GB|MB|KB|TB)", s, re.I)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "TB":
        return val * 1024
    if unit == "GB":
        return val
    if unit == "MB":
        return val / 1024
    if unit == "KB":
        return val / (1024 * 1024)
    return 0
