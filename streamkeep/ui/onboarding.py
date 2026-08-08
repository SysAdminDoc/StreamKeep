"""First-run onboarding wizard — polished multi-step setup for new users."""

from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QApplication, QProgressBar, QRadioButton, QStackedWidget,
    QVBoxLayout, QWidget,
)
from PyQt6.QtCore import QThread, pyqtSignal

from ..capabilities import format_capability_problem, get_runtime_capabilities
from ..i18n import TranslatableDialog
from ..extractors.ytdlp import ytdlp_runtime_status
from ..theme import apply_theme
from ..utils import default_output_dir, flatpak_archive_guidance
from .widgets import (
    make_dialog_hero,
    make_dialog_section,
    make_status_banner,
    update_status_banner,
)


class _CapabilityProbeWorker(QThread):
    """Probe managed runtimes without blocking the first-run dialog."""

    progress = pyqtSignal(str)
    result_ready = pyqtSignal(object, object)
    probe_failed = pyqtSignal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = dict(config or {})

    def run(self):
        try:
            self.progress.emit("Checking managed runtimes…")
            registry = get_runtime_capabilities(
                refresh=True,
                config=self._config,
                should_cancel=self.isInterruptionRequested,
            )
            if registry is None or self.isInterruptionRequested():
                return
            self.progress.emit("Checking the yt-dlp fallback…")
            # ytdlp_runtime_status reads the registry cache populated above;
            # this is a cheap formatting pass, not a second probe.
            runtime_status = ytdlp_runtime_status(config=self._config)
        except Exception as error:
            if not self.isInterruptionRequested():
                self.probe_failed.emit(str(error))
            return
        if not self.isInterruptionRequested():
            self.result_ready.emit(registry, runtime_status)


