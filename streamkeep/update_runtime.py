"""Atomic updater transaction, health marker, and rollback watchdog."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .sqlite_runtime import connect as sqlite_connect


SNAPSHOT_FILES = (
    "config.json",
    "library.db",
    "search.db",
    "tags.db",
    "update-state.json",
)
SQLITE_FILES = {"library.db", "search.db", "tags.db"}
HEALTH_STABILITY_SECONDS = 2.0


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if mode == "wb" else {"encoding": "utf-8", "newline": "\n"}
    with open(temporary, mode, **kwargs) as handle:
        handle.write(data)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(temporary, path)


def _atomic_json(path, value):
    _atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _snapshot_sqlite(source, destination):
    source_db = destination_db = None
    try:
        source_db = sqlite_connect(
            str(Path(source).resolve()), timeout=10, readonly=True,
            configure_journal=False,
        )
        source_db.execute("PRAGMA query_only=ON")
        destination_db = sqlite_connect(
            str(destination), timeout=10, configure_journal=False,
        )
        source_db.backup(destination_db)
        destination_db.commit()
    finally:
        if destination_db is not None:
            destination_db.close()
        if source_db is not None:
            source_db.close()


def snapshot_state(config_dir, snapshot_dir):
    config_dir = Path(config_dir).resolve()
    snapshot_dir = Path(snapshot_dir).resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    entries = []
    for name in SNAPSHOT_FILES:
        source = config_dir / name
        destination = snapshot_dir / name
        existed = source.is_file()
        if existed:
            if name in SQLITE_FILES:
                _snapshot_sqlite(source, destination)
            else:
                shutil.copy2(source, destination)
        entries.append({"name": name, "existed": existed})
    return entries


def restore_state(config_dir, snapshot_dir, entries):
    config_dir = Path(config_dir).resolve()
    snapshot_dir = Path(snapshot_dir).resolve()
    allowed = set(SNAPSHOT_FILES)
    for entry in entries:
        name = str(entry.get("name", "") or "")
        if name not in allowed:
            raise ValueError("Update snapshot contains an unsupported path.")
        current = config_dir / name
        snapshot = snapshot_dir / name
        if name in SQLITE_FILES:
            for suffix in ("-wal", "-shm"):
                try:
                    Path(f"{current}{suffix}").unlink(missing_ok=True)
                except OSError:
                    pass
        if entry.get("existed"):
            if not snapshot.is_file():
                raise ValueError(f"Update snapshot is missing {name}.")
            _atomic_write(current, snapshot.read_bytes())
        else:
            current.unlink(missing_ok=True)


def prepare_update_transaction(
    *, current_path, staged_path, helper_path, config_dir, release_payload, timeout_seconds=90,
):
    current_path = Path(current_path).resolve()
    staged_path = Path(staged_path).resolve()
    helper_path = Path(helper_path).resolve()
    config_dir = Path(config_dir).resolve()
    if current_path.suffix.lower() != ".exe":
        raise ValueError("Installed update target is not a Windows executable.")
    if current_path.parent != staged_path.parent or staged_path != Path(f"{current_path}.new"):
        raise ValueError("Staged update path is not adjacent to the installed executable.")
    if helper_path != Path(f"{current_path}.update-helper.exe"):
        raise ValueError("Update helper path is not adjacent to the installed executable.")
    recovery_root = config_dir / "update-recovery"
    recovery_root.mkdir(parents=True, exist_ok=True)
    transaction_id = uuid.uuid4().hex
    transaction_dir = recovery_root / transaction_id
    snapshot_dir = transaction_dir / "snapshot"
    try:
        entries = snapshot_state(config_dir, snapshot_dir)
    except (OSError, sqlite3.Error):
        shutil.rmtree(transaction_dir, ignore_errors=True)
        raise
    nonce = uuid.uuid4().hex
    transaction = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "nonce": nonce,
        "created_at": _utc_now(),
        "parent_pid": os.getpid(),
        "timeout_seconds": max(10, int(timeout_seconds)),
        "current_path": str(current_path),
        "staged_path": str(staged_path),
        "backup_path": f"{current_path}.old",
        "helper_path": str(helper_path),
        "config_dir": str(config_dir),
        "snapshot_dir": str(snapshot_dir),
        "snapshot_entries": entries,
        "health_path": str(transaction_dir / "healthy.json"),
        "manifest_sha256": str(release_payload.get("manifest_sha256", "") or ""),
        "sequence": int(release_payload.get("sequence", 0) or 0),
        "new_version": str(release_payload.get("version", "") or ""),
        "old_version": str(release_payload.get("current_version", "") or ""),
    }
    transaction_path = transaction_dir / "transaction.json"
    _atomic_json(transaction_path, transaction)
    return transaction_path


def _load_transaction(transaction_path):
    transaction_path = Path(transaction_path).resolve()
    data = json.loads(transaction_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Unsupported update transaction.")
    required = {
        "transaction_id", "nonce", "parent_pid", "timeout_seconds", "current_path",
        "staged_path", "backup_path", "helper_path", "config_dir", "snapshot_dir",
        "snapshot_entries", "health_path", "manifest_sha256", "sequence", "new_version",
    }
    if not required.issubset(data):
        raise ValueError("Incomplete update transaction.")
    current = Path(data["current_path"]).resolve()
    staged = Path(data["staged_path"]).resolve()
    backup = Path(data["backup_path"]).resolve()
    helper = Path(data["helper_path"]).resolve()
    if staged != Path(f"{current}.new") or backup != Path(f"{current}.old"):
        raise ValueError("Update transaction executable paths are invalid.")
    if helper != Path(f"{current}.update-helper.exe"):
        raise ValueError("Update transaction helper path is invalid.")
    config_dir = Path(data["config_dir"]).resolve()
    if transaction_path.name != "transaction.json":
        raise ValueError("Update transaction filename is invalid.")
    if transaction_path.parent.parent != config_dir / "update-recovery":
        raise ValueError("Update transaction recovery root is invalid.")
    if data.get("transaction_id") != transaction_path.parent.name:
        raise ValueError("Update transaction identity is invalid.")
    snapshot_dir = Path(data["snapshot_dir"]).resolve()
    if snapshot_dir != transaction_path.parent / "snapshot":
        raise ValueError("Update transaction snapshot path is invalid.")
    if Path(data["health_path"]).resolve() != transaction_path.parent / "healthy.json":
        raise ValueError("Update transaction health path is invalid.")
    return data


def _pid_running(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            try:
                return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_for_parent_exit(pid, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_running(pid):
            return True
        time.sleep(0.2)
    return not _pid_running(pid)


def _launch(path, *args):
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    return subprocess.Popen(
        [str(path), *[str(arg) for arg in args]],
        close_fds=True,
        creationflags=flags,
    )


def _healthy(transaction):
    health_path = Path(transaction["health_path"])
    try:
        health = json.loads(health_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return (
        health.get("nonce") == transaction.get("nonce")
        and health.get("version") == transaction.get("new_version")
        and health.get("status") == "healthy"
    )


def _write_recovery_notice(transaction, reason):
    config_dir = Path(transaction["config_dir"])
    message = (
        f"Update to v{transaction.get('new_version', '?')} failed ({reason}). "
        f"StreamKeep restored v{transaction.get('old_version', 'the last-known-good build')} "
        "and its pre-update state."
    )
    log_path = config_dir / "update-recovery.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{_utc_now()} {message}\n")
    _atomic_json(config_dir / "update-recovery-notice.json", {
        "created_at": _utc_now(),
        "transaction_id": transaction.get("transaction_id", ""),
        "message": message,
        "log_path": str(log_path),
    })


def _cleanup_transaction(transaction):
    transaction_dir = Path(transaction["snapshot_dir"]).resolve().parent
    shutil.rmtree(transaction_dir, ignore_errors=True)


def _rollback(transaction, reason):
    current = Path(transaction["current_path"])
    backup = Path(transaction["backup_path"])
    failed = Path(f"{current}.failed")
    try:
        if not backup.is_file():
            _write_recovery_notice(transaction, f"{reason}; last-known-good executable was missing")
            return False
        failed.unlink(missing_ok=True)
        if current.exists():
            os.replace(current, failed)
        if backup.exists():
            os.replace(backup, current)
        restore_state(
            transaction["config_dir"],
            transaction["snapshot_dir"],
            transaction["snapshot_entries"],
        )
        _write_recovery_notice(transaction, reason)
        _launch(current)
        _cleanup_transaction(transaction)
        return True
    except (OSError, ValueError, sqlite3.Error):
        _write_recovery_notice(transaction, f"{reason}; automatic restore was incomplete")
        return False


def run_update_watchdog(transaction_path):
    """Run from the copied last-known-good executable, never the new build."""
    try:
        transaction = _load_transaction(transaction_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return 2
    if not _wait_for_parent_exit(transaction["parent_pid"]):
        _write_recovery_notice(transaction, "the previous process did not exit")
        _cleanup_transaction(transaction)
        return 3
    current = Path(transaction["current_path"])
    staged = Path(transaction["staged_path"])
    backup = Path(transaction["backup_path"])
    try:
        backup.unlink(missing_ok=True)
        os.replace(current, backup)
    except OSError:
        _write_recovery_notice(transaction, "the installed executable could not be staged")
        _cleanup_transaction(transaction)
        return 4
    try:
        os.replace(staged, current)
    except OSError:
        try:
            os.replace(backup, current)
            _write_recovery_notice(transaction, "the replacement executable could not be installed")
            _launch(current)
            _cleanup_transaction(transaction)
        except OSError:
            _write_recovery_notice(
                transaction,
                "the replacement executable and automatic binary restore both failed",
            )
        return 4
    try:
        process = _launch(current, "--update-transaction", str(Path(transaction_path).resolve()))
    except OSError:
        _rollback(transaction, "the executable swap could not be completed")
        return 4
    deadline = time.monotonic() + max(10, int(transaction["timeout_seconds"]))
    healthy_since = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _rollback(transaction, f"the new process exited with code {process.returncode}")
            return 5
        if _healthy(transaction):
            if healthy_since is None:
                healthy_since = time.monotonic()
            if time.monotonic() - healthy_since < HEALTH_STABILITY_SECONDS:
                time.sleep(0.25)
                continue
            try:
                backup.unlink(missing_ok=True)
                Path(f"{current}.failed").unlink(missing_ok=True)
            except OSError:
                pass
            _cleanup_transaction(transaction)
            return 0
        healthy_since = None
        time.sleep(0.25)
    _stop_process_tree(process)
    _rollback(transaction, "startup health confirmation timed out")
    return 6


def _stop_process_tree(process):
    if os.name == "nt" and getattr(process, "pid", 0):
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        if taskkill.is_file():
            try:
                subprocess.run(
                    [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
                process.wait(timeout=5)
                return
            except (OSError, subprocess.SubprocessError):
                pass
    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


def mark_transaction_healthy(transaction_path, version):
    transaction = _load_transaction(transaction_path)
    if str(version) != transaction.get("new_version"):
        raise ValueError("Running version does not match the update transaction.")
    current = Path(transaction["current_path"]).resolve()
    if getattr(sys, "frozen", False) and Path(sys.executable).resolve() != current:
        raise ValueError("Running executable does not match the update transaction.")
    state_path = Path(transaction["config_dir"]) / "update-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError, json.JSONDecodeError):
        state = {}
    state.update({
        "highest_sequence": int(transaction["sequence"]),
        "highest_version": version,
        "manifest_sha256": transaction["manifest_sha256"],
        "last_healthy_version": version,
        "last_healthy_at": _utc_now(),
    })
    _atomic_json(state_path, state)
    # The watchdog treats this marker as the commit record, so write it only
    # after all migration-sensitive state and monotonic update state are safe.
    _atomic_json(transaction["health_path"], {
        "status": "healthy",
        "nonce": transaction["nonce"],
        "version": version,
        "confirmed_at": _utc_now(),
    })
    return True


def consume_recovery_notice(config_dir):
    path = Path(config_dir) / "update-recovery-notice.json"
    try:
        notice = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(notice, dict):
            return None
        return notice
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def cleanup_update_helper(executable):
    helper = Path(f"{Path(executable).resolve()}.update-helper.exe")
    try:
        helper.unlink(missing_ok=True)
        Path(f"{Path(executable).resolve()}.failed").unlink(missing_ok=True)
    except OSError:
        pass
