"""Fill missing album-artist tags on audio-only downloads (V41).

Audio from SoundCloud, Audius, and podcast RSS routinely arrives without an
``album_artist`` tag, which is exactly the field media libraries group by — so
a whole artist's back catalogue scatters into one-track "Unknown Artist"
entries. The uploader/channel that the download already carries is the correct
value; this fills it in when, and only when, it is missing.

Two rules make this safe to run on every audio finalize:

* An existing tag is never overwritten. A file that already says who made it
  is authoritative, whatever the source metadata claims.
* Nothing is re-encoded. Tagging is a stream copy into a sibling temp file
  that atomically replaces the original only after ffmpeg succeeds.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..paths import _CREATE_NO_WINDOW

AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac",
})

# Platforms whose audio downloads are music/podcast material worth grouping.
MUSIC_PLATFORMS = frozenset({"soundcloud", "audius", "podcast"})

# Tags we are willing to synthesise, and where each value comes from.
FILLABLE_TAGS = ("album_artist", "artist", "album")

_EXISTING_ALIASES = {
    "album_artist": ("album_artist", "albumartist", "album artist", "TPE2"),
    "artist": ("artist", "TPE1", "author", "performer"),
    "album": ("album", "TALB"),
}


def is_audio_file(path) -> bool:
    """Return whether a path looks like an audio-only output."""
    return Path(str(path or "")).suffix.lower() in AUDIO_EXTENSIONS


def is_music_platform(platform) -> bool:
    """Return whether this source's audio benefits from library grouping."""
    return str(platform or "").strip().lower() in MUSIC_PLATFORMS


def read_tags(path, *, ffprobe="ffprobe", timeout=30) -> dict:
    """Return the container's existing tags, lowercased. Never raises."""
    try:
        result = subprocess.run(
            [
                str(ffprobe), "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=max(1, int(timeout)), creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    tags: dict[str, str] = {}
    sources = [data.get("format", {}) or {}]
    sources += list(data.get("streams", []) or [])
    for source in sources:
        for key, value in (source.get("tags") or {}).items():
            text = str(value or "").strip()
            if text:
                tags.setdefault(str(key).strip().lower(), text)
    return tags


def _has_tag(existing, field) -> bool:
    for alias in _EXISTING_ALIASES.get(field, (field,)):
        if str(existing.get(alias.lower(), "") or "").strip():
            return True
    return False


def plan_music_tags(existing, *, channel="", album="", title="") -> dict:
    """Return only the tags that are missing and can be filled.

    ``album`` is filled from an explicitly supplied album (a podcast show, for
    instance) and never invented from the track title, which would create one
    single-track album per download — worse than no album at all.
    """
    existing = {str(k).lower(): v for k, v in (existing or {}).items()}
    channel = str(channel or "").strip()
    album = str(album or "").strip()
    planned: dict[str, str] = {}
    if channel:
        for field in ("album_artist", "artist"):
            if not _has_tag(existing, field):
                planned[field] = channel
    if album and not _has_tag(existing, "album"):
        planned["album"] = album
    if title and not _has_tag(existing, "title"):
        planned["title"] = str(title).strip()
    return planned


def build_tag_command(source, target, tags, *, ffmpeg="ffmpeg") -> list[str]:
    """Return the ffmpeg argv that copies *source* to *target* with *tags*."""
    if not tags:
        raise ValueError("No tags to write")
    cmd = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(source), "-map", "0", "-c", "copy",
    ]
    for key in sorted(tags):
        cmd += ["-metadata", f"{key}={tags[key]}"]
    cmd.append(str(target))
    return cmd


def apply_music_tags(
    path, *, channel="", album="", title="", ffmpeg="ffmpeg", ffprobe="ffprobe",
    timeout=120,
):
    """Fill missing album-artist style tags in place.

    Returns ``(ok, applied, message)``. ``ok`` is True when the file is left in
    a good state, including the common case of "nothing needed doing".
    """
    source = Path(path)
    if not source.is_file():
        return False, {}, f"No such audio file: {source}"
    if not is_audio_file(source):
        return True, {}, "Not an audio file; nothing to tag"

    existing = read_tags(source, ffprobe=ffprobe)
    planned = plan_music_tags(
        existing, channel=channel, album=album, title=title,
    )
    if not planned:
        return True, {}, "Existing tags already identify the artist"

    target = source.with_name(f"{source.stem}.tagging{source.suffix}")
    try:
        result = subprocess.run(
            build_tag_command(source, target, planned, ffmpeg=ffmpeg),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=max(1, int(timeout)), creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        _cleanup(target)
        return False, {}, f"Could not tag audio: {error}"

    if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        tail = (result.stderr or "").strip().splitlines()[-2:]
        _cleanup(target)
        return False, {}, f"ffmpeg could not write tags: {' '.join(tail)}"

    try:
        os.replace(target, source)
    except OSError as error:
        _cleanup(target)
        return False, {}, f"Could not replace the audio file: {error}"
    return True, planned, f"Filled {', '.join(sorted(planned))}"


def _cleanup(path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def find_audio_outputs(directory) -> list[str]:
    """Return audio files in a finished recording directory, largest first."""
    try:
        entries = [
            path for path in Path(directory).iterdir()
            if path.is_file() and is_audio_file(path)
            and not path.name.startswith(".")
        ]
    except (OSError, ValueError):
        return []
    entries.sort(key=lambda path: path.stat().st_size, reverse=True)
    return [str(path) for path in entries]
