"""Tests for the DRM-free MSE recorder contract (V14)."""

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from streamkeep import mse_capture as mse


def test_init_hook_tees_append_buffer_and_refuses_eme():
    script = mse.build_mse_init_script()
    assert "SourceBuffer.prototype.appendBuffer" in script
    assert "__streamkeep_mse_chunk" in script
    assert "encrypted" in script
    assert "requestMediaKeySystemAccess" in script
    assert "setMediaKeys" in script
    assert "refuses encrypted-media capture" in script


def test_writer_bounds_and_orders_chunks(tmp_path):
    writer = mse.MSECaptureWriter(tmp_path).open()
    for buffer_id, data in ((2, b"second"), (1, b"first")):
        writer.write_chunk({
            "bufferId": buffer_id,
            "data": base64.b64encode(data).decode("ascii"),
        })
    listing = Path(writer.write_concat_list())
    assert listing.read_text(encoding="utf-8").splitlines() == [
        f"file '{tmp_path / '00000000-buffer2.m4s'}'",
        f"file '{tmp_path / '00000001-buffer1.m4s'}'",
    ]
    with pytest.raises(mse.MSECaptureError, match="valid base64"):
        writer.write_chunk({"bufferId": 1, "data": "not base64"})


def test_writer_refuses_new_bytes_after_eme(tmp_path):
    writer = mse.MSECaptureWriter(tmp_path).open()
    writer.refuse_eme("encrypted event")
    assert writer.write_chunk({
        "bufferId": 1,
        "data": base64.b64encode(b"secret").decode("ascii"),
    }) == ""
    assert writer.chunks == 0


def test_concat_command_requires_chunks_and_uses_safe_ffmpeg_shape(tmp_path):
    with pytest.raises(ValueError, match="no chunks"):
        mse.build_mse_concat_command(tmp_path, tmp_path / "out.mp4")
    writer = mse.MSECaptureWriter(tmp_path).open()
    writer.write_chunk({
        "bufferId": 1,
        "data": base64.b64encode(b"fragment").decode("ascii"),
    })
    command = mse.build_mse_concat_command(
        tmp_path, tmp_path / "out.mp4", ffmpeg="ffmpeg-test",
    )
    assert command[:4] == ["ffmpeg-test", "-hide_banner", "-nostdin", "-f"]
    assert command[command.index("-f") + 1] == "concat"
    assert command[-1].endswith("out.mp4")


class _FakePage:
    def __init__(self, event="chunk"):
        self.event = event
        self.bindings = {}
        self.init_script = ""
        self.closed = False

    def expose_binding(self, name, callback):
        self.bindings[name] = callback

    def add_init_script(self, script):
        self.init_script = script

    def set_default_timeout(self, _value):
        pass

    def set_default_navigation_timeout(self, _value):
        pass

    def on(self, _event, _callback):
        pass

    def goto(self, *_args, **_kwargs):
        if self.event == "eme":
            self.bindings["__streamkeep_mse_event"](
                None, {"kind": "eme", "reason": "test EME"},
            )
        else:
            self.bindings["__streamkeep_mse_chunk"](
                None,
                {
                    "bufferId": 1,
                    "data": base64.b64encode(b"fragment").decode("ascii"),
                },
            )

    def evaluate(self, _script):
        return None

    def wait_for_timeout(self, _milliseconds):
        return None

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_page(self):
        return self.page

    def route(self, _pattern, _handler):
        pass

    def route_web_socket(self, _pattern, _handler):
        pass

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, page):
        self.context = _FakeContext(page)
        self.closed = False

    def new_context(self, **_kwargs):
        return self.context

    def close(self):
        self.closed = True


class _FakePlaywrightManager:
    def __init__(self):
        self.playwright = SimpleNamespace()

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return False


def _capture_patches(page):
    return mock.patch.multiple(
        mse,
        ensure_playwright_browser=mock.DEFAULT,
        _safe_headless_url=mock.DEFAULT,
        _cancelled=mock.DEFAULT,
        _launch_scrape_browser=mock.DEFAULT,
    )


def test_record_mse_page_hard_refuses_eme_before_remux(tmp_path):
    page = _FakePage(event="eme")
    browser = _FakeBrowser(page)
    manager = _FakePlaywrightManager()
    with _capture_patches(page) as patched, \
            mock.patch("playwright.sync_api.sync_playwright", return_value=manager):
        patched["ensure_playwright_browser"].return_value = True
        patched["_safe_headless_url"].side_effect = lambda url, **_kwargs: url
        patched["_cancelled"].return_value = False
        patched["_launch_scrape_browser"].return_value = browser
        with pytest.raises(mse.MSEEncryptedError, match="encrypted-media"):
            mse.record_mse_page(
                "https://example.com/player",
                tmp_path / "out.mp4",
                wait_seconds=0,
            )
    assert not list(tmp_path.glob(".streamkeep-mse-*"))


def test_record_mse_page_remuxes_captured_chunks(tmp_path):
    page = _FakePage(event="chunk")
    browser = _FakeBrowser(page)
    manager = _FakePlaywrightManager()
    output = tmp_path / "out.mp4"

    def fake_remux(_staging, target, *, ffmpeg="ffmpeg"):
        assert ffmpeg == "ffmpeg"
        Path(target).write_bytes(b"remuxed")
        return True

    with _capture_patches(page) as patched, \
            mock.patch("playwright.sync_api.sync_playwright", return_value=manager), \
            mock.patch.object(mse, "remux_mse_capture", side_effect=fake_remux):
        patched["ensure_playwright_browser"].return_value = True
        patched["_safe_headless_url"].side_effect = lambda url, **_kwargs: url
        patched["_cancelled"].return_value = False
        patched["_launch_scrape_browser"].return_value = browser
        result = mse.record_mse_page(
            "https://example.com/player", output, wait_seconds=0,
        )
    assert result.output_path == str(output)
    assert result.chunks == 1
    assert result.bytes_written == len(b"fragment")
    assert output.read_bytes() == b"remuxed"
    assert not result.staging_dir

