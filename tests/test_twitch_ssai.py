from pathlib import Path
import time
from types import SimpleNamespace
from unittest import mock

from streamkeep.twitch_ssai import (
    TwitchSSAIPlaylistRefresher,
    filter_twitch_ssai_playlist,
)
from streamkeep.workers.download import DownloadWorker


FIXTURES = Path(__file__).parent / "fixtures" / "manifests"


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_stitched_ad_daterange_removes_ads_and_keeps_resume_discontinuity():
    result = filter_twitch_ssai_playlist(
        _fixture("twitch_stitched_ad.m3u8"),
        "https://usher.ttvnw.net/vod/123.m3u8",
    )

    assert result.ad_segment_count == 2
    assert result.kept_segment_count == 2
    assert result.ad_duration == 12
    assert result.content_duration == 12
    assert "ad-101.ts" not in result.filtered_body
    assert "ad-102.ts" not in result.filtered_body
    assert "stitched-ad-123" not in result.filtered_body
    assert "Amazon commercial" not in result.filtered_body
    assert "content-100.ts" in result.filtered_body
    assert "content-103.ts" in result.filtered_body
    assert sum(
        line == "#EXT-X-DISCONTINUITY"
        for line in result.filtered_body.splitlines()
    ) == 1
    assert result.filtered_body.endswith("#EXT-X-ENDLIST\n")


def test_cue_out_and_cue_in_are_supported_without_false_plain_discontinuity():
    body = """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXTINF:4.0,
https://cdn.example.com/content-1.ts
#EXT-X-DISCONTINUITY
#EXT-X-CUE-OUT:DURATION=4
#EXTINF:4.0,
https://ads.example.com/ad-1.ts
#EXT-X-CUE-IN
#EXT-X-DISCONTINUITY
#EXTINF:4.0,
https://cdn.example.com/content-2.ts
#EXT-X-ENDLIST
"""

    result = filter_twitch_ssai_playlist(
        body, "https://usher.ttvnw.net/vod/123.m3u8",
    )

    assert result.ad_segment_count == 1
    assert "ad-1.ts" not in result.filtered_body
    assert "content-1.ts" in result.filtered_body
    assert "content-2.ts" in result.filtered_body
    assert sum(
        line == "#EXT-X-DISCONTINUITY"
        for line in result.filtered_body.splitlines()
    ) == 1


def test_low_latency_prefetch_after_discontinuity_is_filtered():
    body = """#EXTM3U
#EXT-X-TARGETDURATION:2
#EXTINF:2.0,
https://cdn.example.com/content-1.ts
#EXT-X-DISCONTINUITY
#EXT-X-TWITCH-PREFETCH:https://ads.example.com/ad-prefetch.ts
#EXT-X-TWITCH-PREFETCH:https://ads.example.com/ad-prefetch-2.ts
#EXT-X-TWITCH-LIVE-SEQUENCE:2
#EXT-X-DISCONTINUITY
#EXTINF:2.0,
https://cdn.example.com/content-2.ts
"""

    result = filter_twitch_ssai_playlist(
        body, "https://usher.ttvnw.net/api/channel/hls/test.m3u8",
    )

    assert result.ad_segment_count == 2
    assert "ad-prefetch.ts" not in result.filtered_body
    assert "ad-prefetch-2.ts" not in result.filtered_body
    assert "content-1.ts" in result.filtered_body
    assert "content-2.ts" in result.filtered_body
    assert result.is_live


def test_worker_stages_filtered_playlist_for_twitch_native_hls(tmp_path):
    worker = DownloadWorker(
        "https://usher.ttvnw.net/vod/123/720p.m3u8",
        [(0, "capture", 0, 12)],
        str(tmp_path),
        "hls",
    )
    worker.source_platform = "Twitch"
    guarded_proxy = SimpleNamespace(
        url="http://127.0.0.1:54321",
        stop=mock.Mock(),
    )
    worker._guarded_proxy = guarded_proxy
    worker._guarded_transport_ready = True

    with mock.patch(
        "streamkeep.workers.download.guarded_curl",
        return_value=_fixture("twitch_stitched_ad.m3u8"),
    ), mock.patch(
        "streamkeep.hls.validate_hls_manifest",
        return_value=None,
    ):
        assert worker._prepare_twitch_ssai_manifest()

    staged = Path(worker._twitch_ssai_manifest_path)
    assert staged.exists()
    command = worker._build_ffmpeg_download_cmd(
        str(tmp_path / "capture.mp4"), 0, 12, executable="ffmpeg",
    )
    assert command[command.index("-i") + 1] == str(staged)
    whitelist_index = [
        index for index, value in enumerate(command)
        if value == "-protocol_whitelist"
    ][-1]
    assert "file" in command[whitelist_index + 1]
    assert "ad-101.ts" not in staged.read_text(encoding="utf-8")

    worker._stop_guarded_transport()
    assert not staged.exists()
    guarded_proxy.stop.assert_called_once()


def test_worker_does_not_stage_non_twitch_hls(tmp_path):
    worker = DownloadWorker(
        "https://cdn.example.com/media.m3u8",
        [(0, "capture", 0, 12)],
        str(tmp_path),
        "hls",
    )
    worker.source_platform = "YouTube"
    worker._guarded_proxy = SimpleNamespace(url="http://127.0.0.1:54321")

    with mock.patch(
        "streamkeep.workers.download.guarded_curl",
        side_effect=AssertionError("non-Twitch job must not fetch SSAI"),
    ):
        assert worker._prepare_twitch_ssai_manifest()

    assert worker._twitch_ssai_manifest_path == ""


def test_live_refresher_rewrites_the_filtered_playlist(tmp_path):
    initial = _fixture("twitch_stitched_ad.m3u8").replace(
        "#EXT-X-ENDLIST\n", "",
    ).replace("#EXT-X-TARGETDURATION:6", "#EXT-X-TARGETDURATION:1")
    updated = initial.replace("content-103.ts", "content-104.ts")
    fetch = mock.Mock(return_value=updated)
    path = tmp_path / "twitch-live.m3u8"
    refresher = TwitchSSAIPlaylistRefresher(
        "https://usher.ttvnw.net/api/channel/hls/test.m3u8",
        "http://127.0.0.1:54321",
        str(path),
        fetch=fetch,
    )

    result = refresher.start(initial)
    assert result.is_live
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if "content-104.ts" in path.read_text(encoding="utf-8"):
            break
        time.sleep(0.05)
    refresher.stop()

    assert "content-104.ts" in path.read_text(encoding="utf-8")
    assert fetch.called
