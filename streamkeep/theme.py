"""Premium midnight theme — CAT color palette + full Qt stylesheet.

The stylesheet is built once at import time by interpolating the CAT
dict into the QSS template. Both names are re-exported for consumers
that need individual palette values (badge backgrounds, tray icon, etc).
"""

CAT = {
    "base": "#1e1e2e", "mantle": "#181825", "crust": "#11111b",
    "surface0": "#313244", "surface1": "#45475a", "surface2": "#585b70",
    "overlay0": "#6c7086", "overlay1": "#7f849c",
    "text": "#cdd6f4", "subtext0": "#a6adc8", "subtext1": "#bac2de",
    "lavender": "#b4befe", "blue": "#89b4fa", "sapphire": "#74c7ec",
    "sky": "#89dceb", "teal": "#94e2d5", "green": "#a6e3a1",
    "yellow": "#f9e2af", "peach": "#fab387", "maroon": "#eba0ac",
    "red": "#f38ba8", "mauve": "#cba6f7", "pink": "#f5c2e7",
    "flamingo": "#f2cdcd", "rosewater": "#f5e0dc",
    "panel": "#131b2f", "panelHi": "#1a2440", "panelSoft": "#10192b",
    "stroke": "#2b3652", "muted": "#8f9ab8", "accent": "#7dd3fc",
    "accentSoft": "#6ee7b7", "gold": "#f8d38a",
}

