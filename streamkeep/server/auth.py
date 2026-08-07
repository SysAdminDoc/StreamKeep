"""Bearer-token authentication and replay policy for the companion server.

Owns the scope vocabulary, token minting and validation, the pairing-code
exchange, and the nonce replay ledger. Split out of ``_legacy`` (V163) so the
security boundary is a module that can be read end to end rather than 220 lines
buried in a 2,700-line request handler.

Nothing here imports a server sibling, so it can be imported from anywhere in
the package without a cycle. ``streamkeep.local_server`` stays the public facade
and propagates a patch of any of these names to every module holding it -- see
``_holders`` there -- which is what keeps the existing module-level test patches
reaching the code that actually runs.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass, replace


SCOPE_STATUS = "status"
SCOPE_QUEUE = "queue"
SCOPE_RECOVERY = "recovery"
ALL_SCOPES = frozenset({SCOPE_STATUS, SCOPE_QUEUE, SCOPE_RECOVERY})
PAIRED_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
TOKEN_LABEL_MAX_LENGTH = 128
TOKEN_TTL_MAX_SECONDS = PAIRED_TOKEN_TTL_SECONDS


def generate_bearer_token():
    """Generate a 256-bit URL-safe bearer token."""
    return secrets.token_urlsafe(32)

def valid_bearer_token(token):
    token = str(token or "")
    # Legacy 128-bit hex tokens remain valid for one-way migration.
    return bool(_TOKEN_RE.fullmatch(token))

@dataclass(frozen=True)
class TokenGrant:
    scopes: frozenset
    origin: str = ""
    expires_at: float = 0.0
    token_id: str = ""
    label: str = ""
    created_at: float = 0.0
    last_used: float = 0.0
    is_master: bool = False

class TokenStore:
    """Thread-safe bearer-token registry with redacted metadata views."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens = {}  # token_str -> TokenGrant

    def add(
        self, token, scopes, *, origin="", expires_at=0.0, label="",
        token_id="", created_at=None, is_master=False,
    ):
        if not valid_bearer_token(token):
            raise ValueError("Bearer tokens must contain at least 128 bits.")
        now = time.time()
        created = float(now if created_at is None else created_at or now)
        normalized_label = str(label or "").strip()[:TOKEN_LABEL_MAX_LENGTH]
        with self._lock:
            candidate_id = str(token_id or "").strip()
            existing_ids = {
                grant.token_id for grant in self._tokens.values() if grant.token_id
            }
            while not candidate_id or candidate_id in existing_ids:
                candidate_id = secrets.token_urlsafe(12)
            self._tokens[token] = TokenGrant(
                frozenset(scopes), str(origin or ""), float(expires_at or 0.0),
                candidate_id, normalized_label, created, 0.0, bool(is_master),
            )
            return candidate_id

    def remove(self, token):
        with self._lock:
            self._tokens.pop(token, None)

    def revoke_all(self):
        with self._lock:
            self._tokens.clear()

    def check(self, token):
        candidate = str(token or "")
        if not candidate:
            return None
        with self._lock:
            rows = tuple(self._tokens.items())
        matched = None
        # Do not expose token-prefix timing through the loopback/LAN boundary.
        for stored, grant in rows:
            if secrets.compare_digest(stored, candidate):
                matched = grant
        if matched and matched.expires_at and matched.expires_at <= time.time():
            self.remove(candidate)
            return None
        if matched:
            with self._lock:
                current = self._tokens.get(candidate)
                if current is not None:
                    matched = replace(current, last_used=time.time())
                    self._tokens[candidate] = matched
        return matched

    @staticmethod
    def _metadata(grant):
        return {
            "id": grant.token_id,
            "label": grant.label,
            "scopes": sorted(grant.scopes),
            "origin": grant.origin,
            "created_at": int(grant.created_at) if grant.created_at else 0,
            "last_used": int(grant.last_used) if grant.last_used else None,
            "expires_at": int(grant.expires_at) if grant.expires_at else None,
        }

    def metadata_for_token(self, token):
        """Return metadata for an internal token lookup, never the secret."""
        with self._lock:
            grant = self._tokens.get(str(token or ""))
        return self._metadata(grant) if grant else None

    def list_metadata(self):
        """Return active scoped-token metadata without bearer material."""
        now = time.time()
        with self._lock:
            expired = [
                token for token, grant in self._tokens.items()
                if grant.expires_at and grant.expires_at <= now
            ]
            for token in expired:
                self._tokens.pop(token, None)
            grants = tuple(
                grant for grant in self._tokens.values() if not grant.is_master
            )
        return [
            self._metadata(grant)
            for grant in sorted(grants, key=lambda item: (item.created_at, item.token_id))
        ]

    def remove_by_id(self, token_id):
        """Revoke one scoped token by opaque record id; never the master."""
        wanted = str(token_id or "").strip()
        if not wanted:
            return False
        with self._lock:
            for token, grant in tuple(self._tokens.items()):
                if grant.token_id == wanted and not grant.is_master:
                    self._tokens.pop(token, None)
                    return True
        return False

    def __len__(self):
        with self._lock:
            return len(self._tokens)

