"""V161: a live capture that is killed must still leave a playable file.

An MP4's index lives in a `moov` atom the muxer writes when the file is closed,
so a capture killed by a crash or a power loss produced a file with every
captured byte on disk and none of it readable. These tests drive real ffmpeg
because that is the only thing that can answer the acceptance question — a
string assertion on the argv would pass just as happily against flags that do
not work.
"""

import subprocess
import time

import pytest

from streamkeep.live_capture import (
    build_finalize_remux_command,
    needs_streaming_flags,
    streaming_output_args,
)


# ── The argv contract ───────────────────────────────────────────────

@pytest.mark.parametrize("container", ["mp4", "MP4", ".mp4", "mov", ".m4v"])
def test_index_at_close_containers_get_streaming_flags(container):
    args = streaming_output_args(container)
    assert needs_streaming_flags(container)
    assert "+frag_keyframe" in args[args.index("-movflags") + 1]
    assert "empty_moov" in args[args.index("-movflags") + 1]
    # Without this the muxer can hold the tail in its IO buffer, which decides
    # how much of the recording actually survives the kill.
    assert args[args.index("-flush_packets") + 1] == "1"


@pytest.mark.parametrize("container", ["mkv", "webm", ".ts", "", "original"])
def test_already_recoverable_containers_are_left_alone(container):
    """Matroska and MPEG-TS write their structure as they go."""
    assert not needs_streaming_flags(container)
    assert streaming_output_args(container) == []


def test_a_finalize_remux_never_writes_over_its_own_source():
    """The fragmented capture must stay intact until the remux has succeeded."""
    with pytest.raises(ValueError):
        build_finalize_remux_command("/tmp/a.mp4", "/tmp/a.mp4")
    with pytest.raises(ValueError):
        build_finalize_remux_command("", "/tmp/b.mp4")

    cmd = build_finalize_remux_command("a.mp4", "b.mp4", ffmpeg="FF")
    assert cmd[0] == "FF"
    assert cmd[cmd.index("-i") + 1] == "a.mp4"
    assert cmd[-1] == "b.mp4"
    assert cmd[cmd.index("-c") + 1] == "copy"  # never re-encode


# ── The property that actually matters, against real ffmpeg ─────────

def _tool(name):
    from streamkeep.capabilities import resolve_tool_command

    try:
        return resolve_tool_command(name)
    except Exception:  # the host simply does not have it
        return ""


_FFMPEG = _tool("ffmpeg")
_FFPROBE = _tool("ffprobe")

needs_ffmpeg = pytest.mark.skipif(
    not (_FFMPEG and _FFPROBE), reason="ffmpeg and ffprobe are required",
)


def _capture_then_kill(out, extra_args, seconds=8.0):
    """Run an unbounded realtime capture and kill it, power-loss style."""
    cmd = [
        _FFMPEG, "-hide_banner", "-loglevel", "error",
        "-re", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "15", "-b:v", "2M",
        *extra_args, "-y", str(out),
    ]
    process = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(seconds)
        process.kill()          # no trailer, no moov flush
    finally:
        process.wait(timeout=30)


