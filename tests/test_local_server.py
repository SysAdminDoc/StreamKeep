import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication

from streamkeep import db
from streamkeep.local_server import LocalCompanionServer


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.server = LocalCompanionServer()
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.app.processEvents()

    def _open(self, path, *, token=None, headers=None, method="GET", server=None, data=None):
        server = server or self.server
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        body = None
        if data is not None:
            request_headers.setdefault("Content-Type", "application/json")
            body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.port}{path}",
            headers=request_headers,
            method=method,
            data=body,
        )
        return urllib.request.urlopen(req, timeout=5)

    def test_rejects_dns_rebinding_host_before_auth(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._open(
                "/ping",
                token=self.server.token,
                headers={"Host": "attacker.example"},
            )

        self.assertEqual(ctx.exception.code, 403)

    def test_local_only_rejects_lan_host_header(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._open(
                "/ping",
                token=self.server.token,
                headers={"Host": "192.168.50.10:8765"},
            )

        self.assertEqual(ctx.exception.code, 403)

    def test_lan_mode_accepts_configured_lan_host(self):
        lan_server = LocalCompanionServer(
            bind_lan=True,
            allowed_hosts={"192.168.50.10", "streamkeepbox.local"},
        )
        lan_server.start()
        try:
            with self._open(
                "/ping",
                server=lan_server,
                token=lan_server.token,
                headers={"Host": "192.168.50.10:8765"},
            ) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        finally:
            lan_server.stop()

        self.assertEqual(payload, {"ok": True, "app": "StreamKeep"})

    def test_lan_mode_keeps_rejecting_hostile_host(self):
        lan_server = LocalCompanionServer(
            bind_lan=True,
            allowed_hosts={"192.168.50.10"},
        )
        lan_server.start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._open(
                    "/ping",
                    server=lan_server,
                    token=lan_server.token,
                    headers={"Host": "attacker.example"},
                )
        finally:
            lan_server.stop()

        self.assertEqual(ctx.exception.code, 403)

    def test_ping_requires_bearer_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._open("/ping")

        self.assertEqual(ctx.exception.code, 401)

    def test_authenticated_ping_allows_localhost_origin(self):
        origin = "http://localhost:8765"
        with self._open(
            "/ping",
            token=self.server.token,
            headers={"Origin": origin},
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        self.assertEqual(payload, {"ok": True, "app": "StreamKeep"})
        self.assertEqual(resp.headers["Access-Control-Allow-Origin"], origin)

    def test_cors_does_not_echo_untrusted_origin(self):
        with self._open(
            "/api/status",
            headers={"Origin": "https://attacker.example"},
            method="OPTIONS",
        ) as resp:
            allowed = resp.headers["Access-Control-Allow-Origin"]

        self.assertEqual(resp.status, 204)
        self.assertEqual(allowed, "http://localhost")

    def test_lan_mode_echoes_configured_lan_origin(self):
        lan_server = LocalCompanionServer(
            bind_lan=True,
            allowed_hosts={"192.168.50.10"},
        )
        lan_server.start()
        origin = "http://192.168.50.10:8765"
        try:
            with self._open(
                "/api/status",
                server=lan_server,
                headers={"Host": "192.168.50.10:8765", "Origin": origin},
                method="OPTIONS",
            ) as resp:
                allowed = resp.headers["Access-Control-Allow-Origin"]
        finally:
            lan_server.stop()

        self.assertEqual(resp.status, 204)
        self.assertEqual(allowed, origin)

    def test_status_and_failure_actions_expose_retryable_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            recovery_server = LocalCompanionServer()
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                job_id = db.save_failed_job(
                    url="https://example.com/video",
                    platform="Example",
                    title="Broken video",
                    stage="fetch",
                    error="temporary failure",
                    output_dir=str(Path(tmpdir) / "recording"),
                    queue_data={"url": "https://example.com/video", "title": "Broken video"},
                )
                recovery_server.state_provider = lambda: {
                    "failures": db.load_failed_jobs()
                }
                recovery_server.start()
                try:
                    with self._open(
                        "/api/status",
                        server=recovery_server,
                        token=recovery_server.token,
                    ) as resp:
                        status_payload = json.loads(resp.read().decode("utf-8"))

                    with self._open(
                        "/api/failures/retry",
                        server=recovery_server,
                        token=recovery_server.token,
                        method="POST",
                        data={"id": job_id},
                    ) as resp:
                        retry_payload = json.loads(resp.read().decode("utf-8"))

                    retried = db.load_failed_job(job_id)

                    with self._open(
                        "/api/failures/discard",
                        server=recovery_server,
                        token=recovery_server.token,
                        method="POST",
                        data={"id": job_id},
                    ) as resp:
                        discard_payload = json.loads(resp.read().decode("utf-8"))

                    active_after_discard = db.load_failed_jobs()
                finally:
                    recovery_server.stop()

            self.assertEqual(status_payload["failures"][0]["id"], job_id)
            self.assertTrue(retry_payload["ok"])
            self.assertEqual(retried["status"], "retrying")
            self.assertEqual(retried["retry_count"], 1)
            self.assertTrue(discard_payload["ok"])
            self.assertEqual(active_after_discard, [])


if __name__ == "__main__":
    unittest.main()
