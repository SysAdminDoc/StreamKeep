"""Opt-in local-first translation of public metadata and chapter titles."""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .metadata import MAX_METADATA_BYTES, MetadataSaver, load_metadata_sidecar

TRANSLATION_SCHEMA = "streamkeep.localized-metadata"
TRANSLATION_SCHEMA_VERSION = 1
TRANSLATION_PROVIDER_VERSION = "translation-contract-v1"
MAX_TRANSLATION_CHARS = 100_000
MAX_CHAPTERS = 500
_CLOUD_PROVIDERS = frozenset({"openai", "anthropic"})


class TranslationConsentRequired(RuntimeError):
    """Raised before any cloud provider is contacted without consent."""


class TranslationError(RuntimeError):
    """Raised when a translation response cannot satisfy the sidecar shape."""


def is_cloud_provider(provider):
    return str(provider or "").strip().lower() in _CLOUD_PROVIDERS


def _language(value):
    value = str(value or "").strip().lower().replace("_", "-")
    base = value.split("-", 1)[0]
    return base if base.isalpha() and 2 <= len(base) <= 8 else "en"


def _text(value, limit=MAX_TRANSLATION_CHARS):
    return str(value or "").replace("\x00", " ").strip()[:limit]


def _clean_chapters(chapters):
    rows = []
    for raw in list(chapters or [])[:MAX_CHAPTERS]:
        if not isinstance(raw, dict):
            continue
        try:
            start = max(0.0, float(raw.get("start", 0) or 0))
            end = max(start, float(raw.get("end", start) or start))
        except (TypeError, ValueError, OverflowError):
            start, end = 0.0, 0.0
        title = _text(raw.get("title", "Chapter"), 512) or "Chapter"
        rows.append({"title": title, "start": start, "end": end})
    return rows


def _source_payload(metadata, chapters):
    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "title": _text(metadata.get("title", ""), 1024),
        "description": _text(metadata.get("description", "")),
        "chapters": _clean_chapters(chapters),
    }


def _translation_prompt(source, target_language):
    return (
        "Translate the public media metadata below into the target language. "
        "Return JSON only with exactly these keys: title, description, chapters. "
        "Keep chapter start/end numbers unchanged and return one chapter object "
        "for every input chapter, changing only title. Do not add commentary, "
        "URLs, markup, or personal-data enrichment.\n\n"
        f"Target language: {target_language}\n"
        f"Input JSON:\n{json.dumps(source, ensure_ascii=False)}"
    )


