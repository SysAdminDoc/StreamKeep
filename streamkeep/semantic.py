"""Bounded local semantic-moment indexing.

The semantic index is deliberately separate from the exact transcript/comment
FTS database. It stores only local feature vectors derived from sidecars that
already exist beside a recording; no network, cloud model, or profile lookup
is involved. When the optional local ``all-MiniLM-L6-v2`` runtime is available,
vectors are sentence embeddings so paraphrases can match without shared
words. A deterministic hashed representation remains the explicit degraded
fallback for source checkouts that do not have the optional model runtime.

``semantic.db`` is a rebuildable cache, not the source of truth. The vector
table is intentionally brute-force: the bounded per-recording limits keep the
desktop corpus small enough that a pre-1.0 vector extension is unnecessary.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import struct
import threading
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from .paths import CONFIG_DIR
from .sqlite_runtime import connect as sqlite_connect

_LOGGER = logging.getLogger(__name__)

DB_FILENAME = "semantic.db"
DB_PATH = CONFIG_DIR / DB_FILENAME
SCHEMA = "streamkeep.semantic"
SCHEMA_VERSION = 2
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
VECTOR_VERSION = "minilm-l6-v2"
VECTOR_DIMENSIONS = 384
FALLBACK_VECTOR_VERSION = "hashed-local-v1"
FALLBACK_VECTOR_DIMENSIONS = 96
MODEL_MAX_TOKENS = 256
MODEL_BATCH_SIZE = 32
DEFAULT_MAX_MOMENTS = 10_000
MAX_MAX_MOMENTS = 100_000
DEFAULT_MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_MAX_INDEX_BYTES = 64 * 1024 * 1024
MAX_SIDECAR_BYTES = 32 * 1024 * 1024
MAX_TEXT_CHARS = 4096
MAX_PROVENANCE_CHARS = 512
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SCHEMA_LOCK = threading.Lock()
_MODEL_LOCK = threading.Lock()
_MODEL_STATE = {
    "attempted": False,
    "available": False,
    "tokenizer": None,
    "model": None,
    "torch": None,
    "path": "",
    "error": "",
}


def _bounded_int(value, default, maximum):
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        value = int(default)
    return max(1, min(int(maximum), value))


def _safe_float(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _safe_text(value, limit=MAX_TEXT_CHARS):
    return str(value or "").replace("\x00", " ").strip()[:limit]


def _model_candidates():
    """Return local-only MiniLM locations in preference order.

    The release bundle may place the model beside the application, while
    source checkouts commonly use the Hugging Face cache. No downloader is
    called here: a missing model is a supported, clearly reported fallback.
    ``STREAMKEEP_SEMANTIC_MODEL_PATH`` is useful for portable installs and
    for operators who keep model assets outside the application directory.
    """
    explicit = os.environ.get("STREAMKEEP_SEMANTIC_MODEL_PATH", "").strip()
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    package_root = Path(__file__).resolve().parent.parent
    candidates.extend((
        package_root / "assets" / "models" / "all-MiniLM-L6-v2",
        CONFIG_DIR / "models" / "all-MiniLM-L6-v2",
    ))
    hf_home_value = os.environ.get("HF_HOME", "").strip()
    hf_home = (
        Path(hf_home_value).expanduser()
        if hf_home_value
        else Path.home() / ".cache" / "huggingface"
    )
    snapshot_root = (
        hf_home / "hub" / "models--sentence-transformers--all-MiniLM-L6-v2"
        / "snapshots"
    )
    try:
        candidates.extend(sorted(snapshot_root.iterdir()))
    except OSError:
        pass
    return tuple(path for path in candidates if path.is_dir())


def _resolve_model_path():
    for path in _model_candidates():
        # ``from_pretrained`` gives the final validation error, but this
        # cheap check avoids importing torch for arbitrary empty folders.
        if (path / "config.json").is_file() and any(
            (path / name).is_file()
            for name in ("model.safetensors", "pytorch_model.bin")
        ):
            return path
    return None


def reset_model_backend():
    """Forget a previous model probe (used after changing a model path)."""
    with _MODEL_LOCK:
        _MODEL_STATE.update({
            "attempted": False,
            "available": False,
            "tokenizer": None,
            "model": None,
            "torch": None,
            "path": "",
            "error": "",
        })


def _load_model_backend():
    """Load MiniLM once, never downloading or prompting for credentials."""
    if _MODEL_STATE["attempted"]:
        return _MODEL_STATE
    with _MODEL_LOCK:
        if _MODEL_STATE["attempted"]:
            return _MODEL_STATE
        _MODEL_STATE["attempted"] = True
        if os.environ.get("STREAMKEEP_SEMANTIC_DISABLE_MODEL", "").strip().lower() in {
            "1", "true", "yes", "on",
        }:
            _MODEL_STATE["error"] = (
                "MiniLM disabled by STREAMKEEP_SEMANTIC_DISABLE_MODEL; "
                "using hashed fallback"
            )
            return _MODEL_STATE
        path = _resolve_model_path()
        if path is None:
            _MODEL_STATE["error"] = (
                "all-MiniLM-L6-v2 is not installed locally; "
                "using hashed fallback"
            )
            return _MODEL_STATE
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
            from transformers.utils import logging as transformers_logging

            transformers_logging.set_verbosity_error()
            transformers_logging.disable_progress_bar()
            try:
                # Keep the optional model from monopolising a desktop that is
                # also running downloads, Qt, and the rest of the indexers.
                torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
                torch.set_num_interop_threads(1)
            except RuntimeError as error:
                _LOGGER.debug("[SEMANTIC] Could not tune torch threads: %s", error)
            tokenizer = AutoTokenizer.from_pretrained(
                str(path), local_files_only=True,
            )
            model = AutoModel.from_pretrained(
                str(path), local_files_only=True,
            )
            model.eval()
            _MODEL_STATE.update({
                "available": True,
                "tokenizer": tokenizer,
                "model": model,
                "torch": torch,
                "path": str(path),
                "error": "",
            })
        except Exception as error:  # optional dependency/model is best effort
            _MODEL_STATE["error"] = (
                f"MiniLM unavailable ({type(error).__name__}); using hashed fallback"
            )
            _LOGGER.info("[SEMANTIC] %s", _MODEL_STATE["error"])
    return _MODEL_STATE


def backend_status():
    """Return a user-safe snapshot of the active embedding backend."""
    state = _load_model_backend()
    if state["available"]:
        return {
            "available": True,
            "backend": "MiniLM",
            "model": MODEL_ID,
            "vector_version": VECTOR_VERSION,
            "dimensions": VECTOR_DIMENSIONS,
            "path": state["path"],
            "detail": "Local sentence embeddings are active.",
        }
    return {
        "available": False,
        "backend": "hashed fallback",
        "model": MODEL_ID,
        "vector_version": FALLBACK_VECTOR_VERSION,
        "dimensions": FALLBACK_VECTOR_DIMENSIONS,
        "path": "",
        "detail": state["error"] or "Optional MiniLM runtime is unavailable.",
    }


def _pack_vector(vector):
    return struct.pack("<" + ("f" * len(vector)), *vector)


def _unpack_vector(payload):
    if not isinstance(payload, (bytes, bytearray)):
        return None
    for dimensions in (VECTOR_DIMENSIONS, FALLBACK_VECTOR_DIMENSIONS):
        if len(payload) != dimensions * 4:
            continue
        try:
            return struct.unpack("<" + ("f" * dimensions), payload)
        except struct.error:
            return None
    return None


def _hashed_embedding(text):
    """Return the bounded deterministic fallback vector for *text*."""
    normalized = " ".join(_TOKEN_RE.findall(str(text or "").lower()))
    tokens = normalized.split()
    features = [(token, 1.0) for token in tokens]
    features.extend(
        (normalized[index:index + 3], 0.35)
        for index in range(max(0, len(normalized) - 2))
    )
    vector = [0.0] * FALLBACK_VECTOR_DIMENSIONS
    for feature, weight in features:
        if not feature:
            continue
        digest = hashlib.blake2b(
            feature.encode("utf-8", "ignore"), digest_size=8,
        ).digest()
        bucket = int.from_bytes(digest[:4], "little") % FALLBACK_VECTOR_DIMENSIONS
        vector[bucket] += weight if digest[4] & 1 else -weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return tuple(0.0 for _ in vector)
    return tuple(value / norm for value in vector)


def _model_embeddings(texts, *, cancel_check=None):
    """Embed *texts* in bounded batches with mean pooling and L2 norm."""
    state = _load_model_backend()
    if not state["available"]:
        return None
    torch = state["torch"]
    tokenizer = state["tokenizer"]
    model = state["model"]
    vectors = []
    try:
        for offset in range(0, len(texts), MODEL_BATCH_SIZE):
            if cancel_check and cancel_check():
                return None
            batch_texts = [str(text or "") for text in texts[offset:offset + MODEL_BATCH_SIZE]]
            batch = tokenizer(
                batch_texts,
                padding=True,
                truncation=True,
                max_length=MODEL_MAX_TOKENS,
                return_tensors="pt",
            )
            with torch.inference_mode():
                output = model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1).expand(output.size()).float()
                pooled = (output * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
            vectors.extend(
                tuple(float(value) for value in row.tolist())
                for row in normalized
            )
    except Exception as error:  # an optional backend must never break indexing
        with _MODEL_LOCK:
            _MODEL_STATE["available"] = False
            _MODEL_STATE["error"] = (
                f"MiniLM inference failed ({type(error).__name__}); "
                "using hashed fallback"
            )
        _LOGGER.info("[SEMANTIC] %s", _MODEL_STATE["error"])
        return None
    return vectors


def local_embeddings(texts, *, cancel_check=None):
    """Return normalized local embeddings for a sequence of texts.

    MiniLM is preferred when it is installed locally. The fallback is
    deterministic and intentionally remains available so indexing can never
    make the desktop unusable merely because an optional model is absent.
    """
    values = [str(text or "") for text in texts]
    if not values:
        return []
    model_vectors = _model_embeddings(values, cancel_check=cancel_check)
    if model_vectors is not None:
        return model_vectors
    if cancel_check and cancel_check():
        return None
    return [_hashed_embedding(value) for value in values]


def local_embedding(text):
    """Return one normalized local embedding for *text*."""
    vectors = local_embeddings([text])
    return vectors[0] if vectors else tuple()


def _cosine(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite_connect(str(DB_PATH))
    with _SCHEMA_LOCK:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS semantic_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS semantic_moments (
                rowid          INTEGER PRIMARY KEY AUTOINCREMENT,
                recording_path TEXT NOT NULL,
                start_sec      REAL NOT NULL DEFAULT 0,
                end_sec        REAL NOT NULL DEFAULT 0,
                modality       TEXT NOT NULL,
                provenance     TEXT NOT NULL,
                text           TEXT NOT NULL,
                confidence     REAL NOT NULL DEFAULT 0,
                vector         BLOB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_semantic_path
                ON semantic_moments(recording_path);
            CREATE INDEX IF NOT EXISTS idx_semantic_modality
                ON semantic_moments(modality);
            """
        )
        previous = {
            str(row[0]): str(row[1])
            for row in db.execute(
                "SELECT key, value FROM semantic_meta"
            ).fetchall()
        }
        state = _load_model_backend()
        active_version = (
            VECTOR_VERSION if state["available"] else FALLBACK_VECTOR_VERSION
        )
        if previous and (
            previous.get("schema_version") != str(SCHEMA_VERSION)
            or previous.get("vector_version") != active_version
        ):
            # This table is a cache. Dropping incompatible rows is safer than
            # mixing 96-dimensional hashed vectors with 384-dimensional model
            # vectors, and the next rebuild repopulates it transactionally.
            db.execute("DELETE FROM semantic_moments")
        db.executemany(
            "INSERT OR REPLACE INTO semantic_meta(key, value) VALUES (?, ?)",
            (
                ("schema", SCHEMA),
                ("schema_version", str(SCHEMA_VERSION)),
                ("vector_version", active_version),
                ("vector_dimensions", str(
                    VECTOR_DIMENSIONS if state["available"]
                    else FALLBACK_VECTOR_DIMENSIONS
                )),
                ("model_id", MODEL_ID),
            ),
        )
        db.commit()
    return db


