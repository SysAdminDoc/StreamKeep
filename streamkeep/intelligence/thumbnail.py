"""Smart Thumbnail Generator — score frames for visual interest (F61).

Extracts candidate frames at scene boundaries, scores each by luminance
variance (contrast) and edge density (detail). Optionally detects faces
via mediapipe if available. Selects the top-scored frame and saves as
``smart-thumbnail.jpg`` (1280x720), preserving any existing ``thumbnail.jpg``.

Optional text overlay via Pillow (stream title, channel, date).
"""

import os
import shutil
import subprocess
import tempfile
import warnings

from PyQt6.QtCore import QThread, pyqtSignal

from ..capabilities import (
    CapabilityUnavailableError,
    require_capability,
    resolve_tool_command,
)
from ..paths import _CREATE_NO_WINDOW, FFMPEG_SAFETY


MAX_THUMBNAIL_PIXELS = 20_000_000
MAX_THUMBNAIL_FRAMES = 8
THUMBNAIL_PROVIDER_VERSION = "smart-thumbnail-v2"
SMART_THUMBNAIL_NAME = "smart-thumbnail.jpg"


def _open_bounded_image(image_path, *, mode="L"):
    """Open one still image under explicit Pillow pixel/frame limits."""
    require_capability("pillow")
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_THUMBNAIL_PIXELS
    image = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(image_path)
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_THUMBNAIL_PIXELS:
                raise ValueError("thumbnail exceeds the Pillow pixel limit")
            if int(getattr(image, "n_frames", 1) or 1) > MAX_THUMBNAIL_FRAMES:
                raise ValueError("thumbnail exceeds the Pillow animation-frame limit")
            image.seek(0)
            result = image.convert(mode).copy()
    except Exception as error:
        raise ValueError("thumbnail could not be decoded within Pillow limits") from error
    finally:
        if image is not None:
            image.close()
    return result


