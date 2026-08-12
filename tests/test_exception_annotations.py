"""Guardrails for broad exception fallbacks.

The first version of this guardrail was satisfied by any ``#`` on or above the
``pass``, so 150 of the 174 sites converged on one identical sentence and the
test measured annotation compliance rather than error visibility (V170). A
reason that appears 150 times says nothing about any of them.

Rewriting 150 comments in bulk would only produce 150 plausible sentences
nobody verified, so the boilerplate is frozen instead: every site that carries
it today is counted per file below, that count may only fall, and any *new* or
*moved* broad-except pass must state a reason of its own. The debt is capped
and shrinking rather than notionally repaid.
"""

import ast
import collections
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]

#: Reviewed 2026-08-07. Reasons that are too generic to tell an operator, or a
#: future reader, what is actually being swallowed. A new site may not use one.
_BOILERPLATE_REASONS = frozenset({
    "safe: best-effort fallback; preserve the primary operation",
    "safe: optional windows shell integration",
    "safe: settings changes must not break the window",
    "safe: optional winrt bridge may not be installed",
})

#: Reviewed 2026-08-07. Per-file count of pre-existing sites still carrying a
#: boilerplate reason. These numbers are a ratchet: they may only decrease.
#: Lower one when you replace a boilerplate reason with a real one; a KeyError
#: or a mismatch here means a new boilerplate site was added.
_BOILERPLATE_BUDGET = {
    "streamkeep/channel_stats.py": 2,
    "streamkeep/chat/kick_ws.py": 1,
    "streamkeep/cli.py": 1,
    "streamkeep/clipboard.py": 1,
    "streamkeep/crash_log.py": 1,
    "streamkeep/http.py": 7,
    "streamkeep/i18n/compile_translations.py": 1,
    "streamkeep/integrations/streamlink.py": 3,
    "streamkeep/monitor.py": 1,
    "streamkeep/mse_capture.py": 2,
    "streamkeep/native_notify.py": 2,
    "streamkeep/player/mpv_widget.py": 16,
    "streamkeep/player/sync_viewer.py": 1,
    "streamkeep/podcast_sidecars.py": 1,
    "streamkeep/postprocess/chat_render_worker.py": 2,
    "streamkeep/postprocess/clip_worker.py": 2,
    "streamkeep/postprocess/codecs.py": 1,
    "streamkeep/postprocess/processor.py": 1,
    "streamkeep/scrape.py": 9,
    "streamkeep/secrets.py": 2,
    "streamkeep/server/_legacy.py": 1,
    "streamkeep/ui/intelligence_dialog.py": 1,
    "streamkeep/ui/main_window.py": 14,
    "streamkeep/ui/main_window_jobs.py": 2,
    "streamkeep/ui/monitor_entry_dialog.py": 1,
    "streamkeep/ui/recover_dialog.py": 2,
    "streamkeep/ui/tabs/download_finalize.py": 2,
    "streamkeep/ui/tabs/download_queue.py": 10,
    "streamkeep/ui/tabs/download_single.py": 15,
    "streamkeep/ui/tabs/download_vod.py": 2,
    "streamkeep/ui/tabs/history.py": 3,
    "streamkeep/ui/tabs/monitor.py": 10,
    "streamkeep/ui/tabs/settings_companion.py": 9,
    "streamkeep/ui/thumb_loader.py": 3,
    "streamkeep/ui/widgets.py": 1,
    "streamkeep/upload/ftp.py": 6,
    "streamkeep/utils.py": 3,
    "streamkeep/workers/download.py": 1,
    "streamkeep/workers/finalize.py": 1,
    "streamkeep/youtube_backend.py": 2,
}

#: A reason shorter than this cannot be describing what is being swallowed.
_MIN_REASON_CHARS = 24