def is_enabled(config=None):
    """Return whether the user opted into local semantic indexing."""
    if config is None:
        try:
            from .config import load_config

            config = load_config()
        except Exception:
            config = {}
    return bool((config or {}).get("semantic_search_enabled", False))


def _read_json(path):
    try:
        if os.path.getsize(path) > MAX_SIDECAR_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _candidate(
    *, recording_path, start=0.0, end=0.0, modality, provenance, text,
    confidence,
):
    text = _safe_text(text)
    if not text:
        return None
    start = max(0.0, _safe_float(start))
    end = max(start, _safe_float(end, start))
    return {
        "recording_path": recording_path,
        "start_sec": start,
        "end_sec": end,
        "modality": _safe_text(modality, 32),
        "provenance": _safe_text(provenance, MAX_PROVENANCE_CHARS),
        "text": text,
        "confidence": max(0.0, min(1.0, _safe_float(confidence))),
    }


def _transcript_candidates(recording_path, names):
    from . import search

    for name in names:
        path = os.path.join(recording_path, name)
        lower = name.lower()
        try:
            if os.path.getsize(path) > MAX_SIDECAR_BYTES:
                continue
        except OSError:
            continue
        if lower.endswith(".srt"):
            segments = search._parse_srt(path)
        elif lower.endswith(".vtt"):
            segments = search._parse_vtt(path)
        elif lower.endswith(".transcript.json"):
            segments = search._parse_transcript_json(path)
        else:
            continue
        for start, end, text in segments:
            result = _candidate(
                recording_path=recording_path,
                start=start,
                end=end,
                modality="transcript",
                provenance=f"transcript:{name}",
                text=text,
                confidence=0.95,
            )
            if result:
                yield result


