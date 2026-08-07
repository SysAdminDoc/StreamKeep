"""Static asset loading and rendering for the browser companion.

Owns the remote UI template and the language selection and interpolation
that turns it into a served page (V163). ``load_web_ui`` was already here;
the rendering that consumed it was 100 lines at the bottom of ``_legacy``.
"""

import json
from html import escape as _html_escape

from ..i18n import available_languages, translate_catalog

from pathlib import Path

WEB_UI_PATH = Path(__file__).with_name("web_ui.html")


def load_web_ui() -> str:
    """Read the bundled remote UI template as UTF-8 text."""
    return WEB_UI_PATH.read_text(encoding="utf-8")


def _web_text(source: str) -> str:
    """Mark a web-remote source string for the shared translation catalog."""
    return source

_WEB_UI_TEXT = {
    "app_title": _web_text("StreamKeep Remote"),
    "auth_instructions": _web_text(
        "Generate a one-time pairing code in StreamKeep Settings, then enter it here."
    ),
    "pairing_code": _web_text("One-time pairing code"),
    "pair_and_connect": _web_text("Pair and connect"),
    "remote_navigation": _web_text("Remote navigation"),
    "status": _web_text("Status"),
    "add_url": _web_text("Add URL"),
    "library": _web_text("Library"),
    "channels": _web_text("Channels"),
    "active_downloads": _web_text("Active Downloads"),
    "active_workers": _web_text("Active Workers"),
    "queue": _web_text("Queue"),
    "resumable": _web_text("Resumable"),
    "failures": _web_text("Failures"),
    "loading": _web_text("Loading..."),
    "add_to_queue": _web_text("Add to Queue"),
    "url_placeholder": _web_text("Paste a stream or VOD URL..."),
    "add": _web_text("Add"),
    "monitored_channels": _web_text("Monitored Channels"),
    "session_rejected": _web_text("StreamKeep rejected this session."),
    "request_failed": _web_text("Request failed ({status})"),
    "pairing_failed": _web_text("Pairing failed"),
    "pairing_hint": _web_text(
        "Pairing failed. Generate a fresh code in StreamKeep Settings."
    ),
    "added_to_queue": _web_text("Added to queue!"),
    "failed_prefix": _web_text("Failed: "),
    "unknown": _web_text("unknown"),
    "no_active_downloads": _web_text("No active downloads."),
    "download": _web_text("Download"),
    "queue_empty": _web_text("Queue empty."),
    "queued": _web_text("queued"),
    "no_active_workers": _web_text("No active workers."),
    "worker_lower": _web_text("worker"),
    "worker": _web_text("Worker"),
    "running": _web_text("(running)"),
    "no_resumable_downloads": _web_text("No resumable downloads."),
    "segments_remaining": _web_text("{count} segments remaining"),
    "no_failures": _web_text("No failures requiring action."),
    "failed": _web_text("failed"),
    "failed_job": _web_text("Failed job"),
    "retry_count": _web_text("retry {count}"),
    "next_attempt": _web_text("next {value}"),
    "resume_available": _web_text("resume available"),
    "retry": _web_text("Retry"),
    "cancel_auto_retry": _web_text("Cancel auto retry"),
    "discard": _web_text("Discard"),
    "no_recordings": _web_text("No recordings yet."),
    "untitled": _web_text("Untitled"),
    "no_channels": _web_text("No channels monitored."),
    "offline": _web_text("offline"),
    "live": _web_text("live"),
}

def _normalize_web_language(value: str, supported: set[str]) -> str | None:
    candidate = str(value or "").strip().lower().replace("_", "-")
    if not candidate or candidate == "*":
        return None
    if candidate in supported:
        return candidate
    base = candidate.split("-", 1)[0]
    return base if base in supported else None

def _select_web_language(explicit: str = "", accept_language: str = "") -> str:
    """Choose a catalog language from an explicit query setting or header."""
    supported = set(available_languages())
    selected = _normalize_web_language(explicit, supported)
    if selected:
        return selected

    choices: list[tuple[float, int, str]] = []
    for order, item in enumerate(str(accept_language or "").split(",")):
        parts = [part.strip() for part in item.split(";")]
        language = _normalize_web_language(parts[0], supported)
        if not language:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            name, _, raw_value = parameter.partition("=")
            if name.strip().lower() != "q":
                continue
            try:
                quality = float(raw_value.strip())
            except ValueError:
                quality = 0.0
        if quality > 0:
            choices.append((quality, -order, language))
    if choices:
        return max(choices)[2]
    return "en"

def _render_web_ui(language: str) -> str:
    values = {
        key: translate_catalog(source, language, context="WebRemote")
        for key, source in _WEB_UI_TEXT.items()
    }
    rendered = _WEB_UI_HTML.replace(
        "{{web_i18n}}",
        json.dumps(values, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026"),
    )
    rendered = rendered.replace("{{lang}}", _html_escape(language, quote=True))
    for key, value in values.items():
        rendered = rendered.replace(
            "{{t:" + key + "}}",
            _html_escape(value, quote=True),
        )
    return rendered

_WEB_UI_HTML = load_web_ui()
