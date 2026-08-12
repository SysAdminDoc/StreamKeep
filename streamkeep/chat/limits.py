"""Shared bounds for untrusted chat transport input."""

IRC_BUFFER_LIMIT = 64 * 1024
KICK_PAYLOAD_LIMIT = 256 * 1024


class ChatPayloadTooLarge(RuntimeError):
    """Raised when a chat endpoint exceeds a bounded transport payload."""
