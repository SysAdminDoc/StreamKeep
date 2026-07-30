import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from streamkeep.metadata import (
    METADATA_SCHEMA,
    METADATA_SCHEMA_VERSION,
    MetadataSaver,
    MetadataWriteError,
    build_archival_provenance,
    canonical_webpage_url,
    load_metadata_sidecar,
)
from streamkeep.models import QualityInfo, StreamInfo
from streamkeep.postprocess.bundle_worker import BundleWorker


_SIGNED_TWITCH_URL = (
    "https://usher.ttvnw.net/vod/987654321.m3u8"
    "?token=%7Bdelivery-secret%7D&sig=SUPERSECRET&allow_source=true"
)


def _twitch_info():
    return StreamInfo(
        platform="Twitch",
        channel="ExampleStreamer",
        title="A public title",
        url=_SIGNED_TWITCH_URL,
        qualities=[
            QualityInfo(
                name="source",
                resolution="1920x1080",
                bandwidth=6_000_000,
                url=_SIGNED_TWITCH_URL,
                format_type="hls",
            )
        ],
        total_secs=3_600,
        start_time="2026-07-29T12:00:00Z",
        thumbnail_url=(
            "https://static-cdn.jtvnw.net/thumb.jpg"
            "?token=THUMBSECRET"
        ),
    )


class PublicMetadataTests(unittest.TestCase):
    def test_signed_twitch_delivery_url_becomes_stable_provenance(self):
        provenance = build_archival_provenance(_twitch_info())

        self.assertEqual(provenance.platform, "Twitch")
        self.assertEqual(provenance.source_id, "vod:987654321")
        self.assertEqual(
            provenance.webpage_url,
            "https://www.twitch.tv/videos/987654321",
        )

    def test_twitch_lookalike_domain_is_not_treated_as_twitch(self):
        result = canonical_webpage_url(
            "https://eviltwitch.tv/videos/123?token=SECRET"
        )
        self.assertEqual(result, "https://eviltwitch.tv/videos/123")

    def test_credential_shaped_explicit_source_id_is_rejected(self):
        info = _twitch_info()
        info.source_id = "token:IDENTITYSECRET"
        provenance = build_archival_provenance(info)
        self.assertEqual(provenance.source_id, "vod:987654321")
        self.assertNotIn("IDENTITYSECRET", json.dumps(provenance.to_dict()))

    def test_metadata_sidecar_is_versioned_and_never_persists_delivery_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "streamkeep.image_fetch.download_image",
                return_value=False,
            ):
                saved = MetadataSaver.save(tmpdir, _twitch_info())

            payload = json.loads(
                Path(saved["metadata_path"]).read_text(encoding="utf-8")
            )
            serialized = json.dumps(payload)
            self.assertEqual(payload["schema"], METADATA_SCHEMA)
            self.assertEqual(
                payload["schema_version"], METADATA_SCHEMA_VERSION
            )
            self.assertEqual(
                payload["provenance"],
                {
                    "platform": "Twitch",
                    "source_id": "vod:987654321",
                    "webpage_url": (
                        "https://www.twitch.tv/videos/987654321"
                    ),
                },
            )
            self.assertNotIn("SUPERSECRET", serialized)
            self.assertNotIn("delivery-secret", serialized)
            self.assertNotIn("THUMBSECRET", serialized)
            self.assertNotIn('"url"', serialized)
            self.assertNotIn("thumbnail_url", serialized)
            self.assertNotIn('"token"', serialized.lower())
            self.assertNotIn('"sig"', serialized.lower())

    def test_legacy_sidecar_is_readable_but_scrubbed(self):
        legacy = {
            "platform": "Twitch",
            "channel": "ExampleStreamer",
            "title": "Legacy",
            "url": _SIGNED_TWITCH_URL,
            "thumbnail": (
                "https://static-cdn.jtvnw.net/a.jpg?sig=THUMBSECRET"
            ),
            "headers": {"Authorization": "Bearer HEADERSECRET"},
            "cookies": "auth-token=COOKIESECRET",
            "internal_credential": "CREDENTIALSECRET",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "metadata.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")

            payload = load_metadata_sidecar(path)

        serialized = json.dumps(payload)
        self.assertEqual(payload["schema_version"], METADATA_SCHEMA_VERSION)
        self.assertEqual(
            payload["provenance"]["webpage_url"],
            "https://www.twitch.tv/videos/987654321",
        )
        self.assertEqual(
            payload["provenance"]["source_id"], "vod:987654321"
        )
        for secret in (
            "SUPERSECRET",
            "delivery-secret",
            "THUMBSECRET",
            "HEADERSECRET",
            "COOKIESECRET",
            "CREDENTIALSECRET",
        ):
            self.assertNotIn(secret, serialized)

    def test_nfo_uses_stable_unique_id_and_local_thumbnail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "thumbnail.jpg").write_bytes(b"jpeg")

            path = MetadataSaver.write_nfo(
                tmpdir, _twitch_info(), file_base="recording"
            )
            text = Path(path).read_text(encoding="utf-8")

        self.assertIn(
            '<uniqueid type="twitch" default="true">'
            "vod:987654321</uniqueid>",
            text,
        )
        self.assertIn("<thumb>thumbnail.jpg</thumb>", text)
        self.assertNotIn("<trailer", text.lower())
        self.assertNotIn("usher.ttvnw.net", text)
        self.assertNotIn("SUPERSECRET", text)

    def test_atomic_write_failure_is_surfaced_and_temp_file_is_removed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "streamkeep.metadata.os.replace",
                side_effect=PermissionError("read only"),
            ):
                with self.assertRaisesRegex(
                    MetadataWriteError, "Could not write public sidecar"
                ):
                    MetadataSaver.save(
                        tmpdir,
                        StreamInfo(
                            platform="Direct",
                            title="Write failure",
                            webpage_url="https://example.com/watch/1",
                        ),
                    )

            self.assertFalse(Path(tmpdir, "metadata.json.tmp").exists())


