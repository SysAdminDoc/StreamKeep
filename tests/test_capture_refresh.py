"""Mid-capture manifest/token refresh coverage."""

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from streamkeep.capture_refresh import (
    detect_manifest_expiry,
    jittered_refresh_delay,
)
from streamkeep.models import ResumeState
from streamkeep.workers.download import DownloadWorker


def test_expired_status_detection_requires_transport_context():
    assert detect_manifest_expiry(["HTTP error 403 Forbidden"]).status == 403
    assert detect_manifest_expiry(["segment request returned 410 Gone"]).status == 410
    assert detect_manifest_expiry(["frame=403 time=00:00:04.00"]) is None


def test_refresh_backoff_is_bounded_and_jittered():
    assert jittered_refresh_delay(1, random_fn=lambda _low, _high: 0.75) == 0.375
    assert jittered_refresh_delay(8, random_fn=lambda _low, _high: 1.25) == 8.0


def test_http_403_refreshes_once_and_stitches_one_recording(tmp_path, monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler contract
            requests.append(self.path)
            if self.path == "/old.m3u8":
                self.send_response(403)
                self.end_headers()
                return
            if self.path == "/resolve":
                payload = b"fresh delivery"
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        old_url = f"http://127.0.0.1:{server.server_address[1]}/old.m3u8"
        resolve_url = f"http://127.0.0.1:{server.server_address[1]}/resolve"
        worker = DownloadWorker(
            old_url, [(0, "capture", 0, 10)], str(tmp_path), "hls",
        )
        worker.webpage_url = "https://example.com/watch"
        worker.max_retries = 0
        worker._ffmpeg_path = "ffmpeg"
        state = ResumeState(
            source_url=worker.webpage_url,
            playlist_url=old_url,
            output_dir=str(tmp_path),
            segments=[[0, "capture", 0, 10]],
        )
        worker.attach_resume_state(state)

        def resolve_delivery(_worker):
            with urllib.request.urlopen(resolve_url, timeout=5) as response:
                assert response.read() == b"fresh delivery"
            return (
                "https://cdn.example.com/stream.m3u8",
                "",
                [],
                "hls",
            )

        worker.set_manifest_refresh_resolver(resolve_delivery)
        done = []
        errors = []
        logs = []
        worker.all_done.connect(lambda: done.append(True))
        worker.error.connect(lambda index, message: errors.append((index, message)))
        worker.log.connect(logs.append)

        class FakeStderr:
            def __init__(self, lines=()):
                self.lines = tuple(lines)

            def __iter__(self):
                return iter(self.lines)

            def close(self):
                return

        calls = []

        class FakeProcess:
            def __init__(self, command, *args, **kwargs):
                del args, kwargs
                calls.append(command)
                self.command = command
                self.returncode = 0
                self.stderr = FakeStderr()
                output = Path(command[-1])
                if "-f" in command and command[command.index("-f") + 1] == "concat":
                    output.write_bytes(b"first-partsecond-part")
                    return
                if len(calls) == 1:
                    try:
                        urllib.request.urlopen(old_url, timeout=5)
                    except urllib.error.HTTPError as error:
                        assert error.code == 403
                    output.write_bytes(b"first-part")
                    self.returncode = 1
                    self.stderr = FakeStderr((
                        "frame= 10 time=00:00:05.00\n",
                        "[https] HTTP error 403 Forbidden\n",
                    ))
                else:
                    assert "https://cdn.example.com/stream.m3u8" in command
                    assert command[command.index("-ss") + 1] == "5.0"
                    output.write_bytes(b"second-part")

            def wait(self):
                return self.returncode

        monkeypatch.setattr("streamkeep.workers.download.time.sleep", lambda _delay: None)
        with mock.patch.object(worker, "_ensure_supported_ffmpeg", return_value=True), \
                mock.patch.object(worker, "_ensure_guarded_transport", return_value=True), \
                mock.patch("streamkeep.workers.download.validate_remote_url"), \
                mock.patch("streamkeep.workers.download.subprocess.Popen", FakeProcess):
            worker.run()

        output = tmp_path / "capture.mp4"
        report = tmp_path / "capture.mp4.streamkeep-refresh.json"
        assert done == [True]
        assert errors == []
        assert output.read_bytes() == b"first-partsecond-part"
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["events"][0]["status"] == 403
        assert state.playlist_url == "https://cdn.example.com/stream.m3u8"
        assert state.refresh_events[0]["reason"] == "expired manifest/token"
        assert state.completed == [0]
        assert requests == ["/resolve", "/old.m3u8"] or requests == [
            "/old.m3u8", "/resolve",
        ]
        assert any("Delivery URL refreshed" in line for line in logs)
        assert any("one continuous recording" in line for line in logs)
        assert len(calls) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_refresh_budget_gives_up_with_named_reason(tmp_path, monkeypatch):
    worker = DownloadWorker(
        "https://old.example/stream.m3u8",
        [(0, "capture", 0, 10)],
        str(tmp_path),
        "hls",
    )
    worker.webpage_url = "https://example.com/watch"
    worker.max_retries = 0
    worker.manifest_refresh_max = 2
    worker._ffmpeg_path = "ffmpeg"
    worker.set_manifest_refresh_resolver(lambda _worker: None)
    done = []
    errors = []
    logs = []
    worker.all_done.connect(lambda: done.append(True))
    worker.error.connect(lambda index, message: errors.append((index, message)))
    worker.log.connect(logs.append)

    class FakeStderr:
        def __iter__(self):
            return iter(("[https] HTTP error 410 Gone\n",))

        def close(self):
            return

    class FakeProcess:
        returncode = 1
        stderr = FakeStderr()

        def __init__(self, *args, **kwargs):
            del args, kwargs

        def wait(self):
            return self.returncode

    monkeypatch.setattr("streamkeep.workers.download.time.sleep", lambda _delay: None)
    with mock.patch.object(worker, "_ensure_supported_ffmpeg", return_value=True), \
            mock.patch.object(worker, "_ensure_guarded_transport", return_value=True), \
            mock.patch("streamkeep.workers.download.subprocess.Popen", FakeProcess):
        worker.run()

    assert done == []
    assert errors and "Manifest/token refresh exhausted" in errors[0][1]
    assert any("REFRESH GIVE UP" in line for line in logs)
    assert worker.manifest_refresh_attempts == 2
