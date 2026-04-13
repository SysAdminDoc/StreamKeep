"""Player transport controls — play/pause, seek, volume, speed, subs (F52)."""

from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal

def _fmt_time(secs):
    s = max(0, int(secs or 0))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class PlayerControls(QWidget):
    """Transport bar: play/pause, seek slider, volume, speed, subtitle picker."""

    seek_requested = pyqtSignal(float)      # absolute seconds
    toggle_pause = pyqtSignal()
    stop_requested = pyqtSignal()
    volume_changed = pyqtSignal(int)
    speed_changed = pyqtSignal(float)
    subtitle_changed = pyqtSignal(object)   # track_id or False
    fullscreen_requested = pyqtSignal()
    pip_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0.0
        self._seeking = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        # Play/Pause
        self.play_btn = QPushButton("||")
        self.play_btn.setFixedWidth(36)
        self.play_btn.setObjectName("secondary")
        self.play_btn.clicked.connect(self.toggle_pause.emit)
        lay.addWidget(self.play_btn)

        # Stop
        stop_btn = QPushButton("[]")
        stop_btn.setFixedWidth(36)
        stop_btn.setObjectName("secondary")
        stop_btn.clicked.connect(self.stop_requested.emit)
        lay.addWidget(stop_btn)

        # Time label (current)
        self.time_label = QLabel("0:00")
        self.time_label.setFixedWidth(60)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.time_label)

        # Seek slider
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.sliderPressed.connect(self._on_seek_press)
        self.seek_slider.sliderReleased.connect(self._on_seek_release)
        self.seek_slider.sliderMoved.connect(self._on_seek_move)
        lay.addWidget(self.seek_slider, 1)

        # Duration label
        self.dur_label = QLabel("0:00")
        self.dur_label.setFixedWidth(60)
        lay.addWidget(self.dur_label)

        # Volume
        vol_label = QLabel("Vol")
        vol_label.setFixedWidth(24)
        lay.addWidget(vol_label)
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 150)
        self.vol_slider.setValue(100)
        self.vol_slider.setFixedWidth(80)
        self.vol_slider.valueChanged.connect(self.volume_changed.emit)
        lay.addWidget(self.vol_slider)

        # Speed
        self.speed_combo = QComboBox()
        self.speed_combo.setFixedWidth(70)
        for s in ("0.25x", "0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x", "3x"):
            self.speed_combo.addItem(s, float(s.rstrip("x")))
        self.speed_combo.setCurrentIndex(3)  # 1x
        self.speed_combo.currentIndexChanged.connect(
            lambda i: self.speed_changed.emit(self.speed_combo.itemData(i))
        )
        lay.addWidget(self.speed_combo)

        # Subtitle track
        self.sub_combo = QComboBox()
        self.sub_combo.setFixedWidth(90)
        self.sub_combo.addItem("Subs Off", False)
        self.sub_combo.currentIndexChanged.connect(
            lambda i: self.subtitle_changed.emit(self.sub_combo.itemData(i))
        )
        lay.addWidget(self.sub_combo)

        # PiP (F53)
        pip_btn = QPushButton("PiP")
        pip_btn.setFixedWidth(36)
        pip_btn.setObjectName("secondary")
        pip_btn.setToolTip("Picture-in-Picture mini player")
        pip_btn.clicked.connect(self.pip_requested.emit)
        lay.addWidget(pip_btn)

        # Fullscreen
        fs_btn = QPushButton("[ ]")
        fs_btn.setFixedWidth(36)
        fs_btn.setObjectName("secondary")
        fs_btn.setToolTip("Toggle fullscreen")
        fs_btn.clicked.connect(self.fullscreen_requested.emit)
        lay.addWidget(fs_btn)

    def set_position(self, secs):
        """Update the seek slider and time label from the current position."""
        if self._seeking:
            return
        self.time_label.setText(_fmt_time(secs))
        if self._duration > 0:
            pct = min(1000, int(secs / self._duration * 1000))
            self.seek_slider.setValue(pct)

    def set_duration(self, secs):
        self._duration = secs
        self.dur_label.setText(_fmt_time(secs))

    def set_paused(self, paused):
        self.play_btn.setText(">" if paused else "||")

    def set_subtitle_tracks(self, tracks):
        """Populate the subtitle combo. *tracks* is [(id, label), ...]."""
        self.sub_combo.blockSignals(True)
        self.sub_combo.clear()
        self.sub_combo.addItem("Subs Off", False)
        for tid, label in tracks:
            self.sub_combo.addItem(label[:20], tid)
        self.sub_combo.blockSignals(False)

    def _on_seek_press(self):
        self._seeking = True

    def _on_seek_move(self, val):
        if self._duration > 0:
            secs = val / 1000.0 * self._duration
            self.time_label.setText(_fmt_time(secs))

    def _on_seek_release(self):
        self._seeking = False
        if self._duration > 0:
            secs = self.seek_slider.value() / 1000.0 * self._duration
            self.seek_requested.emit(secs)
