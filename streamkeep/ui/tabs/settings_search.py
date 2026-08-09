"""Search and visibility indexing for the large Settings surface."""

from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)


_CONTROL_TYPES = (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
)
_DESCRIPTOR_NAMES = frozenset({"fieldLabel", "sectionTitle", "heroTitle"})
_SUFFIXES = (
    "_check", "_combo", "_edit", "_input", "_spin", "_btn",
    "_table", "_list", "_text", "_browser",
)


def _normalise(value):
    return " ".join(
        str(value or "").replace("_", " ").replace("-", " ").casefold().split()
    )


def _widget_text(widget):
    values = []
    for method_name in ("text", "placeholderText", "accessibleName", "toolTip"):
        method = getattr(widget, method_name, None)
        if callable(method):
            try:
                value = method()
            except RuntimeError:
                value = ""
            if value:
                values.append(str(value))
    if isinstance(widget, QComboBox):
        values.extend(
            widget.itemText(index) for index in range(widget.count())
        )
    object_name = widget.objectName()
    if object_name:
        values.append(object_name)
    for property_name in (
        "settingKey", "setting_key", "configKey", "config_key",
    ):
        value = widget.property(property_name)
        if value:
            values.append(str(value))
    return tuple(value for value in values if value)


def _base_attribute_name(name):
    value = str(name or "")
    for suffix in _SUFFIXES:
        if value.endswith(suffix):
            return value[:-len(suffix)]
    return value


def _config_key_matches(attribute_name, config):
    """Return config keys that are plausibly represented by an attribute."""
    base = _base_attribute_name(attribute_name)
    base_tokens = set(base.split("_")) - {""}
    matches = []
    for raw_key in (config or {}):
        key = str(raw_key)
        if key in (attribute_name, base):
            matches.append(key)
            continue
        key_tokens = set(key.split("_")) - {""}
        shared = base_tokens & key_tokens
        if base and (base in key or key in base):
            matches.append(key)
        elif len(base_tokens) >= 2 and len(shared) >= 2:
            matches.append(key)
        elif len(base_tokens) == 1 and base_tokens == key_tokens:
            matches.append(key)
    return matches


class SettingsSearchController:
    """Index Settings controls and filter them without destroying state."""

    def __init__(self, root, sections, *, owner=None):
        self.root = root
        self._owner = owner
        self._original_visibility = {
            id(widget): not widget.isHidden()
            for widget in [root, *root.findChildren(QWidget)]
        }
        self._sections = []
        self._attribute_names = {
            id(value): name
            for name, value in vars(owner or {}).items()
            if isinstance(value, QWidget)
        }
        for name, section in sections:
            if section is None:
                continue
            self._sections.append(self._index_section(str(name), section))
        self._visible_names = tuple(
            record["name"] for record in self._sections
        )

    @property
    def section_names(self):
        return tuple(record["name"] for record in self._sections)

    @property
    def visible_section_names(self):
        return self._visible_names

    def _index_section(self, name, section):
        widgets = [section, *section.findChildren(QWidget)]
        controls = [
            widget for widget in widgets
            if isinstance(widget, _CONTROL_TYPES)
        ]
        descriptor_widgets = [
            widget for widget in section.children()
            if isinstance(widget, QLabel)
            and widget.objectName() in _DESCRIPTOR_NAMES
        ]
        if not descriptor_widgets:
            descriptor_widgets = [
                widget for widget in section.children()
                if isinstance(widget, QLabel)
            ][:1]
        descriptor_terms = _normalise(
            f"{name} {self._terms(descriptor_widgets)}"
        )
        for control in controls:
            self._set_setting_keys(control)
        control_terms = {
            id(control): self._terms((control, *self._sibling_labels(control)))
            for control in controls
        }
        return {
            "name": name,
            "section": section,
            "widgets": widgets,
            "controls": controls,
            "descriptor_terms": descriptor_terms,
            "control_terms": control_terms,
        }

    def _terms(self, widgets):
        values = []
        for widget in widgets:
            values.extend(_widget_text(widget))
        return _normalise(" ".join(values))

    def _sibling_labels(self, control):
        parent = control.parentWidget()
        if parent is None:
            return ()
        children = parent.children()
        try:
            control_index = children.index(control)
        except ValueError:
            return ()
        for child in reversed(children[:control_index]):
            if isinstance(child, _CONTROL_TYPES):
                break
            if isinstance(child, QLabel):
                return (child,)
        return ()

    def _set_setting_keys(self, control):
        attribute_name = self._attribute_names.get(id(control), "")
        if not attribute_name:
            return
        keys = _config_key_matches(
            attribute_name,
            getattr(self._owner, "_config", {}) if self._owner else {},
        )
        values = [attribute_name, _base_attribute_name(attribute_name), *keys]
        control.setProperty("settingKey", " ".join(dict.fromkeys(values)))

    def _restore(self, widget):
        original = self._original_visibility.get(id(widget), True)
        widget.setVisible(original)

    @staticmethod
    def _matches(query, terms):
        return not query or query in terms

    def filter(self, value):
        """Apply a case-insensitive search and return visible section names."""
        query = _normalise(value)
        visible_names = []
        for record in self._sections:
            section = record["section"]
            controls = record["controls"]
            descriptor_match = self._matches(query, record["descriptor_terms"])
            control_matches = {
                id(control): self._matches(
                    query, record["control_terms"].get(id(control), "")
                )
                for control in controls
            }
            section_visible = not query or descriptor_match or any(
                control_matches.values()
            )
            self._restore(section)
            section.setVisible(
                self._original_visibility.get(id(section), True)
                and section_visible
            )
            if section_visible:
                visible_names.append(record["name"])
            if not query or descriptor_match:
                for widget in record["widgets"]:
                    if widget is not section:
                        self._restore(widget)
                continue
            matched_controls = {
                id(control) for control in controls if control_matches[id(control)]
            }
            for widget in record["widgets"]:
                if widget is section:
                    continue
                if isinstance(widget, _CONTROL_TYPES):
                    widget.setVisible(
                        self._original_visibility.get(id(widget), True)
                        and id(widget) in matched_controls
                    )
                elif isinstance(widget, QLabel):
                    label_match = self._matches(query, self._terms((widget,)))
                    sibling_match = any(
                        id(control) in matched_controls
                        for control in controls
                        if control.parentWidget() is widget.parentWidget()
                    )
                    widget.setVisible(
                        self._original_visibility.get(id(widget), True)
                        and (label_match or sibling_match)
                    )
                elif isinstance(widget, QWidget):
                    descendant_match = any(
                        id(control) in matched_controls
                        for control in controls
                        if widget is control or widget.isAncestorOf(control)
                    )
                    widget.setVisible(
                        self._original_visibility.get(id(widget), True)
                        and descendant_match
                    )
        self._visible_names = tuple(visible_names)
        return self._visible_names
