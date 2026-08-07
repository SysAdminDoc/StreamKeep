"""Validated FFmpeg jobs for operator-selected raw media protocols.

Raw capture deliberately bypasses yt-dlp: cameras, listener sockets, SRT
links, multicast IPTV, and ICY radio are transports rather than web pages.
This module owns the protocol grammar, bounded duration policy, redacted
command export, and headless subprocess runner used by the CLI and worker.
"""

from __future__ import annotations

import ipaddress
import queue
import re
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path

from .capabilities import (
    CapabilityUnavailableError,
    require_capability,
    resolve_tool_command,
    version_at_least,
)
from .icy import IcySplitResult, split_icy_stream
from .net_guard import GuardedHTTPProxy, validate_remote_url
from .paths import FFMPEG_RAW_CAPTURE_SAFETY, _CREATE_NO_WINDOW


RAW_PROTOCOLS = (
    "rtsp",
    "rtmp-listen",
    "srt-caller",
    "srt-listener",
    "udp",
    "rtp",
    "icy",
)
RAW_PROTOCOL_ALIASES = {
    "rtmp": "rtmp-listen",
    "srt": "srt-caller",
    "radio": "icy",
}
MAX_CAPTURE_SECONDS = 7 * 24 * 60 * 60
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_CAPTURE_SHUTDOWN_GRACE_SECONDS = 5


class RawCaptureError(ValueError):
    """Raised when a raw capture cannot be safely constructed."""


@dataclass(frozen=True)
class RawCaptureSpec:
    """Immutable raw-protocol capture job.

    ``passphrase`` is intentionally excluded from :meth:`to_public_dict` and
    from every diagnostic string. Callers should supply it from stdin or an
    environment-backed secret, never a command-line argument.
    """

    protocol: str
    endpoint: str
    output_path: str
    transport: str = "tcp"
    duration_secs: int = 0
    max_duration_secs: int = MAX_CAPTURE_SECONDS
    split_tracks: bool = False
    allow_self_signed: bool = False
    passphrase: str = ""
    user_agent: str = "StreamKeep raw capture"

    def to_public_dict(self) -> dict[str, object]:
        return {
            "protocol": normalize_protocol(self.protocol),
            "endpoint": redact_endpoint(self.endpoint, self.passphrase),
            "output_path": self.output_path,
            "transport": self.transport,
            "duration_secs": int(self.duration_secs or 0),
            "max_duration_secs": int(self.max_duration_secs or 0),
            "split_tracks": bool(self.split_tracks),
            "allow_self_signed": bool(self.allow_self_signed),
        }

    @property
    def effective_duration_secs(self) -> int:
        requested = int(self.duration_secs or 0)
        maximum = int(self.max_duration_secs or 0)
        return requested or maximum


@dataclass(frozen=True)
class ValidatedRawCapture:
    spec: RawCaptureSpec
    endpoint: str
    protocol: str


@dataclass(frozen=True)
class RawCaptureResult:
    success: bool
    exit_code: int
    output_path: str
    elapsed_secs: float
    lines: tuple[str, ...] = ()
    stopped: bool = False
    tracks_manifest: str = ""


def normalize_protocol(value: str) -> str:
    protocol = str(value or "").strip().lower()
    protocol = RAW_PROTOCOL_ALIASES.get(protocol, protocol)
    if protocol not in RAW_PROTOCOLS:
        choices = ", ".join(RAW_PROTOCOLS)
        raise RawCaptureError(f"unsupported raw protocol {protocol!r}; choose {choices}")
    return protocol


def _clean_text(
    value: str,
    label: str,
    *,
    required: bool = True,
    allow_backslash: bool = False,
) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise RawCaptureError(f"{label} is required")
    if (
        len(text) > 8192
        or _CONTROL_RE.search(text)
        or ("\\" in text and not allow_backslash)
    ):
        raise RawCaptureError(f"{label} contains unsafe characters")
    return text


def _url_endpoint(value: str, schemes: set[str], label: str) -> str:
    text = _clean_text(value, label)
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError as error:
        raise RawCaptureError(f"{label} is malformed") from error
    if parsed.scheme.lower() not in schemes or not parsed.hostname:
        raise RawCaptureError(
            f"{label} must use one of {', '.join(sorted(schemes))} and include a host"
        )
    if parsed.fragment:
        raise RawCaptureError(f"{label} must not contain a fragment")
    try:
        port = parsed.port
    except ValueError as error:
        raise RawCaptureError(f"{label} has an invalid port") from error
    if port is not None and not 1 <= port <= 65535:
        raise RawCaptureError(f"{label} port is outside 1-65535")
    return text


