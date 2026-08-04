"""Full-text transcript search — SQLite FTS5 index over generated
transcripts (.srt, .vtt, .transcript.json).

The index lives in ``%APPDATA%/StreamKeep/search.db``.  Each row stores
a recording path, text segment, and start/end timestamps in seconds.
"""

import html
import json
import os
import re
import sqlite3
import threading

from .paths import CONFIG_DIR
from .sqlite_runtime import connect as sqlite_connect
from .sqlite_runtime import runtime_status as sqlite_runtime_status

# WebVTT timestamp: hours are optional (MM:SS.mmm and HH:MM:SS.mmm are both
# valid per the W3C WebVTT spec); minutes/seconds are two digits, millis three.
_VTT_TS = r"(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})"
_VTT_CUE_RE = re.compile(_VTT_TS + r"\s*-->\s*" + _VTT_TS)
_VTT_TAG_RE = re.compile(r"<[^>]+>")


def _vtt_ts_to_secs(hours, minutes, seconds, millis):
    return (
        (int(hours) if hours else 0) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis) / 1000
    )


def _strip_vtt_markup(text):
    """Drop WebVTT cue tags (voice/class/italic/timestamp spans) and unescape
    entities so indexed text is plain and searchable."""
    text = _VTT_TAG_RE.sub("", text)
    return html.unescape(text).strip()

DB_PATH = CONFIG_DIR / "search.db"
SCHEMA_VERSION = 3
_SCHEMA_LOCK = threading.Lock()


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite_connect(str(DB_PATH))
    with _SCHEMA_LOCK:
        _ensure_schema(db)
    return db


