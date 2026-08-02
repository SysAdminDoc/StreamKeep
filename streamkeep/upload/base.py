"""Upload destination base class + adapter registry (F68).

Each adapter subclasses ``UploadDestination`` and implements ``upload()``
and ``test_connection()``.  Subclasses auto-register via ``__init_subclass__``.
"""

from __future__ import annotations

from typing import Any


def sanitize_upload_message(message: object, config: dict[str, Any] | None = None) -> str:
    """Redact credentials and credential-bearing URLs from adapter output."""
    from ..diagnostics import redact_text

    text = redact_text(str(message or ""))
    for key, value in dict(config or {}).items():
        if not isinstance(value, str) or len(value) < 3:
            continue
        if any(token in str(key).lower() for token in (
            "password", "secret", "token", "access_key", "api_key",
            "private_key", "passphrase",
        )):
            text = text.replace(value, "<redacted>")
    return text

class UploadDestination:
    """Abstract base for upload adapters."""

    NAME = ""
    _registry = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.NAME:
            UploadDestination._registry.append(cls)

    def __init__(self, config=None):
        self.config = config or {}

    def upload(self, file_path, metadata=None, progress_cb=None):
        """Upload *file_path* to the destination.

        *metadata* is an optional dict with title, channel, date, etc.
        *progress_cb* is called with ``(bytes_sent, total_bytes)`` or None.

        Returns ``(ok, message)``.
        """
        raise NotImplementedError

    def test_connection(self):
        """Test connectivity to the destination.

        Returns ``(ok, message)``.
        """
        raise NotImplementedError

    def safe_message(self, message):
        """Return an adapter message safe for logs and API responses."""
        return sanitize_upload_message(message, self.config)

    @classmethod
    def all_adapters(cls):
        return {c.NAME: c for c in cls._registry if c.NAME}
