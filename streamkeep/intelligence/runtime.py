"""Reachable, consent-aware runtime for summaries and smart thumbnails.

The intelligence workers deliberately keep their durable contract separate from
the provider implementations. Profiles store only non-secret settings in
SQLite; API keys live in the existing secure store. Cloud summaries require a
one-use preview token bound to the exact transcript payload, provider, model,
and recording path. Local Ollama processing never needs that token.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from .. import db
from ..secrets import delete_secret_value, get_secret_value, set_secret_value
from .summarize import (
    MAX_TRANSCRIPT_CHARS,
    SummaryConsentRequired,
    is_cloud_provider,
    load_transcript,
    provider_label,
    redact_transcript,
    summarize_recording,
    transcript_digest,
)
from .thumbnail import THUMBNAIL_PROVIDER_VERSION, generate_thumbnail


_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PROVIDERS = frozenset({"ollama", "openai", "anthropic"})
_DEFAULT_MODELS = {
    "ollama": "llama3",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-20250514",
}
_MAX_RESULT_TEXT = 100_000
_CONSENT_TTL_SECONDS = 10 * 60


class IntelligenceError(RuntimeError):
    """A safe, operator-facing intelligence workflow error."""


def _safe_error(error: Any, secret: str = "") -> str:
    from ..diagnostics import redact_text

    message = redact_text(str(error or "intelligence job failed"))
    if secret:
        message = message.replace(str(secret), "***REDACTED***")
    return message[:2000]


def _safe_profile_id(value: str) -> str:
    profile_id = str(value or "").strip()
    if not _PROFILE_ID_RE.fullmatch(profile_id):
        raise IntelligenceError(
            "profile_id must contain only letters, numbers, '.', '_' or '-'."
        )
    return profile_id


def _normalize_url(value: str, provider: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if not value:
        return "" if provider == "ollama" else (
            "https://api.anthropic.com" if provider == "anthropic"
            else "https://api.openai.com"
        )
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise IntelligenceError("api_url must be an HTTP(S) URL without credentials.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise IntelligenceError(
            "api_url must not contain credentials, a query, or a fragment."
        )
    if parsed.scheme == "http" and parsed.hostname not in {
        "127.0.0.1", "localhost", "::1",
    }:
        raise IntelligenceError("Cloud API URLs must use HTTPS.")
    return value


def _provider_version(provider: str) -> str:
    if provider == "ollama":
        return "ollama-local-v1"
    if provider == "anthropic":
        return "anthropic-2023-06-01"
    return "openai-chat-completions-v1"


def local_capabilities(provider: str = "ollama") -> dict[str, Any]:
    """Return a bounded, side-effect-free capability check for local models."""
    provider = str(provider or "").strip().lower()
    if provider != "ollama":
        return {"provider": provider, "available": True, "detail": "Remote provider configured."}
    endpoint = "http://127.0.0.1:11434/api/version"
    try:
        with urllib.request.urlopen(endpoint, timeout=0.35) as response:
            data = json.loads(response.read(4096).decode("utf-8", errors="replace"))
        version = str(data.get("version", "") or "") if isinstance(data, dict) else ""
        return {
            "provider": provider, "available": True,
            "endpoint": endpoint, "version": version,
            "detail": "Local Ollama endpoint is responding.",
        }
    except Exception:
        return {
            "provider": provider, "available": False,
            "endpoint": endpoint,
            "detail": "Local Ollama is not responding; install it or start its local service.",
        }


def _profile_secret_id(profile_id: str) -> str:
    return f"intelligence-profile:{profile_id}"


def _public_profile(row: dict[str, Any]) -> dict[str, Any]:
    config = dict(row.get("config") or {})
    config.pop("api_key", None)
    config.pop("token", None)
    config.pop("secret", None)
    return {
        "profile_id": str(row.get("profile_id", "")),
        "label": str(row.get("label", "")),
        "provider": str(row.get("provider", "")),
        "provider_label": provider_label(row.get("provider", "")),
        "model": str(row.get("model", "")),
        "api_url": str(row.get("api_url", "")),
        "config": config,
        "has_api_key": bool(row.get("secret_ref")),
        "created_at": str(row.get("created_at", "")),
        "updated_at": str(row.get("updated_at", "")),
    }


def save_profile(
    profile_id: str,
    provider: str,
    config: dict[str, Any] | None = None,
    *,
    label: str = "",
) -> dict[str, Any]:
    """Save a provider profile without writing its API key to SQLite."""
    profile_id = _safe_profile_id(profile_id)
    provider = str(provider or "").strip().lower()
    if provider not in _PROVIDERS:
        raise IntelligenceError(f"Unsupported intelligence provider: {provider or 'empty'}")
    incoming = dict(config or {})
    model = str(incoming.pop("model", "") or _DEFAULT_MODELS[provider]).strip()
    api_url = _normalize_url(incoming.pop("api_url", ""), provider)
    secret = ""
    for key in ("api_key", "token", "secret"):
        if key in incoming:
            secret = str(incoming.pop(key) or "")
            break
    existing = db.load_intelligence_profile(profile_id)
    secret_ref = str(existing.get("secret_ref", "") if existing else "")
    if secret:
        secret_ref = set_secret_value(_profile_secret_id(profile_id), secret)
    elif bool(incoming.pop("clear_api_key", False)):
        delete_secret_value(_profile_secret_id(profile_id))
        secret_ref = ""
    public_config = {
        key: value for key, value in incoming.items()
        if key in {"redact_default", "timeout_seconds"}
    }
    row = db.save_intelligence_profile(
        profile_id, provider, model, api_url, public_config,
        label=str(label or ""), secret_ref=secret_ref,
    )
    return _public_profile(row)


def list_profiles() -> list[dict[str, Any]]:
    return [_public_profile(row) for row in db.load_intelligence_profiles()]


def delete_profile(profile_id: str) -> bool:
    profile_id = _safe_profile_id(profile_id)
    deleted = db.delete_intelligence_profile(profile_id)
    if deleted:
        delete_secret_value(_profile_secret_id(profile_id))
    return deleted


def _resolve_profile(
    profile_id: str = "",
    *,
    provider: str = "ollama",
    model: str = "",
    api_url: str = "",
    api_key: str = "",
    redact: bool = False,
    require_secret: bool = True,
) -> dict[str, Any]:
    profile_id = str(profile_id or "").strip()
    if profile_id:
        row = db.load_intelligence_profile(profile_id)
        if row is None:
            raise IntelligenceError("intelligence profile was not found")
        provider = str(row.get("provider", "ollama") or "ollama").lower()
        model = str(row.get("model", "") or _DEFAULT_MODELS[provider])
        api_url = str(row.get("api_url", "") or "")
        public_config = dict(row.get("config") or {})
        redact = bool(redact or public_config.get("redact_default", False))
        if row.get("secret_ref"):
            value = get_secret_value(
                str(row["secret_ref"])[len("secretref:"):]
                if str(row["secret_ref"]).startswith("secretref:")
                else _profile_secret_id(profile_id)
            )
            api_key = str(value or "")
    provider = str(provider or "ollama").strip().lower()
    if provider not in _PROVIDERS:
        raise IntelligenceError("unsupported intelligence provider")
    model = str(model or _DEFAULT_MODELS[provider]).strip()
    api_url = _normalize_url(api_url, provider)
    if is_cloud_provider(provider) and require_secret and not api_key:
        raise IntelligenceError(
            f"No API key is configured for {provider_label(provider)}."
        )
    return {
        "profile_id": profile_id,
        "provider": provider,
        "model": model,
        "api_url": api_url,
        "api_key": api_key,
        "redact": bool(redact),
        "provider_version": _provider_version(provider),
    }


class _ConsentStore:
    """Short-lived, one-use consent records bound to exact payload metadata."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}

    def _prune(self):
        now = time.time()
        self._items = {
            key: value for key, value in self._items.items()
            if float(value.get("expires_at", 0)) > now
        }

    def preview(
        self,
        recording_dir: str,
        *,
        provider: str,
        model: str,
        api_url: str = "",
        profile_id: str = "",
        redact: bool = False,
    ) -> dict[str, Any]:
        recording_dir = os.path.abspath(str(recording_dir or ""))
        if not os.path.isdir(recording_dir):
            raise IntelligenceError("recording directory was not found")
        provider = str(provider or "ollama").strip().lower()
        model = str(model or _DEFAULT_MODELS.get(provider, "")).strip()
        payload = load_transcript(recording_dir)
        if redact and is_cloud_provider(provider):
            payload = redact_transcript(payload)
        payload = payload[:MAX_TRANSCRIPT_CHARS]
        if len(payload.strip()) < 100:
            raise IntelligenceError("No transcript found or transcript is too short (<100 chars).")
        digest = transcript_digest(payload)
        token = ""
        expires_at = 0
        if is_cloud_provider(provider):
            token = secrets.token_urlsafe(32)
            expires_at = int(time.time() + _CONSENT_TTL_SECONDS)
            item = {
                "recording_dir": recording_dir,
                "provider": provider,
                "model": model,
                "api_url": str(api_url or ""),
                "profile_id": str(profile_id or ""),
                "payload": payload,
                "payload_sha256": digest,
                "redaction_applied": bool(redact),
                "expires_at": expires_at,
            }
            with self._lock:
                self._prune()
                self._items[token] = item
        return {
            "provider": provider,
            "provider_label": provider_label(provider),
            "model": model,
            "payload": payload,
            "payload_sha256": digest,
            "payload_chars": len(payload),
            "redaction_applied": bool(redact),
            "requires_consent": is_cloud_provider(provider),
            "consent_token": token,
            "expires_at": expires_at,
        }

    def consume(
        self,
        token: str,
        *,
        recording_dir: str,
        provider: str,
        model: str,
        api_url: str = "",
        profile_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._prune()
            item = self._items.pop(str(token or ""), None)
        if item is None:
            raise SummaryConsentRequired("Consent preview is missing, expired, or already used.")
        expected = {
            "recording_dir": os.path.abspath(str(recording_dir or "")),
            "provider": str(provider or "").lower(),
            "model": str(model or ""),
            "api_url": str(api_url or ""),
            "profile_id": str(profile_id or ""),
        }
        for key, value in expected.items():
            if not hmac.compare_digest(str(item.get(key, "")), str(value)):
                raise SummaryConsentRequired("Consent preview does not match this request.")
        return item


def public_job(job: dict[str, Any] | None) -> dict[str, Any]:
    """Return an API-safe job view without local paths or secret material."""
    item = dict(job or {})
    source_path = str(item.pop("source_path", "") or "")
    result_path = str(item.get("result_path", "") or "")
    item["source_name"] = Path(source_path).name if source_path else ""
    item["result_name"] = Path(result_path).name if result_path else ""
    item.pop("result_path", None)
    item.pop("result", None)
    item["provider_label"] = provider_label(item.get("provider", ""))
    return item


class IntelligenceRuntime:
    """Durable local worker runtime shared by REST, CLI, and the desktop."""

    def __init__(self):
        db.init_db()
        db.recover_intelligence_jobs()
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._consent = _ConsentStore()

    def preview(self, recording_dir: str, **kwargs) -> dict[str, Any]:
        resolved = _resolve_profile(**{
            key: kwargs.get(key, "")
            for key in ("profile_id", "provider", "model", "api_url", "api_key")
        }, redact=bool(kwargs.get("redact", False)), require_secret=False)
        return self._consent.preview(
            recording_dir,
            provider=resolved["provider"], model=resolved["model"],
            api_url=resolved["api_url"], profile_id=resolved["profile_id"],
            redact=resolved["redact"],
        ) | {"capability": local_capabilities(resolved["provider"])}

    def start_summary(
        self,
        recording_dir: str,
        *,
        profile_id: str = "",
        provider: str = "ollama",
        model: str = "",
        api_url: str = "",
        api_key: str = "",
        consent_token: str = "",
        redact: bool = False,
        history_id: int = 0,
        wait: bool = False,
    ) -> dict[str, Any]:
        resolved = _resolve_profile(
            profile_id, provider=provider, model=model, api_url=api_url,
            api_key=api_key, redact=redact,
        )
        if is_cloud_provider(resolved["provider"]):
            consent = self._consent.consume(
                consent_token,
                recording_dir=recording_dir,
                provider=resolved["provider"], model=resolved["model"],
                api_url=resolved["api_url"], profile_id=resolved["profile_id"],
            )
            payload = str(consent["payload"])
            redaction_applied = bool(consent.get("redaction_applied"))
        else:
            payload = load_transcript(recording_dir)[:MAX_TRANSCRIPT_CHARS]
            redaction_applied = False
        digest = transcript_digest(payload)
        job = db.create_intelligence_job(
            "summary", recording_dir, history_id=history_id,
            profile_id=resolved["profile_id"],
            provider=resolved["provider"], model=resolved["model"],
            provider_version=resolved["provider_version"],
            payload_sha256=digest, payload_chars=len(payload),
            redaction_applied=redaction_applied,
            result_path=os.path.join(recording_dir, ".summary.md"),
        )
        self._launch(job["job_id"], self._run_summary, {
            "recording_dir": os.path.abspath(recording_dir),
            "resolved": resolved,
            "payload": payload,
        })
        if wait:
            return self.wait(job["job_id"])
        return public_job(job)

    def start_thumbnail(
        self,
        recording_dir: str,
        *,
        history_id: int = 0,
        title: str = "",
        channel: str = "",
        date: str = "",
        wait: bool = False,
    ) -> dict[str, Any]:
        job = db.create_intelligence_job(
            "thumbnail", recording_dir, history_id=history_id,
            provider="local", model="frame-scoring-v1",
            provider_version=THUMBNAIL_PROVIDER_VERSION,
            result_path=os.path.join(recording_dir, "smart-thumbnail.jpg"),
        )
        self._launch(job["job_id"], self._run_thumbnail, {
            "recording_dir": os.path.abspath(recording_dir),
            "title": str(title or ""), "channel": str(channel or ""),
            "date": str(date or ""),
        })
        if wait:
            return self.wait(job["job_id"])
        return public_job(job)

    def _launch(self, job_id: str, target, payload: dict[str, Any]):
        event = threading.Event()
        with self._lock:
            self._events[job_id] = event
            thread = threading.Thread(
                target=target, args=(job_id, event, payload),
                name=f"streamkeep-intelligence-{job_id[:8]}", daemon=True,
            )
            self._threads[job_id] = thread
            thread.start()

    def _finish(self, job_id: str):
        with self._lock:
            self._events.pop(job_id, None)
            self._threads.pop(job_id, None)

    def _run_summary(self, job_id: str, event: threading.Event, payload: dict[str, Any]):
        resolved = payload["resolved"]
        try:
            db.update_intelligence_job(job_id, {"status": "running", "progress": 0.02})
            result = summarize_recording(
                payload["recording_dir"], provider=resolved["provider"],
                model=resolved["model"], api_url=resolved["api_url"],
                api_key=resolved["api_key"], cloud_consent=True,
                transcript_text=payload["payload"],
                redact=False, cancel_event=event,
                progress_fn=lambda value: db.update_intelligence_job(
                    job_id, {"progress": value}
                ),
            )
            if event.is_set() or not result:
                db.update_intelligence_job(job_id, {
                    "status": "cancelled" if event.is_set() else "failed",
                    "error": "Analysis cancelled" if event.is_set()
                    else "No summary generated (no transcript or provider unavailable)",
                })
                return
            db.update_intelligence_job(job_id, {
                "status": "completed", "progress": 1.0,
                "completed_at": db._utc_now_iso(),
                "result": {
                    "provider": resolved["provider"],
                    "model": resolved["model"],
                    "provider_version": resolved["provider_version"],
                    "payload_sha256": transcript_digest(payload["payload"]),
                    "payload_chars": len(payload["payload"]),
                    "redaction_applied": bool(
                        db.load_intelligence_job(job_id).get("redaction_applied")
                    ),
                    "result_path": os.path.join(payload["recording_dir"], ".summary.md"),
                },
            })
        except SummaryConsentRequired as error:
            db.update_intelligence_job(job_id, {
                "status": "failed", "error": _safe_error(error, resolved["api_key"]),
            })
        except Exception as error:
            db.update_intelligence_job(job_id, {
                "status": "failed", "error": _safe_error(error, resolved["api_key"]),
            })
        finally:
            self._finish(job_id)

    def _run_thumbnail(self, job_id: str, event: threading.Event, payload: dict[str, Any]):
        try:
            db.update_intelligence_job(job_id, {"status": "running", "progress": 0.05})
            if event.is_set():
                db.update_intelligence_job(job_id, {"status": "cancelled", "error": "Analysis cancelled"})
                return
            path, score = generate_thumbnail(
                payload["recording_dir"], title=payload["title"],
                channel=payload["channel"], date=payload["date"],
            )
            if event.is_set():
                db.update_intelligence_job(job_id, {"status": "cancelled", "error": "Analysis cancelled"})
                return
            if not path:
                db.update_intelligence_job(job_id, {
                    "status": "failed", "error": "No media file or usable frame was found.",
                })
                return
            db.update_intelligence_job(job_id, {
                "status": "completed", "progress": 1.0,
                "completed_at": db._utc_now_iso(),
                "result": {
                    "provider": "local", "model": "frame-scoring-v1",
                    "provider_version": THUMBNAIL_PROVIDER_VERSION,
                    "score": round(float(score), 4), "result_path": path,
                },
            })
        except Exception as error:
            db.update_intelligence_job(job_id, {"status": "failed", "error": _safe_error(error)})
        finally:
            self._finish(job_id)

    def cancel(self, job_id: str) -> bool:
        job_id = str(job_id or "")
        with self._lock:
            event = self._events.get(job_id)
        if event is not None:
            event.set()
        return db.request_intelligence_cancel(job_id)

    def wait(self, job_id: str, timeout: float = 120.0) -> dict[str, Any]:
        deadline = time.time() + max(0.1, float(timeout or 120))
        while time.time() < deadline:
            job = db.load_intelligence_job(job_id)
            if job is None:
                return {}
            if job.get("status") in {"completed", "failed", "cancelled", "retryable"}:
                return public_job(job)
            time.sleep(0.05)
        return public_job(db.load_intelligence_job(job_id))

    def list_jobs(self, *, kind: str = "", limit: int = 100) -> list[dict[str, Any]]:
        return [public_job(job) for job in db.load_intelligence_jobs(limit, kind=kind)]

    def edit_summary(self, job_id: str, text: str) -> dict[str, Any]:
        value = str(text or "")
        if len(value) > _MAX_RESULT_TEXT:
            raise IntelligenceError("Summary text exceeds the 100,000 character limit.")
        job = db.load_intelligence_job(job_id)
        if not job or job.get("kind") != "summary":
            raise IntelligenceError("summary job was not found")
        result_path = str(job.get("result_path") or "")
        if not result_path:
            result_path = os.path.join(str(job.get("source_path") or ""), ".summary.md")
        target = Path(result_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(value, encoding="utf-8")
        os.replace(temp, target)
        updated = db.update_intelligence_job(job_id, {
            "status": "completed", "progress": 1.0, "edited": True,
            "result_path": str(target), "result": {
                **dict(job.get("result") or {}), "result_path": str(target),
                "edited": True,
            },
        })
        return public_job(updated)

    def rebuild_summary(self, job_id: str, *, consent_token: str = "", wait: bool = False):
        job = db.load_intelligence_job(job_id)
        if not job or job.get("kind") != "summary":
            raise IntelligenceError("summary job was not found")
        return self.start_summary(
            str(job.get("source_path") or ""),
            profile_id=str(job.get("profile_id") or ""),
            provider=str(job.get("provider") or "ollama"),
            model=str(job.get("model") or ""),
            consent_token=consent_token,
            history_id=int(job.get("history_id", 0) or 0),
            wait=wait,
        )


_RUNTIME: IntelligenceRuntime | None = None
_RUNTIME_LOCK = threading.Lock()


def get_runtime() -> IntelligenceRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = IntelligenceRuntime()
        return _RUNTIME


__all__ = [
    "IntelligenceError", "IntelligenceRuntime", "get_runtime", "list_profiles",
    "save_profile", "delete_profile", "public_job", "is_cloud_provider",
    "local_capabilities",
]
