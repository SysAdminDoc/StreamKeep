"""Picture-in-Picture mini player — floating always-on-top window (F53).

Detaches the MpvWidget from the PlayerPanel into a compact overlay that
stays on top of other windows. Drag to move, resize from corner, click
expand to return to the main player.

Usage (from PlayerPanel)::

    pip = PiPWindow(mpv_widget=self.mpv, parent=self)
    pip.show()
    # When closed, mpv_widget is re-parented back to the PlayerPanel
"""

from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt, pyqtSignal

from ..theme import CAT


class PiPWindow(QWidget):
    """Floating mini player with always-on-top + frameless + drag-to-move."""

    closed = pyqtSignal()          # emitted when PiP is dismissed
    expand_requested = pyqtSignal()  # user wants to return to full player

    def __init__(self, mpv_widget, parent=None):
        super().__init__(parent, Qt.WindowType.Window
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumSize(240, 135)
        self.resize(360, 203)

        self._mpv = mpv_widget
        self._drag_pos = None
        self._original_parent = mpv_widget.parent()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title bar
        title_bar = QWidget()
        title_bar.setFixedHeight(28)
        title_bar.setStyleSheet(
            f"background: {CAT['mantle']}; border-bottom: 1px solid {CAT['surface0']};"
        )
        tb_lay = QHBoxLayout(title_bar)
        tb_lay.setContentsMargins(8, 0, 4, 0)
        tb_lay.setSpacing(4)

        play_btn = QPushButton("||")
        play_btn.setFixedSize(24, 20)
        play_btn.setStyleSheet(
            f"background: {CAT['surface0']}; color: {CAT['text']}; "
            f"border: none; border-radius: 3px; font-size: 10px;"
        )
        play_btn.clicked.connect(self._toggle_pause)
        tb_lay.addWidget(play_btn)
        self._play_btn = play_btn

        tb_lay.addStretch(1)

        expand_btn = QPushButton("[ ]")
        expand_btn.setFixedSize(24, 20)
        expand_btn.setToolTip("Return to full player")
        expand_btn.setStyleSheet(
            f"background: {CAT['surface0']}; color: {CAT['text']}; "
            f"border: none; border-radius: 3px; font-size: 10px;"
        )
        expand_btn.clicked.connect(self._on_expand)
        tb_lay.addWidget(expand_btn)

        close_btn = QPushButton("x")
        close_btn.setFixedSize(24, 20)
        close_btn.setStyleSheet(
            f"background: {CAT['red']}; color: {CAT['base']}; "
            f"border: none; border-radius: 3px; font-weight: bold; font-size: 10px;"
        )
        close_btn.clicked.connect(self.close)
        tb_lay.addWidget(close_btn)

        layout.addWidget(title_bar)

        # Re-parent the mpv widget into this window
        mpv_widget.setParent(self)
        layout.addWidget(mpv_widget, 1)
        mpv_widget.show()

        self.setStyleSheet(
            f"background: {CAT['base']}; "
            f"border: 2px solid {CAT['surface1']}; border-radius: 6px;"
        )

    def _toggle_pause(self):
        if self._mpv:
            self._mpv.toggle_pause()
            self._play_btn.setText(">" if self._mpv.paused else "||")

    def _on_expand(self):
        self.expand_requested.emit()
        self.close()

    def return_mpv_widget(self):
        """Re-parent the mpv widget back to its original parent."""
        if self._mpv and self._original_parent:
            self._mpv.setParent(self._original_parent)
            self._mpv.show()

    def closeEvent(self, event):
        self.return_mpv_widget()
        self.closed.emit()
        super().closeEvent(event)

    # ── Drag-to-move ────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)
