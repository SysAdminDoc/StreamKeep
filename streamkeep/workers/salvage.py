"""Background salvage of preserved live-capture fragments."""

from __future__ import annotations

import os
import subprocess
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from ..capabilities import CapabilityUnavailableError, resolve_tool_command
from ..live_capture import (
    build_salvage_command,
    load_report,
    salvage_target,
    write_concat_list,
)
from ..paths import _CREATE_NO_WINDOW


class SalvageWorker(QThread):
    """Rebuild staged captures without blocking the owning Qt window."""

    progress = pyqtSignal(int, int, str)
    log = pyqtSignal(str)
    done = pyqtSignal(object)

    def __init__(self, staging_dirs, parent=None):
        super().__init__(parent)
        self.staging_dirs = [str(path) for path in staging_dirs or ()]
        self._cancel_event = threading.Event()
        self._process_lock = threading.Lock()
        self._process = None

    def cancel(self):
        """Request cancellation and interrupt an ffmpeg process if active."""
        self._cancel_event.set()
        self.requestInterruption()
        with self._process_lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _cancelled(self):
        return self._cancel_event.is_set() or self.isInterruptionRequested()

    @staticmethod
    def _terminate_process(process):
        try:
            process.terminate()
        except OSError:
            pass
        try:
            _stdout, stderr = process.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            _stdout, stderr = process.communicate()
        return stderr or ""

    def _run_ffmpeg(self, command):
        if self._cancelled():
            return None, ""
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as error:
            return -1, str(error)
        with self._process_lock:
            self._process = process
        try:
            while True:
                if self._cancelled():
                    return None, self._terminate_process(process)
                try:
                    _stdout, stderr = process.communicate(timeout=0.2)
                    return process.returncode, stderr or ""
                except subprocess.TimeoutExpired:
                    continue
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None

    def _failed_item(self, result, staging_dir, message):
        result["failed"] += 1
        self.log.emit(
            f"[SALVAGE] ffmpeg could not rebuild "
            f"{os.path.basename(staging_dir)}: {message}"
        )

    def run(self):
        result = {
            "built": 0,
            "failed": 0,
            "skipped": 0,
            "cancelled": False,
            "error": "",
            "total": len(self.staging_dirs),
        }
        try:
            try:
                ffmpeg = resolve_tool_command("ffmpeg")
            except CapabilityUnavailableError as error:
                result["error"] = str(error)
                self.done.emit(result)
                return

            total = len(self.staging_dirs)
            for index, staging_dir in enumerate(self.staging_dirs, start=1):
                if self._cancelled():
                    result["cancelled"] = True
                    break
                name = os.path.basename(staging_dir) or staging_dir
                self.progress.emit(index, total, f"Salvaging {name}")
                target = salvage_target(staging_dir)
                try:
                    report = load_report(staging_dir)
                    summary = str(
                        (report.get("gaps") or {}).get("summary", "") or ""
                    )
                    if os.path.isfile(target):
                        self.log.emit(
                            f"[SALVAGE] {os.path.basename(target)} already exists - "
                            "leaving it untouched."
                        )
                        result["skipped"] += 1
                        continue
                    listing = write_concat_list(staging_dir)
                    if not listing:
                        self.log.emit(
                            f"[SALVAGE] {name} holds no usable fragments."
                        )
                        result["skipped"] += 1
                        continue
                    command = build_salvage_command(
                        staging_dir, target, ffmpeg=ffmpeg, concat_list=listing,
                    )
                except ValueError as error:
                    result["skipped"] += 1
                    self.log.emit(f"[SALVAGE] {error}")
                    continue
                except Exception as error:
                    self._failed_item(result, staging_dir, str(error))
                    continue

                if summary:
                    self.log.emit(f"[SALVAGE] Known gaps: {summary}")
                returncode, stderr = self._run_ffmpeg(command)
                if returncode is None or self._cancelled():
                    result["cancelled"] = True
                    break
                if returncode == 0 and os.path.isfile(target):
                    result["built"] += 1
                    self.log.emit(
                        f"[SALVAGE] Wrote {os.path.basename(target)}"
                    )
                else:
                    tail = (stderr or "").strip().splitlines()[-3:]
                    self._failed_item(result, staging_dir, " ".join(tail))
        except Exception as error:
            result["error"] = str(error)
        self.done.emit(result)
