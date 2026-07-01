import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication

from streamkeep import db
from streamkeep.local_server import (
    SCOPE_QUEUE,
    SCOPE_RECOVERY,
    SCOPE_STATUS,
    LocalCompanionServer,
)


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

    def _open_json(self, path, **kwargs):
        with self._open(path, **kwargs) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status

    def _expect_error(self, path, expected_code, **kwargs):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._open(path, **kwargs)
        self.assertEqual(ctx.exception.code, expected_code)
        try:
            return json.loads(ctx.exception.read().decode("utf-8"))
        except Exception:
            return {}

    def test_rejects_dns_rebinding_host_before_auth(self):
        self._expect_error(
            "/ping", 403,
            token=self.server.token,
            headers={"Host": "attacker.example"},
        )

    def test_local_only_rejects_lan_host_header(self):
        self._expect_error(
            "/ping", 403,
            token=self.server.token,
            headers={"Host": "192.168.50.10:8765"},
        )

    def test_lan_mode_accepts_configured_lan_host(self):
        lan_server = LocalCompanionServer(
            bind_lan=True,
            allowed_hosts={"192.168.50.10", "streamkeepbox.local"},
        )
        lan_server.start()
        try:
            payload, _ = self._open_json(
                "/ping",
                server=lan_server,
                token=lan_server.token,
                headers={"Host": "192.168.50.10:8765"},
            )
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
            self._expect_error(
                "/ping", 403,
                server=lan_server,
                token=lan_server.token,
                headers={"Host": "attacker.example"},
            )
        finally:
            lan_server.stop()

    def test_ping_requires_bearer_token(self):
        err = self._expect_error("/ping", 401)
        self.assertEqual(err.get("err"), "token_invalid")

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

    # ── Token rotation ────────────────────────────────────────────

    def test_rotate_token_invalidates_old_token(self):
        old_token = self.server.token
        payload, _ = self._open_json("/ping", token=old_token)
        self.assertTrue(payload["ok"])

        new_token = self.server.rotate_token()
        self.assertNotEqual(old_token, new_token)

        err = self._expect_error("/ping", 401, token=old_token)
        self.assertEqual(err["err"], "token_invalid")

        payload, _ = self._open_json("/ping", token=new_token)
        self.assertTrue(payload["ok"])

    def test_expired_token_returns_pairing_error(self):
        old_token = self.server.token
        self.server.rotate_token()
        err = self._expect_error("/ping", 401, token=old_token)
        self.assertEqual(err["err"], "token_invalid")
        self.assertIn("Re-pair", err["message"])

    # ── Scoped tokens ─────────────────────────────────────────────

    def test_status_scope_allows_read_endpoints(self):
        tok = self.server.create_scoped_token({SCOPE_STATUS})
        for path in ("/api/status", "/api/library", "/api/monitor"):
            payload, code = self._open_json(path, token=tok)
            self.assertEqual(code, 200, f"{path} should be allowed with status scope")

    def test_status_scope_denies_queue_send(self):
        tok = self.server.create_scoped_token({SCOPE_STATUS})
        err = self._expect_error(
            "/send_url", 403, token=tok, method="POST",
            data={"url": "https://example.com", "action": "queue"},
        )
        self.assertEqual(err["err"], "scope_denied")

    def test_queue_scope_allows_send_url(self):
        tok = self.server.create_scoped_token({SCOPE_QUEUE})
        payload, _ = self._open_json(
            "/send_url", token=tok, method="POST",
            data={"url": "https://example.com/video", "action": "fetch"},
        )
        self.assertTrue(payload["ok"])

    def test_queue_scope_denies_status_read(self):
        tok = self.server.create_scoped_token({SCOPE_QUEUE})
        err = self._expect_error("/api/status", 403, token=tok)
        self.assertEqual(err["err"], "scope_denied")

    def test_recovery_scope_denies_queue_and_status(self):
        tok = self.server.create_scoped_token({SCOPE_RECOVERY})
        self._expect_error("/api/status", 403, token=tok)
        self._expect_error(
            "/send_url", 403, token=tok, method="POST",
            data={"url": "https://example.com", "action": "queue"},
        )

    def test_master_token_has_all_scopes(self):
        payload, _ = self._open_json("/api/status", token=self.server.token)
        self.assertTrue(payload["ok"])
        payload, _ = self._open_json(
            "/send_url", token=self.server.token, method="POST",
            data={"url": "https://example.com/video", "action": "fetch"},
        )
        self.assertTrue(payload["ok"])

    def test_revoked_scoped_token_rejected(self):
        tok = self.server.create_scoped_token({SCOPE_STATUS})
        payload, _ = self._open_json("/api/status", token=tok)
        self.assertTrue(payload["ok"])

        self.server.revoke_token(tok)
        self._expect_error("/api/status", 401, token=tok)

    # ── Failure actions (with scopes) ─────────────────────────────

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
                    status_payload, _ = self._open_json(
                        "/api/status",
                        server=recovery_server,
                        token=recovery_server.token,
                    )

                    retry_payload, _ = self._open_json(
                        "/api/failures/retry",
                        server=recovery_server,
                        token=recovery_server.token,
                        method="POST",
                        data={"id": job_id},
                    )

                    retried = db.load_failed_job(job_id)

                    discard_payload, _ = self._open_json(
                        "/api/failures/discard",
                        server=recovery_server,
                        token=recovery_server.token,
                        method="POST",
                        data={"id": job_id},
                    )

                    active_after_discard = db.load_failed_jobs()
                finally:
                    recovery_server.stop()

            self.assertEqual(status_payload["failures"][0]["id"], job_id)
            self.assertTrue(retry_payload["ok"])
            self.assertEqual(retried["status"], "retrying")
            self.assertEqual(retried["retry_count"], 1)
            self.assertTrue(discard_payload["ok"])
            self.assertEqual(active_after_discard, [])

    def test_headless_server_with_fixed_token(self):
        """Smoke test: server with a fixed token works like service mode."""
        from streamkeep.local_server import ALL_SCOPES
        srv = LocalCompanionServer()
        srv._token_store.remove(srv.token)
        fixed = "deadbeefcafebabe1234567890abcdef"
        srv.token = fixed
        srv._token_store.add(fixed, ALL_SCOPES)
        srv.start()
        try:
            payload, _ = self._open_json("/ping", server=srv, token=fixed)
            self.assertTrue(payload["ok"])
            self._expect_error("/ping", 401, server=srv, token="wrong-token")
        finally:
            srv.stop()

    def test_recovery_scope_allows_failure_actions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "library.db"
            recovery_server = LocalCompanionServer()
            with mock.patch.object(db, "DB_PATH", db_path):
                db.init_db()
                job_id = db.save_failed_job(
                    url="https://example.com/video",
                    platform="Example",
                    title="Test",
                    stage="fetch",
                    error="test error",
                    output_dir=str(Path(tmpdir) / "out"),
                    queue_data={},
                )
                recovery_server.state_provider = lambda: {}
                recovery_server.start()
                try:
                    recovery_tok = recovery_server.create_scoped_token({SCOPE_RECOVERY})
                    payload, _ = self._open_json(
                        "/api/failures/retry",
                        server=recovery_server,
                        token=recovery_tok,
                        method="POST",
                        data={"id": job_id},
                    )
                    self.assertTrue(payload["ok"])

                    status_tok = recovery_server.create_scoped_token({SCOPE_STATUS})
                    err = self._expect_error(
                        "/api/failures/discard", 403,
                        server=recovery_server,
                        token=status_tok,
                        method="POST",
                        data={"id": job_id},
                    )
                    self.assertEqual(err["err"], "scope_denied")
                finally:
                    recovery_server.stop()


if __name__ == "__main__":
    unittest.main()
