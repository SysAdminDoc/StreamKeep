"""Tests for the optional gallery-dl second engine (V10)."""

import sys
from unittest import mock

import pytest

from streamkeep import cli
from streamkeep.integrations import gallery_dl


# ── URL classification ──────────────────────────────────────────────


def test_gallery_hosts_recognized():
    for url in (
        "https://twitter.com/user/status/123",
        "https://x.com/user",
        "https://www.instagram.com/p/abc/",
        "https://www.pixiv.net/en/artworks/123",
        "https://gelbooru.com/index.php?page=post&s=view&id=1",
        "https://danbooru.donmai.us/posts/1",
    ):
        assert gallery_dl.is_gallery_host(url), url


def test_non_gallery_hosts_ignored():
    for url in (
        "https://www.youtube.com/watch?v=abc",
        "https://kick.com/someone",
        "https://example.com/video.mp4",
        "",
        "not a url",
    ):
        assert not gallery_dl.is_gallery_host(url), url


# ── availability / prefix ───────────────────────────────────────────


def test_available_true_when_module_present(monkeypatch):
    monkeypatch.setattr(gallery_dl.importlib.util, "find_spec", lambda name: object())
    assert gallery_dl.gallery_dl_available() is True


def test_available_false_when_absent(monkeypatch):
    monkeypatch.setattr(gallery_dl.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(gallery_dl.shutil, "which", lambda name: None)
    assert gallery_dl.gallery_dl_available() is False


def test_command_prefix_prefers_module(monkeypatch):
    monkeypatch.setattr(gallery_dl.importlib.util, "find_spec", lambda name: object())
    assert gallery_dl.gallery_dl_command_prefix() == [sys.executable, "-m", "gallery_dl"]


def test_command_prefix_raises_when_absent(monkeypatch):
    monkeypatch.setattr(gallery_dl.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(gallery_dl.shutil, "which", lambda name: None)
    with pytest.raises(gallery_dl.GalleryDlUnavailable):
        gallery_dl.gallery_dl_command_prefix()


def test_install_hint_mentions_pip():
    assert "pip install" in gallery_dl.gallery_dl_install_hint()


# ── command builder ─────────────────────────────────────────────────


def _fixed_prefix(monkeypatch):
    monkeypatch.setattr(gallery_dl, "gallery_dl_command_prefix", lambda: ["gallery-dl"])


def test_command_builder_maps_all_options(monkeypatch):
    _fixed_prefix(monkeypatch)
    cmd = gallery_dl.build_gallery_dl_command(
        "https://x.com/user",
        r"D:\Downloads",
        archive_path=r"D:\archives\x.sqlite",
        cookies_file=r"D:\cookies.txt",
        proxy="http://127.0.0.1:8080",
        rate_limit="2M",
        simulate=True,
    )
    assert cmd[0] == "gallery-dl"
    assert cmd[-1] == "https://x.com/user"
    assert "--destination" in cmd and r"D:\Downloads" in cmd
    assert "--download-archive" in cmd and r"D:\archives\x.sqlite" in cmd
    assert "--cookies" in cmd and r"D:\cookies.txt" in cmd
    assert "--proxy" in cmd and "http://127.0.0.1:8080" in cmd
    assert "--limit-rate" in cmd and "2M" in cmd
    assert "--simulate" in cmd


def test_command_builder_rejects_leading_dash(monkeypatch):
    _fixed_prefix(monkeypatch)
    with pytest.raises(ValueError):
        gallery_dl.build_gallery_dl_command("-oProxy=evil", "D:\\x")


def test_command_builder_rejects_empty_url(monkeypatch):
    _fixed_prefix(monkeypatch)
    with pytest.raises(ValueError):
        gallery_dl.build_gallery_dl_command("   ", "D:\\x")


def test_command_builder_omits_unset_options(monkeypatch):
    _fixed_prefix(monkeypatch)
    cmd = gallery_dl.build_gallery_dl_command("https://x.com/u", "D:\\out")
    assert "--cookies" not in cmd
    assert "--proxy" not in cmd
    assert "--simulate" not in cmd
    assert cmd[-1] == "https://x.com/u"


# ── CLI dispatch ────────────────────────────────────────────────────


def test_cli_gallery_absent_dep_exits_with_hint(monkeypatch, capsys):
    monkeypatch.setattr(gallery_dl, "gallery_dl_available", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.run_cli(["gallery", "https://x.com/user"])
    assert exc.value.code == 1
    assert "gallery-dl is not installed" in capsys.readouterr().out


def test_cli_gallery_runs_engine_and_returns_rc(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(gallery_dl, "gallery_dl_available", lambda: True)
    monkeypatch.setattr(gallery_dl, "gallery_dl_command_prefix", lambda: ["gallery-dl"])
    captured = {}

    def _fake_run(cmd, check=False):
        captured["cmd"] = cmd
        return mock.Mock(returncode=0)

    monkeypatch.setattr("subprocess.run", _fake_run)
    with pytest.raises(SystemExit) as exc:
        cli.run_cli(["gallery", "https://x.com/user", "-o", str(tmp_path), "--simulate"])
    assert exc.value.code == 0
    cmd = captured["cmd"]
    assert cmd[0] == "gallery-dl"
    assert cmd[-1] == "https://x.com/user"
    assert "--simulate" in cmd
    assert str(tmp_path) in cmd
    assert "gallery-dl" in capsys.readouterr().out


def test_cli_gallery_is_a_headless_trigger():
    monkeypatch_argv = ["StreamKeep.py", "gallery", "https://x.com/u"]
    with mock.patch.object(sys, "argv", monkeypatch_argv):
        assert cli.has_cli_args() is True
