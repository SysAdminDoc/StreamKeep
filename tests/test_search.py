import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import search


class SearchTests(unittest.TestCase):
    def test_index_recording_removes_stale_entries_when_transcripts_disappear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "search.db"
            recording_dir = tmp / "recording"
            recording_dir.mkdir()
            transcript = recording_dir / "captions.srt"
            transcript.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nHello world\n",
                encoding="utf-8",
            )

            with mock.patch.object(search, "DB_PATH", db_path):
                indexed = search.index_recording(str(recording_dir))
                hits = search.search_transcripts("hello")

                transcript.unlink()
                reindexed = search.index_recording(str(recording_dir))
                stale_hits = search.search_transcripts("hello")

            self.assertEqual(indexed, 1)
            self.assertEqual(len(hits), 1)
            self.assertEqual(reindexed, 0)
            self.assertEqual(stale_hits, [])

    def test_search_transcripts_handles_invalid_fts_queries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "search.db"
            with mock.patch.object(search, "DB_PATH", db_path):
                hits = search.search_transcripts('"unterminated')
            self.assertEqual(hits, [])


class WebVTTParsingTests(unittest.TestCase):
    def _parse(self, body):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "c.vtt"
            path.write_text(body, encoding="utf-8")
            return search._parse_vtt(str(path))

    def test_minute_only_timestamps_are_accepted(self):
        segs = self._parse(
            "WEBVTT\n\n05:10.500 --> 05:12.000\nMinute only cue\n"
        )
        self.assertEqual(len(segs), 1)
        start, end, text = segs[0]
        self.assertAlmostEqual(start, 310.5, places=3)
        self.assertAlmostEqual(end, 312.0, places=3)
        self.assertEqual(text, "Minute only cue")

    def test_hour_timestamps_still_work(self):
        segs = self._parse(
            "WEBVTT\n\n01:05:10.500 --> 01:05:12.000\nHour cue\n"
        )
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0][0], 3910.5, places=3)

    def test_identifier_settings_and_markup_are_handled(self):
        segs = self._parse(
            "WEBVTT\n\n"
            "cue-7\n"
            "00:00:01.000 --> 00:00:03.000 position:50% line:0 align:middle\n"
            "<v Alice>Hello <c.loud>there</c> <i>world</i>\n"
        )
        self.assertEqual(len(segs), 1)
        # Cue identifier is not part of the text; settings and markup stripped.
        self.assertEqual(segs[0][2], "Hello there world")

    def test_malformed_cue_is_isolated(self):
        segs = self._parse(
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\nGood one\n\n"
            "NOTE this is a comment block\n\n"
            "99:99 --> broken\nShould be skipped\n\n"
            "00:00:05.000 --> 00:00:06.000\nGood two\n"
        )
        self.assertEqual([s[2] for s in segs], ["Good one", "Good two"])

    def test_indexed_vtt_hit_jumps_to_correct_offset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "search.db"
            recording = tmp / "rec"
            recording.mkdir()
            (recording / "captions.vtt").write_text(
                "WEBVTT\n\n02:03.250 --> 02:05.000\nfindable phrase\n",
                encoding="utf-8",
            )
            with mock.patch.object(search, "DB_PATH", db_path):
                search.index_recording(str(recording))
                hits = search.search_transcripts("findable")
            self.assertEqual(len(hits), 1)
            self.assertAlmostEqual(hits[0]["start_sec"], 123.25, places=2)


class PodcastTranscriptChapterTests(unittest.TestCase):
    def test_podcast_namespace_json_transcript_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ep.transcript.json"
            path.write_text(
                '{"version":"1.0.0","segments":['
                '{"speaker":"Host","startTime":1.0,"endTime":2.5,'
                '"body":"welcome aboard"}]}',
                encoding="utf-8",
            )
            segs = search._parse_transcript_json(str(path))
            self.assertEqual(len(segs), 1)
            start, end, text = segs[0]
            self.assertAlmostEqual(start, 1.0)
            self.assertAlmostEqual(end, 2.5)
            self.assertEqual(text, "Host: welcome aboard")

    def test_podcast_chapters_json_fills_end_times_and_orders(self):
        from streamkeep.extractors.podcast import parse_podcast_chapters_json
        chapters = parse_podcast_chapters_json(
            '{"version":"1.2.0","chapters":['
            '{"startTime":73.5,"title":"Second"},'
            '{"startTime":0,"title":"Intro","img":"a.png"},'
            '{"startTime":200,"title":"Hidden","toc":false},'
            '{"startTime":150,"title":"Third","endTime":190}]}'
        )
        # toc:false excluded; ordered by start; end filled from next start.
        self.assertEqual([c["title"] for c in chapters], ["Intro", "Second", "Third"])
        self.assertAlmostEqual(chapters[0]["end"], 73.5)
        self.assertAlmostEqual(chapters[1]["end"], 150.0)
        self.assertAlmostEqual(chapters[2]["end"], 190.0)
        self.assertEqual(chapters[0]["img"], "a.png")

    def test_podcast_chapters_json_rejects_malformed(self):
        from streamkeep.extractors.podcast import parse_podcast_chapters_json
        self.assertEqual(parse_podcast_chapters_json("not json"), [])
        self.assertEqual(parse_podcast_chapters_json('{"chapters":"nope"}'), [])
        # Entry with a non-numeric startTime is skipped, valid one kept.
        chapters = parse_podcast_chapters_json(
            '{"chapters":[{"title":"bad","startTime":"x"},'
            '{"title":"ok","startTime":5}]}'
        )
        self.assertEqual([c["title"] for c in chapters], ["ok"])


if __name__ == "__main__":
    unittest.main()
