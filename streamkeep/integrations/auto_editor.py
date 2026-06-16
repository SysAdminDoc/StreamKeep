"""auto-editor integration — intelligent silence/dead-air removal.

When ``auto-editor`` is installed (``pip install auto-editor``), this
module provides an alternative to the built-in ffmpeg silencedetect
approach.  auto-editor supports multi-method analysis (audio loudness,
motion detection, subtitle timing) with configurable margins.

The integration works by:
  1. Running ``auto-editor`` in export-timeline mode (no destructive edit)
  2. Parsing the exported timeline to extract non-silent segments
  3. Writing a ``.chapters.auto_editor.txt`` sidecar with the cut points
  4. Optionally re-muxing the file using the same concat approach as
     the built-in silence removal

Usage::

    from streamkeep.integrations.auto_editor import (
        is_available, remove_silence, export_timeline,
    )
    if is_available():
        remove_silence(src, dst, method="audio", margin="0.2s")
"""

import json
import os
import subprocess
import tempfile

from ..paths import _CREATE_NO_WINDOW, FFMPEG_SAFETY


def is_available():
    """Return True if auto-editor is installed and callable."""
    try:
        r = subprocess.run(
            ["auto-editor", "--help"],
            capture_output=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def version():
    """Return the auto-editor version string, or ''."""
    try:
        r = subprocess.run(
            ["auto-editor", "-V"],
            capture_output=True, text=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        return (r.stdout or "").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def export_timeline(src, method="audio", threshold="4%", margin="0.2s",
                    log_fn=None):
    """Run auto-editor and return a list of ``(start_secs, end_secs)`` tuples
    representing the non-silent (kept) segments.

    *method* can be: ``"audio"`` (default), ``"motion"``, or a compound
    expression like ``"(or audio:4% motion:2%)"`` — passed directly to
    ``--edit``.

    Returns ``[]`` on any failure.
    """
    tmpdir = tempfile.mkdtemp(prefix="sk_autoeditor_")
    timeline_path = os.path.join(tmpdir, "timeline.json")
    try:
        edit_arg = method
        if ":" not in method and method in ("audio", "motion"):
            edit_arg = f"{method}:{threshold}"

        cmd = [
            "auto-editor", src,
            "--edit", edit_arg,
            "--margin", str(margin),
            "--export", "json",
            "--output", timeline_path,
            "--no-open",
        ]
        if log_fn:
            log_fn(f"[AUTO-EDITOR] Analyzing: {os.path.basename(src)}")
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            creationflags=_CREATE_NO_WINDOW,
        )
        if r.returncode != 0:
            if log_fn:
                log_fn(f"[AUTO-EDITOR] Failed: {(r.stderr or '')[:200]}")
            return []

        if not os.path.isfile(timeline_path):
            if log_fn:
                log_fn("[AUTO-EDITOR] No timeline output produced.")
            return []

        with open(timeline_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        segments = []
        for chunk in data.get("chunks", data.get("timeline", [])):
            if isinstance(chunk, dict):
                start = float(chunk.get("start", 0))
                end = float(chunk.get("end", 0))
                speed = chunk.get("speed", 1)
                if speed and speed != 99999 and end > start:
                    segments.append((start, end))
            elif isinstance(chunk, (list, tuple)) and len(chunk) >= 3:
                start, end, speed = float(chunk[0]), float(chunk[1]), chunk[2]
                if speed and speed != 99999 and end > start:
                    segments.append((start, end))

        if log_fn:
            kept = sum(e - s for s, e in segments)
            log_fn(
                f"[AUTO-EDITOR] {len(segments)} segment(s), "
                f"{kept:.1f}s kept"
            )
        return segments

    except Exception as e:
        if log_fn:
            log_fn(f"[AUTO-EDITOR] Error: {e}")
        return []
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except OSError:
            pass


def write_chapters_file(segments, output_path, log_fn=None):
    """Write a chapters file from auto-editor segments.

    Format: one line per segment, ``HH:MM:SS.mmm Chapter N``.
    """
    lines = []
    for i, (start, _end) in enumerate(segments, 1):
        h = int(start // 3600)
        m = int((start % 3600) // 60)
        s = start % 60
        lines.append(f"{h:02d}:{m:02d}:{s:06.3f} Segment {i}")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if log_fn:
        log_fn(f"[AUTO-EDITOR] Wrote chapters: {os.path.basename(output_path)}")


def remove_silence(src, dst, method="audio", threshold="4%", margin="0.2s",
                   log_fn=None):
    """Run auto-editor analysis, then re-mux using ffmpeg concat to produce
    a silence-removed copy.

    Returns True on success, False on failure.
    """
    segments = export_timeline(src, method, threshold, margin, log_fn)
    if not segments:
        return False

    ext = os.path.splitext(src)[1] or ".mp4"
    tmpdir = tempfile.mkdtemp(prefix="sk_ae_mux_")
    try:
        seg_files = []
        for idx, (seg_start, seg_end) in enumerate(segments):
            seg_dst = os.path.join(tmpdir, f"seg_{idx:04d}{ext}")
            seg_cmd = [
                "ffmpeg", *FFMPEG_SAFETY, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{seg_start:.3f}", "-to", f"{seg_end:.3f}",
                "-i", src, "-c", "copy", seg_dst,
            ]
            r = subprocess.run(
                seg_cmd, capture_output=True, timeout=300,
                creationflags=_CREATE_NO_WINDOW,
            )
            if r.returncode == 0 and os.path.exists(seg_dst) and os.path.getsize(seg_dst) > 0:
                seg_files.append(seg_dst)

        if not seg_files:
            if log_fn:
                log_fn("[AUTO-EDITOR] No valid segments extracted.")
            return False

        concat_path = os.path.join(tmpdir, "concat.txt")
        with open(concat_path, "w") as f:
            for sf in seg_files:
                escaped = sf.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        concat_cmd = [
            "ffmpeg", *FFMPEG_SAFETY, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_path,
            "-c", "copy", dst,
        ]
        r = subprocess.run(
            concat_cmd, capture_output=True, timeout=600,
            creationflags=_CREATE_NO_WINDOW,
        )
        if r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0:
            if log_fn:
                log_fn(f"[AUTO-EDITOR] Silence removed: {os.path.basename(dst)}")
            return True
        if log_fn:
            log_fn(f"[AUTO-EDITOR] Concat failed: {(r.stderr or b'').decode('utf-8', errors='replace')[:200]}")
        return False

    except Exception as e:
        if log_fn:
            log_fn(f"[AUTO-EDITOR] Error during re-mux: {e}")
        return False
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except OSError:
            pass