class ShareBundlePrivacyTests(unittest.TestCase):
    def test_bundle_scrubs_legacy_json_nfo_and_generic_sidecars(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "recording"
            source.mkdir()
            (source / "video.mp4").write_bytes(b"media")
            (source / "metadata.json").write_text(
                json.dumps({
                    "platform": "Twitch",
                    "channel": "ExampleStreamer",
                    "title": "Legacy",
                    "url": _SIGNED_TWITCH_URL,
                    "http_headers": {
                        "Authorization": "Bearer HEADERSECRET",
                    },
                    "cookies": "session=COOKIESECRET",
                }),
                encoding="utf-8",
            )
            (source / "recording.nfo").write_text(
                "<movie>\n"
                f"  <trailer>{_SIGNED_TWITCH_URL}</trailer>\n"
                "  <thumb>https://images.example/a.jpg"
                "?sig=THUMBSECRET</thumb>\n"
                "</movie>\n",
                encoding="utf-8",
            )
            (source / "recording.info.json").write_text(
                json.dumps({
                    "asset": (
                        "https://cdn.example/video.mp4"
                        "?X-Amz-Credential=AMZIDENTITY"
                        "&X-Amz-Signature=AMZSECRET"
                    ),
                    "http_headers": {
                        "Authorization": "Bearer OTHERHEADERSECRET",
                    },
                    "cookie": "auth=OTHERCOOKIESECRET",
                    (
                        "https://keys.example/item"
                        "?sig=KEYSIGNATURESECRET"
                    ): "public",
                }),
                encoding="utf-8",
            )
            output = Path(tmpdir) / "share.zip"
            results = []
            worker = BundleWorker(str(source), str(output))
            worker.done.connect(
                lambda success, detail: results.append((success, detail))
            )

            worker.run()

            self.assertEqual(results, [(True, str(output))])
            with zipfile.ZipFile(output) as archive:
                metadata = json.loads(archive.read("metadata.json"))
                nfo = archive.read("recording.nfo").decode("utf-8")
                generic = json.loads(
                    archive.read("recording.info.json")
                )
                public_text = "\n".join(
                    archive.read(name).decode("utf-8", errors="ignore")
                    for name in archive.namelist()
                    if Path(name).suffix.lower() != ".mp4"
                )

        self.assertEqual(metadata["schema"], METADATA_SCHEMA)
        self.assertEqual(
            metadata["provenance"]["webpage_url"],
            "https://www.twitch.tv/videos/987654321",
        )
        self.assertNotIn("<trailer", nfo.lower())
        self.assertIn("<thumb>thumbnail.jpg</thumb>", nfo)
        self.assertEqual(
            generic["asset"],
            "https://cdn.example/video.mp4 [***REDACTED***]",
        )
        for secret in (
            "SUPERSECRET",
            "delivery-secret",
            "HEADERSECRET",
            "COOKIESECRET",
            "THUMBSECRET",
            "AMZIDENTITY",
            "AMZSECRET",
            "OTHERHEADERSECRET",
            "OTHERCOOKIESECRET",
            "KEYSIGNATURESECRET",
        ):
            self.assertNotIn(secret, public_text)
        self.assertNotIn('"token"', public_text.lower())
        self.assertNotIn('"sig"', public_text.lower())
        self.assertNotIn("authorization", public_text.lower())

    def test_oversized_text_sidecar_is_blocked_from_bundle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "recording"
            source.mkdir()
            (source / "video.mp4").write_bytes(b"media")
            (source / "unsafe.txt").write_text(
                "token=SHOULDNOTBESHARED", encoding="utf-8"
            )
            output = Path(tmpdir) / "share.zip"
            progress = []
            results = []
            worker = BundleWorker(str(source), str(output))
            worker.progress.connect(
                lambda percent, detail: progress.append((percent, detail))
            )
            worker.done.connect(
                lambda success, detail: results.append((success, detail))
            )

            with mock.patch(
                "streamkeep.postprocess.bundle_worker."
                "_MAX_SHARE_SIDECAR_BYTES",
                4,
            ):
                worker.run()

            self.assertEqual(results, [(True, str(output))])
            with zipfile.ZipFile(output) as archive:
                self.assertIn("video.mp4", archive.namelist())
                self.assertNotIn("unsafe.txt", archive.namelist())
            self.assertTrue(
                any("Blocked unsafe or oversized sidecar" in detail
                    for _percent, detail in progress)
            )

    def test_malformed_json_sidecar_is_blocked_from_bundle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "recording"
            source.mkdir()
            (source / "video.mp4").write_bytes(b"media")
            (source / "malformed.json").write_text(
                '{"headers": {"Opaque": "UNCLASSIFIEDSECRET"}',
                encoding="utf-8",
            )
            output = Path(tmpdir) / "share.zip"
            progress = []
            worker = BundleWorker(str(source), str(output))
            worker.progress.connect(
                lambda percent, detail: progress.append((percent, detail))
            )

            worker.run()

            with zipfile.ZipFile(output) as archive:
                self.assertNotIn("malformed.json", archive.namelist())
            self.assertTrue(
                any("Blocked unsafe or oversized sidecar" in detail
                    for _percent, detail in progress)
            )


if __name__ == "__main__":
    unittest.main()
