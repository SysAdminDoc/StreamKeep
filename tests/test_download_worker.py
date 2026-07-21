"""Regression tests for the yt-dlp direct download path.

Covers the YouTube opus+mp4 merge bug: yt-dlp pairs an mp4 video track with
a webm/opus audio track (opus is often the highest-bitrate audio), then
auto-switches the merged container to .mkv when the requested extension is
.mp4 — writing "<label>.mkv" instead of "<label>.mp4". The worker then failed
its file-exists check and reported "yt-dlp download failed" despite yt-dlp
exiting 0.
"""

import json
import os
import subprocess
from unittest import mock

import pytest

from streamkeep.capabilities import CapabilityUnavailableError
from streamkeep.models import MediaTrackInfo, ResumeState
from streamkeep.workers.download import DownloadWorker


def _make_worker(tmp_path):
    worker = DownloadWorker(
        playlist_url="",
        segments=[(0, "video", 0, 10)],
        output_dir=str(tmp_path),
        format_type="ytdlp_direct",
    )
    worker.ytdlp_source = "https://www.youtube.com/watch?v=oshdvLLtl3U"
    worker.ytdlp_format = "137+251"
    return worker


def test_ytdlp_cmd_forces_mp4_merge(tmp_path, monkeypatch):
    """The download command must pin the merge container to mp4."""
    worker = _make_worker(tmp_path)
    # Non-YouTube source keeps the test hermetic (skips the JS-runtime probe);
    # the merge flag is added unconditionally regardless of host.
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    monkeypatch.setattr(
        "streamkeep.extractors.ytdlp.ytdlp_command", lambda: ["yt-dlp"]
    )
    outfile = os.path.join(str(tmp_path), "video.mp4")
    captured = {}

    class _FakeStdout:
        def __iter__(self):
            return iter(())

        def close(self):
            pass

    class _FakeProc:
        returncode = 0
        stdout = _FakeStdout()

        def wait(self):
            # yt-dlp "produces" the file the worker asked for.
            with open(outfile, "wb") as fh:
                fh.write(b"x" * 1024)

    def _fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    ok = worker._download_with_ytdlp(0, "video", outfile)
    assert ok is True

    cmd = captured["cmd"]
    assert "--merge-output-format" in cmd
    assert cmd[cmd.index("--merge-output-format") + 1] == "mp4"
    assert cmd[cmd.index("--remux-video") + 1] == "mp4"


def test_ytdlp_cmd_passes_raw_spec_and_sort_verbatim(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    raw_format = " bv*[height<=720]+ba / b "
    raw_sort = "vcodec:av01,res:720,+size"
    worker.ytdlp_format = raw_format
    worker.ytdlp_format_sort = raw_sort
    worker.ytdlp_container = "webm"

    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )

    assert cmd[cmd.index("-f") + 1] == raw_format
    assert cmd[cmd.index("-S") + 1] == raw_sort
    assert cmd[cmd.index("--merge-output-format") + 1] == "webm"
    assert cmd[cmd.index("--remux-video") + 1] == "webm"


def test_named_template_and_credentials_appear_in_standalone_export(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker.cookies_browser = "firefox"
    worker.ytdlp_template_name = "Referrer"
    worker.ytdlp_template_args = (
        "--add-header", "Referer: https://example.com/library",
    )
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"

    runtime_argv = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )
    argv = worker.build_export_argv()
    command = worker.export_command(windows=True)

    assert argv[0] == "yt-dlp"
    assert argv[-1] == "https://example.com/video"
    assert argv[argv.index("--cookies-from-browser") + 1] == "firefox"
    assert argv[argv.index("--add-header") + 1] == (
        "Referer: https://example.com/library"
    )
    assert runtime_argv[runtime_argv.index("--add-header") + 1] == (
        "Referer: https://example.com/library"
    )
    assert '"Referer: https://example.com/library"' in command


