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
from .sqlite_runtime import connect as sqlite_connect

# Files to include in backup (relative to CONFIG_DIR)
BACKUP_FILES = [
    "config.json",
    "library.db",
    "tags.db",
    "search.db",
    "notifications.jsonl",
]
SQLITE_BACKUP_FILES = {"library.db", "search.db", "tags.db"}

# Marker written while the destructive activation phase of a restore is in
# flight. If the process dies between the first and last atomic file swap the
# config directory is left in a mixed state; the next startup rolls every file
# back to its ``.pre-restore`` copy (see ``finalize_interrupted_restore``).
RESTORE_MARKER = ".restore-in-progress.json"


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


class _RestoreError(Exception):
    """Backup content failed a pre-activation trust or integrity check."""


def restore_backup(backup_path):
    """Restore from a .skbackup file.

    **Destructive on success only.** Backup contents are extracted to a private
    staging directory, scrubbed of authentication state, and validated
    (metadata/schema versions, ``trusted_schema=OFF`` ``quick_check`` and
    ``foreign_key_check``, and FTS rebuild) before any current file is touched.
    If any staged file fails validation the current config directory is left
    byte-for-byte intact and a redacted report is returned. Only after every
    staged file validates are the files swapped into place.

    Each swap is individually atomic (``os.replace``); the *set* is made
    crash-consistent by a restore marker. ``config.json`` is swapped last, and
    if the process dies mid-activation the next startup
    (``finalize_interrupted_restore``) rolls every file back to its
    ``.pre-restore`` copy, returning the directory to its prior self-consistent
    state. On clean completion the marker and ``.pre-restore`` copies are
    removed.

    Returns ``(ok, message)``.
    """
    if not backup_path or not os.path.isfile(backup_path):
        return False, "Backup file not found"

    from .diagnostics import redact_text

    staging_dir = None
    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            names = zf.namelist()

            # Validate it's a StreamKeep backup with parseable metadata.
            if "_backup_meta.json" not in names:
                return False, "Invalid backup file (missing metadata)"
            _validate_backup_meta(zf)

            # Stage every known file into a private directory, transforming and
            # validating it there. Nothing under CONFIG_DIR is touched yet.
            staging_dir = Path(tempfile.mkdtemp(prefix="streamkeep_restore_"))
            staged = {}
            for fname in BACKUP_FILES:
                if fname not in names:
                    continue
                dest = staging_dir / fname
                data = zf.read(fname)
                if fname == "config.json":
                    dest.write_bytes(_secret_free_config_bytes(data))
                elif fname in SQLITE_BACKUP_FILES:
                    dest.write_bytes(data)
                    _prepare_and_validate_sqlite(dest, fname)
                else:
                    dest.write_bytes(data)
                staged[fname] = dest
    except _RestoreError as e:
        _cleanup_staging(staging_dir)
        return False, f"Restore aborted: {redact_text(str(e))}"
    except (OSError, ValueError, zipfile.BadZipFile, sqlite3.Error) as e:
        _cleanup_staging(staging_dir)
        return False, f"Restore failed: {redact_text(str(e))}"

    # Every staged file validated — activate them. Each swap is atomic; a
    # marker makes the whole set crash-consistent (rolled back on next start).
    prepared = []
    marker = None
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        for fname, src in staged.items():
            current = CONFIG_DIR / fname
            tmp = current.with_suffix(current.suffix + ".restore-tmp")
            _write_atomic_tmp(tmp, src.read_bytes())
            prepared.append((current, tmp, fname))
        # config.json last: an interrupted restore then biases toward the old
        # config rather than a new config paired with a partially-swapped DB.
        prepared.sort(key=lambda item: item[2] == "config.json")
        marker = _write_restore_marker([fname for _c, _t, fname in prepared])
        for current, tmp, fname in prepared:
            _backup_existing_file(current)
            if fname in SQLITE_BACKUP_FILES:
                _remove_sqlite_sidecars(current)
            os.replace(tmp, current)
        _clear_restore_marker()
        marker = None
        _remove_pre_restore_copies(current for current, _t, _f in prepared)
        return True, f"Restored {len(prepared)} files from backup"
    except OSError as e:
        for _current, tmp, _fname in prepared:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass
        # Roll back any files already swapped so we don't leave a mixed dir.
        if marker is not None:
            finalize_interrupted_restore()
        return False, f"Restore failed during activation: {redact_text(str(e))}"
    finally:
        _cleanup_staging(staging_dir)


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
        src_db = sqlite_connect(
            f"file:{source_path}?mode=ro", uri=True, readonly=True,
            configure_journal=False,
        )
        dst_db = sqlite_connect(str(tmp_path), configure_journal=False)
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


def _write_restore_marker(fnames):
    """Durably record that a destructive restore activation is in progress."""
    marker = CONFIG_DIR / RESTORE_MARKER
    tmp = marker.with_suffix(".json.tmp")
    _write_atomic_tmp(tmp, json.dumps({"files": list(fnames)}).encode("utf-8"))
    os.replace(tmp, marker)
    return marker


def _clear_restore_marker():
    try:
        (CONFIG_DIR / RESTORE_MARKER).unlink(missing_ok=True)
    except OSError:
        pass


