import io
import json
from pathlib import Path

import pytest

from streamkeep import cli
from streamkeep.icy import parse_icy_metadata, split_icy_stream
from streamkeep.raw_capture import (
    RawCaptureError,
    RawCaptureSpec,
    build_ffmpeg_command,
    redact_endpoint,
    validate_raw_capture,
)


def _icy_block(title):
    payload = f"StreamTitle='{title}';StreamUrl='https://radio.example/{title}';".encode(
        "iso-8859-1"
    )
    length = (len(payload) + 15) // 16
    return bytes([length]) + payload.ljust(length * 16, b"\x00")


def test_icy_metadata_parser_and_track_split(tmp_path):
    stream = io.BytesIO(
        b"aaaa" + _icy_block("First") +
        b"bbbb" + _icy_block("Second") +
        b"cccc"
    )

    metadata = parse_icy_metadata(b"StreamTitle='First';StreamUrl='https://x';\x00")
    assert metadata.stream_title == "First"
    assert metadata.stream_url == "https://x"

    result = split_icy_stream(
        stream,
        tmp_path / "radio.mp3",
        metadata_interval=4,
    )
    assert [track.title for track in result.tracks] == [
        "Unknown", "First", "Second",
    ]
    assert [Path(track.path).read_bytes() for track in result.tracks] == [
        b"aaaa", b"bbbb", b"cccc",
    ]
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["schema"] == "streamkeep.icy-tracks"
    assert manifest["total_bytes"] == 12


def test_raw_capture_protocol_commands_are_explicit_and_bounded(tmp_path):
    cases = [
        (
            "rtsp",
            "rtsps://camera.example/live",
            {"-rtsp_transport": "tcp"},
        ),
        (
            "rtmp-listen",
            "rtmp://0.0.0.0:1935/live/stream",
            {"-listen": "1"},
        ),
        (
            "srt-caller",
            "srt://encoder.example:9000",
            {},
        ),
        (
            "srt-listener",
            "srt://0.0.0.0:9000",
            {},
        ),
        ("udp", "udp://@239.1.1.1:5000", {}),
        ("rtp", "rtp://@239.1.1.2:5001", {}),
        ("icy", "https://radio.example/stream", {"-reconnect": "1"}),
    ]
    for protocol, endpoint, expected in cases:
        passphrase = "correct horse battery staple" if protocol == "srt-caller" else ""
        spec = RawCaptureSpec(
            protocol, endpoint, str(tmp_path / f"{protocol}.mkv"),
            duration_secs=30, passphrase=passphrase,
        )
        validated = validate_raw_capture(spec)
        argv = build_ffmpeg_command(
            validated.spec,
            executable="ffmpeg",
            ffmpeg_version="8.1.2",
        )
        assert argv[0] == "ffmpeg"
        assert argv[argv.index("-t") + 1] == "30"
        assert "yt-dlp" not in argv
        assert "file" not in argv[argv.index("-protocol_whitelist") + 1].split(",")
        for flag, value in expected.items():
            assert flag in argv
            if value:
                assert argv[argv.index(flag) + 1] == value
        if protocol == "srt-caller":
            assert "mode=caller" in argv[argv.index("-i") + 1]
            assert "passphrase=" in argv[argv.index("-i") + 1]
            assert passphrase not in str(validated.spec.to_public_dict())


def test_raw_capture_validation_rejects_unsafe_or_ambiguous_inputs(tmp_path):
    with pytest.raises(RawCaptureError, match="multicast"):
        validate_raw_capture(RawCaptureSpec(
            "udp", "udp://127.0.0.1:5000", str(tmp_path / "out.ts"),
        ))
    with pytest.raises(RawCaptureError, match="listener"):
        validate_raw_capture(RawCaptureSpec(
            "srt-listener", "srt://encoder.example:9000", str(tmp_path / "out.ts"),
        ))
    with pytest.raises(RawCaptureError, match="FFmpeg 8"):
        build_ffmpeg_command(
            RawCaptureSpec(
                "rtsp", "rtsps://camera.example/live", str(tmp_path / "out.mkv"),
                allow_self_signed=True,
            ),
            ffmpeg_version="7.0.0",
        )


def test_srt_diagnostics_redact_passphrase_and_cli_parser_has_capture_job():
    secret = "correct horse battery staple"
    assert secret not in redact_endpoint(
        "srt://encoder.example:9000?passphrase=" + secret, secret
    )
    args = cli.build_parser().parse_args([
        "capture", "srt-listener", "srt://0.0.0.0:9000",
        "--output", "capture.ts", "--passphrase-stdin", "--duration", "15",
    ])
    assert args.command == "capture"
    assert args.protocol == "srt-listener"
    assert args.passphrase_stdin is True
    assert args.duration == 15


def test_raw_worker_exports_without_importing_ui_widgets():
    from streamkeep.workers.raw_capture import RawCaptureWorker

    spec = RawCaptureSpec(
        "rtmp-listen", "rtmp://0.0.0.0:1935/live", "capture.mkv",
    )
    worker = RawCaptureWorker(spec)
    assert worker.spec is spec
    worker.cancel()
    assert worker._stop_event.is_set()