def test_native_download_exports_equivalent_ffmpeg_plan(tmp_path):
    worker = DownloadWorker(
        "https://cdn.example.com/media.m3u8",
        [(0, "capture", 12, 30)],
        str(tmp_path),
        "hls",
    )
    argv = worker.build_export_argv()
    assert argv[0] == "ffmpeg"
    assert argv[argv.index("-ss") + 1] == "12"
    assert argv[argv.index("-i") + 1] == worker.playlist_url
    assert argv[argv.index("-t") + 1] == "30"


def test_hls_clear_key_exports_native_ytdlp_override(tmp_path):
    source = "https://cdn.example.com/media.m3u8"
    worker = DownloadWorker(
        source, [(0, "capture", 0, 30)], str(tmp_path), "hls"
    )
    worker.hls_key_override = "00112233445566778899aabbccddeeff"
    worker.hls_key_iv = "01"

    argv = worker.build_export_argv()

    assert argv[0] == "yt-dlp"
    assert "-f" not in argv
    assert argv[-1] == source
    assert argv[argv.index("--extractor-args") + 1] == (
        "generic:hls_key=00112233445566778899AABBCCDDEEFF,"
        "0x00000000000000000000000000000001"
    )


def test_hls_clear_key_uses_ytdlp_download_path(tmp_path):
    worker = DownloadWorker(
        "https://cdn.example.com/media.m3u8",
        [(0, "capture", 0, 30)], str(tmp_path), "hls",
    )
    worker.hls_key_override = "00112233445566778899aabbccddeeff"

    with mock.patch.object(
        worker, "_ensure_supported_ffmpeg", return_value=True,
    ), mock.patch.object(
        worker, "_ensure_supported_ytdlp", return_value=True,
    ), mock.patch.object(
        worker, "_download_with_ytdlp", return_value=True,
    ) as download:
        worker.run()

    download.assert_called_once()


def test_hls_clear_key_is_never_written_to_resume_sidecar(tmp_path):
    worker = DownloadWorker(
        "https://cdn.example.com/media.m3u8",
        [(0, "capture", 0, 30)], str(tmp_path), "hls",
    )
    worker.hls_key_override = "00112233445566778899aabbccddeeff"
    worker.attach_resume_state(ResumeState(output_dir=str(tmp_path)))

    assert worker._resume_state is None
    assert not (tmp_path / ".streamkeep_resume.json").exists()


def test_selected_tracks_build_explicit_multi_representation_mux(tmp_path):
    manifest = "https://cdn.example.com/main.mpd"
    worker = DownloadWorker(
        manifest, [(0, "capture", 12, 30)], str(tmp_path), "dash"
    )
    worker.selected_tracks = [
        MediaTrackInfo(kind="video", url=manifest, stream_index=1),
        MediaTrackInfo(kind="audio", language="en", url=manifest, stream_index=0),
        MediaTrackInfo(kind="audio", language="es", url=manifest, stream_index=1),
        MediaTrackInfo(kind="subtitle", language="en", url=manifest, stream_index=0),
    ]

    argv = worker.build_export_argv()

    assert argv.count("-i") == 1
    assert [argv[i + 1] for i, value in enumerate(argv) if value == "-map"] == [
        "0:v:1", "0:a:0", "0:a:1", "0:s:0",
    ]
    assert argv.count("-ss") == 1
    assert "language=en" in argv
    assert "language=es" in argv
    assert argv[argv.index("-c:s") + 1] == "mov_text"


def test_selected_hls_renditions_use_distinct_inputs_and_dedupe(tmp_path):
    video = "https://cdn.example.com/video.m3u8"
    audio = "https://cdn.example.com/audio.m3u8"
    worker = DownloadWorker(
        video, [(0, "capture", 0, 10)], str(tmp_path), "hls"
    )
    worker.selected_tracks = [
        MediaTrackInfo(kind="video", url=video),
        MediaTrackInfo(kind="audio", language="en", url=audio),
        MediaTrackInfo(kind="subtitle", language="en", url=video),
    ]

    argv = worker.build_export_argv()

    inputs = [argv[i + 1] for i, value in enumerate(argv) if value == "-i"]
    assert inputs == [video, audio]
    assert [argv[i + 1] for i, value in enumerate(argv) if value == "-map"] == [
        "0:v:0", "1:a:0", "0:s:0",
    ]


