"""Qt internationalization and live retranslation support.

Static widget text is translated through the shared ``StreamKeep`` context.
The small runtime walker lets the existing hand-authored Qt UI participate in
translation without requiring generated ``retranslateUi`` methods.  Explicit
``tr``/``tr_n`` calls cover dynamic status and plural messages.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QLocale, QTranslator
from PyQt6.QtWidgets import QDialog, QWidget

_I18N_DIR = Path(__file__).parent
_translator: QTranslator | None = None
_current_lang = "en"

_SOURCE_ATTR = "_streamkeep_i18n_source"
_LAST_ATTR = "_streamkeep_i18n_last"
_TOKEN_RE = re.compile(r"(%n|%\d+|\{[^{}]+\}|https?://\S+|<[^>]+>)")
_PSEUDO_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "áƀçďëƒğħïĵķľɱñöþɋŕšťüṽŵẋÿžÁƁÇĎËƑĞĦÏĴĶĽṀÑÖÞɊŔŠŤÜṼŴẊŸŽ",
)


def available_languages() -> list[str]:
    """Return compiled language codes plus the layout-test pseudo locale."""
    langs = {"en", "qps-ploc"}
    for path in _I18N_DIR.glob("streamkeep_*.qm"):
        code = path.stem.removeprefix("streamkeep_")
        if code:
            langs.add(code)
    return sorted(langs, key=lambda code: (code != "en", code == "qps-ploc", code))


def current_language() -> str:
    """Return the installed language code, or ``en`` for source strings."""
    return _current_lang


def _pseudo_text(source: str) -> str:
    if not source or source.isspace():
        return source
    chunks = _TOKEN_RE.split(source)
    translated: list[str] = []
    for chunk in chunks:
        if not chunk or _TOKEN_RE.fullmatch(chunk):
            translated.append(chunk)
            continue
        expanded = chunk.translate(_PSEUDO_MAP)
        letters = sum(character.isalpha() for character in chunk)
        if letters >= 4:
            expanded += " " + "~" * max(1, letters // 5)
        translated.append(expanded)
    return "⟦" + "".join(translated) + "⟧"


def tr(source: str, *, context: str = "StreamKeep", n: int = -1) -> str:
    """Translate *source* in a named context, with pseudo-locale support."""
    source = str(source or "")
    if _current_lang == "qps-ploc":
        result = _pseudo_text(source)
    elif _current_lang == "en":
        result = source
    else:
        result = QCoreApplication.translate(context, source, None, n) or source
    if n >= 0:
        result = result.replace("%n", str(n))
    return result


def tr_n(source: str, n: int, *, context: str = "StreamKeep") -> str:
    """Translate a numerus source containing ``%n``."""
    return tr(source, context=context, n=n)


def tr_format(source: str, *, context: str = "StreamKeep", n: int = -1, **values) -> str:
    """Translate a named-placeholder source and interpolate it safely."""
    return tr(source, context=context, n=n).format(**values)


def _translated_value(obj, key: str, current: str, *, context: str = "StreamKeep") -> str:
    """Remember source text while allowing application-driven text changes."""
    sources = getattr(obj, _SOURCE_ATTR, {})
    rendered = getattr(obj, _LAST_ATTR, {})
    source = sources.get(key)
    if source is None or (key in rendered and current != rendered[key]):
        source = current
        sources[key] = source
    value = tr(source, context=context)
    rendered[key] = value
    setattr(obj, _SOURCE_ATTR, sources)
    setattr(obj, _LAST_ATTR, rendered)
    return value


def _translate_property(obj, getter_name: str, setter_name: str, key: str) -> None:
    getter = getattr(obj, getter_name, None)
    setter = getattr(obj, setter_name, None)
    if not callable(getter) or not callable(setter):
        return
    current = getter()
    if not isinstance(current, str) or not current:
        return
    setter(_translated_value(obj, key, current))


def _translate_combo(combo) -> None:
    current_sources = getattr(combo, "_streamkeep_i18n_item_sources", None)
    last = getattr(combo, "_streamkeep_i18n_item_last", None)
    should_translate = bool(combo.property("i18nTranslateItems")) or (
        _current_lang == "qps-ploc"
    )
    if not should_translate and current_sources is None:
        # Many legacy workflow combos intentionally use currentText() as their
        # stable value.  Translate only explicitly data-backed combos in real
        # locales; the pseudo locale may still expand every item for layout QA.
        return
    current = [combo.itemText(index) for index in range(combo.count())]
    if current_sources is None or last is None or len(current_sources) != len(current):
        current_sources = list(current)
    else:
        current_sources = [
            shown if shown != last[index] else current_sources[index]
            for index, shown in enumerate(current)
        ]
    translated = [
        tr(source) if should_translate else source for source in current_sources
    ]
    for index, value in enumerate(translated):
        combo.setItemText(index, value)
    combo._streamkeep_i18n_item_sources = current_sources
    combo._streamkeep_i18n_item_last = translated


def _translate_tabs(tabs) -> None:
    sources = getattr(tabs, "_streamkeep_i18n_tab_sources", None)
    last = getattr(tabs, "_streamkeep_i18n_tab_last", None)
    current = [tabs.tabText(index) for index in range(tabs.count())]
    if sources is None or last is None or len(sources) != len(current):
        sources = list(current)
    else:
        sources = [
            shown if shown != last[index] else sources[index]
            for index, shown in enumerate(current)
        ]
    translated = [tr(source) for source in sources]
    for index, value in enumerate(translated):
        tabs.setTabText(index, value)
    tabs._streamkeep_i18n_tab_sources = sources
    tabs._streamkeep_i18n_tab_last = translated


def _translate_table_headers(table) -> None:
    sources = getattr(table, "_streamkeep_i18n_header_sources", None)
    last = getattr(table, "_streamkeep_i18n_header_last", None)
    current = []
    for column in range(table.columnCount()):
        item = table.horizontalHeaderItem(column)
        current.append(item.text() if item is not None else "")
    if sources is None or last is None or len(sources) != len(current):
        sources = list(current)
    else:
        sources = [
            shown if shown != last[index] else sources[index]
            for index, shown in enumerate(current)
        ]
    translated = [tr(source) if source else "" for source in sources]
    for column, value in enumerate(translated):
        item = table.horizontalHeaderItem(column)
        if item is not None:
            item.setText(value)
    table._streamkeep_i18n_header_sources = sources
    table._streamkeep_i18n_header_last = translated


def translate_widget_tree(root) -> None:
    """Translate static text on *root* and all descendant Qt widgets/actions."""
    try:
        from PyQt6.QtGui import QAction
        from PyQt6.QtWidgets import (
            QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit,
            QPlainTextEdit, QTableWidget, QTabWidget, QTextEdit, QWidget,
        )
    except ImportError:
        return
    if not isinstance(root, QWidget):
        return

    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        _translate_property(widget, "windowTitle", "setWindowTitle", "windowTitle")
        _translate_property(widget, "toolTip", "setToolTip", "toolTip")
        _translate_property(widget, "statusTip", "setStatusTip", "statusTip")
        _translate_property(widget, "whatsThis", "setWhatsThis", "whatsThis")
        _translate_property(widget, "accessibleName", "setAccessibleName", "accessibleName")
        _translate_property(
            widget, "accessibleDescription", "setAccessibleDescription", "accessibleDescription"
        )
        if isinstance(widget, (QLabel, QAbstractButton, QGroupBox)):
            _translate_property(widget, "text", "setText", "text")
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            _translate_property(
                widget, "placeholderText", "setPlaceholderText", "placeholderText"
            )
        if isinstance(widget, QComboBox):
            _translate_combo(widget)
        if isinstance(widget, QTabWidget):
            _translate_tabs(widget)
        if isinstance(widget, QTableWidget):
            _translate_table_headers(widget)

    for action in root.findChildren(QAction):
        _translate_property(action, "text", "setText", "text")
        _translate_property(action, "toolTip", "setToolTip", "toolTip")
        _translate_property(action, "statusTip", "setStatusTip", "statusTip")


def find_clipped_text_widgets(root) -> list[str]:
    """Return object paths whose constrained size cannot fit pseudo text.

    The audit intentionally reports only explicitly constrained controls;
    layouts are allowed to grow naturally and wrapping labels are valid.
    """
    from PyQt6.QtWidgets import QAbstractButton, QComboBox, QGroupBox, QLabel, QWidget

    if not isinstance(root, QWidget):
        return []
    clipped: list[str] = []
    for widget in [root, *root.findChildren(QWidget)]:
        if not isinstance(widget, (QLabel, QAbstractButton, QComboBox, QGroupBox)):
            continue
        if isinstance(widget, QLabel) and widget.wordWrap():
            continue
        hint = widget.sizeHint()
        maximum = widget.maximumSize()
        constrained_width = min(widget.width(), maximum.width())
        constrained_height = min(widget.height(), maximum.height())
        width_is_fixed = widget.minimumWidth() == maximum.width()
        height_is_fixed = widget.minimumHeight() == maximum.height()
        if (width_is_fixed and constrained_width < hint.width()) or (
            height_is_fixed and constrained_height < hint.height()
        ):
            name = widget.objectName() or type(widget).__name__
            clipped.append(name)
    return clipped


class TranslatableDialog(QDialog):
    """Dialog base that applies the active catalog immediately before show."""

    def showEvent(self, event):  # noqa: N802 - Qt override
        translate_widget_tree(self)
        super().showEvent(event)


class TranslatableWidget(QWidget):
    """Widget base that translates children added before its first show."""

    def showEvent(self, event):  # noqa: N802 - Qt override
        translate_widget_tree(self)
        super().showEvent(event)


def _retranslate_open_windows(app) -> None:
    if not hasattr(app, "topLevelWidgets"):
        return
    for widget in app.topLevelWidgets():
        translate_widget_tree(widget)


def install_translator(lang: str, app=None) -> bool:
    """Install *lang* and immediately retranslate every open Qt window."""
    global _translator, _current_lang
    app = app or QCoreApplication.instance()
    if app is None:
        return False
    if _translator is not None:
        app.removeTranslator(_translator)
        _translator = None

    lang = str(lang or "en")
    loaded = lang in {"en", "qps-ploc"}
    if not loaded:
        translator = QTranslator(app)
        qm_path = str(_I18N_DIR / f"streamkeep_{lang}.qm")
        loaded = os.path.isfile(qm_path) and translator.load(qm_path)
        if not loaded:
            loaded = translator.load(QLocale(lang), "streamkeep", "_", str(_I18N_DIR))
        if loaded:
            app.installTranslator(translator)
            _translator = translator

    _current_lang = lang if loaded else "en"
    _retranslate_open_windows(app)
    return loaded


__all__ = [
    "available_languages", "current_language", "find_clipped_text_widgets",
    "install_translator", "tr", "tr_format", "tr_n", "TranslatableDialog",
    "TranslatableWidget", "translate_widget_tree",
]
