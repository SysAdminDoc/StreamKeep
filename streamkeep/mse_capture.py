"""DRM-free Media Source Extensions capture (V14).

This module is an explicit, headless-only capture path for pages that feed a
video through the browser's ``SourceBuffer`` API instead of exposing a normal
media URL.  It installs a Playwright init script before navigation, copies each
successful ``appendBuffer`` payload into bounded staging files, and remuxes
the ordered byte stream with FFmpeg.

The recorder is intentionally narrow:

* exactly one page/tab is captured;
* it never changes playback speed or injects pointer/keyboard input;
* encrypted-media entry points and ``encrypted`` events fail closed;
* network traffic uses the existing headless DNS-pinned request broker;
* failed remuxes retain the staging directory for an explicit recovery pass.

It is not a DRM extractor and cannot capture a page whose playback requires
EME.  The optional Playwright dependency is loaded only when this feature is
called; ordinary StreamKeep startup remains unchanged.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import _CREATE_NO_WINDOW
from .scrape import (
    _HEADLESS_MAX_REDIRECTS,
    _HeadlessNetworkPolicy,
    _cancelled,
    _launch_scrape_browser,
    _pinned_request,
    _safe_headless_url,
    ensure_playwright_browser,
)

MAX_MSE_CHUNKS = 8192
MAX_MSE_CHUNK_BYTES = 16 * 1024 * 1024
MAX_MSE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MSE_WAIT_SECONDS = 3600.0
_SAFE_BUFFER_ID = re.compile(r"[^A-Za-z0-9_-]+")


class MSECaptureError(RuntimeError):
    """Raised when a bounded MSE capture cannot be completed."""


class MSEEncryptedError(MSECaptureError):
    """Raised when a page enters an encrypted-media session."""


class MSEUnavailable(MSECaptureError):
    """Raised when Playwright/Chromium is not available for explicit capture."""


@dataclass(frozen=True)
class MSECaptureResult:
    output_path: str = ""
    staging_dir: str = ""
    chunks: int = 0
    bytes_written: int = 0
    refused_eme: bool = False


MSE_INIT_SCRIPT = r"""
(() => {
  if (window.__streamkeep_mse_recorder) return;
  const state = {
    refused: false,
    nextBufferId: 1,
    buffers: new WeakMap(),
  };
  window.__streamkeep_mse_recorder = state;

  const event = (kind, reason) => {
    state.refused = true;
    try {
      void window.__streamkeep_mse_event({ kind, reason: String(reason || kind) });
    } catch (_) {
      // A page closing while the binding is in flight is harmless; the
      // Python side also observes the state through the binding callback.
    }
  };

  const toBase64 = (value) => {
    const bytes = value instanceof ArrayBuffer
      ? new Uint8Array(value)
      : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    let binary = "";
    const step = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += step) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + step));
    }
    return btoa(binary);
  };

  const append = SourceBuffer.prototype.appendBuffer;
  SourceBuffer.prototype.appendBuffer = function(value) {
    let copy = null;
    try {
      const bytes = value instanceof ArrayBuffer
        ? new Uint8Array(value)
        : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
      copy = bytes.slice().buffer;
    } catch (_) {
      copy = null;
    }
    const result = append.call(this, value);
    if (copy && !state.refused) {
      const bufferId = state.buffers.get(this) || state.nextBufferId++;
      state.buffers.set(this, bufferId);
      queueMicrotask(() => {
        if (state.refused) return;
        try {
          void window.__streamkeep_mse_chunk({
            bufferId,
            data: toBase64(copy),
          });
        } catch (_) {
          // The host may stop the capture while a segment is being queued.
        }
      });
    }
    return result;
  };

  window.addEventListener("encrypted", (detail) => {
    event("eme", "encrypted event");
  }, true);

  if (navigator.requestMediaKeySystemAccess) {
    Object.defineProperty(navigator, "requestMediaKeySystemAccess", {
      configurable: true,
      value: (...args) => {
        event("eme", args[0] || "EME request");
        return Promise.reject(new DOMException(
          "StreamKeep refuses encrypted-media capture", "NotSupportedError"
        ));
      },
    });
  }

  if (window.HTMLMediaElement && HTMLMediaElement.prototype.setMediaKeys) {
    const setMediaKeys = HTMLMediaElement.prototype.setMediaKeys;
    HTMLMediaElement.prototype.setMediaKeys = function(keys) {
      if (keys) {
        event("eme", "setMediaKeys");
        return Promise.reject(new DOMException(
          "StreamKeep refuses encrypted-media capture", "NotSupportedError"
        ));
      }
      return setMediaKeys.call(this, keys);
    };
  }

  if (window.MediaKeySession && MediaKeySession.prototype.generateRequest) {
    const generateRequest = MediaKeySession.prototype.generateRequest;
    MediaKeySession.prototype.generateRequest = function(...args) {
      event("eme", "generateRequest");
      return Promise.reject(new DOMException(
        "StreamKeep refuses encrypted-media capture", "NotSupportedError"
      ));
    };
  }
})();
"""


def build_mse_init_script() -> str:
    """Return a fresh copy of the pre-navigation SourceBuffer/EME hook."""
    return str(MSE_INIT_SCRIPT)


class MSECaptureWriter:
    """Bounded ordered staging writer for SourceBuffer append payloads."""

    def __init__(
        self,
        staging_dir,
        *,
        max_chunks=MAX_MSE_CHUNKS,
        max_chunk_bytes=MAX_MSE_CHUNK_BYTES,
        max_total_bytes=MAX_MSE_TOTAL_BYTES,
    ):
        self.staging_dir = Path(staging_dir)
        self.max_chunks = max(1, int(max_chunks))
        self.max_chunk_bytes = max(1, int(max_chunk_bytes))
        self.max_total_bytes = max(1, int(max_total_bytes))
        self.chunks = 0
        self.bytes_written = 0
        self.refused_eme = False
        self.refusal_reason = ""
        self.error = ""

    def open(self):
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        return self

    def refuse_eme(self, reason="encrypted media"):
        self.refused_eme = True
        self.refusal_reason = str(reason or "encrypted media")

    def write_chunk(self, payload):
        if self.refused_eme:
            return ""
        if not isinstance(payload, dict):
            raise MSECaptureError("MSE chunk payload is not an object")
        encoded = payload.get("data", "")
        if not isinstance(encoded, str) or not encoded:
            raise MSECaptureError("MSE chunk payload is missing data")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise MSECaptureError("MSE chunk payload is not valid base64") from error
        if not data:
            return ""
        if len(data) > self.max_chunk_bytes:
            raise MSECaptureError("MSE chunk exceeds the size limit")
        if self.chunks >= self.max_chunks:
            raise MSECaptureError("MSE capture has too many chunks")
        if self.bytes_written + len(data) > self.max_total_bytes:
            raise MSECaptureError("MSE capture exceeds the total size limit")
        try:
            buffer_id = int(payload.get("bufferId", 0) or 0)
        except (TypeError, ValueError):
            buffer_id = 0
        filename = f"{self.chunks:08d}-buffer{max(0, buffer_id)}.m4s"
        target = self.staging_dir / filename
        target.write_bytes(data)
        self.chunks += 1
        self.bytes_written += len(data)
        return str(target)

    def write_concat_list(self) -> str:
        if self.chunks <= 0:
            return ""
        files = sorted(self.staging_dir.glob("*.m4s"))
        if not files:
            return ""
        listing = self.staging_dir / "concat.txt"
        listing.write_text(
            "".join(
                "file '{}'\n".format(str(path).replace("'", r"'\''"))
                for path in files
            ),
            encoding="utf-8",
        )
        return str(listing)


def build_mse_concat_command(staging_dir, output_path, *, ffmpeg="ffmpeg"):
    """Build an FFmpeg concat/remux command for a populated staging dir."""
    writer = MSECaptureWriter(staging_dir)
    files = sorted(Path(staging_dir).glob("*.m4s"))
    writer.chunks = len(files)
    listing = writer.write_concat_list()
    if not listing:
        raise ValueError("MSE staging directory contains no chunks")
    return [
        str(ffmpeg), "-hide_banner", "-nostdin", "-f", "concat", "-safe", "0",
        "-i", listing, "-c", "copy", "-y", str(output_path),
    ]


def remux_mse_capture(staging_dir, output_path, *, ffmpeg="ffmpeg") -> bool:
    """Remux MSE staging into *output_path* and retain staging on failure."""
    try:
        command = build_mse_concat_command(
            staging_dir, output_path, ffmpeg=ffmpeg,
        )
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False, creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, ValueError):
        return False
    return (
        result.returncode == 0
        and os.path.isfile(output_path)
        and os.path.getsize(output_path) > 0
    )


def _bounded_wait(value) -> float:
    try:
        return max(0.0, min(MAX_MSE_WAIT_SECONDS, float(value)))
    except (TypeError, ValueError, OverflowError):
        return 30.0


def _route_page_request(route, request, *, policy, deadline, should_cancel, log_fn):
    """Serve a page request through the existing pinned headless broker."""
    if _cancelled(should_cancel) or time.monotonic() >= deadline:
        route.abort("blockedbyclient")
        return
    normalized = _safe_headless_url(request.url, policy=policy)
    if not normalized:
        route.abort("blockedbyclient")
        return
    if getattr(request, "resource_type", "") in {"image", "font", "media"}:
        route.abort("blockedbyclient")
        return
    try:
        result = _pinned_request(
            normalized,
            policy=policy,
            method=getattr(request, "method", "GET"),
            headers=getattr(request, "headers", {}),
            timeout=max(0.1, min(8.0, deadline - time.monotonic())),
        )
        if 300 <= result["status"] < 400:
            policy.redirects += 1
            if policy.redirects > _HEADLESS_MAX_REDIRECTS:
                route.abort("blockedbyclient")
                return
        route.fulfill(
            status=result["status"],
            headers=result["headers"],
            body=result["body"],
        )
    except Exception as error:
        if log_fn:
            log_fn(f"[MSE] Blocked request: {str(error)[:160]}")
        route.abort("blockedbyclient")


def record_mse_page(
    page_url,
    output_path,
    *,
    wait_seconds=30,
    log_fn=None,
    should_cancel=None,
    allow_private_network=False,
    cleanup_on_success=True,
) -> MSECaptureResult:
    """Capture one DRM-free MSE page in a fresh headless browser context."""
    policy = _HeadlessNetworkPolicy(
        allow_private_network=allow_private_network,
    )
    page_url = _safe_headless_url(page_url, policy=policy)
    if not page_url:
        raise MSECaptureError("Page URL is outside the headless network policy")
    output_path = str(output_path)
    wait = _bounded_wait(wait_seconds)
    if not ensure_playwright_browser(log_fn, should_cancel=should_cancel):
        raise MSEUnavailable(
            "Playwright Chromium is unavailable; install the optional browser "
            "runtime before starting an MSE capture"
        )
    if _cancelled(should_cancel):
        raise MSECaptureError("MSE capture was cancelled")

    output_parent = os.path.dirname(os.path.abspath(output_path)) or os.getcwd()
    os.makedirs(output_parent, exist_ok=True)
    staging_dir = tempfile.mkdtemp(prefix=".streamkeep-mse-", dir=output_parent)
    writer = MSECaptureWriter(staging_dir).open()
    deadline = time.monotonic() + wait
    browser = None
    context = None
    page = None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = _launch_scrape_browser(playwright)
            context = browser.new_context(
                user_agent="StreamKeep MSE recorder",
                viewport={"width": 1280, "height": 800},
                accept_downloads=False,
                service_workers="block",
                ignore_https_errors=False,
            )
            page = context.new_page()
            page.set_default_timeout(750)
            page.set_default_navigation_timeout(8000)

            def on_event(_source, payload):
                if isinstance(payload, dict) and payload.get("kind") == "eme":
                    writer.refuse_eme(payload.get("reason", "encrypted media"))

            def on_chunk(_source, payload):
                if writer.refused_eme:
                    return
                try:
                    writer.write_chunk(payload)
                except MSECaptureError as error:
                    writer.error = str(error)

            page.expose_binding("__streamkeep_mse_event", on_event)
            page.expose_binding("__streamkeep_mse_chunk", on_chunk)
            page.add_init_script(build_mse_init_script())
            context.route(
                "**/*",
                lambda route, request: _route_page_request(
                    route, request, policy=policy, deadline=deadline,
                    should_cancel=should_cancel, log_fn=log_fn,
                ),
            )
            if hasattr(context, "route_web_socket"):
                context.route_web_socket("**/*", lambda websocket: websocket.close())
            page.on("dialog", lambda dialog: dialog.dismiss())
            page.on("download", lambda download: download.cancel())
            page.on("popup", lambda popup: popup.close())
            if log_fn:
                log_fn(f"[MSE] Loading {page_url[:100]} (one tab, {wait:g}s)")
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=8000)
            except Exception as error:
                if log_fn:
                    log_fn(f"[MSE] Navigation warning: {str(error)[:120]}")
            # Do not alter playback speed or simulate user input. A best-effort
            # play() lets muted/autoplay-safe players begin feeding MSE.
            try:
                page.evaluate(
                    "document.querySelectorAll('video,audio').forEach(v => "
                    "{ v.play().catch(() => {}); });"
                )
            except Exception:
                pass  # safe: best-effort fallback; preserve the primary operation
            while time.monotonic() < deadline:
                if _cancelled(should_cancel):
                    break
                page.wait_for_timeout(
                    int(max(1, min(250, (deadline - time.monotonic()) * 1000)))
                )
    except MSECaptureError:
        raise
    except ImportError as error:
        raise MSEUnavailable(
            "Playwright is not installed; install the optional browser runtime"
        ) from error
    finally:
        for resource in (page, context, browser):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                pass  # safe: best-effort fallback; preserve the primary operation

    if writer.refused_eme:
        reason = writer.refusal_reason or "encrypted media"
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise MSEEncryptedError(
            f"MSE capture refused: encrypted-media session detected ({reason})"
        )
    if writer.error:
        raise MSECaptureError(writer.error)
    if writer.chunks <= 0:
        raise MSECaptureError(
            "No SourceBuffer appendBuffer payloads were captured; "
            "the page may require playback or use a non-MSE media path"
        )
    if not remux_mse_capture(staging_dir, output_path):
        raise MSECaptureError(
            f"FFmpeg could not remux the MSE capture; staging was kept at "
            f"{staging_dir}"
        )
    if cleanup_on_success:
        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_result = ""
    else:
        staging_result = staging_dir
    return MSECaptureResult(
        output_path=output_path,
        staging_dir=staging_result,
        chunks=writer.chunks,
        bytes_written=writer.bytes_written,
    )