def test_selected_tracks_reject_multiple_video_representations(tmp_path):
    worker = DownloadWorker(
        "https://cdn.example.com/main.mpd",
        [(0, "capture", 0, 10)], str(tmp_path), "dash",
    )
    worker.selected_tracks = [
        MediaTrackInfo(kind="video", url=worker.playlist_url, stream_index=0),
        MediaTrackInfo(kind="video", url=worker.playlist_url, stream_index=1),
    ]

    with pytest.raises(ValueError, match="at most one video"):
        worker.build_export_argv()


def test_resume_sidecar_keeps_template_secret_in_secure_config(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_template_name = "Authenticated archive"
    worker.ytdlp_template_args = (
        "--add-header", "Authorization: Bearer never-write-this",
    )
    state = ResumeState(output_dir=str(tmp_path))

    worker.attach_resume_state(state)

    payload = json.loads(
        (tmp_path / ".streamkeep_resume.json").read_text(encoding="utf-8")
    )
    assert payload["ytdlp_template_name"] == "Authenticated archive"
    assert "never-write-this" not in json.dumps(payload)


def test_ytdlp_audio_cmd_extracts_requested_codec_and_quality(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.ytdlp_format = "bestaudio/best"
    worker.ytdlp_audio_format = "flac"
    worker.ytdlp_audio_quality = "0"

    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "audio.%(ext)s")
    )

    assert "-x" in cmd
    assert cmd[cmd.index("--audio-format") + 1] == "flac"
    assert cmd[cmd.index("--audio-quality") + 1] == "0"
    assert "--merge-output-format" not in cmd
    assert "--remux-video" not in cmd


def test_ytdlp_subtitle_cmd_supports_two_languages_conversion_and_embed(
    tmp_path, monkeypatch
):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.download_subs = True
    worker.subtitle_languages = "en,es"
    worker.subtitle_auto = True
    worker.subtitle_convert = "srt"
    worker.subtitle_embed = True
    monkeypatch.setattr(
        "streamkeep.extractors.ytdlp.ytdlp_command", lambda: ["yt-dlp"]
    )

    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )

    assert "--write-subs" in cmd
    assert "--write-auto-subs" in cmd
    assert cmd[cmd.index("--sub-langs") + 1] == "en,es"
    assert cmd[cmd.index("--convert-subs") + 1] == "srt"
    assert "--embed-subs" in cmd
    assert "--no-embed-subs" not in cmd


def test_ytdlp_subtitle_sidecars_can_exclude_automatic_captions(
    tmp_path, monkeypatch
):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.download_subs = True
    worker.subtitle_languages = "ja,fr"
    worker.subtitle_auto = False
    worker.subtitle_embed = False
    monkeypatch.setattr(
        "streamkeep.extractors.ytdlp.ytdlp_command", lambda: ["yt-dlp"]
    )

    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )

    assert "--no-write-auto-subs" in cmd
    assert "--no-embed-subs" in cmd
    assert "--write-auto-subs" not in cmd
    assert "--embed-subs" not in cmd


def test_ytdlp_audio_extract_forces_selected_subtitles_to_sidecars(
    tmp_path, monkeypatch
):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.ytdlp_audio_format = "mp3"
    worker.download_subs = True
    worker.subtitle_languages = "en"
    worker.subtitle_embed = True
    monkeypatch.setattr(
        "streamkeep.extractors.ytdlp.ytdlp_command", lambda: ["yt-dlp"]
    )

    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "audio.%(ext)s")
    )

    assert "--no-embed-subs" in cmd
    assert "--embed-subs" not in cmd


