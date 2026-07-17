"""Single token-driven visual system for palette, density, focus, and state.

Palettes are dicts with the same keys. ``build_stylesheet(palette)``
interpolates any palette into the QSS template. ``CAT`` always points
to the active palette; ``STYLESHEET`` is the active QSS string.

Consumer code that reads ``CAT["blue"]`` etc. continues to work — the
dict is mutated in-place when the theme changes via ``apply_theme()``.
"""

CAT_MOCHA = {
    "base": "#081723", "mantle": "#09131f", "crust": "#06101a",
    "surface0": "#132a3a", "surface1": "#1a3547", "surface2": "#244559",
    "overlay0": "#597184", "overlay1": "#73899a",
    "text": "#eef3f6", "subtext0": "#9fb0be", "subtext1": "#c2ced6",
    "lavender": "#b4befe", "blue": "#89b4fa", "sapphire": "#74c7ec",
    "sky": "#89dceb", "teal": "#94e2d5", "green": "#a6e3a1",
    "yellow": "#f9e2af", "peach": "#fab387", "maroon": "#eba0ac",
    "red": "#f38ba8", "mauve": "#cba6f7", "pink": "#f5c2e7",
    "flamingo": "#f2cdcd", "rosewater": "#f5e0dc",
    "panel": "#0b1d2b", "panelHi": "#102536", "panelSoft": "#0a1825",
    "stroke": "#294354", "muted": "#8fa4b4", "accent": "#35c5ef",
    "accentSoft": "#70d7b2", "gold": "#f0c77a",
}

CAT_LATTE = {
    "base": "#eff1f5", "mantle": "#e6e9ef", "crust": "#dce0e8",
    "surface0": "#ccd0da", "surface1": "#bcc0cc", "surface2": "#acb0be",
    "overlay0": "#9ca0b0", "overlay1": "#8c8fa1",
    "text": "#4c4f69", "subtext0": "#6c6f85", "subtext1": "#5c5f77",
    "lavender": "#7287fd", "blue": "#1e66f5", "sapphire": "#209fb5",
    "sky": "#04a5e5", "teal": "#179299", "green": "#40a02b",
    "yellow": "#df8e1d", "peach": "#fe640b", "maroon": "#e64553",
    "red": "#d20f39", "mauve": "#8839ef", "pink": "#ea76cb",
    "flamingo": "#dd7878", "rosewater": "#dc8a78",
    "panel": "#e6e9ef", "panelHi": "#dce0e8", "panelSoft": "#eff1f5",
    "stroke": "#bcc0cc", "muted": "#6c6f85", "accent": "#1e66f5",
    "accentSoft": "#40a02b", "gold": "#df8e1d",
}

# CAT is the "live" palette — mutated in-place so all ``CAT["x"]`` refs
# across the app pick up theme changes without reimporting.
CAT = dict(CAT_MOCHA)

CAT_HIGH_CONTRAST = {
    "base": "#000000", "mantle": "#0a0a0a", "crust": "#000000",
    "surface0": "#1a1a1a", "surface1": "#2a2a2a", "surface2": "#3a3a3a",
    "overlay0": "#8a8a8a", "overlay1": "#aaaaaa",
    "text": "#ffffff", "subtext0": "#cccccc", "subtext1": "#dddddd",
    "lavender": "#8888ff", "blue": "#6699ff", "sapphire": "#55bbff",
    "sky": "#55ddff", "teal": "#55eedd", "green": "#55ff55",
    "yellow": "#ffff55", "peach": "#ffaa55", "maroon": "#ff8888",
    "red": "#ff4444", "mauve": "#bb77ff", "pink": "#ff88cc",
    "flamingo": "#ff9999", "rosewater": "#ffbbbb",
    "panel": "#0a0a0a", "panelHi": "#151515", "panelSoft": "#050505",
    "stroke": "#767676", "muted": "#b8b8b8", "accent": "#6699ff",
    "accentSoft": "#55ff55", "gold": "#ffff55",
}

THEMES = {"dark": CAT_MOCHA, "light": CAT_LATTE, "high_contrast": CAT_HIGH_CONTRAST}

