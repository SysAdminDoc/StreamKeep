"""Regression tests for the yt-dlp direct download path.

Covers the YouTube opus+mp4 merge bug: yt-dlp pairs an mp4 video track with
a webm/opus audio track (opus is often the highest-bitrate audio), then
auto-switches the merged container to .mkv when the requested extension is
.mp4 — writing "<label>.mkv" instead of "<label>.mp4". The worker then failed
its file-exists check and reported "yt-dlp download failed" despite yt-dlp
exiting 0.
"""

import os
import subprocess
from unittest import mock

from streamkeep.capabilities import CapabilityUnavailableError
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
