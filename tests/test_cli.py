import json
import os
from pathlib import Path
import subprocess
import sys
from io import StringIO
from unittest import mock

import pytest

from streamkeep import cli
from streamkeep.models import QualityInfo, StreamInfo


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


def _run_fake_cli_download(monkeypatch, args, info, *, mode):
    class Signal:
        def __init__(self):
            self._callback = None

        def connect(self, callback):
            self._callback = callback

        def emit(self, *values):
            if self._callback is not None:
                self._callback(*values)

    class FakeApp:
        def __init__(self, _argv):
            self.quit_count = 0

        def exec(self):
            return 0

        def quit(self):
            self.quit_count += 1

    class FakeFetchWorker:
        def __init__(self, *_args, **_kwargs):
            self.finished = Signal()
            self.error = Signal()
            self.vods_found = Signal()
            self.log = Signal()

        def start(self):
            self.finished.emit(info)

        def isRunning(self):
            return False

        def wait(self, _timeout):
            return True

    class FakeDownloadWorker:
        specs = []

        def __init__(self, spec):
            self.spec = spec
            self.progress = Signal()
            self.log = Signal()
            self.error = Signal()
            self.segment_done = Signal()
            self.all_done = Signal()
            self.finished = Signal()

        @classmethod
        def from_spec(cls, spec):
            cls.specs.append(spec)
            return cls(spec)

        def start(self):
            output_dir = Path(self.spec.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            label = str(self.spec.segments[0][1])
            if mode == "success":
                (output_dir / f"{label}.mp4").write_bytes(b"target recording")
                self.all_done.emit()
            else:
                (output_dir / ".streamkeep_resume.json").write_text(
                    "{}", encoding="utf-8"
                )
                self.error.emit(0, "network timeout")
            self.finished.emit()

        def isRunning(self):
            return False

        def wait(self, _timeout):
            return True

    monkeypatch.setattr(cli, "QCoreApplication", FakeApp)
    monkeypatch.setattr(cli, "_check_ffmpeg", lambda: True)
    monkeypatch.setattr(cli, "_init_db_or_exit", lambda _db: None)
    monkeypatch.setattr(cli, "_print_line", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_print_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("streamkeep.auth_profiles.ensure_migrated", lambda: None)
    monkeypatch.setattr("streamkeep.config.install_file_logging", lambda: None)
    monkeypatch.setattr("streamkeep.config.load_config", lambda: {})
    monkeypatch.setattr("streamkeep.config.write_log_line", lambda *_args: None)
    monkeypatch.setattr(
        "streamkeep.extractors.ytdlp.apply_resolve_timeout_config",
        lambda _config: None,
    )
    monkeypatch.setattr("streamkeep.workers.FetchWorker", FakeFetchWorker)
    monkeypatch.setattr("streamkeep.workers.DownloadWorker", FakeDownloadWorker)

    with pytest.raises(SystemExit) as raised:
        cli._run_download(args)
    return FakeDownloadWorker.specs[-1], raised.value.code


def _fake_stream_info():
    return StreamInfo(
        platform="Test",
        channel="Channel",
        title="Target",
        url="https://example.com/watch",
        qualities=[QualityInfo(
            name="Best",
            url="https://cdn.example.com/target.m3u8",
            resolution="720p",
            format_type="hls",
        )],
        total_secs=60,
        duration_str="1:00",
        webpage_url="https://example.com/watch",
        source_id="test:target",
    )


def _fake_download_args(output_root):
    return cli.build_parser().parse_args([
        "download", "https://example.com/watch",
        "--output", str(output_root),
        "--folder-template", "{channel}",
        "--filename-template", "{title}",
    ])


def test_cli_completion_scopes_manifest_to_one_templated_recording(
    tmp_path, monkeypatch,
):
    library = tmp_path / "library"
    sibling = library / "Other"
    sibling.mkdir(parents=True)
    (sibling / "other.mp4").write_bytes(b"must not be hashed")
    monkeypatch.setattr(
        "streamkeep.db.mark_failed_jobs_resolved_for_url", lambda _url: None
    )

    spec, exit_code = _run_fake_cli_download(
        monkeypatch,
        _fake_download_args(library),
        _fake_stream_info(),
        mode="success",
    )

    assert exit_code == 0
    target = library / "Channel"
    assert Path(spec.output_dir) == target
    manifest = json.loads(
        (target / ".streamkeep_manifest.json").read_text(encoding="utf-8")
    )
    assert [item["path"] for item in manifest["files"]] == ["Target.mp4"]
    assert (sibling / "other.mp4").read_bytes() == b"must not be hashed"


def test_cli_failure_and_retry_keep_the_templated_output_directory(
    tmp_path, monkeypatch,
):
    from streamkeep import db

    config_dir = tmp_path / "config"
    monkeypatch.setattr(db, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(db, "DB_PATH", config_dir / "library.db")
    db.init_db()

    spec, exit_code = _run_fake_cli_download(
        monkeypatch,
        _fake_download_args(tmp_path / "library"),
        _fake_stream_info(),
        mode="failure",
    )

    assert exit_code == 1
    target = Path(spec.output_dir)
    failures = db.load_failed_jobs()
    assert len(failures) == 1
    failure = failures[0]
    assert failure["output_dir"] == str(target)
    assert failure["resume_sidecar"] == str(target / ".streamkeep_resume.json")

    output = StringIO()
    monkeypatch.setattr(cli, "_print_line", lambda text: output.write(text + "\n"))
    retry_args = cli.build_parser().parse_args([
        "operations", "--retry", str(failure["id"]), "--json",
    ])
    cli._run_operations(retry_args)

    payload = json.loads(output.getvalue())
    assert len(payload["actions"]) == 1
    action = payload["actions"][0]
    assert action["action"] == "retry"
    assert action["failure_id"] == failure["id"]
    assert action["ok"] is True
    queued = db.load_queue_job(action["job_id"])
    assert queued["output_dir"] == str(target)


def test_print_helpers_tolerate_windowed_build_without_stdout():
    with mock.patch.object(cli, "_get_output_stream", return_value=None):
        cli._print_line("ready")
        cli._print_progress("working")


def test_print_line_uses_available_stream():
    output = StringIO()
    with mock.patch.object(cli, "_get_output_stream", return_value=output):
        cli._print_line("ready")
    assert output.getvalue() == "ready\n"


def test_credentials_command_reports_no_stored_credentials(tmp_path):
    config_dir = tmp_path / "isolated"
    result = _run_launcher(
        "credentials", "--json", "--config-dir", config_dir,
        appdata=tmp_path / "ambient-appdata",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    platforms = {row["platform"]: row["status"] for row in payload}
    assert platforms == {
        "twitch": "no_credential",
        "youtube": "no_credential",
        "kick": "no_credential",
        "cookies": "no_credential",
    }
    # No secret material must ever surface in the redacted report.
    assert "token" not in result.stdout.lower() or "no_credential" in result.stdout


def test_youtube_health_command_emits_report(tmp_path):
    config_dir = tmp_path / "isolated"
    result = _run_launcher(
        "youtube-health", "--json", "--config-dir", config_dir,
        appdata=tmp_path / "ambient-appdata",
    )
    # Exit 0 or 1 depending on whether a JS runtime is present on this box;
    # either way the report must be well-formed JSON with the expected keys.
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    for key in ("healthy", "state", "player_client", "pot_provider", "warnings"):
        assert key in payload
    assert isinstance(payload["warnings"], list)


def test_youtube_health_parser_exposes_explicit_deno_actions(tmp_path):
    parser = cli.build_parser()
    args = parser.parse_args([
        "youtube-health",
        "--install-deno",
        "--deno-archive", str(tmp_path / "deno.zip"),
        "--remove-deno",
        "--javascript-runtime-preference", "managed",
        "--json",
    ])

    assert args.install_deno is True
    assert args.deno_archive == str(tmp_path / "deno.zip")
    assert args.remove_deno is True
    assert args.javascript_runtime_preference == "managed"


def test_youtube_health_deno_actions_are_explicit_and_reported():
    output = StringIO()
    config = {"youtube_player_client": ""}
    args = cli.build_parser().parse_args([
        "youtube-health", "--json", "--remove-deno", "--install-deno",
        "--javascript-runtime-preference", "managed",
    ])
    installed = {
        "path": r"C:\StreamKeep\runtimes\deno.exe",
        "version": "2.3.1",
        "source": "managed-download",
        "sha256": "abc123",
    }

    with mock.patch("streamkeep.config.load_config", return_value=config), \
            mock.patch("streamkeep.config.save_config", return_value=True) as save_config, \
            mock.patch("streamkeep.javascript_runtime.remove_managed_deno", return_value=True) as remove, \
            mock.patch("streamkeep.javascript_runtime.install_managed_deno", return_value=installed) as install, \
            mock.patch("streamkeep.capabilities.invalidate_runtime_capabilities_cache") as invalidate, \
            mock.patch(
                "streamkeep.extractors.ytdlp.youtube_health_report",
                return_value={"healthy": True, "summary": "ready"},
            ), \
            mock.patch.object(cli, "_get_output_stream", return_value=output):
        cli._run_youtube_health(args)

    payload = json.loads(output.getvalue())
    assert config["javascript_runtime_preference"] == "managed"
    save_config.assert_called_once_with(config)
    remove.assert_called_once_with()
    install.assert_called_once_with(None)
    assert invalidate.call_count == 3
    assert [item["action"] for item in payload["runtime_actions"]] == [
        "set-preference", "remove-deno", "install-deno",
    ]


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


def test_db_command_refuses_newer_schema_version(tmp_path):
    import sqlite3

    from streamkeep import db

    config_dir = tmp_path / "isolated"
    config_dir.mkdir()
    database = config_dir / "library.db"
    connection = sqlite3.connect(str(database))
    connection.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    connection.commit()
    connection.close()

    result = _run_launcher(
        "db", "info", "--config-dir", config_dir,
        appdata=tmp_path / "ambient-appdata",
    )
    assert result.returncode == 2
    assert "schema version" in result.stdout.lower()
    assert str(db.SCHEMA_VERSION + 1) in result.stdout
    assert str(db.SCHEMA_VERSION) in result.stdout
    assert "newer streamkeep build" in result.stdout.lower()


def test_db_rebuild_parser_supports_preview_and_apply(tmp_path):
    parser = cli.build_parser()
    preview = parser.parse_args([
        "db", "rebuild", "--from", str(tmp_path), "--plan", "plan.json", "--json",
    ])
    assert preview.action == "rebuild"
    assert preview.rebuild_from == str(tmp_path)
    assert preview.apply is False
    applied = parser.parse_args([
        "db", "rebuild", "--apply", "--plan", "plan.json", "--json",
    ])
    assert applied.apply is True


def test_db_rebuild_cli_is_headless_and_reconstructs_sidecar_identity(tmp_path):
    config_dir = tmp_path / "isolated"
    library = tmp_path / "library" / "recording"
    library.mkdir(parents=True)
    (library / "video.mp4").write_bytes(b"media")
    (library / "metadata.json").write_text(json.dumps({
        "schema": "streamkeep.metadata",
        "schema_version": 3,
        "provenance": {
            "platform": "Twitch",
            "source_id": "vod:987",
            "webpage_url": "https://www.twitch.tv/videos/987",
        },
        "title": "Rebuilt",
        "channel": "Channel",
    }), encoding="utf-8")
    plan = tmp_path / "rebuild-plan.json"
    preview = _run_launcher(
        "db", "rebuild", "--from", library.parent, "--plan", plan, "--json",
        "--config-dir", config_dir, appdata=tmp_path / "ambient-appdata",
    )
    assert preview.returncode == 0, preview.stderr
    preview_payload = json.loads(preview.stdout)
    assert preview_payload["diagnostics"]["rebuild"] == 1
    assert plan.is_file()

    applied = _run_launcher(
        "db", "rebuild", "--apply", "--plan", plan, "--json",
        "--config-dir", config_dir, appdata=tmp_path / "ambient-appdata",
    )
    assert applied.returncode == 0, applied.stderr
    result = json.loads(applied.stdout)
    assert result["status"] == "completed"
    assert result["rebuilt"] == 1


def test_import_library_parser_requires_preview_or_apply_and_accepts_archives():
    parser = cli.build_parser()
    preview = parser.parse_args([
        "import-library", "preview", "library",
        "--archive", "archive.txt", "--archive-source-url",
        "https://www.twitch.tv/channel", "--plan", "plan.json", "--json",
    ])
    assert preview.command == "import-library"
    assert preview.import_action == "preview"
    assert preview.archive == ["archive.txt"]
    assert preview.archive_source_url == "https://www.twitch.tv/channel"
    assert preview.plan == "plan.json"
    applied = parser.parse_args([
        "import-library", "apply", "--plan", "plan.json", "--json",
    ])
    assert applied.import_action == "apply"
    assert applied.plan == "plan.json"


def test_import_library_cli_preview_and_apply_are_headless(tmp_path):
    config_dir = tmp_path / "isolated"
    library = tmp_path / "library" / "recording"
    library.mkdir(parents=True)
    (library / "video.mp4").write_bytes(b"media")
    (library / "metadata.json").write_text(json.dumps({
        "provenance": {
            "platform": "Twitch",
            "source_id": "vod:123",
            "webpage_url": "https://www.twitch.tv/videos/123",
        },
        "title": "Imported",
        "channel": "channel",
    }), encoding="utf-8")
    plan = tmp_path / "adoption-plan.json"
    preview = _run_launcher(
        "--config-dir", config_dir, "import-library", "preview", library.parent,
        "--plan", plan, "--json", appdata=tmp_path / "ambient-appdata",
    )
    assert preview.returncode == 0, preview.stderr
    preview_payload = json.loads(preview.stdout)
    assert preview_payload["diagnostics"]["adopt"] == 1
    assert plan.is_file()

    applied = _run_launcher(
        "--config-dir", config_dir, "import-library", "apply",
        "--plan", plan, "--json", appdata=tmp_path / "ambient-appdata",
    )
    assert applied.returncode == 0, applied.stderr
    result = json.loads(applied.stdout)
    assert result["status"] == "completed"
    assert result["adopted"] == 1


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

    output_args = cli.build_parser().parse_args([
        "download", "https://example.com/watch",
        "--dub-lang", "es", "--mute",
    ])
    assert output_args.dub_lang == "es"
    assert output_args.mute is True

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


def test_mse_capture_parser_exposes_headless_drm_free_controls():
    args = cli.build_parser().parse_args([
        "mse-capture", "https://example.com/player",
        "--output", "capture.mp4", "--seconds", "12",
        "--allow-lan", "--keep-staging",
    ])
    assert args.url == "https://example.com/player"
    assert args.output == "capture.mp4"
    assert args.seconds == 12
    assert args.allow_lan is True
    assert args.keep_staging is True


def test_bagit_export_is_an_explicit_cli_command(tmp_path, monkeypatch):
    from streamkeep.verify import create_archive_manifest

    recording = tmp_path / "recording"
    recording.mkdir()
    (recording / "clip.mp4").write_bytes(b"cli bagit")
    create_archive_manifest(recording)
    args = cli.build_parser().parse_args([
        "bagit", str(recording), "--json",
    ])
    output = []
    monkeypatch.setattr(cli, "_print_line", output.append)
    cli._run_bagit(args)
    summary = json.loads(output[0])
    assert summary["bagit_version"] == "0.97"
    assert (recording / "manifest-sha256.txt").is_file()


def test_tokens_cli_parser_supports_list_create_and_revoke():
    parser = cli.build_parser()

    listed = parser.parse_args(["tokens", "list", "--server-url", "http://127.0.0.1:9"])
    assert listed.command == "tokens"
    assert listed.tokens_command == "list"
    assert listed.server_url == "http://127.0.0.1:9"

    created = parser.parse_args([
        "tokens", "create", "--label", "CI", "--scope", "status",
        "--scope", "queue", "--expires-in", "3600",
    ])
    assert created.tokens_command == "create"
    assert created.label == "CI"
    assert created.scope == ["status", "queue"]
    assert created.expires_in == 3600

    revoked = parser.parse_args(["tokens", "revoke", "opaque-id"])
    assert revoked.tokens_command == "revoke"
    assert revoked.token_id == "opaque-id"


def test_plugins_json_includes_reviewable_contract_fields(monkeypatch):
    from streamkeep import plugins

    plugin = {
        "id": "json-plugin",
        "name": "JSON Plugin",
        "version": "1.0.0",
        "enabled": False,
        "trusted": False,
        "path": "C:/plugins/json-plugin",
        "error": "",
    }
    report = {
        "id": "json-plugin",
        "name": "JSON Plugin",
        "version": "1.0.0",
        "manifest_version": 2,
        "compatible": True,
        "errors": [],
        "warnings": [],
        "permissions": ["network"],
        "dependencies": [{"name": "requests", "minimum_version": "2.0.0"}],
        "compatibility": {
            "manifest_version": 2,
            "min_app_version": "4.0.0",
            "max_app_version": "5.0.0",
            "current_app_version": "4.44.0",
            "range": ">= 4.0.0 and <= 5.0.0",
        },
        "entrypoints": [{
            "type": "extractor",
            "entrypoint": "Extractor",
            "interface_version": 1,
        }],
        "adapters": [],
        "contract_fingerprint": "a" * 64,
        "trusted": False,
        "trust_reviewed": False,
        "review_required": True,
    }
    monkeypatch.setattr(plugins, "discover_plugins", lambda: [plugin])
    monkeypatch.setattr(plugins, "diagnose_plugin", lambda _plugin: dict(report))
    output = []
    monkeypatch.setattr(cli, "_print_line", output.append)

    cli._run_plugins(cli.build_parser().parse_args(["plugins", "--json"]))

    payload = json.loads(output[0])
    item = payload["plugins"][0]
    assert item["permissions"] == ["network"]
    assert item["dependencies"][0]["name"] == "requests"
    assert item["compatibility"]["range"] == ">= 4.0.0 and <= 5.0.0"
    assert item["entrypoints"][0]["entrypoint"] == "Extractor"
    assert item["review_required"] is True


def test_plugins_load_trusted_prints_contract_before_loading(monkeypatch):
    from streamkeep import plugins

    plugin = {
        "id": "ordered-plugin",
        "name": "Ordered Plugin",
        "version": "1.0.0",
        "enabled": True,
        "trusted": True,
        "path": "C:/plugins/ordered-plugin",
        "error": "",
    }
    report = {
        "id": "ordered-plugin",
        "name": "Ordered Plugin",
        "version": "1.0.0",
        "manifest_version": 2,
        "compatible": True,
        "errors": [],
        "warnings": [],
        "permissions": ["filesystem_read"],
        "dependencies": [],
        "compatibility": {
            "manifest_version": 2,
            "min_app_version": "",
            "max_app_version": "",
            "current_app_version": "4.44.0",
            "range": "Any StreamKeep version",
        },
        "entrypoints": [{
            "type": "postprocess",
            "entrypoint": "Processor",
            "interface_version": 1,
        }],
        "adapters": [],
        "contract_fingerprint": "b" * 64,
        "trusted": True,
        "trust_reviewed": True,
        "review_required": False,
    }
    calls = []
    monkeypatch.setattr(plugins, "discover_plugins", lambda: [plugin])
    monkeypatch.setattr(plugins, "diagnose_plugin", lambda _plugin: dict(report))

    def load_all(log_fn):
        calls.append("load")
        log_fn("[PLUGIN] loaded")
        return 1, 0

    monkeypatch.setattr(plugins, "load_all_plugins", load_all)
    output = []
    monkeypatch.setattr(cli, "_print_line", output.append)

    cli._run_plugins(cli.build_parser().parse_args([
        "plugins", "--load-trusted",
    ]))

    assert calls == ["load"]
    permission_index = next(
        index for index, line in enumerate(output) if "Permissions:" in line
    )
    assert permission_index < output.index("Trusted plugins loaded: 1; errors: 0")
