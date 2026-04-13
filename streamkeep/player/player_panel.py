"""PlayerPanel — composite player widget with mpv + controls + metadata (F52).

This is the main player UI that gets embedded in the main window or shown
as a standalone dialog.  It manages the MpvWidget, PlayerControls, and
metadata display (title, channel, duration).
"""

import os

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal

from ..theme import CAT
from .mpv_widget import MpvWidget
from .player_controls import PlayerControls


class PlayerPanel(QDialog):
    """Standalone player dialog.

    Usage::

        panel = PlayerPanel(parent)
        panel.play_file("/path/to/video.mp4", title="My Stream",
                        start_secs=123.4)
        panel.exec()
        # After close, read panel.last_position for resume tracking
    """

    position_at_close = pyqtSignal(float)  # seconds when user closed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("StreamKeep Player")
        self.setMinimumSize(800, 520)
        self.resize(960, 600)
        self.setStyleSheet(f"background: {CAT['base']};")

        self._history_entry = None
        self.last_position = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Metadata bar
        meta_bar = QWidget()
        meta_bar.setFixedHeight(36)
        meta_bar.setStyleSheet(f"background: {CAT['mantle']};")
        meta_lay = QHBoxLayout(meta_bar)
        meta_lay.setContentsMargins(12, 4, 12, 4)
        self.title_label = QLabel("")
        self.title_label.setStyleSheet(f"color: {CAT['text']}; font-weight: bold;")
        meta_lay.addWidget(self.title_label, 1)
        self.channel_label = QLabel("")
        self.channel_label.setStyleSheet(f"color: {CAT['subtext0']};")
        meta_lay.addWidget(self.channel_label)
        layout.addWidget(meta_bar)

        # MPV widget
        self.mpv = MpvWidget(self)
        layout.addWidget(self.mpv, 1)

        # Transport controls
        self.controls = PlayerControls(self)
        self.controls.setFixedHeight(48)
        self.controls.setStyleSheet(f"background: {CAT['mantle']};")
        layout.addWidget(self.controls)

        # Wire controls -> mpv
        self.controls.toggle_pause.connect(self._toggle_pause)
        self.controls.stop_requested.connect(self._stop)
        self.controls.seek_requested.connect(self.mpv.seek)
        self.controls.volume_changed.connect(self._set_volume)
        self.controls.speed_changed.connect(self._set_speed)
        self.controls.subtitle_changed.connect(self.mpv.set_subtitle_track)
        self.controls.fullscreen_requested.connect(self._toggle_fullscreen)

        # Wire mpv -> controls
        self.mpv.position_changed.connect(self.controls.set_position)
        self.mpv.duration_changed.connect(self._on_duration)
        self.mpv.file_loaded.connect(self._on_file_loaded)
        self.mpv.eof_reached.connect(self._on_eof)

    def play_file(self, file_path, title="", channel="", start_secs=0.0,
                  history_entry=None):
        """Open a media file in the player."""
        self._history_entry = history_entry
        self.title_label.setText(title or os.path.basename(file_path))
        self.channel_label.setText(channel)
        self.mpv.play(file_path, start_secs=start_secs)

    def _toggle_pause(self):
        self.mpv.toggle_pause()
        self.controls.set_paused(self.mpv.paused)

    def _stop(self):
        self.last_position = self.mpv.position
        self.mpv.stop()

    def _set_volume(self, val):
        self.mpv.volume = val

    def _set_speed(self, val):
        self.mpv.speed = val

    def _on_duration(self, secs):
        self.controls.set_duration(secs)

    def _on_file_loaded(self):
        # Populate subtitle tracks
        tracks = self.mpv.subtitle_tracks
        if tracks:
            self.controls.set_subtitle_tracks(tracks)

    def _on_eof(self):
        self.controls.set_paused(True)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def closeEvent(self, event):
        self.last_position = self.mpv.position
        self.position_at_close.emit(self.last_position)
        self.mpv.destroy_mpv()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self._toggle_pause()
        elif key == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.close()
        elif key == Qt.Key.Key_Left:
            self.mpv.seek_relative(-5)
        elif key == Qt.Key.Key_Right:
            self.mpv.seek_relative(5)
        elif key == Qt.Key.Key_Up:
            self.mpv.volume = min(150, self.mpv.volume + 5)
            self.controls.vol_slider.setValue(self.mpv.volume)
        elif key == Qt.Key.Key_Down:
            self.mpv.volume = max(0, self.mpv.volume - 5)
            self.controls.vol_slider.setValue(self.mpv.volume)
        elif key == Qt.Key.Key_F:
            self._toggle_fullscreen()
        else:
            super().keyPressEvent(event)
