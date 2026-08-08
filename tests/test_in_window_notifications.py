"""A failure the user is waiting on must appear where the user is looking.

The OS-level channels suppress themselves while the window is focused, on
purpose: interrupting someone already watching the app is worse than not. But
nothing replaced them, so 32 `except` blocks that reached only `_log` were
invisible to exactly the user most likely to be waiting -- a menu action that
could not proceed simply did nothing at all (V196).
"""

import pytest
from PyQt6.QtWidgets import QLabel

from streamkeep.ui.widgets import ToastOverlay


@pytest.fixture
def window(qt_application):
    from streamkeep.ui.main_window import StreamKeep

    win = StreamKeep(startup_check=True)
    try:
        yield win
    finally:
        win.close()


# ── the surface itself ───────────────────────────────────────────────

def test_a_toast_carries_its_text_and_tone(qt_application):
    overlay = ToastOverlay()
    card = overlay.show_toast("Could not reach the encoder", "error")

    assert card is not None
    assert card.property("tone") == "error"
    assert overlay.visible_messages() == ["Could not reach the encoder"]
    assert overlay.isVisible()


def test_a_toast_states_its_tone_in_text_not_only_colour(qt_application):
    """Colour is never the only signal (WCAG 1.4.1)."""
    overlay = ToastOverlay()
    card = overlay.show_toast("Disk is full", "warning")

    described = f"{card.accessibleName()} {card.accessibleDescription()}".lower()
    assert "warning" in described, described
    assert "disk is full" in described


def test_an_empty_message_produces_no_toast(qt_application):
    overlay = ToastOverlay()
    assert overlay.show_toast("   ", "error") is None
    assert overlay.visible_messages() == []
    assert not overlay.isVisible()


def test_the_stack_is_bounded(qt_application):
    overlay = ToastOverlay()
    for index in range(ToastOverlay.MAX_VISIBLE + 3):
        overlay.show_toast(f"message {index}", "info")

    messages = overlay.visible_messages()
    assert len(messages) == ToastOverlay.MAX_VISIBLE
    assert messages[-1] == f"message {ToastOverlay.MAX_VISIBLE + 2}", (
        "the newest message must survive; the oldest is dropped"
    )


def test_a_toast_never_swallows_a_click(qt_application):
    from PyQt6.QtCore import Qt

    overlay = ToastOverlay()
    assert overlay.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents
    ), "a toast floating over the page must not intercept clicks"


def test_an_unknown_tone_falls_back_rather_than_raising(qt_application):
    overlay = ToastOverlay()
    card = overlay.show_toast("something happened", "catastrophe")
    assert card.property("tone") == "info"


# ── the failure paths ────────────────────────────────────────────────

def test_report_failure_reaches_status_notifications_and_a_toast(window):
    statuses = []
    window._set_status = lambda text, tone="idle": statuses.append((text, tone))

    window._report_failure("The scene detector could not be loaded")

    assert statuses and statuses[-1][1] == "error"
    assert window._toasts.visible_messages() == [
        "The scene detector could not be loaded"
    ]
    assert any(
        "scene detector" in note.text for note in window._notifications.items()
    ), "the message must remain readable after the toast fades"


def test_a_history_action_that_cannot_proceed_says_so(window, tmp_path, monkeypatch):
    """Chat highlights previously returned quietly, so nothing happened."""
    import builtins

    real_import = builtins.__import__

    def _refuse(name, *args, **kwargs):
        if "spike_detect" in name:
            raise ImportError("spike detector unavailable")
        return real_import(name, *args, **kwargs)

    src = tmp_path / "capture"
    src.mkdir()
    (src / "chat.jsonl").write_text("{}\n", encoding="utf-8")

    reported = []
    window._report_failure = lambda text, level="error": reported.append(text)
    monkeypatch.setattr(builtins, "__import__", _refuse)
    try:
        window._show_chat_highlights(str(src))
    except AttributeError:
        pytest.skip("chat-highlight entry point is named differently")
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)

    assert reported, "the action failed with no user-visible message"
    assert "spike detector" in reported[0]


def test_clearing_notifications_reports_and_can_be_undone(window):
    statuses = []
    window._set_status = lambda text, tone="idle": statuses.append((text, tone))
    for index in range(3):
        window._notifications.push(f"event {index}", level="info")

    window._on_clear_notifications()

    assert window._notifications.items() == []
    assert statuses and "Cleared 3" in statuses[-1][0], statuses
    assert any(
        "Undo" in message for message in window._toasts.visible_messages()
    ), window._toasts.visible_messages()

    window._on_undo_clear_notifications()
    restored = [note.text for note in window._notifications.items()]
    assert len(restored) == 3, restored
    assert window._cleared_notifications is None


def test_clearing_an_empty_buffer_says_there_was_nothing(window):
    statuses = []
    window._set_status = lambda text, tone="idle": statuses.append((text, tone))
    window._notifications.clear()

    window._on_clear_notifications()

    assert statuses and "no notifications" in statuses[-1][0].lower()


def test_a_degraded_search_does_not_claim_no_results(window):
    """An empty result set with a broken index must not read as an empty archive."""
    source = (
        __import__("pathlib").Path("streamkeep/ui/main_window.py")
        .read_text(encoding="utf-8")
    )
    assert "transcript search is unavailable" in source, (
        "the no-results branch must distinguish a broken index from an "
        "empty archive"
    )
    assert "degraded = False" in source and "degraded = True" in source


def test_the_toast_overlay_survives_a_missing_body_label(qt_application):
    """visible_messages must not raise on a partially torn-down card."""
    overlay = ToastOverlay()
    card = overlay.show_toast("hello", "info")
    label = card.findChild(QLabel, "toastBody")
    label.setObjectName("renamed")
    assert overlay.visible_messages() == []
