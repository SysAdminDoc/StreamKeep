"""Focused DASH addressing and URL-policy tests."""

import ast
import ipaddress
from pathlib import Path

import pytest

from streamkeep.dash import parse_mpd_xml
from streamkeep.models import MediaTrackInfo
from streamkeep.net_guard import RemoteURLPolicyError
from streamkeep.workers.download import DownloadWorker


FIXTURES = Path(__file__).parent / "fixtures" / "manifests"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _public_fixture_dns(monkeypatch):
    """Keep parser URL-policy tests deterministic and offline."""
    def resolve(host, _port):
        try:
            return (ipaddress.ip_address(host),)
        except ValueError:
            return (ipaddress.ip_address("93.184.216.34"),)

    monkeypatch.setattr(
        "streamkeep.net_guard.resolve_host_addresses", resolve,
    )


def test_segment_timeline_expands_repeats_and_dash_templates():
    qualities = parse_mpd_xml(
        _read("segment_timeline.mpd"),
        "https://cdn.example.com/manifests/main.mpd",
    )

    assert len(qualities) == 1
    quality = qualities[0]
    assert quality.format_type == "dash"
    assert quality.initialization_url == (
        "https://cdn.example.com/manifests/video/v1/init.mp4"
    )
    assert [segment.number for segment in quality.segments] == [7, 8, 9, 10]
    assert [(segment.start, segment.duration) for segment in quality.segments] == [
        (0, 2000), (2000, 2000), (4000, 2000), (6000, 1000),
    ]
    assert quality.segment_urls == [
        "https://cdn.example.com/manifests/video/v1/007-0.m4s",
        "https://cdn.example.com/manifests/video/v1/008-2000.m4s",
        "https://cdn.example.com/manifests/video/v1/009-4000.m4s",
        "https://cdn.example.com/manifests/video/v1/010-6000.m4s",
    ]


def test_segment_base_resolves_single_file_and_index_ranges():
    qualities = parse_mpd_xml(
        _read("segment_base.mpd"),
        "https://cdn.example.com/manifests/main.mpd",
    )

    assert len(qualities) == 1
    quality = qualities[0]
    assert quality.url == "https://cdn.example.com/manifests/media/video.mp4"
    assert quality.index_range == "800-1599"
    assert quality.initialization_url == quality.url
    assert quality.initialization_range == "0-799"
    assert quality.segment_urls == [quality.url]
    assert quality.segments[0].index_range == "800-1599"
    assert quality.tracks[0].index_range == "800-1599"
    assert quality.tracks[0].initialization_range == "0-799"


def test_segment_base_index_range_is_carried_to_ffmpeg_input(tmp_path):
    worker = DownloadWorker(
        "https://cdn.example.com/manifests/main.mpd",
        [(0, "capture", 0, 30)],
        str(tmp_path),
        "dash",
    )
    worker.selected_tracks = [MediaTrackInfo(
        kind="video",
        url="https://cdn.example.com/manifests/media/video.mp4",
        index_range="800-1599",
    )]

    command = worker.build_export_argv()
    headers = command[command.index("-headers") + 1]
    assert "Range: bytes=800-1599" in headers
    assert command.count("-i") == 1


@pytest.mark.parametrize(
    "base_url, expected_reason",
    [
        ("file:///tmp/manifest.mpd", r"HTTP\(S\)"),
        ("ftp://cdn.example.com/manifest.mpd", r"HTTP\(S\)"),
        ("http://127.0.0.1:8080/manifest.mpd", "allowed"),
    ],
)
def test_manifest_base_url_crosses_remote_policy(base_url, expected_reason):
    with pytest.raises(RemoteURLPolicyError, match=expected_reason):
        parse_mpd_xml("<MPD/>", base_url)


def test_baseurl_derived_uri_crosses_remote_policy():
    manifest = """
    <MPD>
      <Period>
        <AdaptationSet mimeType="video/mp4">
          <Representation id="unsafe">
            <BaseURL>file:///tmp/stream.mp4</BaseURL>
          </Representation>
        </AdaptationSet>
      </Period>
    </MPD>
    """
    with pytest.raises(RemoteURLPolicyError, match=r"HTTP\(S\)"):
        parse_mpd_xml(manifest, "https://cdn.example.com/main.mpd")


def test_manifest_modules_have_no_bare_remote_url_joins():
    root = Path(__file__).parents[1] / "streamkeep"
    for name in ("dash.py", "hls.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        bare_joins = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "urljoin"
        ]
        assert not bare_joins, f"{name} contains an unguarded urljoin"
