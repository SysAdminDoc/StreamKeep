"""Album-artist auto-fill for audio-only downloads (V41)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep.postprocess import music_tags as mt

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _make_audio(path, *, tags=None):
    """Write a short real audio file, optionally pre-tagged."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
    ]
    for key, value in (tags or {}).items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)


class ClassificationTests(unittest.TestCase):
    def test_audio_extensions_are_recognised(self):
        for name in ("a.mp3", "a.M4A", "a.opus", "a.flac"):
            with self.subTest(name=name):
                self.assertTrue(mt.is_audio_file(name))
        for name in ("a.mp4", "a.mkv", "a", "a.txt"):
            with self.subTest(name=name):
                self.assertFalse(mt.is_audio_file(name))

    def test_only_music_and_podcast_sources_are_targeted(self):
        for platform in ("SoundCloud", "audius", "Podcast"):
            with self.subTest(platform=platform):
                self.assertTrue(mt.is_music_platform(platform))
        for platform in ("Twitch", "YouTube", "", None):
            with self.subTest(platform=platform):
                self.assertFalse(mt.is_music_platform(platform))


class PlanningTests(unittest.TestCase):
    def test_missing_artist_fields_are_filled_from_the_channel(self):
        planned = mt.plan_music_tags({}, channel="Some Artist")
        self.assertEqual(planned["album_artist"], "Some Artist")
        self.assertEqual(planned["artist"], "Some Artist")

    def test_an_existing_tag_is_never_overwritten(self):
        planned = mt.plan_music_tags(
            {"album_artist": "Real Artist", "artist": "Real Artist"},
            channel="Uploader Name",
        )
        self.assertNotIn("album_artist", planned)
        self.assertNotIn("artist", planned)

    def test_common_tag_aliases_count_as_present(self):
        for alias in ("albumartist", "ALBUM_ARTIST", "TPE2"):
            with self.subTest(alias=alias):
                planned = mt.plan_music_tags(
                    {alias: "Real Artist"}, channel="Uploader",
                )
                self.assertNotIn("album_artist", planned)

    def test_a_blank_existing_tag_does_not_block_the_fill(self):
        planned = mt.plan_music_tags(
            {"album_artist": "   "}, channel="Uploader",
        )
        self.assertEqual(planned["album_artist"], "Uploader")

    def test_an_album_is_only_filled_when_one_is_supplied(self):
        # Never invent an album from the track title: that produces one
        # single-track album per download, which is worse than none.
        self.assertNotIn(
            "album", mt.plan_music_tags({}, channel="A", title="Track One"),
        )
        self.assertEqual(
            mt.plan_music_tags({}, channel="A", album="The Show")["album"],
            "The Show",
        )

    def test_nothing_is_planned_without_a_channel(self):
        self.assertEqual(mt.plan_music_tags({}, channel=""), {})

    def test_the_command_is_a_stream_copy_carrying_every_tag(self):
        cmd = mt.build_tag_command(
            "in.mp3", "out.mp3", {"artist": "A", "album_artist": "A"},
        )
        self.assertIn("-c", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
        self.assertIn("artist=A", cmd)
        self.assertIn("album_artist=A", cmd)
        self.assertEqual(cmd[-1], "out.mp3")

    def test_building_a_command_with_no_tags_is_refused(self):
        with self.assertRaises(ValueError):
            mt.build_tag_command("in.mp3", "out.mp3", {})


class DiscoveryTests(unittest.TestCase):
    def test_audio_outputs_are_found_largest_first_ignoring_dotfiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "small.mp3").write_bytes(b"x" * 10)
            (root / "big.m4a").write_bytes(b"x" * 100)
            (root / "video.mp4").write_bytes(b"x" * 1000)
            (root / ".streamkeep_thumb.mp3").write_bytes(b"x" * 5000)
            found = [os.path.basename(p) for p in mt.find_audio_outputs(root)]
            self.assertEqual(found, ["big.m4a", "small.mp3"])

    def test_a_missing_directory_yields_nothing(self):
        self.assertEqual(mt.find_audio_outputs("/no/such/dir"), [])


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg and ffprobe are required")
class RealAudioTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_an_untagged_track_gains_its_album_artist(self):
        track = self.root / "track.mp3"
        _make_audio(track)
        ok, applied, message = mt.apply_music_tags(track, channel="Some Artist")
        self.assertTrue(ok, message)
        self.assertEqual(applied.get("album_artist"), "Some Artist")
        tags = mt.read_tags(track)
        self.assertEqual(tags.get("album_artist"), "Some Artist")
        self.assertEqual(tags.get("artist"), "Some Artist")
        self.assertTrue(track.is_file())
        self.assertGreater(track.stat().st_size, 0)

    def test_an_existing_artist_tag_survives_untouched(self):
        track = self.root / "tagged.mp3"
        _make_audio(track, tags={"artist": "Real Artist", "album_artist": "Real Artist"})
        ok, applied, _message = mt.apply_music_tags(track, channel="Uploader Name")
        self.assertTrue(ok)
        self.assertEqual(applied, {})
        tags = mt.read_tags(track)
        self.assertEqual(tags.get("album_artist"), "Real Artist")
        self.assertEqual(tags.get("artist"), "Real Artist")

    def test_tagging_leaves_no_temporary_file_behind(self):
        track = self.root / "track.m4a"
        _make_audio(track)
        mt.apply_music_tags(track, channel="Some Artist")
        names = {p.name for p in self.root.iterdir()}
        self.assertEqual(names, {"track.m4a"})

    def test_a_second_pass_is_a_no_op(self):
        track = self.root / "track.mp3"
        _make_audio(track)
        mt.apply_music_tags(track, channel="Some Artist")
        first = track.read_bytes()
        ok, applied, _message = mt.apply_music_tags(track, channel="Other Name")
        self.assertTrue(ok)
        self.assertEqual(applied, {})
        self.assertEqual(track.read_bytes(), first)

    def test_a_podcast_episode_gets_the_show_as_its_album(self):
        track = self.root / "episode.m4a"
        _make_audio(track)
        ok, applied, message = mt.apply_music_tags(
            track, channel="My Show", album="My Show", title="Episode 12",
        )
        self.assertTrue(ok, message)
        tags = mt.read_tags(track)
        self.assertEqual(tags.get("album"), "My Show")
        self.assertEqual(tags.get("album_artist"), "My Show")

    def test_a_video_file_is_left_alone(self):
        video = self.root / "clip.mp4"
        video.write_bytes(b"not really a video")
        before = video.read_bytes()
        ok, applied, _message = mt.apply_music_tags(video, channel="Some Artist")
        self.assertTrue(ok)
        self.assertEqual(applied, {})
        self.assertEqual(video.read_bytes(), before)

    def test_a_failed_tag_write_preserves_the_original(self):
        track = self.root / "track.mp3"
        _make_audio(track)
        before = track.read_bytes()
        with mock.patch.object(
            mt.subprocess, "run",
            return_value=mock.Mock(returncode=1, stderr="boom", stdout=""),
        ):
            ok, applied, message = mt.apply_music_tags(track, channel="A")
        self.assertFalse(ok)
        self.assertEqual(applied, {})
        self.assertIn("could not write tags", message)
        self.assertEqual(track.read_bytes(), before)
        self.assertEqual({p.name for p in self.root.iterdir()}, {"track.mp3"})


class FinalizeIntegrationTests(unittest.TestCase):
    def _worker(self):
        from streamkeep.workers.finalize import FinalizeWorker

        return FinalizeWorker.__new__(FinalizeWorker)

    def test_only_music_platforms_schedule_the_step(self):
        from streamkeep.models import StreamInfo

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "track.mp3").write_bytes(b"x" * 10)
            worker = self._worker()
            task = {"out_dir": tmp}
            self.assertTrue(
                worker._music_tag_targets(
                    task, StreamInfo(platform="SoundCloud"),
                )
            )
            self.assertEqual(
                worker._music_tag_targets(task, StreamInfo(platform="Twitch")),
                [],
            )

    def test_a_video_only_recording_schedules_nothing(self):
        from streamkeep.models import StreamInfo

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "stream.mp4").write_bytes(b"x" * 10)
            worker = self._worker()
            self.assertEqual(
                worker._music_tag_targets(
                    {"out_dir": tmp}, StreamInfo(platform="SoundCloud"),
                ),
                [],
            )

    def test_no_output_directory_schedules_nothing(self):
        from streamkeep.models import StreamInfo

        worker = self._worker()
        self.assertEqual(
            worker._music_tag_targets({}, StreamInfo(platform="Audius")), [],
        )
