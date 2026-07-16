"""Platform subtitle download + format normalization (F59).

Downloads platform-provided subtitles/closed captions alongside the video.
For yt-dlp-based downloads, passes ``--write-subs --sub-langs`` flags.
For native extractors, provides subtitle discovery + download helpers.

Normalizes TTML/SBV/VTT to SRT for maximum player compatibility.
"""

import html
import os
import re
from dataclasses import dataclass


# ── Format conversion ───────────────────────────────────────────────

def vtt_to_srt(vtt_text):
    """Convert WebVTT text to SRT format."""
    lines = vtt_text.strip().splitlines()
    # Skip WEBVTT header
    start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("WEBVTT"):
            start = i + 1
            continue
        if line.strip() == "" and i <= start:
            start = i + 1
            continue
        break

    srt_lines = []
    cue_idx = 1
    i = start
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            # Timestamp line: convert . to , for SRT
            ts_line = line.replace(".", ",")
            # Remove VTT position/alignment metadata after timestamp
            ts_line = re.sub(r"([\d:,]+\s*-->\s*[\d:,]+).*", r"\1", ts_line)
            srt_lines.append(str(cue_idx))
            srt_lines.append(ts_line)
            i += 1
            # Collect text lines until blank
            text_lines = []
            while i < len(lines) and lines[i].strip():
                # Strip VTT tags like <c> </c> <v Name>
                cleaned = re.sub(r"<[^>]+>", "", lines[i])
                text_lines.append(cleaned)
                i += 1
            srt_lines.extend(text_lines)
            srt_lines.append("")
            cue_idx += 1
        else:
            i += 1

    return "\n".join(srt_lines)