def _host_port_endpoint(value: str, protocol: str) -> str:
    text = _clean_text(value, "endpoint")
    candidate = text if "://" in text else f"{protocol}://{text}"
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError as error:
        raise RawCaptureError("endpoint is malformed") from error
    if parsed.scheme.lower() != protocol or not parsed.hostname:
        raise RawCaptureError(f"endpoint must be a {protocol} multicast URI")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        port = parsed.port
    except (ValueError, TypeError) as error:
        raise RawCaptureError(
            f"{protocol.upper()} capture requires a numeric multicast host and port"
        ) from error
    if not address.is_multicast:
        raise RawCaptureError(f"{protocol.upper()} capture requires a multicast address")
    if port is None or not 1 <= port <= 65535:
        raise RawCaptureError("multicast port is outside 1-65535")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise RawCaptureError("multicast endpoint may not contain credentials or options")
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"{protocol}://@{host}:{port}"


def _srt_endpoint(spec: RawCaptureSpec, mode: str) -> str:
    text = _url_endpoint(spec.endpoint, {"srt"}, "endpoint")
    parsed = urllib.parse.urlsplit(text)
    host = parsed.hostname or ""
    is_listener = mode == "listener"
    if is_listener:
        allowed = {"0.0.0.0", "::", "localhost"}
        if host not in allowed:
            raise RawCaptureError("SRT listener must bind 0.0.0.0, ::, or localhost")
    elif host in {"0.0.0.0", "::", "localhost"}:
        raise RawCaptureError("SRT caller requires a remote host")
    try:
        port = parsed.port
    except ValueError as error:
        raise RawCaptureError("SRT endpoint has an invalid port") from error
    if port is None or not 1 <= port <= 65535:
        raise RawCaptureError("SRT port is outside 1-65535")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    passphrase = str(spec.passphrase or "")
    if passphrase and not 10 <= len(passphrase) <= 79:
        raise RawCaptureError("SRT passphrases must be 10-79 characters")
    existing_mode = str(query.get("mode", [""])[0]).lower()
    if existing_mode and existing_mode != mode:
        raise RawCaptureError(f"SRT endpoint mode must be {mode}")
    existing_passphrase = str(query.get("passphrase", [""])[0])
    if existing_passphrase and existing_passphrase != passphrase:
        raise RawCaptureError(
            "SRT endpoint passphrase must match the supplied secret"
        )
    query["mode"] = [mode]
    if passphrase:
        query["passphrase"] = [passphrase]
    host_text = parsed.netloc.rsplit("@", 1)[-1]
    if ":" not in host_text and host == "::":
        host_text = "[::]:" + str(port)
    return urllib.parse.urlunsplit((
        "srt", host_text, parsed.path, urllib.parse.urlencode(query, doseq=True), "",
    ))


def validate_raw_capture(spec: RawCaptureSpec) -> ValidatedRawCapture:
    """Validate and normalize one raw capture without opening a socket."""
    if not isinstance(spec, RawCaptureSpec):
        raise RawCaptureError("capture requires a RawCaptureSpec")
    protocol = normalize_protocol(spec.protocol)
    output = _clean_text(
        spec.output_path, "output path", allow_backslash=True,
    )
    if Path(output).name in {"", ".", ".."}:
        raise RawCaptureError("output path must name a file")
    try:
        duration = int(spec.duration_secs or 0)
        maximum = int(spec.max_duration_secs or 0)
    except (TypeError, ValueError) as error:
        raise RawCaptureError("capture duration must be an integer") from error
    if duration < 0 or maximum <= 0:
        raise RawCaptureError("capture duration must be non-negative and max duration positive")
    if maximum > MAX_CAPTURE_SECONDS:
        raise RawCaptureError(
            f"max duration cannot exceed {MAX_CAPTURE_SECONDS} seconds"
        )
    if duration and duration > maximum:
        raise RawCaptureError("duration cannot exceed max duration")
    transport = str(spec.transport or "tcp").lower()
    if transport not in {"tcp", "udp"}:
        raise RawCaptureError("RTSP transport must be tcp or udp")
    if spec.split_tracks and protocol != "icy":
        raise RawCaptureError("track splitting is available only for ICY radio")
    if spec.passphrase and protocol not in {"srt-caller", "srt-listener"}:
        raise RawCaptureError("passphrase is available only for SRT capture")

    if protocol == "rtsp":
        endpoint = _url_endpoint(spec.endpoint, {"rtsp", "rtsps"}, "endpoint")
        if spec.allow_self_signed and not endpoint.lower().startswith("rtsps://"):
            raise RawCaptureError("allow-self-signed requires an RTSPS endpoint")
    elif protocol == "rtmp-listen":
        endpoint = _url_endpoint(spec.endpoint, {"rtmp", "rtmps"}, "endpoint")
        try:
            parsed = urllib.parse.urlsplit(endpoint)
            port = parsed.port
        except ValueError as error:
            raise RawCaptureError("RTMP listener port is invalid") from error
        if parsed.hostname not in {"0.0.0.0", "::", "localhost", "127.0.0.1"}:
            raise RawCaptureError("RTMP listener must bind a local wildcard or localhost")
        if port is None:
            raise RawCaptureError("RTMP listener requires an explicit port")
        if spec.allow_self_signed and not endpoint.lower().startswith("rtmps://"):
            raise RawCaptureError("allow-self-signed requires an RTMPS endpoint")
    elif protocol == "srt-caller":
        endpoint = _srt_endpoint(spec, "caller")
        if spec.allow_self_signed:
            raise RawCaptureError("SRT does not use TLS self-signed certificates")
    elif protocol == "srt-listener":
        endpoint = _srt_endpoint(spec, "listener")
        if spec.allow_self_signed:
            raise RawCaptureError("SRT does not use TLS self-signed certificates")
    elif protocol in {"udp", "rtp"}:
        endpoint = _host_port_endpoint(spec.endpoint, protocol)
        if spec.allow_self_signed:
            raise RawCaptureError("self-signed TLS is not applicable to multicast")
    else:
        endpoint = _url_endpoint(spec.endpoint, {"http", "https"}, "endpoint")
        if spec.passphrase or spec.allow_self_signed:
            raise RawCaptureError("ICY capture does not accept SRT or TLS options")

    normalized = replace(
        spec,
        protocol=protocol,
        endpoint=endpoint,
        transport=transport,
        duration_secs=duration,
        max_duration_secs=maximum,
        output_path=output,
    )
    return ValidatedRawCapture(normalized, endpoint, protocol)