# Layout density presets (F75)
DENSITY_COMPACT = {
    "font_size": 12, "row_height": 48, "padding": 6, "control_h": 32,
    "radius": 7, "scale": 0.82, "thumb_w": 80, "name": "compact",
}
DENSITY_COZY = {
    "font_size": 14, "row_height": 72, "padding": 8, "control_h": 38,
    "radius": 8, "scale": 1.0, "thumb_w": 112, "name": "cozy",
}
DENSITY_SPACIOUS = {
    "font_size": 16, "row_height": 96, "padding": 11, "control_h": 46,
    "radius": 10, "scale": 1.25, "thumb_w": 160, "name": "spacious",
}
DENSITIES = {"compact": DENSITY_COMPACT, "cozy": DENSITY_COZY, "spacious": DENSITY_SPACIOUS}
_active_density = dict(DENSITY_COZY)
_active_theme = "dark"
_active_accent = ""


def get_density():
    """Return the active density preset dict."""
    return dict(_active_density)


def set_density(name, app=None):
    """Set the active density and refresh the application stylesheet."""
    global _active_density
    _active_density = dict(DENSITIES.get(name, DENSITY_COZY))
    if app is not None:
        _rebuild_stylesheet(app)
    return _active_density


def get_visual_state():
    """Return the persisted visual-system choices."""
    return {
        "theme": _active_theme,
        "density": _active_density["name"],
        "accent": _active_accent,
    }


