"""Shared output-template resolution and native container naming (V39)."""

import os
import unittest
from unittest import mock

from streamkeep.models import StreamInfo
from streamkeep.utils import (
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    resolve_output_paths,
)


def _info(title="A Stream", channel="SomeChannel", platform="Twitch"):
    return StreamInfo(
        title=title, channel=channel, platform=platform,
        start_time="2026-08-02T10:00:00Z",
    )


class TemplateResolverTests(unittest.TestCase):
    def test_the_built_in_defaults_apply_without_configuration(self):
        directory, base = resolve_output_paths(_info(), os.path.join("R", "out"))
        self.assertEqual(base, "A Stream")
        self.assertEqual(
            directory,
            os.path.join("R", "out", "SomeChannel", "2026-08-02 - A Stream"),
        )
        self.assertEqual(DEFAULT_FOLDER_TEMPLATE, "{channel}/{date} - {title}")
        self.assertEqual(DEFAULT_FILE_TEMPLATE, "{title}")

    def test_a_configured_default_beats_the_built_in(self):
        directory, base = resolve_output_paths(
            _info(), "out",
            config={"folder_template": "{platform}", "file_template": "{channel}"},
        )
        self.assertEqual(directory, os.path.join("out", "Twitch"))
        self.assertEqual(base, "SomeChannel")

    def test_an_explicit_override_beats_the_configured_default(self):
        directory, base = resolve_output_paths(
            _info(), "out",
            folder_template="{channel}",
            file_template="{title} [{platform}]",
            config={"folder_template": "IGNORED", "file_template": "IGNORED"},
        )
        self.assertEqual(directory, os.path.join("out", "SomeChannel"))
        self.assertEqual(base, "A Stream [Twitch]")

    def test_a_nested_file_template_contributes_directories(self):
        directory, base = resolve_output_paths(
            _info(), "out", folder_template="{platform}",
            file_template="{channel}/{title}",
        )
        self.assertEqual(directory, os.path.join("out", "Twitch", "SomeChannel"))
        self.assertEqual(base, "A Stream")

    def test_the_base_name_never_carries_an_extension(self):
        _directory, base = resolve_output_paths(
            _info(title="clip.mp4"), "out", file_template="{title}",
        )
        self.assertFalse(base.endswith(os.sep))
        self.assertNotIn("/", base)
        self.assertNotIn("\\", base)

    def test_an_empty_render_falls_back_to_a_usable_name(self):
        directory, base = resolve_output_paths(
            _info(title="", channel=""), "out", file_template="{title}",
        )
        self.assertTrue(base)
        self.assertTrue(directory.startswith("out"))

    def test_unsafe_path_components_are_sanitized(self):
        directory, base = resolve_output_paths(
            _info(title="a/b:c*?", channel=".."), "out",
            folder_template="{channel}", file_template="{title}",
        )
        self.assertNotIn(":", base)
        self.assertNotIn("*", base)
        # A traversal component must never survive into the path.
        self.assertNotIn("..", os.path.relpath(directory, "out").split(os.sep))

    def test_gui_and_cli_agree_for_the_same_configuration(self):
        config = {"folder_template": "{channel}/{year}", "file_template": "{title}"}
        info = _info()
        first = resolve_output_paths(info, "out", config=config)
        second = resolve_output_paths(info, "out", config=config)
        self.assertEqual(first, second)
        self.assertEqual(first[0], os.path.join("out", "SomeChannel", "2026"))


class NativeContainerTests(unittest.TestCase):
    """The ffmpeg path must name its output by the chosen container."""

    def _worker(self, container):
        from streamkeep.workers.download import DownloadWorker

        worker = DownloadWorker.__new__(DownloadWorker)
        worker.ytdlp_container = container
        return worker

    def test_the_configured_container_decides_the_extension(self):
        for container, expected in (
            ("mp4", "mp4"), ("mkv", "mkv"), ("webm", "webm"), (".mkv", "mkv"),
            ("MKV", "mkv"),
        ):
            with self.subTest(container=container):
                self.assertEqual(
                    self._worker(container)._native_container(), expected,
                )

    def test_original_and_empty_fall_back_to_a_concrete_muxer(self):
        # ffmpeg needs a real container; "original" is not one.
        for container in ("original", "", None):
            with self.subTest(container=container):
                self.assertEqual(
                    self._worker(container)._native_container(), "mp4",
                )

    def test_a_non_mp4_capture_is_no_longer_named_mp4(self):
        from streamkeep.workers.download import DownloadWorker

        worker = DownloadWorker(
            "https://example.com/master.m3u8",
            [(0, "capture", 0, 120)],
            "out",
        )
        worker.ytdlp_container = "mkv"
        with mock.patch.object(
            DownloadWorker, "_uses_ytdlp_download", return_value=False,
        ), mock.patch.object(
            DownloadWorker, "_build_ffmpeg_download_cmd",
            side_effect=lambda outfile, *a, **kw: ["ffmpeg", outfile],
        ):
            cmd = worker.build_export_argv()
        self.assertTrue(cmd[-1].endswith("capture.mkv"), cmd)
        self.assertFalse(cmd[-1].endswith(".mp4"))

    def test_chunked_live_parts_follow_the_container_too(self):
        from streamkeep.workers.download import DownloadWorker

        worker = DownloadWorker.__new__(DownloadWorker)
        worker.ytdlp_container = "mkv"
        self.assertEqual(worker._native_container(), "mkv")
        base = os.path.splitext("out/capture.mkv")[0] + \
            f"_part%03d.{worker._native_container()}"
        self.assertTrue(base.endswith("_part%03d.mkv"))


class CliTemplateFlagTests(unittest.TestCase):
    def test_the_download_parser_accepts_both_templates(self):
        from streamkeep.cli import build_parser

        args = build_parser().parse_args([
            "download", "https://example.com/v",
            "--filename-template", "{channel} - {title}",
            "--folder-template", "{platform}/{year}",
        ])
        self.assertEqual(args.file_template, "{channel} - {title}")
        self.assertEqual(args.folder_template, "{platform}/{year}")

    def test_the_templates_default_to_empty_so_config_wins(self):
        from streamkeep.cli import build_parser

        args = build_parser().parse_args(["download", "https://example.com/v"])
        self.assertEqual(args.file_template, "")
        self.assertEqual(args.folder_template, "")
