import json
import unittest
import urllib.error
import urllib.request

from PyQt6.QtCore import QCoreApplication

from streamkeep.local_server import LocalCompanionServer


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.server = LocalCompanionServer()
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.app.processEvents()

    def _open(self, path, *, token=None, headers=None, method="GET", server=None):
        server = server or self.server
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.port}{path}",
            headers=request_headers,
            method=method,
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


if __name__ == "__main__":
    unittest.main()
