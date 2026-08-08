"""Reusable UI widget helpers — pure functions that build styled Qt widgets.

These were previously methods on the StreamKeep class even though none of
them touched `self`. Moving them to module level makes the main window
smaller, easier to test, and lets future tab-widget splits reuse them
without importing the god object.
"""

from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QTimer, Qt
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSpinBox, QCheckBox,
    QComboBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QTextEdit, QVBoxLayout, QWidget,
)

from ..theme import CAT, get_density
from ..i18n import TranslatableDialog


# Platform badge mapping — key → (CAT colour key, display text).
# Resolved via PLATFORM_BADGES property so colours track theme changes.
_BADGE_MAP = {
    "Kick":       ("green",    "Kick"),
    "Twitch":     ("mauve",    "Twitch"),
    "Rumble":     ("green",    "Rumble"),
    "SoundCloud": ("peach",    "SoundCloud"),
    "Reddit":     ("peach",    "Reddit"),
    "Audius":     ("mauve",    "Audius"),
    "Podcast":    ("yellow",   "Podcast"),
    "Direct":     ("blue",     "Direct"),
    "yt-dlp":     ("overlay1", "yt-dlp"),
}


class _BadgeLookup(dict):
    """Dict-like that rebuilds badge colours from the live CAT dict on
    every access, so theme switches are reflected immediately."""

    def __getitem__(self, key):
        cat_key, text = _BADGE_MAP[key]
        return {"color": CAT[cat_key], "text": text}

    def __contains__(self, key):
        return key in _BADGE_MAP

    def get(self, key, default=None):
        if key in _BADGE_MAP:
            return self[key]
        return default


PLATFORM_BADGES = _BadgeLookup()


def _accessible_text(value):
    """Return concise plain text suitable for an accessible widget name."""
    text = str(value or "").replace("&", "").replace("…", "").strip()
    return " ".join(text.rstrip(":").split())


def _humanize_widget_name(value):
    """Turn a Python/object name into a stable user-facing control name."""
    text = str(value or "").strip("_").replace("_", " ")
    for suffix in (" input", " combo", " spin", " check", " cb", " btn"):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
            break
    words = []
    acronyms = {"url": "URL", "vod": "VOD", "hls": "HLS", "iv": "IV"}
    for word in text.split():
        words.append(acronyms.get(word.lower(), word.capitalize()))
    return " ".join(words)


def set_accessible(widget, name, description=""):
    """Set an explicit accessible name and optional description."""
    clean_name = _accessible_text(name)
    clean_description = _accessible_text(description)
    if clean_name:
        widget.setAccessibleName(clean_name)
    if clean_description:
        widget.setAccessibleDescription(clean_description)
    return widget


def set_accessible_role(widget, role, *, orientation=""):
    """Attach the Qt 6.11 role/orientation contract to a custom widget.

    PyQt6 does not currently expose ``QAccessible::setFactory`` or the
    ``QAccessible::Role`` enum. These stable dynamic properties are consumed
    by the platform accessibility bridge and by the offscreen audit suite;
    native Qt controls still expose their built-in role as usual.
    """
    clean_role = _accessible_text(role).casefold()
    if clean_role:
        widget.setProperty("accessibleRole", clean_role)
        widget.setProperty("qtAccessibleRole", clean_role)
    clean_orientation = _accessible_text(orientation).casefold()
    if clean_orientation:
        widget.setProperty("accessibleOrientation", clean_orientation)
        widget.setProperty("qtAccessibleOrientation", clean_orientation)
    return widget


def set_accessible_switch(widget, name="", description=""):
    """Mark a checkable control as a screen-reader switch."""
    set_accessible(widget, name, description)
    return set_accessible_role(widget, "switch")


def set_accessible_slider(widget, name="", description=""):
    """Mark a slider and retain its real Qt orientation for AT clients."""
    set_accessible(widget, name, description)
    orientation = ""
    if hasattr(widget, "orientation"):
        try:
            orientation = widget.orientation().name
        except AttributeError:
            orientation = str(widget.orientation())
    return set_accessible_role(
        widget, "slider", orientation=orientation or "horizontal",
    )


