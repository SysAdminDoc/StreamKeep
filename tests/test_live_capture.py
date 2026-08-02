"""Live-capture reliability: gap detection, raw staging, salvage (V36)."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import live_capture as lc
from streamkeep.integrations import ytarchive as ya

# A realistic yt-dlp --live-from-start transcript with two lost fragments and
# ordinary retries that must NOT be counted as losses.
GAPPY_OUTPUT = [
    "[youtube] Extracting URL: https://www.youtube.com/watch?v=abc",
    "[download] Destination: stream.mp4",
    "[download] Got server error. Retrying fragment 5 (1/10)...",
    "[download]  12.5% of ~1.20GiB at 3.10MiB/s ETA 05:12",
    "WARNING: fragment 41 not found, unable to continue",
    "WARNING: Skipping fragment 42 ...",
    "[download] Got error: giving up after 10 fragment retries",
    "[download] 100% of ~1.20GiB",
]
CLEAN_OUTPUT = [
    "[download] Destination: stream.mp4",
    "[download] Got server error. Retrying fragment 5 (1/10)...",
    "[download] 100% of ~1.20GiB",
]


class FragmentGapTests(unittest.TestCase):
    def test_lost_fragments_are_detected_and_retries_are_not(self):
        gaps = lc.parse_fragment_gaps(GAPPY_OUTPUT)
        self.assertTrue(gaps.has_gaps)
        self.assertEqual(gaps.missing, (41, 42))
        self.assertEqual(gaps.gave_up, 10)
        # Fragment 5 was merely retried; it is not a hole.
        self.assertNotIn(5, gaps.missing)

    def test_a_clean_capture_reports_no_gaps(self):
        gaps = lc.parse_fragment_gaps(CLEAN_OUTPUT)
        self.assertFalse(gaps.has_gaps)
        self.assertEqual(gaps.count, 0)
        self.assertEqual(gaps.describe(), "no fragment gaps reported")

    def test_empty_output_is_safe(self):
        for value in ([], None, [""]):
            with self.subTest(value=value):
                self.assertFalse(lc.parse_fragment_gaps(value).has_gaps)

    def test_consecutive_losses_collapse_into_intervals(self):
        gaps = lc.parse_fragment_gaps([
            "WARNING: Skipping fragment 3",
            "WARNING: Skipping fragment 4",
            "WARNING: Skipping fragment 5",
            "WARNING: fragment 20 not found",
        ])
        self.assertEqual(gaps.intervals(), ((3, 5), (20, 20)))
        self.assertIn("3-5", gaps.describe())
        self.assertIn("20", gaps.describe())

    def test_an_unnumbered_loss_is_still_reported(self):
        gaps = lc.parse_fragment_gaps([
            "ERROR: unable to download fragment data, unable to continue",
        ])
        self.assertTrue(gaps.has_gaps)
        self.assertEqual(gaps.missing, ())
        self.assertEqual(gaps.unknown_losses, 1)
        self.assertIn("unidentified", gaps.describe())

    def test_the_report_serializes_for_the_sidecar(self):
        payload = lc.parse_fragment_gaps(GAPPY_OUTPUT).to_dict()
        self.assertEqual(json.loads(json.dumps(payload))["missing"], [41, 42])
        self.assertEqual(payload["intervals"], [[41, 42]])


class RawStagingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _seed(self, *names):
        for index, name in enumerate(names):
            (self.root / name).write_bytes(b"x" * (100 + index))

    def test_staging_files_are_moved_out_of_the_reaper_path(self):
        self._seed("stream.mp4.part", "stream.f140.m4s", "unrelated.txt")
        gaps = lc.parse_fragment_gaps(GAPPY_OUTPUT)
        staged = lc.preserve_raw_capture(
            str(self.root / "stream.mp4"), str(self.root), "stream",
            gaps=gaps, reason="test",
        )
        self.assertTrue(staged.exists)
        names = {os.path.basename(path) for path in staged.files}
        self.assertEqual(names, {"stream.mp4.part", "stream.f140.m4s"})
        # Moved, not copied, and unrelated files are untouched.
        self.assertFalse((self.root / "stream.mp4.part").exists())
        self.assertTrue((self.root / "unrelated.txt").exists())
        self.assertGreater(staged.total_bytes, 0)

    def test_the_report_records_the_known_gaps(self):
        self._seed("stream.mp4.part")
        staged = lc.preserve_raw_capture(
            str(self.root / "stream.mp4"), str(self.root), "stream",
            gaps=lc.parse_fragment_gaps(GAPPY_OUTPUT),
            reason="live capture reported fragment gaps",
        )
        report = lc.load_report(staged.directory)
        self.assertEqual(report["version"], lc.REPORT_VERSION)
        self.assertEqual(report["gaps"]["missing"], [41, 42])
        self.assertIn("fragment gaps", report["reason"])

    def test_preserving_twice_keeps_the_larger_copy_and_stays_idempotent(self):
        self._seed("stream.mp4.part")
        target = str(self.root / "stream.mp4")
        lc.preserve_raw_capture(target, str(self.root), "stream")
        # A second interrupted attempt leaves a bigger part file behind.
        (self.root / "stream.mp4.part").write_bytes(b"y" * 500)
        staged = lc.preserve_raw_capture(target, str(self.root), "stream")
        self.assertEqual(len(staged.files), 1)
        self.assertEqual(
            Path(staged.files[0]).read_bytes(), b"y" * 500,
        )

    def test_nothing_to_preserve_creates_no_directory(self):
        staged = lc.preserve_raw_capture(
            str(self.root / "stream.mp4"), str(self.root), "stream",
        )
        self.assertFalse(staged.exists)
        self.assertEqual(lc.list_staged_captures(str(self.root)), [])

    def test_staged_directories_are_discoverable(self):
        self._seed("stream.mp4.part")
        lc.preserve_raw_capture(
            str(self.root / "stream.mp4"), str(self.root), "stream",
        )
        found = lc.list_staged_captures(str(self.root))
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].endswith(lc.STAGING_SUFFIX))


class SalvageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.staging = self.root / f"stream{lc.STAGING_SUFFIX}"
        self.staging.mkdir()

    def test_salvage_always_targets_a_new_file(self):
        target = lc.salvage_target(str(self.staging))
        self.assertTrue(target.endswith(".salvaged.mp4"))
        # It must never be the original output the capture was aiming at.
        self.assertNotEqual(target, str(self.root / "stream.mp4"))
        self.assertNotIn(lc.STAGING_SUFFIX, target)

    def test_the_concat_list_orders_fragments_numerically(self):
        for index in (1, 2, 10):
            (self.staging / f"stream.part{index}.ts").write_bytes(b"x")
        listing = lc.write_concat_list(str(self.staging))
        lines = Path(listing).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        # Lexical order would put part10 before part2.
        self.assertIn("part1.ts", lines[0])
        self.assertIn("part2.ts", lines[1])
        self.assertIn("part10.ts", lines[2])

    def test_the_report_and_list_are_never_concatenated(self):
        (self.staging / lc.REPORT_NAME).write_text("{}", encoding="utf-8")
        (self.staging / "stream.ts").write_bytes(b"x")
        listing = lc.write_concat_list(str(self.staging))
        content = Path(listing).read_text(encoding="utf-8")
        self.assertNotIn(lc.REPORT_NAME, content)
        self.assertNotIn("concat.txt", content)

    def test_an_empty_staging_directory_yields_no_list(self):
        self.assertEqual(lc.write_concat_list(str(self.staging)), "")

    def test_the_salvage_command_is_a_stream_copy_of_the_list(self):
        (self.staging / "stream.ts").write_bytes(b"x")
        listing = lc.write_concat_list(str(self.staging))
        target = lc.salvage_target(str(self.staging))
        cmd = lc.build_salvage_command(
            str(self.staging), target, ffmpeg="ffmpeg", concat_list=listing,
        )
        self.assertIn("-c", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
        self.assertEqual(cmd[-1], target)
        self.assertIn(listing, cmd)

    def test_building_a_command_without_a_list_refuses(self):
        with self.assertRaises(ValueError):
            lc.build_salvage_command(
                str(self.staging), "out.mp4", concat_list=str(self.root / "no.txt"),
            )

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
    def test_a_real_salvage_produces_a_playable_file_without_touching_the_raw(self):
        # Build two tiny real MPEG-TS fragments so the concat demuxer has
        # something genuinely decodable to work with.
        for index in range(2):
            fragment = self.staging / f"stream.part{index}.ts"
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=0.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-f", "mpegts", str(fragment),
                ],
                check=True, capture_output=True,
            )
        before = {
            path.name: path.read_bytes()
            for path in self.staging.iterdir() if path.is_file()
        }
        listing = lc.write_concat_list(str(self.staging))
        target = lc.salvage_target(str(self.staging))
        result = subprocess.run(
            lc.build_salvage_command(
                str(self.staging), target, concat_list=listing,
            ),
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(target))
        self.assertGreater(os.path.getsize(target), 0)
        # The raw capture is unchanged.
        for name, data in before.items():
            self.assertEqual((self.staging / name).read_bytes(), data)


class YtArchiveEngineTests(unittest.TestCase):
    def test_absence_produces_a_hint_rather_than_a_crash(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(ya.ytarchive_available())
            with self.assertRaises(ya.YtArchiveUnavailable):
                ya.ytarchive_command_prefix()
        self.assertIn("go install", ya.ytarchive_install_hint())

    def test_only_youtube_addresses_route_to_the_engine(self):
        self.assertTrue(ya.is_youtube_live_url("https://www.youtube.com/watch?v=x"))
        self.assertTrue(ya.is_youtube_live_url("https://youtu.be/x"))
        self.assertFalse(ya.is_youtube_live_url("https://www.twitch.tv/x"))
        self.assertFalse(ya.is_youtube_live_url("https://evil-youtube.com/x"))
        self.assertFalse(ya.is_youtube_live_url(""))

    def test_quality_preferences_map_onto_engine_selectors(self):
        cases = {
            "": "best", "best": "best", "source": "best",
            "1080p": "1080p", "720": "720p", "audio": "audio_only",
            "144p": "best",  # not a selector ytarchive accepts
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(ya.normalize_quality(value), expected)

    def test_the_command_puts_quality_last_and_refuses_option_smuggling(self):
        with mock.patch("shutil.which", return_value="/usr/bin/ytarchive"):
            cmd = ya.build_ytarchive_command(
                "https://youtu.be/x", "/out/%(title)s.%(ext)s",
                quality="1080p", proxy="http://127.0.0.1:8080", wait=True,
            )
            self.assertEqual(cmd[-2:], ["https://youtu.be/x", "1080p"])
            self.assertIn("--merge", cmd)
            self.assertIn("--wait", cmd)
            self.assertEqual(cmd[cmd.index("--proxy") + 1], "http://127.0.0.1:8080")
            with self.assertRaises(ValueError):
                ya.build_ytarchive_command("-oflag", "/out/x.%(ext)s")
            with self.assertRaises(ValueError):
                ya.build_ytarchive_command("https://youtu.be/x", "")


class WorkerIntegrationTests(unittest.TestCase):
    """The worker paths, exercised without a network or a real subprocess."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _worker(self):
        from streamkeep.workers.download import DownloadWorker

        worker = DownloadWorker.__new__(DownloadWorker)
        worker.output_dir = str(self.root)
        worker.live_engine_fallback = False
        worker.last_capture_gaps = None
        worker.proxy = ""
        worker.ytdlp_format = ""
        worker.logged = []
        worker.log = mock.Mock()
        worker.log.emit = worker.logged.append
        return worker

    def test_a_completed_capture_with_gaps_is_reported_not_hidden(self):
        worker = self._worker()
        gaps = worker._report_fragment_gaps("stream", GAPPY_OUTPUT)
        self.assertTrue(gaps.has_gaps)
        self.assertIs(worker.last_capture_gaps, gaps)
        self.assertTrue(
            any("missing fragments 41-42" in line for line in worker.logged),
            worker.logged,
        )

    def test_a_clean_capture_logs_nothing(self):
        worker = self._worker()
        self.assertFalse(worker._report_fragment_gaps("s", CLEAN_OUTPUT).has_gaps)
        self.assertEqual(worker.logged, [])

    def test_a_gapped_live_failure_preserves_the_raw_capture(self):
        worker = self._worker()
        (self.root / "stream.mp4.part").write_bytes(b"x" * 4096)
        handled = worker._handle_live_gap_failure(
            0, "stream", str(self.root / "stream.mp4"), None, GAPPY_OUTPUT,
        )
        # No engine configured, so the failure still stands...
        self.assertFalse(handled)
        # ...but the bytes survived and are discoverable for salvage.
        staged = lc.list_staged_captures(str(self.root))
        self.assertEqual(len(staged), 1)
        self.assertEqual(lc.load_report(staged[0])["gaps"]["missing"], [41, 42])
        self.assertTrue(
            any("[SALVAGE]" in line for line in worker.logged), worker.logged,
        )

    def test_a_failure_without_gaps_preserves_nothing(self):
        worker = self._worker()
        (self.root / "stream.mp4.part").write_bytes(b"x" * 4096)
        self.assertFalse(
            worker._handle_live_gap_failure(
                0, "stream", str(self.root / "stream.mp4"), None,
                ["ERROR: Video unavailable"],
            )
        )
        self.assertEqual(lc.list_staged_captures(str(self.root)), [])

    def test_the_fallback_is_skipped_unless_opted_in_and_installed(self):
        worker = self._worker()
        worker._effective_ytdlp_source = lambda: "https://youtu.be/x"
        # Off by default.
        self.assertFalse(worker._try_ytarchive_fallback(0, "s", "out.%(ext)s"))
        worker.live_engine_fallback = True
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(worker._try_ytarchive_fallback(0, "s", "out.%(ext)s"))
        self.assertTrue(
            any("not installed" in line for line in worker.logged), worker.logged,
        )

    def test_the_fallback_never_runs_for_a_non_youtube_source(self):
        worker = self._worker()
        worker.live_engine_fallback = True
        worker._effective_ytdlp_source = lambda: "https://www.twitch.tv/x"
        with mock.patch("shutil.which", return_value="/usr/bin/ytarchive"):
            self.assertFalse(
                worker._try_ytarchive_fallback(0, "s", "out.%(ext)s")
            )
        self.assertEqual(worker.logged, [])

    def test_an_installed_engine_is_invoked_from_the_start(self):
        worker = self._worker()
        worker.live_engine_fallback = True
        worker._effective_ytdlp_source = lambda: "https://youtu.be/x"
        seen = {}

        def fake_stream(cmd, seg_idx, label, outfile, expected, is_live=False):
            seen["cmd"] = cmd
            seen["is_live"] = is_live
            return "ok", []

        worker._stream_ytdlp_download = fake_stream
        with mock.patch("shutil.which", return_value="/usr/bin/ytarchive"):
            self.assertTrue(
                worker._try_ytarchive_fallback(
                    0, "stream", str(self.root / "stream.%(ext)s"),
                )
            )
        self.assertTrue(seen["is_live"])
        self.assertIn("/usr/bin/ytarchive", seen["cmd"][0])
        self.assertEqual(seen["cmd"][-1], "best")
