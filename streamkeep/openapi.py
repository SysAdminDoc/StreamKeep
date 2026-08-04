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
    "GET /api/operations",
    "POST /api/operations/action",
    "POST /api/operations/export",
    "GET /api/library",
    "GET /api/monitor",
    "GET /gallery",
    "GET /share/{id}",
    "GET /media/{id}",
    "GET /feed/{id}.xml",
    "GET /api/shares",
    "GET /api/uploads",
    "GET /api/uploads/profiles",
    "GET /api/intelligence",
    "GET /api/intelligence/profiles",
    "GET /api/jobs/{id}",
    "POST /pair",
    "POST /send_url",
    "POST /api/validate",
    "POST /api/queue",
    "POST /api/shares/recording",
    "POST /api/shares/recording/revoke",
    "POST /api/shares/feed",
    "POST /api/shares/feed/revoke",
    "POST /api/uploads",
    "POST /api/uploads/profiles",
    "POST /api/uploads/retry",
    "POST /api/uploads/cancel",
    "POST /api/media-server/preview",
    "POST /api/media-server/export",
    "POST /api/intelligence/profiles",
    "POST /api/intelligence/preview",
    "POST /api/intelligence/summary",
    "POST /api/intelligence/thumbnail",
    "POST /api/intelligence/cancel",
    "POST /api/intelligence/summary/edit",
    "POST /api/intelligence/summary/rebuild",
    "POST /api/jobs/cancel",
    "POST /api/failures/retry",
    "POST /api/failures/cancel-retry",
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
        "description": (
            "Bearer token is missing, expired, revoked, or not presented from "
            "its paired browser origin."
        ),
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
                        "(retry/discard failures). Paired browser tokens are "
                        "bound to their exact Origin; safe same-origin requests "
                        "that omit Origin require Sec-Fetch-Site: same-origin "
                        "and a matching request authority."
                    ),
                }
            },
            "parameters": {
                "MutationTimestamp": {
                    "name": "X-StreamKeep-Timestamp",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "integer", "format": "int64"},
                    "description": "Current Unix timestamp in seconds.",
                },
                "MutationNonce": {
                    "name": "X-StreamKeep-Nonce",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 22,
                        "maxLength": 128,
                    },
                    "description": "One-use replay nonce for this request.",
                },
            },
            "schemas": {
                "Error": _ok_error_schema(),
                "Job": _job_schema(),
                "QueueRequest": {
                    "type": "object",
                    "description": (
                        "Only the listed URL, picker, quality, clip, and "
                        "same-root output fields are accepted from a queue "
                        "client. Unknown executor fields are ignored."
                    ),
                    "additionalProperties": False,
                    "properties": {
                        "url": {"type": "string", "format": "uri"},
                        "quality": {"type": "string"},
                        "action": {"type": "string", "enum": ["fetch", "queue"]},
                        "validation_id": {
                            "type": "string",
                            "description": "One-use id returned by POST /api/validate.",
                        },
                        "media_item_id": {
                            "type": "string",
                            "description": "Picker item id selected for this queue job.",
                        },
                        "media_item_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 100,
                            "description": "Exactly one id may be queued per request.",
                        },
                        "background_audio_id": {
                            "type": "string",
                            "description": "Optional background audio picker id.",
                        },
                        "request_headers": {
                            "type": "object",
                            "description": (
                                "Optional browser replay headers. The server "
                                "keeps only Referer, Origin, User-Agent, Cookie, "
                                "and Authorization for the active job."
                            ),
                            "additionalProperties": {"type": "string"},
                        },
                        "source_context": {
                            "type": "object",
                            "description": "Non-secret active-tab context for a browser handoff.",
                            "additionalProperties": {"type": "string"},
                        },
                        "media_item_type": {
                            "type": "string",
                            "enum": ["video", "audio", "photo", "gif"],
                        },
                        "vod_source": {"type": "string"},
                        "vod_platform": {"type": "string"},
                        "title": {"type": "string"},
                        "platform": {"type": "string"},
                        "source_id": {"type": "string"},
                        "webpage_url": {"type": "string", "format": "uri"},
                        "vod_title": {"type": "string"},
                        "vod_channel": {"type": "string"},
                        "feed_url": {"type": "string", "format": "uri"},
                        "clip_start": {"type": "string"},
                        "clip_end": {"type": "string"},
                        "output_dir": {
                            "type": "string",
                            "description": (
                                "Optional subdirectory below StreamKeep's "
                                "configured output root."
                            ),
                        },
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
                "ShareRecordingRequest": {
                    "type": "object",
                    "properties": {
                        "history_id": {"type": "integer", "minimum": 1},
                    },
                    "required": ["history_id"],
                },
                "ShareFeedRequest": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "string",
                            "description": "Empty publishes all shared recordings.",
                        },
                        "title": {"type": "string"},
                    },
                    "required": ["channel"],
                },
                "UploadProfileRequest": {
                    "type": "object",
                    "properties": {
                        "profile_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
                        "label": {"type": "string"},
                        "adapter": {"type": "string"},
                        "config": {
                            "type": "object",
                            "description": "Destination settings; secret fields are stored outside SQLite.",
                        },
                    },
                    "required": ["profile_id", "adapter", "config"],
                },
                "UploadRequest": {
                    "type": "object",
                    "properties": {
                        "profile_id": {"type": "string"},
                        "source_path": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                    "required": ["profile_id", "source_path"],
                },
                "MediaServerExportRequest": {
                    "type": "object",
                    "properties": {
                        "config": {"type": "object"},
                        "out_dir": {"type": "string"},
                        "info": {"type": "object"},
                        "upload_profile_id": {"type": "string"},
                    },
                    "required": ["config", "out_dir"],
                },
                "IntelligenceProfileRequest": {
                    "type": "object",
                    "properties": {
                        "profile_id": {"type": "string"},
                        "label": {"type": "string"},
                        "provider": {"type": "string", "enum": ["ollama", "openai", "anthropic"]},
                        "config": {
                            "type": "object",
                            "description": "Model and endpoint settings; API keys are stored in the secure store.",
                        },
                    },
                    "required": ["profile_id", "provider", "config"],
                },
                "IntelligenceRequest": {
                    "type": "object",
                    "properties": {
                        "recording_dir": {"type": "string"},
                        "profile_id": {"type": "string"},
                        "provider": {"type": "string"},
                        "model": {"type": "string"},
                        "api_url": {"type": "string"},
                        "redact": {"type": "boolean"},
                        "consent_token": {"type": "string"},
                        "history_id": {"type": "integer"},
                    },
                    "required": ["recording_dir"],
                },
                "IntelligenceJobRequest": {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
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
                                "backup": {
                                    "type": "object",
                                    "description": (
                                        "Automatic profile backup schedule: last "
                                        "success, size, next run, and any failure "
                                        "reason. Host paths are never included."
                                    ),
                                },
                            },
                        }),
                        "401": unauthorized,
                        "403": forbidden,
                    },
                }
            },
            "/api/operations": {
                "get": {
                    "summary": "Read a paged, filterable operations view.",
                    "tags": ["operations"],
                    "security": bearer,
                    "parameters": [
                        {"name": "state", "in": "query", "schema": {"type": "string"}},
                        {"name": "source", "in": "query", "schema": {"type": "string"}},
                        {"name": "stage", "in": "query", "schema": {"type": "string"}},
                        {"name": "kind", "in": "query", "schema": {"type": "string"}},
                        {"name": "search", "in": "query", "schema": {"type": "string"}},
                        {"name": "page", "in": "query", "schema": {"type": "integer", "minimum": 0}},
                        {"name": "page_size", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 200}},
                    ],
                    "responses": {
                        "200": json_ok("Paged operations and aggregate state.", {"type": "object"}),
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
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
            "/gallery": {
                "get": {
                    "summary": "Browse currently published recordings.",
                    "tags": ["publishing"],
                    "security": bearer,
                    "responses": {
                        "200": {"description": "Authenticated gallery HTML."},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                }
            },
            "/share/{id}": {
                "get": {
                    "summary": "Open one published recording player page.",
                    "tags": ["publishing"],
                    "security": bearer,
                    "parameters": [{
                        "name": "id", "in": "path", "required": True,
                        "schema": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                    }],
                    "responses": {
                        "200": {"description": "Authenticated player HTML."},
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {"description": "Share revoked or media missing.", "content": error_content},
                    },
                }
            },
            "/media/{id}": {
                "get": {
                    "summary": "Stream one published media file with Range support.",
                    "tags": ["publishing"],
                    "security": bearer,
                    "parameters": [{
                        "name": "id", "in": "path", "required": True,
                        "schema": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                    }],
                    "responses": {
                        "200": {"description": "Media bytes."},
                        "206": {"description": "Partial media bytes."},
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {"description": "Share revoked or media missing."},
                        "416": {"description": "Invalid byte range."},
                    },
                }
            },
            "/feed/{id}.xml": {
                "get": {
                    "summary": "Read one published, authenticated RSS feed.",
                    "tags": ["publishing"],
                    "security": bearer,
                    "parameters": [{
                        "name": "id", "in": "path", "required": True,
                        "schema": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                    }],
                    "responses": {
                        "200": {"description": "RSS 2.0 feed XML."},
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {"description": "Feed revoked or unknown."},
                    },
                }
            },
            "/api/shares": {
                "get": {
                    "summary": "List published recordings and feed definitions.",
                    "tags": ["publishing"],
                    "security": bearer,
                    "responses": {
                        "200": json_ok("Publication state.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "recordings": {"type": "array", "items": {"type": "object"}},
                                "feeds": {"type": "array", "items": {"type": "object"}},
                            },
                        }),
                        "401": unauthorized,
                        "403": forbidden,
                    },
                }
            },
            "/api/uploads": {
                "get": {
                    "summary": "List persisted upload progress and retry state.",
                    "tags": ["uploads"],
                    "security": bearer,
                    "responses": {
                        "200": json_ok("Upload jobs.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "uploads": {"type": "array", "items": {"type": "object"}},
                            },
                        }),
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
                "post": {
                    "summary": "Queue one completed file for upload delivery.",
                    "tags": ["uploads"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/UploadRequest"}}
                    }},
                    "responses": {
                        "202": json_ok("Upload queued.", {"type": "object"}),
                        "400": {"description": "Invalid upload job.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/uploads/profiles": {
                "get": {
                    "summary": "List redacted upload destination profiles.",
                    "tags": ["uploads"],
                    "security": bearer,
                    "responses": {
                        "200": json_ok("Upload profiles.", {"type": "object"}),
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
                "post": {
                    "summary": "Save a secure upload destination profile.",
                    "tags": ["uploads"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/UploadProfileRequest"}}
                    }},
                    "responses": {
                        "201": json_ok("Profile saved.", {"type": "object"}),
                        "400": {"description": "Invalid profile.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/uploads/retry": {
                "post": {
                    "summary": "Retry a persisted upload job.",
                    "tags": ["uploads"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"type": "object", "required": ["upload_id"]}}
                    }},
                    "responses": {
                        "202": json_ok("Upload retry queued.", {"type": "object"}),
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {"description": "Upload is not retryable.", "content": error_content},
                    },
                },
            },
            "/api/uploads/cancel": {
                "post": {
                    "summary": "Cancel a queued or active upload job.",
                    "tags": ["uploads"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"type": "object", "required": ["upload_id"]}}
                    }},
                    "responses": {
                        "200": json_ok("Upload cancelled.", {"type": "object"}),
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {"description": "Upload is not cancellable.", "content": error_content},
                    },
                },
            },
            "/api/intelligence": {
                "get": {
                    "summary": "List persisted summary and smart-thumbnail jobs.",
                    "tags": ["intelligence"],
                    "security": bearer,
                    "responses": {
                        "200": json_ok("Intelligence jobs.", {"type": "object"}),
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/intelligence/profiles": {
                "get": {
                    "summary": "List redacted local/cloud intelligence profiles.",
                    "tags": ["intelligence"],
                    "security": bearer,
                    "responses": {
                        "200": json_ok("Intelligence profiles.", {"type": "object"}),
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
                "post": {
                    "summary": "Save an intelligence profile in the secure store.",
                    "tags": ["intelligence"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/IntelligenceProfileRequest"}}
                    }},
                    "responses": {
                        "201": json_ok("Profile saved.", {"type": "object"}),
                        "400": {"description": "Invalid profile.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/intelligence/preview": {
                "post": {
                    "summary": "Show the exact transcript payload and cloud consent boundary.",
                    "tags": ["intelligence"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/IntelligenceRequest"}}
                    }},
                    "responses": {
                        "200": json_ok("Transcript preview.", {"type": "object"}),
                        "400": {"description": "Preview failed.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/intelligence/summary": {
                "post": {
                    "summary": "Queue a local summary or a consent-bound cloud summary.",
                    "tags": ["intelligence"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/IntelligenceRequest"}}
                    }},
                    "responses": {
                        "202": json_ok("Summary queued.", {"type": "object"}),
                        "400": {"description": "Consent/provider/job validation failed.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/intelligence/thumbnail": {
                "post": {
                    "summary": "Queue a local resource-bounded smart thumbnail.",
                    "tags": ["intelligence"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/IntelligenceRequest"}}
                    }},
                    "responses": {
                        "202": json_ok("Thumbnail queued.", {"type": "object"}),
                        "400": {"description": "Thumbnail job validation failed.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/intelligence/cancel": {
                "post": {
                    "summary": "Cancel a queued or active intelligence job.",
                    "tags": ["intelligence"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/IntelligenceJobRequest"}}
                    }},
                    "responses": {
                        "200": json_ok("Cancellation requested.", {"type": "object"}),
                        "404": {"description": "Job not cancellable.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/intelligence/summary/edit": {
                "post": {
                    "summary": "Edit a persisted summary without rebuilding it.",
                    "tags": ["intelligence"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"type": "object", "required": ["job_id", "text"]}}
                    }},
                    "responses": {
                        "200": json_ok("Summary updated.", {"type": "object"}),
                        "400": {"description": "Summary edit failed.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/intelligence/summary/rebuild": {
                "post": {
                    "summary": "Rebuild a saved summary; cloud rebuilds require fresh consent.",
                    "tags": ["intelligence"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/IntelligenceJobRequest"}}
                    }},
                    "responses": {
                        "202": json_ok("Summary rebuild queued.", {"type": "object"}),
                        "400": {"description": "Summary rebuild failed.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/media-server/preview": {
                "post": {
                    "summary": "Preview a deterministic media-server layout.",
                    "tags": ["media-server"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/MediaServerExportRequest"}}
                    }},
                    "responses": {
                        "200": json_ok("Layout preview.", {"type": "object"}),
                        "400": {"description": "Preview failed.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/media-server/export": {
                "post": {
                    "summary": "Materialize a layout and optionally queue its files for upload.",
                    "tags": ["media-server"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/MediaServerExportRequest"}}
                    }},
                    "responses": {
                        "201": json_ok("Layout materialized.", {"type": "object"}),
                        "202": json_ok("Layout materialized and upload queued.", {"type": "object"}),
                        "400": {"description": "Export failed.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
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
            "/api/validate": {
                "post": {
                    "summary": "Resolve a URL into safe media picker metadata.",
                    "description": (
                        "The response contains bounded media metadata and an "
                        "expiring validation id. Delivery URLs and credentials "
                        "remain server-side; submit the selected ids to "
                        "POST /api/queue."
                    ),
                    "tags": ["queue"],
                    "security": bearer,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/QueueRequest"}}},
                    },
                    "responses": {
                        "200": json_ok("Picker metadata returned.", {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "validated": {"type": "boolean"},
                                "validation_id": {"type": "string"},
                                "expires_at": {"type": "integer"},
                                "media_items": {
                                    "type": "array",
                                    "items": {"type": "object"},
                                },
                                "picker": {
                                    "type": "array",
                                    "items": {"type": "object"},
                                },
                                "background_audio": {
                                    "type": "array",
                                    "items": {"type": "object"},
                                },
                                "selection": {"type": "object"},
                            },
                        }),
                        "400": {"description": "Invalid URL, handoff, or probe.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                        "500": {"description": "Probe failed.", "content": error_content},
                        "503": {"description": "Probe service unavailable.", "content": error_content},
                    },
                }
            },
            "/api/shares/recording": {
                "post": {
                    "summary": "Publish one existing history recording.",
                    "tags": ["publishing"],
                    "security": bearer,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/ShareRecordingRequest"}}},
                    },
                    "responses": {
                        "201": json_ok("Recording published.", {"type": "object"}),
                        "400": {"description": "Invalid history id or request.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {"description": "Recording folder/media is missing.", "content": error_content},
                    },
                }
            },
            "/api/shares/recording/revoke": {
                "post": {
                    "summary": "Revoke one recording share immediately.",
                    "tags": ["publishing"],
                    "security": bearer,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "share_id": {"type": "string"},
                                "history_id": {"type": "integer"},
                            },
                        }}},
                    },
                    "responses": {
                        "200": json_ok("Recording share revoked.", {"type": "object"}),
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {"description": "Share not found.", "content": error_content},
                    },
                }
            },
            "/api/shares/feed": {
                "post": {
                    "summary": "Publish an authenticated RSS feed.",
                    "tags": ["publishing"],
                    "security": bearer,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/ShareFeedRequest"}}},
                    },
                    "responses": {
                        "201": json_ok("Feed published.", {"type": "object"}),
                        "400": {"description": "Invalid channel or title.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                }
            },
            "/api/shares/feed/revoke": {
                "post": {
                    "summary": "Revoke an RSS feed immediately.",
                    "tags": ["publishing"],
                    "security": bearer,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"feed_id": {"type": "string"}},
                            "required": ["feed_id"],
                        }}},
                    },
                    "responses": {
                        "200": json_ok("Feed revoked.", {"type": "object"}),
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {"description": "Feed not found.", "content": error_content},
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
            "/api/operations/action": {
                "post": {
                    "summary": "Retry or discard up to 100 selected failures.",
                    "tags": ["operations"],
                    "security": bearer,
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["retry", "discard"]},
                            "failure_ids": {"type": "array", "items": {"type": "integer"}},
                        },
                        "required": ["action", "failure_ids"],
                    }}}},
                    "responses": {
                        "200": json_ok("Selected failure actions.", {"type": "object"}),
                        "400": {"description": "Invalid action.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
            },
            "/api/operations/export": {
                "post": {
                    "summary": "Return a redacted, URL/path-free operations report.",
                    "tags": ["operations"],
                    "security": bearer,
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {
                        "200": json_ok("Redacted operations report.", {"type": "object"}),
                        "400": {"description": "Invalid report request.", "content": error_content},
                        "401": unauthorized,
                        "403": forbidden,
                    },
                },
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
            "/api/failures/cancel-retry": {
                "post": {
                    "summary": (
                        "Cancel automatic retry while retaining the failure "
                        "for manual intervention."
                    ),
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
                        "200": json_ok(
                            "Automatic retry cancelled.",
                            {"type": "object"},
                        ),
                        "400": {
                            "description": "Invalid failure id.",
                            "content": error_content,
                        },
                        "401": unauthorized,
                        "403": forbidden,
                        "404": {
                            "description": "Failure not found.",
                            "content": error_content,
                        },
                        "500": {
                            "description": "Cancellation failed.",
                            "content": error_content,
                        },
                    },
                }
            },
        },
    }
    mutation_parameters = [
        {"$ref": "#/components/parameters/MutationTimestamp"},
        {"$ref": "#/components/parameters/MutationNonce"},
    ]
    for path_item in spec["paths"].values():
        operation = path_item.get("post")
        if operation is not None:
            operation["parameters"] = list(mutation_parameters)
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
