"""Live-capture reliability: fragment-gap detection and raw-capture salvage.

yt-dlp's ``--live-from-start`` drops fragments on unstable streams and reports
it only in passing output, so a capture can finish "successfully" with silent
holes in it. This module turns that output into an explicit gap report, and
preserves the raw staging files an interrupted or failed finalization leaves
behind so the bytes that *were* captured can still be turned into a playable
file.

Two rules govern salvage:

* The raw capture is never modified. Salvage always writes a **new** file.
* Salvage is idempotent. Re-running it produces the same result and never
  overwrites a known-good output.

Everything here is pure enough to test without a network, a stream, or Qt.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

STAGING_SUFFIX = ".rawcapture"
REPORT_NAME = "capture-report.json"
REPORT_VERSION = 1

# Extensions yt-dlp / ffmpeg leave behind mid-capture. These are the bytes a
# salvage pass has to work from.
STAGING_EXTENSIONS = (".part", ".ytdl", ".ts", ".m4s", ".frag", ".temp")

_FRAGMENT_RE = re.compile(r"\bfragment\s+(\d+)\b", re.IGNORECASE)
_GIVE_UP_RE = re.compile(
    r"giving up after\s+(\d+)\s+(?:fragment\s+)?retries", re.IGNORECASE
)
# Phrases that mean a fragment was abandoned, not merely retried. "Retrying
# fragment N" is normal and must not count as a gap.
_LOST_PHRASES = (
    "not found",
    "unable to continue",
    "skipping fragment",
    "fragment not available",
    "giving up",
    "unable to download fragment",
    "could not download fragment",
)


@dataclass(frozen=True)
class CaptureGaps:
    """Fragments a live capture is known to have lost."""

    missing: tuple[int, ...] = ()
    gave_up: int = 0
    unknown_losses: int = 0

    @property
    def has_gaps(self) -> bool:
        return bool(self.missing) or self.gave_up > 0 or self.unknown_losses > 0

    @property
    def count(self) -> int:
        return len(self.missing) + self.unknown_losses

    def intervals(self) -> tuple[tuple[int, int], ...]:
        """Collapse the missing fragment indices into inclusive ranges."""
        if not self.missing:
            return ()
        ordered = sorted(set(self.missing))
        ranges: list[list[int]] = [[ordered[0], ordered[0]]]
        for index in ordered[1:]:
            if index == ranges[-1][1] + 1:
                ranges[-1][1] = index
            else:
                ranges.append([index, index])
        return tuple((start, end) for start, end in ranges)

    def describe(self) -> str:
        """Return an operator-readable summary of what was lost."""
        if not self.has_gaps:
            return "no fragment gaps reported"
        parts = []
        intervals = self.intervals()
        if intervals:
            rendered = ", ".join(
                str(start) if start == end else f"{start}-{end}"
                for start, end in intervals[:8]
            )
            if len(intervals) > 8:
                rendered += f", +{len(intervals) - 8} more"
            parts.append(f"missing fragments {rendered}")
        if self.unknown_losses:
            parts.append(f"{self.unknown_losses} unidentified fragment loss(es)")
        if self.gave_up:
            parts.append(f"gave up after {self.gave_up} retries")
        return "; ".join(parts)

    def to_dict(self) -> dict:
        return {
            "missing": list(self.missing),
            "intervals": [list(pair) for pair in self.intervals()],
            "gave_up": self.gave_up,
            "unknown_losses": self.unknown_losses,
            "summary": self.describe(),
        }


def parse_fragment_gaps(lines) -> CaptureGaps:
    """Extract abandoned fragments from yt-dlp's combined output.

    Only phrases that mean a fragment was *lost* count. A plain
    ``Retrying fragment 5 (1/10)`` is ordinary backoff and is ignored, so a
    flaky-but-complete capture is not reported as damaged.
    """
    missing: list[int] = []
    gave_up = 0
    unknown = 0
    for raw in lines or ():
        line = str(raw or "")
        lowered = line.casefold()
        if not any(phrase in lowered for phrase in _LOST_PHRASES):
            continue
        give_up_match = _GIVE_UP_RE.search(line)
        if give_up_match:
            gave_up = max(gave_up, int(give_up_match.group(1)))
        fragment_match = _FRAGMENT_RE.search(line)
        if fragment_match:
            missing.append(int(fragment_match.group(1)))
        elif not give_up_match:
            unknown += 1
    return CaptureGaps(
        missing=tuple(sorted(set(missing))),
        gave_up=gave_up,
        unknown_losses=unknown,
    )


# ── Raw-capture staging ─────────────────────────────────────────────

@dataclass
class StagedCapture:
    """A preserved raw capture plus what is known about its completeness."""

    directory: str = ""
    files: list[str] = field(default_factory=list)
    total_bytes: int = 0
    gaps: CaptureGaps = field(default_factory=CaptureGaps)
    reason: str = ""

    @property
    def exists(self) -> bool:
        return bool(self.directory) and os.path.isdir(self.directory)


def staging_dir_for(output_path) -> str:
    """Return the staging directory that belongs to one output file."""
    base, _ext = os.path.splitext(str(output_path or ""))
    return f"{base}{STAGING_SUFFIX}" if base else ""


def find_staging_files(output_dir, label="") -> list[str]:
    """Return raw capture leftovers in *output_dir*, largest first."""
    try:
        entries = list(Path(output_dir).iterdir())
    except (OSError, ValueError):
        return []
    prefix = str(label or "")
    found = []
    for entry in entries:
        if not entry.is_file():
            continue
        name = entry.name
        if prefix and not name.startswith(prefix):
            continue
        lowered = name.casefold()
        if not any(lowered.endswith(ext) for ext in STAGING_EXTENSIONS):
            continue
        try:
            found.append((entry.stat().st_size, str(entry)))
        except OSError:
            continue
    found.sort(reverse=True)
    return [path for _size, path in found]


def preserve_raw_capture(
    output_path, output_dir, label="", *, gaps=None, reason="",
) -> StagedCapture:
    """Move a failed capture's staging files somewhere they will not be reaped.

    Called when a live capture is interrupted or its finalization fails. The
    files are *moved* into ``<output>.rawcapture/`` alongside a JSON report, so
    later cleanup passes that delete ``.part`` files cannot destroy them and a
    salvage attempt has a stable place to read from.
    """
    staged = StagedCapture(gaps=gaps or CaptureGaps(), reason=str(reason or ""))
    sources = find_staging_files(output_dir, label)
    if not sources:
        return staged
    directory = staging_dir_for(output_path) or os.path.join(
        str(output_dir), f"{label or 'capture'}{STAGING_SUFFIX}"
    )
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return staged
    for source in sources:
        target = os.path.join(directory, os.path.basename(source))
        if os.path.abspath(source) == os.path.abspath(target):
            continue
        try:
            if os.path.exists(target):
                # A previous preserve already kept this file; keep the larger.
                if os.path.getsize(target) >= os.path.getsize(source):
                    os.unlink(source)
                    continue
                os.unlink(target)
            shutil.move(source, target)
        except OSError:
            continue
    staged.directory = directory
    staged.files = sorted(
        str(path) for path in Path(directory).iterdir()
        if path.is_file() and path.name != REPORT_NAME
    )
    staged.total_bytes = sum(
        os.path.getsize(path) for path in staged.files
        if os.path.isfile(path)
    )
    _write_report(staged)
    return staged


def _write_report(staged: StagedCapture) -> None:
    report = {
        "version": REPORT_VERSION,
        "reason": staged.reason,
        "total_bytes": staged.total_bytes,
        "files": [os.path.basename(path) for path in staged.files],
        "gaps": staged.gaps.to_dict(),
    }
    try:
        Path(staged.directory, REPORT_NAME).write_text(
            json.dumps(report, indent=2), encoding="utf-8",
        )
    except OSError:
        pass


def load_report(staging_dir) -> dict:
    """Read a staged capture's report, or ``{}`` when it is absent/invalid."""
    try:
        data = json.loads(
            Path(staging_dir, REPORT_NAME).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def list_staged_captures(output_dir) -> list[str]:
    """Return every preserved raw-capture directory under *output_dir*."""
    try:
        entries = list(Path(output_dir).iterdir())
    except (OSError, ValueError):
        return []
    return sorted(
        str(entry) for entry in entries
        if entry.is_dir() and entry.name.endswith(STAGING_SUFFIX)
    )


def salvage_target(staging_dir, container="mp4") -> str:
    """Return the new file a salvage pass writes, never an existing output."""
    base = str(staging_dir or "")
    if base.endswith(STAGING_SUFFIX):
        base = base[: -len(STAGING_SUFFIX)]
    suffix = str(container or "mp4").lstrip(".") or "mp4"
    return f"{base}.salvaged.{suffix}"


def build_salvage_command(staging_dir, target, *, ffmpeg="ffmpeg", concat_list=""):
    """Return the ffmpeg argv that remuxes staged fragments into *target*.

    A stream copy only: salvage must not re-encode, and must not touch the
    staged input. Raises ``ValueError`` when there is nothing to salvage.
    """
    listing = str(concat_list or os.path.join(staging_dir, "concat.txt"))
    if not os.path.isfile(listing):
        raise ValueError("No concat list was prepared for salvage")
    return [
        str(ffmpeg), "-hide_banner", "-nostdin", "-y",
        "-f", "concat", "-safe", "0", "-i", listing,
        "-c", "copy", "-movflags", "+faststart",
        str(target),
    ]


def write_concat_list(staging_dir) -> str:
    """Write an ffmpeg concat list over the staged fragments, in order.

    Returns the list path, or '' when the directory holds nothing usable.
    Fragments are ordered by any trailing number in their name so a numeric
    sequence reassembles correctly regardless of lexical padding.
    """
    try:
        entries = [
            path for path in Path(staging_dir).iterdir()
            if path.is_file() and path.name not in {REPORT_NAME, "concat.txt"}
        ]
    except (OSError, ValueError):
        return ""
    if not entries:
        return ""

    def sort_key(path: Path):
        numbers = re.findall(r"(\d+)", path.name)
        return (int(numbers[-1]) if numbers else 0, path.name)

    entries.sort(key=sort_key)
    listing = Path(staging_dir, "concat.txt")
    try:
        listing.write_text(
            "".join(
                "file '{}'\n".format(str(path).replace("'", r"'\''"))
                for path in entries
            ),
            encoding="utf-8",
        )
    except OSError:
        return ""
    return str(listing)
