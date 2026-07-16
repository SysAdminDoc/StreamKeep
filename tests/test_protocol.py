from io import StringIO
from unittest import mock

import pytest

from streamkeep import cli
from streamkeep.protocol import (
    PROTOCOL_SCHEME,
    build_bookmarklet,
    is_protocol_uri,
    parse_streamkeep_uri,
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


def test_has_cli_args_recognizes_protocol_and_new_subcommands():
    for argv in (
        ["streamkeep://download?url=https://x/v"],
        ["import-har", "x.har"],
        ["register-protocol"],
        ["bookmarklet"],
    ):
        with mock.patch.object(cli.sys, "argv", ["StreamKeep.py", *argv]):
            assert cli.has_cli_args() is True
