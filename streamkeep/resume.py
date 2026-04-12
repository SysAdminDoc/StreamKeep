"""Resume sidecar — persists in-flight download state for crash-safe resume.

A sidecar file named `.streamkeep_resume.json` is written into each output
directory at download start, refreshed after each segment completes or on
cancel, and deleted when the download finishes cleanly. At app startup the
Download tab scans known output directories for orphan sidecars and shows
a "Resume N interrupted download(s)" banner.

Token-bearing playlist URLs (Kick m3u8 master, Twitch signed playback, etc.)
typically expire in ~24h. Before actually restarting a download the UI should
re-run the extractor for the source_url to refresh playlist_url — if the
segment list has shifted shape we fall back to a full restart.
"""

import json
import os
from dataclasses import asdict
from datetime import datetime

from .models import ResumeState

SIDECAR_NAME = ".streamkeep_resume.json"


def _sidecar_path(output_dir):
    return os.path.join(output_dir, SIDECAR_NAME)


def save_resume_state(state):
    """Atomically write a ResumeState to its output directory.

    Silent on error — a resume sidecar is a nice-to-have, never a correctness
    requirement, so disk-full / permission errors must not block the actual
    download."""
    if not state or not state.output_dir:
        return
    try:
        os.makedirs(state.output_dir, exist_ok=True)
    except OSError:
        return
    state.updated_at = datetime.now().isoformat(timespec="seconds")
    if not state.created_at:
        state.created_at = state.updated_at
    try:
        payload = json.dumps(asdict(state), indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return
    path = _sidecar_path(state.output_dir)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            try:
                f.flush()
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
        os.replace(tmp, path)
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def load_resume_state(output_dir):
    """Read a sidecar. Returns a ResumeState or None."""
    if not output_dir:
        return None
    path = _sidecar_path(output_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # Ignore unknown keys (forward compat); fill defaults for missing ones.
    fields = set(ResumeState.__dataclass_fields__.keys())
    clean = {k: v for k, v in data.items() if k in fields}
    try:
        return ResumeState(**clean)
    except TypeError:
        return None


def clear_resume_state(output_dir):
    """Delete the sidecar. Safe to call if none exists."""
    if not output_dir:
        return
    path = _sidecar_path(output_dir)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def scan_for_orphan_sidecars(roots):
    """Find resume sidecars under any of the given root directories whose
    download looks interrupted (no matching all-done marker, fewer completed
    segments than the original segment list, etc.).

    Returns a list of ResumeState, freshest first. Used by the startup banner.
    """
    found = []
    seen = set()
    for root in roots or []:
        if not root or not os.path.isdir(root):
            continue
        # Walk at most 3 levels deep — per-VOD output folders live directly
        # inside the user's Videos/Capture root; scanning a whole disk is a
        # non-goal and would be slow on big archives.
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
            if depth > 3:
                dirnames[:] = []
                continue
            if SIDECAR_NAME not in filenames:
                continue
            real = os.path.realpath(dirpath)
            if real in seen:
                continue
            seen.add(real)
            state = load_resume_state(dirpath)
            if state is None:
                continue
            total = len(state.segments or [])
            done = len(state.completed or [])
            if total and done >= total:
                # All segments completed but sidecar wasn't cleaned up —
                # finalize probably crashed. Treat as already-done and
                # ignore rather than offering to "resume" a completed job.
                continue
            found.append(state)
    found.sort(key=lambda s: s.updated_at or "", reverse=True)
    return found


def merge_completed(state, seg_idx):
    """Record a completed segment index. Idempotent."""
    if not state:
        return
    if seg_idx in state.completed:
        return
    state.completed.append(int(seg_idx))


def remaining_segments(state):
    """Return (seg_idx, label, start, duration) tuples that still need work.

    Tolerates segments stored either as lists (JSON round-trip) or tuples.
    """
    if not state:
        return []
    done = set(int(x) for x in (state.completed or []))
    out = []
    for seg in (state.segments or []):
        if not seg or len(seg) < 4:
            continue
        try:
            idx = int(seg[0])
        except (TypeError, ValueError):
            continue
        if idx in done:
            continue
        out.append((idx, str(seg[1]), float(seg[2]), float(seg[3])))
    return out
