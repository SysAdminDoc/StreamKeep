"""ICY/Shoutcast metadata parsing and deterministic track splitting.

The ICY metadata interval is embedded in an otherwise continuous audio byte
stream.  Keeping this parser independent of FFmpeg makes it testable with a
small in-memory stream and lets raw radio captures preserve now-playing
changes without scraping logs or trusting shell text.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from .utils import safe_filename


@dataclass(frozen=True)
class IcyMetadata:
    """Metadata announced at an ICY boundary."""

    stream_title: str = ""
    stream_url: str = ""
    raw: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return self.stream_title, self.stream_url


@dataclass(frozen=True)
class IcyAudioChunk:
    """Audio bytes followed by the metadata that applies to the next chunk."""

    data: bytes
    metadata: IcyMetadata = IcyMetadata()


@dataclass(frozen=True)
class IcyTrack:
    """One split radio track written by :func:`split_icy_stream`."""

    index: int
    title: str
    url: str
    path: str
    bytes_written: int
    offset: int


@dataclass(frozen=True)
class IcySplitResult:
    """Manifest-level result for an ICY split operation."""

    manifest_path: str
    tracks: tuple[IcyTrack, ...]
    total_bytes: int
    stopped: bool = False


def parse_icy_metadata(payload: bytes | str) -> IcyMetadata:
    """Parse a NUL-padded ICY metadata block.

    ICY uses semicolon-separated ``key='value'`` pairs. Unknown keys are
    intentionally ignored; preserving the raw text in the manifest keeps the
    parser forward-compatible without treating metadata as executable input.
    """
    if isinstance(payload, bytes):
        text = payload.rstrip(b"\x00").decode("iso-8859-1", errors="replace")
    else:
        text = str(payload or "").rstrip("\x00")
    values: dict[str, str] = {}
    for part in text.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        if key:
            values[key] = value
    return IcyMetadata(
        stream_title=values.get("streamtitle", "").strip(),
        stream_url=values.get("streamurl", "").strip(),
        raw=text,
    )


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    chunks = []
    remaining = max(0, int(length))
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def iter_icy_audio(
    stream: BinaryIO,
    metadata_interval: int,
    *,
    read_size: int = 64 * 1024,
) -> Iterator[IcyAudioChunk]:
    """Yield audio bytes while consuming each embedded ICY metadata block.

    A zero interval is valid for servers that do not provide metadata; those
    streams are yielded in bounded chunks with empty metadata.
    """
    interval = max(0, int(metadata_interval or 0))
    current = IcyMetadata()
    if interval == 0:
        while True:
            data = stream.read(max(1, int(read_size)))
            if not data:
                return
            yield IcyAudioChunk(data, current)

    while True:
        audio = _read_exact(stream, interval)
        if not audio:
            return
        yield IcyAudioChunk(audio, current)
        length_byte = _read_exact(stream, 1)
        if not length_byte:
            return
        metadata_length = length_byte[0] * 16
        current = parse_icy_metadata(
            _read_exact(stream, metadata_length) if metadata_length else b""
        )


def _track_filename(base_name: str, index: int, title: str) -> str:
    safe_title = safe_filename(title, max_len=72) if title else "Unknown"
    safe_base = safe_filename(base_name, max_len=72) or "radio"
    return f"{safe_base}_{index:03d}_{safe_title}.mp3"


def split_icy_stream(
    stream: BinaryIO,
    output_path: str | Path,
    metadata_interval: int,
    *,
    stop_event=None,
    max_seconds: float = 0,
    clock: Callable[[], float] = time.monotonic,
) -> IcySplitResult:
    """Write raw MP3 track files as ICY ``StreamTitle`` changes arrive.

    The split boundary is the metadata interval boundary, so no metadata
    bytes are written into an audio file. Each file remains a valid raw stream
    fragment for FFmpeg remuxing even when the station changes title between
    codec frame boundaries.
    """
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    base_name = target.stem or "radio"
    tracks: list[IcyTrack] = []
    handle = None
    current_meta = IcyMetadata()
    current_path = ""
    current_bytes = 0
    total_bytes = 0
    offset = 0
    started = clock()
    stopped = False

    def open_track(metadata: IcyMetadata):
        nonlocal handle, current_path, current_bytes, current_meta
        if handle is not None:
            handle.close()
        index = len(tracks) + 1
        path = target.parent / _track_filename(
            base_name, index, metadata.stream_title,
        )
        handle = path.open("wb")
        current_path = str(path)
        current_bytes = 0
        current_meta = metadata

    try:
        for chunk in iter_icy_audio(stream, metadata_interval):
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            if max_seconds > 0 and clock() - started >= max_seconds:
                stopped = True
                break
            if handle is None:
                open_track(chunk.metadata)
            elif chunk.metadata.key != current_meta.key and current_bytes:
                handle.close()
                tracks.append(IcyTrack(
                    len(tracks) + 1,
                    current_meta.stream_title or "Unknown",
                    current_meta.stream_url,
                    current_path,
                    current_bytes,
                    offset - current_bytes,
                ))
                handle = None
                open_track(chunk.metadata)
            handle.write(chunk.data)
            current_bytes += len(chunk.data)
            total_bytes += len(chunk.data)
            offset += len(chunk.data)
    finally:
        if handle is not None:
            handle.close()

    if handle is not None or current_path:
        if current_bytes:
            tracks.append(IcyTrack(
                len(tracks) + 1,
                current_meta.stream_title or "Unknown",
                current_meta.stream_url,
                current_path,
                current_bytes,
                offset - current_bytes,
            ))
    manifest_path = target.with_suffix(".tracks.json")
    manifest = {
        "schema": "streamkeep.icy-tracks",
        "version": 1,
        "source": base_name,
        "total_bytes": total_bytes,
        "stopped": stopped,
        "tracks": [asdict(track) for track in tracks],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return IcySplitResult(
        str(manifest_path), tuple(tracks), total_bytes, stopped,
    )
