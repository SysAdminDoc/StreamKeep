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