def contrast_ratio(first, second):
    """Return WCAG relative-luminance contrast for two ``#RRGGBB`` colors."""
    def luminance(value):
        channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        channels = [
            channel / 12.92 if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _accent_text(accent):
    black_ratio = contrast_ratio(accent, "#000000")
    white_ratio = contrast_ratio(accent, "#ffffff")
    return "#000000" if black_ratio >= white_ratio else "#ffffff"


def _detect_system_theme():
    """Return 'dark' or 'light' based on OS preference."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return "light" if val == 1 else "dark"
    except Exception:
        return "dark"

def build_stylesheet(p=None):
    """Build StreamKeep's restrained, text-led visual system.

    Containers use spacing and hairline dividers for hierarchy. Borders are
    reserved for editable controls, tables use open rows, and only primary
    actions receive a saturated fill.
    """
    if p is None:
        p = CAT
    density = get_density()
    font_size = density["font_size"]
    padding = density["padding"]
    radius = density["radius"]
    control_height = density["control_h"]
    on_accent = _accent_text(p["accent"])
    return f"""
QMainWindow, QDialog {{
    background-color: {p['base']};
}}
QWidget {{
    color: {p['text']};
    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
    font-size: {font_size}px;
}}
QWidget#chrome, QAbstractScrollArea#chrome,
QAbstractScrollArea#chrome > QWidget#chrome {{
    background-color: transparent;
    border: none;
}}
QFrame#appHeader, QFrame#pageHeader {{
    background-color: transparent;
    border: none;
}}
QFrame#appNav {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {p['stroke']};
}}
QFrame#composerCard {{
    background-color: {p['panel']};
    border: none;
    border-radius: {radius + 2}px;
}}
QFrame#optionsRow, QFrame#workSection, QFrame#fieldBlock,
QFrame#toolbar, QFrame#subtleCard, QFrame#metricCard {{
    background-color: transparent;
    border: none;
    border-radius: 0;
}}
QFrame#card, QFrame#panel, QFrame#heroCard, QFrame#shellCard,
QFrame#shellMetaCard, QFrame#footerBar {{
    background-color: {p['panelSoft']};
    border: none;
    border-radius: {radius + 2}px;
}}
QFrame#statusBar {{
    background-color: transparent;
    border: none;
    border-top: 1px solid {p['stroke']};
}}
QFrame#dialogHero, QFrame#dialogSection, QFrame#dialogStatus,
QFrame#emptyStateCard {{
    background-color: {p['panel']};
    border: none;
    border-radius: {radius + 2}px;
}}
QFrame#dialogStatus[tone="info"] {{ border-left: 3px solid {p['accent']}; }}
QFrame#dialogStatus[tone="success"] {{ border-left: 3px solid {p['green']}; }}
QFrame#dialogStatus[tone="warning"] {{ border-left: 3px solid {p['yellow']}; }}
QFrame#dialogStatus[tone="error"] {{ border-left: 3px solid {p['red']}; }}
QFrame#updateBanner, QFrame#resumeBanner, QFrame#activeRecordings {{
    background-color: {p['panelHi']};
    border: none;
    border-left: 3px solid {p['accent']};
    border-radius: 8px;
}}
QFrame#resumeBanner {{ border-left-color: {p['peach']}; }}
QFrame#activeRecordings {{ border-left-color: {p['green']}; }}
QFrame#playerMetaBar, QFrame#playerSidebar, QFrame#playerTransportBar,
QFrame#playerSlotCard, QFrame#playerPipShell, QFrame#playerPipTitleBar {{
    background-color: {p['panel']};
    border: none;
    border-radius: 10px;
}}
QFrame#playerVideoCanvas {{
    background-color: {p['crust']};
    border: none;
    border-radius: 10px;
}}
QLabel {{
    color: {p['text']};
    background-color: transparent;
    border: none;
}}
QLabel#appBrand {{
    color: {p['text']};
    font-size: 22px;
    font-weight: 750;
}}
QLabel#title {{
    color: {p['text']};
    font-size: 25px;
    font-weight: 750;
}}
QLabel#heroTitle {{
    color: {p['text']};
    font-size: 28px;
    font-weight: 750;
}}
QLabel#heroBody, QLabel#dialogBody {{
    color: {p['subtext0']};
    font-size: 15px;
}}
QLabel#sectionTitle {{
    color: {p['text']};
    font-size: 17px;
    font-weight: 700;
}}
QLabel#sectionBody, QLabel#tableHint, QLabel#fieldHint,
QLabel#subtleText, QLabel#statusBody {{
    color: {p['muted']};
    font-size: 13px;
}}
QLabel#fieldLabel, QLabel#metricLabel {{
    color: {p['subtext0']};
    font-size: 13px;
    font-weight: 650;
}}
QLabel#metricValue, QLabel#shellStatValue {{
    color: {p['text']};
    font-size: 17px;
    font-weight: 700;
}}
QLabel#metricSubvalue, QLabel#shellStatBody, QLabel#shellStatMeta,
QLabel#footerMeta, QLabel#statusLabel {{
    color: {p['muted']};
    font-size: 13px;
}}
QLabel#dialogEyebrow, QLabel#eyebrow {{
    color: {p['accent']};
    font-size: 12px;
    font-weight: 700;
}}
QLabel#dialogTitle {{
    color: {p['text']};
    font-size: 23px;
    font-weight: 750;
}}
QLabel#statusTitle, QLabel#emptyStateTitle {{
    color: {p['text']};
    font-size: 15px;
    font-weight: 700;
}}
QLabel#emptyStateBody {{ color: {p['muted']}; font-size: 13px; }}
QLabel#pillBadge, QLabel#playerBadgeMuted {{
    color: {p['subtext1']};
    background-color: transparent;
    border: none;
    padding: 0;
    font-size: 12px;
    font-weight: 650;
}}
QLabel#streamInfo {{
    color: {p['subtext1']};
    background-color: {p['panelHi']};
    border: none;
    border-left: 3px solid {p['accent']};
    border-radius: 6px;
    padding: 9px 11px;
    font-size: 13px;
}}
QLabel#playerKicker {{ color: {p['accent']}; font-size: 11px; font-weight: 700; }}
QLabel#playerTitle {{ color: {p['text']}; font-size: 18px; font-weight: 700; }}
QLabel#playerMeta, QLabel#playerHint, QLabel#playerTinyLabel {{ color: {p['muted']}; font-size: 12px; }}
QLabel#playerSectionTitle, QLabel#playerMiniTitle {{ color: {p['text']}; font-size: 13px; font-weight: 700; }}
QLabel#playerMiniMeta {{ color: {p['muted']}; font-size: 11px; }}
QLabel#templatePreview {{
    color: {p['green']};
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-size: 12px;
}}
QLabel#templatePreview[tone="error"] {{ color: {p['red']}; }}
QLineEdit, QComboBox, QSpinBox, QTimeEdit, QDateEdit {{
    background-color: {p['base']};
    color: {p['text']};
    border: 1px solid {p['stroke']};
    border-radius: {radius}px;
    padding: {padding}px {padding + 2}px;
    font-size: {font_size}px;
    min-height: {max(18, control_height - (padding * 2) - 2)}px;
    selection-background-color: {p['accent']};
    selection-color: {on_accent};
}}
QLineEdit#globalSearch {{ background-color: {p['panel']}; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover,
QTimeEdit:hover, QDateEdit:hover {{ border-color: {p['overlay0']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QTimeEdit:focus, QDateEdit:focus {{ border-color: {p['accent']}; }}
QPushButton {{
    background-color: {p['surface0']};
    color: {p['text']};
    border: none;
    border-radius: {radius}px;
    padding: {padding}px {padding + 5}px;
    font-size: {font_size}px;
    font-weight: 600;
}}
QPushButton:hover {{ background-color: {p['surface1']}; }}
QPushButton:pressed {{ background-color: {p['surface2']}; }}
QPushButton:focus {{
    border: 2px solid {p['accent']};
}}
QPushButton:disabled {{ background-color: {p['panelSoft']}; color: {p['overlay0']}; }}
QPushButton#primary {{
    background-color: {p['accent']};
    color: {on_accent};
    font-weight: 750;
}}
QPushButton#primary:hover {{ background-color: {p['sky']}; }}
QPushButton#primary:disabled {{
    background-color: {p['surface0']};
    color: {p['overlay0']};
}}
QPushButton#secondary {{ background-color: {p['surface0']}; }}
QPushButton#ghost {{ background-color: transparent; color: {p['subtext1']}; }}
QPushButton#ghost:hover {{ background-color: {p['panelHi']}; color: {p['text']}; }}
QPushButton#toggleAccent {{ background-color: transparent; color: {p['subtext1']}; }}
QPushButton#toggleAccent:checked {{ background-color: {p['surface0']}; color: {p['accent']}; }}
QPushButton#danger {{ background-color: {p['red']}; color: {_accent_text(p['red'])}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background-color: {p['panel']};
    color: {p['text']};
    selection-background-color: {p['surface1']};
    border: 1px solid {p['stroke']};
    padding: 4px;
}}
QTableWidget, QTableView {{
    background-color: transparent;
    alternate-background-color: {p['panelSoft']};
    color: {p['text']};
    border: none;
    gridline-color: transparent;
    selection-background-color: {p['surface0']};
    selection-color: {p['text']};
    font-size: {font_size}px;
}}
QTableWidget:focus, QTableView:focus, QListWidget:focus, QTreeWidget:focus {{
    border: 2px solid {p['accent']};
}}
QTableWidget::item, QTableView::item {{
    padding: {padding}px {padding + 1}px;
    border: none;
    border-bottom: 1px solid {p['stroke']};
}}
QHeaderView::section {{
    background-color: {p['panelSoft']};
    color: {p['muted']};
    border: none;
    border-bottom: 1px solid {p['stroke']};
    padding: {padding}px {padding + 1}px;
    font-size: {max(11, font_size - 1)}px;
    font-weight: 700;
}}
QTableCornerButton::section {{
    background-color: {p['panelSoft']};
    border: none;
    border-bottom: 1px solid {p['stroke']};
}}
QTextEdit, QPlainTextEdit {{
    background-color: transparent;
    color: {p['text']};
    border: none;
    border-radius: 0;
    padding: 4px;
    selection-background-color: {p['surface1']};
}}
QTextEdit#log {{
    color: {p['subtext0']};
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-size: 12px;
}}
QListWidget, QListWidget#globalResults, QListWidget#playerChapterList {{
    background-color: {p['panel']};
    color: {p['text']};
    border: none;
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{ padding: {padding}px {padding + 2}px; border-radius: {radius - 2}px; }}
QListWidget::item:hover, QListWidget::item:selected {{ background-color: {p['surface0']}; }}
QMenu {{
    background-color: {p['panel']};
    color: {p['text']};
    border: 1px solid {p['stroke']};
    padding: 5px;
}}
QMenu::item {{ padding: 7px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background-color: {p['surface0']}; }}
QToolTip {{
    background-color: {p['panelHi']};
    color: {p['text']};
    border: 1px solid {p['stroke']};
    padding: 6px 8px;
}}
QProgressBar {{
    background-color: {p['surface0']};
    border: none;
    border-radius: 4px;
    height: 8px;
    color: transparent;
}}
QProgressBar::chunk {{ background-color: {p['accent']}; border-radius: 4px; }}
QCheckBox:focus, QRadioButton:focus, QSlider:focus {{
    border: 1px solid {p['accent']};
    border-radius: 4px;
}}
QCheckBox, QRadioButton {{ color: {p['text']}; spacing: 7px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 3px;
    border: 1px solid {p['stroke']}; background-color: {p['base']};
}}
QCheckBox::indicator:checked {{ background-color: {p['accent']}; border-color: {p['accent']}; }}
QRadioButton::indicator {{
    width: 16px; height: 16px; border-radius: 8px;
    border: 1px solid {p['stroke']}; background-color: {p['base']};
}}
QRadioButton::indicator:checked {{ background-color: {p['accent']}; border-color: {p['accent']}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['surface2']}; border-radius: 4px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['surface2']}; border-radius: 4px; min-width: 28px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QSplitter::handle {{ background-color: {p['stroke']}; width: 1px; height: 1px; }}
"""


ACCENT_PRESETS = {
    "Mauve": "#cba6f7", "Blue": "#89b4fa", "Green": "#a6e3a1",
    "Peach": "#fab387", "Pink": "#f5c2e7", "Red": "#f38ba8",
    "Teal": "#94e2d5", "Yellow": "#f9e2af",
}


def _normalize_accent(value):
    value = str(value or "").strip().lower()
    if not value:
        return ""
    if len(value) != 7 or value[0] != "#":
        return ""
    try:
        int(value[1:], 16)
    except ValueError:
        return ""
    return value


def _refresh_visual_widgets(app):
    """Repolish open windows, scale table rows, and release clipped widths."""
    if app is None or not hasattr(app, "topLevelWidgets"):
        return
    from PyQt6.QtWidgets import (
        QAbstractButton, QAbstractItemView, QComboBox, QLabel, QWidget,
    )

    scale = float(_active_density["scale"])
    for window in app.topLevelWidgets():
        widgets = [window, *window.findChildren(QWidget)]
        for widget in widgets:
            if isinstance(widget, QAbstractItemView):
                header = getattr(widget, "verticalHeader", lambda: None)()
                base = widget.property("visualBaseRowHeight")
                if header is not None and base:
                    header.setDefaultSectionSize(max(24, round(int(base) * scale)))
            if isinstance(widget, (QAbstractButton, QComboBox, QLabel)):
                maximum = widget.maximumWidth()
                if (
                    widget.minimumWidth() == maximum
                    and maximum < widget.sizeHint().width()
                ):
                    widget.setMaximumWidth(16777215)
            style = widget.style() if hasattr(widget, "style") else None
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)
        window.updateGeometry()
        window.update()


def _rebuild_stylesheet(app=None):
    global STYLESHEET
    STYLESHEET = build_stylesheet(CAT)
    if app is not None:
        app.setStyleSheet(STYLESHEET)
        _refresh_visual_widgets(app)
    return STYLESHEET


def apply_visual_system(theme="dark", density="cozy", accent="", app=None):
    """Apply the complete persisted visual state in one atomic refresh."""
    global _active_density, _active_theme, _active_accent
    theme = str(theme or "dark")
    if theme not in {"dark", "light", "system", "high_contrast"}:
        theme = "dark"
    density = str(density or "cozy")
    _active_density = dict(DENSITIES.get(density, DENSITY_COZY))
    _active_theme = theme
    resolved_theme = _detect_system_theme() if theme == "system" else theme
    CAT.clear()
    CAT.update(THEMES.get(resolved_theme, CAT_MOCHA))
    _active_accent = _normalize_accent(accent)
    if _active_accent:
        CAT["accent"] = _active_accent
        CAT["blue"] = _active_accent
        CAT["lavender"] = _active_accent
    return _rebuild_stylesheet(app)


def apply_accent(hex_color, app=None):
    """Override or clear the accent while preserving theme and density."""
    return apply_visual_system(
        _active_theme, _active_density["name"], hex_color, app=app
    )


def apply_theme(name, app=None):
    """Switch the active theme. Updates CAT in-place and rebuilds STYLESHEET.

    *name*: 'dark', 'light', or 'system'.
    *app*: optional QApplication — if provided, calls ``app.setStyleSheet()``
           for an instant theme switch without restart.
    """
    return apply_visual_system(
        name, _active_density["name"], _active_accent, app=app
    )


# Build initial stylesheet from default (Mocha) palette
STYLESHEET = build_stylesheet(CAT)