def _probe_duration(path):
    """Seconds a player can actually read back, or None if it cannot."""
    result = subprocess.run(
        [_FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return float(result.stdout.strip().splitlines()[0])
    except ValueError:
        return None


@needs_ffmpeg
@pytest.mark.slow
def test_a_killed_mp4_capture_is_unreadable_without_the_flags(tmp_path):
    """The premise. Without this the fix below proves nothing."""
    out = tmp_path / "plain.mp4"
    _capture_then_kill(out, [])

    assert out.stat().st_size > 100_000, "the capture must have written real data"
    assert _probe_duration(out) is None, (
        "a plain MP4 killed mid-capture is expected to be unreadable; if this "
        "starts passing, ffmpeg changed its default and the flags may be moot"
    )


@needs_ffmpeg
@pytest.mark.slow
def test_a_killed_capture_with_the_flags_plays_back_what_it_recorded(tmp_path):
    out = tmp_path / "frag.mp4"
    _capture_then_kill(out, streaming_output_args("mp4"))

    duration = _probe_duration(out)
    assert duration is not None, "a killed capture must still be playable"
    # It cannot have recovered more than it ran, and recovering only a token
    # fraction would mean the tail was still lost in a buffer.
    assert 3.0 < duration < 9.0, f"recovered {duration}s of an ~8s capture"


@needs_ffmpeg
@pytest.mark.slow
def test_the_finalize_remux_turns_a_survived_capture_into_a_plain_file(tmp_path):
    """The final mux is resumable: its input stays playable throughout."""
    source = tmp_path / "frag.mp4"
    _capture_then_kill(source, streaming_output_args("mp4"))
    recovered = _probe_duration(source)
    assert recovered is not None

    target = tmp_path / "final.mp4"
    result = subprocess.run(
        build_finalize_remux_command(source, target, ffmpeg=_FFMPEG),
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr[-400:]
    assert source.exists(), "the source must survive its own remux"
    remuxed = _probe_duration(target)
    assert remuxed is not None
    assert abs(remuxed - recovered) < 1.0, (
        f"remux changed the duration: {recovered} -> {remuxed}"
    )


# ── The worker actually uses them ───────────────────────────────────

def _capture_worker(tmp_path, container="mp4", *, duration):
    """A native capture worker. duration <= 0 is an unbounded live capture."""
    from streamkeep.workers.download import DownloadWorker

    worker = DownloadWorker(
        playlist_url="https://cdn.example.com/live.m3u8",
        segments=[(0, "capture", 0, duration)],
        output_dir=str(tmp_path),
        format_type="mp4" if container == "mp4" else "hls",
    )
    worker.ytdlp_container = container
    worker._ffmpeg_path = r"C:\Tools\ffmpeg.exe"
    return worker


def test_a_live_mp4_capture_command_carries_the_streaming_flags(tmp_path):
    worker = _capture_worker(tmp_path, "mp4", duration=0)

    cmd = worker._build_ffmpeg_download_cmd(
        str(tmp_path / "live.mp4"), 0, 0,
    )

    assert "-movflags" in cmd
    assert "+frag_keyframe" in cmd[cmd.index("-movflags") + 1]
    assert cmd[cmd.index("-flush_packets") + 1] == "1"


def test_a_fixed_duration_download_does_not_get_them(tmp_path):
    """A bounded download closes its own file; faststart is the better shape."""
    worker = _capture_worker(tmp_path, "mp4", duration=30)

    cmd = worker._build_ffmpeg_download_cmd(
        str(tmp_path / "vod.mp4"), 0, 30,
    )

    assert "-movflags" not in cmd
    assert "-flush_packets" not in cmd


def test_a_matroska_live_capture_is_left_unchanged(tmp_path):
    worker = _capture_worker(tmp_path, "mkv", duration=0)

    cmd = worker._build_ffmpeg_download_cmd(
        str(tmp_path / "live.mkv"), 0, 0,
    )

    assert "-movflags" not in cmd


# ── The finalize repack costs nothing when it fails ─────────────────

def test_finalize_is_a_no_op_when_the_capture_was_not_fragmented(tmp_path):
    worker = _capture_worker(tmp_path, "mkv", duration=0)
    out = tmp_path / "live.mkv"
    out.write_bytes(b"x" * 1024)

    assert worker._finalize_streaming_capture(str(out)) is True
    assert out.read_bytes() == b"x" * 1024


def test_a_failed_finalize_leaves_the_recording_untouched(tmp_path, monkeypatch):
    """The whole point of resumability: failing must cost nothing."""
    import subprocess as sp

    worker = _capture_worker(tmp_path, "mp4", duration=0)
    worker._used_streaming_output = True
    out = tmp_path / "live.mp4"
    original = b"fragmented-bytes" * 64
    out.write_bytes(original)
    logged = []
    worker.log.connect(logged.append)

    def _fail(*args, **kwargs):
        return sp.CompletedProcess(args, 1, "", "ffmpeg exploded")

    monkeypatch.setattr(sp, "run", _fail)

    assert worker._finalize_streaming_capture(str(out)) is True
    assert out.read_bytes() == original, "the capture must survive a failed mux"
    assert not list(tmp_path.glob("*.streamkeep-final*")), "temp file left behind"
    assert any("playable" in message for message in logged), logged


@needs_ffmpeg
@pytest.mark.slow
def test_finalize_replaces_a_survived_capture_in_place(tmp_path):
    worker = _capture_worker(tmp_path, "mp4", duration=0)
    worker._used_streaming_output = True
    worker._ffmpeg_path = _FFMPEG
    out = tmp_path / "live.mp4"
    _capture_then_kill(out, streaming_output_args("mp4"))
    before = _probe_duration(out)
    assert before is not None

    assert worker._finalize_streaming_capture(str(out)) is True

    after = _probe_duration(out)
    assert after is not None, "the finalized file must still be playable"
    assert abs(after - before) < 1.0
    assert not list(tmp_path.glob("*.streamkeep-final*"))
