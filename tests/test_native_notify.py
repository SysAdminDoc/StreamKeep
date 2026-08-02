"""Tests for the native OS notification layer (F80).

These exercise backend selection and the dispatch/fallback logic without
requiring a real toast backend or a display server.
"""

import sys

import streamkeep.native_notify as nn


def _reset_backend():
    nn._BACKEND = None


def teardown_function(_):
    _reset_backend()


def test_qt_fallback_returns_false_without_tray(monkeypatch):
    """With no native backend and no tray icon, notify() reports not-shown."""
    _reset_backend()
    monkeypatch.setattr(nn, "_detect_backend", lambda: "qt")
    assert nn.notify("Title", "Body") is False


def test_qt_fallback_uses_tray_icon(monkeypatch):
    """The Qt fallback forwards to the supplied tray icon's showMessage."""
    _reset_backend()
    monkeypatch.setattr(nn, "_detect_backend", lambda: "qt")

    class _FakeTray:
        def __init__(self):
            self.calls = []

        def showMessage(self, title, message, icon, msecs):
            self.calls.append((title, message, msecs))

    # Patch the QSystemTrayIcon import target so no real Qt is needed.
    import types

    fake_qtwidgets = types.ModuleType("PyQt6.QtWidgets")

    class _MsgIcon:
        Information = 1

    class _QSystemTrayIcon:
        MessageIcon = _MsgIcon

    fake_qtwidgets.QSystemTrayIcon = _QSystemTrayIcon
    monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", fake_qtwidgets)

    tray = _FakeTray()
    assert nn.notify("Done", "clip.mp4", tray_icon=tray) is True
    assert tray.calls and tray.calls[0][0] == "Done"


def test_toast_backend_dispatches(monkeypatch):
    """When a toast backend is selected, notify() routes to the toast path."""
    _reset_backend()
    monkeypatch.setattr(nn, "_detect_backend", lambda: "toast")
    seen = {}
    monkeypatch.setattr(
        nn, "_notify_toast",
        lambda title, message, actions: seen.update(
            title=title, message=message, actions=actions
        ) or True,
    )
    assert nn.notify("T", "M", actions={"open": "/tmp"}) is True
    assert seen["title"] == "T"
    assert seen["actions"] == {"open": "/tmp"}


def test_is_native_available_matches_backend(monkeypatch):
    _reset_backend()
    monkeypatch.setattr(nn, "_detect_backend", lambda: "toast_legacy")
    assert nn.is_native_available() is True
    monkeypatch.setattr(nn, "_detect_backend", lambda: "qt")
    assert nn.is_native_available() is False


def test_detect_backend_caches():
    """Backend detection memoizes so repeated calls are stable/cheap."""
    _reset_backend()
    first = nn._detect_backend()
    assert first in ("toast", "toast_legacy", "qt")
    assert nn._detect_backend() == first


def test_native_notifications_is_import_validated():
    """The new toggle must be a recognized boolean config key so config
    import/export validates rather than rejects it."""
    from streamkeep.config import _BOOL_CONFIG_KEYS

    assert "native_notifications" in _BOOL_CONFIG_KEYS


def test_desktop_lifecycle_raises_a_native_toast(qt_application, monkeypatch):
    """The desktop notification path must reach the native adapter (V52).

    This is the reachability proof behind the ``native-notifications`` shipped
    capability claim: without a call site the adapter is dead code, and the
    release gate should say so.
    """
    from streamkeep.ui.main_window import StreamKeep

    seen = {}

    def fake_notify(title, message, level="info", tray_icon=None):
        seen.update({"title": title, "message": message, "level": level})
        return True

    monkeypatch.setattr("streamkeep.native_notify.notify", fake_notify)

    window = StreamKeep.__new__(StreamKeep)
    window._tray_icon = None
    monkeypatch.setattr(
        type(window), "isActiveWindow", lambda self: False, raising=False,
    )
    window._fire_native_toast("Download complete", level="success")

    assert seen["title"] == "StreamKeep"
    assert seen["message"] == "Download complete"
    assert seen["level"] == "success"


def test_a_focused_window_suppresses_the_native_toast(qt_application, monkeypatch):
    from streamkeep.ui.main_window import StreamKeep

    calls = []
    monkeypatch.setattr(
        "streamkeep.native_notify.notify",
        lambda *a, **k: calls.append(a) or True,
    )
    window = StreamKeep.__new__(StreamKeep)
    window._tray_icon = None
    monkeypatch.setattr(
        type(window), "isActiveWindow", lambda self: True, raising=False,
    )
    window._fire_native_toast("Download complete")
    assert calls == []
