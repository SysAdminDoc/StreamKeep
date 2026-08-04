"""Static asset loading for the browser companion."""

from pathlib import Path

WEB_UI_PATH = Path(__file__).with_name("web_ui.html")


def load_web_ui() -> str:
    """Read the bundled remote UI template as UTF-8 text."""
    return WEB_UI_PATH.read_text(encoding="utf-8")