def _sidecar_items(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("segments", "items", "entries", "words", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    if data.get("text") or data.get("body"):
        return [data]
    return []


def _ocr_candidates(recording_path, names):
    for name in names:
        lower = name.lower()
        path = os.path.join(recording_path, name)
        if lower.endswith(".ocr.txt"):
            try:
                if os.path.getsize(path) > MAX_SIDECAR_BYTES:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    lines = handle.read().splitlines()
            except OSError:
                continue
            for line in lines:
                result = _candidate(
                    recording_path=recording_path,
                    modality="ocr",
                    provenance=f"ocr:{name}",
                    text=line,
                    confidence=0.65,
                )
                if result:
                    yield result
            continue
        if not lower.endswith(".ocr.json"):
            continue
        data = _read_json(path)
        for item in _sidecar_items(data):
            if not isinstance(item, dict):
                continue
            text = item.get("text", item.get("body", ""))
            start = item.get("start", item.get("start_time", item.get("time", 0)))
            end = item.get("end", item.get("end_time", start))
            result = _candidate(
                recording_path=recording_path,
                start=start,
                end=end,
                modality="ocr",
                provenance=f"ocr:{name}",
                text=text,
                confidence=item.get("confidence", 0.65),
            )
            if result:
                yield result


def _scene_candidates(recording_path):
    for relative in (
        os.path.join(".streamkeep_scenes", "scenes.json"),
        ".storyboard.json",
    ):
        path = os.path.join(recording_path, relative)
        data = _read_json(path)
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            start = item.get("time", item.get("start", 0))
            end = item.get("end", _safe_float(start) + 1.0)
            text = (
                item.get("text") or item.get("label") or item.get("title")
                or item.get("caption")
                or "scene change visual frame"
            )
            result = _candidate(
                recording_path=recording_path,
                start=start,
                end=end,
                modality="scene",
                provenance=f"scene:{relative}",
                text=text,
                confidence=item.get("confidence", 0.55),
            )
            if result:
                yield result
        break


def _audio_candidates(recording_path):
    try:
        from .intelligence.highlight import _load_audio_peaks

        peaks = _load_audio_peaks(recording_path, bucket_secs=30)
    except (ImportError, OSError, ValueError):
        return
    for bucket, value in peaks.items():
        value = max(0.0, min(1.0, _safe_float(value)))
        if value < 0.55:
            continue
        result = _candidate(
            recording_path=recording_path,
            start=float(bucket) * 30,
            end=(float(bucket) + 1) * 30,
            modality="audio",
            provenance="audio:.waveform.bin",
            text=f"audio peak loud sound energy {value:.2f}",
            confidence=0.45 + value * 0.45,
        )
        if result:
            yield result


def _comment_candidates(recording_path, names):
    from . import search

    for name in names:
        if not name.lower().endswith(".comments.json"):
            continue
        path = os.path.join(recording_path, name)
        for item in search._parse_comments_json(path):
            author = item.get("author", "") or "anonymous"
            result = _candidate(
                recording_path=recording_path,
                modality="comment",
                provenance=f"comment:{name}",
                text=f"{author}: {item.get('text', '')}",
                confidence=0.7,
            )
            if result:
                yield result


def _collect_candidates(recording_path, *, cancel_check=None):
    try:
        names = sorted(os.listdir(recording_path))
    except OSError:
        return
    for candidate in _transcript_candidates(recording_path, names):
        if cancel_check and cancel_check():
            return
        yield candidate
    for candidate in _ocr_candidates(recording_path, names):
        if cancel_check and cancel_check():
            return
        yield candidate
    for candidate in _scene_candidates(recording_path):
        if cancel_check and cancel_check():
            return
        yield candidate
    for candidate in _audio_candidates(recording_path):
        if cancel_check and cancel_check():
            return
        yield candidate
    for candidate in _comment_candidates(recording_path, names):
        if cancel_check and cancel_check():
            return
        yield candidate


def _materialize_candidates(candidates, *, cancel_check=None):
    if not candidates:
        return []
    vectors = local_embeddings(
        [candidate["text"] for candidate in candidates],
        cancel_check=cancel_check,
    )
    if vectors is None:
        return None
    materialized = []
    for candidate, vector in zip(candidates, vectors):
        row = dict(candidate)
        row["vector"] = _pack_vector(vector)
        materialized.append(row)
    return materialized


def _materialize_candidate(candidate):
    rows = _materialize_candidates([candidate])
    return rows[0] if rows else None


def index_recording(
    recording_path,
    *,
    max_moments=DEFAULT_MAX_MOMENTS,
    max_bytes=DEFAULT_MAX_INDEX_BYTES,
    cancel_check=None,
):
    """Rebuild one recording's semantic rows within explicit bounds.

    Cancellation happens before the transaction commits, so a cancelled
    recording retains its previous index instead of being left half-empty.
    """
    recording_path = str(recording_path or "")
    if not recording_path or not os.path.isdir(recording_path):
        return 0
    max_moments = _bounded_int(max_moments, DEFAULT_MAX_MOMENTS, MAX_MAX_MOMENTS)
    max_bytes = _bounded_int(max_bytes, DEFAULT_MAX_INDEX_BYTES, MAX_MAX_INDEX_BYTES)
    candidates = []
    estimated = 0
    truncated = False
    for raw in _collect_candidates(recording_path, cancel_check=cancel_check):
        if cancel_check and cancel_check():
            return 0
        row_size = (
            VECTOR_DIMENSIONS * 4
            + len(raw["text"].encode("utf-8"))
            + 256
        )
        if len(candidates) >= max_moments or estimated + row_size > max_bytes:
            truncated = True
            break
        estimated += row_size
        candidates.append(raw)
    if cancel_check and cancel_check():
        return 0
    rows = _materialize_candidates(candidates, cancel_check=cancel_check)
    if rows is None:
        return 0

    db = _connect()
    try:
        db.execute("BEGIN")
        db.execute(
            "DELETE FROM semantic_moments WHERE recording_path = ?",
            (recording_path,),
        )
        if rows:
            db.executemany(
                "INSERT INTO semantic_moments "
                "(recording_path, start_sec, end_sec, modality, provenance, "
                "text, confidence, vector) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        row["recording_path"], row["start_sec"], row["end_sec"],
                        row["modality"], row["provenance"], row["text"],
                        row["confidence"], row["vector"],
                    )
                    for row in rows
                ],
            )
        if cancel_check and cancel_check():
            db.rollback()
            return 0
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return len(rows), truncated


