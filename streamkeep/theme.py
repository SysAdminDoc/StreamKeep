"""Single token-driven visual system for palette, density, focus, and state.

Palettes are dicts with the same keys. ``build_stylesheet(palette)``
interpolates any palette into the QSS template. ``CAT`` always points
to the active palette; ``STYLESHEET`` is the active QSS string.

Consumer code that reads ``CAT["blue"]`` etc. continues to work — the
dict is mutated in-place when the theme changes via ``apply_theme()``.
"""

STREAMKEEP_DARK = {
    "base": "#07111f", "mantle": "#050d18", "crust": "#030914",
    "surface0": "#17283d", "surface1": "#213752", "surface2": "#304b6d",
    "overlay0": "#687d96", "overlay1": "#8fa1b7",
    "text": "#f4f7fb", "subtext0": "#9daec2", "subtext1": "#c4cfdd",
    "lavender": "#a6b4ff", "blue": "#5b8cff", "sapphire": "#63b5e6",
    "sky": "#79a8ff", "teal": "#5bd3bf", "green": "#65d6a0",
    "yellow": "#f2b84b", "peach": "#f2a56f", "maroon": "#e8899a",
    "red": "#f07186", "mauve": "#b99af2", "pink": "#e59dce",
    "flamingo": "#efb2b4", "rosewater": "#f1c7c3",
    "panel": "#0e1b2a", "panelHi": "#13243a", "panelSoft": "#0a1624",
    "stroke": "#22364d", "muted": "#8fa0b5", "accent": "#5b8cff",
    "accentSoft": "#65d6a0", "gold": "#f2b84b",
}

STREAMKEEP_LIGHT = {
    "base": "#f4f7fa", "mantle": "#eaf0f5", "crust": "#dce5ed",
    "surface0": "#e1e8ef", "surface1": "#d2dce6", "surface2": "#bfccd9",
    "overlay0": "#8290a1", "overlay1": "#6a788a",
    "text": "#172235", "subtext0": "#4f5e71", "subtext1": "#344358",
    "lavender": "#5d6fe5", "blue": "#2563d9", "sapphire": "#197b9c",
    "sky": "#1685b7", "teal": "#147b72", "green": "#2f7d4b",
    "yellow": "#9a6a08", "peach": "#b65313", "maroon": "#b33f52",
    "red": "#bd2944", "mauve": "#7440b8", "pink": "#ac3a88",
    "flamingo": "#aa5555", "rosewater": "#a95a48",
    "panel": "#ffffff", "panelHi": "#eef3f7", "panelSoft": "#f8fafc",
    "stroke": "#c8d3df", "muted": "#59687a", "accent": "#2563d9",
    "accentSoft": "#2f7d5b", "gold": "#9a6a08",
}

# CAT is the "live" palette — mutated in-place so all ``CAT["x"]`` refs
# across the app pick up theme changes without reimporting.
CAT = dict(STREAMKEEP_DARK)

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

THEMES = {
    "dark": STREAMKEEP_DARK,
    "light": STREAMKEEP_LIGHT,
    "high_contrast": CAT_HIGH_CONTRAST,
}

# Layout density presets (F75)
DENSITY_COMPACT = {
    "font_size": 15, "row_height": 40, "padding": 5, "control_h": 34,
    "radius": 5, "scale": 0.82, "thumb_w": 80, "name": "compact",
}
DENSITY_COZY = {
    "font_size": 16, "row_height": 46, "padding": 6, "control_h": 38,
    "radius": 6, "scale": 0.92, "thumb_w": 104, "name": "cozy",
}
DENSITY_SPACIOUS = {
    "font_size": 17, "row_height": 72, "padding": 10, "control_h": 46,
    "radius": 7, "scale": 1.25, "thumb_w": 144, "name": "spacious",
}
DENSITIES = {"compact": DENSITY_COMPACT, "cozy": DENSITY_COZY, "spacious": DENSITY_SPACIOUS}
_active_density = dict(DENSITY_COZY)
_active_theme = "dark"
_active_accent = ""
_system_accessibility_observers = {}


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