def bind_label(label, control, *, name="", description=""):
    """Associate a visible label with its keyboard-focusable control."""
    label.setBuddy(control)
    return set_accessible(
        control,
        name or label.text(),
        description or label.toolTip(),
    )


def configure_accessibility(root, *, owner=None, page_name="", names=None):
    """Apply explicit names/descriptions to a widget subtree.

    ``owner`` lets builders reuse their meaningful ``win.<control>`` attribute
    names rather than duplicating labels. ``names`` may override those names by
    attribute name. Native Qt roles and states remain intact.
    """
    names = names or {}
    if page_name:
        set_accessible(root, page_name)

    attributes = {}
    if owner is not None:
        for attr_name, value in vars(owner).items():
            if isinstance(value, QWidget) and (value is root or root.isAncestorOf(value)):
                attributes[id(value)] = attr_name

    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        attr_name = attributes.get(id(widget), "")
        explicit = names.get(attr_name, "")
        if isinstance(explicit, tuple):
            explicit_name, explicit_description = explicit
        else:
            explicit_name, explicit_description = explicit, ""

        candidate = explicit_name or widget.accessibleName()
        if not candidate and isinstance(widget, QAbstractButton):
            candidate = widget.text() or widget.toolTip()
        if not candidate and isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            candidate = widget.placeholderText()
        if not candidate and attr_name:
            candidate = _humanize_widget_name(attr_name)
        if not candidate and widget.objectName():
            candidate = _humanize_widget_name(widget.objectName())

        is_interactive = isinstance(
            widget,
            (
                QAbstractButton,
                QAbstractItemView,
                QComboBox,
                QLineEdit,
                QPlainTextEdit,
                QProgressBar,
                QSlider,
                QAbstractSpinBox,
                QTextEdit,
            ),
        )
        if is_interactive and candidate:
            description = explicit_description or widget.toolTip()
            set_accessible(widget, candidate, description)
            if isinstance(widget, QCheckBox):
                set_accessible_role(widget, "switch")
            elif isinstance(widget, QSlider):
                set_accessible_slider(widget)
            widget.setProperty("accessibilityConfigured", True)

    focusable_types = (
        QAbstractButton,
        QAbstractItemView,
        QComboBox,
        QLineEdit,
        QPlainTextEdit,
        QSlider,
        QAbstractSpinBox,
        QTextEdit,
    )
    for index, widget in enumerate(widgets):
        if not isinstance(widget, QLabel) or widget.buddy() is not None:
            continue
        if widget.objectName() != "fieldLabel":
            continue
        for candidate in widgets[index + 1:index + 7]:
            if isinstance(candidate, focusable_types) and candidate.isEnabled():
                widget.setBuddy(candidate)
                if not candidate.accessibleName():
                    set_accessible(candidate, widget.text(), widget.toolTip())
                break


def update_accessible_status(widget, text, *, tone="info", label="Status"):
    """Expose a changing status as text plus a non-color state description."""
    message = _accessible_text(text) or "No message"
    state = _accessible_text(tone) or "info"
    set_accessible(widget, f"{label}: {message}", f"{state} status update")
    revision = int(widget.property("accessibleStatusRevision") or 0) + 1
    widget.setProperty("accessibleStatusRevision", revision)


