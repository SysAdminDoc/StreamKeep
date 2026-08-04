"""Best-effort Windows shell integration for the desktop queue.

The taskbar API is exposed through the COM interface that Windows provides to
ordinary Win32 windows.  It is intentionally loaded with :mod:`ctypes` rather
than a Qt add-on: Qt 6 no longer ships the old QtWinExtras taskbar wrapper and
StreamKeep must remain an unsigned, onedir application.

The module contains no Qt or required third-party imports.  Every Windows
surface is optional and failures reduce the feature to a no-op.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import uuid
from collections.abc import Iterable, Mapping


_LOGGER = logging.getLogger(__name__)

TBPF_NOPROGRESS = 0x00000000
TBPF_INDETERMINATE = 0x00000001
TBPF_NORMAL = 0x00000002
TBPF_ERROR = 0x00000004
TBPF_PAUSED = 0x00000008

TASKBAR_PROGRESS_STATES = frozenset({
    "none", "indeterminate", "normal", "error", "paused",
})

_TERMINAL_QUEUE_STATUSES = frozenset({"done", "failed", "cancelled"})


def _clamp_percent(value):
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return 0


def aggregate_queue_progress(queue: Iterable[Mapping], *, paused=False, error=False):
    """Return a taskbar-safe aggregate snapshot for queue rows.

    Completed rows remain part of the aggregate while at least one row is
    still pending, so a queue of three downloads moves 0% -> 33% -> 66% ->
    100% across the whole batch.  Once all rows are complete the shell surface
    is cleared.  Failed rows retain an ``error`` state so an unattended user
    can see that the batch needs attention.
    """
    rows = [row for row in (queue or ()) if isinstance(row, Mapping)]
    considered = [
        row for row in rows
        if str(row.get("status", "queued") or "queued").strip().lower()
        != "cancelled"
    ]
    failed = sum(
        str(row.get("status", "") or "").strip().lower() == "failed"
        for row in considered
    )
    if error and considered:
        failed = max(1, failed)
    pending = [
        row for row in considered
        if str(row.get("status", "queued") or "queued").strip().lower()
        not in _TERMINAL_QUEUE_STATUSES
    ]
    if not pending:
        return {
            "state": "error" if failed else "none",
            "completed": 0 if failed else 0,
            "total": len(considered) if failed else 0,
            "failed": failed,
        }

    total = max(1, len(considered))
    completed = 0
    progress_known = False
    for row in considered:
        status = str(row.get("status", "queued") or "queued").strip().lower()
        if status == "done":
            completed += 100
            progress_known = True
        elif status == "failed":
            continue
        elif "progress" in row:
            completed += _clamp_percent(row.get("progress"))
            progress_known = True

    if failed:
        state = "error"
    elif paused or any(
        str(row.get("status", "") or "").strip().lower() == "paused"
        for row in pending
    ):
        state = "paused"
    elif progress_known:
        state = "normal"
    else:
        state = "indeterminate"

    return {
        "state": state,
        "completed": max(0, min(completed, total * 100)),
        "total": total * 100,
        "failed": failed,
    }


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(value):
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


class TaskbarProgress:
    """Small, failure-tolerant wrapper around ``ITaskbarList3``.

    The wrapper does not set an AppUserModelID, create a package identity, or
    require signing.  It only updates the taskbar button belonging to the
    supplied top-level window handle.
    """

    _CLSID_TASKBAR_LIST = "56FDF344-FD6D-11D0-958A-006097C9A090"
    _IID_TASKBAR_LIST3 = "EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF"

    def __init__(self, *, enabled=True, log_fn=None):
        self._instance = None
        self._vtable = None
        self._ole32 = None
        self._com_initialized = False
        self._log_fn = log_fn
        if enabled and sys.platform == "win32":
            self._initialize()

    @property
    def available(self):
        """Whether the COM interface was loaded successfully."""
        return self._instance is not None and self._vtable is not None

    def _log(self, message):
        try:
            if self._log_fn:
                self._log_fn(message)
            else:
                _LOGGER.debug(message)
        except Exception:
            pass  # safe: shell logging must never affect the queue

    def _initialize(self):
        try:
            self._ole32 = ctypes.WinDLL("ole32")
            co_initialize = self._ole32.CoInitializeEx
            co_initialize.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            co_initialize.restype = ctypes.c_long
            hr = int(co_initialize(None, 0x2))  # COINIT_APARTMENTTHREADED
            if hr < 0:
                self._log(f"[WINDOWS] Taskbar COM initialization unavailable: {hr}")
                return
            self._com_initialized = True

            create_instance = self._ole32.CoCreateInstance
            create_instance.argtypes = [
                ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
                ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
            ]
            create_instance.restype = ctypes.c_long
            instance = ctypes.c_void_p()
            hr = int(create_instance(
                ctypes.byref(_guid(self._CLSID_TASKBAR_LIST)),
                None,
                0x1,  # CLSCTX_INPROC_SERVER
                ctypes.byref(_guid(self._IID_TASKBAR_LIST3)),
                ctypes.byref(instance),
            ))
            if hr < 0 or not instance.value:
                self._log(f"[WINDOWS] ITaskbarList3 unavailable: {hr}")
                self.close()
                return
            self._instance = instance
            self._vtable = ctypes.cast(
                instance,
                ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
            ).contents
            self._call(3)  # ITaskbarList::HrInit
        except Exception as error:
            self._log(f"[WINDOWS] Could not load taskbar progress: {error}")
            self.close()

    def _call(self, index, *argtypes_and_values):
        if not self.available:
            return False
        argtypes = argtypes_and_values[::2]
        values = argtypes_and_values[1::2]
        try:
            prototype = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, *argtypes,
            )
            method = prototype(self._vtable[index])
            return int(method(self._instance, *values)) >= 0
        except Exception as error:
            self._log(f"[WINDOWS] Taskbar operation failed: {error}")
            return False

    def _set_state(self, hwnd, state):
        flags = {
            "none": TBPF_NOPROGRESS,
            "indeterminate": TBPF_INDETERMINATE,
            "normal": TBPF_NORMAL,
            "error": TBPF_ERROR,
            "paused": TBPF_PAUSED,
        }.get(state, TBPF_NORMAL)
        # ITaskbarList3::SetProgressState is vtable slot 10.
        return self._call(
            10,
            ctypes.c_void_p, ctypes.c_void_p(int(hwnd)),
            ctypes.c_uint, ctypes.c_uint(flags),
        )

    def set_progress(self, hwnd, completed, total, state="normal"):
        """Set or clear progress for a top-level window handle."""
        if not self.available or not hwnd:
            return False
        state = str(state or "normal").strip().lower()
        if state not in TASKBAR_PROGRESS_STATES:
            state = "normal"
        if state == "none":
            return self._set_state(hwnd, "none")
        if not self._set_state(hwnd, state):
            return False
        if state == "indeterminate":
            return True
        try:
            completed_value = max(0, int(completed))
            total_value = max(1, int(total))
        except (TypeError, ValueError):
            completed_value, total_value = 0, 1
        # ITaskbarList3::SetProgressValue is vtable slot 9.
        return self._call(
            9,
            ctypes.c_void_p, ctypes.c_void_p(int(hwnd)),
            ctypes.c_ulonglong, ctypes.c_ulonglong(completed_value),
            ctypes.c_ulonglong, ctypes.c_ulonglong(total_value),
        )

    def update(self, hwnd, snapshot):
        """Apply an :func:`aggregate_queue_progress` snapshot."""
        snapshot = snapshot or {}
        return self.set_progress(
            hwnd,
            snapshot.get("completed", 0),
            snapshot.get("total", 0),
            snapshot.get("state", "none"),
        )

    def clear(self, hwnd):
        """Remove the taskbar progress indicator."""
        return self.set_progress(hwnd, 0, 0, "none")

    def close(self):
        """Release the optional COM object without affecting the shell."""
        instance = self._instance
        vtable = self._vtable
        self._instance = None
        self._vtable = None
        try:
            if instance is not None and vtable is not None:
                release = ctypes.WINFUNCTYPE(
                    ctypes.c_ulong, ctypes.c_void_p,
                )(vtable[2])
                release(instance)
        except Exception:
            pass  # safe: COM release is best effort during shutdown
        if self._com_initialized and self._ole32 is not None:
            try:
                self._ole32.CoUninitialize()
            except Exception:
                pass  # safe: COM cleanup is best effort during shutdown
        self._com_initialized = False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass  # safe: interpreter teardown may already have removed ctypes
