import json
import http.client
import secrets
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication, Qt

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

    def _open(
        self, path, *, token=None, headers=None, method="GET", server=None,
        data=None, freshness=True,
    ):
        server = server or self.server
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        body = None
        if data is not None:
            request_headers.setdefault("Content-Type", "application/json")
            body = json.dumps(data).encode("utf-8")
        if method in ("POST", "PATCH", "DELETE") and freshness:
            request_headers.setdefault("X-StreamKeep-Timestamp", str(int(time.time())))
            request_headers.setdefault("X-StreamKeep-Nonce", secrets.token_urlsafe(18))
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

    def test_rejects_duplicate_or_malformed_host_authority(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        connection.putrequest("GET", "/ping", skip_host=True)
        connection.putheader("Host", "127.0.0.1")
        connection.putheader("Host", "attacker.example")
        connection.endheaders()
        response = connection.getresponse()
        try:
            self.assertEqual(response.status, 403)
            self.assertEqual(json.loads(response.read())["err"], "host_denied")
        finally:
            connection.close()

        malformed = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        malformed.putrequest("GET", "/ping", skip_host=True)
        malformed.putheader("Host", "localhost:not-port")
        malformed.endheaders()
        malformed_response = malformed.getresponse()
        try:
            self.assertEqual(malformed_response.status, 403)
            self.assertEqual(json.loads(malformed_response.read())["err"], "host_denied")
        finally:
            malformed.close()

    def test_local_only_rejects_lan_host_header(self):
        self._expect_error(
            "/ping", 403,
            token=self.server.token,
            headers={"Host": "192.168.50.10:8765"},
        )

    def test_lan_mode_requires_explicit_https_reverse_proxy_origin(self):
        for origin in ("", "http://streamkeepbox.local", "https://streamkeepbox.local/api"):
            with self.subTest(origin=origin):
                with self.assertRaisesRegex(ValueError, "HTTPS reverse-proxy origin"):
                    LocalCompanionServer(bind_lan=True, external_origin=origin)

    def test_lan_mode_accepts_only_trusted_proxy_forwarding(self):
        lan_server = LocalCompanionServer(
            bind_lan=True,
            external_origin="https://streamkeepbox.local",
        )
        lan_server.start()
        try:
            payload, _ = self._open_json(
                "/ping",
                server=lan_server,
                token=lan_server.token,
                headers={
                    "Host": "streamkeepbox.local",
                    "Origin": "https://streamkeepbox.local",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "streamkeepbox.local",
                },
            )
            advertised_url = lan_server.url
        finally:
            lan_server.stop()

        self.assertEqual(payload, {"ok": True, "app": "StreamKeep"})
        self.assertEqual(lan_server._bind_addr, "127.0.0.1")
        self.assertEqual(advertised_url, "https://streamkeepbox.local/")

    def test_lan_mode_rejects_mismatched_forwarded_authority(self):
        lan_server = LocalCompanionServer(
            bind_lan=True,
            external_origin="https://streamkeepbox.local",
        )
        lan_server.start()
        try:
            error = self._expect_error(
                "/ping",
                403,
                server=lan_server,
                token=lan_server.token,
                headers={
                    "Host": "streamkeepbox.local",
                    "Origin": "https://streamkeepbox.local",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "attacker.example",
                },
            )
        finally:
            lan_server.stop()
        self.assertEqual(error["err"], "transport_denied")

    def test_lan_mode_rejects_direct_plaintext_external_request(self):
        lan_server = LocalCompanionServer(
            bind_lan=True,
            external_origin="https://streamkeepbox.local",
        )
        lan_server.start()
        try:
            self._expect_error(
                "/ping", 403,
                server=lan_server,
                token=lan_server.token,
                headers={
                    "Host": "streamkeepbox.local",
                    "Origin": "https://streamkeepbox.local",
                },
            )
        finally:
            lan_server.stop()

    def test_lan_mode_requires_https_pairing_before_scoped_access(self):
        lan_server = LocalCompanionServer(
            bind_lan=True,
            external_origin="https://streamkeepbox.local",
        )
        lan_server.start()
        boundary_headers = {
            "Host": "streamkeepbox.local",
            "Origin": "https://streamkeepbox.local",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "streamkeepbox.local",
        }
        try:
            code = lan_server.create_pairing_code()
            paired, status = self._open_json(
                "/pair",
                server=lan_server,
                method="POST",
                headers=boundary_headers,
                data={"code": code, "scopes": [SCOPE_STATUS]},
            )
            payload, _ = self._open_json(
                "/ping",
                server=lan_server,
                token=paired["token"],
                headers=boundary_headers,
            )
        finally:
            lan_server.stop()

        self.assertEqual(status, 201)
        self.assertEqual(paired["scopes"], [SCOPE_STATUS])
        self.assertTrue(payload["ok"])

    def test_ping_requires_bearer_token(self):
        err = self._expect_error("/ping", 401)
        self.assertEqual(err.get("err"), "token_invalid")

    def test_loopback_rejects_spoofed_forwarding_headers(self):
        error = self._expect_error(
            "/ping",
            403,
            token=self.server.token,
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "localhost",
            },
        )
        self.assertEqual(error["err"], "transport_denied")

    def test_one_time_pairing_mints_origin_bound_scoped_token(self):
        code = self.server.create_pairing_code()
        extension_origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
        paired, status = self._open_json(
            "/pair",
            method="POST",
            headers={"Origin": extension_origin},
            data={"code": code, "scopes": [SCOPE_STATUS, SCOPE_QUEUE]},
        )
        self.assertEqual(status, 201)
        self.assertGreaterEqual(len(paired["token"]), 43)
        self.assertEqual(paired["scopes"], ["queue", "status"])

        payload, _ = self._open_json(
            "/ping",
            token=paired["token"],
            headers={"Origin": extension_origin},
        )
        self.assertTrue(payload["ok"])
        self._expect_error(
            "/ping",
            401,
            token=paired["token"],
            headers={"Origin": "chrome-extension://ponmlkjihgfedcbaponmlkjihgfedcba"},
        )
        reused = self._expect_error(
            "/pair",
            401,
            method="POST",
            headers={"Origin": extension_origin},
            data={"code": code},
        )
        self.assertEqual(reused["err"], "pairing_invalid")

    def test_pairing_rejects_host_origin_and_cross_site_substitution(self):
        code = self.server.create_pairing_code()
        cases = (
            ({"Host": "attacker.example"}, "host_denied"),
            ({"Origin": "https://attacker.example"}, "origin_denied"),
            ({"Sec-Fetch-Site": "cross-site"}, "cross_site_denied"),
        )
        for headers, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                error = self._expect_error(
                    "/pair", 403, method="POST", headers=headers, data={"code": code}
                )
                self.assertEqual(error["err"], expected_error)

        paired, status = self._open_json(
            "/pair", method="POST", data={"code": code}
        )
        self.assertEqual(status, 201)
        self.assertTrue(paired["token"])

    def test_pairing_code_locks_after_repeated_wrong_attempts(self):
        code = self.server.create_pairing_code()
        for _ in range(5):
            self._expect_error(
                "/pair", 401, method="POST", data={"code": "wrong-code"}
            )
        self._expect_error("/pair", 401, method="POST", data={"code": code})

    def test_pairing_requires_freshness_without_consuming_the_code(self):
        code = self.server.create_pairing_code()
        error = self._expect_error(
            "/pair", 400, method="POST", data={"code": code}, freshness=False
        )
        self.assertEqual(error["err"], "request_timestamp_invalid")
        paired, status = self._open_json(
            "/pair", method="POST", data={"code": code}
        )
        self.assertEqual(status, 201)
        self.assertTrue(paired["token"])

    def test_queue_rejects_ssrf_target_urls(self):
        """A submitted URL resolving to loopback/metadata/private space is
        refused (V30). IP-literal targets need no DNS, so this is offline."""
        for path, body in (
            ("/api/queue", {"url": "http://127.0.0.1/secret"}),
            ("/api/queue", {"url": "http://169.254.169.254/latest/meta-data/"}),
            ("/send_url", {"url": "http://192.168.1.20:8080/x", "action": "queue"}),
        ):
            with self.subTest(url=body["url"]):
                error = self._expect_error(
                    path, 400, token=self.server.token, method="POST", data=body,
                )
                self.assertEqual(error["err"], "url_not_allowed")

    def test_queue_allows_private_target_when_opted_in(self):
        server = LocalCompanionServer(allow_private_network=True)
        server.start()
        try:
            payload, status = self._open_json(
                "/api/queue", token=server.token, method="POST", server=server,
                data={"url": "http://192.168.1.20:8080/x"},
            )
            # No queue_submitter is wired, so the handler emits the signal and
            # returns 200 rather than 400 — proving the SSRF gate let it pass.
            self.assertTrue(payload["ok"])
            self.assertEqual(status, 200)
        finally:
            server.stop()

    def test_every_mutating_endpoint_requires_freshness_proof(self):
        cases = (
            ("/send_url", {"url": "https://example.com/a", "action": "queue"}),
            ("/api/queue", {"url": "https://example.com/a"}),
            ("/api/jobs/cancel", {"job_id": "job-a"}),
            ("/api/failures/retry", {"id": 1}),
            ("/api/failures/discard", {"id": 1}),
        )
        for path, body in cases:
            with self.subTest(path=path):
                error = self._expect_error(
                    path,
                    400,
                    token=self.server.token,
                    method="POST",
                    data=body,
                    freshness=False,
                )
                self.assertEqual(error["err"], "request_timestamp_invalid")

    def test_mutation_rejects_cross_site_fetch_metadata_and_stale_timestamp(self):
        cross_site = self._expect_error(
            "/send_url",
            403,
            token=self.server.token,
            method="POST",
            headers={"Sec-Fetch-Site": "cross-site"},
            data={"url": "https://example.com/a"},
        )
        self.assertEqual(cross_site["err"], "cross_site_denied")
        stale = self._expect_error(
            "/send_url",
            400,
            token=self.server.token,
            method="POST",
            headers={
                "X-StreamKeep-Timestamp": str(int(time.time()) - 1000),
                "X-StreamKeep-Nonce": secrets.token_urlsafe(18),
            },
            data={"url": "https://example.com/a"},
        )
        self.assertEqual(stale["err"], "request_timestamp_expired")

    def test_every_mutating_endpoint_rejects_host_and_origin_substitution(self):
        cases = (
            ("/send_url", {"url": "https://example.com/a", "action": "queue"}),
            ("/api/queue", {"url": "https://example.com/a"}),
            ("/api/jobs/cancel", {"job_id": "job-a"}),
            ("/api/failures/retry", {"id": 1}),
            ("/api/failures/discard", {"id": 1}),
        )
        for path, body in cases:
            with self.subTest(path=path, boundary="host"):
                error = self._expect_error(
                    path,
                    403,
                    token=self.server.token,
                    method="POST",
                    headers={"Host": "attacker.example"},
                    data=body,
                )
                self.assertEqual(error["err"], "host_denied")
            with self.subTest(path=path, boundary="origin"):
                error = self._expect_error(
                    path,
                    403,
                    token=self.server.token,
                    method="POST",
                    headers={"Origin": "https://attacker.example"},
                    data=body,
                )
                self.assertEqual(error["err"], "origin_denied")

    def test_every_mutating_endpoint_rejects_nonce_replay(self):
        cases = (
            ("/send_url", {"url": "https://example.com/a", "action": "queue"}),
            ("/api/queue", {"url": "https://example.com/a"}),
            ("/api/jobs/cancel", {"job_id": "job-a"}),
            ("/api/failures/retry", {"id": 999999}),
            ("/api/failures/discard", {"id": 999999}),
        )
        for path, body in cases:
            headers = {
                "X-StreamKeep-Timestamp": str(int(time.time())),
                "X-StreamKeep-Nonce": secrets.token_urlsafe(18),
            }
            try:
                with self._open(
                    path,
                    token=self.server.token,
                    method="POST",
                    headers=headers,
                    data=body,
                ) as response:
                    response.read()
            except urllib.error.HTTPError:
                pass
            with self.subTest(path=path):
                error = self._expect_error(
                    path,
                    409,
                    token=self.server.token,
                    method="POST",
                    headers=headers,
                    data=body,
                )
                self.assertEqual(error["err"], "request_replayed")

    def test_durable_queue_ack_is_observable_and_cancellable(self):
        jobs = {}

        def submit(data):
            job = {
                "job_id": "job-123", "url": data["url"], "status": "queued",
            }
            jobs[job["job_id"]] = job
            return dict(job)

        def cancel(job_id):
            job = jobs.get(job_id)
            if job:
                job["status"] = "cancelled"
                return dict(job)
            return None

        durable_server = LocalCompanionServer()
        durable_server.queue_submitter = submit
        durable_server.job_canceller = cancel
        durable_server.state_provider = lambda: {"queue": list(jobs.values())}
        durable_server.start()
        try:
            queued, status = self._open_json(
                "/api/queue", server=durable_server,
                token=durable_server.token, method="POST",
                data={"url": "https://example.com/video"},
            )
            observed, _ = self._open_json(
                "/api/jobs/job-123", server=durable_server,
                token=durable_server.token,
            )
            cancelled, _ = self._open_json(
                "/api/jobs/cancel", server=durable_server,
                token=durable_server.token, method="POST",
                data={"job_id": "job-123"},
            )
        finally:
            durable_server.stop()

        self.assertEqual(status, 202)
        self.assertEqual(queued["job_id"], "job-123")
        self.assertEqual(observed["job"]["status"], "queued")
        self.assertEqual(cancelled["job"]["status"], "cancelled")

    def test_authenticated_ping_allows_localhost_origin(self):
        origin = f"http://localhost:{self.server.port}"
        with self._open(
            "/ping",
            token=self.server.token,
            headers={"Origin": origin},
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        self.assertEqual(payload, {"ok": True, "app": "StreamKeep"})
        self.assertEqual(resp.headers["Access-Control-Allow-Origin"], origin)

    def test_cors_rejects_untrusted_origin(self):
        err = self._expect_error(
            "/api/status",
            403,
            headers={"Origin": "https://attacker.example"},
            method="OPTIONS",
        )
        self.assertEqual(err["err"], "origin_denied")

    def test_lan_mode_echoes_configured_lan_origin(self):
        lan_server = LocalCompanionServer(
            bind_lan=True,
            external_origin="https://streamkeepbox.local",
        )
        lan_server.start()
        origin = "https://streamkeepbox.local"
        try:
            with self._open(
                "/api/status",
                server=lan_server,
                headers={
                    "Host": "streamkeepbox.local",
                    "Origin": origin,
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "streamkeepbox.local",
                },
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

    # ── Clip-range handoff ──────────────────────────────────────────

    def test_send_url_with_clip_range_accepted(self):
        payload, _ = self._open_json(
            "/send_url", token=self.server.token, method="POST",
            data={
                "url": "https://example.com/video",
                "action": "queue",
                "clip_start": "0:30",
                "clip_end": "5:00",
            },
        )
        self.assertTrue(payload["ok"])

    def test_send_url_rejects_invalid_clip_order(self):
        err = self._expect_error(
            "/send_url", 400, token=self.server.token, method="POST",
            data={
                "url": "https://example.com/video",
                "action": "queue",
                "clip_start": 300,
                "clip_end": 30,
            },
        )
        self.assertIn("clip_end", err.get("err", ""))

    def test_send_url_accepts_numeric_timestamps(self):
        payload, _ = self._open_json(
            "/send_url", token=self.server.token, method="POST",
            data={
                "url": "https://example.com/video",
                "action": "fetch",
                "clip_start": 10.5,
                "clip_end": 120.0,
            },
        )
        self.assertTrue(payload["ok"])

    def test_send_url_without_clip_still_works(self):
        payload, _ = self._open_json(
            "/send_url", token=self.server.token, method="POST",
            data={"url": "https://example.com/video", "action": "fetch"},
        )
        self.assertTrue(payload["ok"])

    def test_clip_range_emits_clip_received_signal(self):
        received = []
        # DirectConnection so the slot runs synchronously in the server thread;
        # this unittest has no running Qt event loop to drain a queued signal.
        self.server.clip_received.connect(
            lambda url, start, end: received.append((url, start, end)),
            Qt.ConnectionType.DirectConnection,
        )
        payload, _ = self._open_json(
            "/send_url", token=self.server.token, method="POST",
            data={
                "url": "https://example.com/video",
                "action": "fetch",
                "clip_start": "0:30",
                "clip_end": "5:00",
            },
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(received, [("https://example.com/video", 30.0, 300.0)])

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