def test_ytdlp_sponsorblock_cmd_keeps_mark_remove_and_api_distinct(
    tmp_path, monkeypatch
):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.sponsorblock = True
    worker.sponsorblock_mark = "intro,chapter"
    worker.sponsorblock_remove = "sponsor,selfpromo"
    worker.sponsorblock_api = "https://sponsor.example/api"
    monkeypatch.setattr(
        "streamkeep.extractors.ytdlp.ytdlp_command", lambda: ["yt-dlp"]
    )

    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )

    assert cmd[cmd.index("--sponsorblock-mark") + 1] == "intro,chapter"
    assert cmd[cmd.index("--sponsorblock-remove") + 1] == "sponsor,selfpromo"
    assert cmd[cmd.index("--sponsorblock-api") + 1] == (
        "https://sponsor.example/api"
    )


def test_ytdlp_cmd_uses_incremental_download_archive(tmp_path, monkeypatch):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.download_archive = str(tmp_path / "source.txt")
    worker.break_on_existing = True
    monkeypatch.setattr(
        "streamkeep.extractors.ytdlp.ytdlp_command", lambda: ["yt-dlp"]
    )

    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )

    assert cmd[cmd.index("--download-archive") + 1] == worker.download_archive
    assert "--break-on-existing" in cmd


def test_ytdlp_cmd_includes_fragment_retry_live_and_embedding_matrix(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/live"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.ytdlp_concurrent_fragments = 4
    worker.ytdlp_retries = "8"
    worker.ytdlp_fragment_retries = "infinite"
    worker.ytdlp_retry_sleep = "fragment:exp=1:20"
    worker.ytdlp_unavailable_fragments = "abort"
    worker.ytdlp_throttled_rate = "250K"
    worker.ytdlp_live_from_start = True
    worker.ytdlp_wait_for_video = "30-120"
    worker.ytdlp_embed_chapters = True
    worker.ytdlp_embed_metadata = False
    worker.ytdlp_embed_thumbnail = True

    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )

    assert cmd[cmd.index("-N") + 1] == "4"
    assert cmd[cmd.index("--retries") + 1] == "8"
    assert cmd[cmd.index("--fragment-retries") + 1] == "infinite"
    assert cmd[cmd.index("--retry-sleep") + 1] == "fragment:exp=1:20"
    assert "--abort-on-unavailable-fragments" in cmd
    assert cmd[cmd.index("--throttled-rate") + 1] == "250K"
    assert "--live-from-start" in cmd
    assert cmd[cmd.index("--wait-for-video") + 1] == "30-120"
    assert "--embed-chapters" in cmd
    assert "--no-embed-metadata" in cmd
    assert "--embed-thumbnail" in cmd


def test_ytdlp_original_container_does_not_force_merge_or_remux(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.ytdlp_container = "original"

    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )

    assert "--merge-output-format" not in cmd
    assert "--remux-video" not in cmd


def test_ytdlp_dynamic_output_discovers_actual_media_extension(tmp_path):
    worker = _make_worker(tmp_path)
    template = os.path.join(str(tmp_path), "audio.%(ext)s")
    produced = tmp_path / "audio.opus"
    produced.write_bytes(b"audio payload")
    (tmp_path / "audio.info.json").write_text("{}", encoding="utf-8")

    assert worker._find_ytdlp_output(template) == str(produced)