def TAB_STYLE():
    """Build the compact archive-workstation rail style from the live theme."""
    density = get_density()
    vertical = density["padding"] + 5
    nav_font_size = max(13, density["font_size"] - 2)
    return f"""
QPushButton#tab {{
    background-color: transparent;
    color: {CAT['subtext1']};
    border: none;
    border-left: 3px solid transparent;
    padding: {vertical}px 14px;
    font-weight: 600;
    font-size: {nav_font_size}px;
    border-radius: 6px;
    text-align: left;
}}
QPushButton#tab:hover {{
    color: {CAT['text']};
    background-color: {CAT['panelHi']};
}}
/* The focus ring was overlay0 on panelHi, which measures 2.91:1 in the light
   palette -- below WCAG 1.4.11's 3:1 for a UI component. accent clears it in all
   three palettes (4.86 light / 5.10 dark / 6.58 high-contrast) and matches how
   focus is drawn everywhere else (V195). */
QPushButton#tab:focus {{
    background-color: {CAT['panelHi']};
    border: 1px solid {CAT['accent']};
    border-left: 3px solid transparent;
}}
QPushButton#tabActive:focus {{
    background-color: {CAT['panelHi']};
    border: 1px solid {CAT['accent']};
    border-left: 3px solid {CAT['accent']};
}}
QPushButton#tabActive {{
    background-color: {CAT['panelHi']};
    color: {CAT['text']};
    border: none;
    border-left: 3px solid {CAT['accent']};
    padding: {vertical}px 14px;
    font-weight: 700;
    font-size: {nav_font_size}px;
    border-radius: 6px;
    text-align: left;
}}
"""


def path_label(path_text, fallback="Choose folder"):
    """Return the basename of a path for display, or `fallback` if empty."""
    path_text = (path_text or "").strip()
    if not path_text:
        return fallback
    try:
        p = Path(path_text)
        if p.name:
            return p.name
    except Exception:
        pass  # safe: best-effort fallback; preserve the primary operation
    return path_text


def make_metric_card(label_text, value_text="--", sub_text=""):
    """Build a single-line metric for quiet operational context."""
    card = QFrame()
    card.setObjectName("metricCard")
    card.setMinimumHeight(34)
    lay = QHBoxLayout(card)
    lay.setContentsMargins(0, 4, 16, 4)
    lay.setSpacing(7)

    label = QLabel(label_text)
    label.setObjectName("metricLabel")
    value = QLabel(value_text)
    value.setObjectName("metricValue")
    value.setWordWrap(False)
    sub = QLabel(sub_text)
    sub.setObjectName("metricSubvalue")
    sub.setWordWrap(False)
    sub.setVisible(bool(sub_text))

    lay.addWidget(label)
    lay.addWidget(value)
    lay.addWidget(sub)
    lay.addStretch(1)
    return card, value, sub


def make_field_block(title, hint=""):
    """Build a dense field group; longer guidance is available as a tooltip."""
    card = QFrame()
    card.setObjectName("fieldBlock")
    card.setMinimumHeight(0)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)

    label = QLabel(title)
    label.setObjectName("fieldLabel")
    if hint:
        label.setToolTip(hint)
    lay.addWidget(label)

    return card, lay


def make_dialog_hero(title, body="", eyebrow="", badge_text=""):
    """Build a premium dialog intro card.

    Returns ``(card, title_label, body_label, badge_label)``.
    """
    card = QFrame()
    card.setObjectName("dialogHero")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(18, 18, 18, 18)
    lay.setSpacing(8)

    top_row = QHBoxLayout()
    top_row.setContentsMargins(0, 0, 0, 0)
    top_row.setSpacing(8)
    eyebrow_label = QLabel(eyebrow or "")
    eyebrow_label.setObjectName("dialogEyebrow")
    eyebrow_label.setVisible(bool(eyebrow))
    top_row.addWidget(eyebrow_label)
    top_row.addStretch(1)
    badge_label = QLabel(badge_text or "")
    badge_label.setObjectName("pillBadge")
    badge_label.setVisible(bool(badge_text))
    top_row.addWidget(badge_label)
    lay.addLayout(top_row)

    title_label = QLabel(title)
    title_label.setObjectName("dialogTitle")
    title_label.setWordWrap(True)
    lay.addWidget(title_label)

    body_label = QLabel(body)
    body_label.setObjectName("dialogBody")
    body_label.setWordWrap(True)
    body_label.setVisible(bool(body))
    lay.addWidget(body_label)

    return card, title_label, body_label, badge_label


