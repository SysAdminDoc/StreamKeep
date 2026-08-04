from types import SimpleNamespace
from unittest import mock

from streamkeep.models import ResumeState
from streamkeep.ui import main_window_jobs


class _Signal:
    def __init__(self):
        self.handlers = []

    def connect(self, callback):
        self.handlers.append(callback)


class _ResumeWorker:
    def __init__(self):
        self.progress = _Signal()
        self.segment_done = _Signal()
        self.error = _Signal()
        self.log = _Signal()
        self.all_done = _Signal()
        self.started = False

    def start(self):
        self.started = True


def test_collect_resume_roots_deduplicates_active_and_monitor_paths(tmp_path):
    first = tmp_path / "archive"
    second = tmp_path / "other"
    first.mkdir()
    second.mkdir()
    window = SimpleNamespace(
        output_input=SimpleNamespace(text=lambda: str(first)),
        _config={"output_dir": str(first)},
        monitor=SimpleNamespace(
            entries=[
                SimpleNamespace(override_output_dir=str(first)),
                SimpleNamespace(override_output_dir=str(second)),
            ],
        ),
    )

    with mock.patch.object(main_window_jobs, "_default_output_dir", return_value=first / "default"):
        roots = main_window_jobs.MainWindowJobsMixin._collect_resume_scan_roots(window)

    assert roots == [str(first), str(first / "default"), str(second)]


def test_resume_banner_and_discard_surface_orphan_state(tmp_path):
    state = ResumeState(
        title="Interrupted show",
        output_dir=str(tmp_path),
        segments=[[0, "one", 0, 5], [1, "two", 5, 5]],
        completed=[0],
    )
    banner = SimpleNamespace(visible=None, setVisible=lambda value: setattr(banner, "visible", value))
    label = SimpleNamespace(text_value="", setText=lambda value: setattr(label, "text_value", value))
    logs = []
    statuses = []
    window = SimpleNamespace(
        resume_banner=banner,
        resume_banner_label=label,
        _resume_candidates=[state],
        _log=logs.append,
        _set_status=lambda *args: statuses.append(args),
    )
    window._refresh_resume_banner = lambda: main_window_jobs.MainWindowJobsMixin._refresh_resume_banner(window)

    main_window_jobs.MainWindowJobsMixin._refresh_resume_banner(window)
    assert banner.visible is True
    assert "Interrupted show" in label.text_value
    assert "1/2" in label.text_value

    with mock.patch.object(main_window_jobs, "clear_resume_state") as clear:
        main_window_jobs.MainWindowJobsMixin._on_resume_discard(window)

    clear.assert_called_once_with(str(tmp_path))
    assert window._resume_candidates == []
    assert "[RESUME] Discarded 1 pending resume sidecar(s)." in logs
    assert statuses[-1][0].startswith("Discarded 1 interrupted")


def test_resume_job_uses_validated_ytdlp_template_args(tmp_path):
    state = ResumeState(
        source_url="",
        platform="youtube",
        source_id="video-1",
        webpage_url="https://example.test/watch?v=video-1",
        playlist_url="https://cdn.example.test/video.m3u8",
        title="Resume me",
        quality_name="best",
        format_type="ytdlp_direct",
        output_dir=str(tmp_path),
        segments=[[0, "first", 0, 10], [1, "second", 10, 10]],
        completed=[0],
        ytdlp_template_name="safe transfer",
    )
    worker = _ResumeWorker()

    class _Window(main_window_jobs.MainWindowJobsMixin):
        def __init__(self):
            self._resume_candidates = [state]
            self._config = {
                "ytdlp_arg_templates": {
                    "safe transfer": ["--format", "bv*+ba/b"],
                },
            }
            self._parallel_connections = 2
            self.download_worker = None
            self._active_stream_info = None
            self._logs = []
            self._statuses = []

        def _refresh_resume_banner(self):
            pass

        def _log(self, message):
            self._logs.append(message)

        def _set_status(self, *args):
            self._statuses.append(args)

        def _set_download_context(self, **_kwargs):
            pass

        def _attach_resume_to_worker(self, *_args, **_kwargs):
            pass

        def _on_dl_progress(self, *_args):
            pass

        def _on_segment_done(self, *_args):
            pass

        def _on_dl_error(self, *_args):
            pass

        def _on_all_done(self, *_args):
            pass

    window = _Window()
    with mock.patch.object(
        main_window_jobs.DownloadWorker, "from_spec", return_value=worker,
    ) as from_spec:
        main_window_jobs.MainWindowJobsMixin._kick_off_resume(window, state)

    from_spec.assert_called_once()
    spec = from_spec.call_args.args[0]
    assert spec.format_type == "ytdlp_direct"
    assert spec.segments == ((1, "second", 10.0, 10.0),)
    assert spec.ytdlp_template_name == "safe transfer"
    assert spec.ytdlp_template_args == ("--format", "bv*+ba/b")
    assert window.download_worker is worker
    assert worker.started is True
    assert any("1/2 already done" in message for message in window._logs)