def _query_ollama(prompt, model="", timeout=120):
    body = json.dumps({
        "model": model or "llama3",
        "prompt": prompt,
        "system": "You are a careful metadata translator. Return valid JSON only.",
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data.get("response", "") or "")


def _query_openai(prompt, api_url, api_key, model="", timeout=120):
    body = json.dumps({
        "model": model or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 4000,
    }).encode("utf-8")
    request = urllib.request.Request(
        api_url.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data["choices"][0]["message"]["content"] or "")


def _query_anthropic(prompt, api_key, model="", timeout=120):
    body = json.dumps({
        "model": model or "claude-sonnet-4-20250514",
        "max_tokens": 4000,
        "system": "Return valid JSON only.",
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data["content"][0]["text"] or "")


def _query_provider(prompt, *, provider="ollama", model="", api_url="", api_key="", cloud_consent=False):
    provider = str(provider or "ollama").strip().lower()
    if is_cloud_provider(provider) and not cloud_consent:
        raise TranslationConsentRequired(
            f"Explicit consent is required before sending metadata to {provider}."
        )
    if provider == "ollama":
        return _query_ollama(prompt, model=model)
    if provider == "openai":
        if not api_url or not api_key:
            raise TranslationError("OpenAI translation requires an endpoint and secure API key")
        return _query_openai(prompt, api_url, api_key, model=model)
    if provider == "anthropic":
        if not api_key:
            raise TranslationError("Anthropic translation requires a secure API key")
        return _query_anthropic(prompt, api_key, model=model)
    raise TranslationError(f"Unsupported translation provider: {provider}")


def _decode_response(response):
    response = str(response or "").strip()
    if response.startswith("```"):
        response = response.strip("`")
        if response.lower().startswith("json"):
            response = response[4:].lstrip()
    try:
        payload = json.loads(response)
    except (TypeError, ValueError) as error:
        start, end = response.find("{"), response.rfind("}")
        if start < 0 or end <= start:
            raise TranslationError("Translation provider returned invalid JSON") from error
        try:
            payload = json.loads(response[start:end + 1])
        except (TypeError, ValueError) as nested:
            raise TranslationError("Translation provider returned invalid JSON") from nested
    if not isinstance(payload, dict):
        raise TranslationError("Translation provider returned a non-object")
    return payload


def translate_payload(
    metadata,
    chapters,
    target_language,
    *,
    provider="ollama",
    model="",
    api_url="",
    api_key="",
    cloud_consent=False,
    query_fn=None,
):
    """Translate only public title/description/chapter fields."""
    target_language = _language(target_language)
    provider_name = str(provider or "ollama").strip().lower()
    if is_cloud_provider(provider_name) and not cloud_consent:
        raise TranslationConsentRequired(
            f"Explicit consent is required before sending metadata to {provider_name}."
        )
    source = _source_payload(metadata, chapters)
    if not source["title"] and not source["description"] and not source["chapters"]:
        return {
            "status": "empty",
            "schema": TRANSLATION_SCHEMA,
            "schema_version": TRANSLATION_SCHEMA_VERSION,
            "target_language": target_language,
            "original": source,
            "translated": source,
        }
    prompt = _translation_prompt(source, target_language)
    response = (query_fn or _query_provider)(
        prompt,
        provider=provider_name,
        model=model,
        api_url=api_url,
        api_key=api_key,
        cloud_consent=cloud_consent,
    )
    translated = _decode_response(response)
    translated_title = _text(translated.get("title", ""), 1024)
    translated_description = _text(translated.get("description", ""))
    raw_chapters = translated.get("chapters", [])
    if not isinstance(raw_chapters, list):
        raise TranslationError("Translation provider returned invalid chapters")
    if len(raw_chapters) != len(source["chapters"]):
        raise TranslationError("Translation provider changed the chapter count")
    translated_chapters = []
    for original, raw in zip(source["chapters"], raw_chapters):
        if not isinstance(raw, dict):
            raise TranslationError("Translation provider returned an invalid chapter")
        translated_chapters.append({
            "title": _text(raw.get("title", ""), 512) or original["title"],
            "start": original["start"],
            "end": original["end"],
        })
    return {
        "status": "translated",
        "schema": TRANSLATION_SCHEMA,
        "schema_version": TRANSLATION_SCHEMA_VERSION,
        "provider": provider_name,
        "provider_version": TRANSLATION_PROVIDER_VERSION,
        "target_language": target_language,
        "translated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "original": source,
        "translated": {
            "title": translated_title or source["title"],
            "description": translated_description,
            "chapters": translated_chapters,
        },
    }


def _atomic_write(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".streamkeep_translation_", suffix=".tmp", dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return str(target)


def _chapter_sidecar(recording_dir, file_base):
    base = os.path.basename(file_base) if file_base else "chapters"
    expected = Path(recording_dir) / f"{base}.chapters.json"
    if expected.is_file():
        return expected
    try:
        candidates = sorted(
            Path(recording_dir).glob("*.chapters.json"),
            key=lambda path: path.name,
        )
    except OSError:
        return expected
    return candidates[0] if len(candidates) == 1 else expected


def translate_recording(
    recording_dir,
    *,
    file_base="",
    target_language="en",
    provider="ollama",
    model="",
    api_url="",
    api_key="",
    cloud_consent=False,
    query_fn=None,
):
    """Write translated sidecars while preserving every original sidecar."""
    directory = Path(recording_dir or "")
    if not directory.is_dir():
        return {"status": "unavailable", "reason": "Recording directory does not exist"}
    metadata_path = directory / "metadata.json"
    if not metadata_path.is_file() or metadata_path.stat().st_size > MAX_METADATA_BYTES:
        return {"status": "unavailable", "reason": "Metadata sidecar is missing or exceeds the safety limit"}
    metadata = load_metadata_sidecar(metadata_path)
    chapter_path = _chapter_sidecar(str(directory), file_base)
    chapters = []
    if chapter_path.is_file() and chapter_path.stat().st_size <= MAX_METADATA_BYTES:
        try:
            raw = json.loads(chapter_path.read_text(encoding="utf-8"))
            chapters = raw.get("chapters", []) if isinstance(raw, dict) else raw
        except (OSError, UnicodeError, ValueError):
            chapters = []
    result = translate_payload(
        metadata,
        chapters,
        target_language,
        provider=provider,
        model=model,
        api_url=api_url,
        api_key=api_key,
        cloud_consent=cloud_consent,
        query_fn=query_fn,
    )
    if result["status"] != "translated":
        return result
    language = result["target_language"]
    metadata_output = _atomic_write(directory / f"metadata.{language}.json", result)
    base = os.path.basename(file_base) if file_base else "chapters"
    chapter_output = _atomic_write(
        directory / f"{base}.chapters.{language}.json",
        {
            "schema": TRANSLATION_SCHEMA,
            "schema_version": TRANSLATION_SCHEMA_VERSION,
            "target_language": language,
            "original": {"chapters": result["original"]["chapters"]},
            "translated": {"chapters": result["translated"]["chapters"]},
        },
    ) if result["original"]["chapters"] else ""
    translated = result["translated"]
    info = SimpleNamespace(
        platform=metadata.get("platform", ""),
        title=result["original"]["title"],
        description=metadata.get("description", ""),
        channel=metadata.get("channel", ""),
        source_id=metadata.get("source_id", ""),
        webpage_url=metadata.get("webpage_url", ""),
        total_secs=metadata.get("total_secs", 0),
        start_time=metadata.get("start_time", ""),
        chapters=result["original"]["chapters"],
    )
    nfo_path = MetadataSaver.write_nfo(
        str(directory), info,
        file_base=f"{base}.{language}",
        source_url=metadata.get("webpage_url", ""),
        title_override=translated.get("title", ""),
        description_override=translated.get("description", ""),
    )
    return {
        "status": "translated",
        "target_language": language,
        "metadata_path": metadata_output,
        "chapters_path": chapter_output,
        "nfo_path": nfo_path,
    }
