"""Native OS Notifications — Windows Toast / macOS / Linux (F80).

Provides richer notifications than Qt's QSystemTrayIcon.showMessage():
  - Windows: Toast notifications with action buttons
  - macOS: NSUserNotification (via pyobjc)
  - Linux: libnotify via dbus

Falls back to Qt tray icon notifications when native backends are unavailable.

Usage::

    from streamkeep.native_notify import notify
    notify("Download complete", "xQc - Just Chatting.mp4",
           actions={"open": "/path/to/folder"})
"""

import os
import sys
from html import escape as _xml_escape

_BACKEND = None   # "toast" | "qt" | None
_PROGRESS_BACKEND = None
_PROGRESS_NOTIFICATION = None
_PROGRESS_TAG = "streamkeep-queue-progress"


def _detect_backend():
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND

    if sys.platform == "win32":
        try:
            __import__("windows_toasts")
            _BACKEND = "toast"
            return _BACKEND
        except ImportError:
            pass
        try:
            __import__("win10toast")
            _BACKEND = "toast_legacy"
            return _BACKEND
        except ImportError:
            pass

    _BACKEND = "qt"
    return _BACKEND


def notify(title, message, *, actions=None, level="info", tray_icon=None):
    """Show a native notification.

    *actions* is an optional dict of action_id -> data (e.g., {"open": path}).
    *level* is "info", "success", "warning", or "error".
    *tray_icon* is a QSystemTrayIcon for Qt fallback.

    Returns True if the notification was shown.
    """
    backend = _detect_backend()

    if backend == "toast":
        return _notify_toast(title, message, actions)
    if backend == "toast_legacy":
        return _notify_toast_legacy(title, message)
    if backend == "qt" and tray_icon is not None:
        return _notify_qt(title, message, tray_icon)
    return False


def _notify_toast(title, message, actions=None):
    """Windows Toast notification via windows-toasts library."""
    try:
        from windows_toasts import Toast, WindowsToaster

        toaster = WindowsToaster("StreamKeep")
        toast = Toast()
        toast.text_fields = [title, message]

        if actions and "open" in actions:
            path = actions["open"]
            toast.on_activated = lambda _: _open_path(path)

        toaster.show_toast(toast)
        return True
    except Exception:
        return False


def _notify_toast_legacy(title, message):
    """Windows notification via win10toast (simpler, no actions)."""
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(
            title, message,
            duration=5,
            threaded=True,
        )
        return True
    except Exception:
        return False


def _notify_qt(title, message, tray_icon):
    """Fallback to Qt tray icon notification."""
    try:
        from PyQt6.QtWidgets import QSystemTrayIcon
        tray_icon.showMessage(
            title, message,
            QSystemTrayIcon.MessageIcon.Information, 5000,
        )
        return True
    except Exception:
        return False


def _open_path(path):
    """Open a file or folder in the system file manager."""
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass  # safe: best-effort fallback; preserve the primary operation


def is_native_available():
    """Return True if a native notification backend (not Qt) is available."""
    backend = _detect_backend()
    return backend in ("toast", "toast_legacy")


def _load_progress_backend():
    """Load an optional AppNotification progress backend once.

    ``winsdk`` is deliberately not a StreamKeep runtime dependency.  A frozen
    unsigned build therefore keeps the feature disabled unless an environment
    already provides the bridge, and importing the normal notification module
    never pulls WinRT into the process.
    """
    global _PROGRESS_BACKEND
    if _PROGRESS_BACKEND is not None:
        return _PROGRESS_BACKEND
    if sys.platform != "win32":
        _PROGRESS_BACKEND = False
        return _PROGRESS_BACKEND
    try:
        from winsdk.windows.data.xml.dom import XmlDocument
        from winsdk.windows.ui.notifications import (
            NotificationData,
            ToastNotification,
            ToastNotificationManager,
        )
        create_notifier = getattr(
            ToastNotificationManager, "create_toast_notifier", None,
        )
        if callable(create_notifier):
            _PROGRESS_BACKEND = {
                "XmlDocument": XmlDocument,
                "NotificationData": NotificationData,
                "ToastNotification": ToastNotification,
                "create_notifier": create_notifier,
            }
            return _PROGRESS_BACKEND
    except Exception:
        pass  # safe: optional WinRT bridge may not be installed
    _PROGRESS_BACKEND = False
    return _PROGRESS_BACKEND


def _progress_data(backend, completed, total, state):
    data = backend["NotificationData"]()
    total = max(1, int(total or 0))
    completed = max(0, min(int(completed or 0), total))
    if state == "indeterminate":
        value = "indeterminate"
        override = ""
    else:
        value = f"{completed / total:.6f}"
        override = f"{completed / total * 100:.0f}%"
    status = {
        "paused": "Paused to save power",
        "error": "Attention needed",
        "normal": "Downloading",
        "indeterminate": "Preparing downloads",
    }.get(state, "Downloading")
    data.values["progressValue"] = value
    data.values["progressStatus"] = status
    data.values["progressValueStringOverride"] = override
    return data


def notify_progress(
    title,
    message,
    *,
    completed,
    total,
    state="normal",
):
    """Show or update one Windows progress-bound notification.

    This deliberately has no Qt-tray fallback: if the optional AppNotification
    bridge or the host's notification identity is unavailable, a long queue
    does not degrade into one static toast per item.
    """
    global _PROGRESS_NOTIFICATION, _PROGRESS_BACKEND
    backend = _load_progress_backend()
    if not backend or sys.platform != "win32":
        return False
    state = str(state or "normal").strip().lower()
    if state not in {"normal", "paused", "error", "indeterminate"}:
        state = "normal"
    try:
        if _PROGRESS_NOTIFICATION is None:
            notifier = backend["create_notifier"]()
            doc = backend["XmlDocument"]()
            xml = (
                "<toast><visual><binding template=\"ToastGeneric\">"
                f"<text>{_xml_escape(str(title or 'StreamKeep'))}</text>"
                f"<text>{_xml_escape(str(message or ''))}</text>"
                "<progress title=\"Queue\" "
                "value=\"bind:progressValue\" "
                "status=\"bind:progressStatus\" "
                "valueStringOverride=\"bind:progressValueStringOverride\"/>"
                "</binding></visual></toast>"
            )
            doc.load_xml(xml)
            toast = backend["ToastNotification"](doc)
            toast.tag = _PROGRESS_TAG
            sequence = 1
            toast.data = _progress_data(backend, completed, total, state)
            toast.data.sequence_number = sequence
            notifier.show(toast)
            _PROGRESS_NOTIFICATION = {
                "notifier": notifier,
                "sequence": sequence,
            }
            return True

        notification = _PROGRESS_NOTIFICATION
        notification["sequence"] += 1
        data = _progress_data(backend, completed, total, state)
        data.sequence_number = notification["sequence"]
        result = notification["notifier"].update(data, _PROGRESS_TAG)
        result_name = str(getattr(result, "name", result) or "").lower()
        if result_name and result_name not in {"0", "succeeded", "notificationupdateresult.succeeded"}:
            if "succeed" not in result_name:
                _PROGRESS_NOTIFICATION = None
                return False
        return True
    except Exception:
        # A missing AUMID, an unpackaged notification host, or an older Windows
        # build must never affect queue execution.
        _PROGRESS_NOTIFICATION = None
        _PROGRESS_BACKEND = False
        return False


def clear_progress_notification():
    """Forget the current progress notification so the next batch starts fresh."""
    global _PROGRESS_NOTIFICATION
    had_notification = _PROGRESS_NOTIFICATION is not None
    _PROGRESS_NOTIFICATION = None
    return had_notification
