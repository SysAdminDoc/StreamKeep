import json
import tempfile
import unittest
from pathlib import Path

from streamkeep import resume


class ResumeTests(unittest.TestCase):
    def test_load_resume_state_rejects_oversized_sidecar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sidecar = root / resume.SIDECAR_NAME
            sidecar.write_text("x" * 32, encoding="utf-8")

            original_limit = resume.MAX_SIDECAR_BYTES
            resume.MAX_SIDECAR_BYTES = 8
            try:
                state = resume.load_resume_state(str(root))
            finally:
                resume.MAX_SIDECAR_BYTES = original_limit

            self.assertIsNone(state)

    def test_load_resume_state_uses_sidecar_directory_and_sanitizes_lists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sidecar = root / resume.SIDECAR_NAME
            sidecar.write_text(
                json.dumps(
                    {
                        "output_dir": "C:/wrong/place",
                        "segments": [
                            [0, "Segment 0", 0, 10],
                            ["bad", "Segment 1", 10, 10],
                            [2, "Segment 2", "oops", 5],
                        ],
                        "completed": ["1", "bad", -1, "1"],
                        "title": "Recovered",
                        "ytdlp_format": "bv*[height<=720]+ba/b",
                        "ytdlp_format_sort": "res:720",
                        "ytdlp_container": "webm",
                        "ytdlp_audio_format": "opus",
                        "ytdlp_audio_quality": "128K",
                        "download_subs": True,
                        "subtitle_languages": "en,es",
                        "subtitle_auto": False,
                        "subtitle_convert": "srt",
                        "subtitle_embed": False,
                        "sponsorblock": True,
                        "sponsorblock_mark": "intro,chapter",
                        "sponsorblock_remove": "sponsor",
                        "sponsorblock_api": "https://sponsor.example/api",
                        "download_archive": "C:/archives/source.txt",
                        "break_on_existing": True,
                        "selected_tracks": [
                            {
                                "id": "dash-main-video-v1",
                                "kind": "video",
                                "label": "1080p",
                                "url": "https://cdn.example.com/main.mpd",
                                "stream_index": 1,
                                "bandwidth": 5000000,
                            },
                            {
                                "id": "bad-kind",
                                "kind": "data",
                                "url": "https://cdn.example.com/main.mpd",
                            },
                            {
                                "id": "missing-url",
                                "kind": "audio",
                            },
                        ],
                        "ytdlp_concurrent_fragments": 4,
                        "ytdlp_retries": "8",
                        "ytdlp_fragment_retries": "infinite",
                        "ytdlp_retry_sleep": "fragment:exp=1:20",
                        "ytdlp_unavailable_fragments": "abort",
                        "ytdlp_throttled_rate": "250K",
                        "ytdlp_live_from_start": True,
                        "ytdlp_wait_for_video": "30-120",
                        "ytdlp_embed_chapters": True,
                        "ytdlp_embed_metadata": False,
                        "ytdlp_embed_thumbnail": True,
                        "ytdlp_template_name": "Authenticated archive",
                    }
                ),
                encoding="utf-8",
            )

            state = resume.load_resume_state(str(root))

            self.assertIsNotNone(state)
            self.assertEqual(state.output_dir, str(root.resolve()))
            self.assertEqual(state.segments, [[0, "Segment 0", 0.0, 10.0]])
            self.assertEqual(state.completed, [1])
            self.assertEqual(state.ytdlp_format, "bv*[height<=720]+ba/b")
            self.assertEqual(state.ytdlp_format_sort, "res:720")
            self.assertEqual(state.ytdlp_container, "webm")
            self.assertEqual(state.ytdlp_audio_format, "opus")
            self.assertEqual(state.ytdlp_audio_quality, "128K")
            self.assertTrue(state.download_subs)
            self.assertEqual(state.subtitle_languages, "en,es")
            self.assertFalse(state.subtitle_auto)
            self.assertEqual(state.subtitle_convert, "srt")
            self.assertFalse(state.subtitle_embed)
            self.assertTrue(state.sponsorblock)
            self.assertEqual(state.sponsorblock_mark, "intro,chapter")
            self.assertEqual(state.sponsorblock_remove, "sponsor")
            self.assertEqual(
                state.sponsorblock_api, "https://sponsor.example/api"
            )
            self.assertEqual(
                state.download_archive, "C:/archives/source.txt"
            )
            self.assertTrue(state.break_on_existing)
            self.assertEqual(len(state.selected_tracks), 1)
            self.assertEqual(
                state.selected_tracks[0]["id"], "dash-main-video-v1"
            )
            self.assertEqual(state.selected_tracks[0]["stream_index"], 1)
            self.assertEqual(state.ytdlp_concurrent_fragments, 4)
            self.assertEqual(state.ytdlp_retries, "8")
            self.assertEqual(state.ytdlp_fragment_retries, "infinite")
            self.assertEqual(state.ytdlp_retry_sleep, "fragment:exp=1:20")
            self.assertEqual(state.ytdlp_unavailable_fragments, "abort")
            self.assertEqual(state.ytdlp_throttled_rate, "250K")
            self.assertTrue(state.ytdlp_live_from_start)
            self.assertEqual(state.ytdlp_wait_for_video, "30-120")
            self.assertTrue(state.ytdlp_embed_chapters)
            self.assertFalse(state.ytdlp_embed_metadata)
            self.assertTrue(state.ytdlp_embed_thumbnail)
            self.assertEqual(state.ytdlp_template_name, "Authenticated archive")


if __name__ == "__main__":
    unittest.main()
