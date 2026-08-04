from io import StringIO
import plistlib
from unittest import mock

import pytest

from streamkeep import cli
from streamkeep.protocol import (
    LINUX_MIME_TYPE,
    PROTOCOL_SCHEME,
    build_bookmarklet,
    is_protocol_uri,
    linux_protocol_desktop_entry,
    macos_protocol_info_plist,
    macos_protocol_launcher,
    parse_streamkeep_uri,
    register_protocol,
    register_linux_protocol,
    register_macos_protocol,
    unregister_linux_protocol,
    unregister_macos_protocol,
    windows_protocol_registry_plan,
)


@pytest.mark.parametrize("text,expected", [
    ("streamkeep://download?url=https://x.example/v", True),
    ("STREAMKEEP:https://x.example/v", True),
    ("  streamkeep://x  ", True),
    ("https://x.example/v", False),
    ("", False),
    (None, False),
])
def test_is_protocol_uri(text, expected):
    assert is_protocol_uri(text) is expected


TARGET = "https://www.youtube.com/watch?v=abc123&t=5"


@pytest.mark.parametrize("uri", [
    # Percent-encoded query form (what the bookmarklet emits) and the
    # bare-URL forms all recover the full target, including its own query.
    "streamkeep://download?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc123%26t%3D5",
    "streamkeep://download/" + TARGET,
    "streamkeep://" + TARGET,
    "streamkeep:" + TARGET,
])
def test_parse_streamkeep_uri_forms_all_recover_target(uri):
    request = parse_streamkeep_uri(uri)
    assert request["action"] == "download"
    assert request["url"] == TARGET


def test_parse_unencoded_query_form_recovers_simple_url():
    # An unencoded ?url= value without embedded '&' is recovered as-is;
    # bookmarklets percent-encode, so ampersand-bearing URLs use that form.
    request = parse_streamkeep_uri(
        "streamkeep://download?url=https://x.example/watch/v0"
    )
    assert request["url"] == "https://x.example/watch/v0"


def test_parse_streamkeep_uri_reads_quality_hint():
    request = parse_streamkeep_uri(
        "streamkeep://download?url=https://x.example/v&quality=best"
    )
    assert request["url"] == "https://x.example/v"
    assert request["quality"] == "best"


def test_parse_streamkeep_uri_drops_unknown_quality():
    request = parse_streamkeep_uri(
        "streamkeep://download?url=https://x.example/v&quality=4k"
    )
    assert request["quality"] == ""


@pytest.mark.parametrize("uri", [
    "https://x.example/v",                                  # not our scheme
    "streamkeep://download?url=",                           # empty target
    "streamkeep://download?url=ftp://x.example/f",          # non-HTTP target
    "streamkeep://download?url=file:///etc/passwd",         # local scheme
    "streamkeep://download?url=https://user:pw@x.example/", # credentials
    "streamkeep://download",                                # no url at all
    "streamkeep://javascript:alert(1)",                     # not HTTP(S)
])
def test_parse_streamkeep_uri_rejects_unsafe(uri):
    with pytest.raises(ValueError):
        parse_streamkeep_uri(uri)


def test_build_bookmarklet_encodes_current_location():
    bookmarklet = build_bookmarklet()
    assert bookmarklet.startswith("javascript:")
    assert f"{PROTOCOL_SCHEME}://download?url=" in bookmarklet
    assert "encodeURIComponent(location.href)" in bookmarklet


def test_windows_registry_plan_declares_url_protocol_and_command():
    plan = windows_protocol_registry_plan(command='"C:\\SK.exe" "%1"')
    root = "Software\\Classes\\streamkeep"
    assert (root, "", "URL:streamkeep Protocol") in plan
    assert (root, "URL Protocol", "") in plan
    assert (
        root + "\\shell\\open\\command", "", '"C:\\SK.exe" "%1"'
    ) in plan


def test_linux_desktop_entry_declares_scheme_and_uri_field():
    entry = linux_protocol_desktop_entry(command='"/opt/StreamKeep" "%1"')
    assert "Exec=\"/opt/StreamKeep\" \"%u\"" in entry
    assert f"MimeType={LINUX_MIME_TYPE};" in entry


