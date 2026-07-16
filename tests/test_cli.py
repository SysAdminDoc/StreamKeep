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