def _prune_paths(paths):
    paths = tuple(str(path) for path in paths if path)
    db = _connect()
    try:
        if paths:
            placeholders = ",".join("?" for _ in paths)
            db.execute(
                f"DELETE FROM semantic_moments WHERE recording_path NOT IN ({placeholders})",
                paths,
            )
        else:
            db.execute("DELETE FROM semantic_moments")
        db.commit()
    finally:
        db.close()


def reconcile_with_library(recording_paths=None):
    """Drop index rows for recordings the current library does not have.

    ``semantic.db`` is deliberately outside the backup set — it is a derived,
    rebuildable artifact — but that means restoring an older ``library.db``
    used to leave semantic search returning hits for recordings the restored
    library no longer knows about. Pruning ran only inside a full rebuild, so
    nothing reconciled the two on the restore path (V148).

    Reconciling rather than discarding keeps the index useful for everything
    the restored library still holds; anything it is missing is filled in by
    the next indexing pass. Returns a summary dict for the caller to surface.
    """
    if not DB_PATH.exists():
        return {"pruned": 0, "kept": 0, "unindexed": 0, "ran": False}
    if recording_paths is None:
        try:
            from . import db as _db
            recording_paths = [
                str(row.get("path") or "") if isinstance(row, dict)
                else str(getattr(row, "path", "") or "")
                for row in _db.iter_history(page_size=1000)
            ]
        except Exception as error:
            _LOGGER.warning(
                "[SEMANTIC] Could not read the library to reconcile: %s", error,
            )
            return {"pruned": 0, "kept": 0, "unindexed": 0, "ran": False}
    known = {str(path) for path in recording_paths if path}
    connection = _connect()
    try:
        indexed = {
            str(row[0]) for row in connection.execute(
                "SELECT DISTINCT recording_path FROM semantic_moments"
            ).fetchall()
        }
    finally:
        connection.close()
    orphans = indexed - known
    if orphans:
        _prune_paths(known)
    return {
        "pruned": len(orphans),
        "kept": len(indexed & known),
        "unindexed": len(known - indexed),
        "ran": True,
    }


