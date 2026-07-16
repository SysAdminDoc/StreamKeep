"""Run hidden startup-contract checks against a built StreamKeep artifact."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _windows_processes():
    if os.name != "nt":
        return []
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    rows = []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            rows.append((
                int(entry.th32ProcessID),
                int(entry.th32ParentProcessID),
                str(entry.szExeFile),
            ))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


def _same_image_descendants(root_pid, image_name):
    descendants = {int(root_pid)}
    matches = set()
    rows = _windows_processes()
    changed = True
    while changed:
        changed = False
        for pid, parent_pid, name in rows:
            if parent_pid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
                if name.lower() == image_name.lower():
                    matches.add(pid)
    return matches


def _run_case(executable, run_root, fixture, timeout):
    case_root = run_root / fixture
    config_dir = case_root / "config"
    ready_file = case_root / "ready.json"
    case_root.mkdir(parents=True, exist_ok=False)
    command = [
        str(executable),
        "startup-check",
        "--config-dir", str(config_dir),
        "--ready-file", str(ready_file),
        "--fixture", fixture,
    ]
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        cwd=executable.parent,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    reentry_pids = set()
    timed_out = False
    while proc.poll() is None:
        reentry_pids.update(_same_image_descendants(proc.pid, executable.name))
        if time.monotonic() - started > timeout:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            break
        time.sleep(0.1)
    reentry_pids.update(_same_image_descendants(proc.pid, executable.name))

    marker = {}
    if ready_file.is_file():
        try:
            marker = json.loads(ready_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker = {}
    runtime_pid = marker.get("pid")
    runtime_pid_in_tree = (
        runtime_pid == proc.pid
        or runtime_pid in reentry_pids
    )
    unexpected_reentry_pids = set(reentry_pids)
    if runtime_pid != proc.pid:
        unexpected_reentry_pids.discard(runtime_pid)
    checks = {
        "exit_zero": proc.returncode == 0,
        "within_timeout": not timed_out,
        "ready_marker": marker.get("ready") is True,
        # PyInstaller one-file uses a bootloader parent plus exactly one
        # same-image runtime child.  The readiness marker must come from that
        # direct runtime process; any additional same-image descendant is a
        # real re-entry/fanout failure.
        "marker_pid_in_process_tree": runtime_pid_in_tree,
        "runtime_child_is_direct": (
            runtime_pid == proc.pid or marker.get("parent_pid") == proc.pid
        ),
        "frozen_runtime": marker.get("frozen") is True,
        "no_visible_windows": marker.get("visible_top_level_widgets") == 0,
        "single_qapplication": marker.get("qt_application_instances") == 1,
        "single_application_window": marker.get("application_windows") == 1,
        "no_unexpected_same_image_reentry": not unexpected_reentry_pids,
    }
    return {
        "fixture": fixture,
        "command": command,
        "pid": proc.pid,
        "returncode": proc.returncode,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "same_image_descendant_pids": sorted(reentry_pids),
        "unexpected_reentry_pids": sorted(unexpected_reentry_pids),
        "checks": checks,
        "passed": all(checks.values()),
        "marker": marker,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--work-dir", default="work/artifact-smoke", type=Path)
    parser.add_argument("--timeout", default=45.0, type=float)
    args = parser.parse_args(argv)

    executable = args.executable.expanduser().resolve()
    if not executable.is_file():
        parser.error(f"artifact not found: {executable}")
    work_dir = args.work_dir.expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    run_root = work_dir / (
        "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )
    run_root.mkdir(parents=True, exist_ok=False)

    cases = [
        _run_case(executable, run_root, fixture, args.timeout)
        for fixture in ("empty", "migrated", "populated")
    ]
    summary = {
        "schema_version": 1,
        "artifact": str(executable),
        "run_root": str(run_root),
        "passed": all(case["passed"] for case in cases),
        "cases": cases,
    }
    summary_path = run_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
