"""Privacy-safe, bounded request logging for the local companion server."""

import json
import os
import threading
import time
from urllib.parse import urlsplit

from .. import paths as _paths

_REQUEST_LOG_LOCK = threading.Lock()


def _route(value):
    """Return a bounded route without authorities, queries, or fragments."""
    raw = str(value or "/").split("?", 1)[0].split("#", 1)[0]
    try:
        route = urlsplit(raw).path or raw
    except ValueError:
        route = raw
    if "://" in route:
        route = route.split("://", 1)[1]
        route = route[route.find("/"):] if "/" in route else "/"
    route = "".join(
        character for character in route
        if ord(character) >= 32 and ord(character) != 127
    )
    if not route.startswith("/"):
        route = "/" + route
    return route[:160] or "/"


def _method(value):
    method = "".join(
        character for character in str(value or "")
        if ord(character) >= 32 and ord(character) != 127
    )
    return method[:32] or "-"


def _rotate_locked(log_path, backup_count):
    for index in range(max(0, int(backup_count)), 0, -1):
        source = (
            log_path if index == 1
            else log_path.with_name(f"{log_path.name}.{index - 1}")
        )
        destination = log_path.with_name(f"{log_path.name}.{index}")
        try:
            if source.is_file():
                os.replace(source, destination)
        except OSError:
            continue


def _append(entry):
    """Append one request record while bounding the active log and backups."""
    log_path = _paths.SERVER_REQUEST_LOG
    try:
        max_bytes = max(0, int(_paths.SERVER_REQUEST_LOG_MAX_BYTES))
        backup_count = max(0, int(_paths.SERVER_REQUEST_LOG_BACKUP_COUNT))
    except (AttributeError, TypeError, ValueError):
        return
    line = (json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    with _REQUEST_LOG_LOCK:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            current_size = log_path.stat().st_size if log_path.is_file() else 0
            if max_bytes and current_size and current_size + len(line) > max_bytes:
                if backup_count:
                    _rotate_locked(log_path, backup_count)
                else:
                    log_path.unlink(missing_ok=True)
            with log_path.open("ab") as handle:
                handle.write(line)
        except OSError:
            return


class RequestLogMixin:
    """Capture one sanitized request record around ``BaseHTTPRequestHandler``."""

    _request_log_secrets = ()

    def handle_one_request(self):
        self._request_log_started_at = time.perf_counter()
        self._request_log_received = False
        self._request_log_status = None
        self._request_log_bytes = 0
        try:
            return super().handle_one_request()
        finally:
            if not self._request_log_received:
                self._request_log_received = bool(
                    getattr(self, "raw_requestline", b"")
                )
            if self._request_log_received:
                self._write_request_log()

    def parse_request(self):
        self._request_log_received = bool(
            getattr(self, "raw_requestline", b"")
        )
        return super().parse_request()

    def log_message(self, *_args, **_kwargs):
        return

    def log_request(self, code="-", size="-"):
        try:
            self._request_log_status = int(getattr(code, "value", code))
        except (TypeError, ValueError):
            self._request_log_status = 0

    def send_header(self, keyword, value):
        if str(keyword).lower() == "content-length":
            try:
                self._request_log_bytes = max(0, int(value))
            except (TypeError, ValueError):
                self._request_log_bytes = 0
        return super().send_header(keyword, value)

    def _write_request_log(self):
        route = _route(getattr(self, "path", "/"))
        for secret in (*self._request_log_secrets, getattr(self, "_auth_token", "")):
            secret = str(secret or "")
            if secret:
                route = route.replace(secret, "<redacted>")
        try:
            duration_ms = round(
                max(0.0, time.perf_counter() - self._request_log_started_at)
                * 1000,
                3,
            )
        except (AttributeError, TypeError):
            duration_ms = 0.0
        _append({
            "bytes": int(max(0, self._request_log_bytes)),
            "duration_ms": duration_ms,
            "method": _method(getattr(self, "command", "")),
            "path": route,
            "status": int(self._request_log_status or 0),
            "timestamp": time.time(),
        })