def search_moments(query, limit=100, *, threshold=0.08):
    """Return local vector-ranked hits with timestamps and provenance.

    Exact transcript ranking is fused by :func:`streamkeep.search.hybrid_search`;
    this function stays focused on the rebuildable semantic cache so callers
    can also ask for modality-specific local moments.
    """
    query = _safe_text(query, 512)
    if not query:
        return []
    try:
        limit = max(1, min(500, int(limit or 100)))
    except (TypeError, ValueError, OverflowError):
        limit = 100
    query_vector = local_embedding(query)
    query_tokens = set(_TOKEN_RE.findall(query.lower()))
    db = _connect()
    try:
        rows = db.execute(
            "SELECT recording_path, start_sec, end_sec, modality, provenance, "
            "text, confidence, vector FROM semantic_moments"
        ).fetchall()
    finally:
        db.close()
    hits = []
    for row in rows:
        vector = _unpack_vector(row[7])
        if vector is None:
            continue
        text_tokens = set(_TOKEN_RE.findall(str(row[5] or "").lower()))
        lexical = (
            len(query_tokens & text_tokens) / len(query_tokens)
            if query_tokens else 0.0
        )
        score = max(0.0, min(1.0, _cosine(query_vector, vector)))
        score = max(0.0, min(1.0, score * 0.75 + lexical * 0.25))
        if score < float(threshold):
            continue
        hits.append({
            "recording_path": row[0],
            "start_sec": row[1],
            "end_sec": row[2],
            "modality": row[3],
            "provenance": row[4],
            "text": row[5],
            "confidence": row[6],
            "score": score,
            "vector_version": (
                VECTOR_VERSION if len(vector) == VECTOR_DIMENSIONS
                else FALLBACK_VECTOR_VERSION
            ),
        })
    hits.sort(key=lambda item: (-item["score"], item["recording_path"], item["start_sec"]))
    return hits[:limit]


