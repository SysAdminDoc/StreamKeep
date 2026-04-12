"""Clip / Trim dialog — visual scrubber + lossless (or re-encode) range cut.

Filmstrip of 20 ffmpeg-generated thumbnails with two draggable handles
(start / end) layered over them. A live preview image shows the current
playhead frame. HH:MM:SS text fields stay in sync with the handles and
accept manual entry.

Stream-copy (default) is keyframe-aligned (fast, can be off by up to one
GOP). Re-encode is frame-accurate (slower) and exposes the existing video
codec picker.
"""

import os
import subprocess

from PyQt6.QtCore import Qt, QRectF, QThread, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QLineEdit, QProgressBar, QPushButton, QTextEdit, QVBoxLayout,
)

from ..paths import _CREATE_NO_WINDOW
from ..postprocess import ClipWorker, ThumbWorker, probe_duration
from ..postprocess.codecs import VIDEO_CODECS
from ..utils import fmt_duration

THUMB_COUNT = 20
THUMB_W = 120
THUMB_H = 68     # 16:9-ish placeholder height when thumbs are missing
STRIP_PAD = 6


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


class ScrubberView(QGraphicsView):
    """QGraphicsView that lays out N thumbnails horizontally and overlays
    two draggable handles. Emits `handles_changed(start, end)` as a ratio
    of total duration.
    """

    handles_changed = pyqtSignal(float, float)   # start_ratio, end_ratio
    preview_requested = pyqtSignal(float)        # playhead_ratio

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(self.renderHints())
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(THUMB_H + 30)
        self.setStyleSheet("background: transparent;")
        self._thumb_items = []
        self._start_ratio = 0.0
        self._end_ratio = 1.0
        self._drag_target = None   # "start" | "end" | None
        self._strip_width = THUMB_COUNT * THUMB_W
        self._build_strip()

    def _build_strip(self):
        scene = self.scene()
        scene.clear()
        self._thumb_items = []
        # Placeholder rectangles so the layout is stable before thumbs land.
        for i in range(THUMB_COUNT):
            x = i * THUMB_W
            placeholder = QGraphicsRectItem(QRectF(x, 0, THUMB_W - 1, THUMB_H))
            placeholder.setPen(QPen(QColor(69, 71, 90)))
            placeholder.setBrush(QBrush(QColor(30, 30, 46)))
            scene.addItem(placeholder)
            pix_item = QGraphicsPixmapItem()
            pix_item.setPos(x, 0)
            scene.addItem(pix_item)
            self._thumb_items.append(pix_item)
        # Selection mask — dimmed overlay over the out-of-range region.
        self._dim_left = QGraphicsRectItem()
        self._dim_left.setBrush(QBrush(QColor(0, 0, 0, 160)))
        self._dim_left.setPen(QPen(Qt.PenStyle.NoPen))
        scene.addItem(self._dim_left)
        self._dim_right = QGraphicsRectItem()
        self._dim_right.setBrush(QBrush(QColor(0, 0, 0, 160)))
        self._dim_right.setPen(QPen(Qt.PenStyle.NoPen))
        scene.addItem(self._dim_right)
        # Handles: tall narrow rectangles at start/end.
        self._start_handle = QGraphicsRectItem()
        self._start_handle.setBrush(QBrush(QColor(137, 180, 250)))   # CAT blue
        self._start_handle.setPen(QPen(QColor(137, 180, 250)))
        self._start_handle.setZValue(5)
        scene.addItem(self._start_handle)
        self._end_handle = QGraphicsRectItem()
        self._end_handle.setBrush(QBrush(QColor(166, 227, 161)))     # CAT green
        self._end_handle.setPen(QPen(QColor(166, 227, 161)))
        self._end_handle.setZValue(5)
        scene.addItem(self._end_handle)
        scene.setSceneRect(0, 0, self._strip_width, THUMB_H + 2)
        self._refresh_handles()

    def set_thumb(self, index, path):
        if not (0 <= index < len(self._thumb_items)):
            return
        pix = QPixmap(path)
        if pix.isNull():
            return
        scaled = pix.scaled(
            THUMB_W - 1, THUMB_H,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumb_items[index].setPixmap(scaled)

    def set_handles(self, start_ratio, end_ratio, *, emit=True):
        start_ratio = max(0.0, min(1.0, float(start_ratio)))
        end_ratio = max(0.0, min(1.0, float(end_ratio)))
        if end_ratio < start_ratio:
            end_ratio = start_ratio
        self._start_ratio = start_ratio
        self._end_ratio = end_ratio
        self._refresh_handles()
        if emit:
            self.handles_changed.emit(self._start_ratio, self._end_ratio)

    def _refresh_handles(self):
        if self._strip_width <= 0:
            return
        sx = self._start_ratio * self._strip_width
        ex = self._end_ratio * self._strip_width
        self._start_handle.setRect(sx - 2, -2, 4, THUMB_H + 4)
        self._end_handle.setRect(ex - 2, -2, 4, THUMB_H + 4)
        self._dim_left.setRect(0, 0, max(0, sx), THUMB_H)
        self._dim_right.setRect(ex, 0, max(0, self._strip_width - ex), THUMB_H)

    def resizeEvent(self, event):
        # Scale the scene so the strip fills the view width exactly.
        self.fitInView(QRectF(0, 0, self._strip_width, THUMB_H),
                       Qt.AspectRatioMode.IgnoreAspectRatio)
        super().resizeEvent(event)

    def _ratio_from_event(self, event):
        pt = self.mapToScene(event.pos())
        return max(0.0, min(1.0, pt.x() / max(1.0, self._strip_width)))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        ratio = self._ratio_from_event(event)
        # Pick whichever handle is closer. Clicking the dead middle grabs
        # whichever side you're nearer to.
        if abs(ratio - self._start_ratio) <= abs(ratio - self._end_ratio):
            self._drag_target = "start"
            self.set_handles(ratio, self._end_ratio)
        else:
            self._drag_target = "end"
            self.set_handles(self._start_ratio, ratio)
        self.preview_requested.emit(ratio)

    def mouseMoveEvent(self, event):
        if self._drag_target is None:
            return super().mouseMoveEvent(event)
        ratio = self._ratio_from_event(event)
        if self._drag_target == "start":
            self.set_handles(min(ratio, self._end_ratio), self._end_ratio)
        else:
            self.set_handles(self._start_ratio, max(ratio, self._start_ratio))
        self.preview_requested.emit(ratio)

    def mouseReleaseEvent(self, event):
        self._drag_target = None
        super().mouseReleaseEvent(event)


class ClipDialog(QDialog):
    """Modal dialog that runs a ClipWorker with a visual scrubber."""

    def __init__(self, parent, source_path, *, default_end=None):
        super().__init__(parent)
        self.setWindowTitle("Trim / Clip")
        self.setMinimumWidth(720)
        self.setModal(True)
        self.source_path = source_path
        self._worker = None
        self._thumb_worker = None
        self._preview_worker = None
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

        # Filmstrip + preview row
        strip_row = QHBoxLayout()
        strip_row.setSpacing(10)
        self.scrubber = ScrubberView()
        self.scrubber.handles_changed.connect(self._on_handles_changed)
        self.scrubber.preview_requested.connect(self._on_preview_requested)
        strip_row.addWidget(self.scrubber, 4)
        # Live preview panel
        preview_col = QVBoxLayout()
        preview_col.setSpacing(4)
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(260, 146)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet(
            "background-color: #181825; border: 1px solid #313244; "
            "border-radius: 8px; color: #6c7086;"
        )
        self.preview_label.setText("Drag the handles")
        preview_col.addWidget(self.preview_label)
        self.preview_time_label = QLabel("—")
        self.preview_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_time_label.setStyleSheet("color: #a6adc8;")
        preview_col.addWidget(self.preview_time_label)
        strip_row.addLayout(preview_col, 0)
        root.addLayout(strip_row)

        # In / Out text fields (kept so power users can type exact times)
        range_row = QHBoxLayout()
        range_row.setSpacing(10)
        in_col = QVBoxLayout()
        in_col.setSpacing(4)
        in_col.addWidget(QLabel("Start (HH:MM:SS)"))
        self.start_input = QLineEdit("00:00:00.000")
        self.start_input.editingFinished.connect(self._on_text_changed)
        in_col.addWidget(self.start_input)
        range_row.addLayout(in_col, 1)
        out_col = QVBoxLayout()
        out_col.setSpacing(4)
        out_col.addWidget(QLabel("End (HH:MM:SS)"))
        self.end_input = QLineEdit(format_hhmmss(default_end))
        self.end_input.editingFinished.connect(self._on_text_changed)
        out_col.addWidget(self.end_input)
        range_row.addLayout(out_col, 1)
        dur_col = QVBoxLayout()
        dur_col.setSpacing(4)
        dur_col.addWidget(QLabel("Clip length"))
        self.dur_label = QLabel(format_hhmmss(default_end))
        self.dur_label.setStyleSheet("color: #a6e3a1; font-weight: 600;")
        dur_col.addWidget(self.dur_label)
        range_row.addLayout(dur_col, 0)
        root.addLayout(range_row)

        # Mode
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        self.reencode_check = QCheckBox("Frame-accurate (re-encode)")
        self.reencode_check.setToolTip(
            "Off (default): lossless stream copy — fast, but cut-points "
            "snap to the nearest keyframe.\nOn: frame-exact trim using "
            "the selected codec — slower."
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
        self.log_view.setFixedHeight(110)
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
                "[WARN] ffprobe could not determine duration. Scrubber "
                "disabled; enter values manually."
            )
        else:
            # Seed initial handle state from default_end.
            end_ratio = min(1.0, max(0.0, default_end / self._duration))
            self.scrubber.set_handles(0.0, end_ratio, emit=False)
            self._kick_off_thumbnails()

    # ── Thumbnail + preview ─────────────────────────────────────

    def _kick_off_thumbnails(self):
        self._thumb_worker = ThumbWorker(
            self.source_path, count=THUMB_COUNT, width=THUMB_W,
        )
        self._thumb_worker.thumb_ready.connect(self.scrubber.set_thumb)
        self._thumb_worker.start()

    def _on_preview_requested(self, ratio):
        if not self._duration:
            return
        at = ratio * self._duration
        self.preview_time_label.setText(format_hhmmss(at))
        # Debounce: if a preview worker is still running, let it finish
        # rather than piling up — users drag fast.
        if self._preview_worker is not None and self._preview_worker.isRunning():
            return
        pw = _PreviewWorker(self.source_path, at)
        pw.ready.connect(self._on_preview_ready)
        self._preview_worker = pw
        pw.start()

    def _on_preview_ready(self, path):
        if not path or not os.path.exists(path):
            return
        pix = QPixmap(path)
        if pix.isNull():
            return
        self.preview_label.setPixmap(pix.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    # ── Scrubber <-> text field sync ────────────────────────────

    def _on_handles_changed(self, start_ratio, end_ratio):
        if not self._duration:
            return
        s = start_ratio * self._duration
        e = end_ratio * self._duration
        # Block editingFinished re-entry
        self.start_input.blockSignals(True)
        self.end_input.blockSignals(True)
        self.start_input.setText(format_hhmmss(s))
        self.end_input.setText(format_hhmmss(e))
        self.start_input.blockSignals(False)
        self.end_input.blockSignals(False)
        self.dur_label.setText(format_hhmmss(max(0.0, e - s)))

    def _on_text_changed(self):
        if not self._duration:
            # Still update the displayed length so users have feedback.
            s = parse_hhmmss(self.start_input.text(), 0.0)
            e = parse_hhmmss(self.end_input.text(), 0.0)
            self.dur_label.setText(format_hhmmss(max(0.0, e - s)))
            return
        s = parse_hhmmss(self.start_input.text(), 0.0)
        e = parse_hhmmss(self.end_input.text(), self._duration)
        sr = min(1.0, max(0.0, s / self._duration))
        er = min(1.0, max(0.0, e / self._duration))
        if er < sr:
            er = sr
        self.scrubber.set_handles(sr, er, emit=False)
        self.dur_label.setText(format_hhmmss(max(0.0, e - s)))

    # ── Misc ────────────────────────────────────────────────────

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
        self.scrubber.setEnabled(not busy)
        self.cancel_btn.setText("Cancel" if busy else "Close")

    def _on_trim(self):
        start_s = parse_hhmmss(self.start_input.text(), 0.0)
        end_s = parse_hhmmss(self.end_input.text(), self._duration or 0.0)
        if end_s <= start_s:
            self._log_line("[ERROR] End must be greater than start.")
            return
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

    def _on_progress(self, pct, _status):
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
        for w in (self._worker, self._thumb_worker, self._preview_worker):
            try:
                if w is not None and w.isRunning():
                    w.cancel() if hasattr(w, "cancel") else None
                    w.wait(1000)
            except Exception:
                pass
        super().reject()


class _PreviewWorker(QThread):
    """One-shot preview thumbnail at an arbitrary second. Writes to a
    rotating temp path so consecutive drags don't clobber each other's
    files before QPixmap has loaded them."""

    ready = pyqtSignal(str)

    def __init__(self, src_path, at_secs):
        super().__init__()
        self.src_path = src_path
        self.at_secs = float(at_secs)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            src_dir = os.path.dirname(self.src_path) or "."
            cache_dir = os.path.join(src_dir, ".streamkeep_thumbs")
            try:
                os.makedirs(cache_dir, exist_ok=True)
            except OSError:
                self.ready.emit("")
                return
            dst = os.path.join(cache_dir, f"_preview_{int(self.at_secs * 1000):010d}.jpg")
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, self.at_secs):.3f}",
                "-i", self.src_path,
                "-frames:v", "1",
                "-vf", "scale=260:-2",
                "-q:v", "4",
                "-y", dst,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_CREATE_NO_WINDOW,
                    timeout=15,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                self.ready.emit("")
                return
            if self._cancel:
                return
            if proc.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
                self.ready.emit(dst)
            else:
                self.ready.emit("")
        except Exception:
            self.ready.emit("")
