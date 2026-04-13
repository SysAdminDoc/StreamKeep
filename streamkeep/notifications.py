"""In-app notifications center — ring buffer + file-backed JSONL persistence.

Decoupled from the Qt UI so the main window only wires the dropdown; the
ring buffer itself is a pure data structure that can be unit-tested.
File persistence (F4) appends every notification to ``notifications.jsonl``
inside the config directory.
"""

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from .paths import CONFIG_DIR

NOTIF_LOG = CONFIG_DIR / "notifications.jsonl"


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

    def push(self, text, level="info"):
        now = datetime.now()
        note = Notification(
            ts=now.strftime("%H:%M:%S"),
            text=str(text or "")[:200],
            level=str(level or "info"),
        )
        self._buf.appendleft(note)
        self._unread += 1
        self._persist(note, now)
        return note

    def _persist(self, note, now):
        try:
            NOTIF_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(NOTIF_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": now.isoformat(timespec="seconds"),
                    "text": note.text,
                    "level": note.level,
                }) + "\n")
        except OSError:
            pass

    def load_history(self, limit=5000):
        """Load notification history from the JSONL file."""
        entries = []
        try:
            if not NOTIF_LOG.is_file():
                return entries
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
        return entries[-limit:] if len(entries) > limit else entries

    def mark_all_read(self):
        self._unread = 0

    @property
    def unread(self):
        return self._unread

    def items(self):
        return list(self._buf)

    def clear(self):
        self._buf.clear()
        self._unread = 0
