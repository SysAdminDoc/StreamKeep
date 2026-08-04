import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest

from streamkeep.integrations import auto_editor
from streamkeep.intelligence import summarize
from streamkeep.postprocess import chat_render_worker, clip_worker, codecs
from streamkeep.postprocess import convert_worker, normalization, processor
from streamkeep.postprocess import scene_worker, thumb_worker, transcribe_worker


class _CompletedProcess:
    def __init__(self, returncode=0, *, stdout="", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _signal_values(signal):
    values = []
    signal.connect(lambda *args: values.append(args))
    return values


def test_clip_command_shapes_keep_paths_as_argv_values(qt_application):
    source = "--source file.mp4"
    output = "--clip output.mp4"
    with mock.patch.object(clip_worker, "resolve_tool_command", return_value="ffmpeg"):
        copy_cmd = clip_worker.ClipWorker(source, output, 2, 8)._build_cmd()
        encode_cmd = clip_worker.ClipWorker(
            source,
            output,
            2,
            8,
            reencode=True,
            video_filter="scale=1280:-2",
        )._build_cmd()

    assert "-nostdin" in copy_cmd
    assert copy_cmd[copy_cmd.index("-i") + 1] == source
    assert copy_cmd[-1] == output
    assert copy_cmd[copy_cmd.index("-c") + 1] == "copy"
    assert encode_cmd[encode_cmd.index("-i") + 1] == source
    assert encode_cmd[encode_cmd.index("-vf") + 1] == "scale=1280:-2"
    assert encode_cmd[-1] == output
    assert encode_cmd.index("-ss") > encode_cmd.index("-i")


def test_clip_failure_removes_nonempty_partial_output(tmp_path, qt_application):
    source = tmp_path / "source.mp4"
    output = tmp_path / "clip.mp4"
    source.write_bytes(b"source")

    def fake_popen(cmd, **_kwargs):
        Path(cmd[-1]).write_bytes(b"partial encoder output")
        return SimpleNamespace(stderr=[], returncode=1, wait=lambda: None)

    worker = clip_worker.ClipWorker(str(source), str(output), 0, 5)
    done = _signal_values(worker.done)
    with mock.patch.object(clip_worker, "resolve_tool_command", return_value="ffmpeg"), \
            mock.patch.object(clip_worker.subprocess, "Popen", side_effect=fake_popen):
        worker.run()

    assert done == [(False, "")]
    assert not output.exists()


def test_highlight_concat_command_is_safe_and_failed_output_is_removed(
    tmp_path, qt_application,
):
    source = tmp_path / "source.mp4"
    output = tmp_path / "--highlight.mp4"
    source.write_bytes(b"source")
    calls = []

    def fake_popen(cmd, **_kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            Path(cmd[-1]).write_bytes(b"segment")
            return SimpleNamespace(returncode=0, wait=lambda: None)
        Path(cmd[-1]).write_bytes(b"partial concat")
        return SimpleNamespace(returncode=1, wait=lambda: None)

    worker = clip_worker.HighlightWorker(
        str(source), str(output), [(1, 4)],
    )
    done = _signal_values(worker.done)
    with mock.patch.object(clip_worker, "resolve_tool_command", return_value="ffmpeg"), \
            mock.patch.object(clip_worker.subprocess, "Popen", side_effect=fake_popen):
        worker.run()

    assert done == [(False, "")]
    assert not output.exists()
    assert "-nostdin" in calls[0]
    assert "-safe" in calls[1]
    assert calls[1][calls[1].index("-safe") + 1] == "0"
    assert calls[1][-1] == str(output)


def test_whisper_cpp_command_and_intermediate_cleanup(tmp_path, qt_application):
    media = tmp_path / "--input.wav"
    media.write_bytes(b"audio")
    worker = transcribe_worker.TranscribeWorker(str(media), language="en")
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        srt_path = Path(cmd[cmd.index("-of") + 1] + ".srt")
        srt_path.write_text(
            "1\n00:00:01,000 --> 00:00:02,500\nhello\n",
            encoding="utf-8",
        )
        return _CompletedProcess(stdout="", stderr="")

    with mock.patch.object(transcribe_worker.subprocess, "run", side_effect=fake_run):
        segments = worker._run_whisper_cpp("whisper-cli")

    assert segments == [{"start": 1.0, "end": 2.5, "text": "hello"}]
    assert calls[0][0] == "whisper-cli"
    assert calls[0][calls[0].index("-f") + 1] == str(media)
    assert "-osrt" in calls[0]
    assert not Path(calls[0][calls[0].index("-of") + 1] + ".srt").exists()


def test_whisper_backend_order_keeps_existing_runtime_ahead_of_ffmpeg():
    ready = {"supported": True}
    with mock.patch.object(
        transcribe_worker, "_whisperx_available", return_value=False,
    ), mock.patch.dict(
        sys.modules, {"faster_whisper": None},
    ), mock.patch.object(
        transcribe_worker.shutil, "which",
        side_effect=lambda name: "whisper-cli" if name == "whisper-cli" else None,
    ), mock.patch.object(
        transcribe_worker, "_ffmpeg_whisper_capability", return_value=ready,
    ):
        assert transcribe_worker.is_available({}) == "whisper-cli"

    with mock.patch.object(
        transcribe_worker, "_whisperx_available", return_value=False,
    ), mock.patch.dict(
        sys.modules, {"faster_whisper": None},
    ), mock.patch.object(
        transcribe_worker.shutil, "which", return_value=None,
    ), mock.patch.object(
        transcribe_worker, "_ffmpeg_whisper_capability", return_value=ready,
    ):
        assert transcribe_worker.is_available({}) == "ffmpeg-whisper"


def test_whisper_cpp_failure_removes_partial_intermediate(tmp_path, qt_application):
    media = tmp_path / "input.wav"
    media.write_bytes(b"audio")

    def fake_run(cmd, **_kwargs):
        srt_path = Path(cmd[cmd.index("-of") + 1] + ".srt")
        srt_path.write_bytes(b"partial")
        return _CompletedProcess(returncode=1, stderr="decoder failed")

    worker = transcribe_worker.TranscribeWorker(str(media))
    with mock.patch.object(transcribe_worker.subprocess, "run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="decoder failed"):
            worker._run_whisper_cpp("whisper-cli")

    assert not Path(str(media.with_suffix("")) + ".wspcpp.srt").exists()


def test_ffmpeg_whisper_command_parses_srt_and_cleans_intermediate(
    tmp_path, qt_application,
):
    media = tmp_path / "input.wav"
    model = tmp_path / "ggml-base.bin"
    media.write_bytes(b"audio")
    model.write_bytes(b"model")
    worker = transcribe_worker.TranscribeWorker(
        str(media),
        config={"whisper_model_path": str(model)},
    )
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        graph = cmd[cmd.index("-af") + 1]
        encoded = graph.split("destination='", 1)[1].split("'", 1)[0]
        destination = encoded.replace("\\:", ":").replace("\\\\", "\\")
        Path(destination).write_text(
            "1\n00:00:01,000 --> 00:00:02,500\nhello\n",
            encoding="utf-8",
        )
        return _CompletedProcess(stdout="", stderr="")

    capability = {
        "supported": True,
        "command": ["ffmpeg"],
        "model_path": str(model),
        "ffmpeg_path": "ffmpeg",
    }
    with mock.patch.object(
        transcribe_worker, "_ffmpeg_whisper_capability", return_value=capability,
    ), mock.patch.object(transcribe_worker.subprocess, "run", side_effect=fake_run):
        segments = worker._run_ffmpeg_whisper()

    assert segments == [{"start": 1.0, "end": 2.5, "text": "hello"}]
    assert calls[0][0] == "ffmpeg"
    assert "whisper=model=" in calls[0][calls[0].index("-af") + 1]
    assert "format=srt" in calls[0][calls[0].index("-af") + 1]
    assert "-nostdin" in calls[0]
    assert not list(tmp_path.glob(".streamkeep_ffmpeg_whisper_*.srt"))


def test_ffmpeg_whisper_backend_uses_shared_sidecar_outputs(tmp_path, qt_application):
    media = tmp_path / "recording.wav"
    media.write_bytes(b"audio")
    worker = transcribe_worker.TranscribeWorker(str(media))
    done = _signal_values(worker.done)
    segments = [{"start": 0.0, "end": 1.0, "text": "hello"}]

    with mock.patch.object(
        transcribe_worker, "is_available", return_value="ffmpeg-whisper",
    ), mock.patch.object(
        worker, "_run_ffmpeg_whisper", return_value=segments,
    ):
        worker.run()

    assert done == [(True, str(media.with_suffix("")))]
    for suffix in (".srt", ".vtt", ".transcript.json", ".chapters.auto.txt"):
        assert (tmp_path / f"recording{suffix}").is_file()


def test_transcribe_output_failure_removes_new_sidecars(tmp_path, qt_application):
    media = tmp_path / "recording.wav"
    media.write_bytes(b"audio")
    worker = transcribe_worker.TranscribeWorker(str(media))
    done = _signal_values(worker.done)
    segments = [{"start": 0.0, "end": 1.0, "text": "hello"}]

    def write_first_then_fail(path, _text):
        Path(path).write_text("partial", encoding="utf-8")
        raise OSError("disk full")

    with mock.patch.object(transcribe_worker, "is_available", return_value="whisper-cli"), \
            mock.patch.object(worker, "_run_whisper_cpp", return_value=segments), \
            mock.patch.object(
                transcribe_worker, "_write_text_atomically",
                side_effect=write_first_then_fail,
            ):
        worker.run()

    assert done == [(False, "Could not write transcript outputs: disk full")]
    for suffix in (".srt", ".vtt", ".transcript.json", ".chapters.auto.txt"):
        assert not (tmp_path / f"recording{suffix}").exists()
    assert not list(tmp_path.glob(".streamkeep_transcript_*.tmp"))


class _Pipe:
    def __init__(self):
        self.closed = False

    def write(self, value):
        return len(value)

    def close(self):
        self.closed = True


class _ChatProcess:
    def __init__(self, output, returncode):
        self.stdin = _Pipe()
        self.stderr = SimpleNamespace(read=lambda: b"encoder failed")
        self.returncode = returncode
        if returncode != 0:
            Path(output).write_bytes(b"partial chat video")

    def wait(self):
        return self.returncode


def test_chat_render_builds_nostdin_argv_and_removes_failed_output(
    tmp_path, qt_application,
):
    chat = tmp_path / "chat.jsonl"
    output = tmp_path / "--chat.mp4"
    chat.write_text(json.dumps({"ts": 10, "nick": "alice", "message": "hello"}) + "\n")
    calls = []

    def fake_popen(cmd, **_kwargs):
        calls.append(list(cmd))
        return _ChatProcess(output, returncode=1)

    with mock.patch("streamkeep.capabilities.require_capability"), \
            mock.patch("streamkeep.capabilities.resolve_tool_command", return_value="ffmpeg"), \
            mock.patch.object(chat_render_worker.subprocess, "Popen", side_effect=fake_popen):
        worker = chat_render_worker.ChatRenderWorker(
            str(chat), str(output), width=200, height=200, fps=1,
            preview_secs=0.1, enable_emotes=False,
        )
        done = _signal_values(worker.done)
        worker.run()

    assert done and done[-1][0] is False
    assert not output.exists()
    assert "-nostdin" in calls[0]
    assert calls[0][calls[0].index("-i") + 1] == "pipe:0"
    assert calls[0][-1] == str(output)


def test_thumb_command_and_failure_cleanup(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "--thumb.jpg"
    source.write_bytes(b"source")
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"partial thumbnail")
        return _CompletedProcess(returncode=1)

    with mock.patch.object(thumb_worker, "resolve_tool_command", return_value="ffmpeg"), \
            mock.patch.object(thumb_worker.subprocess, "run", side_effect=fake_run):
        assert not thumb_worker._run_ffmpeg_thumb(str(source), 4, str(output))

    assert not output.exists()
    assert "-nostdin" in calls[0]
    assert calls[0][calls[0].index("-i") + 1] == str(source)
    assert calls[0][-1] == str(output)


def test_probe_duration_is_an_argv_call_with_no_shell(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    calls = []

    def fake_check_output(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return "12.5\n"

    with mock.patch.object(thumb_worker, "resolve_tool_command", return_value="ffprobe"), \
            mock.patch.object(thumb_worker.subprocess, "check_output", side_effect=fake_check_output):
        assert thumb_worker.probe_duration(str(source)) == 12.5

    assert calls[0][0][0] == "ffprobe"
    assert calls[0][0][-1] == str(source)
    assert "shell" not in calls[0][1]


def test_scene_worker_checks_encoder_status_and_cleans_partial_thumb(
    tmp_path, monkeypatch, qt_application,
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    fake_scenedetect = ModuleType("scenedetect")
    fake_scenedetect.ContentDetector = lambda threshold: threshold
    fake_scenedetect.detect = lambda *_args: [
        (SimpleNamespace(get_seconds=lambda: 4.5), None),
    ]
    monkeypatch.setitem(sys.modules, "scenedetect", fake_scenedetect)
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"partial scene thumb")
        return _CompletedProcess(returncode=1)

    worker = scene_worker.SceneWorker(str(source))
    ready = _signal_values(worker.scenes_ready)
    with mock.patch.object(scene_worker, "resolve_tool_command", return_value="ffmpeg"), \
            mock.patch.object(scene_worker.subprocess, "run", side_effect=fake_run):
        worker.run()

    assert ready == [([],)]
    assert not (tmp_path / ".streamkeep_scenes" / "scene_000.jpg").exists()
    assert "-nostdin" in calls[0]
    assert calls[0][calls[0].index("-i") + 1] == str(source)


def test_normalization_two_pass_argv_and_failed_output_cleanup(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "normalised.mp4"
    source.write_bytes(b"source")
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        if len(calls) == 2:
            Path(cmd[-1]).write_bytes(b"partial normalised output")
            return _CompletedProcess(returncode=1, stderr=b"bad loudnorm")
        return _CompletedProcess(
            returncode=0,
            stderr=(
                b'noise {"input_i": -20, "input_tp": -2, "input_lra": 5, '
                b'"input_thresh": -30, "target_offset": 1} tail'
            ),
        )

    with mock.patch.object(normalization, "resolve_tool_command", return_value="ffmpeg"), \
            mock.patch.object(normalization.subprocess, "run", side_effect=fake_run):
        assert not normalization.normalize_two_pass(str(source), str(output))

    assert not output.exists()
    assert "-nostdin" in calls[0]
    assert calls[0][calls[0].index("-i") + 1] == str(source)
    assert calls[1][-1] == str(output)


def test_codec_detection_is_cached_and_uses_structured_argv():
    stdout = " V..... libx264       H.264\n V..... h264_nvenc    NVIDIA\n A..... aac            AAC\n"
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return _CompletedProcess(stdout=stdout)

    old_cache = codecs._FFMPEG_ENCODERS_CACHE
    try:
        codecs._FFMPEG_ENCODERS_CACHE = None
        with mock.patch.object(codecs, "resolve_tool_command", return_value="ffmpeg"), \
                mock.patch.object(codecs.subprocess, "run", side_effect=fake_run):
            assert codecs.detect_ffmpeg_encoders() == {"libx264", "h264_nvenc"}
            assert codecs.detect_ffmpeg_encoders() == {"libx264", "h264_nvenc"}
    finally:
        codecs._FFMPEG_ENCODERS_CACHE = old_cache

    assert calls == [["ffmpeg", "-hide_banner", "-encoders"]]


def test_convert_worker_routes_video_and_reports_output(tmp_path, qt_application):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "source.converted.mp4"
    original = processor.PostProcessor._run_video_convert
    original_values = {
        name: getattr(processor.PostProcessor, name)
        for name in (
            "convert_video_format", "convert_video_codec", "convert_video_scale",
            "convert_video_fps", "convert_delete_source",
        )
    }

    def fake_convert(_cls, path, _log):
        Path(os.path.splitext(path)[0] + ".converted.mp4").write_bytes(b"converted")

    try:
        processor.PostProcessor._run_video_convert = classmethod(fake_convert)
        processor.PostProcessor.convert_video_format = "mp4"
        processor.PostProcessor.convert_video_codec = "h264"
        processor.PostProcessor.convert_video_scale = ""
        processor.PostProcessor.convert_video_fps = "original"
        processor.PostProcessor.convert_delete_source = False
        worker = convert_worker.ConvertWorker([str(source)], True, False)
        completed = _signal_values(worker.all_done)
        worker.run()
    finally:
        processor.PostProcessor._run_video_convert = original
        for name, value in original_values.items():
            setattr(processor.PostProcessor, name, value)

    assert completed == [(1, 0)]
    assert output.read_bytes() == b"converted"


def test_processor_convert_removes_partial_output_on_ffmpeg_failure(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "source.converted.mp4"
    source.write_bytes(b"source")
    original_values = {
        name: getattr(processor.PostProcessor, name)
        for name in (
            "convert_video_format", "convert_video_codec", "convert_video_scale",
            "convert_video_fps", "convert_delete_source",
        )
    }
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"partial converted output")
        return _CompletedProcess(returncode=1, stderr="failed")

    try:
        processor.PostProcessor.convert_video_format = "mp4"
        processor.PostProcessor.convert_video_codec = "h264"
        processor.PostProcessor.convert_video_scale = ""
        processor.PostProcessor.convert_video_fps = "original"
        processor.PostProcessor.convert_delete_source = False
        with mock.patch.object(processor, "resolve_tool_command", return_value="ffmpeg"), \
                mock.patch.object(processor.subprocess, "run", side_effect=fake_run):
            processor.PostProcessor._run_video_convert(str(source), lambda _msg: None)
    finally:
        for name, value in original_values.items():
            setattr(processor.PostProcessor, name, value)

    assert not output.exists()
    assert "-nostdin" in calls[0]
    assert calls[0][calls[0].index("-i") + 1] == str(source)
    assert calls[0][-1] == str(output)


def test_auto_editor_export_uses_explicit_args_and_parses_timeline(tmp_path):
    source = "--source.mp4"
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        timeline = Path(cmd[cmd.index("--output") + 1])
        timeline.write_text(
            json.dumps({
                "chunks": [
                    {"start": 0, "end": 4, "speed": 1},
                    {"start": 4, "end": 8, "speed": 99999},
                ],
            }),
            encoding="utf-8",
        )
        return _CompletedProcess(stdout="", stderr="")

    with mock.patch.object(auto_editor.subprocess, "run", side_effect=fake_run):
        segments = auto_editor.export_timeline(source)

    assert segments == [(0.0, 4.0)]
    assert calls[0][1] == source
    assert calls[0][calls[0].index("--edit") + 1] == "audio:4%"
    assert "--no-open" in calls[0]
    assert "shell" not in calls[0]


def test_auto_editor_mux_removes_partial_output_on_concat_failure(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "--without-silence.mp4"
    source.write_bytes(b"source")
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            Path(cmd[-1]).write_bytes(b"segment")
            return _CompletedProcess(returncode=0)
        Path(cmd[-1]).write_bytes(b"partial mux")
        return _CompletedProcess(returncode=1, stderr=b"concat failed")

    with mock.patch.object(auto_editor, "export_timeline", return_value=[(1, 3)]), \
            mock.patch.object(auto_editor, "resolve_tool_command", return_value="ffmpeg"), \
            mock.patch.object(auto_editor.subprocess, "run", side_effect=fake_run):
        assert not auto_editor.remove_silence(str(source), str(output))

    assert not output.exists()
    assert "-nostdin" in calls[0]
    assert "-safe" in calls[1]
    assert calls[1][-1] == str(output)


def test_summary_writes_atomically_and_requires_cloud_consent(tmp_path):
    transcript = "A useful transcript line. " * 8
    with mock.patch.object(summarize, "_query_llm", return_value="## Overview\nDone"):
        result = summarize.summarize_recording(
            str(tmp_path), transcript_text=transcript,
        )
    assert result == "## Overview\nDone"
    assert (tmp_path / ".summary.md").read_text(encoding="utf-8") == result
    assert not list(tmp_path.glob(".streamkeep_summary_*.tmp"))

    with pytest.raises(summarize.SummaryConsentRequired):
        summarize.summarize_recording(
            str(tmp_path), provider="openai", transcript_text=transcript,
        )


def test_summary_atomic_write_failure_does_not_leave_temporary_file(tmp_path):
    transcript = "A useful transcript line. " * 8
    with mock.patch.object(summarize, "_query_llm", return_value="summary"), \
            mock.patch.object(
                summarize.os, "replace", side_effect=OSError("disk full"),
            ):
        result = summarize.summarize_recording(
            str(tmp_path), transcript_text=transcript,
        )

    assert result == "summary"
    assert not (tmp_path / ".summary.md").exists()
    assert not list(tmp_path.glob(".streamkeep_summary_*.tmp"))