def _remove_pre_restore_copies(paths):
    """Delete the ``.pre-restore`` copies left by a completed restore."""
    for path in paths:
        path = Path(path)
        bak = path.with_suffix(path.suffix + ".pre-restore")
        try:
            bak.unlink(missing_ok=True)
        except OSError:
            pass


def finalize_interrupted_restore():
    """Roll back a restore that died mid-activation. Idempotent.

    Called once at startup before the database is opened. If the restore
    marker is present the previous restore was interrupted between the first
    and last atomic swap, leaving a mixed config directory. Every file named
    in the marker that still has a ``.pre-restore`` copy is reverted so the
    directory returns to its prior self-consistent state; leftover
    ``.restore-tmp`` files are removed. Returns ``True`` if a rollback ran.
    """
    marker = CONFIG_DIR / RESTORE_MARKER
    if not marker.is_file():
        return False
    try:
        fnames = json.loads(marker.read_text(encoding="utf-8")).get("files") or []
    except (OSError, ValueError):
        fnames = list(BACKUP_FILES)
    for fname in fnames:
        current = CONFIG_DIR / fname
        tmp = current.with_suffix(current.suffix + ".restore-tmp")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        bak = current.with_suffix(current.suffix + ".pre-restore")
        if bak.is_file():
            if fname in SQLITE_BACKUP_FILES:
                _remove_sqlite_sidecars(current)
            try:
                os.replace(str(bak), str(current))
            except OSError:
                pass
    _clear_restore_marker()
    return True


def _remove_sqlite_sidecars(path):
    """Remove SQLite WAL/SHM sidecars before restoring a database file."""
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass


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


def _validate_backup_meta(zf):
    """Reject a backup whose metadata is missing or unparseable."""
    try:
        meta = json.loads(zf.read("_backup_meta.json").decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        raise _RestoreError("backup metadata is unreadable") from e
    if not isinstance(meta, dict) or not str(meta.get("version") or "").strip():
        raise _RestoreError("backup metadata is missing a version")


def _prepare_and_validate_sqlite(path, fname):
    """Scrub then validate a staged SQLite file before it can be activated.

    Raises ``_RestoreError`` if the database is corrupt, has a newer schema than
    this build supports, or fails referential-integrity checks. On success the
    staged file is safe to swap into the live config directory.
    """
    # Scrub credential/auth state first (existing restore behavior).
    connection = sqlite_connect(str(path), configure_journal=False)
    try:
        _scrub_database_auth_state(connection)
    finally:
        connection.close()
    _remove_sqlite_sidecars(path)

    # Validate integrity under an untrusted-schema connection so a malicious
    # backup cannot execute embedded schema logic during the checks.
    connection = sqlite_connect(str(path), configure_journal=False)
    try:
        connection.execute("PRAGMA trusted_schema=OFF")
        _check_backup_schema_version(connection, fname)
        integrity = connection.execute("PRAGMA quick_check").fetchall()
        result = [str(r[0]) for r in integrity]
        if not (len(result) == 1 and result[0] == "ok"):
            raise _RestoreError(f"{fname} failed the integrity quick_check")
        fk_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        if fk_rows:
            raise _RestoreError(
                f"{fname} failed foreign_key_check "
                f"({len(fk_rows)} orphaned references)"
            )
    except sqlite3.DatabaseError as e:
        raise _RestoreError(f"{fname} is not a readable SQLite database") from e
    finally:
        connection.close()
    _remove_sqlite_sidecars(path)

    if fname == "search.db":
        _rebuild_search_fts(path)


def _check_backup_schema_version(connection, fname):
    """Reject databases whose schema is newer than this build understands."""
    if fname == "library.db":
        from .db import SCHEMA_VERSION
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise _RestoreError(
                f"library.db schema v{version} is newer than the supported "
                f"v{SCHEMA_VERSION}"
            )
    elif fname == "search.db":
        from .search import SCHEMA_VERSION as SEARCH_SCHEMA_VERSION
        has_meta = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_meta'"
        ).fetchone()
        version = 0
        if has_meta:
            row = connection.execute(
                "SELECT value FROM search_meta WHERE key='schema_version'"
            ).fetchone()
            try:
                version = int(row[0]) if row else 0
            except (TypeError, ValueError):
                version = 0
        if version > SEARCH_SCHEMA_VERSION:
            raise _RestoreError(
                f"search.db schema v{version} is newer than the supported "
                f"v{SEARCH_SCHEMA_VERSION}"
            )


def _rebuild_search_fts(path):
    """Rebuild the transcript FTS index so restored search state is consistent."""
    connection = sqlite_connect(str(path), configure_journal=False)
    try:
        has_fts = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='transcript_fts'"
        ).fetchone()
        if has_fts:
            connection.execute(
                "INSERT INTO transcript_fts(transcript_fts) VALUES('rebuild')"
            )
            connection.commit()
    except sqlite3.DatabaseError as e:
        raise _RestoreError("search.db FTS index could not be rebuilt") from e
    finally:
        connection.close()
    _remove_sqlite_sidecars(path)


def _write_atomic_tmp(tmp_path, data):
    """Write *data* to *tmp_path* durably (no rename); caller does os.replace."""
    with open(tmp_path, "wb") as f:
        f.write(data)
        try:
            f.flush()
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass


def _cleanup_staging(staging_dir):
    if not staging_dir:
        return
    try:
        shutil.rmtree(staging_dir, ignore_errors=True)
    except OSError:
        pass