def make_dialog_section(title="", body=""):
    """Build a dialog section card. Returns ``(card, content_layout)``."""
    card = QFrame()
    card.setObjectName("dialogSection")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(10)

    if title:
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        lay.addWidget(title_label)
    if body:
        body_label = QLabel(body)
        body_label.setObjectName("sectionBody")
        body_label.setWordWrap(True)
        body_label.setMinimumWidth(0)
        body_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        lay.addWidget(body_label)

    content = QVBoxLayout()
    content.setContentsMargins(0, 0, 0, 0)
    content.setSpacing(10)
    lay.addLayout(content)
    return card, content


def make_status_banner(title="", body="", tone="info"):
    """Build a tone-aware inline status banner.

    Returns ``(card, title_label, body_label)``.
    """
    card = QFrame()
    card.setObjectName("dialogStatus")
    card.setProperty("tone", tone or "info")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(4)

    title_label = QLabel(title)
    title_label.setObjectName("statusTitle")
    title_label.setWordWrap(True)
    body_label = QLabel(body)
    body_label.setObjectName("statusBody")
    body_label.setWordWrap(True)
    body_label.setMinimumWidth(0)
    body_label.setSizePolicy(
        QSizePolicy.Policy.Ignored,
        QSizePolicy.Policy.Preferred,
    )
    body_label.setVisible(bool(body))

    lay.addWidget(title_label)
    lay.addWidget(body_label)
    update_accessible_status(
        card,
        " — ".join(part for part in (title, body) if part),
        tone=tone,
    )
    set_accessible(title_label, f"Status: {title or 'No message'}")
    card.setVisible(bool(title or body))
    return card, title_label, body_label


def update_status_banner(card, title_label, body_label, *, title="", body="", tone="info"):
    """Update a status banner created by ``make_status_banner``."""
    title_label.setText(title)
    body_label.setText(body)
    body_label.setVisible(bool(body))
    card.setProperty("tone", tone or "info")
    style = card.style()
    if style is not None:
        style.unpolish(card)
        style.polish(card)
    update_accessible_status(
        card,
        " — ".join(part for part in (title, body) if part),
        tone=tone,
    )
    set_accessible(title_label, f"Status: {title or 'No message'}")
    set_accessible(body_label, body or title or "No message")
    card.setVisible(bool(title or body))


def make_empty_state(title, body="", *, compact=False):
    """Build a consistent empty-state card.

    Returns ``(card, title_label, body_label)``.
    """
    card = QFrame()
    card.setObjectName("emptyStateCard")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(18, 18, 18, 18)
    lay.setSpacing(6 if compact else 8)

    title_label = QLabel(title)
    title_label.setObjectName("emptyStateTitle")
    title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title_label.setWordWrap(True)
    body_label = QLabel(body)
    body_label.setObjectName("emptyStateBody")
    body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    body_label.setWordWrap(True)

    lay.addStretch(1)
    lay.addWidget(title_label)
    lay.addWidget(body_label)
    lay.addStretch(1)
    return card, title_label, body_label