STYLESHEET = f"""
QMainWindow {{
    background-color: {CAT['crust']};
}}
QWidget {{
    color: {CAT['text']};
    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
    font-size: 13px;
}}
QWidget#chrome {{
    background-color: {CAT['crust']};
}}
QFrame#heroCard {{
    background-color: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {CAT['panelHi']},
        stop: 0.58 {CAT['panel']},
        stop: 1 {CAT['panelSoft']}
    );
    border: 1px solid {CAT['stroke']};
    border-radius: 22px;
}}
QFrame#card, QFrame#panel, QFrame#footerBar {{
    background-color: {CAT['mantle']};
    border: 1px solid {CAT['stroke']};
    border-radius: 18px;
}}
QFrame#activeRecordings {{
    background-color: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 rgba(166, 227, 161, 35),
        stop: 1 rgba(137, 180, 250, 25)
    );
    border: 1px solid {CAT['green']};
    border-radius: 14px;
}}
QFrame#resumeBanner {{
    background-color: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 rgba(250, 179, 135, 45),
        stop: 1 rgba(137, 180, 250, 45)
    );
    border: 1px solid {CAT['peach']};
    border-radius: 14px;
}}
QLabel#resumeBannerLabel {{
    color: {CAT['text']};
    font-weight: 600;
}}
QFrame#subtleCard, QFrame#metricCard, QFrame#toolbar {{
    background-color: {CAT['panelSoft']};
    border: 1px solid {CAT['stroke']};
    border-radius: 16px;
}}
QLabel {{
    color: {CAT['text']};
    border: none;
}}
QLabel#title {{
    font-size: 28px;
    font-weight: 700;
    color: {CAT['rosewater']};
}}
QLabel#subtitle {{
    font-size: 12px;
    color: {CAT['muted']};
}}
QLabel#eyebrow {{
    color: {CAT['accent']};
    font-size: 11px;
    font-weight: 700;
}}
QLabel#heroTitle {{
    font-size: 24px;
    font-weight: 700;
    color: {CAT['rosewater']};
}}
QLabel#heroBody {{
    font-size: 13px;
    color: {CAT['subtext1']};
}}
QLabel#sectionTitle {{
    font-size: 16px;
    font-weight: 700;
    color: {CAT['rosewater']};
}}
QLabel#sectionBody, QLabel#tableHint, QLabel#fieldHint, QLabel#subtleText {{
    color: {CAT['muted']};
    font-size: 12px;
}}
QLabel#fieldLabel {{
    color: {CAT['subtext1']};
    font-size: 11px;
    font-weight: 700;
}}
QLabel#metricLabel {{
    color: {CAT['muted']};
    font-size: 11px;
    font-weight: 700;
}}
QLabel#metricValue {{
    color: {CAT['rosewater']};
    font-size: 18px;
    font-weight: 700;
}}
QLabel#metricSubvalue {{
    color: {CAT['subtext1']};
    font-size: 12px;
}}
QLabel#statusLabel {{
    color: {CAT['subtext1']};
    font-size: 12px;
}}
QLabel#streamInfo {{
    font-size: 12px;
    color: {CAT['subtext1']};
    padding: 10px 12px;
    background-color: {CAT['panelSoft']};
    border: 1px solid {CAT['stroke']};
    border-radius: 12px;
}}
QLineEdit, QComboBox, QSpinBox {{
    background-color: {CAT['surface0']};
    color: {CAT['text']};
    border: 1px solid {CAT['stroke']};
    border-radius: 12px;
    padding: 10px 12px;
    font-size: 13px;
    selection-background-color: {CAT['surface2']};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
    border-color: {CAT['accent']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {CAT['accent']};
}}
QPushButton {{
    background-color: {CAT['surface0']};
    color: {CAT['text']};
    border: 1px solid {CAT['stroke']};
    border-radius: 12px;
    padding: 10px 16px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: {CAT['surface1']};
    border-color: {CAT['accent']};
}}
QPushButton:pressed {{
    background-color: {CAT['surface2']};
}}
QPushButton:disabled {{
    background-color: {CAT['surface0']};
    color: {CAT['overlay0']};
    border-color: {CAT['surface0']};
}}
QPushButton#primary {{
    background-color: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {CAT['accentSoft']},
        stop: 1 {CAT['green']}
    );
    color: {CAT['crust']};
    border: none;
    padding: 10px 22px;
    font-size: 14px;
}}
QPushButton#primary:hover {{
    background-color: {CAT['teal']};
}}
QPushButton#primary:disabled {{
    background-color: {CAT['surface1']};
    color: {CAT['overlay0']};
}}
QPushButton#secondary {{
    background-color: {CAT['panelSoft']};
    color: {CAT['rosewater']};
}}
QPushButton#ghost {{
    background-color: transparent;
    color: {CAT['subtext1']};
}}
QPushButton#ghost:hover {{
    background-color: {CAT['panelSoft']};
}}
QPushButton#toggleAccent:checked {{
    background-color: {CAT['accent']};
    color: {CAT['crust']};
    border-color: {CAT['accent']};
}}
QPushButton#danger {{
    background-color: {CAT['red']};
    color: {CAT['crust']};
    border: none;
}}
QPushButton#danger:hover {{
    background-color: {CAT['maroon']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {CAT['surface0']};
    color: {CAT['text']};
    selection-background-color: {CAT['surface2']};
    border: 1px solid {CAT['stroke']};
    border-radius: 10px;
}}
QTableWidget {{
    background-color: {CAT['mantle']};
    color: {CAT['text']};
    alternate-background-color: {CAT['panelSoft']};
    border: 1px solid {CAT['stroke']};
    border-radius: 16px;
    gridline-color: transparent;
    selection-background-color: {CAT['panelHi']};
    font-size: 13px;
    padding: 4px;
}}
QTableWidget::item {{
    padding: 10px 12px;
    border-bottom: 1px solid {CAT['stroke']};
}}
QTableWidget::item:selected {{
    background-color: {CAT['panelHi']};
}}
QHeaderView::section {{
    background-color: {CAT['panelSoft']};
    color: {CAT['muted']};
    border: none;
    border-bottom: 1px solid {CAT['stroke']};
    padding: 12px;
    font-weight: 700;
    font-size: 12px;
}}
QTextEdit#log {{
    background-color: {CAT['crust']};
    color: {CAT['subtext0']};
    border: 1px solid {CAT['stroke']};
    border-radius: 16px;
    padding: 10px;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 11px;
}}
QProgressBar {{
    background-color: {CAT['panelSoft']};
    border: none;
    border-radius: 6px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 {CAT['accent']},
        stop: 1 {CAT['green']}
    );
    border-radius: 6px;
}}
QCheckBox {{
    color: {CAT['text']};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {CAT['stroke']};
    background-color: {CAT['surface0']};
}}
QCheckBox::indicator:checked {{
    background-color: {CAT['accentSoft']};
    border-color: {CAT['accentSoft']};
}}
QScrollBar:vertical {{
    background-color: transparent;
    width: 10px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background-color: {CAT['surface2']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {CAT['overlay0']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QSplitter::handle {{
    background-color: {CAT['stroke']};
    height: 1px;
    margin: 6px 0;
}}
"""
