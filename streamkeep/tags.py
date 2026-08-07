"""Tag system — SQLite-backed many-to-many tags for recordings.

Tags are stored in ``%APPDATA%/StreamKeep/tags.db`` (separate from the
JSON config to avoid bloat).  Two categories: *system* tags (auto-generated
from metadata) and *user* tags (manually assigned).

Smart collections are JSON rule sets stored in ``config["collections"]``.
"""

import sqlite3
import json
from pathlib import Path

from .paths import CONFIG_DIR
from .sqlite_runtime import connect as sqlite_connect

DB_PATH = CONFIG_DIR / "tags.db"

# Duration bucket boundaries in seconds
_DURATION_BUCKETS = [
    (3600, "short (<1h)"),
    (7200, "medium (1-2h)"),
    (14400, "long (2-4h)"),
    (999999, "marathon (4h+)"),
]


def _connect():
    """Open (and initialize if needed) the tags database."""
    db = sqlite_connect(str(DB_PATH))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tags (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT NOT NULL,
            kind    TEXT NOT NULL DEFAULT 'user',
            UNIQUE(name, kind)
        );
        CREATE TABLE IF NOT EXISTS recording_tags (
            recording_path TEXT NOT NULL,
            tag_id         INTEGER NOT NULL REFERENCES tags(id),
            PRIMARY KEY (recording_path, tag_id)
        );
        CREATE INDEX IF NOT EXISTS idx_rt_path ON recording_tags(recording_path);
        CREATE INDEX IF NOT EXISTS idx_rt_tag  ON recording_tags(tag_id);
    """ + _COLLECTION_SCHEMA)
    return db


# ── User collections (V168) ─────────────────────────────────────────
#
# A recording used to have exactly one home: the season folder it was filed
# into. Something belonging to two playlists had to be duplicated on disk or
# arbitrarily assigned to one of them. Membership is many-to-many here and the
# on-disk copy stays single -- see ``media_server.plan_collection_homes``.
#
# Separate from smart collections (``evaluate_collection_rules``), which are a
# rule evaluated over history and own no rows. These are explicit and curated,
# so they need storage.
_COLLECTION_SCHEMA = """
    CREATE TABLE IF NOT EXISTS collections (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS collection_members (
        collection_id  INTEGER NOT NULL REFERENCES collections(id),
        recording_path TEXT NOT NULL,
        position       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (collection_id, recording_path)
    );
    CREATE INDEX IF NOT EXISTS idx_cm_path ON collection_members(recording_path);
    CREATE INDEX IF NOT EXISTS idx_cm_coll ON collection_members(collection_id);
"""


def get_or_create_tag(db, name, kind="user"):
    """Return the tag ID, creating the tag if it doesn't exist."""
    row = db.execute(
        "SELECT id FROM tags WHERE name=? AND kind=?", (name, kind)
    ).fetchone()
    if row:
        return row[0]
    cur = db.execute(
        "INSERT INTO tags (name, kind) VALUES (?, ?)", (name, kind)
    )
    db.commit()
    return cur.lastrowid


def _sync_sidecar_tags(db, path):
    """Persist recoverable tag state beside a recording when possible."""
    sidecar = Path(path) / "metadata.json"
    if not sidecar.is_file():
        return
    try:
        from .metadata import (
            _atomic_write_text,
            load_metadata_sidecar,
            normalize_metadata_payload,
        )
        payload = load_metadata_sidecar(sidecar)
        if not payload:
            return
        payload["tags"] = [
            {"name": name, "kind": kind}
            for name, kind in get_tags_for_recording(db, str(path))
        ]
        _atomic_write_text(
            sidecar,
            json.dumps(
                normalize_metadata_payload(payload),
                indent=2,
                ensure_ascii=False,
            ) + "\n",
        )
        manifest = Path(path) / ".streamkeep_manifest.json"
        if manifest.is_file():
            from .verify import create_archive_manifest
            create_archive_manifest(path, write_sidecar=True)
    except Exception:
        # The SQLite tag operation remains authoritative if a read-only or
        # incomplete recording folder cannot accept a public sidecar update.
        return


def tag_recording(db, path, tag_name, kind="user", *, _sync_sidecar=True):
    """Add a tag to a recording (by path)."""
    tag_id = get_or_create_tag(db, tag_name, kind)
    try:
        db.execute(
            "INSERT OR IGNORE INTO recording_tags (recording_path, tag_id) VALUES (?, ?)",
            (path, tag_id),
        )
        db.commit()
        if _sync_sidecar:
            _sync_sidecar_tags(db, path)
    except sqlite3.IntegrityError:
        pass


def untag_recording(db, path, tag_name, kind="user"):
    """Remove a tag from a recording."""
    row = db.execute(
        "SELECT id FROM tags WHERE name=? AND kind=?", (tag_name, kind)
    ).fetchone()
    if row:
        db.execute(
            "DELETE FROM recording_tags WHERE recording_path=? AND tag_id=?",
            (path, row[0]),
        )
        db.commit()
        _sync_sidecar_tags(db, path)


def get_tags_for_recording(db, path):
    """Return list of ``(tag_name, kind)`` for a recording."""
    rows = db.execute("""
        SELECT t.name, t.kind FROM tags t
        JOIN recording_tags rt ON rt.tag_id = t.id
        WHERE rt.recording_path = ?
        ORDER BY t.kind, t.name
    """, (path,)).fetchall()
    return [(r[0], r[1]) for r in rows]


def relocate_recording_tags(old_path, new_path):
    """Move one recording's tag references in one transaction.

    The maintenance coordinator performs this SQLite transaction as part of
    the per-recording filesystem/index unit. A pre-existing destination row is
    refused instead of silently merging two recordings' tag state.
    """
    old_path = str(old_path or "")
    new_path = str(new_path or "")
    if not old_path or not new_path:
        raise ValueError("recording paths are required")
    if old_path == new_path:
        return 0
    db = _connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        source_count = int(db.execute(
            "SELECT COUNT(*) FROM recording_tags WHERE recording_path=?",
            (old_path,),
        ).fetchone()[0] or 0)
        destination_count = int(db.execute(
            "SELECT COUNT(*) FROM recording_tags WHERE recording_path=?",
            (new_path,),
        ).fetchone()[0] or 0)
        if source_count and destination_count:
            raise ValueError("destination already has recording tags")
        cursor = db.execute(
            "UPDATE recording_tags SET recording_path=? WHERE recording_path=?",
            (new_path, old_path),
        )
        db.commit()
        return int(cursor.rowcount or 0)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_all_tags(db):
    """Return all tags as ``[(name, kind, count)]``."""
    rows = db.execute("""
        SELECT t.name, t.kind, COUNT(rt.recording_path) AS cnt
        FROM tags t LEFT JOIN recording_tags rt ON rt.tag_id = t.id
        GROUP BY t.id ORDER BY t.kind, t.name
    """).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def get_recordings_by_tag(db, tag_name):
    """Return list of recording paths that have the given tag."""
    rows = db.execute("""
        SELECT rt.recording_path FROM recording_tags rt
        JOIN tags t ON t.id = rt.tag_id
        WHERE t.name = ?
    """, (tag_name,)).fetchall()
    return [r[0] for r in rows]


def auto_tag_recording(db, path, info=None, vod_info=None):
    """Generate system tags for a recording based on metadata."""
    if not path:
        return

    # Platform tag
    platform = getattr(info, "platform", "") if info else ""
    if platform:
        tag_recording(
            db, path, f"platform:{platform}", kind="system",
            _sync_sidecar=False,
        )

    # Channel tag
    channel = ""
    if vod_info and getattr(vod_info, "channel", ""):
        channel = vod_info.channel
    elif info and getattr(info, "channel", ""):
        channel = info.channel
    if channel:
        tag_recording(
            db, path, f"channel:{channel}", kind="system",
            _sync_sidecar=False,
        )

    # Resolution tag
    if info:
        for q in (info.qualities or []):
            res = getattr(q, "resolution", "") or ""
            if "1080" in res:
                tag_recording(
                    db, path, "res:1080p", kind="system",
                    _sync_sidecar=False,
                )
                break
            elif "720" in res:
                tag_recording(
                    db, path, "res:720p", kind="system",
                    _sync_sidecar=False,
                )
                break
            elif "480" in res:
                tag_recording(
                    db, path, "res:480p", kind="system",
                    _sync_sidecar=False,
                )
                break

    # Duration bucket
    total_secs = getattr(info, "total_secs", 0) if info else 0
    if total_secs and total_secs > 0:
        for threshold, label in _DURATION_BUCKETS:
            if total_secs < threshold:
                tag_recording(
                    db, path, f"duration:{label}", kind="system",
                    _sync_sidecar=False,
                )
                break

    # Live tag
    if info and getattr(info, "is_live", False):
        tag_recording(
            db, path, "type:live", kind="system", _sync_sidecar=False,
        )
    else:
        tag_recording(
            db, path, "type:vod", kind="system", _sync_sidecar=False,
        )
    _sync_sidecar_tags(db, path)


def build_rebuilt_tags_database(target_path, records):
    """Build a replacement tag DB from sidecar tag rows.

    The live tag database is intentionally not opened here.  Rebuild apply
    stages this file beside the library database and activates both files only
    after they have been constructed successfully.
    """
    from pathlib import Path

    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    db = sqlite_connect(str(target))
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS tags (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                name    TEXT NOT NULL,
                kind    TEXT NOT NULL DEFAULT 'user',
                UNIQUE(name, kind)
            );
            CREATE TABLE IF NOT EXISTS recording_tags (
                recording_path TEXT NOT NULL,
                tag_id         INTEGER NOT NULL REFERENCES tags(id),
                PRIMARY KEY (recording_path, tag_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rt_path ON recording_tags(recording_path);
            CREATE INDEX IF NOT EXISTS idx_rt_tag  ON recording_tags(tag_id);
        """ + _COLLECTION_SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        tag_ids = {}
        for record in records or []:
            path = str((record or {}).get("path", "") or "")
            if not path:
                continue
            for row in (record or {}).get("tags", []) or []:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name", "") or "").strip()[:256]
                kind = str(row.get("kind", "user") or "user").strip().lower()
                if not name or kind not in {"system", "user"}:
                    continue
                key = (name, kind)
                tag_id = tag_ids.get(key)
                if tag_id is None:
                    cursor = db.execute(
                        "INSERT OR IGNORE INTO tags(name, kind) VALUES (?, ?)",
                        key,
                    )
                    if cursor.lastrowid:
                        tag_id = int(cursor.lastrowid)
                    else:
                        tag_id = int(db.execute(
                            "SELECT id FROM tags WHERE name=? AND kind=?",
                            key,
                        ).fetchone()[0])
                    tag_ids[key] = tag_id
                db.execute(
                    "INSERT OR IGNORE INTO recording_tags(recording_path, tag_id) "
                    "VALUES (?, ?)",
                    (path, tag_id),
                )
        db.commit()
        return {
            "tags": len(tag_ids),
            "recording_tags": int(
                db.execute("SELECT COUNT(*) FROM recording_tags").fetchone()[0]
            ),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── Explicit collection membership (V168) ───────────────────────────

def _collection_now():
    """UTC timestamp for a new collection.

    Local rather than reused from ``db.primitives``: this module has no
    dependency on the library-database package and adding one for a timestamp
    would risk a cycle for nothing.
    """
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_or_create_collection(db, name):
    """Return the collection id for *name*, creating it if needed."""
    label = str(name or "").strip()
    if not label:
        raise ValueError("collection name must not be empty")
    row = db.execute(
        "SELECT id FROM collections WHERE name=?", (label,)
    ).fetchone()
    if row:
        return row[0]
    cur = db.execute(
        "INSERT INTO collections (name, created_at) VALUES (?, ?)",
        (label, _collection_now()),
    )
    db.commit()
    return cur.lastrowid


def add_to_collection(db, path, name, *, position=None):
    """Add a recording to a collection. Idempotent.

    Adding does not remove the recording from any other collection: that is the
    whole point. ``position`` orders it within this collection only.
    """
    collection_id = get_or_create_collection(db, name)
    key = str(path)
    if position is None:
        row = db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM collection_members "
            "WHERE collection_id=?",
            (collection_id,),
        ).fetchone()
        position = int(row[0] if row else 0)
    db.execute(
        "INSERT OR REPLACE INTO collection_members "
        "(collection_id, recording_path, position) VALUES (?, ?, ?)",
        (collection_id, key, int(position)),
    )
    db.commit()
    return collection_id


def remove_from_collection(db, path, name):
    """Remove one membership, leaving every other membership intact."""
    row = db.execute(
        "SELECT id FROM collections WHERE name=?", (str(name or "").strip(),)
    ).fetchone()
    if not row:
        return False
    cur = db.execute(
        "DELETE FROM collection_members WHERE collection_id=? AND recording_path=?",
        (row[0], str(path)),
    )
    db.commit()
    return cur.rowcount > 0


def get_collections_for_recording(db, path):
    """Every collection this recording belongs to, in name order."""
    return [
        row[0] for row in db.execute(
            "SELECT c.name FROM collections c "
            "JOIN collection_members m ON m.collection_id = c.id "
            "WHERE m.recording_path=? ORDER BY c.name",
            (str(path),),
        ).fetchall()
    ]


def get_collection_members(db, name):
    """Recording paths in a collection, in the operator's chosen order."""
    return [
        row[0] for row in db.execute(
            "SELECT m.recording_path FROM collection_members m "
            "JOIN collections c ON c.id = m.collection_id "
            "WHERE c.name=? ORDER BY m.position, m.recording_path",
            (str(name or "").strip(),),
        ).fetchall()
    ]


def get_all_collections(db):
    """``[(name, member_count), ...]`` for every collection, name-ordered."""
    return [
        (row[0], int(row[1] or 0)) for row in db.execute(
            "SELECT c.name, COUNT(m.recording_path) FROM collections c "
            "LEFT JOIN collection_members m ON m.collection_id = c.id "
            "GROUP BY c.id ORDER BY c.name"
        ).fetchall()
    ]


def delete_collection(db, name):
    """Delete a collection and its memberships. Recordings are untouched."""
    row = db.execute(
        "SELECT id FROM collections WHERE name=?", (str(name or "").strip(),)
    ).fetchone()
    if not row:
        return False
    db.execute(
        "DELETE FROM collection_members WHERE collection_id=?", (row[0],)
    )
    db.execute("DELETE FROM collections WHERE id=?", (row[0],))
    db.commit()
    return True


def relocate_collection_memberships(old_path, new_path):
    """Follow a recording that moved on disk, keeping every membership.

    Membership is keyed by path, so a re-template or a manual move would
    otherwise silently drop the recording out of every collection it was in.
    """
    db = _connect()
    try:
        cur = db.execute(
            "UPDATE OR REPLACE collection_members SET recording_path=? "
            "WHERE recording_path=?",
            (str(new_path), str(old_path)),
        )
        db.commit()
        return cur.rowcount
    finally:
        db.close()


# ── Smart Collections ────────────────────────────────────────────────

def evaluate_collection(rule, history):
    """Evaluate a smart collection rule against history entries.

    A *rule* is a dict: ``{field, op, value}``.
    Supported fields: platform, channel, quality, title, watched, favorite.
    Supported ops: eq, ne, contains, gt, lt.

    Returns matching HistoryEntry objects.
    """
    results = []
    field = rule.get("field", "")
    op = rule.get("op", "eq")
    value = rule.get("value", "")

    for h in history:
        entry_val = str(getattr(h, field, "") or "").lower()
        cmp_val = str(value).lower()

        if op == "eq" and entry_val == cmp_val:
            results.append(h)
        elif op == "ne" and entry_val != cmp_val:
            results.append(h)
        elif op == "contains" and cmp_val in entry_val:
            results.append(h)
        elif op == "gt":
            try:
                if float(entry_val) > float(cmp_val):
                    results.append(h)
            except (ValueError, TypeError):
                pass
        elif op == "lt":
            try:
                if float(entry_val) < float(cmp_val):
                    results.append(h)
            except (ValueError, TypeError):
                pass
    return results


def evaluate_collection_rules(rules, history, logic="and"):
    """Evaluate multiple rules with AND/OR logic."""
    if not rules:
        return list(history)
    if logic == "or":
        seen = set()
        results = []
        for rule in rules:
            for h in evaluate_collection(rule, history):
                hid = id(h)
                if hid not in seen:
                    seen.add(hid)
                    results.append(h)
        return results
    else:  # AND
        result_set = None
        for rule in rules:
            matches = set(id(h) for h in evaluate_collection(rule, history))
            if result_set is None:
                result_set = matches
            else:
                result_set &= matches
        if result_set is None:
            return list(history)
        return [h for h in history if id(h) in result_set]
