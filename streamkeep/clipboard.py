"""Clipboard monitor — polls for new URLs and emits them."""

import re

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

_URL_RE = re.compile(r'https?://[^\s<>"\'\\]+')
# Punctuation that natural prose tacks onto the end of a URL.
_TRAILING_PUNCT = ".,;:!?\"')]}>"


def _trim_trailing_punctuation(url):
    """Strip prose punctuation captured after a URL.

    A trailing ``)`` / ``]`` / ``}`` is only removed when it is unbalanced —
    URLs that legitimately contain the closing bracket (e.g. Wikipedia links)
    keep it.
    """
    closers = {")": "(", "]": "[", "}": "{"}
    while url and url[-1] in _TRAILING_PUNCT:
        last = url[-1]
        if last in closers:
            opener = closers[last]
            if url.count(opener) >= url.count(last):
                break
        url = url[:-1]
    return url


def extract_url(text):
    """Return the first cleaned http(s) URL in *text*, or ``""``.

    Scans the first non-empty line so pastes like ``here's a link: https://…``
    still work, and trims trailing punctuation the regex over-captures.
    """
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = _URL_RE.search(line)
        if match:
            return _trim_trailing_punctuation(match.group(0))
        break
    return ""


class ClipboardMonitor(QObject):
    """Monitors clipboard for new URLs and emits them."""

    url_detected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._last_clip = ""
        self._enabled = False
        self._timer = QTimer()
        self._timer.timeout.connect(self._check)

    def start(self):
        self._enabled = True
        self._last_clip = QApplication.clipboard().text() or ""
        self._timer.start(800)

    def stop(self):
        self._enabled = False
        self._timer.stop()

    @property
    def is_running(self):
        return self._enabled

    def _check(self):
        if not self._enabled:
            return
        try:
            text = QApplication.clipboard().text() or ""
            if text == self._last_clip:
                return
            self._last_clip = text
            url = extract_url(text)
            if url:
                self.url_detected.emit(url)
        except Exception:
            pass
