"""Tests for the optional, guarded Streamlink live engine (V13)."""

from types import SimpleNamespace
from unittest import mock

import pytest

from streamkeep.integrations import streamlink as sl


class _FakeStream:
    def __init__(self, url, payload=b"media"):
        self.url = url
        self.payload = payload

    def open(self):
        return self

    def read(self, _size):
        payload, self.payload = self.payload, b""
        return payload

    def close(self):
        return None


def test_session_options_enable_low_latency_without_removed_ad_switch():
    options = sl.build_session_options(
        sl.StreamlinkOptions(
            quality="source", low_latency=True,
            start_offset="12", live_restart=True,
        ),
        proxy_url="http://127.0.0.1:9999",
        request_headers=[
            {"name": "Referer", "value": "https://player.example/"},
            {"name": "X-Not-Replayed", "value": "drop"},
        ],
    )
    assert options["hls-live-edge"] == 2
    assert options["hls-segment-stream-data"] is True
    assert options["hls-start-offset"] == 12.0
    assert options["hls-live-restart"] is True
    assert options["http-proxy"] == "http://127.0.0.1:9999"
    assert options["http-headers"] == {
        "Referer": "https://player.example/",
    }
    assert all("disable-ads" not in key for key in options)


def test_quality_and_nested_stream_urls_are_checked():
    streams = {
        "best": _FakeStream("https://cdn.example/live.m3u8"),
        "worst": _FakeStream("https://cdn.example/low.m3u8"),
    }
    with mock.patch.object(
        sl, "_validated_source", side_effect=lambda url: url,
    ):
        assert sl.select_stream(streams, "best") is streams["best"]
    with pytest.raises(sl.StreamlinkSecurityError, match="unsafe nested URL"):
        sl.validate_streams({"best": _FakeStream("file:///secret.txt")})


def test_capture_copy_honors_cancellation_and_closes_resources(tmp_path):
    stream = _FakeStream("https://cdn.example/live.m3u8", b"abc")
    proxy = mock.Mock()
    session = SimpleNamespace(http=mock.Mock())
    capture = sl.StreamlinkCapture(stream, session, proxy)
    result = capture.copy_to(
        tmp_path / "capture.part",
        cancel_check=lambda: False,
        progress_cb=lambda total: None,
    )
    assert result.bytes_written == 3
    assert result.stopped is False
    assert (tmp_path / "capture.part").read_bytes() == b"abc"
    capture.close()
    session.http.close.assert_called_once_with()
    proxy.stop.assert_called_once_with()


def test_engine_passes_guarded_options_to_a_resolved_plugin():
    class FakeSession:
        last = None

        def __init__(self, options):
            self.options = options
            self.http = mock.Mock()
            FakeSession.last = self

        def resolve_url(self, _url):
            return "twitch", FakePlugin, "https://twitch.tv/channel"

    class FakePlugin:
        def __init__(self, _session, _url, options=None):
            self.options = options or {}

        def streams(self):
            return {"best": _FakeStream("https://cdn.example/live.m3u8")}

    fake_module = SimpleNamespace(Streamlink=FakeSession)
    target = SimpleNamespace(url="https://twitch.tv/channel")
    with mock.patch.object(sl, "streamlink_version", return_value="8.4.0"), \
            mock.patch.object(sl.importlib, "import_module", return_value=fake_module), \
            mock.patch.object(sl, "validate_remote_url", return_value=target), \
            mock.patch.object(sl, "_validated_source", side_effect=lambda url: url):
        capture = sl.StreamlinkEngine().open(
            "https://twitch.tv/channel",
            platform="Twitch",
            options=sl.StreamlinkOptions(low_latency=True),
        )
    try:
        assert FakeSession.last.options["webbrowser"] is False
        assert FakeSession.last.options["hls-live-edge"] == 2
        assert capture.stream.url.endswith("live.m3u8")
        assert capture.stream is not None
    finally:
        capture.close()


def test_old_or_missing_streamlink_is_not_available():
    with mock.patch.object(sl, "streamlink_version", return_value="8.3.0"):
        assert sl.streamlink_available() is False
        with pytest.raises(sl.StreamlinkUnavailable):
            sl.StreamlinkEngine()._load()


def test_download_worker_routes_an_enabled_twitch_live_to_streamlink(tmp_path):
    from streamkeep.workers.download import DownloadWorker

    worker = DownloadWorker.__new__(DownloadWorker)
    worker.webpage_url = "https://twitch.tv/channel"
    worker.playlist_url = "https://cdn.example/live.m3u8"
    worker.source_platform = "Twitch"
    worker.request_headers = {}
    worker.streamlink_hls_start_offset = 0
    worker.streamlink_hls_live_restart = False
    worker._cancel = False
    worker._ffmpeg_path = "ffmpeg"
    worker._resume_state = None
    worker.output_dir = str(tmp_path)
    worker.log = mock.Mock()
    worker.progress = mock.Mock()
    worker.segment_done = mock.Mock()
    worker._mark_segment_done = mock.Mock()
    outfile = str(tmp_path / "live.mp4")
    raw = outfile + ".streamlink.part"

    fake_capture = mock.MagicMock()
    fake_capture.__enter__.return_value = fake_capture
    fake_capture.copy_to.return_value = sl.StreamlinkCaptureResult(raw, 123)
    fake_engine = mock.Mock()
    fake_engine.open.return_value = fake_capture

    def fake_remux(raw_path, output_path, *, ffmpeg):
        assert raw_path == raw
        assert ffmpeg == "ffmpeg"
        (tmp_path / "live.mp4").write_bytes(b"remuxed")
        return True

    with mock.patch.object(sl, "StreamlinkEngine", return_value=fake_engine), \
            mock.patch.object(sl, "remux_capture", side_effect=fake_remux):
        outcome = worker._download_with_streamlink(0, "live", outfile)
    assert outcome == "ok"
    fake_engine.open.assert_called_once_with(
        worker.webpage_url,
        platform="Twitch",
        options=mock.ANY,
        request_headers={},
    )
    worker._mark_segment_done.assert_called_once_with(0)