def _annotation_for(lines, pass_line):
    """Return the reason attached to a ``pass``, however it was written."""
    current = lines[pass_line - 1]
    if "#" in current:
        return current.split("#", 1)[1].strip()
    block = []
    index = pass_line - 2
    while index >= 0 and lines[index].lstrip().startswith("#"):
        block.insert(0, lines[index].lstrip().lstrip("#").strip())
        index -= 1
    return " ".join(block).strip()


def _broad_exception_passes():
    """Yield ``(relative_path, line, reason)`` for every bare swallow."""
    for path in sorted((_ROOT / "streamkeep").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not isinstance(node.type, ast.Name) or node.type.id != "Exception":
                continue
            if len(node.body) != 1 or not isinstance(node.body[0], ast.Pass):
                continue
            line = node.body[0].lineno
            relative = path.relative_to(_ROOT).as_posix()
            yield relative, line, _annotation_for(lines, line)


def test_every_broad_exception_pass_states_a_reason():
    """The original guardrail: something must be written down."""
    violations = [
        f"{path}:{line}"
        for path, line, reason in _broad_exception_passes()
        if not reason
    ]
    assert not violations, (
        "Unannotated broad exception pass: " + ", ".join(violations)
    )


def test_a_stated_reason_is_long_enough_to_be_one():
    violations = [
        f"{path}:{line} ({reason!r})"
        for path, line, reason in _broad_exception_passes()
        if reason and len(reason) < _MIN_REASON_CHARS
    ]
    assert not violations, (
        "Broad exception pass with a reason too short to explain anything: "
        + ", ".join(violations)
    )


def test_boilerplate_reasons_do_not_spread():
    """The ratchet.

    A boilerplate reason is only tolerated on the sites that already carried
    one when this was frozen, and only up to the count recorded then. Adding a
    new site with a copied reason pushes a file over its budget and fails here;
    replacing one with a real reason puts the file under budget, which also
    fails, with a message telling you to lower the number.
    """
    counts = collections.Counter(
        path for path, _line, reason in _broad_exception_passes()
        if reason.lower() in _BOILERPLATE_REASONS
    )

    over, under = [], []
    for path in sorted(set(counts) | set(_BOILERPLATE_BUDGET)):
        actual = counts.get(path, 0)
        allowed = _BOILERPLATE_BUDGET.get(path, 0)
        if actual > allowed:
            over.append(f"{path}: {actual} boilerplate sites, budget {allowed}")
        elif actual < allowed:
            under.append(f"{path}: {actual} now, budget still {allowed}")

    assert not over, (
        "A broad exception pass may not reuse a boilerplate reason. State what "
        "is actually being swallowed and why losing it is acceptable:\n  "
        + "\n  ".join(over)
    )
    assert not under, (
        "Boilerplate reasons were removed — lower the budget in "
        "_BOILERPLATE_BUDGET so the ratchet holds:\n  " + "\n  ".join(under)
    )


def test_the_boilerplate_budget_only_covers_files_that_exist():
    """A stale budget entry would silently permit a reintroduced site."""
    missing = [
        path for path in _BOILERPLATE_BUDGET if not (_ROOT / path).is_file()
    ]
    assert not missing, "Budget names files that no longer exist: " + ", ".join(missing)


class _Worker:
    def __init__(self, waits):
        self.running = True
        self.waits = list(waits)
        self.calls = []

    def isRunning(self):
        return self.running

    def cancel(self):
        self.calls.append("cancel")

    def requestInterruption(self):
        self.calls.append("interrupt")

    def terminate(self):
        self.calls.append("terminate")

    def wait(self, timeout):
        self.calls.append(("wait", timeout))
        result = self.waits.pop(0)
        if result:
            self.running = False
        return result


def test_stop_worker_escalates_after_cancel_timeout():
    from streamkeep.ui.main_window import _stop_worker

    worker = _Worker([False, True])
    assert _stop_worker(
        worker, 3000, cancel=True, terminate_timeout=500, label="test"
    )
    assert worker.calls == [
        "cancel", ("wait", 3000), "terminate", ("wait", 500),
    ]
