"""In-app notifications center — ring buffer + file-backed JSONL persistence.

Decoupled from the Qt UI so the main window only wires the dropdown; the
ring buffer itself is a pure data structure that can be unit-tested.
File persistence (F4) appends every notification to ``notifications.jsonl``
inside the config directory. Local-server security rejections use a separate,
structured JSONL audit file with route and client identity redaction.
"""

import json
import os
import re
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .paths import CONFIG_DIR

NOTIF_LOG = CONFIG_DIR / "notifications.jsonl"
NOTIF_LOG_MAX_BYTES = 5 * 1024 * 1024
NOTIF_LOG_KEEP_LINES = 20000
SECURITY_EVENT_LOG = CONFIG_DIR / "security-events.jsonl"
SECURITY_EVENT_LOG_MAX_BYTES = 2 * 1024 * 1024
SECURITY_EVENT_LOG_KEEP_LINES = 10000
_NOTIF_FILE_LOCK = threading.Lock()
_SECURITY_EVENT_FILE_LOCK = threading.Lock()
_SECURITY_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_SECURITY_CLIENT_RE = re.compile(r"^client-[a-f0-9]{16}$")


def _security_route(value):
    """Keep an audit route while dropping authorities, queries, and fragments."""
    raw = str(value or "/").split("?", 1)[0].split("#", 1)[0].strip()
    try:
        parsed = urlsplit(raw)
        route = parsed.path or raw
    except ValueError:
        route = raw
    route = re.sub(r"[\x00-\x1f\x7f]", "", route)
    if not route.startswith("/"):
        route = "/" + route
    return route[:160] or "/"


def _security_code(value, fallback):
    code = str(value or "").strip().lower()
    return code if _SECURITY_CODE_RE.fullmatch(code) else fallback


def _security_client(value):
    client = str(value or "").strip().lower()
    return client if _SECURITY_CLIENT_RE.fullmatch(client) else "client-0000000000000000"


def record_security_event(event):
    """Persist and return a privacy-safe local-server security event."""
    event = dict(event or {}) if isinstance(event, dict) else {}
    safe = {
        "timestamp": str(
            event.get("timestamp") or
            datetime.now(timezone.utc).isoformat(timespec="seconds")
        )[:40],
        "route": _security_route(event.get("route")),
        "reason": _security_code(event.get("reason"), "security_rejection"),
        "client_id": _security_client(event.get("client_id")),
        "outcome": _security_code(event.get("outcome"), "rejected"),
    }
    with _SECURITY_EVENT_FILE_LOCK:
        try:
            SECURITY_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(SECURITY_EVENT_LOG, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(safe, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            _compact_security_event_log_locked()
        except OSError:
            pass
    return safe


def _compact_security_event_log_locked():
    try:
        if (
            SECURITY_EVENT_LOG_MAX_BYTES <= 0
            or SECURITY_EVENT_LOG_KEEP_LINES <= 0
            or not SECURITY_EVENT_LOG.is_file()
            or SECURITY_EVENT_LOG.stat().st_size <= SECURITY_EVENT_LOG_MAX_BYTES
        ):
            return
    except OSError:
        return

    lines = deque(maxlen=max(1, int(SECURITY_EVENT_LOG_KEEP_LINES or 1)))
    try:
        with open(SECURITY_EVENT_LOG, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    lines.append(line.rstrip("\r\n"))
    except OSError:
        return

    temporary = SECURITY_EVENT_LOG.with_name(SECURITY_EVENT_LOG.name + ".tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, SECURITY_EVENT_LOG)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def load_security_events(limit=200):
    """Load the newest structured local-server security events."""
    try:
        limit = max(1, int(limit or 1))
    except (TypeError, ValueError):
        limit = 200
    entries = deque(maxlen=limit)
    with _SECURITY_EVENT_FILE_LOCK:
        try:
            if not SECURITY_EVENT_LOG.is_file():
                return []
            with open(SECURITY_EVENT_LOG, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(value, dict):
                        entries.append(value)
        except OSError:
            pass
    return list(entries)


@dataclass
class Notification:
    ts: str = ""
    text: str = ""
    level: str = "info"   # "info" | "success" | "warning" | "error"


class NotificationCenter:
    """Bounded history of recent events. Newest first."""

    def __init__(self, capacity=50):
        self._buf = deque(maxlen=int(capacity))
        self._unread = 0
        self._lock = threading.Lock()

    def push(self, text, level="info"):
        now = datetime.now()
        note = Notification(
            ts=now.strftime("%H:%M:%S"),
            text=str(text or "")[:200],
            level=str(level or "info"),
        )
        with self._lock:
            self._buf.appendleft(note)
            self._unread += 1
        self._persist(note, now)
        return note

    def push_security_event(self, event):
        """Raise an in-app warning for a sanitized local-server rejection."""
        event = dict(event or {}) if isinstance(event, dict) else {}
        reason = _security_code(event.get("reason"), "security_rejection")
        route = _security_route(event.get("route"))
        client_id = _security_client(event.get("client_id"))
        return self.push(
            f"Companion security rejection: {reason} on {route} ({client_id}).",
            "warning",
        )

    def _persist(self, note, now):
        with _NOTIF_FILE_LOCK:
            try:
                NOTIF_LOG.parent.mkdir(parents=True, exist_ok=True)
                with open(NOTIF_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "ts": now.isoformat(timespec="seconds"),
                        "text": note.text,
                        "level": note.level,
                    }) + "\n")
                self._compact_log_locked()
            except OSError:
                pass

    def _compact_log_locked(self):
        try:
            if NOTIF_LOG_MAX_BYTES <= 0 or NOTIF_LOG_KEEP_LINES <= 0:
                return
            if not NOTIF_LOG.is_file() or NOTIF_LOG.stat().st_size <= NOTIF_LOG_MAX_BYTES:
                return
        except OSError:
            return

        lines = deque(maxlen=max(1, int(NOTIF_LOG_KEEP_LINES or 1)))
        try:
            with open(NOTIF_LOG, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        lines.append(line.rstrip("\r\n"))
        except OSError:
            return

        tmp_path = NOTIF_LOG.with_name(NOTIF_LOG.name + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass
            os.replace(tmp_path, NOTIF_LOG)
        except OSError:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def load_history(self, limit=5000):
        """Load notification history from the JSONL file."""
        try:
            limit = max(1, int(limit or 1))
        except (TypeError, ValueError):
            limit = 5000
        entries = deque(maxlen=limit)
        with _NOTIF_FILE_LOCK:
            try:
                if not NOTIF_LOG.is_file():
                    return []
                with open(NOTIF_LOG, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(json.loads(line))
                        except (json.JSONDecodeError, TypeError):
                            continue
            except OSError:
                pass
        return list(entries)

    def mark_all_read(self):
        with self._lock:
            self._unread = 0

    @property
    def unread(self):
        with self._lock:
            return self._unread

    def items(self):
        with self._lock:
            return list(self._buf)

    def clear(self):
        with self._lock:
            self._buf.clear()
            self._unread = 0


def record_notification(text, level="info"):
    """Persist a notification for headless/background producers.

    Desktop callers should use ``NotificationCenter.push`` so the live bell
    updates too. The scrub scheduler has no UI object, so it uses this small
    durable-only bridge.
    """
    center = NotificationCenter(capacity=1)
    return center.push(text, level=level)
