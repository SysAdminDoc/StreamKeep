"""OpenAPI 3.1 specification for the StreamKeep local REST server.

The spec is generated in-process from a single source of truth so it can be
served at ``GET /api/spec`` and validated against the live route table by the
test-suite (``tests/test_openapi.py``). Keeping the description here — rather
than in a hand-maintained YAML file — guarantees the published contract never
drifts from ``local_server.py``.

Design notes:
  * Security scheme is HTTP bearer; the token is minted through the pairing
    flow and carries scopes (``status``/``queue``/``recovery``).
  * ``/`` (web UI), ``/ping``, ``/pair`` and ``/api/spec`` are intentionally
    documented but the last three are unauthenticated or self-authenticating.
  * Every path that the server dispatches must appear here and vice-versa; the
    consistency test asserts the two sets are identical.
"""

from __future__ import annotations

from . import VERSION

# Canonical ``METHOD /path`` table the server actually dispatches. The route
# table in ``local_server.do_GET``/``do_POST`` is asserted equal to this set by
# the test-suite so the spec cannot silently drift. ``/api/jobs/{id}`` is the
# templated form of the ``/api/jobs/`` prefix handler.
DOCUMENTED_OPERATIONS = frozenset({
    "GET /",
    "GET /ping",
    "GET /api/spec",
    "GET /api/status",
    "GET /api/library",
    "GET /api/monitor",
    "GET /api/jobs/{id}",
    "POST /pair",
    "POST /send_url",
    "POST /api/queue",
    "POST /api/jobs/cancel",
    "POST /api/failures/retry",
    "POST /api/failures/discard",
})


def _ok_error_schema():
    return {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean", "example": False},
            "err": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["ok"],
    }


def _job_schema():
    return {
        "type": "object",
        "description": "A durable queue job record.",
        "properties": {
            "job_id": {"type": "string"},
            "url": {"type": "string", "format": "uri"},
            "state": {"type": "string"},
            "source": {"type": "string"},
            "title": {"type": "string"},
        },
    }


