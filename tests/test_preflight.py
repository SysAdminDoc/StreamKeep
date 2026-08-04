import unittest
from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal

from streamkeep.models import MediaTrackInfo, QualityInfo, StreamInfo, VODInfo
from streamkeep.preflight import (
    PreflightError,
    ProbeCache,
    build_picker_response,
    collect_probe_result,
    normalize_media_selection,
    serialize_stream_picker,
    serialize_vod_picker,
    validate_queue_payload,
)


class _TimeoutProbeWorker(QObject):
    finished = pyqtSignal(object)
    vods_found = pyqtSignal(list, str, object)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.interruption_requested = False
        self._finished = False

    def start(self):
        return None

    def requestInterruption(self):
        self.interruption_requested = True

    def wait(self, _timeout):
        return self._finished

    def isFinished(self):
        return self._finished

    def isRunning(self):
        return not self._finished


class PreflightContractTests(unittest.TestCase):
    def test_probe_timeout_transfers_running_worker_to_reaper_callback(self):
        worker = _TimeoutProbeWorker()
        retained = []

        with self.assertRaisesRegex(PreflightError, "timed out"):
            collect_probe_result(
                lambda: worker,
                timeout_seconds=0.01,
                on_timeout=retained.append,
            )

        self.assertTrue(worker.interruption_requested)
        self.assertEqual(retained, [worker])
        self.assertFalse(worker.isFinished())

    def test_vod_picker_keeps_delivery_sources_private_and_preserves_type(self):
        delivery = "https://cdn.example/video.m3u8?token=secret"
        picker = serialize_vod_picker(
            [
                VODInfo(
                    title="Photo set",
                    source=delivery,
                    platform="Gallery",
                    media_type="photo",
                ),
                VODInfo(
                    title="Animated",
                    source="https://cdn.example/animated.gif",
                    platform="Gallery",
                    media_type="gif",
                ),
            ],
            "https://example.com/channel",
        )

        self.assertEqual(
            [item["type"] for item in picker["media_items"]],
            ["photo", "gif"],
        )
        self.assertNotIn(delivery, str(picker["media_items"]))
        self.assertEqual(
            picker["_media_items"][picker["media_items"][0]["id"]]["vod_source"],
            delivery,
        )

    def test_stream_picker_exposes_audio_choices_without_urls(self):
        delivery = "https://signed.example/master.m3u8?token=secret"
        info = StreamInfo(
            title="Episode",
            platform="Test",
            url=delivery,
            qualities=[
                QualityInfo(
                    name="1080p",
                    resolution="1920x1080",
                    tracks=[
                        MediaTrackInfo(
                            id="audio-en",
                            kind="audio",
                            label="English",
                            language="en",
                            default=True,
                        ),
                    ],
                ),
            ],
        )
        picker = serialize_stream_picker(info, "https://example.com/episode")

        self.assertEqual(picker["media_items"][0]["type"], "audio")
        self.assertEqual(picker["background_audio"][0]["language"], "en")
        self.assertNotIn(delivery, str(picker["media_items"]))
        self.assertNotIn(delivery, str(picker["background_audio"]))

    def test_cache_selection_is_bound_to_url_and_one_use(self):
        picker = serialize_stream_picker(
            StreamInfo(
                title="Demo",
                url="https://cdn.example/demo.mp4",
                qualities=[
                    QualityInfo(name="best", url="https://cdn.example/demo.mp4")
                ],
            ),
            "https://example.com/demo",
        )
        cache = ProbeCache()
        validation_id, expires_at = cache.put("https://example.com/demo", picker)
        response = build_picker_response(
            "https://example.com/demo", picker, validation_id, expires_at
        )
        self.assertEqual(response["selection"]["media_item_id"], "quality:0")
        with self.assertRaises(PreflightError):
            cache.take(validation_id, "https://example.com/other")
        private = cache.take(validation_id, "https://example.com/demo")
        selected = normalize_media_selection(
            {
                "url": "https://example.com/demo",
                "validation_id": validation_id,
                "media_item_id": "quality:0",
                "background_audio_id": "",
            },
            private,
        )
        self.assertEqual(selected["quality"], "best")
        with self.assertRaises(PreflightError):
            cache.take(validation_id, "https://example.com/demo")

    def test_queue_validation_rejects_unknown_picker_type_and_bad_urls(self):
        with self.assertRaisesRegex(PreflightError, "media_item_type"):
            validate_queue_payload(
                {
                    "url": "https://example.com/video",
                    "media_item_type": "document",
                }
            )
        with self.assertRaisesRegex(PreflightError, "invalid url"):
            validate_queue_payload({"url": "file:///secret"})
        with self.assertRaisesRegex(PreflightError, "picker id"):
            validate_queue_payload(
                {
                    "url": "https://example.com/video",
                    "media_item_id": "not safe!",
                }
            )

    def test_audio_background_selection_is_verified_against_cached_picker(self):
        info = SimpleNamespace(
            title="Demo",
            platform="Test",
            channel="",
            duration_str="",
            total_secs=10,
            url="https://cdn.example/demo",
            qualities=[
                SimpleNamespace(
                    name="best",
                    resolution="",
                    format_type="hls",
                    bandwidth=0,
                    tracks=[
                        SimpleNamespace(
                            id="video",
                            kind="video",
                            label="Video",
                            language="",
                            codec="h264",
                            default=True,
                            autoselect=True,
                        ),
                        SimpleNamespace(
                            id="en",
                            kind="audio",
                            label="English",
                            language="en",
                            codec="aac",
                            default=True,
                            autoselect=False,
                        )
                    ],
                    audio_url="",
                )
            ],
        )
        picker = serialize_stream_picker(info, "https://example.com/demo")
        audio_id = picker["background_audio"][0]["id"]
        selected = normalize_media_selection(
            {
                "url": "https://example.com/demo",
                "validation_id": "validation",
                "media_item_id": "quality:0",
                "background_audio_id": audio_id,
            },
            picker,
        )
        self.assertEqual(selected["background_audio_id"], audio_id)
        self.assertEqual(selected["media_item_type"], "video")
        with self.assertRaisesRegex(PreflightError, "not in the picker"):
            normalize_media_selection(
                {
                    "url": "https://example.com/demo",
                    "validation_id": "validation",
                    "media_item_id": "quality:0",
                    "background_audio_id": "audio:unknown",
                },
                picker,
            )


if __name__ == "__main__":
    unittest.main()
