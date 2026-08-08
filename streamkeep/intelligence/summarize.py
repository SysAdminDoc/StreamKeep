"""Content summary via LLM — local (ollama) or cloud API (F60).

Feeds transcript text to an LLM and generates a structured `.summary.md`
with overview, key topics, notable moments with timestamps, and participants.

Supported backends:
  - ollama (local, free): POST http://localhost:11434/api/generate
  - Anthropic Claude API: via anthropic SDK
  - OpenAI-compatible: via requests to any /v1/chat/completions endpoint

Chunked processing: transcripts > 8K tokens are split, summarized per-chunk,
then the chunk summaries are summarized into a final output.
"""

import json
import hashlib
import os
import re
import tempfile
import threading
import urllib.request

from ..net_guard import (
    MAX_PROVIDER_RESPONSE_BYTES,
    GuardedRequestError,
    guarded_json_post,
    read_bounded,
    require_https_endpoint,
)

from PyQt6.QtCore import QThread, pyqtSignal


MAX_CHUNK_CHARS = 24000   # ~6K tokens at 4 chars/token
MAX_SUMMARY_WORDS = 500
MAX_TRANSCRIPT_CHARS = 1_500_000
SUMMARY_PROVIDER_VERSION = "summary-contract-v1"
_CLOUD_PROVIDERS = frozenset({"anthropic", "openai"})
ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
#: Ollama is local and unauthenticated; addressed directly, read bounded.
OLLAMA_ENDPOINT = "http://localhost:11434/api/generate"
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,})\b"
)


class SummaryConsentRequired(RuntimeError):
    """Raised when a cloud provider is selected without explicit consent."""

SYSTEM_PROMPT = """You are a content analyst. Given a transcript from a live stream or video recording, produce a structured summary in Markdown with these sections:

## Overview
A 2-3 sentence overview of the content.

## Key Topics
- Bulleted list of main topics discussed

## Notable Moments
- [HH:MM:SS] Brief description of what happened

## Participants
- List of speakers/participants identified

Keep the summary under 500 words. Use timestamps from the transcript."""


def is_cloud_provider(provider: str) -> bool:
    """Return whether a provider sends transcript data to a hosted API."""
    return str(provider or "").strip().lower() in _CLOUD_PROVIDERS


def provider_label(provider: str) -> str:
    labels = {
        "ollama": "Ollama (local)",
        "openai": "OpenAI-compatible cloud endpoint",
        "anthropic": "Anthropic Claude cloud API",
    }
    return labels.get(str(provider or "").strip().lower(), str(provider or "unknown"))


def redact_transcript(text: str) -> str:
    """Redact common personal data and bearer tokens before a cloud request."""
    value = str(text or "")
    value = _TOKEN_RE.sub("[REDACTED_TOKEN]", value)
    value = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    return _URL_RE.sub("[REDACTED_URL]", value)