def redact_endpoint(endpoint: str, passphrase: str = "") -> str:
    """Remove endpoint credentials and SRT passphrases from diagnostics."""
    text = str(endpoint or "")
    if passphrase:
        text = text.replace(str(passphrase), "***")
    try:
        parsed = urllib.parse.urlsplit(text)
        if parsed.username is not None or parsed.password is not None:
            host = parsed.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port else ""
            text = urllib.parse.urlunsplit((
                parsed.scheme, f"***@{host}{port}", parsed.path,
                parsed.query, "",
            ))
    except ValueError:
        pass
    return text


def redact_capture_line(line: str, spec: RawCaptureSpec) -> str:
    """Redact the endpoint and SRT secret from an FFmpeg diagnostic line."""
    text = str(line or "")
    text = text.replace(spec.endpoint, redact_endpoint(spec.endpoint, spec.passphrase))
    if spec.passphrase:
        text = text.replace(spec.passphrase, "***")
    return text


def _ffmpeg_version(ffmpeg_version: str | None) -> str:
    if ffmpeg_version:
        return str(ffmpeg_version)
    try:
        return str(require_capability("ffmpeg").get("version") or "")
    except CapabilityUnavailableError as error:
        raise RawCaptureError(str(error)) from error


def build_ffmpeg_command(
    spec: RawCaptureSpec,
    *,
    executable: str = "ffmpeg",
    ffmpeg_version: str | None = "8.1.2",
    proxy_url: str = "",
) -> list[str]:
    """Build an explicit FFmpeg argv for a non-split raw capture."""
    validated = validate_raw_capture(spec)
    if validated.protocol == "icy" and validated.spec.split_tracks:
        raise RawCaptureError(
            "ICY track splitting is handled by the metadata splitter, not FFmpeg argv"
        )
    version = _ffmpeg_version(ffmpeg_version)
    command = [str(executable), *FFMPEG_RAW_CAPTURE_SAFETY, "-hide_banner", "-loglevel", "warning"]
    protocol = validated.protocol
    endpoint = validated.endpoint
    if protocol == "rtsp":
        command.extend(["-rtsp_transport", validated.spec.transport])
    elif protocol == "rtmp-listen":
        command.extend(["-listen", "1"])
    elif protocol == "icy":
        command.extend([
            "-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_at_eof", "1", "-reconnect_delay_max", "10",
            "-icy", "1",
        ])
        if proxy_url:
            command.extend(["-http_proxy", proxy_url])
    # Stated in both directions, never inherited: FFmpeg 8.x defaults
    # tls_verify to 0 and 9.0 hardcodes it to 1, so leaving it unset made
    # ``allow_self_signed`` a no-op on 8.x (self-signed origins already worked
    # without the opt-in) and a hard requirement on 9.0 — the same spec
    # behaving differently on the two supported majors.
    if validated.spec.allow_self_signed:
        if not version_at_least(version, "8.0.0"):
            raise RawCaptureError(
                "allow-self-signed capture requires FFmpeg 8 or newer"
            )
        command.extend(["-tls_verify", "0"])
    else:
        command.extend(["-tls_verify", "1"])
    command.extend(["-i", endpoint, "-map", "0", "-c", "copy"])
    command.extend(["-t", str(validated.spec.effective_duration_secs)])
    command.extend(["-y", validated.spec.output_path])
    return command


