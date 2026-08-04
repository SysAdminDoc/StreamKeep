import base64
import hashlib
from unittest import mock

from streamkeep.extractors.podcast import PodcastRSSExtractor, parse_podcast_feed
from streamkeep.feed import generate_rss
from streamkeep.metadata import MetadataSaver, load_metadata_sidecar
from streamkeep.models import StreamInfo
from streamkeep.podcast_sidecars import verify_podcast_integrity


PAGE_ONE = """
<rss xmlns:podcast="https://podcastindex.org/namespace/1.0"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Fidelity Show</title>
    <podcast:guid>show-guid</podcast:guid>
    <podcast:medium>podcast</podcast:medium>
    <podcast:image href="https://img.example/show.jpg" purpose="artwork"/>
    <atom:link rel="next" href="https://feed.example/page-2.xml"/>
    <item>
      <title><![CDATA[Episode & One]]></title>
      <guid>episode-guid-1</guid>
      <enclosure url="https://cdn.example/episode-1.mp3" type="audio/mpeg"/>
      <itunes:duration>01:02:03</itunes:duration>
      <podcast:season name="Season Alpha">2</podcast:season>
      <podcast:episode display="Bonus">2.5</podcast:episode>
      <podcast:person role="guest" group="cast">Jane Doe</podcast:person>
      <podcast:soundbite startTime="12.5" duration="30">A clip</podcast:soundbite>
      <podcast:funding url="https://fund.example/show">Support the show</podcast:funding>
      <podcast:license url="https://license.example/show">CC-BY-4.0</podcast:license>
      <podcast:location rel="creator" country="US">Austin, TX</podcast:location>
      <podcast:txt purpose="verify">ownership-token</podcast:txt>
      <podcast:value type="lightning" method="keysend">
        <podcast:valueRecipient name="Host" address="abc" split="100"/>
      </podcast:value>
      <podcast:alternateEnclosure type="audio/mpeg" length="4">
        <podcast:source uri="https://cdn.example/episode-1.mp3" contentType="audio/mpeg"/>
        <podcast:integrity type="sha256" value="deadbeef"/>
      </podcast:alternateEnclosure>
      <podcast:image href="https://img.example/episode-1.jpg" purpose="artwork"/>
      <podcast:transcript url="https://cdn.example/episode-1.vtt" type="text/vtt" language="en"/>
      <podcast:chapters url="https://cdn.example/episode-1.json" type="application/json+chapters"/>
    </item>
  </channel>
</rss>
"""

PAGE_TWO = """
<rss xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <item>
      <title>Episode Two</title>
      <guid>episode-guid-2</guid>
      <enclosure url="https://cdn.example/episode-2.mp3" type="audio/mpeg"/>
    </item>
  </channel>
</rss>
"""


def test_podcast_parser_preserves_namespace_metadata_and_pagination():
    first = parse_podcast_feed(PAGE_ONE, "https://feed.example/page-1.xml")
    assert first["next_url"] == "https://feed.example/page-2.xml"
    item = first["items"][0]
    metadata = item["metadata"]
    assert item["guid"] == "episode-guid-1"
    assert item["artwork_url"].endswith("episode-1.jpg")
    assert metadata["season"] == {"number": 2, "name": "Season Alpha"}
    assert metadata["episode"] == {"number": 2.5, "display": "Bonus"}
    assert metadata["person"][0]["name"] == "Jane Doe"
    assert metadata["soundbite"][0]["start_time"] == 12.5
    assert metadata["funding"][0]["url"].startswith("https://")
    assert metadata["license"]["name"] == "CC-BY-4.0"
    assert metadata["location"][0]["country"] == "US"
    assert metadata["txt"][0]["purpose"] == "verify"
    assert "podcast:value" in metadata["value"][0]["raw_xml"]
    assert metadata["alternate_enclosures"][0]["sources"][0]["uri"].endswith(
        "episode-1.mp3"
    )
    assert metadata["sidecars"][0]["kind"] == "transcript"

    with mock.patch(
        "streamkeep.extractors.podcast.curl", side_effect=[PAGE_ONE, PAGE_TWO]
    ):
        vods, cursor = PodcastRSSExtractor().list_vods(
            "https://feed.example/page-1.xml"
        )
    assert cursor is None
    assert [vod.title for vod in vods] == ["Episode & One", "Episode Two"]
    assert vods[0].source_id != vods[1].source_id
    assert vods[0].feed_url == "https://feed.example/page-1.xml"
    assert vods[0].thumbnail_url.endswith("episode-1.jpg")


def test_podcast_integrity_hash_is_verified_and_mismatch_is_visible(tmp_path):
    media = tmp_path / "episode.mp3"
    data = b"podcast bytes"
    media.write_bytes(data)
    encoded = base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")
    alternate = [{
        "sources": [{"uri": "https://cdn.example/episode.mp3"}],
        "integrity": {"type": "sri", "value": f"sha256-{encoded}"},
    }]
    verified = verify_podcast_integrity(
        str(media), alternate, "https://cdn.example/episode.mp3?token=short-lived"
    )
    assert verified[0]["status"] == "verified"

    alternate[0]["integrity"]["value"] = "sha256-" + base64.b64encode(b"wrong").decode()
    mismatch = verify_podcast_integrity(
        str(media), alternate, "https://cdn.example/episode.mp3"
    )
    assert mismatch[0]["status"] == "mismatch"


def test_podcast_metadata_is_persisted_as_library_sidecar(tmp_path):
    info = StreamInfo(
        platform="Podcast",
        title="Episode",
        url="https://cdn.example/episode.mp3",
        source_id="episode:abc",
        webpage_url="https://feed.example/show.xml",
        podcast_metadata={
            "guid": "episode-guid-1",
            "podcast_guid": "show-guid",
            "medium": "podcast",
            "txt": [{"value": "ownership-token", "purpose": "verify"}],
            "value": [{"raw_xml": '<podcast:value type="lightning"/>'}],
        },
    )
    saved = MetadataSaver.save(str(tmp_path), info)
    assert saved["metadata_path"].endswith("metadata.json")
    payload = load_metadata_sidecar(tmp_path)
    assert payload["podcast"]["guid"] == "episode-guid-1"
    assert payload["podcast"]["value"][0]["raw_xml"].startswith("<podcast:value")


def test_published_feed_preserves_non_payment_podcast_tags(tmp_path):
    media = tmp_path / "episode.mp3"
    media.write_bytes(b"audio")
    xml = generate_rss([{
        "share_id": "a" * 32,
        "title": "Episode",
        "channel": "Show",
        "media_path": str(media),
        "podcast": {
            "podcast_guid": "show-guid",
            "medium": "podcast",
            "season": {"number": 2},
            "episode": {"number": 3, "display": "Three"},
            "value": [{"raw_xml": "<podcast:value/>"}],
        },
    }], "https://localhost:8080")
    assert "<podcast:season" in xml
    assert "<podcast:episode" in xml
    assert "podcast:value" not in xml
    assert "xmlns:podcast" in xml