def transcript_digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _load_transcript(recording_dir):
    """Load transcript text from .transcript.json or .srt files."""
    # Prefer .transcript.json (has timestamps)
    tj = os.path.join(recording_dir, ".transcript.json")
    if os.path.isfile(tj):
        try:
            with open(tj, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                lines = []
                for seg in data:
                    if not isinstance(seg, dict):
                        continue
                    ts = seg.get("start", 0)
                    text = seg.get("text", seg.get("word", ""))
                    speaker = seg.get("speaker", "")
                    h = int(ts) // 3600
                    m = (int(ts) % 3600) // 60
                    s = int(ts) % 60
                    prefix = f"[{h}:{m:02d}:{s:02d}]"
                    if speaker:
                        prefix += f" {speaker}:"
                    lines.append(f"{prefix} {text}")
                return "\n".join(lines)[:MAX_TRANSCRIPT_CHARS]
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    # Fallback to .srt
    for fn in os.listdir(recording_dir):
        if fn.endswith(".srt"):
            try:
                with open(os.path.join(recording_dir, fn), "r", encoding="utf-8") as f:
                    return f.read(MAX_TRANSCRIPT_CHARS)
            except OSError:
                pass

    return ""


load_transcript = _load_transcript


def _chunk_text(text, max_chars=MAX_CHUNK_CHARS):
    """Split text into chunks respecting line boundaries."""
    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines():
        if current_len + len(line) > max_chars and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


def _write_summary_atomically(path, text):
    """Write a summary beside the recording without exposing a partial file."""
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(
        prefix=".streamkeep_summary_", suffix=".tmp", dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


# ── LLM backends ────────────────────────────────────────────────────

def _query_ollama(prompt, model="llama3", log_fn=None):
    """Query a local ollama instance."""
    try:
        body = json.dumps({
            "model": model,
            "prompt": prompt,
            "system": SYSTEM_PROMPT,
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_ENDPOINT,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        # Ollama listens unauthenticated on loopback, so it is addressed
        # directly rather than through the address-validating proxy, which
        # exists to keep *remote* requests off private space. The read is still
        # bounded: anything squatting that port could otherwise stream an
        # unbounded body into the finalize path.
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = read_bounded(
                resp, MAX_PROVIDER_RESPONSE_BYTES, subject="ollama provider",
            )
        data = json.loads(payload.decode("utf-8"))
        return data.get("response", "")
    except Exception as e:
        if log_fn:
            log_fn(f"[SUMMARY] ollama query failed: {e}")
        return ""


def _query_openai_compat(prompt, api_url, api_key, model="gpt-4o-mini", log_fn=None):
    """Query an OpenAI-compatible endpoint."""
    try:
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 2000,
        }).encode("utf-8")
        base = require_https_endpoint(
            api_url, subject="OpenAI-compatible summary provider",
        )
        if not base:
            raise GuardedRequestError(
                "An OpenAI-compatible provider needs an https:// base URL."
            )
        data = guarded_json_post(
            base + "/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=120,
            subject="OpenAI-compatible summary provider",
        )
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        if log_fn:
            log_fn(f"[SUMMARY] OpenAI-compat query failed ({api_url}): {e}")
        return ""


def _query_anthropic(prompt, api_key, model="claude-sonnet-4-20250514", log_fn=None):
    """Query the Anthropic Claude API."""
    try:
        body = json.dumps({
            "model": model,
            "max_tokens": 2000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        data = guarded_json_post(
            ANTHROPIC_ENDPOINT,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=120,
            subject="Anthropic summary provider",
        )
        return data["content"][0]["text"]
    except Exception as e:
        if log_fn:
            log_fn(f"[SUMMARY] Anthropic query failed: {e}")
        return ""


def _query_llm(prompt, provider="ollama", model="", api_url="", api_key="",
               log_fn=None, cancel_event=None):
    """Dispatch to the appropriate LLM backend."""
    if cancel_event is not None and cancel_event.is_set():
        return ""
    if provider == "ollama":
        return _query_ollama(prompt, model=model or "llama3", log_fn=log_fn)
    elif provider == "anthropic":
        return _query_anthropic(prompt, api_key=api_key,
                                model=model or "claude-sonnet-4-20250514", log_fn=log_fn)
    elif provider == "openai":
        return _query_openai_compat(prompt, api_url=api_url, api_key=api_key,
                                     model=model or "gpt-4o-mini", log_fn=log_fn)
    return ""


# ── Main summarize function ─────────────────────────────────────────

def summarize_recording(recording_dir, *, provider="ollama", model="",
                        api_url="", api_key="", log_fn=None,
                        cloud_consent=False, transcript_text=None,
                        redact=False, cancel_event=None, progress_fn=None):
    """Generate a summary for a recording directory.

    Returns the summary text (Markdown), or '' on failure.
    """
    provider = str(provider or "ollama").strip().lower()
    if is_cloud_provider(provider) and not cloud_consent:
        if log_fn:
            log_fn(
                f"[SUMMARY] Cloud provider {provider_label(provider)} requires "
                "explicit transcript consent."
            )
        raise SummaryConsentRequired(
            f"Explicit consent is required before sending transcript data to "
            f"{provider_label(provider)}."
        )

    transcript = (
        _load_transcript(recording_dir)
        if transcript_text is None else str(transcript_text or "")
    )
    if redact and is_cloud_provider(provider):
        transcript = redact_transcript(transcript)
    if not transcript or len(transcript.strip()) < 100:
        if log_fn:
            log_fn("[SUMMARY] No transcript found or too short (<100 chars)")
        return ""

    chunks = _chunk_text(transcript)
    if progress_fn:
        progress_fn(0.05)

    def cancelled():
        return cancel_event is not None and cancel_event.is_set()

    if cancelled():
        return ""

    if len(chunks) == 1:
        prompt = f"Summarize this stream transcript:\n\n{chunks[0]}"
        summary = _query_llm(
            prompt, provider, model, api_url, api_key, log_fn=log_fn,
            cancel_event=cancel_event,
        )
        if progress_fn:
            progress_fn(0.85 if summary else 0.5)
    else:
        # Multi-chunk: summarize each, then summarize summaries
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            if cancelled():
                return ""
            prompt = (
                f"Summarize part {i+1}/{len(chunks)} of a stream transcript. "
                f"Focus on key events and topics:\n\n{chunk}"
            )
            cs = _query_llm(
                prompt, provider, model, api_url, api_key, log_fn=log_fn,
                cancel_event=cancel_event,
            )
            if cs:
                chunk_summaries.append(cs)
            if progress_fn:
                progress_fn(0.1 + 0.6 * ((i + 1) / len(chunks)))
        if not chunk_summaries:
            if log_fn:
                log_fn("[SUMMARY] All chunk summaries failed — no output")
            return ""
        combined = "\n\n---\n\n".join(chunk_summaries)
        prompt = (
            f"These are summaries of {len(chunk_summaries)} consecutive parts "
            f"of the same stream. Combine them into one final summary:\n\n{combined}"
        )
        if cancelled():
            return ""
        summary = _query_llm(
            prompt, provider, model, api_url, api_key, log_fn=log_fn,
            cancel_event=cancel_event,
        )
        if progress_fn:
            progress_fn(0.85 if summary else 0.75)

    if summary and not cancelled():
        # Save alongside recording
        out_path = os.path.join(recording_dir, ".summary.md")
        try:
            _write_summary_atomically(out_path, summary)
        except OSError:
            pass

    if progress_fn and summary and not cancelled():
        progress_fn(1.0)
    return summary if not cancelled() else ""


# ── Worker thread ───────────────────────────────────────────────────

class SummarizeWorker(QThread):
    """Run LLM summarization in the background."""

    done = pyqtSignal(bool, str)   # ok, summary_or_error
    log = pyqtSignal(str)

    def __init__(self, recording_dir, provider="ollama", model="",
                 api_url="", api_key="", *, cloud_consent=False,
                 transcript_text=None, redact=False):
        super().__init__()
        self._dir = recording_dir
        self._provider = provider
        self._model = model
        self._api_url = api_url
        self._api_key = api_key
        self._cloud_consent = bool(cloud_consent)
        self._transcript_text = transcript_text
        self._redact = bool(redact)
        self._cancel_event = threading.Event()

    def cancel(self):
        """Request cancellation between bounded provider calls."""
        self._cancel_event.set()

    def run(self):
        try:
            self.log.emit(f"[SUMMARY] Generating summary via {self._provider}...")
            result = summarize_recording(
                self._dir,
                provider=self._provider,
                model=self._model,
                api_url=self._api_url,
                api_key=self._api_key,
                log_fn=self.log.emit,
                cloud_consent=self._cloud_consent,
                transcript_text=self._transcript_text,
                redact=self._redact,
                cancel_event=self._cancel_event,
                progress_fn=lambda value: self.log.emit(
                    f"[SUMMARY] Progress {int(value * 100)}%"
                ),
            )
            if result:
                self.log.emit(f"[SUMMARY] Summary generated ({len(result)} chars)")
                self.done.emit(True, result)
            else:
                self.done.emit(False, "No summary generated (no transcript or LLM unreachable)")
        except Exception as e:
            self.log.emit(f"[SUMMARY] Error: {e}")
            self.done.emit(False, str(e))