def _run_premium_dialog(
    parent,
    *,
    title,
    body="",
    eyebrow="",
    badge_text="",
    tone="info",
    summary_title="",
    summary_body="",
    details_title="Details",
    details_body="",
    primary_label="OK",
    secondary_label="",
    default_action="primary",
    min_width=560,
    min_height=0,
    details_monospaced=False,
):
    """Run a premium confirmation/info dialog and return the chosen action."""
    dlg = TranslatableDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(max(420, int(min_width or 420)))
    if min_height:
        dlg.setMinimumHeight(int(min_height))

    root = QVBoxLayout(dlg)
    root.setContentsMargins(18, 18, 18, 18)
    root.setSpacing(12)

    hero, _, _, _ = make_dialog_hero(
        title,
        body,
        eyebrow=eyebrow,
        badge_text=badge_text,
    )
    root.addWidget(hero)

    if summary_title or summary_body:
        banner, banner_title, banner_body = make_status_banner()
        update_status_banner(
            banner,
            banner_title,
            banner_body,
            title=summary_title,
            body=summary_body,
            tone=tone,
        )
        root.addWidget(banner)

    if details_body:
        section, content = make_dialog_section(details_title)
        details_view = QTextEdit()
        if details_monospaced:
            details_view.setObjectName("log")
        details_view.setReadOnly(True)
        details_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        details_view.setPlainText(str(details_body))
        details_view.setMinimumHeight(120)
        content.addWidget(details_view)
        root.addWidget(section, 1)

    choice = {"value": "secondary" if secondary_label else "primary"}

    btn_row = QHBoxLayout()
    btn_row.addStretch(1)

    secondary_btn = None
    if secondary_label:
        secondary_btn = QPushButton(secondary_label)
        secondary_btn.setObjectName("secondary")
        secondary_btn.clicked.connect(lambda: (choice.__setitem__("value", "secondary"), dlg.reject()))
        btn_row.addWidget(secondary_btn)

    primary_btn = QPushButton(primary_label)
    primary_btn.setObjectName("primary")
    primary_btn.clicked.connect(lambda: (choice.__setitem__("value", "primary"), dlg.accept()))
    btn_row.addWidget(primary_btn)
    root.addLayout(btn_row)

    if default_action == "secondary" and secondary_btn is not None:
        secondary_btn.setDefault(True)
        secondary_btn.setAutoDefault(True)
    else:
        primary_btn.setDefault(True)
        primary_btn.setAutoDefault(True)

    dlg.exec()
    return choice["value"]


def ask_premium_confirmation(parent, **kwargs):
    """Show a premium confirmation dialog and return ``True`` on confirm."""
    return _run_premium_dialog(parent, **kwargs) == "primary"


def show_premium_message(parent, **kwargs):
    """Show a premium informational dialog."""
    _run_premium_dialog(parent, **kwargs)


def ask_premium_text_input(
    parent,
    *,
    title,
    body="",
    eyebrow="",
    badge_text="",
    tone="info",
    summary_title="",
    summary_body="",
    field_label="Value",
    field_hint="",
    placeholder="",
    text="",
    primary_label="Save",
    secondary_label="Cancel",
    default_action="primary",
    min_width=560,
    min_height=0,
    validator=None,
    strip_result=True,
):
    """Show a premium text-input dialog and return ``(text, accepted)``."""
    dlg = TranslatableDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(max(420, int(min_width or 420)))
    if min_height:
        dlg.setMinimumHeight(int(min_height))

    root = QVBoxLayout(dlg)
    root.setContentsMargins(18, 18, 18, 18)
    root.setSpacing(12)

    hero, _, _, _ = make_dialog_hero(
        title,
        body,
        eyebrow=eyebrow,
        badge_text=badge_text,
    )
    root.addWidget(hero)

    if summary_title or summary_body:
        banner, banner_title, banner_body = make_status_banner()
        update_status_banner(
            banner,
            banner_title,
            banner_body,
            title=summary_title,
            body=summary_body,
            tone=tone,
        )
        root.addWidget(banner)

    section, content = make_dialog_section(field_label, field_hint)
    input_edit = QLineEdit(str(text or ""))
    input_edit.setClearButtonEnabled(True)
    input_edit.setPlaceholderText(placeholder or "")
    content.addWidget(input_edit)

    error_banner, error_title, error_body = make_status_banner()
    error_banner.setVisible(False)
    content.addWidget(error_banner)
    root.addWidget(section)

    result = {"accepted": False, "value": str(text or "")}

    def _hide_error():
        if error_banner.isVisible():
            update_status_banner(error_banner, error_title, error_body, title="", body="", tone="error")

    def _validate(value):
        if validator is None:
            return True, ""
        try:
            outcome = validator(value)
        except Exception:
            return False, "Validate the entry and try again."
        if isinstance(outcome, tuple):
            ok = bool(outcome[0])
            msg = str(outcome[1] or "") if len(outcome) > 1 else ""
            return ok, msg
        return bool(outcome), ""

    def _accept():
        value = input_edit.text()
        if strip_result:
            value = value.strip()
        ok, msg = _validate(value)
        if not ok:
            update_status_banner(
                error_banner,
                error_title,
                error_body,
                title="Check the value and try again",
                body=msg or "Enter a valid value before continuing.",
                tone="error",
            )
            input_edit.setFocus()
            input_edit.selectAll()
            return
        result["accepted"] = True
        result["value"] = value
        dlg.accept()

    def _reject():
        dlg.reject()

    input_edit.textChanged.connect(lambda _text: _hide_error())

    btn_row = QHBoxLayout()
    btn_row.addStretch(1)

    secondary_btn = QPushButton(secondary_label)
    secondary_btn.setObjectName("secondary")
    secondary_btn.clicked.connect(_reject)
    btn_row.addWidget(secondary_btn)

    primary_btn = QPushButton(primary_label)
    primary_btn.setObjectName("primary")
    primary_btn.clicked.connect(_accept)
    btn_row.addWidget(primary_btn)
    root.addLayout(btn_row)

    input_edit.returnPressed.connect(_accept)
    input_edit.setFocus()
    input_edit.selectAll()

    if default_action == "secondary":
        secondary_btn.setDefault(True)
        secondary_btn.setAutoDefault(True)
    else:
        primary_btn.setDefault(True)
        primary_btn.setAutoDefault(True)

    dlg.exec()
    return result["value"], result["accepted"]


