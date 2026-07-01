import unittest
import xml.etree.ElementTree as ET

from streamkeep.opml import export_opml, import_opml


class ExportTests(unittest.TestCase):
    def test_export_groups_by_platform(self):
        entries = [
            {"url": "https://kick.com/ch1", "platform": "Kick", "channel_id": "ch1"},
            {"url": "https://twitch.tv/ch2", "platform": "Twitch", "channel_id": "ch2"},
            {"url": "https://kick.com/ch3", "platform": "Kick", "channel_id": "ch3"},
        ]
        xml = export_opml(entries)
        root = ET.fromstring(xml)
        groups = root.findall(".//body/outline")
        platforms = [g.get("text") for g in groups]
        self.assertEqual(platforms, ["Kick", "Twitch"])
        kick_children = groups[0].findall("outline")
        self.assertEqual(len(kick_children), 2)

    def test_export_marks_rss_feeds_as_rss_type(self):
        entries = [
            {"url": "https://example.com/feed.rss", "platform": "Podcast", "channel_id": "pod"},
            {"url": "https://twitch.tv/streamer", "platform": "Twitch", "channel_id": "streamer"},
        ]
        xml = export_opml(entries)
        root = ET.fromstring(xml)
        outlines = root.findall(".//body/outline/outline")
        types = {o.get("text"): o.get("type") for o in outlines}
        self.assertEqual(types["pod"], "rss")
        self.assertEqual(types["streamer"], "link")

    def test_export_skips_non_http_urls(self):
        entries = [
            {"url": "file:///local/path", "platform": "Local"},
            {"url": "https://valid.com/feed", "platform": "Web", "channel_id": "v"},
        ]
        xml = export_opml(entries)
        root = ET.fromstring(xml)
        all_outlines = root.findall(".//body/outline/outline")
        self.assertEqual(len(all_outlines), 1)

    def test_roundtrip_preserves_urls(self):
        entries = [
            {"url": "https://example.com/feed.rss", "platform": "Podcast", "channel_id": "MyPodcast"},
            {"url": "https://kick.com/streamer", "platform": "Kick", "channel_id": "streamer"},
        ]
        xml = export_opml(entries)
        imported, report = import_opml(xml)
        imported_urls = {e["url"] for e in imported}
        self.assertEqual(imported_urls, {"https://example.com/feed.rss", "https://kick.com/streamer"})
        self.assertEqual(report["imported"], 2)
        self.assertEqual(report["duplicates"], 0)


class ImportTests(unittest.TestCase):
    SAMPLE_OPML = """<?xml version="1.0" encoding="UTF-8"?>
    <opml version="2.0">
      <head><title>Test</title></head>
      <body>
        <outline text="Podcasts">
          <outline text="Tech Pod" type="rss" xmlUrl="https://techpod.com/feed.rss"/>
          <outline text="News Pod" type="rss" xmlUrl="https://news.com/feed.xml"/>
        </outline>
        <outline text="Streams">
          <outline text="Gamer" xmlUrl="https://twitch.tv/gamer"/>
        </outline>
      </body>
    </opml>"""

    def test_import_parses_nested_outlines(self):
        entries, report = import_opml(self.SAMPLE_OPML)
        self.assertEqual(report["total"], 3)
        self.assertEqual(report["imported"], 3)
        self.assertEqual(len(entries), 3)
        platforms = {e["platform"] for e in entries}
        self.assertEqual(platforms, {"Podcasts", "Streams"})

    def test_import_reports_duplicates(self):
        existing = {"https://techpod.com/feed.rss"}
        entries, report = import_opml(self.SAMPLE_OPML, existing_urls=existing)
        self.assertEqual(report["duplicates"], 1)
        self.assertEqual(report["imported"], 2)

    def test_import_reports_invalid_urls(self):
        opml = """<?xml version="1.0"?>
        <opml version="2.0">
          <head><title>T</title></head>
          <body>
            <outline text="Bad" xmlUrl="not-a-url"/>
            <outline text="Good" xmlUrl="https://ok.com/feed"/>
          </body>
        </opml>"""
        entries, report = import_opml(opml)
        self.assertEqual(report["invalid"], 1)
        self.assertEqual(report["imported"], 1)

    def test_import_rejects_malformed_xml(self):
        entries, report = import_opml("<not valid xml")
        self.assertEqual(len(entries), 0)
        self.assertTrue(len(report["errors"]) > 0)
        self.assertIn("XML parse error", report["errors"][0])

    def test_import_handles_empty_body(self):
        opml = '<?xml version="1.0"?><opml version="2.0"><head/></opml>'
        entries, report = import_opml(opml)
        self.assertEqual(len(entries), 0)
        self.assertIn("No <body>", report["errors"][0])

    def test_import_deduplicates_within_file(self):
        opml = """<?xml version="1.0"?>
        <opml version="2.0">
          <head><title>T</title></head>
          <body>
            <outline text="A" xmlUrl="https://example.com/feed"/>
            <outline text="B" xmlUrl="https://example.com/feed"/>
          </body>
        </opml>"""
        entries, report = import_opml(opml)
        self.assertEqual(report["imported"], 1)
        self.assertEqual(report["duplicates"], 1)


if __name__ == "__main__":
    unittest.main()
