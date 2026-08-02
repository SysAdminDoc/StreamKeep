"""Reachable, credential-safe upload profiles and durable transfer jobs."""

from __future__ import annotations

import os
import re
import threading
from typing import Any

from .. import db
from ..diagnostics import redact_text
from ..secrets import (
    SECRET_REF_PREFIX,
    delete_secret_value,
    get_secret_value,
    set_secret_value,
)
from .base import UploadDestination, sanitize_upload_message

_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SECRET_KEYS = frozenset({
    "access_key", "api_key", "password", "passphrase", "private_key",
    "secret", "secret_key", "token", "username",
})
_RUNTIME_LOCK = threading.Lock()
_RUNTIME = None


def _secret_key(key: object) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    return normalized in _SECRET_KEYS or normalized.endswith(
        ("_password", "_secret", "_token", "_access_key", "_secret_key")
    )


def _split_config(config: dict[str, Any] | None):
    public: dict[str, Any] = {}
    secret: dict[str, Any] = {}
    for raw_key, value in dict(config or {}).items():
        key = str(raw_key)
        if _secret_key(key):
            if value not in (None, "", [], {}):
                secret[key] = value
        elif isinstance(value, (str, int, float, bool)) or value is None:
            public[key] = value
    return public, secret


def _profile_secret_id(profile_id: str) -> str:
    return f"upload-profile:{profile_id}"


def _validate_profile_id(profile_id: str) -> str:
    profile_id = str(profile_id or "").strip()
    if not _PROFILE_ID_RE.fullmatch(profile_id):
        raise ValueError("upload profile id must contain only letters, numbers, '_' or '-'")
    return profile_id


def save_profile(
    profile_id: str,
    adapter: str,
    config: dict[str, Any] | None = None,
    *,
    label: str = "",
) -> dict[str, Any]:
    """Save a destination profile with secret fields in the secure store."""
    profile_id = _validate_profile_id(profile_id)
    adapter = str(adapter or "").strip()
    if adapter not in UploadDestination.all_adapters():
        raise ValueError(f"Unknown upload adapter: {adapter}")
    public, secret = _split_config(config)
    secret_id = _profile_secret_id(profile_id)
    if secret:
        secret_ref = set_secret_value(secret_id, secret)
    else:
        delete_secret_value(secret_id)
        secret_ref = ""
    db.save_upload_profile(
        profile_id, adapter, public, label=str(label or "").strip(),
        secret_ref=secret_ref,
    )
    return profile_view(profile_id) or {}


def profile_view(profile_id: str) -> dict[str, Any] | None:
    row = db.load_upload_profile(profile_id)
    if row is None:
        return None
    return {
        "profile_id": row["profile_id"],
        "label": row["label"],
        "adapter": row["adapter"],
        "config": dict(row.get("config", {})),
        "has_credentials": bool(row.get("secret_ref")),
        "created_at": row.get("created_at", ""),
        "updated_at": row.get("updated_at", ""),
    }


def list_profiles() -> list[dict[str, Any]]:
    return [
        item
        for row in db.load_upload_profiles()
        for item in [profile_view(row["profile_id"])]
        if item is not None
    ]


def delete_profile(profile_id: str) -> bool:
    profile_id = _validate_profile_id(profile_id)
    deleted = db.delete_upload_profile(profile_id)
    if deleted:
        delete_secret_value(_profile_secret_id(profile_id))
    return deleted


def resolve_profile(profile_id: str) -> dict[str, Any] | None:
    row = db.load_upload_profile(profile_id)
    if row is None:
        return None
    config = dict(row.get("config", {}))
    secret_ref = str(row.get("secret_ref", "") or "")
    if secret_ref.startswith(SECRET_REF_PREFIX):
        values = get_secret_value(secret_ref[len(SECRET_REF_PREFIX):])
        if isinstance(values, dict):
            config.update(values)
    return {
        "profile_id": row["profile_id"],
        "label": row["label"],
        "adapter": row["adapter"],
        "config": config,
    }


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, value in dict(metadata or {}).items():
        key = str(raw_key)
        if _secret_key(key):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = redact_text(str(value)) if isinstance(value, str) else value
    return result


