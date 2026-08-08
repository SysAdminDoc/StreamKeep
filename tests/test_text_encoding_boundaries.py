"""Text I/O must name its encoding rather than inherit the locale's.

``subprocess.run(..., text=True)`` with no ``encoding=`` decodes using the
locale encoding with ``errors='strict'``. On Windows that is cp1252, and this
app never enables UTF-8 mode, so any subprocess output holding a byte sequence
invalid in cp1252 raises ``UnicodeDecodeError``. The worst case was guaranteed
rather than theoretical: ``transcribe_worker`` decodes transcript text, so
transcribing non-Latin-1 speech crashed the worker, and ``processor`` and
``workers/download`` decode ffprobe and yt-dlp output carrying titles and paths.

In the write direction the same default refuses to encode a non-cp1252 filename,
and ffmpeg's concat demuxer expects UTF-8 regardless of what the locale says.

Nineteen subprocess sites and two concat writers were missing it while four
sibling call sites had it right, so the convention is asserted here instead of
being left to per-site discipline (V218).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SEARCH_DIRS = ("streamkeep", "packaging")
_DECODING_KWARGS = {"text", "universal_newlines"}
_SUBPROCESS_CALLS = {"run", "Popen", "check_output", "call", "check_call"}


def _python_files():
    for directory in _SEARCH_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            yield path


def _relative(path):
    return str(path.relative_to(ROOT)).replace("\\", "/")


def test_every_subprocess_that_decodes_text_names_its_encoding():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name not in _SUBPROCESS_CALLS:
                continue
            owner = getattr(getattr(func, "value", None), "id", "")
            if owner not in ("subprocess", ""):
                continue
            if owner == "" and name not in ("run", "Popen"):
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            if kwargs & _DECODING_KWARGS and "encoding" not in kwargs:
                offenders.append(f"{_relative(path)}:{node.lineno}")
    assert not offenders, (
        "these subprocess calls decode text using the locale encoding, which "
        "is cp1252 on Windows and raises UnicodeDecodeError on non-Latin-1 "
        "output; pass encoding=\"utf-8\", errors=\"replace\": "
        + ", ".join(offenders)
    )


def test_no_text_mode_open_relies_on_the_locale_encoding():
    """Writing a media path with the locale encoding refuses non-cp1252 names."""
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "open"):
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            if "encoding" in kwargs:
                continue
            # ``open(path, mode, **kwargs)`` may supply the encoding
            # dynamically -- update_runtime._atomic_write does exactly that,
            # choosing binary or utf-8 text by payload type. AST cannot decide
            # it either way, so a ** unpacking is not counted.
            if any(keyword.arg is None for keyword in node.keywords):
                continue
            mode = ""
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value or "")
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = str(keyword.value.value or "")
            # Binary modes carry no encoding; a default mode is text.
            if "b" in mode:
                continue
            offenders.append(f"{_relative(path)}:{node.lineno}")
    assert not offenders, (
        "these open() calls use the locale encoding for text I/O; pass "
        "encoding=\"utf-8\": " + ", ".join(offenders)
    )


def test_every_concat_writer_agrees():
    """All four ffmpeg concat listings must be written the same way."""
    writers = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        if "concat" not in text:
            continue
        if 'file \'{}\'' in text or "file '{escaped}'" in text:
            writers.append((_relative(path), text))
    assert len(writers) >= 4, (
        f"expected at least 4 concat writers, found {[n for n, _ in writers]}"
    )
    for name, text in writers:
        assert 'encoding="utf-8"' in text, (
            f"{name} writes an ffmpeg concat listing without utf-8; the "
            "demuxer expects UTF-8 whatever the locale says"
        )
