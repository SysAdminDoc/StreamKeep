"""Startup crash-recovery reporting.

``init_db`` runs three recovery entry points -- an interrupted restore, an
interrupted rebuild, an interrupted re-template -- before and after opening the
schema. They are the only defence against a config directory left half-swapped
by a process that died mid-operation.

Their failure used to be swallowed, so the app carried on against a mixed
directory with no log line, no crash-log entry and no warning: the failure mode
this code exists to prevent became the one nobody could see (V185). It is still
not allowed to abort startup, because a recovery that cannot run is not a reason
to refuse to open the library -- so the failure is recorded, logged, and written
to the crash log, and startup continues.

Kept out of ``_legacy.py`` so the monolith keeps shrinking (V163).
"""
from __future__ import annotations

import logging
from importlib import import_module
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

#: Recovery failures recorded during the most recent ``init_db``. Drained by
#: the startup path so the operator is told the library may be half-restored,
#: rather than the warning existing only in a rotating app log.
_FAILURES: list[dict[str, Any]] = []


def call_recovery(module_name: str, attribute: str, **kwargs) -> Any:
    """Import and call one crash-recovery entry point.

    Imported lazily and by name because ``backup``, ``rebuild`` and
    ``maintenance`` all import the database package, so a module-level import
    here would be circular.
    """
    module = import_module(module_name, "streamkeep")
    return getattr(module, attribute)(**kwargs)


def report_failure(label: str, action: Callable[[], Any]) -> Any:
    """Run *action*, recording and logging any failure without aborting.

    Returns the action's result, or ``None`` when it failed.
    """
    try:
        return action()
    except Exception as error:
        detail = f"{label} recovery failed: {error}"
        _FAILURES.append({"stage": label, "error": str(error)})
        _LOGGER.error("[RECOVERY] %s", detail)
        try:
            from ..crash_log import record_startup_warning
            record_startup_warning(detail)
        except Exception:
            # The crash log is itself best-effort; the logger call above and
            # the recorded entry are what carry this finding.
            pass
        return None


def consume_failures() -> list[dict[str, Any]]:
    """Return and clear the failures recorded during ``init_db``."""
    failures = list(_FAILURES)
    _FAILURES.clear()
    return failures
