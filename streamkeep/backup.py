"""Backup & Restore Wizard — export/import StreamKeep config + data (F72).

Creates a ``.skbackup`` file (ZIP) containing:
  - config.json (user preferences)
  - library.db (history, monitor, queue)
  - tags.db (tag associations)
  - notifications.jsonl (notification history)
  - cookies.txt (if present)

Restore extracts and replaces the current config directory contents.
Scheduled auto-backup via ``schedule_backup()`` with rotation.
"""

import os
import shutil
import zipfile
from datetime import datetime

from .paths import CONFIG_DIR

# Files to include in backup (relative to CONFIG_DIR)
BACKUP_FILES = [
    "config.json",
    "library.db",
    "tags.db",
    "search.db",
    "notifications.jsonl",
    "cookies.txt",
]


def create_backup(output_path, *, include_logs=False):
    """Create a .skbackup file at *output_path*.

    Returns ``(ok, message)``.
    """
    if not output_path:
        return False, "No output path specified"

    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            # Add metadata
            zf.writestr("_backup_meta.json", _meta_json())

            count = 0
            for fname in BACKUP_FILES:
                fpath = CONFIG_DIR / fname
                if fpath.is_file():
                    zf.write(str(fpath), fname)
                    count += 1

            # Optionally include logs
            if include_logs:
                for fname in ("streamkeep.log", "streamkeep.log.1", "crash.log"):
                    fpath = CONFIG_DIR / fname
                    if fpath.is_file():
                        zf.write(str(fpath), f"logs/{fname}")

        size_kb = os.path.getsize(output_path) / 1024
        return True, f"Backup created: {count} files, {size_kb:.0f} KB"
    except (OSError, zipfile.BadZipFile) as e:
        return False, f"Backup failed: {e}"


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
                    # Back up the current file before overwriting
                    current = CONFIG_DIR / fname
                    if current.is_file():
                        bak = current.with_suffix(current.suffix + ".pre-restore")
                        try:
                            shutil.copy2(str(current), str(bak))
                        except OSError:
                            pass
                    zf.extract(fname, str(CONFIG_DIR))
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
    import json
    from . import VERSION
    return json.dumps({
        "version": VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "config_dir": str(CONFIG_DIR),
    }, indent=2)
