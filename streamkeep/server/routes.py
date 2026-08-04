"""Canonical Browser Companion REST route table."""

PRODUCT_REST_PATHS = frozenset({
    "POST /pair",
    "POST /api/validate",
    "POST /api/queue",
    "GET /gallery",
    "GET /feed/{id}.xml",
    "POST /api/shares/recording",
    "POST /api/shares/recording/revoke",
    "POST /api/shares/feed",
    "POST /api/shares/feed/revoke",
    "GET /api/uploads",
    "GET /api/uploads/profiles",
    "GET /api/health",
    "GET /api/intelligence",
    "GET /api/intelligence/profiles",
    "POST /api/uploads",
    "POST /api/uploads/profiles",
    "POST /api/uploads/retry",
    "POST /api/uploads/cancel",
    "POST /api/media-server/preview",
    "POST /api/media-server/export",
    "POST /api/intelligence/preview",
    "POST /api/intelligence/profiles",
    "POST /api/intelligence/summary",
    "POST /api/intelligence/thumbnail",
    "POST /api/intelligence/cancel",
    "POST /api/intelligence/summary/edit",
    "POST /api/intelligence/summary/rebuild",
    "POST /api/failures/retry",
    "POST /api/failures/cancel-retry",
    "GET /api/operations",
    "POST /api/operations/action",
    "POST /api/operations/export",
    "GET /api/tokens",
    "POST /api/tokens",
    "DELETE /api/tokens/{id}",
})
ROUTE_TABLE = tuple(sorted(PRODUCT_REST_PATHS))

__all__ = ["PRODUCT_REST_PATHS", "ROUTE_TABLE", "build_handler"]


def build_handler(*args, **kwargs):
    """Build the authenticated request handler used by the server thread."""
    from . import _legacy as _implementation
    return _implementation._build_handler(*args, **kwargs)
