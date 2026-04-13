"""In-app notifications center — ring buffer + (optional) system beep.

Decoupled from the Qt UI so the main window only wires the dropdown; the
ring buffer itself is a pure data structure that can be unit-tested.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime


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
        note = Notification(
            ts=datetime.now().strftime("%H:%M:%S"),
            text=str(text or "")[:200],
            level=str(level or "info"),
        )
        self._buf.appendleft(note)
        self._unread += 1
        return note

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