def index_status():
    """Return a safe diagnostic snapshot of the optional local index."""
    backend = backend_status()
    db = _connect()
    try:
        count = db.execute("SELECT COUNT(*) FROM semantic_moments").fetchone()[0]
        paths = db.execute(
            "SELECT COUNT(DISTINCT recording_path) FROM semantic_moments"
        ).fetchone()[0]
    finally:
        db.close()
    try:
        size = DB_PATH.stat().st_size
    except OSError:
        size = 0
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "vector_version": backend["vector_version"],
        "vector_dimensions": backend["dimensions"],
        "backend": backend["backend"],
        "backend_available": backend["available"],
        "backend_detail": backend["detail"],
        "enabled": is_enabled(),
        "moments": int(count or 0),
        "recordings": int(paths or 0),
        "bytes": int(size),
        "path": str(DB_PATH),
    }


class SemanticIndexWorker(QThread):
    """Cancellable rebuild worker for the opt-in local semantic index."""

    progress = pyqtSignal(int, str)
    log = pyqtSignal(str)
    done = pyqtSignal(dict)

    def __init__(self, recording_paths, *, max_moments=DEFAULT_MAX_MOMENTS,
                 max_bytes=DEFAULT_MAX_INDEX_BYTES, parent=None):
        super().__init__(parent)
        self.recording_paths = tuple(str(path) for path in recording_paths if path)
        self.max_moments = max_moments
        self.max_bytes = max_bytes
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        total = len(self.recording_paths)
        indexed = moments = 0
        truncated = 0
        cancelled = False
        for index, path in enumerate(self.recording_paths, start=1):
            if self._cancel.is_set():
                cancelled = True
                break
            try:
                result = index_recording(
                    path,
                    max_moments=self.max_moments,
                    max_bytes=self.max_bytes,
                    cancel_check=self._cancel.is_set,
                )
                count, was_truncated = result if isinstance(result, tuple) else (result, False)
                if self._cancel.is_set():
                    cancelled = True
                    break
                indexed += 1
                moments += int(count or 0)
                truncated += int(bool(was_truncated))
            except Exception as error:
                self.log.emit(f"[SEMANTIC] Skipped {path}: {error}")
            self.progress.emit(
                int(index / max(total, 1) * 100),
                f"Semantic index {index}/{total}",
            )
        if not cancelled:
            _prune_paths(self.recording_paths)
        self.done.emit({
            "cancelled": cancelled,
            "recordings": indexed,
            "moments": moments,
            "truncated": truncated,
        })
