import tempfile
import unittest
from pathlib import Path

from streamkeep import feed, gallery


class GalleryTests(unittest.TestCase):
    def test_rss_enclosure_escapes_xml_attribute_delimiters(self):
        xml = feed.generate_rss(
            [{"share_id": 'id"<&', "media_path": "", "duration_secs": 1}],
            'https://media.example/"<&',
        )

        self.assertIn(
            'url="https://media.example/&quot;&lt;&amp;/media/id&quot;&lt;&amp;"',
            xml,
        )

    def test_gallery_escapes_base_url_and_share_id_in_attributes(self):
        share_id = 'id"<&'
        entry = {
            "share_id": share_id,
            "path": "/p",
            "title": "Title",
            "channel": "Channel",
            "media": "clip.mp4",
        }
        html = gallery.render_gallery_html('https://media.example/"<&', [entry])
        share_html = gallery.render_share_html(
            share_id,
            'https://media.example/"<&',
            info=entry,
        )

        escaped_base = "https://media.example/&quot;&lt;&amp;"
        escaped_id = "id&quot;&lt;&amp;"
        self.assertIn(f'href="{escaped_base}/share/{escaped_id}"', html)
        self.assertIn(f'href="{escaped_base}/gallery"', share_html)
        self.assertIn(f'src="{escaped_base}/media/{escaped_id}"', share_html)

    def test_gallery_cards_render_image_set_thumbnails(self):
        html = gallery.render_gallery_html(
            "http://127.0.0.1:8787",
            [{
                "share_id": "image-set",
                "title": "Image set",
                "media": "cover.jpg",
            }],
        )

        self.assertIn('class="thumb"', html)
        self.assertIn("loading=\"lazy\"", html)
        self.assertIn("image/jpeg", gallery._media_type("cover.jpg"))

    def test_serve_media_range_returns_partial_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            media = Path(tmpdir) / "clip.webm"
            media.write_bytes(b"0123456789")

            data, status, headers = gallery.serve_media_range(
                str(media),
                "bytes=2-5",
            )

            self.assertEqual(status, 206)
            self.assertEqual(data, b"2345")
            self.assertEqual(headers["Content-Range"], "bytes 2-5/10")
            self.assertEqual(headers["Content-Length"], "4")

    def test_serve_media_range_rejects_invalid_ranges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            media = Path(tmpdir) / "clip.mp4"
            media.write_bytes(b"0123456789")

            data, status, headers = gallery.serve_media_range(
                str(media),
                "bytes=99-100",
            )

            self.assertIsNone(data)
            self.assertEqual(status, 416)
            self.assertEqual(headers["Content-Range"], "bytes */10")


    def test_range_request_capped_to_max_chunk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            media = Path(tmpdir) / "large.mp4"
            content = b"x" * 100
            media.write_bytes(content)

            old_chunk = gallery._MAX_RANGE_CHUNK
            try:
                gallery._MAX_RANGE_CHUNK = 20
                data, status, headers = gallery.serve_media_range(
                    str(media), "bytes=0-99",
                )
                self.assertEqual(status, 206)
                self.assertEqual(len(data), 20)
                self.assertEqual(headers["Content-Length"], "20")
                self.assertEqual(headers["Content-Range"], "bytes 0-19/100")
            finally:
                gallery._MAX_RANGE_CHUNK = old_chunk

    def test_multi_range_returns_416(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            media = Path(tmpdir) / "clip.mp4"
            media.write_bytes(b"0123456789")

            data, status, headers = gallery.serve_media_range(
                str(media), "bytes=0-3,5-9",
            )
            self.assertIsNone(data)
            self.assertEqual(status, 416)
if __name__ == "__main__":
    unittest.main()
