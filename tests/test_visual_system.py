import hashlib

from PyQt6.QtWidgets import (
    QFrame, QLineEdit, QPushButton, QTableWidget, QVBoxLayout, QWidget,
)

from streamkeep.theme import (
    CAT, apply_visual_system, contrast_ratio, get_visual_state,
)
from streamkeep.ui.widgets import style_table


def _render_component_gallery(qt_application):
    gallery = QWidget()
    gallery.setObjectName("chrome")
    layout = QVBoxLayout(gallery)
    card = QFrame()
    card.setObjectName("composerCard")
    card_layout = QVBoxLayout(card)
    field = QLineEdit()
    field.setPlaceholderText("Source URL")
    primary = QPushButton("Download Selected")
    primary.setObjectName("primary")
    table = QTableWidget(2, 2)
    table.setHorizontalHeaderLabels(["Title", "Status"])
    style_table(table, 46)
    card_layout.addWidget(field)
    card_layout.addWidget(primary)
    card_layout.addWidget(table)
    layout.addWidget(card)
    gallery.resize(520, 360)
    gallery.show()
    qt_application.processEvents()
    image = gallery.grab().toImage()
    payload = image.bits().asstring(image.sizeInBytes())
    result = hashlib.sha256(payload).hexdigest()
    gallery.close()
    return result


def test_visual_state_applies_theme_density_accent_and_contrast(qt_application):
    try:
        apply_visual_system("high_contrast", "spacious", "#f5c2e7", qt_application)
        assert get_visual_state() == {
            "theme": "high_contrast",
            "density": "spacious",
            "accent": "#f5c2e7",
        }
        assert CAT["base"] == "#000000"
        assert CAT["accent"] == "#f5c2e7"
        assert contrast_ratio(CAT["text"], CAT["base"]) >= 7.0
        assert contrast_ratio(CAT["stroke"], CAT["base"]) >= 3.0
        stylesheet = qt_application.styleSheet()
        assert "QTableWidget, QTableView" in stylesheet
        assert "font-size: 16px" in stylesheet
        assert "border-radius: 999px" not in stylesheet
        assert "QFrame#card, QFrame#heroCard" in stylesheet
        assert "background-color: transparent" in stylesheet
    finally:
        apply_visual_system("dark", "cozy", "", qt_application)


def test_secondary_text_meets_wcag_aa_in_every_palette():
    from streamkeep.theme import THEMES

    for name, palette in THEMES.items():
        for surface in ("panel", "base"):
            for token in ("text", "subtext0", "subtext1", "muted"):
                ratio = contrast_ratio(palette[token], palette[surface])
                assert ratio >= 4.5, (
                    f"{name}: {token} on {surface} = {ratio:.2f} (< 4.5:1 WCAG AA)"
                )


def test_density_releases_clipped_fixed_text_and_scales_table_rows(qt_application):
    root = QWidget()
    layout = QVBoxLayout(root)
    action = QPushButton("A deliberately long action label")
    action.setFixedWidth(40)
    table = QTableWidget(1, 1)
    style_table(table, 40)
    layout.addWidget(action)
    layout.addWidget(table)
    root.show()
    qt_application.processEvents()
    try:
        apply_visual_system("dark", "spacious", "", qt_application)
        assert action.maximumWidth() > 40
        assert table.verticalHeader().defaultSectionSize() == 50
    finally:
        root.close()
        apply_visual_system("dark", "cozy", "", qt_application)


def test_offscreen_theme_density_screenshot_matrix(qt_application):
    hashes = {}
    try:
        for theme in ("system", "dark", "light", "high_contrast"):
            for density in ("compact", "cozy", "spacious"):
                apply_visual_system(theme, density, "", qt_application)
                hashes[(theme, density)] = _render_component_gallery(qt_application)
        assert len(set(hashes.values())) >= 9
        for theme in ("dark", "light", "high_contrast"):
            assert len({hashes[(theme, density)] for density in (
                "compact", "cozy", "spacious",
            )}) == 3
    finally:
        apply_visual_system("dark", "cozy", "", qt_application)


def test_cozy_density_uses_readable_type_and_compact_controls(qt_application):
    try:
        apply_visual_system("dark", "cozy", "", qt_application)
        state = get_visual_state()
        stylesheet = qt_application.styleSheet()
        assert state["density"] == "cozy"
        assert "font-size: 16px" in stylesheet
        assert "font-size: 22px" in stylesheet
        assert "border-radius: 6px" in stylesheet
        assert "QFrame#metricCard" in stylesheet
        assert "QFrame#queuePane, QFrame#activityPane, QFrame#dataPane," in stylesheet
        assert "QFrame#settingsNav" in stylesheet
        assert "QPushButton#commandGhost" in stylesheet
    finally:
        apply_visual_system("dark", "cozy", "", qt_application)