def readable_on(foreground, background, minimum=4.5):
    """Darken or lighten *foreground* until it is legible on *background*.

    Returned unchanged when it already clears *minimum*. This is computed
    rather than hand-picked because the accent is user-supplied — ``apply_accent``
    accepts any hex — so a palette value chosen to pass with the default accent
    says nothing about the one the user actually set.
    """
    if contrast_ratio(foreground, background) >= minimum:
        return foreground
    # Move away from the background: toward black on a light surface, toward
    # white on a dark one. Whichever end is reachable wins.
    target = (
        "#000000"
        if contrast_ratio(background, "#000000")
        >= contrast_ratio(background, "#ffffff")
        else "#ffffff"
    )
    start = [int(foreground[index:index + 2], 16) for index in (1, 3, 5)]
    end = [int(target[index:index + 2], 16) for index in (1, 3, 5)]
    for step in range(1, 21):
        blend = step / 20
        mixed = "#%02x%02x%02x" % tuple(
            round(start[i] + (end[i] - start[i]) * blend) for i in range(3)
        )
        if contrast_ratio(mixed, background) >= minimum:
            return mixed
    return target


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


def _system_prefers_high_contrast(app):
    """Read Qt 6.10+'s OS contrast hint when the runtime exposes it."""
    try:
        hints = app.styleHints().accessibility()
        preference = hints.contrastPreference()
        return getattr(preference, "name", "") == "HighContrast"
    except (AttributeError, RuntimeError, TypeError):
        return False


def _resolve_system_theme(app=None):
    if app is not None and _system_prefers_high_contrast(app):
        return "high_contrast"
    return _detect_system_theme()


def _apply_system_accessibility_theme(app):
    """Apply a changed OS contrast preference only in System mode."""
    if app is None or _active_theme != "system":
        return False
    resolved_theme = _resolve_system_theme(app)
    next_palette = THEMES.get(resolved_theme, STREAMKEEP_DARK)
    if CAT == next_palette:
        return False
    CAT.clear()
    CAT.update(next_palette)
    _rebuild_stylesheet(app)
    return True


def _bind_system_accessibility_observer(app):
    """Listen for live contrast changes without affecting explicit themes."""
    if app is None:
        return
    key = id(app)
    if key in _system_accessibility_observers:
        return
    try:
        hints = app.styleHints().accessibility()
        if hints is None:
            return
    except (AttributeError, RuntimeError):
        return

    def on_contrast_changed(*_args):
        _apply_system_accessibility_theme(app)

    try:
        hints.contrastPreferenceChanged.connect(on_contrast_changed)
    except (AttributeError, RuntimeError):
        return
    # Retain the Python callback and QObject for the application's lifetime;
    # Qt owns the signal source and disconnects it on application teardown.
    _system_accessibility_observers[key] = (hints, on_contrast_changed)

