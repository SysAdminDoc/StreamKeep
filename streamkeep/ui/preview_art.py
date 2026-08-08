"""Small code-rendered preview art used until a real local thumbnail loads."""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap

from ..theme import CAT


_PLATFORM_TONES = {
    "twitch": "mauve",
    "kick": "green",
    "youtube": "red",
    "vimeo": "blue",
    "podcast": "yellow",
    "soundcloud": "peach",
    "reddit": "peach",
    "rumble": "green",
}


def preview_placeholder(title="", platform="", *, width=96, height=54, missing=False):
    """Return restrained archive-card art without touching the filesystem."""
    pixmap = QPixmap(int(width), int(height))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(0.5, 0.5, width - 1.0, height - 1.0)

    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0.0, QColor(CAT["panelHi"]))
    gradient.setColorAt(1.0, QColor(CAT["surface0"]))
    painter.setPen(QPen(QColor(CAT["stroke"]), 1.0))
    painter.setBrush(gradient)
    painter.drawRoundedRect(rect, 7, 7)

    platform_key = str(platform or "").strip().casefold()
    tone = _PLATFORM_TONES.get(platform_key, "accent")
    accent = QColor(CAT[tone])
    if missing:
        accent.setAlpha(125)
    line_pen = QPen(accent, 2.0)
    line_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(line_pen)
    for offset in (-22, -4, 14, 32, 50):
        painter.drawLine(offset, height, offset + 46, 8)

    painter.setPen(Qt.PenStyle.NoPen)
    pill = QColor(CAT["crust"])
    pill.setAlpha(205)
    painter.setBrush(pill)
    painter.drawRoundedRect(QRectF(8, height - 23, width - 16, 17), 5, 5)
    painter.setPen(QColor(CAT["subtext1"]))
    initials = (str(platform or "") or str(title or "") or "SK")[:2].upper()
    painter.drawText(
        QRectF(13, height - 23, width - 26, 17),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        initials,
    )
    if missing:
        painter.setPen(QColor(CAT["yellow"]))
        painter.drawText(
            QRectF(0, 4, width - 7, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "!",
        )
    painter.end()
    return pixmap
