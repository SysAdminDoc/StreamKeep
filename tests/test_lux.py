"""Tests for the optional lux CN-platform fallback engine (V25)."""

from unittest import mock

import pytest

from streamkeep import cli
from streamkeep.integrations import lux


# ── URL classification ──────────────────────────────────────────────


def test_cn_platforms_recognized():
    for url in (
        "https://www.bilibili.com/video/BV1xx",
        "https://b23.tv/abc",
        "https://www.douyin.com/video/123",
        "https://v.youku.com/v_show/id_x.html",
        "https://v.qq.com/x/cover/abc.html",
        "https://www.acfun.cn/v/ac123",
    ):
        assert lux.is_cn_platform(url), url


def test_non_cn_platforms_ignored():
    for url in (
        "https://www.youtube.com/watch?v=abc",
        "https://x.com/user",
        "https://example.com/video.mp4",
        "",
        "garbage",
    ):
        assert not lux.is_cn_platform(url), url


# ── availability / prefix ───────────────────────────────────────────


def test_available_true_when_on_path(monkeypatch):
    monkeypatch.setattr(lux.shutil, "which", lambda name: r"C:\tools\lux.exe")
    assert lux.lux_available() is True
    assert lux.lux_command_prefix() == [r"C:\tools\lux.exe"]


def test_unavailable_raises(monkeypatch):
    monkeypatch.setattr(lux.shutil, "which", lambda name: None)
    assert lux.lux_available() is False
    with pytest.raises(lux.LuxUnavailable):
        lux.lux_command_prefix()


def test_install_hint_mentions_lux():
    hint = lux.lux_install_hint()
    assert "lux is not installed" in hint
    assert "iawia002/lux" in hint


# ── command builder ─────────────────────────────────────────────────


def _fixed_prefix(monkeypatch):
    monkeypatch.setattr(lux, "lux_command_prefix", lambda: ["lux"])


def test_command_builder_maps_options(monkeypatch):
    _fixed_prefix(monkeypatch)
    cmd = lux.build_lux_command(
        "https://www.bilibili.com/video/BV1xx",
        r"D:\Downloads",
        cookie=r"D:\cookies.txt",
        stream_format="dash-flv480",
        referer="https://www.bilibili.com/",
        info=True,
    )
    assert cmd[0] == "lux"
    assert cmd[-1] == "https://www.bilibili.com/video/BV1xx"
    assert "--output-path" in cmd and r"D:\Downloads" in cmd
    assert "--cookie" in cmd and r"D:\cookies.txt" in cmd
    assert "--stream-format" in cmd and "dash-flv480" in cmd
    assert "--refer" in cmd and "https://www.bilibili.com/" in cmd
    assert "--info" in cmd


def test_command_builder_rejects_leading_dash(monkeypatch):
    _fixed_prefix(monkeypatch)
    with pytest.raises(ValueError):
        lux.build_lux_command("-oevil", r"D:\x")


def test_command_builder_rejects_empty(monkeypatch):
    _fixed_prefix(monkeypatch)
    with pytest.raises(ValueError):
        lux.build_lux_command("  ", r"D:\x")


def test_command_builder_omits_unset(monkeypatch):
    _fixed_prefix(monkeypatch)
    cmd = lux.build_lux_command("https://www.douyin.com/video/1", r"D:\out")
    assert "--cookie" not in cmd
    assert "--info" not in cmd
    assert "--stream-format" not in cmd


# ── CLI dispatch ────────────────────────────────────────────────────


def test_cli_lux_absent_dep_exits_with_hint(monkeypatch, capsys):
    monkeypatch.setattr(lux, "lux_available", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.run_cli(["lux", "https://www.bilibili.com/video/BV1"])
    assert exc.value.code == 1
    assert "lux is not installed" in capsys.readouterr().out


def test_cli_lux_runs_engine_and_returns_rc(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(lux, "lux_available", lambda: True)
    monkeypatch.setattr(lux, "lux_command_prefix", lambda: ["lux"])
    captured = {}

    def _fake_run(cmd, check=False, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return mock.Mock(returncode=0)

    monkeypatch.setattr("subprocess.run", _fake_run)
    with pytest.raises(SystemExit) as exc:
        cli.run_cli(["lux", "https://www.bilibili.com/video/BV1", "-o", str(tmp_path), "--info"])
    assert exc.value.code == 0
    cmd = captured["cmd"]
    assert cmd[0] == "lux"
    assert cmd[-1] == "https://www.bilibili.com/video/BV1"
    assert "--info" in cmd
    assert str(tmp_path) in cmd
    assert "lux" in capsys.readouterr().out