def build_stylesheet(p=None):
    """Build StreamKeep's card-led local archive control-room visual system."""
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
    font-family: 'Segoe UI', 'Arial', sans-serif;
    font-size: {font_size}px;
}}
QWidget#chrome, QAbstractScrollArea#chrome,
QAbstractScrollArea#chrome > QWidget#chrome {{
    background-color: transparent;
    border: none;
}}
QFrame#appHeader {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {p['stroke']};
}}
QFrame#navRail {{
    background-color: {p['mantle']};
    border: none;
    border-right: 1px solid {p['stroke']};
}}
QFrame#brandMark {{
    background-color: {p['panelHi']};
    border: 1px solid {p['accent']};
    border-radius: 11px;
}}
QFrame#navRule {{
    background-color: {p['stroke']};
    border: none;
    min-height: 1px;
    max-height: 1px;
}}
QFrame#shellContent {{
    background-color: {p['base']};
    border: none;
}}
QFrame#pageHeader {{
    background-color: transparent;
    border: none;
}}
QFrame#appNav {{
    background-color: transparent;
    border: none;
}}
QFrame#composerCard {{
    background-color: {p['panel']};
    border: 1px solid {p['stroke']};
    border-top: 2px solid {p['accent']};
    border-radius: 12px;
}}
QFrame#sourceField {{
    background-color: {p['panelSoft']};
    border: 1px solid {p['stroke']};
    border-radius: 9px;
}}
QFrame#paneToolbar {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {p['stroke']};
}}
QFrame#paneFooter {{
    background-color: transparent;
    border: none;
    border-top: 1px solid {p['stroke']};
}}
QFrame#queueName, QFrame#queueStatus, QFrame#queueProgress {{
    background-color: transparent;
    border: none;
}}
QFrame#optionsRow {{
    background-color: transparent;
    border: none;
    border-radius: 0;
}}
QFrame#subtleCard {{
    background-color: {p['panelSoft']};
    border: 1px solid {p['stroke']};
    border-radius: 10px;
}}
QFrame#toolbar {{
    background-color: {p['panel']};
    border: 1px solid {p['stroke']};
    border-radius: 10px;
}}
QFrame#fieldBlock {{
    background-color: {p['panel']};
    border: 1px solid {p['stroke']};
    border-radius: 10px;
}}
QFrame#composerCard QFrame#fieldBlock {{
    background-color: transparent;
    border: none;
    border-radius: 0;
}}
QFrame#metricCard {{
    background-color: {p['panel']};
    border: 1px solid {p['stroke']};
    border-top: 2px solid {p['accent']};
    border-radius: 11px;
}}
QFrame#metricCard[tone="success"] {{ border-top-color: {p['green']}; }}
QFrame#metricCard[tone="warning"] {{ border-top-color: {p['yellow']}; }}
QFrame#metricCard[tone="danger"] {{ border-top-color: {p['red']}; }}
QFrame#fieldBlock QFrame#metricCard {{
    background-color: {p['panelSoft']};
}}
QFrame#queuePane, QFrame#activityPane, QFrame#dataPane,
QFrame#analyticsPanel, QFrame#archiveHealthPane {{
    background-color: {p['panel']};
    border: 1px solid {p['stroke']};
    border-radius: 12px;
}}
QFrame#healthRow {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {p['stroke']};
}}
QFrame#settingsNav {{
    background-color: {p['panel']};
    border: 1px solid {p['stroke']};
    border-radius: 10px;
}}
QFrame#heroCard, QFrame#settingsBody {{
    background-color: transparent;
    border: none;
    border-radius: 0;
}}
QFrame#card {{
    background-color: {p['panel']};
    border: 1px solid {p['stroke']};
    border-radius: 11px;
}}
QFrame#panel, QFrame#shellCard, QFrame#shellMetaCard, QFrame#footerBar {{
    background-color: {p['panelSoft']};
    border: none;
    border-radius: {radius}px;
}}
QFrame#statusBar {{
    background-color: {p['mantle']};
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
/* In-window transient notifications (V196). Raised above the page on
   ``panelHi`` with a stroke, because a toast floats over arbitrary content and
   needs its own edge rather than borrowing the page background. The tone is
   carried by a left bar *and* by the message text, never by colour alone. */
