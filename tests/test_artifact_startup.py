import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("fixture", "expected_count"),
    [("empty", 0), ("migrated", 1), ("populated", 1)],
)
def test_source_startup_contract_is_offscreen_and_isolated(
    tmp_path, fixture, expected_count
):
    config_dir = tmp_path / fixture / "config"
    ready_file = tmp_path / fixture / "ready.json"
    ambient_appdata = tmp_path / "ambient-appdata"
    env = os.environ.copy()
    env["APPDATA"] = str(ambient_appdata)
    env["QT_QPA_PLATFORM"] = "windows"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "StreamKeep.py"),
            "startup-check",
            "--config-dir", str(config_dir),
            "--ready-file", str(ready_file),
            "--fixture", fixture,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    marker = json.loads(ready_file.read_text(encoding="utf-8"))
    assert marker["ready"] is True
    assert marker["frozen"] is False
    assert marker["visible_top_level_widgets"] == 0
    assert marker["qt_application_instances"] == 1
    assert marker["application_windows"] == 1
    assert marker["history_loaded"] == expected_count
    assert marker["history_table_rows"] == expected_count
    assert marker["checks"]["embedded_ytdlp_available"] is True
    assert marker["checks"]["embedded_ytdlp_runner"] is True
    assert marker["checks"]["thumbnail_loader_initialized"] is True
    if expected_count:
        assert marker["thumbnail_rendered"] is True
    assert not (ambient_appdata / "StreamKeep").exists()
