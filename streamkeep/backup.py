"""Backup & Restore Wizard — export/import StreamKeep config + data (F72).

Creates a secret-free ``.skbackup`` file (ZIP) containing:
  - config.json (user preferences)
  - library.db (history, monitor, queue)
  - tags.db (tag associations)
  - notifications.jsonl (notification history)

Authentication state and cookies are deliberately excluded. Use the explicit
password-protected portable-secret backup for credential transfer.

Restore extracts and replaces the current config directory contents.
Scheduled auto-backup via ``schedule_backup()`` with rotation.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .paths import CONFIG_DIR

# Files to include in backup (relative to CONFIG_DIR)
BACKUP_FILES = [
    "config.json",
    "library.db",
    "tags.db",
    "search.db",
    "notifications.jsonl",
]
SQLITE_BACKUP_FILES = {"library.db", "search.db", "tags.db"}


def create_backup(output_path, *, include_logs=False):
    """Create a .skbackup file at *output_path*.

    Returns ``(ok, message)``.
    """
    if not output_path:
        return False, "No output path specified"

    snapshot_paths = []
    try:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            # Add metadata
            zf.writestr("_backup_meta.json", _meta_json())

            count = 0
            for fname in BACKUP_FILES:
                fpath = CONFIG_DIR / fname
                if fpath.is_file():
                    if fname == "config.json":
                        try:
                            from .secrets import secret_free_config
                            config = json.loads(fpath.read_text(encoding="utf-8"))
                            safe_config = secret_free_config(config)
                            zf.writestr(
                                fname,
                                json.dumps(safe_config, indent=2, ensure_ascii=False),
                            )
                            count += 1
                            continue
                        except (OSError, json.JSONDecodeError, TypeError):
                            return False, "Backup failed: config.json is invalid"
                    source_path = fpath
                    if fname in SQLITE_BACKUP_FILES:
                        snap = _snapshot_sqlite_db(fpath)
                        if snap is None:
                            return False, f"Backup failed: could not snapshot {fname}"
                        snapshot_paths.append(snap)
                        source_path = snap
                    zf.write(str(source_path), fname)
                    count += 1

            # Optionally include logs
            if include_logs:
                from .diagnostics import redact_text
                for fname in ("streamkeep.log", "streamkeep.log.1", "crash.log"):
                    fpath = CONFIG_DIR / fname
                    if fpath.is_file():
                        safe_log = redact_text(
                            fpath.read_text(encoding="utf-8", errors="replace")
                        )
                        zf.writestr(f"logs/{fname}", safe_log)

        size_kb = os.path.getsize(output_path) / 1024
        return True, f"Backup created: {count} files, {size_kb:.0f} KB"
    except (OSError, zipfile.BadZipFile) as e:
        return False, f"Backup failed: {e}"
    finally:
        for snap in snapshot_paths:
            try:
                Path(snap).unlink(missing_ok=True)
            except OSError:
                pass


def restore_backup(backup_path):
    """Restore from a .skbackup file.

    **Destructive** — replaces current config files with backup contents.
    Returns ``(ok, message)``.
    """
    if not backup_path or not os.path.isfile(backup_path):
        return False, "Backup file not found"

    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            names = zf.namelist()

            # Validate it's a StreamKeep backup
            if "_backup_meta.json" not in names:
                return False, "Invalid backup file (missing metadata)"

            # Extract only known files to CONFIG_DIR
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            restored = 0
            for fname in BACKUP_FILES:
                if fname in names:
                    current = CONFIG_DIR / fname
                    _backup_existing_file(current)
                    if fname in SQLITE_BACKUP_FILES:
                        _remove_sqlite_sidecars(current)
                    data = zf.read(fname)
                    if fname == "config.json":
                        data = _secret_free_config_bytes(data)
                    elif fname == "library.db":
                        data = _secret_free_sqlite_bytes(data)
                    _write_atomic(current, data)
                    restored += 1

        return True, f"Restored {restored} files from backup"
    except (OSError, zipfile.BadZipFile) as e:
        return False, f"Restore failed: {e}"


def list_backup_contents(backup_path):
    """List files in a backup without extracting.

    Returns list of ``(filename, size_bytes)`` tuples.
    """
    if not backup_path or not os.path.isfile(backup_path):
        return []
    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            return [(info.filename, info.file_size) for info in zf.infolist()
                    if not info.filename.startswith("_")]
    except (OSError, zipfile.BadZipFile):
        return []


def auto_backup(backup_dir, *, keep_last=5):
    """Create a timestamped backup and rotate old ones.

    Returns ``(ok, message)``.
    """
    if not backup_dir:
        return False, "No backup directory configured"
    try:
        keep_last = max(1, int(keep_last or 1))
    except (TypeError, ValueError):
        keep_last = 5
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_dir, f"streamkeep_{timestamp}.skbackup")
    ok, msg = create_backup(path)
    if not ok:
        return ok, msg

    # Rotate: keep only the last N backups
    backups = sorted(
        [f for f in os.listdir(backup_dir) if f.endswith(".skbackup")],
        reverse=True,
    )
    for old in backups[keep_last:]:
        try:
            os.unlink(os.path.join(backup_dir, old))
        except OSError:
            pass

    return True, f"Auto-backup: {msg} (keeping last {keep_last})"


def _meta_json():
    """Generate backup metadata JSON."""
    from . import VERSION
    return json.dumps({
        "version": VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "config_dir": str(CONFIG_DIR),
        "includes_auth_state": False,
    }, indent=2)


def _snapshot_sqlite_db(source_path):
    """Create a consistent temporary snapshot of a SQLite database."""
    if not source_path.is_file():
        return None
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"{source_path.stem}_snapshot_",
        suffix=source_path.suffix,
        delete=False,
    )
    tmp_path = Path(tmp.name)
    tmp.close()
    src_db = None
    dst_db = None
    try:
        src_db = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        dst_db = sqlite3.connect(str(tmp_path))
        src_db.backup(dst_db)
        dst_db.commit()
        if source_path.name == "library.db":
            _scrub_database_auth_state(dst_db)
        return tmp_path
    except sqlite3.Error:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    finally:
        if dst_db is not None:
            dst_db.close()
        if src_db is not None:
            src_db.close()


def _backup_existing_file(path):
    """Keep a one-deep pre-restore copy of an existing file."""
    if not path.is_file():
        return
    bak = path.with_suffix(path.suffix + ".pre-restore")
    try:
        shutil.copy2(str(path), str(bak))
    except OSError:
        pass


def _remove_sqlite_sidecars(path):
    """Remove SQLite WAL/SHM sidecars before restoring a database file."""
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass


def _write_atomic(path, data):
    """Write *data* to *path* atomically."""
    tmp = path.with_suffix(path.suffix + ".restore-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        try:
            f.flush()
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass
    os.replace(tmp, path)


def _scrub_database_auth_state(connection):
    from .diagnostics import redact_text
    connection.execute("PRAGMA journal_mode=DELETE")
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'"
    ).fetchone()
    if row:
        connection.execute("DELETE FROM accounts")
    tables = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> 'accounts'"
    ).fetchall()
    for (table,) in tables:
        table_q = '"' + str(table).replace('"', '""') + '"'
        columns = connection.execute(f"PRAGMA table_info({table_q})").fetchall()
        text_columns = [
            str(column[1]) for column in columns
            if any(marker in str(column[2] or "").upper()
                   for marker in ("TEXT", "CHAR", "CLOB"))
        ]
        for column in text_columns:
            column_q = '"' + column.replace('"', '""') + '"'
            rows = connection.execute(
                f"SELECT rowid, {column_q} FROM {table_q} "
                f"WHERE {column_q} IS NOT NULL AND {column_q} <> ''"
            ).fetchall()
            for rowid, value in rows:
                safe_value = redact_text(str(value))
                if safe_value != value:
                    connection.execute(
                        f"UPDATE {table_q} SET {column_q}=? WHERE rowid=?",
                        (safe_value, rowid),
                    )
    connection.commit()
    connection.execute("VACUUM")
    connection.commit()


def _secret_free_config_bytes(data):
    from .secrets import secret_free_config
    config = json.loads(data.decode("utf-8"))
    if not isinstance(config, dict):
        raise ValueError("config.json is not an object")
    return json.dumps(
        secret_free_config(config), indent=2, ensure_ascii=False
    ).encode("utf-8")


def _secret_free_sqlite_bytes(data):
    tmp = tempfile.NamedTemporaryFile(
        prefix="streamkeep_restore_scrub_", suffix=".db", delete=False,
    )
    path = Path(tmp.name)
    try:
        tmp.write(data)
        tmp.close()
        connection = sqlite3.connect(str(path))
        try:
            _scrub_database_auth_state(connection)
        finally:
            connection.close()
        return path.read_bytes()
    finally:
        try:
            tmp.close()
        except OSError:
            pass
        path.unlink(missing_ok=True)
        Path(f"{path}-wal").unlink(missing_ok=True)
        Path(f"{path}-shm").unlink(missing_ok=True)