def wrap_scroll_page(page):
    """Wrap a page widget in a QScrollArea with styled chrome."""
    page.setObjectName("chrome")
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    scroll = QScrollArea()
    scroll.setObjectName("chrome")
    scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    # At 200% scaling and the supported minimum window size, dense settings
    # rows can exceed the viewport. Keep every control reachable instead of
    # clipping the right edge.
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.viewport().setObjectName("chrome")
    scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    scroll.setWidget(page)
    reveal_filter = _FocusRevealFilter(scroll)
    scroll._streamkeep_focus_reveal_filter = reveal_filter
    for candidate in [page, *page.findChildren(QWidget)]:
        candidate.installEventFilter(reveal_filter)
    return scroll


class _FocusRevealFilter(QObject):
    """Keep focused controls visible inside a dense scroll page."""

    def __init__(self, scroll):
        super().__init__(scroll)
        self.scroll = scroll
        self._pending_widget = None
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._reveal_pending)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and isinstance(watched, QWidget):
            self._pending_widget = watched
            self._reveal_timer.start(0)
        return False

    def _reveal_pending(self):
        widget = self._pending_widget
        self._pending_widget = None
        self._reveal(widget)

    def _reveal(self, widget):
        try:
            if widget is not None and widget.isVisible():
                self.scroll.ensureWidgetVisible(widget, 24, 24)
        except RuntimeError:
            # The page may have been torn down between FocusIn and the queued
            # geometry pass; that is not an accessibility failure.
            return


def style_table(table, row_height=46, *, accessible_name="", accessible_description=""):
    """Apply shared model/view behavior and density-scaled row metrics."""
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    table.setTabKeyNavigation(True)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setProperty("visualBaseRowHeight", int(row_height))
    scaled_height = round(int(row_height) * float(get_density()["scale"]))
    table.verticalHeader().setDefaultSectionSize(max(24, scaled_height))
    table.horizontalHeader().setHighlightSections(False)
    if accessible_name:
        set_accessible(table, accessible_name, accessible_description)
    table.setProperty("accessibilityConfigured", True)


def set_metric(value_label, sub_label, value, sub=""):
    """Update a metric card's value and subtitle."""
    value_label.setText(value)
    sub_label.setText(sub)
    sub_label.setVisible(bool(sub))