def _ensure_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS search_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transcript_segments (
            rowid          INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_path TEXT NOT NULL,
            text           TEXT NOT NULL,
            start_sec      REAL NOT NULL DEFAULT 0,
            end_sec        REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_ts_path ON transcript_segments(recording_path);
        CREATE TABLE IF NOT EXISTS comment_entries (
            rowid          INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_path TEXT NOT NULL,
            comment_id     TEXT NOT NULL DEFAULT '',
            parent_id      TEXT NOT NULL DEFAULT '',
            author         TEXT NOT NULL DEFAULT '',
            text           TEXT NOT NULL,
            published_at   TEXT NOT NULL DEFAULT '',
            like_count     INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_comments_path ON comment_entries(recording_path);
    """)
    fts5_fixed = sqlite_runtime_status().get("fts5_fixed", True)
    existing = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='transcript_fts'"
    ).fetchone()
    trigger_names = {
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name IN ('transcript_segments_ai', 'transcript_segments_ad', "
            "'transcript_segments_au')"
        ).fetchall()
    }
    if fts5_fixed:
        db.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
                recording_path, text, start_sec, end_sec,
                content='transcript_segments',
                content_rowid='rowid'
            );
            CREATE TRIGGER IF NOT EXISTS transcript_segments_ai AFTER INSERT ON transcript_segments BEGIN
                INSERT INTO transcript_fts(rowid, recording_path, text, start_sec, end_sec)
                VALUES (new.rowid, new.recording_path, new.text, new.start_sec, new.end_sec);
            END;
            CREATE TRIGGER IF NOT EXISTS transcript_segments_ad AFTER DELETE ON transcript_segments BEGIN
                INSERT INTO transcript_fts(transcript_fts, rowid, recording_path, text, start_sec, end_sec)
                VALUES ('delete', old.rowid, old.recording_path, old.text, old.start_sec, old.end_sec);
            END;
            CREATE TRIGGER IF NOT EXISTS transcript_segments_au AFTER UPDATE ON transcript_segments BEGIN
                INSERT INTO transcript_fts(transcript_fts, rowid, recording_path, text, start_sec, end_sec)
                VALUES ('delete', old.rowid, old.recording_path, old.text, old.start_sec, old.end_sec);
                INSERT INTO transcript_fts(rowid, recording_path, text, start_sec, end_sec)
            VALUES (new.rowid, new.recording_path, new.text, new.start_sec, new.end_sec);
            END;
        """)
        if existing is None or len(trigger_names) != 3:
            db.execute("INSERT INTO transcript_fts(transcript_fts) VALUES('rebuild')")
    else:
        db.executescript("""
            DROP TRIGGER IF EXISTS transcript_segments_ai;
            DROP TRIGGER IF EXISTS transcript_segments_ad;
            DROP TRIGGER IF EXISTS transcript_segments_au;
        """)
    comment_existing = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='comment_fts'"
    ).fetchone()
    comment_trigger_names = {
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name IN ('comment_entries_ai', 'comment_entries_ad', "
            "'comment_entries_au')"
        ).fetchall()
    }
    if fts5_fixed:
        db.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS comment_fts USING fts5(
                recording_path, author, text, published_at,
                content='comment_entries',
                content_rowid='rowid'
            );
            CREATE TRIGGER IF NOT EXISTS comment_entries_ai AFTER INSERT ON comment_entries BEGIN
                INSERT INTO comment_fts(rowid, recording_path, author, text, published_at)
                VALUES (new.rowid, new.recording_path, new.author, new.text, new.published_at);
            END;
            CREATE TRIGGER IF NOT EXISTS comment_entries_ad AFTER DELETE ON comment_entries BEGIN
                INSERT INTO comment_fts(comment_fts, rowid, recording_path, author, text, published_at)
                VALUES ('delete', old.rowid, old.recording_path, old.author, old.text, old.published_at);
            END;
            CREATE TRIGGER IF NOT EXISTS comment_entries_au AFTER UPDATE ON comment_entries BEGIN
                INSERT INTO comment_fts(comment_fts, rowid, recording_path, author, text, published_at)
                VALUES ('delete', old.rowid, old.recording_path, old.author, old.text, old.published_at);
                INSERT INTO comment_fts(rowid, recording_path, author, text, published_at)
                VALUES (new.rowid, new.recording_path, new.author, new.text, new.published_at);
            END;
        """)
        if comment_existing is None or len(comment_trigger_names) != 3:
            db.execute("INSERT INTO comment_fts(comment_fts) VALUES('rebuild')")
    else:
        db.executescript("""
            DROP TRIGGER IF EXISTS comment_entries_ai;
            DROP TRIGGER IF EXISTS comment_entries_ad;
            DROP TRIGGER IF EXISTS comment_entries_au;
        """)
    row = db.execute(
        "SELECT value FROM search_meta WHERE key = 'schema_version'"
    ).fetchone()
    try:
        current_version = int(row[0]) if row else 0
    except (TypeError, ValueError):
        current_version = 0
    if current_version < SCHEMA_VERSION:
        db.execute(
            "INSERT OR REPLACE INTO search_meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        db.commit()


def _parse_srt(path):
    """Parse an .srt file into segments: [(start_sec, end_sec, text)]."""
    segments = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return segments

    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        ts_line = lines[1]
        m = re.match(
            r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
            r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
            ts_line,
        )
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        text = " ".join(lines[2:]).strip()
        if text:
            segments.append((start, end, text))
    return segments


def _parse_vtt(path):
    """Parse a .vtt file into segments.

    WebVTT-spec-correct: accepts both ``MM:SS.mmm`` and ``HH:MM:SS.mmm`` cue
    timestamps, ignores cue identifiers and trailing cue settings, strips
    inline markup, and isolates malformed cues (a bad block is skipped rather
    than aborting the file).
    """
    segments = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return segments

    content = content.replace("﻿", "")
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue
        header = lines[0].strip().upper()
        # Skip the file signature and non-cue blocks.
        if header.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        cue_idx = None
        cue_match = None
        for i, line in enumerate(lines):
            match = _VTT_CUE_RE.match(line.strip())
            if match:
                cue_idx, cue_match = i, match
                break
        if cue_match is None:
            continue  # malformed / setting-only block — isolate and skip
        g = cue_match.groups()
        start = _vtt_ts_to_secs(g[0], g[1], g[2], g[3])
        end = _vtt_ts_to_secs(g[4], g[5], g[6], g[7])
        text = _strip_vtt_markup(" ".join(lines[cue_idx + 1:]))
        if text:
            segments.append((start, end, text))
    return segments


def _parse_transcript_json(path):
    """Parse a .transcript.json file."""
    segments = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return segments

    items = data if isinstance(data, list) else data.get("segments", [])
    if not isinstance(items, list):
        return segments
    for item in items:
        if not isinstance(item, dict):
            continue
        # Accept both the internal shape ({text, start, end}) and the Podcast
        # Namespace JSON transcript shape ({body, startTime, endTime, speaker}).
        text = str(item.get("text") or item.get("body") or "").strip()
        speaker = str(item.get("speaker") or "").strip()
        if speaker and text:
            text = f"{speaker}: {text}"
        raw_start = item.get("start", item.get("startTime", 0))
        raw_end = item.get("end", item.get("endTime"))
        try:
            start = float(raw_start or 0)
            end = float(raw_end if raw_end is not None else start + 1)
        except (TypeError, ValueError):
            continue
        if text:
            segments.append((start, end, text))
    return segments


def _parse_comments_json(path):
    """Parse a versioned ``*.comments.json`` sidecar."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if (
        not isinstance(data, dict)
        or data.get("schema") != "streamkeep.comments"
        or data.get("schema_version") != 1
    ):
        return []
    rows = data.get("comments", [])
    if not isinstance(rows, list):
        return []
    comments = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue
        try:
            like_count = max(0, int(item.get("like_count", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            like_count = 0
        comments.append({
            "comment_id": str(item.get("id", "") or "")[:256],
            "parent_id": str(item.get("parent_id", "") or "")[:256],
            "author": str(item.get("author", "") or "")[:256],
            "text": text[:8192],
            "published_at": str(item.get("published_at", "") or "")[:64],
            "like_count": like_count,
        })
    return comments


def index_recording(recording_path):
    """Index all transcript files found in a recording directory.

    Runs in the calling thread. Safe to call multiple times — removes
    old entries before re-indexing.
    """
    if not recording_path:
        return 0

    all_segments = []
    all_comments = []
    db = _connect()
    try:
        db.execute(
            "DELETE FROM transcript_segments WHERE recording_path = ?",
            (recording_path,),
        )
        db.execute(
            "DELETE FROM comment_entries WHERE recording_path = ?",
            (recording_path,),
        )
        if os.path.isdir(recording_path):
            for fname in os.listdir(recording_path):
                fpath = os.path.join(recording_path, fname)
                fl = fname.lower()
                if fl.endswith(".srt"):
                    all_segments.extend(_parse_srt(fpath))
                elif fl.endswith(".vtt"):
                    all_segments.extend(_parse_vtt(fpath))
                elif fl.endswith(".transcript.json"):
                    all_segments.extend(_parse_transcript_json(fpath))
                elif fl.endswith(".comments.json"):
                    all_comments.extend(_parse_comments_json(fpath))
            if all_segments:
                db.executemany(
                    "INSERT INTO transcript_segments (recording_path, text, start_sec, end_sec) "
                    "VALUES (?, ?, ?, ?)",
                    [
                        (recording_path, text, start, end)
                        for start, end, text in all_segments
                    ],
                )
            if all_comments:
                db.executemany(
                    "INSERT INTO comment_entries "
                    "(recording_path, comment_id, parent_id, author, text, "
                    "published_at, like_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            recording_path,
                            row["comment_id"], row["parent_id"],
                            row["author"], row["text"],
                            row["published_at"], row["like_count"],
                        )
                        for row in all_comments
                    ],
                )
        db.commit()
    finally:
        db.close()
    return len(all_segments)


def _quote_fts5_term(term):
    return '"' + str(term).replace('"', '""') + '"'


def _fts5_literal_query(query):
    """Quote terms and restrict FTS5 matching to transcript text."""
    terms = [
        _quote_fts5_term(term)
        for term in str(query or "").strip().split()
    ]
    return f"text : ({' '.join(terms)})" if terms else ""


def _fts5_any_literal_query(query):
    """Quote a query so FTS5 searches every indexed comment column."""
    terms = [
        _quote_fts5_term(term)
        for term in str(query or "").strip().split()
    ]
    return " AND ".join(terms)


def _like_transcript_filter(query):
    terms = [term for term in str(query or "").strip().split() if term]
    clauses = []
    params = []
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("text LIKE ? ESCAPE '\\' COLLATE NOCASE")
        params.append(f"%{escaped}%")
    return " AND ".join(clauses), params


def search_transcripts(query, limit=100):
    """Search indexed transcripts. Returns list of dicts:
    ``[{recording_path, text, start_sec, end_sec}]``
    """
    query = str(query or "").strip()
    if not query:
        return []
    fts_query = _fts5_literal_query(query)
    if not fts_query:
        return []
    try:
        limit = max(1, int(limit or 100))
    except (TypeError, ValueError):
        limit = 100
    db = _connect()
    try:
        if sqlite_runtime_status().get("fts5_fixed", True):
            rows = db.execute(
                "SELECT s.recording_path, s.text, s.start_sec, s.end_sec "
                "FROM transcript_fts f "
                "JOIN transcript_segments s ON s.rowid = f.rowid "
                "WHERE transcript_fts MATCH ? "
                "ORDER BY bm25(transcript_fts), s.rowid "
                "LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        else:
            like_filter, like_params = _like_transcript_filter(query)
            rows = db.execute(
                "SELECT recording_path, text, start_sec, end_sec "
                "FROM transcript_segments "
                f"WHERE {like_filter} ORDER BY rowid LIMIT ?",
                (*like_params, limit),
            ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        db.close()
    return [
        {"recording_path": r[0], "text": r[1], "start_sec": r[2], "end_sec": r[3]}
        for r in rows
    ]


def _like_comment_filter(query):
    terms = [term for term in str(query or "").strip().split() if term]
    clauses = []
    params = []
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(
            "(author LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "OR text LIKE ? ESCAPE '\\' COLLATE NOCASE)"
        )
        params.extend([f"%{escaped}%", f"%{escaped}%"])
    return " AND ".join(clauses), params


def search_comments(query, limit=100):
    """Search indexed platform comments by author or published text."""
    query = str(query or "").strip()
    if not query:
        return []
    fts_query = _fts5_any_literal_query(query)
    if not fts_query:
        return []
    try:
        limit = max(1, int(limit or 100))
    except (TypeError, ValueError):
        limit = 100
    db = _connect()
    try:
        if sqlite_runtime_status().get("fts5_fixed", True):
            rows = db.execute(
                "SELECT c.recording_path, c.comment_id, c.parent_id, c.author, "
                "c.text, c.published_at, c.like_count "
                "FROM comment_fts f "
                "JOIN comment_entries c ON c.rowid = f.rowid "
                "WHERE comment_fts MATCH ? "
                "ORDER BY bm25(comment_fts), c.rowid LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        else:
            like_filter, like_params = _like_comment_filter(query)
            rows = db.execute(
                "SELECT recording_path, comment_id, parent_id, author, text, "
                "published_at, like_count FROM comment_entries "
                f"WHERE {like_filter} ORDER BY rowid LIMIT ?",
                (*like_params, limit),
            ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        db.close()
    return [
        {
            "recording_path": row[0],
            "comment_id": row[1],
            "parent_id": row[2],
            "author": row[3],
            "text": row[4],
            "published_at": row[5],
            "like_count": row[6],
            "start_sec": 0.0,
            "end_sec": 0.0,
        }
        for row in rows
    ]


def index_all_async(history, log_fn=None):
    """Index all recordings in history in a background thread."""
    def _run():
        if history is None:
            from . import db as library_db
            from .models import HistoryEntry
            entries = (
                HistoryEntry.from_dict(row)
                for row in library_db.iter_history(page_size=250)
            )
            history_count = library_db.history_count()
        else:
            entries = history
            history_count = len(history)
        total = 0
        for h in entries:
            path = getattr(h, "path", "") or ""
            if path and os.path.isdir(path):
                n = index_recording(path)
                total += n
        if log_fn:
            log_fn(
                f"[SEARCH] Indexed {total} transcript segments across "
                f"{history_count} recordings."
            )
    threading.Thread(target=_run, daemon=True).start()