QFrame#toast {{
    background-color: {p['panelHi']};
    border: 1px solid {p['stroke']};
    border-left: 3px solid {p['accent']};
    border-radius: 10px;
}}
QFrame#toast[tone="success"] {{ border-left-color: {p['green']}; }}
QFrame#toast[tone="warning"] {{ border-left-color: {p['yellow']}; }}
QFrame#toast[tone="error"] {{ border-left-color: {p['red']}; }}
QLabel#toastBody {{
    color: {p['text']};
    background-color: transparent;
}}
QLabel#toastGlyph {{
    color: {p['accent']};
    background-color: transparent;
    font-size: {font_size + 1}px;
}}
QFrame#toast[tone="success"] QLabel#toastGlyph {{ color: {p['green']}; }}
QFrame#toast[tone="warning"] QLabel#toastGlyph {{ color: {p['yellow']}; }}
QFrame#toast[tone="error"] QLabel#toastGlyph {{ color: {p['red']}; }}
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
    font-size: 20px;
    font-weight: 750;
}}
QLabel#brandCaption, QLabel#navFootnote {{
    color: {p['muted']};
    font-size: 11px;
    font-weight: 650;
}}
QLabel#shellPageTitle {{
    color: {p['text']};
    font-size: 29px;
    font-weight: 750;
}}
QLabel#shellPageBody {{
    color: {p['muted']};
    font-size: 14px;
}}
QLabel#title {{
    color: {p['text']};
    font-size: 28px;
    font-weight: 750;
}}
QLabel#heroTitle {{
    color: {p['text']};
    font-size: 22px;
    font-weight: 750;
}}
QLabel#composerTitle {{
    color: {p['text']};
    font-size: 15px;
    font-weight: 600;
}}
QLabel#heroBody, QLabel#dialogBody {{
    color: {p['subtext0']};
    font-size: 15px;
}}
QLabel#sectionTitle {{
    color: {p['text']};
    font-size: 16px;
    font-weight: 700;
}}
QLabel#sectionBody, QLabel#tableHint, QLabel#fieldHint,
QLabel#subtleText, QLabel#statusBody {{
    color: {p['muted']};
    font-size: 15px;
}}
QLabel#fieldLabel, QLabel#metricLabel {{
    color: {p['subtext0']};
    font-size: 12px;
    font-weight: 650;
}}
QLabel#metricValue, QLabel#shellStatValue {{
    color: {p['text']};
    font-size: 25px;
    font-weight: 750;
}}
QLabel#metricSubvalue, QLabel#shellStatBody, QLabel#shellStatMeta,
QLabel#footerMeta, QLabel#statusLabel {{
    color: {p['muted']};
    font-size: 12px;
}}
QLabel#toolbarMeta {{ color: {p['subtext0']}; font-size: 14px; }}
QLabel#queueTitle {{ color: {p['text']}; font-size: 13px; font-weight: 600; }}
QLabel#queueMeta {{ color: {p['muted']}; font-size: 12px; }}
QLabel#queueStatusText, QLabel#queueProgressText {{
    color: {p['subtext1']};
    font-size: 13px;
}}
QLabel#sourceLinkIcon {{
    color: {p['subtext1']};
    border: none;
    border-right: 1px solid {p['stroke']};
    font-size: 20px;
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
QLabel#healthState {{ color: {p['green']}; font-size: 14px; font-weight: 700; }}
QLabel#healthTitle {{ color: {p['text']}; font-size: 14px; font-weight: 650; }}
QLabel#healthDetail {{ color: {p['muted']}; font-size: 12px; }}
QLabel#pillBadge, QLabel#playerBadgeMuted {{
    color: {p['subtext1']};
    background-color: transparent;
    border: none;
    padding: 0;
    font-size: 12px;
    font-weight: 600;
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
    background-color: {p['panelSoft']};
    color: {p['text']};
    border: 1px solid {p['stroke']};
    border-radius: {radius}px;
    padding: {padding}px {padding + 2}px;
    font-size: {font_size}px;
    min-height: {max(18, control_height - (padding * 2) - 2)}px;
    selection-background-color: {p['accent']};
    selection-color: {on_accent};
}}
QLineEdit#globalSearch {{
    background-color: {p['panel']};
    font-size: 14px;
    border-radius: 20px;
}}
QLineEdit#sourceComposer {{
    background-color: transparent;
    border: none;
    border-radius: 0;
    min-height: 30px;
    font-size: 14px;
}}
/* The composer sits inside its own framed card, so it deliberately has no
   border of its own -- but suppressing :focus too left the single most
   important control in the app with no visible focus state (WCAG 2.4.7, V195).
   A bottom rule reads as an underline inside the card rather than a competing
   box, and is distinct from :hover. */
QLineEdit#sourceComposer:hover {{
    border: none;
    border-bottom: 1px solid {p['overlay0']};
}}
QLineEdit#sourceComposer:focus {{
    border: none;
    border-bottom: 2px solid {p['accent']};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover,
QTimeEdit:hover, QDateEdit:hover {{ border-color: {p['overlay0']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QTimeEdit:focus, QDateEdit:focus {{ border-color: {p['accent']}; }}
QPushButton {{
    background-color: {p['surface0']};
    color: {p['text']};
    border: 1px solid {p['stroke']};
    border-radius: {radius + 2}px;
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
    border-color: {p['accent']};
    font-weight: 700;
}}
QPushButton#primary:hover {{ background-color: {p['sky']}; }}
QPushButton#primary:disabled {{
    background-color: {p['surface0']};
    color: {p['overlay0']};
}}
QPushButton#secondary {{ background-color: {p['panelHi']}; border-color: {p['stroke']}; }}
QPushButton#ghost {{ background-color: transparent; color: {p['subtext1']}; }}
QPushButton#ghost:hover {{ background-color: {p['panelHi']}; color: {p['text']}; }}
QPushButton#commandGhost {{
    background-color: transparent;
    color: {p['subtext1']};
    border: none;
    border-radius: 0;
    padding-left: 2px;
    padding-right: 12px;
}}
QPushButton#commandGhost:hover {{ color: {p['text']}; background-color: transparent; }}
QPushButton#headerIcon {{
    background-color: transparent;
    color: {p['subtext1']};
    border: none;
    border-radius: 5px;
    padding: 0;
    font-size: 20px;
    font-weight: 500;
}}
QPushButton#headerIcon:hover {{ background-color: {p['panelHi']}; color: {p['text']}; }}
QPushButton#systemStatus {{
    background-color: {p['panel']};
    color: {p['subtext1']};
    border: 1px solid {p['stroke']};
    border-radius: 18px;
    padding: 7px 11px;
    font-size: 13px;
    font-weight: 650;
}}
QPushButton#systemStatus:hover {{
    background-color: {p['panelHi']};
    border-color: {p['overlay0']};
}}
QPushButton#toolbarAction {{
    background-color: transparent;
    color: {p['subtext1']};
    border: none;
    border-radius: 3px;
    padding: 5px 8px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton#toolbarAction:hover {{ background-color: {p['panelHi']}; color: {p['text']}; }}