def build_openapi_spec(version=VERSION, *, server_url="http://127.0.0.1:8787"):
    """Return the OpenAPI 3.1 document describing the REST server."""
    bearer = [{"bearerAuth": []}]
    unauthorized = {
        "description": "Missing or invalid bearer token.",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    }
    forbidden = {
        "description": "Token lacks the required scope, or origin/Host rejected.",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    }
    error_content = {
        "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
    }

    def json_ok(desc, schema):
        return {"description": desc, "content": {"application/json": {"schema": schema}}}

    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "StreamKeep Local REST API",
            "version": str(version),
            "description": (
                "Loopback-only control API for StreamKeep. Clients exchange a "
                "short-lived pairing code for an origin-bound bearer token via "
                "`POST /pair`, then call the scoped endpoints. The server binds "
                "to 127.0.0.1; LAN access must be terminated by an explicitly "
                "configured local HTTPS reverse proxy."
            ),
            "license": {"name": "MIT"},
        },
        "servers": [{"url": server_url, "description": "Local loopback listener"}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": (
                        "Bearer token minted by the pairing flow. Scopes: "
                        "status (read state), queue (submit/cancel), recovery "
                        "(retry/discard failures)."
                    ),
                }
            },
            "schemas": {
                "Error": _ok_error_schema(),
                "Job": _job_schema(),
                "QueueRequest": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "format": "uri"},
                        "quality": {"type": "string"},
                        "action": {"type": "string", "enum": ["fetch", "queue"]},
                    },
                    "required": ["url"],
                },
                "PairRequest": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "One-use pairing code."},
                        "scopes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["code"],
                },
            },
        },
        "paths": {
            "/": {
                "get": {
                    "summary": "Serve the single-page web remote UI.",
                    "tags": ["ui"],
                    "responses": {"200": {"description": "HTML web remote UI."}},
                }
            },
            "/ping": {
                "get": {
                    "summary": "Liveness probe (requires any valid token).",
                    "tags": ["status"],
                    "security": bearer,
                    "responses": {
                        "200": json_ok("Server is alive.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "app": {"type": "string", "example": "StreamKeep"},
                            },
                        }),
                        "401": unauthorized,
                    },
                }
            },
            "/api/spec": {
                "get": {
                    "summary": "This OpenAPI 3.1 specification (unauthenticated).",
                    "tags": ["meta"],
                    "responses": {
                        "200": json_ok("OpenAPI document.", {"type": "object"}),
                    },
                }
            },
            "/api/status": {
                "get": {
                    "summary": "Active downloads, queue, failures, and live channels.",
                    "tags": ["status"],
                    "security": bearer,
                    "responses": {
                        "200": json_ok("Runtime state snapshot.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "downloads": {"type": "array", "items": {"type": "object"}},
                                "queue": {"type": "array", "items": {"$ref": "#/components/schemas/Job"}},
                                "failures": {"type": "array", "items": {"type": "object"}},
                                "live_channels": {"type": "array", "items": {"type": "object"}},
                                "active_workers": {"type": "array", "items": {"type": "object"}},
                                "resumable": {"type": "array", "items": {"type": "object"}},
                            },
                        }),
                        "401": unauthorized,
                        "403": forbidden,
                    },
                }
            },
            "/api/library": {
                "get": {
                    "summary": "Recorded VOD/library history.",
                    "tags": ["status"],
                    "security": bearer,
                    "responses": {
                        "200": json_ok("Library history.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "history": {"type": "array", "items": {"type": "object"}},
                            },
                        }),
                        "401": unauthorized,
                        "403": forbidden,
                    },
                }
            },
            "/api/monitor": {
                "get": {
                    "summary": "Channel monitor statuses.",
                    "tags": ["status"],
                    "security": bearer,
                    "responses": {
                        "200": json_ok("Monitor channel list.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "channels": {"type": "array", "items": {"type": "object"}},
                            },
                        }),
                        "401": unauthorized,
                        "403": forbidden,
                    },
                }
            },
            "/api/jobs/{id}": {
                "get": {
                    "summary": "Inspect one durable queue job.",
                    "tags": ["status"],
                    "security": bearer,
                    "parameters": [{
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }],
                    "responses": {
                        "200": json_ok("Job record.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "job_id": {"type": "string"},
                                "job": {"$ref": "#/components/schemas/Job"},
                            },
                        }),
                        "400": {"description": "Invalid job id.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {"description": "Job not found.", "content": error_content},
                    },
                }
            },
            "/pair": {
                "post": {
                    "summary": "Exchange a one-use pairing code for a scoped bearer token.",
                    "tags": ["auth"],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/PairRequest"}}},
                    },
                    "responses": {
                        "201": json_ok("Token issued.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "token": {"type": "string"},
                                "scopes": {"type": "array", "items": {"type": "string"}},
                                "origin": {"type": "string"},
                                "expires_at": {"type": "integer"},
                            },
                        }),
                        "400": {"description": "Missing freshness headers or scope.", "content": error_content},
                        "401": {"description": "Pairing code invalid/expired/used.", "content": error_content},
                        "403": {"description": "Origin or cross-site rejected.", "content": error_content},
                        "415": {"description": "Content-Type must be application/json.", "content": error_content},
                    },
                }
            },
            "/send_url": {
                "post": {
                    "summary": "Hand a URL to StreamKeep (fetch or queue).",
                    "tags": ["queue"],
                    "security": bearer,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/QueueRequest"}}},
                    },
                    "responses": {
                        "200": json_ok("URL accepted for fetch.", {"type": "object"}),
                        "202": json_ok("Job queued.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "job_id": {"type": "string"},
                                "job": {"$ref": "#/components/schemas/Job"},
                            },
                        }),
                        "400": {"description": "Invalid URL or clip range.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                        "500": {"description": "Queue submission failed.", "content": error_content},
                    },
                }
            },
            "/api/queue": {
                "post": {
                    "summary": "Add a URL to the download queue.",
                    "tags": ["queue"],
                    "security": bearer,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/QueueRequest"}}},
                    },
                    "responses": {
                        "200": json_ok("URL accepted (no durable submitter).", {"type": "object"}),
                        "202": json_ok("Job queued.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "job_id": {"type": "string"},
                                "job": {"$ref": "#/components/schemas/Job"},
                            },
                        }),
                        "400": {"description": "Invalid URL.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                        "500": {"description": "Queue submission failed.", "content": error_content},
                    },
                }
            },
            "/api/jobs/cancel": {
                "post": {
                    "summary": "Durably cancel a queue job.",
                    "tags": ["queue"],
                    "security": bearer,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"job_id": {"type": "string"}},
                            "required": ["job_id"],
                        }}},
                    },
                    "responses": {
                        "200": json_ok("Job cancelled.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "job_id": {"type": "string"},
                                "job": {"$ref": "#/components/schemas/Job"},
                            },
                        }),
                        "400": {"description": "Invalid job id.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {"description": "Job not found.", "content": error_content},
                        "503": {"description": "Cancellation unavailable.", "content": error_content},
                    },
                }
            },
            "/api/failures/retry": {
                "post": {
                    "summary": "Retry a persisted failed job.",
                    "tags": ["recovery"],
                    "security": bearer,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"id": {"type": "integer"}},
                            "required": ["id"],
                        }}},
                    },
                    "responses": {
                        "200": json_ok("Failure marked for retry.", {"type": "object"}),
                        "400": {"description": "Invalid failure id.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                        "500": {"description": "Retry failed.", "content": error_content},
                    },
                }
            },
            "/api/failures/discard": {
                "post": {
                    "summary": "Discard a persisted failed job.",
                    "tags": ["recovery"],
                    "security": bearer,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"id": {"type": "integer"}},
                            "required": ["id"],
                        }}},
                    },
                    "responses": {
                        "200": json_ok("Failure discarded.", {"type": "object"}),
                        "400": {"description": "Invalid failure id.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                        "500": {"description": "Discard failed.", "content": error_content},
                    },
                }
            },
        },
    }
    return spec


def spec_operations(spec=None):
    """Return the ``METHOD /path`` set declared by ``spec`` (or a fresh one)."""
    spec = spec or build_openapi_spec()
    ops = set()
    for path, item in spec.get("paths", {}).items():
        for method in item:
            if method.lower() in ("get", "post", "put", "patch", "delete"):
                ops.add(f"{method.upper()} {path}")
    return frozenset(ops)
