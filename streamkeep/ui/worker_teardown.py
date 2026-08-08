"""Shared Qt worker teardown.

Destroying a running ``QThread`` is a ``qFatal``, not an exception, so every
object that owns one has to join it before it goes away. Three shipped crashes
(V177, V179, V180) had that shape on the main window, and the fix there was to
stop enumerating workers by hand and discover them from the owner's attributes
instead.

That discovery stopped at the window boundary. ``ClipDialog`` owns five workers
and joined them only in ``reject()``, so accepting the dialog -- pressing Export
while the waveform, scene, thumbnail or preview worker was still running -- left
four live threads that the window's sweep structurally could not see, because it
walks the window's own attributes and does not recurse into child objects
(V186).

So the discovery and the stop policy live here, and both the window and the
dialogs use them. ``WorkerOwnerMixin`` hooks ``QDialog.done``, which is the
single funnel every exit path goes through: ``accept()`` and ``reject()`` both
call it, and ``QDialog``'s own ``closeEvent`` calls ``reject()``.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread

_LOGGER = logging.getLogger(__name__)


def stop_worker(
    worker,
    timeout=1500,
    *,
    cancel=False,
    terminate_timeout=0,
    label="worker",
):
    """Stop one Qt worker and report failures instead of hiding them.

    ``cancel`` prefers the worker's own ``cancel()`` hook, which is what a
    ``run()`` override blocked in ``subprocess.communicate()`` actually
    responds to -- ``quit()`` is a no-op there because there is no event loop
    (V179). ``terminate_timeout`` is a last resort and is deliberately opt-in:
    terminating a thread mid-syscall is the undefined behaviour V180 removed
    from the health probe.
    """
    if worker is None:
        return True
    try:
        if not worker.isRunning():
            return True
        stop = getattr(worker, "cancel", None) if cancel else None
        if not callable(stop):
            stop = getattr(worker, "requestInterruption", None)
        if callable(stop):
            stop()
        if worker.wait(max(0, int(timeout))):
            return True
        if terminate_timeout:
            terminate = getattr(worker, "terminate", None)
            if callable(terminate):
                terminate()
            return bool(worker.wait(max(0, int(terminate_timeout))))
        return False
    except Exception as error:
        _LOGGER.warning("[SHUTDOWN] Could not stop %s: %s", label, error)
        return False


def iter_owned_workers(owner):
    """Yield ``(label, worker)`` for every running QThread *owner* holds.

    Discovered by inspecting the owner's own attributes rather than by
    registration, so a worker added later is covered the day its attribute is
    assigned instead of the day someone remembers to add it to a teardown list.
    Dict, list, tuple and set containers are searched one level deep, which is
    how the per-job worker maps are held.
    """
    seen = set()

    def collect(label, value):
        if isinstance(value, QThread):
            if id(value) in seen:
                return
            seen.add(id(value))
            if value.isRunning():
                yield label, value
            return
        if isinstance(value, dict):
            for key, item in list(value.items()):
                yield from collect(f"{label}[{key}]", item)
            return
        if isinstance(value, (list, tuple, set)):
            for index, item in enumerate(value):
                yield from collect(f"{label}[{index}]", item)

    for name, value in list(vars(owner).items()):
        yield from collect(name.lstrip("_") or name, value)


def stop_owned_workers(owner, *, timeout=2000, terminate_timeout=500):
    """Join every running QThread *owner* holds. Returns the labels stopped."""
    stopped = []
    for label, worker in iter_owned_workers(owner):
        stop_worker(
            worker, timeout, cancel=True,
            terminate_timeout=terminate_timeout, label=label,
        )
        stopped.append(label)
    return stopped


class WorkerOwnerMixin:
    """Join owned QThreads on every dialog exit path.

    Mix in before ``QDialog``. ``done`` is the only funnel that needs hooking:
    ``accept()`` and ``reject()`` both route through it, and ``QDialog``'s
    ``closeEvent`` calls ``reject()``. A dialog that also needs ordered or
    longer-waiting teardown for a specific worker can still do that first and
    let this sweep collect whatever is left.
    """

    #: Overridable per dialog when a worker legitimately needs longer.
    WORKER_STOP_TIMEOUT_MS = 2000
    WORKER_TERMINATE_TIMEOUT_MS = 500

    def stop_owned_workers(self):
        return stop_owned_workers(
            self,
            timeout=self.WORKER_STOP_TIMEOUT_MS,
            terminate_timeout=self.WORKER_TERMINATE_TIMEOUT_MS,
        )

    def done(self, result):  # noqa: D102 - Qt override
        self.stop_owned_workers()
        super().done(result)