class PairingStore:
    """One-time, short-lived pairing code registry."""

    def __init__(self):
        self._lock = threading.Lock()
        self._digest = b""
        self._expires_at = 0.0
        self._scopes = frozenset()
        self._attempts = 0

    def issue(self, scopes, ttl_seconds=300):
        code = secrets.token_urlsafe(18)
        with self._lock:
            self._digest = hashlib.sha256(code.encode("ascii")).digest()
            self._expires_at = time.time() + max(30, min(600, int(ttl_seconds)))
            self._scopes = frozenset(scopes) & ALL_SCOPES
            self._attempts = 0
        return code

    def consume(self, code):
        digest = hashlib.sha256(str(code or "").encode("utf-8")).digest()
        with self._lock:
            valid = bool(
                self._digest
                and self._expires_at > time.time()
                and self._attempts < 5
                and secrets.compare_digest(self._digest, digest)
            )
            if valid:
                scopes = self._scopes
                self._digest = b""
                self._expires_at = 0.0
                self._scopes = frozenset()
                return scopes
            self._attempts += 1
            if self._attempts >= 5 or self._expires_at <= time.time():
                self._digest = b""
                self._scopes = frozenset()
            return None

class ReplayStore:
    """Bounded per-token nonce cache for mutating requests."""

    def __init__(self, *, max_age_seconds=120, max_entries=20_000):
        self._lock = threading.Lock()
        self._seen = {}
        self._max_age = max(30, min(300, int(max_age_seconds)))
        self._max_entries = max(100, min(100_000, int(max_entries)))

    def validate_freshness(self, timestamp, nonce):
        try:
            request_time = int(str(timestamp or ""))
        except (TypeError, ValueError):
            return False, "request_timestamp_invalid"
        now = int(time.time())
        if abs(now - request_time) > self._max_age:
            return False, "request_timestamp_expired"
        nonce = str(nonce or "")
        if not _NONCE_RE.fullmatch(nonce):
            return False, "request_nonce_invalid"
        return True, ""

    def accept(self, token, timestamp, nonce):
        fresh, error = self.validate_freshness(timestamp, nonce)
        if not fresh:
            return False, error
        now = int(time.time())
        nonce = str(nonce or "")
        key = hashlib.sha256(
            str(token).encode("utf-8") + b"\0" + nonce.encode("ascii")
        ).digest()
        with self._lock:
            cutoff = now - self._max_age
            self._seen = {
                seen_key: seen_at for seen_key, seen_at in self._seen.items()
                if seen_at >= cutoff
            }
            if key in self._seen:
                return False, "request_replayed"
            if len(self._seen) >= self._max_entries:
                return False, "request_replay_window_full"
            self._seen[key] = now
        return True, ""
