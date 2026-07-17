"""Download Analytics Dashboard — historical download stats with charts (F63).

QPainter-rendered charts: downloads per day (bar), platform breakdown (donut),
top channels (horizontal bar). Metric cards at top. Date range filtering.
"""

import re
from datetime import datetime, timedelta

from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QPainter

from ...theme import CAT
from ... import db as _db
from ..widgets import make_metric_card


# ── Chart widgets ───────────────────────────────────────────────────

class BarChartWidget(QWidget):
    """Simple vertical bar chart rendered with QPainter."""

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
        margin_l, margin_b = 40, 24
        chart_w = w - margin_l - 10
        chart_h = h - margin_b - 20

        # Title
        p.setPen(QColor(CAT["text"]))
        p.drawText(QRect(0, 0, w, 18), Qt.AlignmentFlag.AlignCenter, self._title)

        max_val = max((v for _, v in self._data), default=1) or 1
        n = len(self._data)
        bar_w = max(4, min(30, chart_w // max(n, 1) - 2))

        color = QColor(CAT["blue"])
        for i, (label, val) in enumerate(self._data):
            bar_h = int(val / max_val * chart_h) if max_val else 0
            x = margin_l + i * (bar_w + 2)
            y = 20 + chart_h - bar_h
            p.fillRect(x, y, bar_w, bar_h, color)
            # Label every Nth bar
            if n <= 15 or i % max(1, n // 10) == 0:
                p.setPen(QColor(CAT["subtext0"]))
                p.drawText(QRect(x - 4, h - margin_b, bar_w + 8, margin_b),
                           Qt.AlignmentFlag.AlignCenter, str(label)[:6])
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
        size = min(w, h) - 20
        x0 = (w - size) // 2
        y0 = (h - size) // 2 + 10
        rect = QRect(x0, y0, size, size)

        total = sum(v for _, v, _ in self._data) or 1
        start = 0
        for label, val, color_hex in self._data:
            span = int(val / total * 360 * 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color_hex))
            p.drawPie(rect, start, span)
            start += span

        # Inner circle (donut hole)
        inner = int(size * 0.5)
        ix = x0 + (size - inner) // 2
        iy = y0 + (size - inner) // 2
        p.setBrush(QColor(CAT["base"]))
        p.drawEllipse(ix, iy, inner, inner)

        # Title
        p.setPen(QColor(CAT["text"]))
        p.drawText(QRect(0, 0, w, 16), Qt.AlignmentFlag.AlignCenter, self._title)
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
        margin_l = 120
        bar_h = max(10, min(18, (h - 24) // max(len(self._data), 1) - 2))
        max_val = max((v for _, v in self._data), default=1) or 1

        p.setPen(QColor(CAT["text"]))
        p.drawText(QRect(0, 0, w, 16), Qt.AlignmentFlag.AlignCenter, self._title)

        color = QColor(CAT["green"])
        for i, (label, val) in enumerate(self._data):
            y = 20 + i * (bar_h + 3)
            bar_w = int(val / max_val * (w - margin_l - 20))
            # Label
            p.setPen(QColor(CAT["subtext0"]))
            p.drawText(QRect(0, y, margin_l - 4, bar_h),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       str(label)[:16])
            # Bar
            p.fillRect(margin_l, y, max(bar_w, 2), bar_h, color)
            # Value
            p.setPen(QColor(CAT["text"]))
            p.drawText(QRect(margin_l + bar_w + 4, y, 60, bar_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       str(val))
        p.end()


# ── Tab builder ─────────────────────────────────────────────────────

PLATFORM_COLORS = {
    "twitch": "#9146ff", "kick": "#53fc18", "youtube": "#ff0000",
    "rumble": "#85c742", "soundcloud": "#ff5500", "reddit": "#ff4500",
    "direct": "#7dd3fc",
}


def build_analytics_tab(win):
    """Build the Analytics tab widget."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)

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
    hero_copy.addWidget(kicker)
    hero_copy.addWidget(title)
    hero_copy.addWidget(body)
    hero_lay.addLayout(hero_copy)

    # Metric cards row
    cards_row = QHBoxLayout()
    cards_row.setSpacing(18)
    win.analytics_total_card, win.analytics_total_val, _ = make_metric_card(
        "Total Downloads", "0", "all time")
    cards_row.addWidget(win.analytics_total_card)
    win.analytics_size_card, win.analytics_size_val, _ = make_metric_card(
        "Total Size", "0 GB", "estimated")
    cards_row.addWidget(win.analytics_size_card)
    win.analytics_top_card, win.analytics_top_val, _ = make_metric_card(
        "Top Channel", "-", "by count")
    cards_row.addWidget(win.analytics_top_card)
    win.analytics_plat_card, win.analytics_plat_val, _ = make_metric_card(
        "Top Platform", "-", "by count")
    cards_row.addWidget(win.analytics_plat_card)
    hero_lay.addLayout(cards_row)
    lay.addWidget(hero)

    # Date range filter
    filter_card = QFrame()
    filter_card.setObjectName("toolbar")
    filter_row = QHBoxLayout(filter_card)
    filter_row.setContentsMargins(0, 4, 0, 4)
    filter_row.setSpacing(10)
    filter_copy = QVBoxLayout()
    filter_copy.setSpacing(2)
    filter_title = QLabel("Focus Range")
    filter_title.setObjectName("fieldLabel")
    filter_hint = QLabel("Choose a reporting range.")
    filter_hint.setObjectName("subtleText")
    filter_hint.setWordWrap(True)
    filter_copy.addWidget(filter_title)
    filter_copy.addWidget(filter_hint)
    filter_row.addLayout(filter_copy, 1)
    win.analytics_range = QComboBox()
    win.analytics_range.addItems(["All Time", "Last 7 Days", "Last 30 Days", "Last 90 Days", "This Year"])
    win.analytics_range.currentIndexChanged.connect(lambda: _refresh_analytics(win))
    win.analytics_range.setMinimumWidth(170)
    filter_row.addWidget(win.analytics_range)
    lay.addWidget(filter_card)

    # Charts row
    charts_row = QHBoxLayout()
    charts_row.setSpacing(12)
    daily_card = QFrame()
    daily_card.setObjectName("analyticsPanel")
    daily_lay = QVBoxLayout(daily_card)
    daily_lay.setContentsMargins(8, 10, 8, 8)
    daily_lay.setSpacing(6)
    daily_title = QLabel("Capture Volume")
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
    platform_lay.setContentsMargins(8, 10, 8, 8)
    platform_lay.setSpacing(6)
    platform_title = QLabel("Platform Mix")
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
    lay.addLayout(charts_row)

    # Top channels
    channels_card = QFrame()
    channels_card.setObjectName("analyticsPanel")
    channels_lay = QVBoxLayout(channels_card)
    channels_lay.setContentsMargins(8, 10, 8, 8)
    channels_lay.setSpacing(6)
    channels_title = QLabel("Top Channels")
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
    lay.addWidget(channels_card)

    lay.addStretch(1)
    return page


def _refresh_analytics(win):
    """Recalculate analytics with bounded SQLite aggregate queries."""
    range_idx = win.analytics_range.currentIndex() if hasattr(win, "analytics_range") else 0

    # Date filter
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
    stats = _db.history_analytics(cutoff_text)

    # Metric cards
    total = stats["total"]
    win.analytics_total_val.setText(str(total))

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
