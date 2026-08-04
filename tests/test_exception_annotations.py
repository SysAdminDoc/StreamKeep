"""Guardrails for broad exception fallbacks."""

import ast
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]


def test_broad_exception_passes_have_an_inline_safety_reason():
    violations = []
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
            current = lines[line - 1]
            previous = lines[line - 2].lstrip() if line > 1 else ""
            if "#" not in current and not previous.startswith("#"):
                violations.append(f"{path}:{line}")
    assert not violations, "Unannotated broad exception pass: " + ", ".join(violations)


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
