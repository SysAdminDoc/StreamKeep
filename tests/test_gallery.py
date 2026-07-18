import tempfile
import unittest
from pathlib import Path

from streamkeep import gallery


class GalleryTests(unittest.TestCase):
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

    def test_share_id_has_128_bits_of_entropy(self):
        ids = {gallery.generate_share_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)  # no collisions
        for share_id in ids:
            self.assertEqual(len(share_id), 32)  # 128 bits as hex
            self.assertTrue(all(c in "0123456789abcdef" for c in share_id))

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

    def test_shared_registry_thread_safety(self):
        import threading

        gallery.register_shared("a", "/p", "t", "c", "m")
        errors = []

        def _iterate():
            try:
                for _ in range(50):
                    gallery.all_shared()
                    gallery.render_gallery_html()
            except RuntimeError as e:
                errors.append(e)

        def _mutate():
            for i in range(50):
                sid = f"tmp_{i}"
                gallery.register_shared(sid, "/p", "t", "c", "m")
                gallery.unregister_shared(sid)

        t1 = threading.Thread(target=_iterate)
        t2 = threading.Thread(target=_mutate)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(errors, [])
        gallery.unregister_shared("a")


if __name__ == "__main__":
    unittest.main()