def test_linux_register_and_unregister_are_per_user_and_reversible(tmp_path, monkeypatch):
    monkeypatch.setattr("streamkeep.protocol.sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    completed = mock.Mock(returncode=0, stdout="", stderr="")
    with mock.patch("streamkeep.protocol.subprocess.run", return_value=completed) as run:
        ok, message = register_linux_protocol(command="streamkeep %1")
        assert ok, message
        desktop = tmp_path / "data" / "applications" / "streamkeep.desktop"
        assert desktop.is_file()
        assert f"MimeType={LINUX_MIME_TYPE};" in desktop.read_text(encoding="utf-8")
        assert run.call_args_list[0].args[0] == [
            "xdg-mime", "install", "--mode", "user", "--novendor",
            str(desktop),
        ]
        assert run.call_args_list[1].args[0] == [
            "xdg-mime", "default", "streamkeep.desktop", LINUX_MIME_TYPE,
        ]

        ok, message = unregister_linux_protocol()
        assert ok, message
        assert not desktop.exists()
        assert run.call_args_list[2].args[0] == [
            "xdg-mime", "uninstall", "--mode", "user", str(desktop),
        ]


def test_linux_registration_rolls_back_when_xdg_mime_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("streamkeep.protocol.sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    completed = mock.Mock(returncode=1, stdout="", stderr="xdg-mime failed")
    with mock.patch("streamkeep.protocol.subprocess.run", return_value=completed):
        ok, message = register_linux_protocol()
    assert not ok
    assert "xdg-mime" in message
    assert not (tmp_path / "data" / "applications" / "streamkeep.desktop").exists()


def test_macos_bundle_metadata_declares_cf_bundle_url_types():
    info = macos_protocol_info_plist()
    assert info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == [PROTOCOL_SCHEME]
    launcher = macos_protocol_launcher(command=["/usr/bin/python3", "/opt/StreamKeep.py"])
    assert 'exec /usr/bin/python3 /opt/StreamKeep.py "$@"' in launcher


def test_macos_register_and_unregister_use_launchservices(tmp_path, monkeypatch):
    monkeypatch.setattr("streamkeep.protocol.sys.platform", "darwin")
    monkeypatch.setattr(
        "streamkeep.protocol.macos_protocol_bundle_path",
        lambda: tmp_path / "StreamKeep.app",
    )
    completed = mock.Mock(returncode=0, stdout="", stderr="")
    with mock.patch("streamkeep.protocol._macos_lsregister", return_value="lsregister"), \
            mock.patch("streamkeep.protocol.subprocess.run", return_value=completed) as run:
        ok, message = register_macos_protocol(
            command=["/usr/bin/python3", "/opt/StreamKeep.py"],
        )
        assert ok, message
        plist = (tmp_path / "StreamKeep.app" / "Contents" / "Info.plist").read_bytes()
        assert plistlib.loads(plist)["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == [
            PROTOCOL_SCHEME,
        ]
        assert run.call_args.args[0][0:2] == ["lsregister", "-f"]

        ok, message = unregister_macos_protocol()
        assert ok, message
        assert not (tmp_path / "StreamKeep.app").exists()
        assert run.call_args.args[0][0:2] == ["lsregister", "-u"]


def test_unsupported_protocol_platform_has_named_refusal(monkeypatch):
    monkeypatch.setattr("streamkeep.protocol.sys.platform", "freebsd")
    ok, message = register_protocol()
    assert not ok
    assert "freebsd" in message


def test_cli_dispatches_protocol_uri_to_download():
    uri = "streamkeep://download?url=https://x.example/v&quality=best"
    with mock.patch.object(cli, "_run_download") as run_download, \
            mock.patch.object(cli, "setup_crash_logging", create=True):
        # setup_crash_logging is imported lazily; patch the module symbol path.
        with mock.patch("streamkeep.crash_log.setup_crash_logging"):
            cli.run_cli([uri])
    assert run_download.called
    args = run_download.call_args[0][0]
    assert args.command == "download"
    assert args.url == "https://x.example/v"
    assert args.quality == "best"


def test_cli_rejects_malformed_protocol_uri():
    with mock.patch("streamkeep.crash_log.setup_crash_logging"):
        with mock.patch.object(cli, "_get_output_stream", return_value=StringIO()):
            with pytest.raises(SystemExit) as exc:
                cli.run_cli(["streamkeep://download?url=ftp://x/f"])
    assert exc.value.code == 2


def test_cli_bookmarklet_command_prints_bookmarklet():
    output = StringIO()
    with mock.patch("streamkeep.crash_log.setup_crash_logging"):
        with mock.patch.object(cli, "_get_output_stream", return_value=output):
            cli.run_cli(["bookmarklet"])
    assert output.getvalue().startswith("javascript:")


def test_cli_protocol_registration_uses_cross_platform_dispatch():
    output = StringIO()
    with mock.patch("streamkeep.crash_log.setup_crash_logging"), \
            mock.patch.object(cli, "_get_output_stream", return_value=output), \
            mock.patch(
                "streamkeep.protocol.register_protocol",
                return_value=(True, "registered"),
            ) as register:
        cli.run_cli(["register-protocol"])
    register.assert_called_once_with()
    assert "registered" in output.getvalue()


def test_has_cli_args_recognizes_protocol_and_new_subcommands():
    for argv in (
        ["streamkeep://download?url=https://x/v"],
        ["import-har", "x.har"],
        ["register-protocol"],
        ["bookmarklet"],
    ):
        with mock.patch.object(cli.sys, "argv", ["StreamKeep.py", *argv]):
            assert cli.has_cli_args() is True