def ttml_to_srt(ttml_text):
    """Convert TTML/DFXP XML to SRT format (basic conversion)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(ttml_text)
    except ET.ParseError:
        return ""

    # Find all <p> elements (TTML paragraphs)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    paragraphs = root.iter(f"{ns}p") if ns else root.iter("p")

    srt_lines = []
    cue_idx = 1
    for p in paragraphs:
        begin = p.attrib.get("begin", "")
        end = p.attrib.get("end", "")
        dur = p.attrib.get("dur", "")
        if not begin:
            continue
        text = "".join(p.itertext()).strip()
        if not text:
            continue

        begin_srt = _ttml_time_to_srt(begin)
        if end:
            end_srt = _ttml_time_to_srt(end)
        elif dur:
            begin_secs = _ttml_time_to_secs(begin)
            dur_secs = _ttml_time_to_secs(dur)
            end_srt = _ttml_time_to_srt(f"{begin_secs + dur_secs:.3f}") if begin_secs >= 0 and dur_secs >= 0 else ""
        else:
            end_srt = ""
        if not begin_srt or not end_srt:
            continue

        srt_lines.append(str(cue_idx))
        srt_lines.append(f"{begin_srt} --> {end_srt}")
        srt_lines.append(text)
        srt_lines.append("")
        cue_idx += 1

    return "\n".join(srt_lines)


def _ttml_time_to_secs(t):
    """Convert TTML time to seconds as a float. Returns -1 on failure."""
    if not t:
        return -1
    m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})\.?(\d{0,3})", t)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        ms = int((m.group(4) or "0").ljust(3, "0"))
        return h * 3600 + mi * 60 + s + ms / 1000
    m = re.match(r"(\d+\.?\d*)", t)
    if m:
        return float(m.group(1))
    return -1


def _ttml_time_to_srt(t):
    """Convert TTML time (HH:MM:SS.mmm or SS.mmm) to SRT format (HH:MM:SS,mmm)."""
    if not t:
        return ""
    # Already in HH:MM:SS.mmm format
    m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})\.?(\d{0,3})", t)
    if m:
        h, mi, s, ms = m.group(1), m.group(2), m.group(3), m.group(4) or "000"
        return f"{int(h):02d}:{mi}:{s},{ms:0<3}"
    # Seconds format
    m = re.match(r"(\d+\.?\d*)", t)
    if m:
        total = float(m.group(1))
        h = int(total) // 3600
        mi = (int(total) % 3600) // 60
        s = int(total) % 60
        ms = int((total % 1) * 1000)
        return f"{h:02d}:{mi:02d}:{s:02d},{ms:03d}"
    return ""


# ── Subtitle file management ───────────────────────────────────────

def save_subtitle(output_dir, content, lang="en", fmt="srt"):
    """Save subtitle content to a file alongside the recording.

    Returns the written file path, or '' on error.
    """
    if not content or not output_dir:
        return ""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"subtitles.{lang}.{fmt}"
    path = os.path.join(output_dir, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path
    except OSError:
        return ""


def find_subtitles(recording_dir):
    """Find all subtitle files in a recording directory.

    Returns list of ``(path, lang, fmt)`` tuples.
    """
    if not recording_dir or not os.path.isdir(recording_dir):
        return []
    results = []
    for fn in os.listdir(recording_dir):
        fl = fn.lower()
        if fl.endswith((".srt", ".vtt", ".ass", ".sub")):
            # Try to extract language code from filename
            # e.g., "video.en.srt", "subtitles.es.vtt"
            parts = fn.rsplit(".", 2)
            lang = parts[-2] if len(parts) >= 3 and len(parts[-2]) <= 5 else ""
            fmt = fl.rsplit(".", 1)[-1]
            results.append((os.path.join(recording_dir, fn), lang, fmt))
    return results


def normalize_to_srt(file_path):
    """Convert a subtitle file to SRT format if needed. Returns SRT text."""
    if not file_path or not os.path.isfile(file_path):
        return ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return ""

    fl = file_path.lower()
    if fl.endswith(".vtt"):
        return vtt_to_srt(content)
    if fl.endswith((".ttml", ".dfxp", ".xml")):
        return ttml_to_srt(content)
    if fl.endswith(".srt"):
        return content
    return content


# ── yt-dlp subtitle flags ──────────────────────────────────────────

def ytdlp_sub_args(enabled=True, langs="en"):
    """Return yt-dlp command-line args for subtitle download.

    *langs* is a comma-separated language list (e.g., ``"en,es,ja"``).
    """
    if not enabled:
        return []
    args = [
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", langs or "en",
        "--convert-subs", "srt",
    ]
    return args


# ── Cue model, bilingual merge, and LRC export ─────────────────────

_SRT_TS = r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
_SRT_LINE_RE = re.compile(_SRT_TS + r"\s*-->\s*" + _SRT_TS)
# WebVTT allows minute-only (MM:SS.mmm) as well as HH:MM:SS.mmm.
_VTT_TS = r"(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})"
_VTT_LINE_RE = re.compile(_VTT_TS + r"\s*-->\s*" + _VTT_TS)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class SubtitleCue:
    start: float
    end: float
    text: str


def _clean_cue_text(text):
    return html.unescape(_TAG_RE.sub("", text)).strip()


def parse_srt(text):
    """Parse SRT text into a list of ``SubtitleCue`` (malformed cues skipped)."""
    cues = []
    for block in re.split(r"\n\s*\n", str(text or "").strip()):
        lines = block.strip().split("\n")
        ts_idx = match = None
        for i, line in enumerate(lines):
            match = _SRT_LINE_RE.search(line)
            if match:
                ts_idx = i
                break
        if match is None:
            continue
        g = [int(x) for x in match.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = _clean_cue_text(" ".join(lines[ts_idx + 1:]))
        if body:
            cues.append(SubtitleCue(start, end, body))
    return cues


def _vtt_secs(hours, minutes, seconds, millis):
    return (
        (int(hours) if hours else 0) * 3600
        + int(minutes) * 60 + int(seconds) + int(millis) / 1000
    )


def parse_vtt(text):
    """Parse WebVTT text into a list of ``SubtitleCue`` (spec timestamp forms)."""
    cues = []
    content = str(text or "").replace("﻿", "").strip()
    for block in re.split(r"\n\s*\n", content):
        lines = block.strip().split("\n")
        if not lines:
            continue
        if lines[0].strip().upper().startswith(
            ("WEBVTT", "NOTE", "STYLE", "REGION")
        ):
            continue
        ts_idx = match = None
        for i, line in enumerate(lines):
            match = _VTT_LINE_RE.match(line.strip())
            if match:
                ts_idx = i
                break
        if match is None:
            continue
        g = match.groups()
        start = _vtt_secs(g[0], g[1], g[2], g[3])
        end = _vtt_secs(g[4], g[5], g[6], g[7])
        body = _clean_cue_text(" ".join(lines[ts_idx + 1:]))
        if body:
            cues.append(SubtitleCue(start, end, body))
    return cues


def parse_subtitle_text(text, fmt):
    return parse_vtt(text) if str(fmt or "").lower().lstrip(".") == "vtt" else parse_srt(text)


def parse_subtitle_file(path):
    """Parse an .srt/.vtt file by extension into cues."""
    ext = str(path).lower().rsplit(".", 1)[-1]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return parse_subtitle_text(handle.read(), ext)
    except OSError:
        return []


def _fmt_srt_ts(secs):
    millis = int(round(max(0.0, float(secs)) * 1000))
    hours, millis = divmod(millis, 3600_000)
    minutes, millis = divmod(millis, 60_000)
    seconds, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _fmt_ass_ts(secs):
    cents = int(round(max(0.0, float(secs)) * 100))
    hours, cents = divmod(cents, 360_000)
    minutes, cents = divmod(cents, 6000)
    seconds, cents = divmod(cents, 100)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{cents:02d}"


def _fmt_lrc_ts(secs):
    cents = int(round(max(0.0, float(secs)) * 100))
    minutes, cents = divmod(cents, 6000)
    seconds, cents = divmod(cents, 100)
    return f"[{minutes:02d}:{seconds:02d}.{cents:02d}]"


def _overlap(a_start, a_end, b_start, b_end):
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def merge_bilingual_cues(primary, secondary):
    """Anchor on *primary* cues, stacking overlapping *secondary* text below.

    Deterministic overlap policy: each primary cue keeps its own timing and
    order; secondary cues whose window overlaps it (most-overlap first) are
    appended underneath. Cue order and count follow the primary track.
    """
    merged = []
    for cue in primary:
        matches = sorted(
            (
                (_overlap(cue.start, cue.end, s.start, s.end), idx, s)
                for idx, s in enumerate(secondary)
            ),
            key=lambda item: (-item[0], item[1]),
        )
        extra = [s.text for overlap, _idx, s in matches if overlap > 0]
        text = cue.text + ("\n" + " ".join(extra) if extra else "")
        merged.append(SubtitleCue(cue.start, cue.end, text))
    return merged


def render_srt(cues):
    """Render cues to SRT text."""
    parts = []
    for index, cue in enumerate(cues, start=1):
        parts.append(
            f"{index}\n{_fmt_srt_ts(cue.start)} --> {_fmt_srt_ts(cue.end)}\n"
            f"{cue.text}\n"
        )
    return "\n".join(parts)


_ASS_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "Collisions: Normal\n"
    "PlayResX: 1920\n"
    "PlayResY: 1080\n\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
    "MarginL, MarginR, MarginV, Encoding\n"
    "Style: Primary,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
    "0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1\n"
    "Style: Secondary,Arial,40,&H00A0E0FF,&H000000FF,&H00000000,&H00000000,"
    "0,1,0,0,100,100,0,0,1,2,1,2,10,10,90,1\n\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
    "Effect, Text\n"
)


def render_bilingual_ass(primary, secondary):
    """Render primary + secondary tracks to ASS with distinct styles so each
    language keeps its own placement and cue order."""
    lines = [_ASS_HEADER]

    def _dialogue(style, cue):
        text = cue.text.replace("\n", "\\N")
        return (
            f"Dialogue: 0,{_fmt_ass_ts(cue.start)},{_fmt_ass_ts(cue.end)},"
            f"{style},,0,0,0,,{text}\n"
        )

    for cue in primary:
        lines.append(_dialogue("Primary", cue))
    for cue in secondary:
        lines.append(_dialogue("Secondary", cue))
    return "".join(lines)


def export_lrc(cues, *, metadata=None):
    """Render cues to an LRC lyric file with validated monotonic timestamps.

    Cues are sorted by start time so timestamps never decrease. Optional
    *metadata* maps LRC ID tags (ti/ar/al/by/offset) to values.
    """
    lines = []
    for key in ("ti", "ar", "al", "by", "offset"):
        value = (metadata or {}).get(key)
        if value:
            lines.append(f"[{key}:{value}]")
    for cue in sorted(cues, key=lambda c: c.start):
        text = cue.text.replace("\n", " ").strip()
        if text:
            lines.append(f"{_fmt_lrc_ts(cue.start)}{text}")
    return "\n".join(lines) + ("\n" if lines else "")