def _extract_frame(media_path, at_secs, out_path, width=1280, height=720):
    """Extract a single frame from *media_path* at *at_secs* using ffmpeg."""
    try:
        cmd = [
            resolve_tool_command("ffmpeg"), *FFMPEG_SAFETY,
            "-y", "-ss", str(at_secs), "-i", media_path,
            "-frames:v", "1",
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "-q:v", "2", out_path,
        ]
        subprocess.run(
            cmd, capture_output=True, timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    except (CapabilityUnavailableError, subprocess.TimeoutExpired, OSError):
        return False


def _score_frame(image_path):
    """Score a frame image for visual interest.

    Returns a float score (higher = more interesting). Uses:
    - Luminance variance (contrast)
    - Edge density (detail/sharpness)
    - Optional face detection bonus
    """
    score = 0.0
    pillow_ready = False

    try:
        require_capability("pillow")
        from PIL import ImageFilter, ImageStat
        img = _open_bounded_image(image_path, mode="L")
        pillow_ready = True

        # Luminance variance (contrast)
        stat = ImageStat.Stat(img)
        variance = stat.var[0] if stat.var else 0
        score += min(variance / 2000.0, 1.0)  # normalize, cap at 1.0

        # Edge density (Laplacian-like)
        edges = img.filter(ImageFilter.FIND_EDGES)
        edge_stat = ImageStat.Stat(edges)
        edge_mean = edge_stat.mean[0] if edge_stat.mean else 0
        score += min(edge_mean / 30.0, 1.0)

    except (CapabilityUnavailableError, ImportError, OSError, ValueError):
        # No Pillow — basic file-size heuristic (larger JPEG = more detail)
        try:
            size_kb = os.path.getsize(image_path) / 1024
            score += min(size_kb / 200.0, 1.0)
        except OSError:
            pass

    # Face detection bonus (optional)
    try:
        if not pillow_ready:
            return score
        import mediapipe as mp
        face_det = mp.solutions.face_detection.FaceDetection(
            model_selection=0, min_detection_confidence=0.5
        )
        from PIL import Image
        import numpy as np
        img = np.array(Image.open(image_path).convert("RGB"))
        results = face_det.process(img)
        if results.detections:
            score += 0.5 * min(len(results.detections), 3)
        face_det.close()
    except (ImportError, Exception):
        pass

    return score


def _apply_text_overlay(image_path, title="", channel="", date=""):
    """Apply text overlay to the thumbnail using Pillow."""
    if not title and not channel:
        return
    try:
        require_capability("pillow")
        from PIL import ImageDraw, ImageFont
        img = _open_bounded_image(image_path, mode="RGB")
        draw = ImageDraw.Draw(img)

        # Use a default font (Pillow's built-in)
        try:
            font = ImageFont.truetype("arial.ttf", 36)
            font_small = ImageFont.truetype("arial.ttf", 24)
        except (OSError, IOError):
            font = ImageFont.load_default()
            font_small = font

        # Draw title with shadow
        text = title[:60] if title else ""
        if text:
            x, y = 40, img.height - 120
            # Shadow
            draw.text((x + 2, y + 2), text, fill="black", font=font)
            draw.text((x, y), text, fill="white", font=font)

        # Draw channel + date
        sub = ""
        if channel:
            sub = channel
        if date:
            sub = f"{sub} | {date}" if sub else date
        if sub:
            x, y = 40, img.height - 70
            draw.text((x + 1, y + 1), sub, fill="black", font=font_small)
            draw.text((x, y), sub, fill=(200, 200, 200), font=font_small)

        temp_path = image_path + ".tmp"
        img.save(temp_path, "JPEG", quality=92)
        os.replace(temp_path, image_path)
    except (CapabilityUnavailableError, ImportError, OSError, ValueError):
        try:
            if os.path.exists(image_path + ".tmp"):
                os.unlink(image_path + ".tmp")
        except OSError:
            pass
        pass


def generate_thumbnail(recording_dir, *, num_candidates=10, width=1280,
                       height=720, overlay_text=True, title="", channel="",
                       date=""):
    """Generate a smart thumbnail for a recording.

    Extracts *num_candidates* frames at evenly-spaced timestamps, scores
    each, and saves the best as ``smart-thumbnail.jpg`` without replacing an
    existing source thumbnail.

    Returns ``(path, score)`` or ``("", 0)`` on failure.
    """
    # Find media file
    media = ""
    for fn in sorted(os.listdir(recording_dir)):
        if fn.lower().endswith((
            ".mp4", ".mkv", ".ts", ".webm", ".mov", ".avi", ".flv", ".m4v",
            ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac",
        )) and not fn.startswith("."):
            media = os.path.join(recording_dir, fn)
            break
    if not media:
        return "", 0

    # Probe duration
    duration = _probe_duration(media)
    if duration <= 0:
        return "", 0

    # Extract candidate frames in a context-managed directory so cancellation,
    # ffmpeg failures, and Pillow rejection never leave an unbounded cache.
    candidates = []
    interval = duration / (max(1, min(int(num_candidates or 10), 50)) + 1)
    with tempfile.TemporaryDirectory(prefix="sk_thumb_") as tmp_dir:
        for i in range(max(1, min(int(num_candidates or 10), 50))):
            at_secs = interval * (i + 1)
            out = os.path.join(tmp_dir, f"cand_{i:03d}.jpg")
            if _extract_frame(media, at_secs, out, width, height):
                score = _score_frame(out)
                candidates.append((out, score, at_secs))

        if not candidates:
            return "", 0

        # Pick the best. Never overwrite the source thumbnail: media metadata
        # and server exports continue to use thumbnail.jpg as the original.
        candidates.sort(key=lambda c: -c[1])
        best_path, best_score, _best_time = candidates[0]
        final = os.path.join(recording_dir, SMART_THUMBNAIL_NAME)
        temp_final = final + ".tmp"
        try:
            shutil.copy2(best_path, temp_final)
            os.replace(temp_final, final)
        except OSError:
            try:
                if os.path.exists(temp_final):
                    os.unlink(temp_final)
            except OSError:
                pass
            return "", 0

    # Apply text overlay after the candidate directory is removed. The output
    # remains an atomic smart-thumbnail replacement and the source is intact.
    if overlay_text and (title or channel):
        _apply_text_overlay(final, title=title, channel=channel, date=date)

    return final, best_score


def _probe_duration(media_path):
    """Get media duration in seconds via ffprobe."""
    try:
        cmd = [
            resolve_tool_command("ffprobe"), "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            media_path,
        ]
        r = subprocess.run(
            cmd, capture_output=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        if r.returncode == 0:
            return float(r.stdout.decode("utf-8", errors="replace").strip())
    except (CapabilityUnavailableError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return 0


# ── Worker thread ───────────────────────────────────────────────────

class ThumbnailGenWorker(QThread):
    """Generate smart thumbnail in background."""

    done = pyqtSignal(bool, str, float)   # ok, path, score
    log = pyqtSignal(str)

    def __init__(self, recording_dir, title="", channel="", date=""):
        super().__init__()
        self._dir = recording_dir
        self._title = title
        self._channel = channel
        self._date = date

    def run(self):
        try:
            path, score = generate_thumbnail(
                self._dir,
                title=self._title,
                channel=self._channel,
                date=self._date,
            )
            if path:
                self.log.emit(f"[THUMBNAIL] Generated: {path} (score {score:.2f})")
                self.done.emit(True, path, score)
            else:
                self.done.emit(False, "Failed to generate thumbnail", 0)
        except Exception as e:
            self.log.emit(f"[THUMBNAIL] Error: {e}")
            self.done.emit(False, str(e), 0)
