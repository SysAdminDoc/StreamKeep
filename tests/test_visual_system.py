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


def test_every_rendered_rule_block_meets_wcag_aa_in_every_palette():
    """Checks the stylesheet StreamKeep actually renders, not a hand-kept list
    of tokens. A fixed token list cannot see a pairing introduced by a new
    rule — which is how a *selected* state, the one place the label most needs
    to be readable, shipped at 4.40:1 in the light palette."""
    import re

    from streamkeep.theme import THEMES, build_stylesheet

    colour = re.compile(r"(?<![-\w])color\s*:\s*(#[0-9a-fA-F]{6})\s*;")
    background = re.compile(r"background-color\s*:\s*(#[0-9a-fA-F]{6})\s*;")
    failures = []
    for name, palette in THEMES.items():
        sheet = build_stylesheet(palette)
        for block in re.finditer(r"([^{}]*)\{([^{}]*)\}", sheet):
            selector, body = block.group(1).strip(), block.group(2)
            # A disabled control is deliberately low-contrast.
            if ":disabled" in selector:
                continue
            fg, bg = colour.search(body), background.search(body)
            if not fg or not bg:
                continue
            ratio = contrast_ratio(fg.group(1), bg.group(1))
            if ratio < 4.5:
                failures.append(
                    f"{name}: {selector} -> {fg.group(1)} on {bg.group(1)} "
                    f"= {ratio:.2f}"
                )
    assert not failures, "rule blocks below WCAG AA:\n" + "\n".join(failures)


def test_a_custom_accent_stays_readable_when_selected():
    """The accent is user-supplied, so a palette value that happens to pass
    with the default says nothing about the one the operator actually set."""
    from streamkeep.theme import THEMES, readable_on

    for name, palette in THEMES.items():
        for accent in ("#f5c2e7", "#ffff00", "#00ff00", "#2563d9", "#111111"):
            resolved = readable_on(accent, palette["surface0"])
            ratio = contrast_ratio(resolved, palette["surface0"])
            assert ratio >= 4.5, (
                f"{name}: custom accent {accent} -> {resolved} = {ratio:.2f}"
            )


def test_readable_on_leaves_an_already_legible_colour_alone():
    from streamkeep.theme import readable_on

    assert readable_on("#000000", "#ffffff") == "#000000"
    assert readable_on("#ffffff", "#000000") == "#ffffff"


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
