"""Tests for the optional yt-dlp-ytse SABR plugin surface (V34)."""

from pathlib import Path
from types import SimpleNamespace

from streamkeep.integrations import ytse


def _distribution(*files):
    return SimpleNamespace(
        version="0.4.3",
        files=[Path(value) for value in files],
        locate_file=lambda value: Path(r"C:\Python\Lib") / value,
    )


def test_status_requires_the_sabr_plugin_files(monkeypatch):
    monkeypatch.setattr(
        ytse,
        "_distribution",
        lambda: _distribution(*ytse._SABR_FILES),
    )
    monkeypatch.setattr(ytse, "_module_probe", lambda: (False, ""))

    status = ytse.ytse_status()

    assert status["installed"] is True
    assert status["available"] is True
    assert status["version"] == "0.4.3"
    assert status["extractor_args"] == [
        "--extractor-args", "youtube:formats=sabr",
    ]


def test_ump_only_install_is_reported_but_never_used(monkeypatch):
    monkeypatch.setattr(
        ytse,
        "_distribution",
        lambda: _distribution(
            "yt_dlp_plugins/extractor/ytse.py",
            "yt_dlp_plugins/extractor/_ytse/ump.py",
        ),
    )
    monkeypatch.setattr(ytse, "_module_probe", lambda: (False, ""))

    status = ytse.ytse_status()

    assert status["installed"] is True
    assert status["available"] is False
    assert "SABR downloader is not available" in status["detail"]
    assert ytse.ytse_extractor_args("https://www.youtube.com/watch?v=x") == []


def test_sabr_args_are_scoped_to_youtube(monkeypatch):
    monkeypatch.setattr(
        ytse,
        "ytse_status",
        lambda: {"available": True},
    )

    assert ytse.ytse_extractor_args("https://youtu.be/x") == [
        "--extractor-args", "youtube:formats=sabr",
    ]
    assert ytse.ytse_extractor_args("https://example.com/video") == []


def test_documented_sabr_limits_are_explicit():
    blockers = ytse.ytse_fallback_blockers(
        download_sections="*00:10-00:20",
        concurrent_fragments=4,
        resume=True,
    )

    assert blockers == [
        "--download-sections is not supported by yt-dlp-ytse",
        "-N/--concurrent-fragments is not supported by yt-dlp-ytse",
        "resume is not supported by yt-dlp-ytse",
    ]

