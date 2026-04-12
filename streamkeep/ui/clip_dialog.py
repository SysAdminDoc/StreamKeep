"""Clip / Trim dialog — lossless (keyframe) or re-encode (frame-accurate)
range cut for any file in the output folder or history.

Deliberately uses HH:MM:SS text fields rather than QMediaPlayer because
QMediaPlayer's codec matrix on Windows (MediaFoundation) and Linux
(GStreamer) is spotty for ffmpeg-produced .mp4/.mkv files and would
introduce a brittle preview. The duration is probed via ffprobe so the
range is constrained to the real length of the file.
"""

import os
import subprocess

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QProgressBar, QPushButton, QTextEdit, QVBoxLayout,
)

from ..paths import _CREATE_NO_WINDOW
from ..postprocess import ClipWorker
from ..postprocess.codecs import VIDEO_CODECS
from ..utils import fmt_duration


def probe_duration(path):
    """Return file duration in seconds via ffprobe, or 0 on failure."""
    if not path or not os.path.exists(path):
        return 0.0
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            creationflags=_CREATE_NO_WINDOW,
            encoding="utf-8", errors="replace",
            timeout=10,
        )
        return float(out.strip())
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired, ValueError, OSError):
        return 0.0


def parse_hhmmss(text, fallback=0.0):
    """Parse 'HH:MM:SS', 'MM:SS', 'SSSS', or '12.5' into seconds."""
    text = (text or "").strip()
    if not text:
        return fallback
    if ":" in text:
        parts = text.split(":")
        try:
            parts = [float(p) for p in parts]
        except ValueError:
            return fallback
        total = 0.0
        for p in parts:
            total = total * 60.0 + p
        return total
    try:
        return float(text)
    except ValueError:
        return fallback


def format_hhmmss(secs):
    secs = max(0.0, float(secs))
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = secs - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


class ClipDialog(QDialog):
    """Modal dialog that runs a ClipWorker on a single file."""

    def __init__(self, parent, source_path, *, default_end=None):
        super().__init__(parent)
        self.setWindowTitle("Trim / Clip")
        self.setMinimumWidth(560)
        self.setModal(True)
        self.source_path = source_path
        self._worker = None
        self._duration = probe_duration(source_path)
        if default_end is None:
            default_end = self._duration or 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        head = QLabel(
            f"<b>Source:</b> {os.path.basename(source_path)}"
            f"<br><span style='color:#a6adc8'>Duration: "
            f"{fmt_duration(self._duration) if self._duration else 'unknown'}</span>"
        )
        head.setWordWrap(True)
        root.addWidget(head)

        # In / Out range
        range_row = QHBoxLayout()
        range_row.setSpacing(10)
        in_col = QVBoxLayout()
        in_col.setSpacing(4)
        in_col.addWidget(QLabel("Start (HH:MM:SS)"))
        self.start_input = QLineEdit("00:00:00.000")
        in_col.addWidget(self.start_input)
        range_row.addLayout(in_col, 1)
        out_col = QVBoxLayout()
        out_col.setSpacing(4)
        out_col.addWidget(QLabel("End (HH:MM:SS)"))
        self.end_input = QLineEdit(format_hhmmss(default_end))
        out_col.addWidget(self.end_input)
        range_row.addLayout(out_col, 1)
        root.addLayout(range_row)

        # Mode
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        self.reencode_check = QCheckBox("Frame-accurate (re-encode)")
        self.reencode_check.setToolTip(
            "Off (default): lossless stream copy — fast, but the cut snaps "
            "to the nearest keyframe (can be off by up to a few seconds).\n"
            "On: frame-exact trim using the selected codec — slower."
        )
        self.reencode_check.toggled.connect(self._on_reencode_toggled)
        mode_row.addWidget(self.reencode_check)
        self.codec_combo = QComboBox()
        for key, label in VIDEO_CODECS:
            if key == "copy":
                continue
            self.codec_combo.addItem(label, userData=key)
        self.codec_combo.setVisible(False)
        mode_row.addWidget(self.codec_combo)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        # Output path
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(QLabel("Output:"))
        self.out_input = QLineEdit(self._default_output_path(source_path))
        out_row.addWidget(self.out_input, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.setObjectName("secondary")
        browse_btn.clicked.connect(self._on_browse)
        out_row.addWidget(browse_btn)
        root.addLayout(out_row)

        # Progress + log
        self.progress = QProgressBar()
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(120)
        root.addWidget(self.log_view)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton("Close")
        self.cancel_btn.setObjectName("secondary")
        self.cancel_btn.clicked.connect(self._on_close_or_cancel)
        btn_row.addWidget(self.cancel_btn)
        self.trim_btn = QPushButton("Trim")
        self.trim_btn.setObjectName("primary")
        self.trim_btn.clicked.connect(self._on_trim)
        btn_row.addWidget(self.trim_btn)
        root.addLayout(btn_row)

        if not self._duration:
            self._log_line(
                "[WARN] ffprobe could not determine duration — the end "
                "field is unconstrained; enter a real value."
            )

    def _default_output_path(self, source_path):
        base, ext = os.path.splitext(source_path)
        return f"{base}.clip{ext}"

    def _on_browse(self):
        start_dir = os.path.dirname(self.out_input.text().strip()) or ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Clip output", self.out_input.text().strip() or start_dir
        )
        if path:
            self.out_input.setText(path)

    def _on_reencode_toggled(self, checked):
        self.codec_combo.setVisible(bool(checked))

    def _log_line(self, msg):
        self.log_view.append(msg)

    def _set_busy(self, busy):
        self.trim_btn.setEnabled(not busy)
        self.start_input.setEnabled(not busy)
        self.end_input.setEnabled(not busy)
        self.out_input.setEnabled(not busy)
        self.reencode_check.setEnabled(not busy)
        self.codec_combo.setEnabled(not busy)
        self.cancel_btn.setText("Cancel" if busy else "Close")

    def _on_trim(self):
        start_s = parse_hhmmss(self.start_input.text(), 0.0)
        end_s = parse_hhmmss(self.end_input.text(), self._duration or 0.0)
        if end_s <= start_s:
            self._log_line("[ERROR] End must be greater than start.")
            return
        if self._duration and end_s > self._duration + 0.5:
            self._log_line(
                f"[WARN] End ({end_s:.2f}s) exceeds source duration "
                f"({self._duration:.2f}s) — ffmpeg will stop at EOF."
            )
        out_path = self.out_input.text().strip()
        if not out_path:
            self._log_line("[ERROR] Output path is empty.")
            return
        if os.path.abspath(out_path) == os.path.abspath(self.source_path):
            self._log_line("[ERROR] Output cannot overwrite the source file.")
            return
        codec = "libx264"
        if self.reencode_check.isChecked():
            codec = self.codec_combo.currentData() or "libx264"
        self._worker = ClipWorker(
            self.source_path,
            out_path,
            start_s,
            end_s,
            reencode=self.reencode_check.isChecked(),
            video_codec=codec,
            audio_codec="aac",
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(self._log_line)
        self._worker.done.connect(self._on_done)
        self._set_busy(True)
        self._worker.start()

    def _on_progress(self, pct, status):
        self.progress.setValue(int(pct))

    def _on_done(self, ok, out_path):
        self._set_busy(False)
        self.progress.setValue(100 if ok else 0)
        if ok:
            self._log_line(f"[DONE] Saved {out_path}")
        else:
            self._log_line("[DONE] Trim failed — see log above.")

    def _on_close_or_cancel(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
            self._set_busy(False)
            return
        self.accept()

    def reject(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        super().reject()
