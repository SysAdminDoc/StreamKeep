"""Podcast sidecars auto-fetch at finalize (feed_url propagation)."""

from unittest import mock

from streamkeep.models import StreamInfo
from streamkeep.workers.finalize import FinalizeWorker


def _worker(task):
    w = FinalizeWorker.__new__(FinalizeWorker)  # skip QThread __init__
    w.task = dict(task)
    return w


def test_podcast_feed_url_requires_podcast_platform():
    w = _worker({})
    podcast = StreamInfo(platform="Podcast", url="https://cdn/ep.mp3", feed_url="https://f/rss")
    other = StreamInfo(platform="YouTube", url="https://y", feed_url="https://f/rss")
    assert w._podcast_feed_url({}, podcast) == "https://f/rss"
    assert w._podcast_feed_url({"feed_url": "https://f/rss", "platform": "Podcast"}, None) == "https://f/rss"
    assert w._podcast_feed_url({}, other) == ""


def test_planned_steps_includes_sidecars_only_for_podcast_feed():
    w = _worker({})
    podcast = StreamInfo(platform="Podcast", url="https://cdn/ep.mp3", feed_url="https://f/rss")
    steps = w._planned_steps({"record_manifest": False}, podcast, {})
    assert any(key == "sidecars" for _label, key in steps)

    plain = StreamInfo(platform="Podcast", url="https://cdn/ep.mp3")  # no feed
    steps = w._planned_steps({"record_manifest": False}, plain, {})
    assert not any(key == "sidecars" for _label, key in steps)


def test_run_podcast_sidecars_fetches_feed_and_syncs(tmp_path):
    w = _worker({"feed_url": "https://feed/rss", "platform": "Podcast"})
    w.log = mock.Mock()
    info = StreamInfo(platform="Podcast", url="https://cdn/ep1.mp3", feed_url="https://feed/rss")

    manifest = [{"kind": "transcript", "file": "ep1.vtt"}]
    with mock.patch("streamkeep.image_fetch.fetch_url_bytes", return_value=b"<rss/>") as fetch, \
         mock.patch("streamkeep.podcast_sidecars.sync_podcast_sidecars", return_value=manifest) as sync:
        w._run_podcast_sidecars(w.task, info, str(tmp_path), "ep1")

    fetch.assert_called_once()
    assert fetch.call_args.args[0] == "https://feed/rss"
    sync.assert_called_once()
    # enclosure URL, out_dir, and base are passed through to the sync helper.
    args = sync.call_args.args
    assert args[1] == "https://cdn/ep1.mp3"
    assert args[2] == str(tmp_path)
    assert args[3] == "ep1"


def test_run_podcast_sidecars_is_noop_without_feed(tmp_path):
    w = _worker({})
    w.log = mock.Mock()
    info = StreamInfo(platform="YouTube", url="https://y")
    with mock.patch("streamkeep.image_fetch.fetch_url_bytes") as fetch:
        w._run_podcast_sidecars(w.task, info, str(tmp_path), "x")
    fetch.assert_not_called()


def test_run_podcast_sidecars_survives_fetch_error(tmp_path):
    w = _worker({"feed_url": "https://feed/rss", "platform": "Podcast"})
    w.log = mock.Mock()
    info = StreamInfo(platform="Podcast", url="https://cdn/ep.mp3", feed_url="https://feed/rss")
    with mock.patch("streamkeep.image_fetch.fetch_url_bytes", side_effect=OSError("boom")):
        w._run_podcast_sidecars(w.task, info, str(tmp_path), "ep")  # must not raise
    assert w.log.emit.called


def test_queue_item_round_trips_feed_url():
    from streamkeep.ui.tabs.download_queue import DownloadQueueMixin

    normalize = DownloadQueueMixin._normalize_queue_item
    item = normalize(object(), {
        "url": "https://cdn.example.com/ep1.mp3",
        "platform": "Podcast",
        "vod_source": "https://cdn.example.com/ep1.mp3",
        "feed_url": "https://example.com/podcast.rss",
    })
    assert item["feed_url"] == "https://example.com/podcast.rss"
    # A second normalize pass (as on load) preserves it.
    assert normalize(object(), item)["feed_url"] == "https://example.com/podcast.rss"
    # Absent feed_url defaults to empty, never missing.
    plain = normalize(object(), {"url": "https://x", "platform": "Twitch"})
    assert plain["feed_url"] == ""