def create_job(
    profile_id: str,
    source_path: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = resolve_profile(profile_id)
    if profile is None:
        raise ValueError("Upload profile was not found")
    return db.create_upload_job(
        profile["profile_id"], profile["adapter"], source_path,
        metadata=_safe_metadata(metadata),
    )


def public_job(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "upload_id": row.get("upload_id", ""),
        "profile_id": row.get("profile_id", ""),
        "adapter": row.get("adapter", ""),
        "source_name": os.path.basename(str(row.get("source_path", "") or "")),
        "metadata": dict(row.get("metadata", {})),
        "status": row.get("status", ""),
        "bytes_sent": int(row.get("bytes_sent", 0) or 0),
        "total_bytes": int(row.get("total_bytes", 0) or 0),
        "attempts": int(row.get("attempts", 0) or 0),
        "next_attempt_at": float(row.get("next_attempt_at", 0) or 0),
        "last_error": sanitize_upload_message(row.get("last_error", "")),
        "remote_uri": sanitize_upload_message(row.get("remote_uri", "")),
        "created_at": row.get("created_at", ""),
        "updated_at": row.get("updated_at", ""),
        "completed_at": row.get("completed_at", ""),
    }


class UploadRuntime:
    """Run persisted uploads in isolated worker threads.

    A process restart first converts abandoned ``uploading`` rows to visible
    retryable rows.  Secure adapters write a ``.part`` object and only rename
    it after the complete byte count has arrived, so a successful row never
    represents an interrupted transfer.
    """

    def __init__(self):
        db.init_db()
        db.recover_upload_jobs()
        self._lock = threading.Lock()
        self._workers: dict[str, threading.Thread] = {}

    def enqueue(self, profile_id: str, source_path: str, *, metadata=None):
        job = create_job(profile_id, source_path, metadata=metadata)
        self.submit(job["upload_id"])
        return db.load_upload_job(job["upload_id"]) or job

    def submit(self, upload_id: str) -> bool:
        upload_id = str(upload_id or "")
        with self._lock:
            worker = self._workers.get(upload_id)
            if worker is not None and worker.is_alive():
                return False
            worker = threading.Thread(
                target=self._run,
                args=(upload_id,),
                name=f"streamkeep-upload-{upload_id[:8]}",
                daemon=True,
            )
            self._workers[upload_id] = worker
            worker.start()
        return True

    def start_due(self) -> int:
        count = 0
        for row in db.load_due_upload_jobs():
            if self.submit(row["upload_id"]):
                count += 1
        return count

    def retry(self, upload_id: str) -> bool:
        if not db.retry_upload_job(upload_id):
            return False
        self.submit(upload_id)
        return True

    def cancel(self, upload_id: str) -> bool:
        return db.cancel_upload_job(upload_id)

    def _run(self, upload_id: str):
        try:
            row = db.start_upload_job(upload_id)
            if row is None:
                return
            profile = resolve_profile(row["profile_id"])
            if profile is None or profile["adapter"] != row["adapter"]:
                self._finish_failure(upload_id, "Upload profile is unavailable")
                return
            adapter_cls = UploadDestination.all_adapters().get(row["adapter"])
            if adapter_cls is None:
                self._finish_failure(upload_id, "Upload adapter is unavailable")
                return

            def _progress(sent, total):
                db.update_upload_progress(upload_id, sent, total)

            try:
                adapter_config = dict(profile["config"])
                remote_dir = str(
                    row.get("metadata", {}).get("remote_dir", "") or ""
                ).strip()
                if remote_dir:
                    adapter_config["remote_dir"] = remote_dir
                    adapter_config["prefix"] = remote_dir
                destination = adapter_cls(adapter_config)
                ok, message = destination.upload(
                    row["source_path"], row.get("metadata", {}),
                    progress_cb=_progress,
                )
            except Exception as error:
                ok, message = False, f"Upload crashed: {error}"
            safe_message = sanitize_upload_message(message, profile["config"])
            if ok:
                remote_uri = safe_message.split("Uploaded to ", 1)[-1]
                db.finish_upload_job(
                    upload_id, success=True, message="", remote_uri=remote_uri,
                )
            else:
                self._finish_failure(upload_id, safe_message, row=row)
        finally:
            with self._lock:
                self._workers.pop(upload_id, None)

    @staticmethod
    def _finish_failure(upload_id, message, row=None):
        row = row or db.load_upload_job(upload_id) or {}
        attempts = int(row.get("attempts", 1) or 1)
        delay = min(30.0 * (2 ** max(0, attempts - 1)), 3600.0)
        db.finish_upload_job(
            upload_id, success=False,
            message=sanitize_upload_message(message), retry_delay=delay,
        )


def get_runtime() -> UploadRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = UploadRuntime()
        return _RUNTIME
