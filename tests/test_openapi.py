import json
import unittest

from PyQt6.QtCore import QCoreApplication

from streamkeep import openapi
from streamkeep.local_server import LocalCompanionServer


class OpenApiSpecTests(unittest.TestCase):
    def test_spec_is_valid_openapi_31_shape(self):
        spec = openapi.build_openapi_spec()
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertIn("info", spec)
        self.assertIn("paths", spec)
        self.assertIn("bearerAuth", spec["components"]["securitySchemes"])
        # Every scoped operation references the bearer scheme; unauth ones don't.
        self.assertNotIn("security", spec["paths"]["/pair"]["post"])
        self.assertNotIn("security", spec["paths"]["/api/spec"]["get"])
        self.assertEqual(
            spec["paths"]["/api/status"]["get"]["security"],
            [{"bearerAuth": []}],
        )

    def test_spec_version_tracks_package_version(self):
        from streamkeep import VERSION
        self.assertEqual(openapi.build_openapi_spec()["info"]["version"], VERSION)

    def test_spec_is_json_serializable(self):
        # Must round-trip cleanly since it is served as JSON.
        text = json.dumps(openapi.build_openapi_spec())
        self.assertIn("StreamKeep", text)

    def test_documented_operations_match_declared_paths(self):
        # The hand-maintained operation table and the generated spec agree.
        self.assertEqual(
            openapi.spec_operations(),
            openapi.DOCUMENTED_OPERATIONS,
        )


class OpenApiRouteConsistencyTests(unittest.TestCase):
    """The spec must describe exactly the routes the server dispatches."""

    def _server_operations(self):
        import inspect
        # Parse the GET/POST dispatch tables straight from the module source.
        from streamkeep import local_server
        src = inspect.getsource(local_server)
        get_block = src.split("def do_GET", 1)[1].split("def do_POST", 1)[0]
        post_block = src.split("def do_POST", 1)[1].split("def _handle_pair", 1)[0]
        ops = set()
        for method, block in (("GET", get_block), ("POST", post_block)):
            for line in block.splitlines():
                line = line.strip()
                if 'path == "/' in line:
                    route = line.split('path == "', 1)[1].split('"', 1)[0]
                    ops.add(f"{method} {route}")
                elif 'path.startswith("/api/jobs/")' in line:
                    ops.add(f"{method} /api/jobs/{{id}}")
        # "/" is served in do_GET via _serve_web_ui without an explicit ``==``.
        ops.add("GET /")
        return ops

    def test_every_dispatched_route_is_documented(self):
        server_ops = self._server_operations()
        for op in server_ops:
            self.assertIn(op, openapi.DOCUMENTED_OPERATIONS, f"undocumented route: {op}")

    def test_every_documented_route_is_dispatched(self):
        server_ops = self._server_operations()
        for op in openapi.DOCUMENTED_OPERATIONS:
            self.assertIn(op, server_ops, f"documented-but-missing route: {op}")


class OpenApiServedEndpointTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.server = LocalCompanionServer()
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.app.processEvents()

    def test_spec_endpoint_serves_without_auth(self):
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.server.port}/api/spec", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(body["openapi"], "3.1.0")
        self.assertIn("/api/status", body["paths"])


if __name__ == "__main__":
    unittest.main()
