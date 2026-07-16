import json
import os
from pathlib import Path
import subprocess
import sys
from io import StringIO
from unittest import mock

from streamkeep import cli


ROOT = Path(__file__).resolve().parents[1]


def _run_launcher(*args, appdata):
    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run(
        [sys.executable, str(ROOT / "StreamKeep.py"), *map(str, args)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_print_helpers_tolerate_windowed_build_without_stdout():
    with mock.patch.object(cli, "_get_output_stream", return_value=None):
        cli._print_line("ready")
        cli._print_progress("working")


def test_print_line_uses_available_stream():
    output = StringIO()
    with mock.patch.object(cli, "_get_output_stream", return_value=output):
        cli._print_line("ready")
    assert output.getvalue() == "ready\n"


def test_db_command_dispatches_headlessly_and_binds_config_root(tmp_path):
    config_dir = tmp_path / "isolated"
    result = _run_launcher(
        "db", "info", "--config-dir", config_dir,
        appdata=tmp_path / "ambient-appdata",
    )

    assert result.returncode == 0, result.stderr
    diagnostics = json.loads(result.stdout)
    assert Path(diagnostics["path"]) == config_dir / "library.db"
    assert (config_dir / "library.db").is_file()
    assert not (tmp_path / "ambient-appdata" / "StreamKeep").exists()


def test_snapshot_command_accepts_config_root_before_subcommand(tmp_path):
    config_dir = tmp_path / "isolated"
    output = tmp_path / "diagnostic.zip"
    result = _run_launcher(
        "--config-dir", config_dir, "snapshot", "--output", output,
        appdata=tmp_path / "ambient-appdata",
    )

    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert "Snapshot: OK" in result.stdout
    assert not (tmp_path / "ambient-appdata" / "StreamKeep").exists()


def test_backup_command_is_headless_and_secret_free(tmp_path):
    config_dir = tmp_path / "isolated"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"theme": "dark", "hf_token": "must-not-export"}),
        encoding="utf-8",
    )
    output = tmp_path / "ordinary.skbackup"
    result = _run_launcher(
        "backup", "create", output, "--config-dir", config_dir,
        appdata=tmp_path / "ambient-appdata",
    )

    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert b"must-not-export" not in output.read_bytes()
    assert "Backup created" in result.stdout
    assert not (tmp_path / "ambient-appdata" / "StreamKeep").exists()


def test_server_cli_never_accepts_or_prints_bearer_tokens_in_argv():
    source = (ROOT / "streamkeep" / "cli.py").read_text(encoding="utf-8")
    assert 'add_argument("--token"' not in source
    assert '_print_line(f"Token:' not in source
    assert 'add_argument("--token-stdin"' not in source
    assert 'add_argument("--pairing-code-stdout"' in source
    assert "--trusted-proxy-origin" in source
    server_source = source[
        source.index("def _run_server("):source.index("# ── --list-extractors")
    ]
    assert "_print_line(server.token)" not in server_source
    assert 'f"{server.token}' not in server_source


def test_download_parser_exposes_format_container_and_audio_controls():
    args = cli.build_parser().parse_args([
        "download", "https://example.com/watch",
        "--format", "137+251",
        "--format-sort-preset", "prefer-av1",
        "--container", "mkv",
    ])
    assert args.format_spec == "137+251"
    assert args.format_sort_preset == "prefer-av1"
    assert args.container == "mkv"

    audio_args = cli.build_parser().parse_args([
        "download", "https://example.com/watch",
        "--audio-format", "opus", "--audio-quality", "128K",
    ])
    assert audio_args.audio_format == "opus"
    assert audio_args.audio_quality == "128K"

    subtitle_args = cli.build_parser().parse_args([
        "download", "https://example.com/watch",
        "--sub-langs", "en,es", "--auto-subs",
        "--convert-subs", "srt", "--sub-delivery", "sidecar",
    ])
    assert subtitle_args.sub_langs == "en,es"
    assert subtitle_args.auto_subs is True
    assert subtitle_args.convert_subs == "srt"
    assert subtitle_args.sub_delivery == "sidecar"

    sponsor_args = cli.build_parser().parse_args([
        "download", "https://example.com/watch",
        "--sponsorblock-mark", "intro,chapter",
        "--sponsorblock-remove", "sponsor",
        "--sponsorblock-api", "https://sponsor.example/api",
    ])
    assert sponsor_args.sponsorblock_mark == "intro,chapter"
    assert sponsor_args.sponsorblock_remove == "sponsor"

    template_args = cli.build_parser().parse_args([
        "download", "https://example.com/watch",
        "--arg-template", "Authenticated archive",
    ])
    assert template_args.arg_template == "Authenticated archive"
    assert sponsor_args.sponsorblock_api == "https://sponsor.example/api"

    transfer_args = cli.build_parser().parse_args([
        "download", "https://example.com/watch",
        "-N", "4", "--retries", "8",
        "--fragment-retries", "infinite",
        "--retry-sleep", "fragment:exp=1:20",
        "--unavailable-fragments", "abort",
        "--throttled-rate", "250K",
        "--live-from-start", "--wait-for-video", "30-120",
        "--embed-chapters", "--no-embed-metadata", "--embed-thumbnail",
    ])
    assert transfer_args.concurrent_fragments == 4
    assert transfer_args.fragment_retries == "infinite"
    assert transfer_args.live_from_start is True
    assert transfer_args.embed_chapters is True
    assert transfer_args.embed_metadata is False
    assert transfer_args.embed_thumbnail is True