def test_ytdlp_output_paths_cover_fixed_and_unknown_extensions(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_container = "mkv"
    template, expected = worker._ytdlp_output_paths("video")
    assert template.endswith("video.%(ext)s")
    assert expected.endswith("video.mkv")

    worker.ytdlp_audio_format = "best"
    _template, expected = worker._ytdlp_output_paths("audio")
    assert expected == ""

    worker.ytdlp_audio_format = "mp3"
    _template, expected = worker._ytdlp_output_paths("audio")
    assert expected.endswith("audio.mp3")


def test_ytdlp_known_container_does_not_skip_mismatched_sibling(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker.ytdlp_container = "mkv"
    (tmp_path / "video.webm").write_bytes(b"x" * 70000)

    with mock.patch.object(
        worker, "_ensure_supported_ffmpeg", return_value=True
    ), mock.patch.object(
        worker, "_ensure_supported_ytdlp", return_value=True
    ), mock.patch.object(
        worker, "_download_with_ytdlp", return_value=True
    ) as download:
        worker.run()

    download.assert_called_once()
    assert download.call_args.args[2].endswith("video.%(ext)s")
    assert download.call_args.args[3].endswith("video.mkv")


def test_failed_ytdlp_job_does_not_emit_all_done_without_resume_state(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    completions = []
    worker.all_done.connect(lambda: completions.append(True))

    with mock.patch.object(
        worker, "_ensure_supported_ffmpeg", return_value=True
    ), mock.patch.object(
        worker, "_ensure_supported_ytdlp", return_value=True
    ), mock.patch.object(
        worker, "_download_with_ytdlp", return_value=False
    ):
        worker.run()

    assert completions == []


def test_reconcile_output_adopts_sibling_container(tmp_path):
    """A merged .mkv sibling is renamed to the expected .mp4 path."""
    worker = _make_worker(tmp_path)
    outfile = os.path.join(str(tmp_path), "video.mp4")

    sibling = os.path.join(str(tmp_path), "video.mkv")
    with open(sibling, "wb") as fh:
        fh.write(b"y" * 2048)

    assert not os.path.exists(outfile)
    worker._reconcile_output(outfile)

    assert os.path.exists(outfile)
    assert not os.path.exists(sibling)
    assert os.path.getsize(outfile) == 2048


def test_reconcile_output_handles_double_extension(tmp_path):
    """yt-dlp's "<outfile>.mkv" naming (double extension) is also adopted."""
    worker = _make_worker(tmp_path)
    outfile = os.path.join(str(tmp_path), "video.mp4")

    sibling = outfile + ".mkv"  # video.mp4.mkv
    with open(sibling, "wb") as fh:
        fh.write(b"z" * 4096)

    worker._reconcile_output(outfile)

    assert os.path.exists(outfile)
    assert os.path.getsize(outfile) == 4096


def test_reconcile_output_prefers_largest_sibling(tmp_path):
    """When several siblings exist, the largest (the real merge) wins."""
    worker = _make_worker(tmp_path)
    outfile = os.path.join(str(tmp_path), "video.mp4")

    small = os.path.join(str(tmp_path), "video.part")
    big = os.path.join(str(tmp_path), "video.mkv")
    with open(small, "wb") as fh:
        fh.write(b"a" * 100)
    with open(big, "wb") as fh:
        fh.write(b"b" * 9000)

    worker._reconcile_output(outfile)

    assert os.path.exists(outfile)
    assert os.path.getsize(outfile) == 9000


def test_reconcile_output_noop_when_nothing_produced(tmp_path):
    """No siblings -> no file created, no crash."""
    worker = _make_worker(tmp_path)
    outfile = os.path.join(str(tmp_path), "video.mp4")

    worker._reconcile_output(outfile)
    assert not os.path.exists(outfile)


def test_parallel_direct_download_does_not_require_ffmpeg(tmp_path):
    worker = DownloadWorker(
        playlist_url="https://example.com/video.mp4",
        segments=[(0, "video", 0, 10)],
        output_dir=str(tmp_path),
        format_type="mp4",
    )
    worker.parallel_connections = 2

    def parallel(_url, outfile, **_kwargs):
        with open(outfile, "wb") as fh:
            fh.write(b"parallel payload")
        return True

    with mock.patch(
            "streamkeep.workers.download.parallel_http_download",
            side_effect=parallel,
    ), mock.patch(
            "streamkeep.workers.download.resolve_tool_command",
            side_effect=AssertionError("FFmpeg should not be resolved"),
    ):
        worker.run()

    assert (tmp_path / "video.mp4").read_bytes() == b"parallel payload"


def test_ffmpeg_path_blocks_before_process_start(tmp_path):
    worker = DownloadWorker(
        playlist_url="https://example.com/live.m3u8",
        segments=[(0, "video", 0, 10)],
        output_dir=str(tmp_path),
        format_type="hls",
    )
    record = {
        "name": "ffmpeg",
        "display_name": "FFmpeg",
        "path": r"C:\Tools\ffmpeg.exe",
        "version": "8.1.1",
        "minimum": "8.1.2",
        "available": True,
        "supported": False,
        "repair": "Install FFmpeg 8.1.2 or newer.",
    }
    logs = []
    errors = []
    worker.log.connect(logs.append)
    worker.error.connect(lambda index, message: errors.append((index, message)))

    with mock.patch(
            "streamkeep.workers.download.resolve_tool_command",
            side_effect=CapabilityUnavailableError(record),
    ), mock.patch("streamkeep.workers.download.subprocess.Popen") as popen:
        worker.run()

    popen.assert_not_called()
    assert errors and errors[0][0] == 0
    assert any("Install FFmpeg 8.1.2" in line for line in logs)


def _run_live_ffmpeg_worker(tmp_path, returncode, make_output):
    """Drive a live-capture (duration 0) ffmpeg segment to completion with a
    fake Popen that exits with *returncode* and optionally leaves an output
    file. Returns (all_done_count, errors)."""
    worker = DownloadWorker(
        playlist_url="https://example.com/live.m3u8",
        segments=[(0, "video", 0, 0)],  # duration 0 -> live capture
        output_dir=str(tmp_path),
        format_type="hls",
    )
    worker.max_retries = 0
    done = []
    errors = []
    worker.all_done.connect(lambda: done.append(True))
    worker.error.connect(lambda index, message: errors.append((index, message)))

    outfile = os.path.join(str(tmp_path), "video.mp4")

    class _FakeStderr:
        def __iter__(self):
            return iter(("frame= 10 time=00:00:05.00\n",))

        def close(self):
            pass

    class _FakeProc:
        def __init__(self, *a, **k):
            self.returncode = returncode
            self.stderr = _FakeStderr()
            if make_output:
                with open(outfile, "wb") as fh:
                    fh.write(b"x" * 4096)

        def wait(self):
            return self.returncode

    with mock.patch.object(
        worker, "_ensure_supported_ffmpeg", return_value=True
    ), mock.patch(
        "streamkeep.workers.download.resolve_tool_command", return_value="ffmpeg"
    ), mock.patch(
        "streamkeep.workers.download.subprocess.Popen", _FakeProc
    ):
        worker.run()

    return len(done), errors


def test_failed_live_capture_with_output_emits_all_done(tmp_path):
    # A live capture that ends on a non-zero ffmpeg exit but left a usable
    # recording must finalize (all_done), not hang callers with no signal.
    done, errors = _run_live_ffmpeg_worker(tmp_path, returncode=1, make_output=True)
    assert done == 1
    assert errors == []


def test_failed_live_capture_without_output_emits_error(tmp_path):
    # A live capture that produced nothing must surface a terminal error so
    # the CLI/headless callers don't block forever on app.exec().
    done, errors = _run_live_ffmpeg_worker(tmp_path, returncode=1, make_output=False)
    assert done == 0
    assert errors and errors[0][0] == 0


def test_hls_playlist_identity_persists_into_resume_sidecar(tmp_path):
    from streamkeep.hls import parse_hls_media_playlist
    from streamkeep import resume as resume_mod

    worker = DownloadWorker(
        "https://cdn.example.com/media.m3u8",
        [(0, "capture", 0, 6)], str(tmp_path), "hls",
    )
    playlist = parse_hls_media_playlist(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:947210\n"
        "#EXT-X-DISCONTINUITY-SEQUENCE:31\n"
        "#EXTINF:6.0,\nseg947210.ts\n#EXTINF:6.0,\nseg947211.ts\n"
    )
    playlist.validator = '"etag-xyz"'
    worker.set_hls_playlist_identity(playlist)
    worker.attach_resume_state(ResumeState(output_dir=str(tmp_path)))

    state = resume_mod.load_resume_state(str(tmp_path))
    assert state is not None
    assert state.playlist_validator == '"etag-xyz"'
    assert state.media_sequence == 947210
    assert state.discontinuity_sequence == 31
    assert state.playlist_segment_count == 2


def test_aria2c_routing_emits_downloader_argv_and_sanitizes_source(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://www.youtube.com/watch?v=oshdvLLtl3U"
    worker._ffmpeg_path = r"C:\Toolsfmpeg.exe"
    worker.ytdlp_external_downloader = "aria2c"
    worker.ytdlp_aria2c_connections = 8
    worker.ytdlp_aria2c_splits = 8
    worker.ytdlp_aria2c_min_split_size = "1M"

    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )

    assert cmd[cmd.index("--downloader") + 1] == "aria2c"
    assert cmd[cmd.index("--downloader-args") + 1] == (
        "aria2c:--max-connection-per-server=8 --split=8 --min-split-size=1M"
    )
    # The sanitized source is the final positional argument, unchanged.
    assert cmd[-1] == "https://www.youtube.com/watch?v=oshdvLLtl3U"


def test_native_download_has_no_downloader_argv(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Toolsfmpeg.exe"
    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )
    assert "--downloader" not in cmd
    assert "--downloader-args" not in cmd


def test_aria2c_routing_rejects_option_smuggling_source(tmp_path):
    worker = _make_worker(tmp_path)
    # A hostile source that would be read as an aria2c option must be refused
    # once aria2c routing is active (CVE-2026-50574).
    worker._ffmpeg_path = r"C:\Toolsfmpeg.exe"
    worker.ytdlp_source = "--max-connection-per-server=64"
    worker.ytdlp_external_downloader = "aria2c"
    with pytest.raises(ValueError):
        worker._build_ytdlp_download_cmd(
            os.path.join(str(tmp_path), "video.%(ext)s")
        )


def test_youtube_chat_replay_folds_live_chat_into_sub_langs(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://www.youtube.com/watch?v=oshdvLLtl3U"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.capture_youtube_chat = True
    worker.download_subs = True
    worker.subtitle_languages = "en.*,en"
    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )
    assert "--write-subs" in cmd
    assert cmd[cmd.index("--sub-langs") + 1] == "en.*,en,live_chat"


def test_youtube_chat_replay_standalone_when_subs_disabled(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://www.youtube.com/watch?v=oshdvLLtl3U"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.capture_youtube_chat = True
    worker.download_subs = False
    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )
    assert cmd[cmd.index("--sub-langs") + 1] == "live_chat"
    # Chat-only capture never tries to auto-download or embed captions.
    assert "--write-auto-subs" not in cmd
    assert "--no-embed-subs" in cmd


def test_youtube_chat_replay_ignored_for_non_youtube(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.capture_youtube_chat = True
    worker.download_subs = False
    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )
    assert "--sub-langs" not in cmd
    assert "live_chat" not in " ".join(cmd)


def test_youtube_chat_replay_off_by_default(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://www.youtube.com/watch?v=oshdvLLtl3U"
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    worker.download_subs = False
    cmd = worker._build_ytdlp_download_cmd(
        os.path.join(str(tmp_path), "video.%(ext)s")
    )
    assert "live_chat" not in " ".join(cmd)


def test_sabr_hint_emitted_for_gated_youtube_failure(tmp_path):
    """A YouTube SABR/PO-token failure surfaces remediation HINT lines (V26)."""
    worker = _make_worker(tmp_path)
    emitted = []
    worker.log.connect(emitted.append)
    worker._maybe_warn_sabr_pot(["ERROR: Requested format is not available"])
    assert any(m.startswith("[HINT]") for m in emitted)


def test_no_sabr_hint_for_non_youtube_or_transient(tmp_path):
    worker = _make_worker(tmp_path)
    worker.ytdlp_source = "https://example.com/video.mp4"
    emitted = []
    worker.log.connect(emitted.append)
    worker._maybe_warn_sabr_pot(["ERROR: Requested format is not available"])
    assert not any(m.startswith("[HINT]") for m in emitted)

    worker.ytdlp_source = "https://www.youtube.com/watch?v=x"
    emitted.clear()
    worker._maybe_warn_sabr_pot(["ERROR: HTTP Error 503: Service Unavailable"])
    assert not any(m.startswith("[HINT]") for m in emitted)