def _run_process(
    command: list[str],
    spec: RawCaptureSpec,
    *,
    stop_event=None,
    on_line=None,
) -> RawCaptureResult:
    started = time.monotonic()
    lines: list[str] = []
    stopped = False
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as error:
        raise RawCaptureError(f"FFmpeg could not start: {error}") from error

    output_queue: queue.Queue[str | None] = queue.Queue()

    def pump_output():
        try:
            for line in process.stdout or ():
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=pump_output, name="raw-capture-output")
    reader.daemon = True
    reader.start()
    deadline = started + spec.effective_duration_secs + _CAPTURE_SHUTDOWN_GRACE_SECONDS
    try:
        reader_done = False
        while not reader_done:
            try:
                line = output_queue.get(timeout=0.1)
            except queue.Empty:
                hit_stop = stop_event is not None and stop_event.is_set()
                hit_deadline = time.monotonic() >= deadline
                if (hit_stop or hit_deadline) and process.poll() is None:
                    stopped = True
                    process.terminate()
                continue
            if line is None:
                reader_done = True
                continue
            clean = redact_capture_line(line.rstrip(), spec)
            if clean:
                lines.append(clean)
                if on_line is not None:
                    on_line(clean)
        if stop_event is not None and stop_event.is_set() and process.poll() is None:
            stopped = True
            process.terminate()
        try:
            returncode = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait(timeout=5)
    except KeyboardInterrupt:
        stopped = True
        process.terminate()
        returncode = process.wait(timeout=15)
    finally:
        reader.join(timeout=5)
    output = Path(spec.output_path)
    produced = output.is_file() and output.stat().st_size > 0
    success = bool(returncode == 0 and produced or stopped and produced)
    return RawCaptureResult(
        success, int(returncode), str(output), time.monotonic() - started,
        tuple(lines), stopped,
    )


def _capture_icy_tracks(
    spec: RawCaptureSpec,
    *,
    stop_event=None,
    on_line=None,
) -> RawCaptureResult:
    """Capture an ICY response through the guarded HTTP proxy and split it."""
    import urllib.request

    target = validate_remote_url(spec.endpoint)
    proxy = GuardedHTTPProxy()
    proxy.start()
    started = time.monotonic()
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy.url, "https": proxy.url})
        )
        request = urllib.request.Request(
            target.url,
            headers={
                "Icy-MetaData": "1",
                "User-Agent": spec.user_agent or "StreamKeep raw capture",
            },
        )
        with opener.open(request, timeout=20) as response:
            try:
                metaint = int(response.headers.get("icy-metaint", "0") or 0)
            except ValueError:
                metaint = 0
            result: IcySplitResult = split_icy_stream(
                response,
                spec.output_path,
                metaint,
                stop_event=stop_event,
                max_seconds=spec.effective_duration_secs,
            )
        message = f"ICY wrote {len(result.tracks)} track(s) to {result.manifest_path}"
        if on_line is not None:
            on_line(message)
        return RawCaptureResult(
            bool(result.tracks), 0 if result.tracks else 1,
            str(spec.output_path), time.monotonic() - started,
            (message,), result.stopped, result.manifest_path,
        )
    except Exception as error:
        message = redact_capture_line(str(error), spec)
        if on_line is not None:
            on_line(message)
        return RawCaptureResult(
            False, 1, str(spec.output_path), time.monotonic() - started,
            (message,), False, "",
        )
    finally:
        proxy.stop()


def run_raw_capture(
    spec: RawCaptureSpec,
    *,
    ffmpeg_path: str | None = None,
    ffmpeg_version: str | None = None,
    stop_event=None,
    on_line=None,
) -> RawCaptureResult:
    """Run one capture job headlessly and return a redacted result."""
    validated = validate_raw_capture(spec)
    Path(validated.spec.output_path).parent.mkdir(parents=True, exist_ok=True)
    if validated.protocol == "icy" and validated.spec.split_tracks:
        return _capture_icy_tracks(
            validated.spec, stop_event=stop_event, on_line=on_line,
        )

    proxy = None
    proxy_url = ""
    try:
        if validated.protocol == "icy":
            validate_remote_url(validated.endpoint)
            proxy = GuardedHTTPProxy()
            proxy_url = proxy.start()
        executable = ffmpeg_path or resolve_tool_command("ffmpeg")
        command = build_ffmpeg_command(
            validated.spec,
            executable=executable,
            ffmpeg_version=ffmpeg_version,
            proxy_url=proxy_url,
        )
        return _run_process(
            command, validated.spec, stop_event=stop_event, on_line=on_line,
        )
    finally:
        if proxy is not None:
            proxy.stop()