class OnboardingWizard(TranslatableDialog):
    """Multi-step first-run setup wizard."""

    _STEP_TITLES = [
        "Welcome",
        "Save location",
        "Appearance",
        "Ready to go",
    ]

    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to StreamKeep")
        self.setFixedSize(640, 500)
        self.setModal(True)
        self._config = config if config is not None else {}
        self._output_dir = str(default_output_dir())
        self._theme = "dark"
        self._skipped = False
        self._probe_worker = None
        self._capability_registry = {}
        self._ytdlp_status = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)

        hero, _, _, self._hero_badge = make_dialog_hero(
            "Set up StreamKeep in a minute",
            "Pick where recordings go, choose how the app should look, "
            "and confirm the essentials before you start downloading.",
            eyebrow="FIRST RUN",
            badge_text="4-step setup",
        )
        layout.addWidget(hero)

        step_row = QHBoxLayout()
        step_row.setContentsMargins(2, 0, 2, 0)
        step_row.setSpacing(8)
        self._step_label = QLabel("")
        self._step_label.setObjectName("fieldLabel")
        step_row.addWidget(self._step_label)
        step_row.addStretch(1)
        self._step_meta = QLabel("")
        self._step_meta.setObjectName("fieldHint")
        step_row.addWidget(self._step_meta)
        layout.addLayout(step_row)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        self._stack.addWidget(self._page_welcome())
        self._stack.addWidget(self._page_output())
        self._stack.addWidget(self._page_theme())
        self._stack.addWidget(self._page_done())

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self._skip_btn = QPushButton("Skip setup")
        self._skip_btn.setObjectName("ghost")
        self._skip_btn.clicked.connect(self._skip_all)
        nav.addWidget(self._skip_btn)
        nav.addStretch(1)
        self._back_btn = QPushButton("Back")
        self._back_btn.setObjectName("secondary")
        self._back_btn.clicked.connect(self._go_back)
        nav.addWidget(self._back_btn)
        self._next_btn = QPushButton("Continue")
        self._next_btn.setObjectName("primary")
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._next_btn)
        layout.addLayout(nav)

        self._start_capability_probe()
        self._update_summary()
        self._update_nav()

    def _page_welcome(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        section, content = make_dialog_section(
            "System readiness",
            "StreamKeep works best with ffmpeg available in PATH. "
            "If it is missing, the app can still open but downloads will not start yet.",
        )
        self._ffmpeg_banner, self._ffmpeg_title, self._ffmpeg_body = make_status_banner()
        content.addWidget(self._ffmpeg_banner)
        self._ytdlp_banner, self._ytdlp_title, self._ytdlp_body = make_status_banner()
        content.addWidget(self._ytdlp_banner)
        self._probe_status = QLabel("Checking managed runtimes…")
        self._probe_status.setObjectName("fieldHint")
        self._probe_status.setWordWrap(True)
        content.addWidget(self._probe_status)
        self._probe_progress = QProgressBar()
        self._probe_progress.setObjectName("onboardingProbeProgress")
        self._probe_progress.setAccessibleName("Runtime readiness progress")
        self._probe_progress.setRange(0, 0)
        self._probe_progress.setTextVisible(False)
        content.addWidget(self._probe_progress)

        checklist, checklist_content = make_dialog_section(
            "What this setup covers",
            "These defaults can all be changed later in Settings.",
        )
        for line in [
            "Choose a default recording folder.",
            "Pick dark, light, high-contrast, or follow-system appearance.",
            "Start with safe, clean defaults and skip the rest for now.",
        ]:
            item = QLabel(f"• {line}")
            item.setObjectName("sectionBody")
            item.setWordWrap(True)
            checklist_content.addWidget(item)

        outer.addWidget(section)
        outer.addWidget(checklist)
        outer.addStretch(1)
        return page

    def _page_output(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        section, content = make_dialog_section(
            "Default recording folder",
            "This becomes the starting point for downloads, monitoring, and saved metadata.",
        )
        self._output_banner, self._output_title, self._output_body = make_status_banner()
        content.addWidget(self._output_banner)

        browse_row = QHBoxLayout()
        browse_row.setSpacing(8)
        browse_btn = QPushButton("Choose folder…")
        browse_btn.setObjectName("primary")
        browse_btn.clicked.connect(self._browse_output)
        browse_row.addWidget(browse_btn)
        reset_btn = QPushButton("Use recommended folder")
        reset_btn.setObjectName("secondary")
        reset_btn.clicked.connect(self._reset_output)
        browse_row.addWidget(reset_btn)
        browse_row.addStretch(1)
        content.addLayout(browse_row)

        note = QLabel(
            "Tip: keeping recordings under one root makes storage cleanup, "
            "history, and auto-record profiles much easier to manage. "
            + flatpak_archive_guidance()
        )
        note.setObjectName("fieldHint")
        note.setWordWrap(True)
        content.addWidget(note)

        outer.addWidget(section)
        outer.addStretch(1)
        return page

    def _page_theme(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        section, content = make_dialog_section(
            "Appearance",
            "Choose the default look for the app. You can switch themes any time without restarting.",
        )
        self._theme_banner, self._theme_title, self._theme_body = make_status_banner()
        content.addWidget(self._theme_banner)

        self._dark_radio = QRadioButton("Dark — richer contrast and a focused, cinematic workspace")
        self._dark_radio.setChecked(True)
        self._light_radio = QRadioButton("Light — brighter surfaces and cleaner daytime readability")
        self._high_contrast_radio = QRadioButton(
            "High Contrast — maximum separation for easier readability"
        )
        self._system_radio = QRadioButton("Follow system — stay in sync with your OS preference")
        for radio in (
            self._dark_radio,
            self._light_radio,
            self._high_contrast_radio,
            self._system_radio,
        ):
            radio.toggled.connect(self._update_summary)
            content.addWidget(radio)

        outer.addWidget(section)
        outer.addStretch(1)
        return page

    def _page_done(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        section, content = make_dialog_section(
            "Ready to start",
            "You can paste a stream URL right away, or set up monitor profiles for automatic recording later.",
        )
        self._done_banner, self._done_title, self._done_body = make_status_banner()
        content.addWidget(self._done_banner)

        next_steps = QLabel(
            "Good next steps:\n"
            "• Paste a URL in Download to test your setup.\n"
            "• Open Monitor to track channels automatically.\n"
            "• Visit Settings if you want cookies, proxies, or file templates."
        )
        next_steps.setObjectName("sectionBody")
        next_steps.setWordWrap(True)
        content.addWidget(next_steps)

        outer.addWidget(section)
        outer.addStretch(1)
        return page

    def _start_capability_probe(self):
        """Start the one runtime scan after the dialog has been constructed."""
        self._set_probe_pending("Checking managed runtimes…")
        self._probe_worker = _CapabilityProbeWorker(self._config, self)
        self._probe_worker.progress.connect(self._set_probe_pending)
        self._probe_worker.result_ready.connect(self._on_capability_probe_ready)
        self._probe_worker.probe_failed.connect(self._on_capability_probe_failed)
        self._probe_worker.start()

    def _set_probe_pending(self, message):
        if hasattr(self, "_probe_status"):
            self._probe_status.setText(str(message or "Checking managed runtimes…"))
        if hasattr(self, "_probe_progress"):
            self._probe_progress.setRange(0, 0)
            self._probe_progress.setValue(0)
        if hasattr(self, "_ffmpeg_title"):
            update_status_banner(
                self._ffmpeg_banner,
                self._ffmpeg_title,
                self._ffmpeg_body,
                title="Checking FFmpeg readiness",
                body="The runtime check is running in the background.",
                tone="info",
            )
        if hasattr(self, "_ytdlp_title"):
            update_status_banner(
                self._ytdlp_banner,
                self._ytdlp_title,
                self._ytdlp_body,
                title="Checking yt-dlp fallback",
                body="The runtime check is running in the background.",
                tone="info",
            )

    def _on_capability_probe_ready(self, registry, runtime_status):
        self._capability_registry = dict(registry or {})
        self._ytdlp_status = dict(runtime_status or {})
        if hasattr(self, "_probe_progress"):
            self._probe_progress.setRange(0, 1)
            self._probe_progress.setValue(1)
        if hasattr(self, "_probe_status"):
            self._probe_status.setText("Runtime readiness check complete.")
        self._check_ffmpeg(self._capability_registry)
        self._check_ytdlp_runtime(self._ytdlp_status)

    def _on_capability_probe_failed(self, message):
        if hasattr(self, "_probe_progress"):
            self._probe_progress.setRange(0, 1)
            self._probe_progress.setValue(1)
        if hasattr(self, "_probe_status"):
            self._probe_status.setText(
                "Runtime checks are unavailable; you can continue and repair them later."
            )
        update_status_banner(
            self._ffmpeg_banner,
            self._ffmpeg_title,
            self._ffmpeg_body,
            title="Runtime check unavailable",
            body=str(message or "The managed runtime probe failed."),
            tone="warning",
        )
        update_status_banner(
            self._ytdlp_banner,
            self._ytdlp_title,
            self._ytdlp_body,
            title="Runtime check unavailable",
            body="You can review optional runtime setup later in Settings.",
            tone="warning",
        )

    def _check_ffmpeg(self, registry=None):
        registry = registry if registry is not None else self._capability_registry
        ffmpeg = registry.get("ffmpeg", {})
        if ffmpeg.get("supported"):
            tone = "success"
            title = "FFmpeg ready"
            message = (
                f"FFmpeg {ffmpeg['version']} from {ffmpeg['provenance']}: "
                f"{ffmpeg['path']}"
            )
        else:
            tone = "warning"
            title = "FFmpeg needs repair"
            message = format_capability_problem(ffmpeg)
        update_status_banner(
            self._ffmpeg_banner,
            self._ffmpeg_title,
            self._ffmpeg_body,
            title=title,
            body=message,
            tone=tone,
        )

    def _check_ytdlp_runtime(self, status=None):
        status = status if status is not None else self._ytdlp_status
        state = status.get("state", "missing")
        if state == "ready":
            title = "yt-dlp fallback is ready"
            tone = "success"
        elif state == "limited":
            title = "yt-dlp fallback is limited"
            tone = "warning"
        else:
            title = "yt-dlp fallback is missing"
            tone = "warning"
        update_status_banner(
            self._ytdlp_banner,
            self._ytdlp_title,
            self._ytdlp_body,
            title=title,
            body=status.get("detail", "Install yt-dlp for long-tail site support."),
            tone=tone,
        )

    def _browse_output(self):
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select output folder",
            self._output_dir,
        )
        if chosen:
            self._output_dir = chosen
            self._update_summary()

    def _reset_output(self):
        self._output_dir = str(default_output_dir())
        self._update_summary()

    def _update_summary(self):
        if self._light_radio.isChecked() if hasattr(self, "_light_radio") else False:
            self._theme = "light"
            theme_title = "Light theme selected"
            theme_body = "Bright surfaces with softer contrast for daytime use."
        elif self._high_contrast_radio.isChecked() if hasattr(self, "_high_contrast_radio") else False:
            self._theme = "high_contrast"
            theme_title = "High Contrast theme selected"
            theme_body = "Maximum color separation for easier reading and focus."
        elif self._system_radio.isChecked() if hasattr(self, "_system_radio") else False:
            self._theme = "system"
            theme_title = "Following system theme"
            theme_body = "StreamKeep will follow your OS appearance preference."
        else:
            self._theme = "dark"
            theme_title = "Dark theme selected"
            theme_body = "A calmer, higher-contrast workspace tuned for media-heavy workflows."

        if hasattr(self, "_output_banner"):
            update_status_banner(
                self._output_banner,
                self._output_title,
                self._output_body,
                title="Recordings will be saved here",
                body=self._output_dir,
                tone="info",
            )
        if hasattr(self, "_theme_banner"):
            update_status_banner(
                self._theme_banner,
                self._theme_title,
                self._theme_body,
                title=theme_title,
                body=theme_body,
                tone="info",
            )
        if hasattr(self, "_done_banner"):
            update_status_banner(
                self._done_banner,
                self._done_title,
                self._done_body,
                title="Setup summary",
                body=f"Theme: {self._theme} • Output folder: {self._output_dir}",
                tone="success",
            )

    def _update_nav(self):
        idx = self._stack.currentIndex()
        total = self._stack.count()
        self._step_label.setText(f"Step {idx + 1} of {total}")
        self._step_meta.setText(self._STEP_TITLES[idx])
        self._back_btn.setEnabled(idx > 0)
        self._next_btn.setText("Finish setup" if idx == total - 1 else "Continue")
        self._hero_badge.setText(f"{idx + 1}/{total}")
        self._hero_badge.setVisible(True)

    def _go_next(self):
        idx = self._stack.currentIndex()
        if idx >= self._stack.count() - 1:
            self._finish()
            return
        self._stack.setCurrentIndex(idx + 1)
        self._update_summary()
        self._update_nav()

    def _go_back(self):
        idx = self._stack.currentIndex()
        if idx <= 0:
            return
        self._stack.setCurrentIndex(idx - 1)
        self._update_nav()

    def _skip_all(self):
        self._skipped = True
        self._config["first_run_complete"] = True
        self.accept()

    def _finish(self):
        self._config["output_dir"] = self._output_dir
        self._config["theme"] = self._theme
        self._config["first_run_complete"] = True
        apply_theme(self._theme, app=QApplication.instance())
        self.accept()

    def closeEvent(self, event):
        worker = self._probe_worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait(7000)
        super().closeEvent(event)

    @property
    def chosen_theme(self):
        return self._theme

    @property
    def chosen_output_dir(self):
        return self._output_dir

    @property
    def skipped(self):
        return self._skipped