class ToastOverlay(QWidget):
    """A stack of transient in-window messages, anchored bottom-right.

    The OS-level channels (`_notify`, `_fire_native_toast`) deliberately stay
    quiet while the window is focused, so as not to interrupt someone already
    looking at the app. The consequence was that a user watching StreamKeep was
    the user least likely to learn that anything had failed: 32 `except` blocks
    reached only the log, and a menu action that could not proceed simply did
    nothing (V196).

    This is the in-window replacement. Per repo policy it is a toast rather than
    a modal: it never blocks, it never needs dismissing, and the same text is
    also pushed to the notification centre so it remains readable afterwards.
    """

    #: How long each tone stays before fading, in milliseconds. An error is
    #: worth reading twice as long as a success.
    LIFETIME_MS = {
        "error": 9000,
        "warning": 7000,
        "success": 4000,
        "info": 5000,
    }
    #: More than this and the oldest is dropped rather than growing off-screen.
    MAX_VISIBLE = 4
    MARGIN = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toastOverlay")
        # Transparent to the mouse: a toast must never swallow a click meant
        # for the control underneath it.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Never a standalone window: see ``show_toast``.
        if parent is not None:
            self.setWindowFlags(Qt.WindowType.Widget)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self._toasts = []
        set_accessible_role(self, "alert")
        set_accessible(self, "Notifications", "transient status messages")
        if parent is not None:
            parent.installEventFilter(self)
        self.hide()

    # ── geometry ────────────────────────────────────────────────────
    def eventFilter(self, watched, event):
        if watched is self.parent() and event.type() in (
            QEvent.Type.Resize, QEvent.Type.Show,
        ):
            self._reposition()
        return False

    def _reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        hint = self.sizeHint()
        width = min(max(320, hint.width()), max(320, parent.width() - 2 * self.MARGIN))
        height = hint.height()
        self.setGeometry(
            parent.width() - width - self.MARGIN,
            parent.height() - height - self.MARGIN,
            width,
            height,
        )

    # ── api ─────────────────────────────────────────────────────────
    def show_toast(self, text, level="info"):
        """Add one message. Returns the created card, or ``None`` if empty."""
        message = str(text or "").strip()
        if not message:
            return None
        tone = str(level or "info").strip().lower()
        if tone not in self.LIFETIME_MS:
            tone = "info"

        card = QFrame(self)
        card.setObjectName("toast")
        card.setProperty("tone", tone)
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 11, 14, 11)
        row.setSpacing(10)
        glyph = QLabel({
            "success": "✔",
            "warning": "⚠",
            "error": "✖",
            "info": "•",
        }[tone], card)
        glyph.setObjectName("toastGlyph")
        # The glyph repeats the tone the border already carries, so it is
        # decorative -- the text is the message, and colour is never the only
        # signal.
        glyph.setAccessibleName("")
        label = QLabel(message, card)
        label.setObjectName("toastBody")
        label.setWordWrap(True)
        row.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(label, 1)
        update_accessible_status(card, message, tone=tone, label="Notification")

        self._layout.addWidget(card)
        self._toasts.append(card)
        while len(self._toasts) > self.MAX_VISIBLE:
            self._dismiss(self._toasts[0])

        # Parent the timer to the card rather than using ``singleShot``, so
        # destroying the overlay destroys the pending callback with it. A free
        # ``singleShot`` fires after the window is gone and raises on the
        # deleted layout -- a toast must not outlive its window.
        timer = QTimer(card)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._dismiss(card))
        timer.start(self.LIFETIME_MS[tone])
        # An overlay is an anchored child, never a window of its own. Showing a
        # parentless one makes it a top-level widget that Qt keeps in
        # ``topLevelWidgets`` while Python owns the only reference -- destroy the
        # Python side and the next access is an access violation, which is the
        # V177 shape. So it only ever paints inside a parent.
        if self.parentWidget() is not None:
            self.show()
            self.raise_()
            self._reposition()
        return card

    def _dismiss(self, card):
        if card not in self._toasts:
            return
        self._toasts.remove(card)
        try:
            self._layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        except RuntimeError:
            # The overlay's C++ side is already gone (the window closed while a
            # toast was still on screen). Nothing left to detach.
            return
        if not self._toasts:
            self.hide()
        else:
            self._reposition()

    def clear(self):
        for card in list(self._toasts):
            self._dismiss(card)

    def visible_messages(self):
        """Return the text of every toast currently on screen (for tests)."""
        return [
            card.findChild(QLabel, "toastBody").text()
            for card in self._toasts
            if card.findChild(QLabel, "toastBody") is not None
        ]
