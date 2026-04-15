"""First-Run Onboarding Wizard — multi-step setup on first launch (F76).

Steps:
  1. Welcome + ffmpeg detection
  2. Output directory selection
  3. Theme preference (dark/light)
  4. Done — mark first_run_complete

Skippable at any step via "Skip All" button.
"""

import subprocess

from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..theme import CAT
from ..paths import _CREATE_NO_WINDOW
from ..utils import default_output_dir


class OnboardingWizard(QDialog):
    """Multi-step first-run setup wizard."""

    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to StreamKeep")
        self.setFixedSize(560, 400)
        self._config = config or {}
        self._output_dir = ""
        self._theme = "dark"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        # Build pages
        self._stack.addWidget(self._page_welcome())
        self._stack.addWidget(self._page_output())
        self._stack.addWidget(self._page_theme())
        self._stack.addWidget(self._page_done())

        # Navigation buttons
        nav = QHBoxLayout()
        self._skip_btn = QPushButton("Skip All")
        self._skip_btn.setObjectName("ghost")
        self._skip_btn.clicked.connect(self._skip_all)
        nav.addWidget(self._skip_btn)
        nav.addStretch(1)
        self._back_btn = QPushButton("Back")
        self._back_btn.setObjectName("secondary")
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.setEnabled(False)
        nav.addWidget(self._back_btn)
        self._next_btn = QPushButton("Next")
        self._next_btn.setObjectName("primary")
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._next_btn)
        layout.addLayout(nav)

    def _page_welcome(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)
        title = QLabel("Welcome to StreamKeep")
        title.setObjectName("heroTitle")
        lay.addWidget(title)
        lay.addWidget(QLabel(
            "StreamKeep is a multi-platform stream and VOD downloader.\n\n"
            "This wizard will help you get set up in a few steps."
        ))

        # FFmpeg check
        self._ffmpeg_label = QLabel("Checking for ffmpeg...")
        lay.addWidget(self._ffmpeg_label)
        self._check_ffmpeg()

        lay.addStretch(1)
        return page

    def _page_output(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)
        lay.addWidget(QLabel("Where should StreamKeep save recordings?"))
        self._output_dir = default_output_dir()
        self._output_label = QLabel(self._output_dir)
        self._output_label.setWordWrap(True)
        self._output_label.setStyleSheet(
            f"background: {CAT['surface0']}; padding: 10px 12px; "
            f"border-radius: 14px; border: 1px solid {CAT['stroke']};"
        )
        lay.addWidget(self._output_label)
        browse_btn = QPushButton("Choose Folder...")
        browse_btn.clicked.connect(self._browse_output)
        lay.addWidget(browse_btn)
        lay.addStretch(1)
        return page

    def _page_theme(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)
        lay.addWidget(QLabel("Choose your theme:"))
        self._dark_radio = QRadioButton("Dark (Catppuccin Mocha)")
        self._dark_radio.setChecked(True)
        self._light_radio = QRadioButton("Light (Catppuccin Latte)")
        self._system_radio = QRadioButton("Follow system")
        lay.addWidget(self._dark_radio)
        lay.addWidget(self._light_radio)
        lay.addWidget(self._system_radio)
        lay.addStretch(1)
        return page

    def _page_done(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)
        done_title = QLabel("You're all set!")
        done_title.setObjectName("sectionTitle")
        lay.addWidget(done_title)
        lay.addWidget(QLabel(
            "StreamKeep is ready to use.\n\n"
            "Paste a URL in the Download tab to get started, or add\n"
            "channels in the Monitor tab for automatic recording."
        ))
        lay.addStretch(1)
        return page

    def _check_ffmpeg(self):
        try:
            r = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, timeout=5,
                creationflags=_CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                self._ffmpeg_label.setText("ffmpeg found.")
                self._ffmpeg_label.setStyleSheet(f"color: {CAT['green']};")
            else:
                self._ffmpeg_label.setText("ffmpeg not found. Please install it and add to PATH.")
                self._ffmpeg_label.setStyleSheet(f"color: {CAT['red']};")
        except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
            self._ffmpeg_label.setText("ffmpeg not found. Please install it and add to PATH.")
            self._ffmpeg_label.setStyleSheet(f"color: {CAT['red']};")

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self._output_dir)
        if d:
            self._output_dir = d
            self._output_label.setText(d)

    def _go_next(self):
        idx = self._stack.currentIndex()
        if idx >= self._stack.count() - 1:
            self._finish()
            return
        self._stack.setCurrentIndex(idx + 1)
        self._back_btn.setEnabled(True)
        if idx + 1 == self._stack.count() - 1:
            self._next_btn.setText("Finish")

    def _go_back(self):
        idx = self._stack.currentIndex()
        if idx > 0:
            self._stack.setCurrentIndex(idx - 1)
            self._next_btn.setText("Next")
        if idx - 1 == 0:
            self._back_btn.setEnabled(False)

    def _skip_all(self):
        self._config["first_run_complete"] = True
        self.accept()

    def _finish(self):
        # Apply settings
        self._config["output_dir"] = self._output_dir
        if self._light_radio.isChecked():
            self._config["theme"] = "light"
            self._theme = "light"
        elif self._system_radio.isChecked():
            self._config["theme"] = "system"
            self._theme = "system"
        else:
            self._config["theme"] = "dark"
            self._theme = "dark"
        self._config["first_run_complete"] = True
        self.accept()

    @property
    def chosen_theme(self):
        return self._theme

    @property
    def chosen_output_dir(self):
        return self._output_dir