QPushButton#toolbarEmphasis {{
    background-color: {p['surface0']};
    color: {p['text']};
    border: none;
    border-radius: 3px;
    padding: 5px 10px;
    font-size: 13px;
    font-weight: 550;
}}
QPushButton#footerAction {{
    background-color: transparent;
    color: {p['accent']};
    border: none;
    padding: 2px 4px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton#toggleAccent {{ background-color: transparent; color: {p['subtext1']}; }}
QPushButton#toggleAccent:checked {{ background-color: {p['surface0']}; color: {readable_on(p['accent'], p['surface0'])}; }}
QPushButton#danger {{ background-color: {p['red']}; color: {_accent_text(p['red'])}; }}
QPushButton#danger:disabled {{
    background-color: {p['panelSoft']};
    color: {p['overlay0']};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background-color: {p['panel']};
    color: {p['text']};
    selection-background-color: {p['surface1']};
    border: 1px solid {p['stroke']};
    padding: 4px;
}}
QTableWidget, QTableView, QTreeWidget {{
    background-color: transparent;
    alternate-background-color: {p['panelSoft']};
    color: {p['text']};
    border: none;
    gridline-color: transparent;
    selection-background-color: {p['surface0']};
    selection-color: {p['text']};
    font-size: {font_size}px;
}}
QTableWidget#downloadQueue {{
    background-color: transparent;
    gridline-color: {p['stroke']};
    font-size: 13px;
}}
QTableWidget#downloadQueue::item {{
    padding: 0 7px;
    border: none;
    border-bottom: 1px solid {p['stroke']};
}}
QTableWidget#downloadQueue QHeaderView::section {{
    background-color: {p['panel']};
    color: {p['subtext0']};
    font-size: 13px;
    font-weight: 600;
    padding: 11px 7px;
}}
QTableWidget:focus, QTableView:focus, QListWidget:focus, QTreeWidget:focus {{
    border: 2px solid {p['accent']};
}}
QTableWidget::item, QTableView::item, QTreeWidget::item {{
    padding: {padding}px {padding + 1}px;
    border: none;
    border-bottom: 1px solid {p['stroke']};
}}
QHeaderView::section {{
    background-color: {p['panelHi']};
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
    font-family: 'Segoe UI', 'Arial', sans-serif;
    font-size: 14px;
    padding: 12px 14px;
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
QListWidget::item:hover {{ background-color: {p['surface0']}; }}
/* Selection is what the keyboard moves, so it needs to be distinguishable
   from a mouse hover -- both were surface0, which made the focused row in the
   global search results invisible (WCAG 2.4.7, V195). */
QListWidget::item:selected {{
    background-color: {p['surface1']};
    border-left: 2px solid {p['accent']};
}}
QListWidget:focus::item:selected {{ background-color: {p['surface2']}; }}
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
QProgressBar#queueProgressBar {{ height: 8px; min-height: 8px; max-height: 8px; }}
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
            refresh_theme = getattr(widget, "refresh_theme", None)
            if callable(refresh_theme):
                refresh_theme()
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
    if theme == "system":
        _bind_system_accessibility_observer(app)
    resolved_theme = _resolve_system_theme(app) if theme == "system" else theme
    CAT.clear()
    CAT.update(THEMES.get(resolved_theme, STREAMKEEP_DARK))
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
