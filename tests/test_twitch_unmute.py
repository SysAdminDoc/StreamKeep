from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from streamkeep.twitch_unmute import rewrite_twitch_vod_playlist
from streamkeep.workers.download import DownloadWorker


FIXTURES = Path(__file__).parent / "fixtures" / "manifests"


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_reachable_unmuted_fragment_is_substituted_and_query_is_preserved():
    body = _fixture("twitch_muted.m3u8")
    probes = []

    result = rewrite_twitch_vod_playlist(
        body,
        "https://vod.example.com/v123/720p30/index-dvr.m3u8?token=abc",
        probe=lambda url: probes.append(url) or True,
    )

    assert result.is_endlist
    assert result.muted_segment_count == 1
    assert result.restored_segment_count == 1
    assert result.unavailable_segment_count == 0
    assert result.probe_count == 1
    assert probes == [
        "https://vod.example.com/v123/720p30/segment-101.ts?token=abc"
    ]
    assert "segment-101-muted.ts" not in result.rewritten_body
    assert "segment-101.ts?token=abc" in result.rewritten_body


def test_unavailable_unmuted_fragment_keeps_the_muted_uri():
    body = _fixture("twitch_muted.m3u8")

    result = rewrite_twitch_vod_playlist(
        body,
        "https://vod.example.com/v123/720p30/index-dvr.m3u8",
        probe=lambda _url: False,
    )

    assert result.muted_segment_count == 1
    assert result.restored_segment_count == 0
    assert result.unavailable_segment_count == 1
    assert "segment-101-muted.ts?token=abc" in result.rewritten_body
    assert "segment-101.ts?token=abc" not in result.rewritten_body


def test_live_playlist_is_never_rewritten():
    body = _fixture("twitch_muted.m3u8").replace("#EXT-X-ENDLIST\n", "")
    result = rewrite_twitch_vod_playlist(
        body,
        "https://vod.example.com/v123/720p30/index-dvr.m3u8",
        probe=lambda _url: (_ for _ in ()).throw(
            AssertionError("live playlists must not probe muted fragments")
        ),
    )

    assert result.is_endlist is False
    assert result.muted_segment_count == 0
    assert result.rewritten_body == body


def test_worker_applies_opt_in_unmute_through_the_guarded_probe(tmp_path):
    worker = DownloadWorker(
        "https://vod.example.com/v123/720p30/index-dvr.m3u8",
        [(0, "capture", 0, 18)],
        str(tmp_path),
        "hls",
    )
    worker.source_platform = "Twitch"
    worker.twitch_unmute = True
    worker.log = mock.Mock()
    guarded_proxy = SimpleNamespace(
        url="http://127.0.0.1:54321",
        stop=mock.Mock(),
    )
    worker._guarded_proxy = guarded_proxy
    worker._guarded_transport_ready = True

    with mock.patch(
        "streamkeep.workers.download.guarded_curl",
        return_value=_fixture("twitch_muted.m3u8"),
    ), mock.patch(
        "streamkeep.hls.validate_hls_manifest",
        return_value=None,
    ), mock.patch(
        "streamkeep.workers.download.http_head_details",
        return_value={"status": 206},
    ) as probe:
        assert worker._prepare_twitch_ssai_manifest()

    staged = Path(worker._twitch_ssai_manifest_path)
    assert staged.exists()
    staged_body = staged.read_text(encoding="utf-8")
    assert "segment-101-muted.ts" not in staged_body
    assert "segment-101.ts?token=abc" in staged_body
    probe.assert_called_once_with(
        "https://vod.example.com/v123/720p30/segment-101.ts?token=abc",
        timeout=10,
        guarded_proxy_url="http://127.0.0.1:54321",
    )
    assert any("restored 1/1" in call.args[0] for call in worker.log.emit.call_args_list)

    worker._stop_guarded_transport()
    assert not staged.exists()
    guarded_proxy.stop.assert_called_once()


def test_cli_exposes_the_opt_in_unmute_toggle():
    from streamkeep.cli import build_parser

    args = build_parser().parse_args([
        "download", "https://www.twitch.tv/videos/123", "--twitch-unmute",
    ])

    assert args.twitch_unmute is True
