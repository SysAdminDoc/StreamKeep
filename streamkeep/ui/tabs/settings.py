"""Settings tab — the biggest tab (510+ lines of field blocks) plus the
``SettingsTabMixin`` handler class that is mixed into ``StreamKeep``.

Groups: default output, toolchain probe, cookies, network + rate limit +
bandwidth schedule + parallel connections, YouTube extras, templates,
webhook, dedup, media library, post-processing + converter, manual
converter buttons, import/export/save row.
"""

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHeaderView, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ... import VERSION
from ...capabilities import get_runtime_capabilities
from ...extractors import Extractor
from ...extractors.twitch import TwitchExtractor
from ...extractors.ytdlp import YtDlpExtractor, ytdlp_runtime_status
from ...http import set_native_proxy
from ...i18n import available_languages
from ...paths import CONFIG_FILE
from ...postprocess import (
    AUDIO_CODECS, AUDIO_CONTAINERS, PostProcessor,
    VIDEO_CONTAINERS,
    available_video_codec_keys,
)
from ...theme import ACCENT_PRESETS
from ...utils import (
    DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE,
    default_output_dir as _default_output_dir,
    flatpak_archive_guidance,
    render_template as _render_template,
)
from ..widgets import (
    make_dialog_section,
    make_field_block,
    make_metric_card,
    make_status_banner,
    set_accessible,
    style_table,
)
from .settings_companion import SettingsCompanionMixin
from . import settings_presets as _settings_presets
from .settings_preferences import SettingsPreferencesMixin
from .settings_tools import SettingsToolsMixin


# Keep the former module-level preset imports working for extensions and
# downstream integrations while the implementation has a dedicated owner.
BUILTIN_PRESETS = _settings_presets.BUILTIN_PRESETS
_get_user_presets = _settings_presets._get_user_presets
_save_user_presets = _settings_presets._save_user_presets
_pp_snapshot = _settings_presets._pp_snapshot
_pp_apply_snapshot = _settings_presets._pp_apply_snapshot
_on_pp_preset_delete = _settings_presets._on_pp_preset_delete
_on_pp_preset_save = _settings_presets._on_pp_preset_save
_on_pp_preset_selected = _settings_presets._on_pp_preset_selected
_populate_pp_presets = _settings_presets._populate_pp_presets


def _update_webhook_indicator(win, url):
    """Show auto-detected webhook type below the URL input."""
    url = (url or "").strip()
    lbl = getattr(win, "_webhook_type_label", None)
    if not lbl:
        return
    if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
        lbl.setText("\u2714 Discord webhook detected")
    elif "hooks.slack.com" in url:
        lbl.setText("\u2714 Slack incoming webhook detected")
    elif "api.telegram.org/bot" in url:
        if "chat_id=" in url:
            lbl.setText("\u2714 Telegram bot detected (chat_id found)")
        else:
            lbl.setText("\u26A0 Telegram \u2014 add ?chat_id=YOUR_ID to the URL")
    elif "ntfy.sh" in url or "/ntfy/" in url:
        lbl.setText("\u2714 ntfy push notification detected")
    elif url:
        lbl.setText("Generic JSON POST endpoint")
    else:
        lbl.setText("")
class SettingsTabMixin(
    SettingsToolsMixin, SettingsPreferencesMixin, SettingsCompanionMixin
):
    """Settings-tab handler methods, mixed into ``StreamKeep``."""


    # ── Settings helpers ─────────────────────────────────────────────


    # ── Browser companion ────────────────────────────────────────────



def build_settings_tab(win):
    """Build the Settings tab page. Stashes widget refs on `win.*`."""
    page = QWidget()
    page.setProperty("responsiveLayout", True)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    current_theme = str(win._config.get("theme", "dark") or "dark")
    current_density = str(win._config.get("visual_density", "cozy") or "cozy")
    current_accent = str(win._config.get("visual_accent", "") or "")
    theme_display = {
        "dark": "Dark", "light": "Light", "system": "System",
        "high_contrast": "High Contrast",
    }.get(current_theme, "Dark")

    # ── Hero ────────────────────────────────────────────────────────
    hero = QFrame()
    hero.setObjectName("heroCard")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(2, 2, 2, 4)
    hero_lay.setSpacing(4)

    hero_copy = QVBoxLayout()
    hero_copy.setSpacing(4)
    kicker = QLabel("Settings")
    kicker.setObjectName("eyebrow")
    kicker.setVisible(False)
    title = QLabel("Settings")
    title.setObjectName("heroTitle")
    title.setWordWrap(True)
    body = QLabel(
        "Appearance, downloads, privacy, and integrations."
    )
    body.setObjectName("heroBody")
    body.setWordWrap(True)
    body.setVisible(False)
    hero_copy.addWidget(kicker)
    hero_copy.addWidget(title)
    hero_copy.addWidget(body)
    hero_lay.addLayout(hero_copy)

    settings_meta = QLabel(
        f"StreamKeep v{VERSION}\n"
        f"Config file: {CONFIG_FILE}\n"
        f"Supported platforms: {', '.join(Extractor.all_names())}"
    )
    settings_meta.setObjectName("sectionBody")
    settings_meta.setWordWrap(True)
    settings_meta.setVisible(False)
    hero_lay.addWidget(settings_meta)

    settings_metrics = QHBoxLayout()
    settings_metrics.setSpacing(18)
    theme_card, win.settings_theme_value, win.settings_theme_sub = make_metric_card(
        "Appearance", theme_display, current_density.title()
    )
    config_card, _, _ = make_metric_card(
        "Config",
        CONFIG_FILE.name,
        "Local preferences",
    )
    secrets_card, _, _ = make_metric_card(
        "Secrets",
        "Protected",
        "OS credential store",
    )
    settings_metrics.addWidget(theme_card)
    settings_metrics.addWidget(config_card, 1)
    settings_metrics.addWidget(secrets_card)
    config_card.setToolTip(str(CONFIG_FILE))
    hero_lay.addLayout(settings_metrics)
    lay.addWidget(hero)

    settings_nav = QFrame()
    settings_nav.setObjectName("settingsNav")
    settings_nav_lay = QHBoxLayout(settings_nav)
    settings_nav_lay.setContentsMargins(0, 0, 0, 5)
    settings_nav_lay.setSpacing(3)
    settings_nav_buttons = []
    for label in (
        "General", "Access", "Downloads", "Companion",
        "Automation", "Library", "Processing",
    ):
        button = QPushButton(label)
        button.setObjectName("commandGhost")
        settings_nav_lay.addWidget(button)
        settings_nav_buttons.append(button)
    settings_nav_lay.addStretch(1)
    win.settings_nav = settings_nav
    win.settings_nav_buttons = settings_nav_buttons
    lay.addWidget(settings_nav)

    # Persistent scheduled health conditions (V96)
    health_block, health_lay = make_field_block(
        "System health",
        "Scheduled probes keep runtime, credentials, archives, extractors, and disk pressure visible.",
    )
    health_toolbar = QHBoxLayout()
    health_toolbar.setContentsMargins(0, 0, 0, 0)
    health_toolbar.setSpacing(8)
    win.health_status_label = QLabel("Status: Healthy · 0 active condition(s)")
    win.health_status_label.setObjectName("subtleText")
    health_toolbar.addWidget(win.health_status_label)
    health_toolbar.addStretch(1)
    win.health_updated_label = QLabel("Last checked: Never")
    win.health_updated_label.setObjectName("subtleText")
    health_toolbar.addWidget(win.health_updated_label)
    win.health_run_btn = QPushButton("Run health check")
    win.health_run_btn.setObjectName("commandGhost")
    win.health_run_btn.setToolTip("Run all bounded health probes now")
    set_accessible(
        win.health_run_btn,
        "Run health check",
        "Run the aggregate system health probes now.",
    )
    win.health_run_btn.clicked.connect(win._start_health_check)
    health_toolbar.addWidget(win.health_run_btn)
    health_lay.addLayout(health_toolbar)

    win.health_table = QTableWidget(0, 4)
    win.health_table.setHorizontalHeaderLabels(
        ["Condition", "Severity", "Details", "Repair"]
    )
    win.health_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    win.health_table.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows
    )
    win.health_table.setSelectionMode(
        QAbstractItemView.SelectionMode.SingleSelection
    )
    win.health_table.horizontalHeader().setSectionResizeMode(
        0, QHeaderView.ResizeMode.ResizeToContents
    )
    win.health_table.horizontalHeader().setSectionResizeMode(
        1, QHeaderView.ResizeMode.ResizeToContents
    )
    win.health_table.horizontalHeader().setSectionResizeMode(
        2, QHeaderView.ResizeMode.Stretch
    )
    win.health_table.horizontalHeader().setSectionResizeMode(
        3, QHeaderView.ResizeMode.ResizeToContents
    )
    win.health_table.setMinimumHeight(88)
    win.health_table.setMaximumHeight(260)
    style_table(
        win.health_table,
        row_height=40,
        accessible_name="Persistent system health conditions",
        accessible_description="Severity-ranked conditions with repair actions.",
    )
    health_lay.addWidget(win.health_table)
    lay.addWidget(health_block)

    # ── Card body ───────────────────────────────────────────────────
    card = QFrame()
    card.setObjectName("card")
    card_lay = QVBoxLayout(card)
    card_lay.setContentsMargins(4, 8, 4, 4)
    card_lay.setSpacing(8)

    # Theme selector (F20)
    theme_bar = QFrame()
    theme_bar.setObjectName("toolbar")
    theme_lay = QVBoxLayout(theme_bar)
    theme_lay.setContentsMargins(0, 4, 0, 6)
    theme_lay.setSpacing(6)
    theme_row = QHBoxLayout()
    theme_row.setContentsMargins(0, 0, 0, 0)
    theme_row.setSpacing(10)
    theme_copy = QVBoxLayout()
    theme_copy.setSpacing(2)
    theme_title = QLabel("Appearance")
    theme_title.setObjectName("fieldLabel")
    theme_hint = QLabel("Applied immediately.")
    theme_hint.setObjectName("subtleText")
    theme_hint.setWordWrap(True)
    theme_copy.addWidget(theme_title)
    theme_copy.addWidget(theme_hint)
    theme_row.addLayout(theme_copy, 1)
    theme_lay.addLayout(theme_row)
    controls_grid = QGridLayout()
    controls_grid.setContentsMargins(0, 0, 0, 0)
    controls_grid.setHorizontalSpacing(10)
    controls_grid.setVerticalSpacing(4)
    theme_select_label = QLabel("Theme")
    theme_select_label.setObjectName("fieldLabel")
    controls_grid.addWidget(theme_select_label, 0, 0)
    win.theme_combo = QComboBox()
    win.theme_combo.setProperty("i18nTranslateItems", True)
    win.theme_combo.addItem("Dark", "dark")
    win.theme_combo.addItem("Light", "light")
    win.theme_combo.addItem("System", "system")
    win.theme_combo.addItem("High Contrast", "high_contrast")
    idx = max(0, win.theme_combo.findData(current_theme))
    win.theme_combo.setCurrentIndex(idx)
    win.theme_combo.currentIndexChanged.connect(win._on_theme_changed)
    win.theme_combo.setMinimumWidth(160)
    controls_grid.addWidget(win.theme_combo, 1, 0)
    density_label = QLabel("Density")
    density_label.setObjectName("fieldLabel")
    controls_grid.addWidget(density_label, 0, 1)
    win.density_combo = QComboBox()
    win.density_combo.setProperty("i18nTranslateItems", True)
    for label, value in (
        ("Compact", "compact"), ("Cozy", "cozy"), ("Spacious", "spacious"),
    ):
        win.density_combo.addItem(label, value)
    density_idx = max(0, win.density_combo.findData(current_density))
    win.density_combo.setCurrentIndex(density_idx)
    win.density_combo.currentIndexChanged.connect(win._on_visual_settings_changed)
    controls_grid.addWidget(win.density_combo, 1, 1)
    accent_label = QLabel("Accent")
    accent_label.setObjectName("fieldLabel")
    controls_grid.addWidget(accent_label, 0, 2)
    win.accent_combo = QComboBox()
    win.accent_combo.setProperty("i18nTranslateItems", True)
    win.accent_combo.addItem("Theme default", "")
    for label, value in ACCENT_PRESETS.items():
        win.accent_combo.addItem(label, value.lower())
    accent_idx = max(0, win.accent_combo.findData(current_accent.lower()))
    win.accent_combo.setCurrentIndex(accent_idx)
    win.accent_combo.currentIndexChanged.connect(win._on_visual_settings_changed)
    controls_grid.addWidget(win.accent_combo, 1, 2)
    language_label = QLabel("Language")
    language_label.setObjectName("fieldLabel")
    controls_grid.addWidget(language_label, 0, 3)
    win.language_combo = QComboBox()
    win.language_combo.setProperty("i18nTranslateItems", True)
    language_labels = {
        "en": "English",
        "es": "Español",
        "qps-ploc": "Pseudo (layout test)",
    }
    for lang in available_languages():
        win.language_combo.addItem(language_labels.get(lang, lang), lang)
    lang_idx = max(0, win.language_combo.findData(win._config.get("language", "en")))
    win.language_combo.setCurrentIndex(lang_idx)
    win.language_combo.currentIndexChanged.connect(win._on_language_changed)
    win.language_combo.setMinimumWidth(140)
    controls_grid.addWidget(win.language_combo, 1, 3)
    for column in range(4):
        controls_grid.setColumnStretch(column, 1)
    theme_lay.addLayout(controls_grid)
    card_lay.addWidget(theme_bar)

    # Keep the output path and runtime health readable at the supported
    # minimum width instead of forcing a horizontal settings scrollbar.
    sections_top = QVBoxLayout()
    sections_top.setSpacing(12)

    general_block, general_lay = make_field_block(
        "Default Output", "New downloads will default to this folder."
    )
    output_row = QHBoxLayout()
    output_row.setSpacing(8)
    win.settings_output = QLineEdit(str(_default_output_dir()))
    win.settings_output.setClearButtonEnabled(True)
    output_row.addWidget(win.settings_output, 1)
    browse = QPushButton("Browse")
    browse.setObjectName("secondary")
    browse.clicked.connect(lambda: win._settings_browse(win.settings_output))
    output_row.addWidget(browse)
    general_lay.addLayout(output_row)
    output_hint = QLabel(flatpak_archive_guidance())
    output_hint.setObjectName("fieldHint")
    output_hint.setWordWrap(True)
    general_lay.addWidget(output_hint)
    sections_top.addWidget(general_block, 1)

    tools_block, tools_lay = make_field_block(
        "Local Toolchain",
        "StreamKeep relies on these binaries for robust downloads.",
    )
    registry = get_runtime_capabilities(refresh=True, config=win._config)
    win._runtime_registry_snapshot = registry
    ffmpeg = registry["ffmpeg"]
    curl = registry["curl"]
    pillow = registry["pillow"]
    sqlite = registry.get("sqlite", {
        "state": "unknown",
        "version": "Not found",
        "detail": "SQLite runtime status is unavailable.",
    })
    from ...javascript_runtime import get_managed_deno_info
    try:
        deno = get_managed_deno_info()
    except Exception as error:
        deno = {
            "available": False,
            "version": "",
            "detail": f"Managed Deno status unavailable: {error}",
        }
    yt_status = ytdlp_runtime_status()
    win._ytdlp_status_snapshot = yt_status
    ff_card, _, _ = make_metric_card(
        "FFmpeg",
        ffmpeg["state"].title(),
        str(ffmpeg.get("version") or "Not found"),
    )
    yt_card, _, _ = make_metric_card(
        "yt-dlp",
        yt_status.get("summary", "Missing"),
        str(yt_status.get("yt_dlp_version") or "Not found"),
    )
    curl_card, _, _ = make_metric_card(
        "curl",
        curl["state"].title(),
        str(curl.get("version") or "Not found"),
    )
    pillow_card, _, _ = make_metric_card(
        "Pillow",
        pillow["state"].title(),
        str(pillow.get("version") or "Not found"),
    )
    sqlite_card, _, _ = make_metric_card(
        "SQLite",
        str(sqlite.get("state") or "unknown").title(),
        str(sqlite.get("version") or "Not found"),
    )
    deno_card, _, _ = make_metric_card(
        "Managed Deno",
        "Ready" if deno.get("available") else "Missing",
        str(deno.get("version") or "Not found"),
    )
    for runtime_card, detail in (
        (ff_card, ffmpeg.get("detail", "")),
        (yt_card, yt_status.get("detail", "")),
        (curl_card, curl.get("detail", "")),
        (pillow_card, pillow.get("detail", "")),
        (sqlite_card, sqlite.get("detail", "")),
        (deno_card, deno.get("detail", "")),
    ):
        runtime_card.setToolTip(str(detail or ""))

    tools_metrics = QHBoxLayout()
    tools_metrics.setSpacing(10)
    tools_metrics.addWidget(ff_card)
    tools_metrics.addWidget(yt_card)
    tools_metrics.addWidget(curl_card)
    tools_metrics.addWidget(pillow_card)
    tools_metrics.addWidget(sqlite_card)
    tools_metrics.addWidget(deno_card)
    tools_lay.addLayout(tools_metrics)
    sections_top.addWidget(tools_block, 1)

    deno_block, deno_lay = make_field_block(
        "YouTube JavaScript runtime",
        "Install the pinned Deno runtime only when you request it. "
        "A local ZIP provides an offline installation path; PATH remains the default.",
    )
    deno_preference_row = QHBoxLayout()
    deno_preference_row.setSpacing(8)
    deno_preference_label = QLabel("Preference:")
    deno_preference_label.setFixedWidth(100)
    deno_preference_row.addWidget(deno_preference_label)
    win.deno_preference_combo = QComboBox()
    win.deno_preference_combo.addItem("Prefer PATH runtime", userData="path")
    win.deno_preference_combo.addItem("Prefer managed Deno", userData="managed")
    saved_deno_preference = str(
        win._config.get("javascript_runtime_preference", "path") or "path"
    )
    saved_deno_idx = win.deno_preference_combo.findData(
        "managed" if saved_deno_preference == "managed" else "path"
    )
    win.deno_preference_combo.setCurrentIndex(max(0, saved_deno_idx))
    win.deno_preference_combo.currentIndexChanged.connect(
        win._on_javascript_runtime_preference_changed
    )
    deno_preference_row.addWidget(win.deno_preference_combo, 1)
    deno_lay.addLayout(deno_preference_row)

    deno_actions = QHBoxLayout()
    deno_actions.setSpacing(8)
    win.deno_install_btn = QPushButton("Install pinned Deno")
    win.deno_install_btn.setObjectName("secondary")
    win.deno_install_btn.clicked.connect(win._on_install_deno_clicked)
    deno_actions.addWidget(win.deno_install_btn)
    win.deno_archive_btn = QPushButton("Install from ZIP")
    win.deno_archive_btn.setObjectName("secondary")
    win.deno_archive_btn.clicked.connect(win._on_install_deno_archive_clicked)
    deno_actions.addWidget(win.deno_archive_btn)
    win.deno_remove_btn = QPushButton("Remove managed Deno")
    win.deno_remove_btn.setObjectName("secondary")
    win.deno_remove_btn.setEnabled(False)
    win.deno_remove_btn.clicked.connect(win._on_remove_deno_clicked)
    deno_actions.addWidget(win.deno_remove_btn)
    deno_actions.addStretch(1)
    deno_lay.addLayout(deno_actions)
    win.deno_runtime_status = QLabel("")
    win.deno_runtime_status.setObjectName("subtleText")
    win.deno_runtime_status.setWordWrap(True)
    deno_lay.addWidget(win.deno_runtime_status)
    sections_top.addWidget(deno_block, 1)
    win._refresh_deno_runtime_controls()

    plugin_block, plugin_lay = make_field_block(
        "Plugin trust",
        "Review a plugin's declared permissions and compatibility before it can run.",
    )
    plugin_hint = QLabel(
        "A contract change, including a new permission, requires a fresh review. "
        "Plugins are never enabled from this panel without your explicit approval."
    )
    plugin_hint.setObjectName("subtleText")
    plugin_hint.setWordWrap(True)
    plugin_lay.addWidget(plugin_hint)
    win.plugin_trust_table = QTableWidget(0, 6)
    win.plugin_trust_table.setHorizontalHeaderLabels([
        "Plugin", "Permissions", "Dependencies", "Compatibility",
        "Entry points", "State",
    ])
    win.plugin_trust_table.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows
    )
    win.plugin_trust_table.setSelectionMode(
        QAbstractItemView.SelectionMode.SingleSelection
    )
    win.plugin_trust_table.setEditTriggers(
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    win.plugin_trust_table.setMinimumHeight(150)
    style_table(
        win.plugin_trust_table,
        row_height=42,
        accessible_name="Plugin trust review",
        accessible_description=(
            "Declared plugin permissions, dependencies, compatibility, and entry points"
        ),
    )
    plugin_header = win.plugin_trust_table.horizontalHeader()
    plugin_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    plugin_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    plugin_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    plugin_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    plugin_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
    plugin_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
    win.plugin_trust_table.itemSelectionChanged.connect(
        win._on_plugin_selection_changed
    )
    plugin_lay.addWidget(win.plugin_trust_table)

    plugin_actions = QHBoxLayout()
    plugin_actions.setSpacing(8)
    win.plugin_refresh_btn = QPushButton("Refresh plugins")
    win.plugin_refresh_btn.setObjectName("secondary")
    win.plugin_refresh_btn.clicked.connect(win._on_plugin_refresh_clicked)
    plugin_actions.addWidget(win.plugin_refresh_btn)
    win.plugin_enable_btn = QPushButton("Review and enable")
    win.plugin_enable_btn.setObjectName("primary")
    win.plugin_enable_btn.clicked.connect(win._on_plugin_enable_clicked)
    plugin_actions.addWidget(win.plugin_enable_btn)
    win.plugin_disable_btn = QPushButton("Disable plugin")
    win.plugin_disable_btn.setObjectName("secondary")
    win.plugin_disable_btn.clicked.connect(win._on_plugin_disable_clicked)
    plugin_actions.addWidget(win.plugin_disable_btn)
    plugin_actions.addStretch(1)
    plugin_lay.addLayout(plugin_actions)
    win.plugin_trust_status = QLabel("No plugin selected.")
    win.plugin_trust_status.setObjectName("subtleText")
    win.plugin_trust_status.setWordWrap(True)
    set_accessible(
        win.plugin_trust_status,
        "Plugin trust status",
        "Status of the selected plugin trust review",
    )
    plugin_lay.addWidget(win.plugin_trust_status)
    sections_top.addWidget(plugin_block, 1)
    win._refresh_plugin_trust_ui()
    card_lay.addLayout(sections_top)

    # ── Source adapter review (V147) ────────────────────────────────
    adapter_block, adapter_lay = make_field_block(
        "Source adapter review",
        "Approve the outbound requests a declarative adapter describes before "
        "it is allowed to issue any.",
    )
    adapter_hint = QLabel(
        "A .yaml definition dropped into the adapters directory stays inert "
        "until you read its contract and approve it here. Editing the hosts, "
        "method, URL, headers, or query parameters of an approved adapter "
        "makes it inert again until you review the change."
    )
    adapter_hint.setObjectName("subtleText")
    adapter_hint.setWordWrap(True)
    adapter_lay.addWidget(adapter_hint)
    win.source_adapter_table = QTableWidget(0, 5)
    win.source_adapter_table.setHorizontalHeaderLabels([
        "Adapter", "Hosts", "Requests", "Definition", "State",
    ])
    win.source_adapter_table.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows
    )
    win.source_adapter_table.setSelectionMode(
        QAbstractItemView.SelectionMode.SingleSelection
    )
    win.source_adapter_table.setEditTriggers(
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    win.source_adapter_table.setMinimumHeight(150)
    style_table(
        win.source_adapter_table,
        row_height=42,
        accessible_name="Source adapter review",
        accessible_description=(
            "Declarative source adapters, the hosts and requests they declare, "
            "and whether their current contract has been approved"
        ),
    )
    adapter_header = win.source_adapter_table.horizontalHeader()
    adapter_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    adapter_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    adapter_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    adapter_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    adapter_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
    win.source_adapter_table.itemSelectionChanged.connect(
        win._on_source_adapter_selection_changed
    )
    adapter_lay.addWidget(win.source_adapter_table)

    adapter_actions = QHBoxLayout()
    adapter_actions.setSpacing(8)
    win.source_adapter_refresh_btn = QPushButton("Rescan adapters")
    win.source_adapter_refresh_btn.setObjectName("secondary")
    win.source_adapter_refresh_btn.clicked.connect(
        win._on_source_adapter_refresh_clicked
    )
    adapter_actions.addWidget(win.source_adapter_refresh_btn)
    win.source_adapter_approve_btn = QPushButton("Review and approve")
    win.source_adapter_approve_btn.setObjectName("primary")
    win.source_adapter_approve_btn.clicked.connect(
        win._on_source_adapter_approve_clicked
    )
    adapter_actions.addWidget(win.source_adapter_approve_btn)
    win.source_adapter_revoke_btn = QPushButton("Revoke approval")
    win.source_adapter_revoke_btn.setObjectName("secondary")
    win.source_adapter_revoke_btn.clicked.connect(
        win._on_source_adapter_revoke_clicked
    )
    adapter_actions.addWidget(win.source_adapter_revoke_btn)
    adapter_actions.addStretch(1)
    adapter_lay.addLayout(adapter_actions)
    win.source_adapter_status = QLabel("No source adapter selected.")
    win.source_adapter_status.setObjectName("subtleText")
    win.source_adapter_status.setWordWrap(True)
    set_accessible(
        win.source_adapter_status,
        "Source adapter review status",
        "Contract and review state of the selected declarative source adapter",
    )
    adapter_lay.addWidget(win.source_adapter_status)
    card_lay.addWidget(adapter_block)
    win._refresh_source_adapter_ui()

    # ── Per-source download engine (V165) ───────────────────────────
    engine_block, engine_lay = make_field_block(
        "Source engines",
        "Choose which engine handles a platform when its extractor breaks.",
    )
    engine_hint = QLabel(
        "Extractor breakage is routine on some platforms, and it looks like a "
        "broken app rather than a broken source. A platform that fails "
        "repeatedly raises a health condition naming the engine that failed; "
        "switch it here and the choice applies to that platform only, "
        "overriding the global live-engine switches. Only engines this "
        "machine can actually run are offered."
    )
    engine_hint.setObjectName("subtleText")
    engine_hint.setWordWrap(True)
    engine_lay.addWidget(engine_hint)
    win.source_engine_table = QTableWidget(0, 3)
    win.source_engine_table.setHorizontalHeaderLabels([
        "Platform", "Recent failures", "Engine",
    ])
    win.source_engine_table.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows
    )
    win.source_engine_table.setEditTriggers(
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    win.source_engine_table.setMinimumHeight(130)
    style_table(
        win.source_engine_table,
        row_height=42,
        accessible_name="Per-source download engines",
    )
    engine_lay.addWidget(win.source_engine_table)
    engine_actions = QHBoxLayout()
    engine_actions.setSpacing(8)
    win.source_engine_refresh_btn = QPushButton("Refresh sources")
    win.source_engine_refresh_btn.setObjectName("secondary")
    win.source_engine_refresh_btn.clicked.connect(
        win._on_source_engine_refresh_clicked
    )
    engine_actions.addWidget(win.source_engine_refresh_btn)
    engine_actions.addStretch(1)
    engine_lay.addLayout(engine_actions)
    win.source_engine_status = QLabel("No source engine overrides.")
    win.source_engine_status.setObjectName("subtleText")
    win.source_engine_status.setWordWrap(True)
    set_accessible(
        win.source_engine_status,
        "Source engine status",
        "Which platforms have a non-automatic download engine",
    )
    engine_lay.addWidget(win.source_engine_status)
    card_lay.addWidget(engine_block)
    win._refresh_source_engine_ui()

    # ── Adaptive rate governance (V162) ─────────────────────────────
    governor_block, governor_lay = make_field_block(
        "Rate governance",
        "Back off automatically when a host says you are going too fast.",
    )
    governor_hint = QLabel(
        "When a host answers with 429 or another throttle, StreamKeep halves "
        "how many jobs it runs against that host and spaces the requests out, "
        "then gives the capacity back after a run of clean transfers. The "
        "reaction is per host, so slowing down for a strict site does not slow "
        "down the rest of the queue, and a Retry-After header is honoured over "
        "our own estimate."
    )
    governor_hint.setObjectName("subtleText")
    governor_hint.setWordWrap(True)
    governor_lay.addWidget(governor_hint)
    win.rate_governor_check = QCheckBox("Back off automatically when throttled")
    win.rate_governor_check.setChecked(
        bool(win._config.get("rate_governor_enabled", True))
    )
    win.rate_governor_check.toggled.connect(win._on_rate_governor_toggled)
    governor_lay.addWidget(win.rate_governor_check)
    win.rate_governor_status = QLabel("No host is being throttled.")
    win.rate_governor_status.setObjectName("subtleText")
    win.rate_governor_status.setWordWrap(True)
    set_accessible(
        win.rate_governor_status,
        "Rate governance status",
        "Hosts StreamKeep is currently backing off from",
    )
    governor_lay.addWidget(win.rate_governor_status)
    card_lay.addWidget(governor_block)
    win._refresh_rate_governor_ui()

    # ── Cookies ─────────────────────────────────────────────────────
    cookies_block, cookies_lay = make_field_block(
        "Browser Cookies",
        "Use browser cookies or a cookies.txt file for age-restricted or "
        "authenticated content.",
    )

    row_cookies = QHBoxLayout()
    row_cookies.setSpacing(8)
    win.cookies_combo = QComboBox()
    win.cookies_combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    win.cookies_combo.setMinimumContentsLength(18)
    win.cookies_combo.addItem("None")
    row_cookies.addWidget(win.cookies_combo, 1)
    scan_btn = QPushButton("Scan for browsers")
    scan_btn.setObjectName("secondary")
    scan_btn.clicked.connect(win._on_scan_browsers)
    row_cookies.addWidget(scan_btn)
    cookies_lay.addLayout(row_cookies)

    row_cookiefile = QHBoxLayout()
    row_cookiefile.setSpacing(8)
    win.cookies_file_input = QLineEdit()
    win.cookies_file_input.setPlaceholderText("Path to cookies.txt (Netscape format)")
    win.cookies_file_input.setClearButtonEnabled(True)
    row_cookiefile.addWidget(win.cookies_file_input, 1)
    browse_cookies = QPushButton("Browse")
    browse_cookies.setObjectName("secondary")
    browse_cookies.clicked.connect(win._on_browse_cookies_file)
    row_cookiefile.addWidget(browse_cookies)
    cookies_lay.addLayout(row_cookiefile)

    # Import cookies to cookies.txt (F47)
    row_import = QHBoxLayout()
    row_import.setSpacing(8)
    win.cookies_import_btn = QPushButton("Import cookies from browser")
    win.cookies_import_btn.setObjectName("secondary")
    win.cookies_import_btn.setToolTip(
        "Extract cookies from the selected browser and save as cookies.txt "
        "for authenticated downloads (F47)"
    )
    win.cookies_import_btn.clicked.connect(win._on_import_browser_cookies)
    row_import.addWidget(win.cookies_import_btn)
    win.cookies_check_btn = QPushButton("Check")
    win.cookies_check_btn.setObjectName("secondary")
    win.cookies_check_btn.setFixedWidth(70)
    win.cookies_check_btn.setToolTip("Check the imported cookies for expiry")
    win.cookies_check_btn.clicked.connect(win._on_check_cookies)
    row_import.addWidget(win.cookies_check_btn)
    win.cookies_clear_btn = QPushButton("Clear")
    win.cookies_clear_btn.setObjectName("secondary")
    win.cookies_clear_btn.setFixedWidth(70)
    win.cookies_clear_btn.clicked.connect(win._on_clear_cookies)
    row_import.addWidget(win.cookies_clear_btn)
    row_import.addStretch(1)
    cookies_lay.addLayout(row_import)

    win.cookies_scan_label = QLabel("")
    win.cookies_scan_label.setObjectName("subtleText")
    win.cookies_scan_label.setWordWrap(True)
    cookies_lay.addWidget(win.cookies_scan_label)

    # Show cookies.txt status
    win.cookies_status_label = QLabel("")
    win.cookies_status_label.setObjectName("subtleText")
    cookies_lay.addWidget(win.cookies_status_label)
    card_lay.addWidget(cookies_block)

    # ── Authentication Profiles (V50) ─────────────────────────────
    auth_block, auth_lay = make_field_block(
        "Authentication Profiles",
        "Named credentials bound to the sites they are allowed to "
        "authenticate. A profile is never sent to a site outside its scope.",
    )
    win.auth_profile_list = QComboBox()
    win.auth_profile_list.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    win.auth_profile_list.setMinimumContentsLength(24)
    win.auth_profile_list.currentIndexChanged.connect(
        win._on_auth_profile_selected
    )
    auth_lay.addWidget(win.auth_profile_list)

    win.auth_profile_detail = QLabel("")
    win.auth_profile_detail.setObjectName("subtleText")
    win.auth_profile_detail.setWordWrap(True)
    auth_lay.addWidget(win.auth_profile_detail)

    auth_row = QHBoxLayout()
    auth_row.setSpacing(8)
    for label, slot, width in (
        ("New profile…", "_on_auth_profile_new", 130),
        ("Import cookies", "_on_auth_profile_import", 130),
        ("Check", "_on_auth_profile_check", 78),
        ("Delete", "_on_auth_profile_delete", 78),
    ):
        button = QPushButton(label)
        button.setObjectName("secondary")
        button.setFixedWidth(width)
        button.clicked.connect(getattr(win, slot))
        auth_row.addWidget(button)
    auth_row.addStretch(1)
    auth_lay.addLayout(auth_row)
    card_lay.addWidget(auth_block)
    win._refresh_auth_profiles()

    saved_browser = win._config.get("cookies_browser", "")
    saved_file = win._config.get("cookies_file", "")
    if saved_file:
        win.cookies_file_input.setText(saved_file)
        YtDlpExtractor.cookies_file = saved_file
    win._scan_browsers_silent()
    if saved_browser:
        idx = win.cookies_combo.findText(saved_browser)
        if idx >= 0:
            win.cookies_combo.setCurrentIndex(idx)
        YtDlpExtractor.cookies_browser = saved_browser
    YtDlpExtractor.auth_profile_id = str(
        win._config.get("auth_profile_id", "") or ""
    )
    # Update cookies.txt status indicator (F47)
    if hasattr(win, "_update_cookies_status"):
        win._update_cookies_status()

    # ── Platform Accounts (F48) ───────────────────────────────────
    accounts_block, accounts_lay = make_field_block(
        "Platform Accounts",
        "Store API tokens for authenticated platform access. "
        "Tokens are encrypted with Windows DPAPI.",
    )
    from streamkeep.accounts import PLATFORMS as _ACCT_PLATFORMS, credential_status
    win._account_inputs = {}
    for plat_key, plat_info in _ACCT_PLATFORMS.items():
        arow = QHBoxLayout()
        arow.setSpacing(8)
        alabel = QLabel(f"{plat_info['label']}:")
        alabel.setFixedWidth(100)
        arow.addWidget(alabel)
        ainput = QLineEdit()
        ainput.setPlaceholderText(plat_info["hint"])
        ainput.setEchoMode(QLineEdit.EchoMode.Password)
        arow.addWidget(ainput, 1)
        status = credential_status(plat_key)
        astatus = QLabel(status)
        astatus.setFixedWidth(100)
        astatus.setObjectName("subtleText")
        arow.addWidget(astatus)
        accounts_lay.addLayout(arow)
        win._account_inputs[plat_key] = (ainput, astatus)

    acct_btn_row = QHBoxLayout()
    acct_btn_row.setSpacing(8)
    win.acct_save_btn = QPushButton("Save tokens")
    win.acct_save_btn.setObjectName("secondary")
    win.acct_save_btn.clicked.connect(win._on_save_account_tokens)
    acct_btn_row.addWidget(win.acct_save_btn)
    win.acct_check_btn = QPushButton("Check")
    win.acct_check_btn.setObjectName("secondary")
    win.acct_check_btn.setFixedWidth(80)
    win.acct_check_btn.setToolTip(
        "Validate saved tokens against each platform without downloading"
    )
    win.acct_check_btn.clicked.connect(win._on_check_account_tokens)
    acct_btn_row.addWidget(win.acct_check_btn)
    win.acct_clear_btn = QPushButton("Clear all")
    win.acct_clear_btn.setObjectName("secondary")
    win.acct_clear_btn.setFixedWidth(80)
    win.acct_clear_btn.clicked.connect(win._on_clear_account_tokens)
    acct_btn_row.addWidget(win.acct_clear_btn)
    acct_btn_row.addStretch(1)
    accounts_lay.addLayout(acct_btn_row)
    card_lay.addWidget(accounts_block)

    # ── Network ────────────────────────────────────────────────────
    network_block, network_lay = make_field_block(
        "Network",
        "Optional bandwidth throttling and proxy for geo-blocked content.",
    )
    rate_row = QHBoxLayout()
    rate_row.setSpacing(8)
    rate_label = QLabel("Rate limit:")
    rate_label.setFixedWidth(100)
    rate_row.addWidget(rate_label)
    win.rate_limit_input = QLineEdit()
    win.rate_limit_input.setPlaceholderText("e.g. 500K or 2M (leave blank for unlimited)")
    win.rate_limit_input.setClearButtonEnabled(True)
    rate_row.addWidget(win.rate_limit_input, 1)
    network_lay.addLayout(rate_row)

    transfer_hint = QLabel(
        "yt-dlp transfer depth applies to direct yt-dlp downloads. Blank or "
        "zero values retain yt-dlp defaults."
    )
    transfer_hint.setObjectName("subtleText")
    transfer_hint.setWordWrap(True)
    network_lay.addWidget(transfer_hint)

    fragment_row = QHBoxLayout()
    fragment_row.addWidget(QLabel("Fragments:"))
    win.ytdlp_fragments_spin = QSpinBox()
    win.ytdlp_fragments_spin.setRange(0, 32)
    win.ytdlp_fragments_spin.setSpecialValueText("yt-dlp default")
    win.ytdlp_fragments_spin.setValue(int(
        win._config.get("ytdlp_concurrent_fragments", 0) or 0
    ))
    fragment_row.addWidget(win.ytdlp_fragments_spin)
    fragment_row.addWidget(QLabel("Retries:"))
    win.ytdlp_retries_input = QLineEdit(str(
        win._config.get("ytdlp_retries", "") or ""
    ))
    win.ytdlp_retries_input.setPlaceholderText("default or infinite")
    fragment_row.addWidget(win.ytdlp_retries_input)
    fragment_row.addWidget(QLabel("Fragment retries:"))
    win.ytdlp_fragment_retries_input = QLineEdit(str(
        win._config.get("ytdlp_fragment_retries", "") or ""
    ))
    win.ytdlp_fragment_retries_input.setPlaceholderText("default or infinite")
    fragment_row.addWidget(win.ytdlp_fragment_retries_input)
    network_lay.addLayout(fragment_row)

    retry_row = QHBoxLayout()
    retry_row.addWidget(QLabel("Retry sleep:"))
    win.ytdlp_retry_sleep_input = QLineEdit(str(
        win._config.get("ytdlp_retry_sleep", "") or ""
    ))
    win.ytdlp_retry_sleep_input.setPlaceholderText("e.g. fragment:exp=1:20")
    retry_row.addWidget(win.ytdlp_retry_sleep_input)
    retry_row.addWidget(QLabel("Unavailable fragments:"))
    win.ytdlp_unavailable_combo = QComboBox()
    win.ytdlp_unavailable_combo.addItem("yt-dlp default", userData="")
    win.ytdlp_unavailable_combo.addItem("Skip", userData="skip")
    win.ytdlp_unavailable_combo.addItem("Abort download", userData="abort")
    unavailable = str(
        win._config.get("ytdlp_unavailable_fragments", "") or ""
    )
    index = win.ytdlp_unavailable_combo.findData(unavailable)
    win.ytdlp_unavailable_combo.setCurrentIndex(max(0, index))
    retry_row.addWidget(win.ytdlp_unavailable_combo)
    network_lay.addLayout(retry_row)

    live_row = QHBoxLayout()
    live_row.addWidget(QLabel("Throttled threshold:"))
    win.ytdlp_throttled_input = QLineEdit(str(
        win._config.get("ytdlp_throttled_rate", "") or ""
    ))
    win.ytdlp_throttled_input.setPlaceholderText("e.g. 100K")
    live_row.addWidget(win.ytdlp_throttled_input)
    live_row.addWidget(QLabel("Wait for scheduled video:"))
    win.ytdlp_wait_for_video_input = QLineEdit(str(
        win._config.get("ytdlp_wait_for_video", "") or ""
    ))
    win.ytdlp_wait_for_video_input.setPlaceholderText("seconds or MIN-MAX")
    live_row.addWidget(win.ytdlp_wait_for_video_input)
    win.ytdlp_live_from_start_check = QCheckBox("Live from start")
    win.ytdlp_live_from_start_check.setChecked(bool(
        win._config.get("ytdlp_live_from_start", False)
    ))
    live_row.addWidget(win.ytdlp_live_from_start_check)
    network_lay.addLayout(live_row)

    # Live-capture reliability (V36)
    from ...integrations.ytarchive import (
        ytarchive_available,
        ytarchive_install_hint,
    )
    engine_row = QHBoxLayout()
    engine_row.setSpacing(8)
    win.live_engine_fallback_check = QCheckBox(
        "Re-capture from the start with ytarchive when a live capture drops "
        "fragments"
    )
    win.live_engine_fallback_check.setChecked(bool(
        win._config.get("live_engine_fallback", False)
    ))
    win.live_engine_fallback_check.setToolTip(
        "Raw capture files are always preserved for salvage. This only adds an "
        "optional second attempt using the external ytarchive engine."
        if ytarchive_available() else ytarchive_install_hint()
    )
    win.live_engine_fallback_check.setEnabled(ytarchive_available())
    engine_row.addWidget(win.live_engine_fallback_check)
    engine_row.addStretch(1)
    network_lay.addLayout(engine_row)

    # Optional in-process live transport (V13). Keep the dependency optional
    # and fail closed when an unsupported Streamlink release is installed.
    from ...integrations.streamlink import (
        streamlink_available,
        streamlink_install_hint,
    )
    streamlink_row = QHBoxLayout()
    streamlink_row.setSpacing(8)
    win.streamlink_live_engine_check = QCheckBox(
        "Use Streamlink for Twitch/Kick live captures"
    )
    streamlink_live_available = streamlink_available()
    win.streamlink_live_engine_check.setChecked(bool(
        win._config.get("streamlink_live_engine", False)
    ))
    win.streamlink_live_engine_check.setEnabled(streamlink_live_available)
    win.streamlink_live_engine_check.setToolTip(
        "Uses Streamlink's mandatory Twitch ad filtering, low-latency mode, "
        "and guarded HLS DVR options."
        if streamlink_live_available else streamlink_install_hint()
    )
    streamlink_row.addWidget(win.streamlink_live_engine_check)
    streamlink_row.addStretch(1)
    network_lay.addLayout(streamlink_row)

    twitch_unmute_row = QHBoxLayout()
    twitch_unmute_row.setSpacing(8)
    win.twitch_unmute_check = QCheckBox(
        "Restore available unmuted audio in Twitch VODs"
    )
    win.twitch_unmute_check.setChecked(bool(
        win._config.get("twitch_unmute", False)
    ))
    win.twitch_unmute_check.setToolTip(
        "For finished Twitch VODs, probe same-format CDN fragments whose "
        "paths end in -muted and use the unmuted fragment when it exists. "
        "Unreachable fragments remain muted; live captures are not probed."
    )
    twitch_unmute_row.addWidget(win.twitch_unmute_check)
    twitch_unmute_row.addStretch(1)
    network_lay.addLayout(twitch_unmute_row)

    streamlink_dvr_row = QHBoxLayout()
    streamlink_dvr_row.addWidget(QLabel("Streamlink DVR offset (seconds):"))
    win.streamlink_hls_offset_input = QLineEdit(str(
        win._config.get("streamlink_hls_start_offset", "") or ""
    ))
    win.streamlink_hls_offset_input.setPlaceholderText("0 = current live edge")
    streamlink_dvr_row.addWidget(win.streamlink_hls_offset_input)
    win.streamlink_hls_restart_check = QCheckBox(
        "Start from the oldest available live segment"
    )
    win.streamlink_hls_restart_check.setChecked(bool(
        win._config.get("streamlink_hls_live_restart", False)
    ))
    win.streamlink_hls_restart_check.setToolTip(
        "Uses Streamlink's HLS live-restart/DVR behavior. The source must "
        "still expose those older segments."
    )
    streamlink_dvr_row.addWidget(win.streamlink_hls_restart_check)
    network_lay.addLayout(streamlink_dvr_row)

    embed_row = QHBoxLayout()
    embed_row.addWidget(QLabel("Embed:"))
    for name, label in (
        ("chapters", "Chapters"),
        ("metadata", "Metadata"),
        ("thumbnail", "Thumbnail"),
    ):
        combo = QComboBox()
        combo.addItem(f"{label}: yt-dlp default", userData=None)
        combo.addItem(f"{label}: on", userData=True)
        combo.addItem(f"{label}: off", userData=False)
        current = win._config.get(f"ytdlp_embed_{name}")
        combo.setCurrentIndex(1 if current is True else 2 if current is False else 0)
        setattr(win, f"ytdlp_embed_{name}_combo", combo)
        embed_row.addWidget(combo)
    network_lay.addLayout(embed_row)

    template_hint = QLabel(
        "Named yt-dlp argument templates use one argv element per line. "
        "Only approved transfer, format, subtitle, and playlist options are "
        "accepted; executable paths, plugins, output/config delegation, and "
        "link writers are rejected. Imported templates stay disabled until "
        "you explicitly approve them. Templates can be attached in Download "
        "Advanced or a monitor channel profile."
    )
    template_hint.setObjectName("subtleText")
    template_hint.setWordWrap(True)
    network_lay.addWidget(template_hint)

    template_pick_row = QHBoxLayout()
    template_pick_row.addWidget(QLabel("Argument template:"))
    win.ytdlp_template_editor_combo = QComboBox()
    win.ytdlp_template_editor_combo.currentIndexChanged.connect(
        win._on_ytdlp_template_selected
    )
    template_pick_row.addWidget(win.ytdlp_template_editor_combo, 1)
    win.ytdlp_template_name_input = QLineEdit()
    win.ytdlp_template_name_input.setMaxLength(64)
    win.ytdlp_template_name_input.setPlaceholderText("Template name")
    template_pick_row.addWidget(win.ytdlp_template_name_input, 1)
    network_lay.addLayout(template_pick_row)

    win.ytdlp_template_args_edit = QPlainTextEdit()
    win.ytdlp_template_args_edit.setMaximumHeight(120)
    win.ytdlp_template_args_edit.setPlaceholderText(
        "--add-header\nReferer: https://example.com/\n--user-agent\nArchive workstation"
    )
    network_lay.addWidget(win.ytdlp_template_args_edit)
    template_action_row = QHBoxLayout()
    template_action_row.addStretch(1)
    win.ytdlp_template_delete_btn = QPushButton("Delete template")
    win.ytdlp_template_delete_btn.setObjectName("ghost")
    win.ytdlp_template_delete_btn.clicked.connect(
        win._on_ytdlp_template_delete
    )
    template_action_row.addWidget(win.ytdlp_template_delete_btn)
    win.ytdlp_template_save_btn = QPushButton("Save template")
    win.ytdlp_template_save_btn.setObjectName("secondary")
    win.ytdlp_template_save_btn.clicked.connect(win._on_ytdlp_template_save)
    template_action_row.addWidget(win.ytdlp_template_save_btn)
    network_lay.addLayout(template_action_row)
    win._refresh_ytdlp_template_editor()

    proxy_row = QHBoxLayout()
    proxy_row.setSpacing(8)
    proxy_label = QLabel("Proxy URL:")
    proxy_label.setFixedWidth(100)
    proxy_row.addWidget(proxy_label)
    win.proxy_input = QLineEdit()
    win.proxy_input.setPlaceholderText("e.g. socks5://127.0.0.1:1080 or http://proxy:8080")
    win.proxy_input.setClearButtonEnabled(True)
    proxy_row.addWidget(win.proxy_input, 1)
    network_lay.addLayout(proxy_row)

    # Proxy pool (F49)
    proxy_pool_hint = QLabel(
        "Proxy pool: assign proxies to specific platforms. "
        "Format per line: url|platform1,platform2|label (platforms optional)."
    )
    proxy_pool_hint.setObjectName("subtleText")
    proxy_pool_hint.setWordWrap(True)
    network_lay.addWidget(proxy_pool_hint)
    win.proxy_pool_edit = QPlainTextEdit()
    win.proxy_pool_edit.setMaximumHeight(100)
    win.proxy_pool_edit.setPlaceholderText(
        "socks5://us.proxy:1080|twitch,kick|US proxy\n"
        "http://de.proxy:8080|youtube|DE proxy\n"
        "http://fallback:3128||Global fallback"
    )
    network_lay.addWidget(win.proxy_pool_edit)
    # Load saved pool
    _saved_pool = win._config.get("proxy_pool", [])
    if isinstance(_saved_pool, list) and _saved_pool:
        lines = []
        for pe in _saved_pool:
            if isinstance(pe, dict) and pe.get("url"):
                plats = ",".join(pe.get("platforms", []))
                label = pe.get("label", "")
                lines.append(f"{pe['url']}|{plats}|{label}")
        win.proxy_pool_edit.setPlainText("\n".join(lines))
        from streamkeep.proxy import set_pool
        set_pool(_saved_pool)

    proxy_test_btn = QPushButton("Test proxies")
    proxy_test_btn.setObjectName("secondary")
    proxy_test_btn.setFixedWidth(110)
    proxy_test_btn.clicked.connect(win._on_test_proxies)
    network_lay.addWidget(proxy_test_btn)

    # Bandwidth schedule
    win.bw_enable_check = QCheckBox(
        "Enable bandwidth schedule (overrides Rate limit within the window)"
    )
    win.bw_enable_check.setChecked(win._bandwidth_rule["enabled"])
    network_lay.addWidget(win.bw_enable_check)
    bw_row = QHBoxLayout()
    bw_row.setSpacing(8)
    bw_row.addWidget(QLabel("Window:"))
    win.bw_start_spin = QSpinBox()
    win.bw_start_spin.setRange(0, 23)
    win.bw_start_spin.setSuffix(":00")
    win.bw_start_spin.setValue(win._bandwidth_rule["start_hour"])
    bw_row.addWidget(win.bw_start_spin)
    bw_row.addWidget(QLabel("to"))
    win.bw_end_spin = QSpinBox()
    win.bw_end_spin.setRange(0, 23)
    win.bw_end_spin.setSuffix(":00")
    win.bw_end_spin.setValue(win._bandwidth_rule["end_hour"])
    bw_row.addWidget(win.bw_end_spin)
    bw_row.addSpacing(12)
    bw_row.addWidget(QLabel("Limit:"))
    win.bw_limit_input = QLineEdit(win._bandwidth_rule["limit"])
    win.bw_limit_input.setPlaceholderText("500K")
    win.bw_limit_input.setClearButtonEnabled(True)
    win.bw_limit_input.setFixedWidth(100)
    bw_row.addWidget(win.bw_limit_input)
    bw_row.addStretch(1)
    network_lay.addLayout(bw_row)

    # Speed schedule (F51) — day/night/weekend tiers
    sched_hint = QLabel(
        "Speed schedule: set different bandwidth limits for day, night, "
        "and weekends. Applied to new downloads only."
    )
    sched_hint.setObjectName("subtleText")
    sched_hint.setWordWrap(True)
    network_lay.addWidget(sched_hint)
    win.sched_enable_check = QCheckBox("Enable speed schedule")
    network_lay.addWidget(win.sched_enable_check)
    sched_row = QHBoxLayout()
    sched_row.setSpacing(8)
    sched_row.addWidget(QLabel("Day:"))
    win.sched_day_start = QSpinBox()
    win.sched_day_start.setRange(0, 23)
    win.sched_day_start.setSuffix(":00")
    win.sched_day_start.setValue(8)
    sched_row.addWidget(win.sched_day_start)
    sched_row.addWidget(QLabel("-"))
    win.sched_day_end = QSpinBox()
    win.sched_day_end.setRange(0, 23)
    win.sched_day_end.setSuffix(":00")
    win.sched_day_end.setValue(23)
    sched_row.addWidget(win.sched_day_end)
    sched_row.addWidget(QLabel("Limit:"))
    win.sched_day_limit = QLineEdit("2M")
    win.sched_day_limit.setFixedWidth(80)
    win.sched_day_limit.setPlaceholderText("2M")
    win.sched_day_limit.setClearButtonEnabled(True)
    sched_row.addWidget(win.sched_day_limit)
    network_lay.addLayout(sched_row)

    sched_row2 = QHBoxLayout()
    sched_row2.setSpacing(8)
    sched_row2.addWidget(QLabel("Night limit:"))
    win.sched_night_limit = QLineEdit("")
    win.sched_night_limit.setFixedWidth(80)
    win.sched_night_limit.setPlaceholderText("(unlimited)")
    win.sched_night_limit.setClearButtonEnabled(True)
    sched_row2.addWidget(win.sched_night_limit)
    sched_row2.addSpacing(12)
    sched_row2.addWidget(QLabel("Weekend limit:"))
    win.sched_weekend_limit = QLineEdit("")
    win.sched_weekend_limit.setFixedWidth(80)
    win.sched_weekend_limit.setPlaceholderText("(unlimited)")
    win.sched_weekend_limit.setClearButtonEnabled(True)
    sched_row2.addWidget(win.sched_weekend_limit)
    sched_row2.addStretch(1)
    network_lay.addLayout(sched_row2)

    # Restore saved speed schedule
    _saved_sched = win._config.get("speed_schedule", {})
    if isinstance(_saved_sched, dict):
        win.sched_enable_check.setChecked(bool(_saved_sched.get("enabled", False)))
        win.sched_day_start.setValue(int(_saved_sched.get("day_start", 8) or 8))
        win.sched_day_end.setValue(int(_saved_sched.get("day_end", 23) or 23))
        win.sched_day_limit.setText(str(_saved_sched.get("day_limit", "2M") or ""))
        win.sched_night_limit.setText(str(_saved_sched.get("night_limit", "") or ""))
        win.sched_weekend_limit.setText(str(_saved_sched.get("weekend_limit", "") or ""))
        from streamkeep.scheduler import configure as _sched_configure
        _sched_configure(_saved_sched, win._config.get("rate_limit", ""))

    # Parallel connections per direct MP4
    par_row = QHBoxLayout()
    par_row.setSpacing(8)
    par_label = QLabel("Parallel connections:")
    par_label.setFixedWidth(140)
    par_row.addWidget(par_label)
    win.parallel_spin = QSpinBox()
    win.parallel_spin.setRange(1, 16)
    win.parallel_spin.setValue(win._parallel_connections)
    win.parallel_spin.setToolTip(
        "Multi-connection HTTP Range splitting for direct MP4 files.\n"
        "Higher values can be 3-5x faster on CDN-hosted content.\n"
        "Set to 1 to disable and always use ffmpeg."
    )
    par_row.addWidget(win.parallel_spin)
    par_hint = QLabel("per direct MP4 (1 = off, default 4)")
    par_hint.setObjectName("subtleText")
    par_row.addWidget(par_hint)
    par_row.addStretch(1)
    network_lay.addLayout(par_row)

    # Parallel auto-records (v4.15.0)
    par_ar_row = QHBoxLayout()
    par_ar_row.setSpacing(8)
    par_ar_label = QLabel("Parallel auto-records:")
    par_ar_label.setFixedWidth(140)
    par_ar_row.addWidget(par_ar_label)
    win.parallel_autorecords_spin = QSpinBox()
    win.parallel_autorecords_spin.setRange(1, 4)
    win.parallel_autorecords_spin.setValue(int(win._parallel_autorecords or 2))
    win.parallel_autorecords_spin.setToolTip(
        "Maximum simultaneous auto-recordings when multiple monitored "
        "channels go live at the same time. Each recording uses its "
        "own ffmpeg process."
    )
    par_ar_row.addWidget(win.parallel_autorecords_spin)
    par_ar_hint = QLabel("channels captured at once (default 2)")
    par_ar_hint.setObjectName("subtleText")
    par_ar_row.addWidget(par_ar_hint)
    par_ar_row.addStretch(1)
    network_lay.addLayout(par_ar_row)

    # Concurrent queue downloads (v4.19.0 — F1)
    cq_row = QHBoxLayout()
    cq_row.setSpacing(8)
    cq_label = QLabel("Concurrent queue jobs:")
    cq_label.setFixedWidth(140)
    cq_row.addWidget(cq_label)
    win.concurrent_queue_spin = QSpinBox()
    win.concurrent_queue_spin.setRange(1, 8)
    win.concurrent_queue_spin.setValue(int(getattr(win, "_max_concurrent_downloads", 3)))
    win.concurrent_queue_spin.setToolTip(
        "Maximum queued downloads that run at the same time.\n"
        "Bandwidth is shared evenly across active jobs when a\n"
        "rate limit is set."
    )
    cq_row.addWidget(win.concurrent_queue_spin)
    cq_hint = QLabel("simultaneous queue downloads (default 3)")
    cq_hint.setObjectName("subtleText")
    cq_row.addWidget(cq_hint)
    cq_row.addStretch(1)
    network_lay.addLayout(cq_row)

    # Chunked live recording (v4.15.0)
    chunk_row = QHBoxLayout()
    chunk_row.setSpacing(8)
    win.chunk_check = QCheckBox("Split long live captures into chunks")
    win.chunk_check.setChecked(bool(win._chunk_long_captures))
    win.chunk_check.setToolTip(
        "When enabled, live captures are written as sequential _part001.mp4, "
        "_part002.mp4, ... files of the configured chunk length. Only applies "
        "to live recordings (not VODs)."
    )
    chunk_row.addWidget(win.chunk_check)
    win.chunk_length_spin = QSpinBox()
    win.chunk_length_spin.setRange(600, 21600)       # 10 min .. 6 h
    win.chunk_length_spin.setSingleStep(600)
    win.chunk_length_spin.setSuffix(" sec")
    win.chunk_length_spin.setValue(int(win._chunk_length_secs or 7200))
    win.chunk_length_spin.setEnabled(bool(win._chunk_long_captures))
    win.chunk_check.toggled.connect(win.chunk_length_spin.setEnabled)
    chunk_row.addWidget(win.chunk_length_spin)
    chunk_hint = QLabel("per chunk (default 2 hours)")
    chunk_hint.setObjectName("subtleText")
    chunk_row.addWidget(chunk_hint)
    chunk_row.addStretch(1)
    network_lay.addLayout(chunk_row)

    # Per-platform default quality (v4.17.0)
    quality_hdr = QLabel("Default quality per platform")
    quality_hdr.setObjectName("sectionTitle")
    network_lay.addWidget(quality_hdr)
    win.quality_defaults_combos = {}
    quality_opts = [
        ("", "Highest available (default)"),
        ("source", "Source / best"),
        ("1080p", "1080p"),
        ("720p", "720p"),
        ("480p", "480p"),
        ("360p", "360p"),
        ("lowest", "Lowest available"),
    ]
    saved_q = dict(win._config.get("quality_defaults") or {})
    for platform in ("twitch", "kick", "rumble", "youtube", "other"):
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(platform.capitalize() + ":")
        lbl.setFixedWidth(90)
        row.addWidget(lbl)
        combo = QComboBox()
        for val, text in quality_opts:
            combo.addItem(text, userData=val)
        current = saved_q.get(platform, "")
        idx = max(0, combo.findData(current))
        combo.setCurrentIndex(idx)
        row.addWidget(combo)
        row.addStretch(1)
        network_lay.addLayout(row)
        win.quality_defaults_combos[platform] = combo

    # Whisper transcription model (v4.17.0)
    whisper_row = QHBoxLayout()
    whisper_row.setSpacing(8)
    whisper_row.addWidget(QLabel("Whisper model:"))
    win.whisper_model_combo = QComboBox()
    for m in ("tiny", "base", "small", "medium", "large-v3"):
        win.whisper_model_combo.addItem(m, userData=m)
    wm = str(win._config.get("whisper_model", "tiny") or "tiny")
    idx = max(0, win.whisper_model_combo.findData(wm))
    win.whisper_model_combo.setCurrentIndex(idx)
    win.whisper_model_combo.setToolTip(
        "tiny/base are fast on CPU; medium/large-v3 need GPU for sane speeds."
    )
    whisper_row.addWidget(win.whisper_model_combo)
    whisper_row.addWidget(QLabel("(used by Transcribe... in History)"))
    whisper_row.addStretch(1)
    network_lay.addLayout(whisper_row)

    ffmpeg_whisper_row = QHBoxLayout()
    ffmpeg_whisper_row.setSpacing(8)
    ffmpeg_whisper_row.addWidget(QLabel("FFmpeg whisper model:"))
    win.whisper_ffmpeg_model_input = QLineEdit(
        str(win._config.get("whisper_model_path", "") or "")
    )
    win.whisper_ffmpeg_model_input.setClearButtonEnabled(True)
    win.whisper_ffmpeg_model_input.setPlaceholderText(
        "Optional local whisper.cpp .bin model path"
    )
    win.whisper_ffmpeg_model_input.setToolTip(
        "Enables the FFmpeg whisper fallback only when the resolved FFmpeg "
        "build exposes the filter and this local model file exists."
    )
    ffmpeg_whisper_row.addWidget(win.whisper_ffmpeg_model_input, 1)
    ffmpeg_whisper_browse = QPushButton("Browse")
    ffmpeg_whisper_browse.setObjectName("secondary")
    ffmpeg_whisper_browse.clicked.connect(
        lambda: win._settings_browse_file(
            win.whisper_ffmpeg_model_input, "Select Whisper model"
        )
    )
    ffmpeg_whisper_row.addWidget(ffmpeg_whisper_browse)
    network_lay.addLayout(ffmpeg_whisper_row)
    win.whisper_ffmpeg_status = QLabel("")
    win.whisper_ffmpeg_status.setObjectName("subtleText")
    win.whisper_ffmpeg_status.setWordWrap(True)
    network_lay.addWidget(win.whisper_ffmpeg_status)
    win._refresh_transcription_runtime_controls()

    # Speaker diarization (F29)
    diarize_row = QHBoxLayout()
    diarize_row.setSpacing(8)
    win.diarize_check = QCheckBox("Enable speaker diarization")
    win.diarize_check.setChecked(bool(win._config.get("enable_diarization", False)))
    win.diarize_check.setToolTip(
        "Requires WhisperX + pyannote-audio + a HuggingFace token. "
        "Labels transcript segments with speaker IDs (Speaker 1, Speaker 2, ...)."
    )
    diarize_row.addWidget(win.diarize_check)
    diarize_row.addWidget(QLabel("HF token:"))
    win.hf_token_input = QLineEdit()
    win.hf_token_input.setPlaceholderText("hf_...")
    win.hf_token_input.setEchoMode(QLineEdit.EchoMode.Password)
    win.hf_token_input.setText(str(win._config.get("hf_token", "") or ""))
    win.hf_token_input.setMaximumWidth(260)
    win.hf_token_input.setToolTip(
        "Free HuggingFace token for pyannote speaker diarization models."
    )
    diarize_row.addWidget(win.hf_token_input)
    diarize_row.addStretch(1)
    network_lay.addLayout(diarize_row)

    # Chat Render settings (F22)
    cr_header = QLabel("<b>Chat Render</b>")
    network_lay.addWidget(cr_header)
    cr_row1 = QHBoxLayout()
    cr_row1.setSpacing(8)
    cr_row1.addWidget(QLabel("Width:"))
    win.chat_render_width_spin = QSpinBox()
    win.chat_render_width_spin.setRange(200, 1920)
    win.chat_render_width_spin.setValue(int(win._config.get("chat_render_width", 400) or 400))
    cr_row1.addWidget(win.chat_render_width_spin)
    cr_row1.addWidget(QLabel("Height:"))
    win.chat_render_height_spin = QSpinBox()
    win.chat_render_height_spin.setRange(200, 1080)
    win.chat_render_height_spin.setValue(int(win._config.get("chat_render_height", 600) or 600))
    cr_row1.addWidget(win.chat_render_height_spin)
    cr_row1.addWidget(QLabel("Font size:"))
    win.chat_render_font_spin = QSpinBox()
    win.chat_render_font_spin.setRange(8, 36)
    win.chat_render_font_spin.setValue(int(win._config.get("chat_render_font_size", 14) or 14))
    cr_row1.addWidget(win.chat_render_font_spin)
    cr_row1.addStretch(1)
    network_lay.addLayout(cr_row1)
    cr_row2 = QHBoxLayout()
    cr_row2.setSpacing(8)
    cr_row2.addWidget(QLabel("Message duration (s):"))
    win.chat_render_duration_spin = QSpinBox()
    win.chat_render_duration_spin.setRange(2, 30)
    win.chat_render_duration_spin.setValue(int(win._config.get("chat_render_msg_duration", 8) or 8))
    cr_row2.addWidget(win.chat_render_duration_spin)
    cr_row2.addWidget(QLabel("BG opacity:"))
    win.chat_render_opacity_spin = QSpinBox()
    win.chat_render_opacity_spin.setRange(0, 255)
    win.chat_render_opacity_spin.setValue(int(win._config.get("chat_render_bg_opacity", 180) or 180))
    cr_row2.addWidget(win.chat_render_opacity_spin)
    cr_row2.addStretch(1)
    network_lay.addLayout(cr_row2)

    # Live chat capture (v4.16.0)
    chat_row = QHBoxLayout()
    chat_row.setSpacing(8)
    win.capture_chat_check = QCheckBox("Capture Twitch chat during live recordings")
    win.capture_chat_check.setChecked(bool(win._config.get("capture_live_chat", False)))
    win.capture_chat_check.setToolTip(
        "Attach an anonymous Twitch IRC reader to every auto-recorded "
        "Twitch stream. Writes chat.jsonl sidecar in the output folder. "
        "Kick and other platforms are not supported yet."
    )
    chat_row.addWidget(win.capture_chat_check)
    win.render_chat_ass_check = QCheckBox(".ass sidecar for replay sync")
    win.render_chat_ass_check.setChecked(bool(win._config.get("render_chat_ass", True)))
    win.render_chat_ass_check.setToolTip(
        "In addition to chat.jsonl, write chat.ass next to the video. "
        "VLC/mpv will pick it up automatically on replay."
    )
    chat_row.addWidget(win.render_chat_ass_check)
    chat_row.addStretch(1)
    network_lay.addLayout(chat_row)

    # Browser companion local server (v4.16.0)
    companion_panel, companion_panel_lay = make_dialog_section(
        "Browser Companion",
        "Send URLs from the extension with one click, or open the lightweight web remote for queue and status checks. Keep LAN access off unless another device truly needs it.",
    )
    comp_row = QHBoxLayout()
    comp_row.setSpacing(8)
    win.companion_check = QCheckBox("Enable browser-extension companion (local server)")
    win.companion_check.setChecked(bool(win._config.get("companion_server_enabled", False)))
    win.companion_check.setToolTip(
        "Starts a 127.0.0.1-only HTTP server on a random port. Clients must "
        "exchange a short-lived pairing code for an origin-bound token."
    )
    win.companion_check.toggled.connect(win._on_companion_toggled)
    comp_row.addWidget(win.companion_check)
    win.companion_lan_check = QCheckBox("Enable LAN through HTTPS reverse proxy")
    win.companion_lan_check.setChecked(bool(win._config.get("companion_bind_lan", False)))
    win.companion_lan_check.setToolTip(
        "The app remains loopback-only. A locally managed reverse proxy must "
        "terminate trusted HTTPS and forward to the displayed listener port."
    )
    win.companion_lan_check.toggled.connect(win._on_companion_scope_toggled)
    comp_row.addWidget(win.companion_lan_check)
    comp_row.addStretch(1)
    companion_panel_lay.addLayout(comp_row)

    proxy_origin_row = QHBoxLayout()
    proxy_origin_row.setSpacing(8)
    proxy_origin_row.addWidget(QLabel("HTTPS remote origin:"))
    win.companion_proxy_origin_input = QLineEdit(
        str(win._config.get("companion_proxy_origin", "") or "")
    )
    win.companion_proxy_origin_input.setPlaceholderText(
        "https://streamkeep.example.lan"
    )
    win.companion_proxy_origin_input.setToolTip(
        "Exact public origin served by your trusted local reverse proxy. "
        "HTTP origins, paths, query strings, and broad host patterns are rejected."
    )
    win.companion_proxy_origin_input.editingFinished.connect(
        win._on_companion_proxy_origin_changed
    )
    proxy_origin_row.addWidget(win.companion_proxy_origin_input, 1)
    companion_panel_lay.addLayout(proxy_origin_row)

    win.companion_status_banner, win.companion_status_title, win.companion_status_body = (
        make_status_banner()
    )
    companion_panel_lay.addWidget(win.companion_status_banner)

    companion_metrics = QHBoxLayout()
    companion_metrics.setSpacing(10)
    scope_card, win.companion_scope_value, win.companion_scope_sub = make_metric_card(
        "Access scope",
        "Local only",
        "Recommended",
    )
    remote_card, win.companion_remote_value, win.companion_remote_sub = make_metric_card(
        "Web remote",
        "Off",
        "Local control",
    )
    token_card, win.companion_token_value, win.companion_token_sub = make_metric_card(
        "Pairing code",
        "Waiting",
        "5-minute code",
    )
    companion_metrics.addWidget(scope_card)
    companion_metrics.addWidget(remote_card)
    companion_metrics.addWidget(token_card)
    companion_panel_lay.addLayout(companion_metrics)

    endpoint_row = QHBoxLayout()
    endpoint_row.setSpacing(8)
    endpoint_row.addWidget(QLabel("Web remote:"))
    win.companion_url_display = QLineEdit("")
    win.companion_url_display.setReadOnly(True)
    win.companion_url_display.setPlaceholderText("Enable the companion server to expose a local URL")
    win.companion_url_display.setToolTip(
        "Open the loopback URL on this PC, or the configured HTTPS origin from a paired LAN client."
    )
    endpoint_row.addWidget(win.companion_url_display, 1)
    win.companion_copy_url_btn = QPushButton("Copy")
    win.companion_copy_url_btn.setObjectName("secondary")
    win.companion_copy_url_btn.setFixedWidth(74)
    win.companion_copy_url_btn.clicked.connect(win._on_copy_companion_url)
    endpoint_row.addWidget(win.companion_copy_url_btn)
    win.companion_open_url_btn = QPushButton("Open")
    win.companion_open_url_btn.setObjectName("secondary")
    win.companion_open_url_btn.setFixedWidth(82)
    win.companion_open_url_btn.clicked.connect(win._on_open_companion_remote)
    endpoint_row.addWidget(win.companion_open_url_btn)
    companion_panel_lay.addLayout(endpoint_row)

    comp_token_row = QHBoxLayout()
    comp_token_row.setSpacing(8)
    comp_token_row.addWidget(QLabel("One-time pairing code:"))
    win.companion_token_display = QLineEdit("")
    win.companion_token_display.setReadOnly(True)
    win.companion_token_display.setPlaceholderText("Select New code after the server starts")
    win.companion_token_display.setToolTip(
        "Use this once in the extension or web remote. It expires after five "
        "minutes and is never placed in a URL or log."
    )
    comp_token_row.addWidget(win.companion_token_display, 1)
    win.companion_copy_token_btn = QPushButton("Copy code")
    win.companion_copy_token_btn.setObjectName("secondary")
    win.companion_copy_token_btn.setFixedWidth(108)
    win.companion_copy_token_btn.clicked.connect(win._on_copy_companion_token)
    comp_token_row.addWidget(win.companion_copy_token_btn)
    win.companion_rotate_token_btn = QPushButton("New code")
    win.companion_rotate_token_btn.setObjectName("secondary")
    win.companion_rotate_token_btn.setFixedWidth(82)
    win.companion_rotate_token_btn.setToolTip(
        "Generate a one-use pairing code valid for five minutes."
    )
    win.companion_rotate_token_btn.clicked.connect(
        win._on_generate_companion_pairing_code
    )
    comp_token_row.addWidget(win.companion_rotate_token_btn)
    win.companion_revoke_tokens_btn = QPushButton("Revoke all")
    win.companion_revoke_tokens_btn.setObjectName("secondary")
    win.companion_revoke_tokens_btn.setFixedWidth(92)
    win.companion_revoke_tokens_btn.setToolTip(
        "Invalidate every paired client and rotate the secure master token."
    )
    win.companion_revoke_tokens_btn.clicked.connect(win._on_rotate_companion_token)
    comp_token_row.addWidget(win.companion_revoke_tokens_btn)
    companion_panel_lay.addLayout(comp_token_row)

    token_inventory_label = QLabel("Active scoped tokens")
    token_inventory_label.setObjectName("subtleText")
    companion_panel_lay.addWidget(token_inventory_label)
    win.companion_tokens_table = QTableWidget(0, 6)
    win.companion_tokens_table.setHorizontalHeaderLabels(
        ["Label", "Scopes", "Origin", "Created", "Last used", "Action"]
    )
    win.companion_tokens_table.setEditTriggers(
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    win.companion_tokens_table.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows
    )
    win.companion_tokens_table.verticalHeader().setVisible(False)
    win.companion_tokens_table.setFixedHeight(170)
    token_header = win.companion_tokens_table.horizontalHeader()
    token_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    token_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    token_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    token_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    token_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
    token_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
    companion_panel_lay.addWidget(win.companion_tokens_table)

    companion_hint = QLabel(
        "The master token is generated with 256 bits and kept in the operating-system secure store. This inventory shows only scoped-token metadata; bearer values are never displayed. Mutating calls are nonce-protected."
    )
    companion_hint.setObjectName("subtleText")
    companion_hint.setWordWrap(True)
    companion_panel_lay.addWidget(companion_hint)
    network_lay.addWidget(companion_panel)

    # Notifications sound cue (v4.17.0)
    notif_row = QHBoxLayout()
    notif_row.setSpacing(8)
    win.notif_sound_check = QCheckBox("Audible beep on notification events")
    win.notif_sound_check.setChecked(bool(win._config.get("notif_sound", False)))
    win.notif_sound_check.setToolTip(
        "Play the system beep when a notable event fires (live detected, "
        "download complete, error). The Notifications bell in the header "
        "always updates regardless of this setting."
    )
    notif_row.addWidget(win.notif_sound_check)
    notif_row.addStretch(1)
    network_lay.addLayout(notif_row)

    # Native OS notifications (F80)
    native_notif_row = QHBoxLayout()
    native_notif_row.setSpacing(8)
    win.native_notif_check = QCheckBox("Native OS notifications on completion, live, and failure")
    win.native_notif_check.setChecked(bool(win._config.get("native_notifications", False)))
    win.native_notif_check.setToolTip(
        "Raise a desktop notification (Windows Toast / macOS / Linux) for "
        "notable events. Falls back to the tray icon when no native backend "
        "is installed. Suppressed while the StreamKeep window is focused."
    )
    native_notif_row.addWidget(win.native_notif_check)
    native_notif_row.addStretch(1)
    network_lay.addLayout(native_notif_row)

    # Windows shell integration (V135).  These controls are harmless on other
    # platforms and the adapters degrade silently when the OS surface is not
    # available.
    win.taskbar_progress_check = QCheckBox(
        "Show aggregate queue progress on the Windows taskbar"
    )
    win.taskbar_progress_check.setChecked(
        bool(win._config.get("taskbar_progress", True))
    )
    win.taskbar_progress_check.setToolTip(
        "Use the top-level window's taskbar button for aggregate queue "
        "progress, paused state, and failed-job state. No package identity or "
        "signing is required; unsupported systems are ignored."
    )
    network_lay.addWidget(win.taskbar_progress_check)

    win.windows_progress_notifications_check = QCheckBox(
        "Use one updating Windows progress notification for long queues"
    )
    win.windows_progress_notifications_check.setChecked(
        bool(win._config.get("windows_progress_notifications", False))
    )
    win.windows_progress_notifications_check.setToolTip(
        "When native notifications are enabled, update one progress-bound "
        "notification for queues of two or more items. The optional Windows "
        "notification bridge may be unavailable in unsigned builds."
    )
    network_lay.addWidget(win.windows_progress_notifications_check)

    # Storage health monitor (F67)
    win.disk_monitor_check = QCheckBox("Monitor free space on the download drive")
    win.disk_monitor_check.setChecked(bool(win._config.get("disk_monitor_enabled", True)))
    win.disk_monitor_check.setToolTip(
        "Poll the output drive and show remaining free space in the status bar, "
        "warning before it runs out."
    )
    network_lay.addWidget(win.disk_monitor_check)

    disk_thresh_row = QHBoxLayout()
    disk_thresh_row.setSpacing(8)
    disk_thresh_row.addWidget(QLabel("Warn under"))
    win.disk_warning_spin = QSpinBox()
    win.disk_warning_spin.setRange(1, 10000)
    win.disk_warning_spin.setSuffix(" GB")
    win.disk_warning_spin.setValue(int(win._config.get("disk_warning_gb", 20) or 20))
    disk_thresh_row.addWidget(win.disk_warning_spin)
    disk_thresh_row.addWidget(QLabel("Critical under"))
    win.disk_critical_spin = QSpinBox()
    win.disk_critical_spin.setRange(1, 10000)
    win.disk_critical_spin.setSuffix(" GB")
    win.disk_critical_spin.setValue(int(win._config.get("disk_critical_gb", 5) or 5))
    disk_thresh_row.addWidget(win.disk_critical_spin)
    disk_thresh_row.addStretch(1)
    network_lay.addLayout(disk_thresh_row)

    win.disk_auto_pause_check = QCheckBox("Auto-pause new downloads when space is critically low")
    win.disk_auto_pause_check.setChecked(bool(win._config.get("disk_auto_pause", False)))
    win.disk_auto_pause_check.setToolTip(
        "Stop the active download and hold the queue while free space is below "
        "the critical threshold. The queue resumes automatically once space recovers."
    )
    network_lay.addWidget(win.disk_auto_pause_check)

    win.pause_queue_power_check = QCheckBox(
        "Pause new queue jobs on battery or Energy Saver"
    )
    win.pause_queue_power_check.setChecked(
        bool(win._config.get("pause_queue_on_power", False))
    )
    win.pause_queue_power_check.setToolTip(
        "Hold pending queue jobs while Windows reports battery power or Energy "
        "Saver. Active downloads finish, the decision is logged, and the queue "
        "resumes automatically when AC power returns."
    )
    network_lay.addWidget(win.pause_queue_power_check)

    # Queue-complete power action (V24)
    from ...power import POWER_ACTIONS
    _POWER_ACTION_LABELS = {
        "none": "Do nothing",
        "notify": "Notify only",
        "run-hook": "Run 'queue_complete' hook",
        "lock": "Lock the workstation",
        "sleep": "Sleep",
        "hibernate": "Hibernate",
        "shutdown": "Shut down (cancellable)",
    }
    power_row = QHBoxLayout()
    power_row.setSpacing(8)
    power_row.addWidget(QLabel("When the download queue finishes:"))
    win.queue_complete_action_combo = QComboBox()
    for _action in POWER_ACTIONS:
        win.queue_complete_action_combo.addItem(
            _POWER_ACTION_LABELS.get(_action, _action), _action
        )
    _saved_power = str(win._config.get("queue_complete_action", "none") or "none")
    _power_idx = win.queue_complete_action_combo.findData(_saved_power)
    win.queue_complete_action_combo.setCurrentIndex(max(0, _power_idx))
    win.queue_complete_action_combo.setToolTip(
        "Optional action to run once, after the whole queue drains. Sleep, "
        "hibernate, and shutdown are issued with a native cancellable delay "
        "(Windows: run 'shutdown /a' to abort). Default: do nothing."
    )
    power_row.addWidget(win.queue_complete_action_combo, 1)
    network_lay.addLayout(power_row)

    # Auto-update checker (v4.16.0)
    update_row = QHBoxLayout()
    update_row.setSpacing(8)
    win.update_check_check = QCheckBox("Check for updates on startup")
    win.update_check_check.setChecked(bool(win._config.get("check_for_updates", False)))
    win.update_check_check.setToolTip(
        "Once per launch, asks GitHub whether a newer StreamKeep release "
        "is available. The check is opt-in; downloads and installs still "
        "require explicit confirmation."
    )
    update_row.addWidget(win.update_check_check)
    update_row.addStretch(1)
    network_lay.addLayout(update_row)

    # Load saved network settings
    saved_rate = win._config.get("rate_limit", "")
    saved_proxy = win._config.get("proxy", "")
    if saved_rate:
        win.rate_limit_input.setText(saved_rate)
        YtDlpExtractor.rate_limit = saved_rate
    if saved_proxy:
        win.proxy_input.setText(saved_proxy)
        YtDlpExtractor.proxy = saved_proxy
        set_native_proxy(saved_proxy)
    if hasattr(win, "_refresh_companion_ui"):
        win._refresh_companion_ui()

    card_lay.addWidget(network_block)

    # ── YouTube extras ─────────────────────────────────────────────
    yt_block, yt_lay = make_field_block(
        "yt-dlp Extras", "Optional features for compatible yt-dlp sources."
    )
    win.subs_check = QCheckBox("Download subtitles by default")
    subs_languages_row = QHBoxLayout()
    subs_languages_row.setSpacing(8)
    subs_languages_label = QLabel("Languages:")
    subs_languages_label.setFixedWidth(100)
    subs_languages_row.addWidget(subs_languages_label)
    win.subs_languages_input = QLineEdit(
        str(win._config.get("subtitle_languages", "en.*,en") or "")
    )
    win.subs_languages_input.setPlaceholderText("yt-dlp expression, e.g. en.*,es")
    win.subs_languages_input.setToolTip(
        "Comma-separated language codes or yt-dlp regex patterns. "
        "Per-download source languages are selectable in Advanced."
    )
    subs_languages_row.addWidget(win.subs_languages_input, 1)
    win.subs_auto_check = QCheckBox("Include automatic captions")
    win.subs_auto_check.setChecked(bool(win._config.get("subtitle_auto", True)))
    subs_languages_row.addWidget(win.subs_auto_check)

    subs_output_row = QHBoxLayout()
    subs_output_row.setSpacing(8)
    subs_output_label = QLabel("Output:")
    subs_output_label.setFixedWidth(100)
    subs_output_row.addWidget(subs_output_label)
    win.subs_convert_combo = QComboBox()
    win.subs_convert_combo.addItem("Keep source subtitle format", userData="")
    for sub_format in ("srt", "vtt", "ass"):
        win.subs_convert_combo.addItem(
            f"Convert to {sub_format.upper()}", userData=sub_format
        )
    saved_sub_convert = str(win._config.get("subtitle_convert", "") or "")
    for index in range(win.subs_convert_combo.count()):
        if win.subs_convert_combo.itemData(index) == saved_sub_convert:
            win.subs_convert_combo.setCurrentIndex(index)
            break
    subs_output_row.addWidget(win.subs_convert_combo, 1)
    win.subs_delivery_combo = QComboBox()
    win.subs_delivery_combo.addItem("Embed in video", userData="embed")
    win.subs_delivery_combo.addItem("Keep as sidecar files", userData="sidecar")
    if not bool(win._config.get("subtitle_embed", True)):
        win.subs_delivery_combo.setCurrentIndex(1)
    subs_output_row.addWidget(win.subs_delivery_combo, 1)
    win.capture_youtube_chat_check = QCheckBox(
        "Capture YouTube live-chat replay (live_chat.json) for VODs"
    )
    win.capture_youtube_chat_check.setChecked(
        bool(win._config.get("capture_youtube_chat", False))
    )
    win.capture_youtube_chat_check.setToolTip(
        "For eligible YouTube VODs, also fetch the live-chat replay. It is "
        "normalized into StreamKeep's chat pipeline at finalize; unavailable "
        "replay is non-fatal."
    )
    win.capture_comments_check = QCheckBox(
        "Archive YouTube comments for VODs"
    )
    win.capture_comments_check.setChecked(
        bool(win._config.get("capture_comments", False))
    )
    win.capture_comments_check.setToolTip(
        "Opt in per download to retain public comment text and published author "
        "names. Refused or rate-limited comments are recorded as unavailable "
        "without failing the media download."
    )
    comments_limits_row = QHBoxLayout()
    comments_limits_row.setSpacing(8)
    comments_limits_row.addWidget(QLabel("Comment bounds:"))
    from ...metadata import DEFAULT_COMMENT_MAX_COUNT, DEFAULT_COMMENT_MAX_BYTES
    win.comment_max_count_spin = QSpinBox()
    win.comment_max_count_spin.setRange(1, 100000)
    win.comment_max_count_spin.setValue(int(win._config.get(
        "comment_max_count", DEFAULT_COMMENT_MAX_COUNT
    ) or DEFAULT_COMMENT_MAX_COUNT))
    win.comment_max_count_spin.setSuffix(" comments")
    win.comment_max_count_spin.setToolTip(
        "Maximum number of comments written per recording."
    )
    comments_limits_row.addWidget(win.comment_max_count_spin)
    win.comment_max_bytes_spin = QSpinBox()
    win.comment_max_bytes_spin.setRange(1, 4 * 1024 * 1024)
    win.comment_max_bytes_spin.setSingleStep(64 * 1024)
    win.comment_max_bytes_spin.setValue(int(win._config.get(
        "comment_max_bytes", DEFAULT_COMMENT_MAX_BYTES
    ) or DEFAULT_COMMENT_MAX_BYTES))
    win.comment_max_bytes_spin.setSuffix(" bytes")
    win.comment_max_bytes_spin.setToolTip(
        "Maximum UTF-8 size of the versioned comments sidecar."
    )
    comments_limits_row.addWidget(win.comment_max_bytes_spin)
    comments_limits_row.addStretch(1)
    win.sponsorblock_check = QCheckBox("Enable SponsorBlock by default")
    yt_lay.addWidget(win.subs_check)
    yt_lay.addLayout(subs_languages_row)
    yt_lay.addLayout(subs_output_row)
    yt_lay.addWidget(win.capture_youtube_chat_check)
    yt_lay.addWidget(win.capture_comments_check)
    yt_lay.addLayout(comments_limits_row)
    yt_lay.addWidget(win.sponsorblock_check)

    sb_delay_row = QHBoxLayout()
    sb_delay_row.setSpacing(8)
    sb_delay_row.addWidget(QLabel("Delay subscribed downloads by"))
    win.sponsorblock_delay_spin = QSpinBox()
    win.sponsorblock_delay_spin.setRange(0, 336)  # up to 14 days
    win.sponsorblock_delay_spin.setSuffix(" h after publish")
    win.sponsorblock_delay_spin.setValue(int(win._config.get("sponsorblock_delay_hours", 0) or 0))
    win.sponsorblock_delay_spin.setToolTip(
        "Hold auto-discovered (subscribed-channel) YouTube VODs for this many "
        "hours after they were published before downloading, so SponsorBlock "
        "crowd-sourced segments have time to accumulate. 0 disables the delay. "
        "Already-old VODs and quality upgrades download immediately."
    )
    sb_delay_row.addWidget(win.sponsorblock_delay_spin)
    sb_delay_row.addStretch(1)
    yt_lay.addLayout(sb_delay_row)

    from ...download_options import (
        SPONSORBLOCK_CATEGORIES, SPONSORBLOCK_LEGACY_REMOVE,
        SPONSORBLOCK_NON_REMOVABLE,
    )
    win.sponsorblock_table = QTableWidget()
    win.sponsorblock_table.setColumnCount(2)
    win.sponsorblock_table.setHorizontalHeaderLabels(["Category", "Action"])
    win.sponsorblock_table.setRowCount(len(SPONSORBLOCK_CATEGORIES))
    win.sponsorblock_table.verticalHeader().setVisible(False)
    win.sponsorblock_table.horizontalHeader().setSectionResizeMode(
        0, QHeaderView.ResizeMode.Stretch
    )
    win.sponsorblock_table.horizontalHeader().setSectionResizeMode(
        1, QHeaderView.ResizeMode.Fixed
    )
    win.sponsorblock_table.setColumnWidth(1, 170)
    win.sponsorblock_table.setFixedHeight(310)
    win.sponsorblock_action_combos = {}
    saved_sponsor_enabled = bool(win._config.get("sponsorblock", False))
    saved_sponsor_mark = str(win._config.get("sponsorblock_mark", "") or "")
    saved_sponsor_remove = str(
        win._config.get("sponsorblock_remove", "") or ""
    ) if "sponsorblock_remove" in win._config else (
        SPONSORBLOCK_LEGACY_REMOVE if saved_sponsor_enabled else ""
    )
    marked = set(saved_sponsor_mark.split(","))
    removed = set(saved_sponsor_remove.split(","))
    for row, (category, label) in enumerate(SPONSORBLOCK_CATEGORIES.items()):
        item = QTableWidgetItem(label)
        item.setToolTip(category)
        win.sponsorblock_table.setItem(row, 0, item)
        combo = QComboBox()
        combo.addItem("Ignore", userData="")
        combo.addItem("Mark chapter", userData="mark")
        if category not in SPONSORBLOCK_NON_REMOVABLE:
            combo.addItem("Remove segment", userData="remove")
        if category in removed and category not in SPONSORBLOCK_NON_REMOVABLE:
            combo.setCurrentIndex(2)
        elif category in marked:
            combo.setCurrentIndex(1)
        win.sponsorblock_table.setCellWidget(row, 1, combo)
        win.sponsorblock_action_combos[category] = combo
    yt_lay.addWidget(win.sponsorblock_table)

    sponsor_api_row = QHBoxLayout()
    sponsor_api_row.setSpacing(8)
    sponsor_api_label = QLabel("API URL:")
    sponsor_api_label.setFixedWidth(100)
    sponsor_api_row.addWidget(sponsor_api_label)
    win.sponsorblock_api_input = QLineEdit(
        str(win._config.get("sponsorblock_api", "") or "")
    )
    win.sponsorblock_api_input.setPlaceholderText(
        "https://sponsor.ajay.app (blank = yt-dlp default)"
    )
    sponsor_api_row.addWidget(win.sponsorblock_api_input, 1)
    yt_lay.addLayout(sponsor_api_row)

    # Resolve timeout (config-only escape hatch): post-live manifestless YouTube
    # VODs can take minutes to fully extract. Shared clamp with the CLI paths.
    from ...extractors.ytdlp import apply_resolve_timeout_config
    apply_resolve_timeout_config(win._config)

    win.subs_check.setChecked(bool(win._config.get("download_subs", False)))
    YtDlpExtractor.download_subs = win.subs_check.isChecked()
    YtDlpExtractor.capture_youtube_chat = win.capture_youtube_chat_check.isChecked()
    YtDlpExtractor.capture_comments = win.capture_comments_check.isChecked()
    YtDlpExtractor.subtitle_languages = win.subs_languages_input.text()
    YtDlpExtractor.subtitle_auto = win.subs_auto_check.isChecked()
    YtDlpExtractor.subtitle_convert = saved_sub_convert
    YtDlpExtractor.subtitle_embed = (
        win.subs_delivery_combo.currentData() == "embed"
    )
    def _toggle_subtitle_defaults(enabled):
        win.subs_languages_input.setEnabled(enabled)
        win.subs_auto_check.setEnabled(enabled)
        win.subs_convert_combo.setEnabled(enabled)
        win.subs_delivery_combo.setEnabled(enabled)
    win.subs_check.toggled.connect(_toggle_subtitle_defaults)
    _toggle_subtitle_defaults(win.subs_check.isChecked())
    win.sponsorblock_check.setChecked(saved_sponsor_enabled)
    YtDlpExtractor.sponsorblock = saved_sponsor_enabled
    YtDlpExtractor.sponsorblock_mark = saved_sponsor_mark
    YtDlpExtractor.sponsorblock_remove = saved_sponsor_remove
    YtDlpExtractor.sponsorblock_api = win.sponsorblock_api_input.text()

    def _toggle_sponsorblock_defaults(enabled):
        win.sponsorblock_table.setEnabled(enabled)
        win.sponsorblock_api_input.setEnabled(enabled)
    win.sponsorblock_check.toggled.connect(_toggle_sponsorblock_defaults)
    _toggle_sponsorblock_defaults(saved_sponsor_enabled)

    # YouTube player_client strategy (V19) — the single most effective knob
    # when YouTube caps quality, demands sign-in, or a download breaks.
    from ...extractors.ytdlp import YOUTUBE_PLAYER_CLIENT_PRESETS
    pc_row = QHBoxLayout()
    pc_row.setSpacing(8)
    pc_label = QLabel("YouTube client:")
    pc_label.setFixedWidth(100)
    pc_row.addWidget(pc_label)
    win.youtube_client_combo = QComboBox()
    win.youtube_client_combo.setToolTip(
        "Which player client yt-dlp impersonates for YouTube. Change this if "
        "YouTube caps quality, demands sign-in, or a working download breaks. "
        "Run 'StreamKeep.py youtube-health' for a full capability report."
    )
    _seen_pc_labels = set()
    for pc_key, (pc_label_text, _pc_value) in YOUTUBE_PLAYER_CLIENT_PRESETS.items():
        if pc_key == "default" or pc_label_text in _seen_pc_labels:
            continue  # "" already supplies the Automatic entry
        _seen_pc_labels.add(pc_label_text)
        win.youtube_client_combo.addItem(pc_label_text, userData=pc_key)
    saved_pc = str(win._config.get("youtube_player_client", "") or "")
    saved_pc_idx = win.youtube_client_combo.findData(saved_pc)
    if saved_pc_idx >= 0:
        win.youtube_client_combo.setCurrentIndex(saved_pc_idx)
    YtDlpExtractor.youtube_player_client = saved_pc
    pc_row.addWidget(win.youtube_client_combo, 1)
    win.youtube_health_btn = QPushButton("Test capability")
    win.youtube_health_btn.setObjectName("secondary")
    win.youtube_health_btn.setToolTip(
        "Check yt-dlp/JS runtime readiness and PO-token provider presence, and "
        "show remediation steps if YouTube is likely to return capped or "
        "image-only results."
    )
    win.youtube_health_btn.clicked.connect(win._on_test_youtube_capability)
    pc_row.addWidget(win.youtube_health_btn)
    yt_lay.addLayout(pc_row)

    # PO-token provider lifecycle (V33). yt-dlp can use a provider but cannot
    # start one; this row installs/launches it and reports whether it answers.
    from ...pot_provider import CONFIG_BASE_URL_KEY, CONFIG_COMMAND_KEY
    pot_row = QHBoxLayout()
    pot_row.setSpacing(8)
    pot_label = QLabel("PO-token URL:")
    pot_label.setFixedWidth(100)
    pot_row.addWidget(pot_label)
    win.pot_base_url_input = QLineEdit(
        str(win._config.get(CONFIG_BASE_URL_KEY, "") or "")
    )
    win.pot_base_url_input.setPlaceholderText("http://127.0.0.1:4416 (loopback only)")
    win.pot_base_url_input.setToolTip(
        "Where the local PO-token provider answers. Only loopback addresses "
        "are accepted: a provider handles account-bound tokens."
    )
    pot_row.addWidget(win.pot_base_url_input, 1)
    win.pot_setup_btn = QPushButton("Set up provider")
    win.pot_setup_btn.setObjectName("secondary")
    win.pot_setup_btn.setToolTip(
        "Install the provider plugin where possible, start the configured "
        "local server, then re-probe. Shows copy-paste steps if it cannot."
    )
    win.pot_setup_btn.clicked.connect(win._on_setup_pot_provider)
    pot_row.addWidget(win.pot_setup_btn)
    yt_lay.addLayout(pot_row)

    pot_cmd_row = QHBoxLayout()
    pot_cmd_row.setSpacing(8)
    pot_cmd_label = QLabel("Launch:")
    pot_cmd_label.setFixedWidth(100)
    pot_cmd_row.addWidget(pot_cmd_label)
    win.pot_command_input = QLineEdit(
        str(win._config.get(CONFIG_COMMAND_KEY, "") or "")
    )
    win.pot_command_input.setPlaceholderText(
        "Optional command that starts the provider server"
    )
    pot_cmd_row.addWidget(win.pot_command_input, 1)
    yt_lay.addLayout(pot_cmd_row)

    win.pot_status_label = QLabel("")
    win.pot_status_label.setObjectName("subtleText")
    win.pot_status_label.setWordWrap(True)
    yt_lay.addWidget(win.pot_status_label)

    # Optional remote cipher/PO-token backend (V32). A trusted plugin owns
    # the remote protocol; the host only validates the extractor args it
    # returns and continues locally when the plugin is absent or unavailable.
    from ...youtube_backend import (
        REMOTE_BACKEND_MODE_KEY,
        REMOTE_BACKEND_URL_KEY,
        normalize_backend_mode,
    )
    remote_mode_row = QHBoxLayout()
    remote_mode_row.setSpacing(8)
    remote_mode_label = QLabel("Remote backend:")
    remote_mode_label.setFixedWidth(100)
    remote_mode_row.addWidget(remote_mode_label)
    win.remote_backend_mode_combo = QComboBox()
    win.remote_backend_mode_combo.addItem("Off (local only)", userData="off")
    win.remote_backend_mode_combo.addItem("Auto (optional)", userData="auto")
    win.remote_backend_mode_combo.addItem(
        "Required (warn if unavailable)", userData="required"
    )
    saved_backend_mode = normalize_backend_mode(
        win._config.get(REMOTE_BACKEND_MODE_KEY, "off")
    )
    saved_backend_index = win.remote_backend_mode_combo.findData(saved_backend_mode)
    if saved_backend_index >= 0:
        win.remote_backend_mode_combo.setCurrentIndex(saved_backend_index)
    win.remote_backend_mode_combo.setToolTip(
        "Use a trusted youtube_backend plugin to ask a remote helper for "
        "cipher or PO-token extractor arguments. Missing helpers fail open."
    )
    remote_mode_row.addWidget(win.remote_backend_mode_combo, 1)
    win.remote_backend_test_btn = QPushButton("Test")
    win.remote_backend_test_btn.setObjectName("secondary")
    win.remote_backend_test_btn.setToolTip(
        "Probe the configured remote backend and show its redacted health status."
    )
    win.remote_backend_test_btn.clicked.connect(win._on_test_remote_backend)
    remote_mode_row.addWidget(win.remote_backend_test_btn)
    yt_lay.addLayout(remote_mode_row)

    remote_url_row = QHBoxLayout()
    remote_url_row.setSpacing(8)
    remote_url_label = QLabel("Backend URL:")
    remote_url_label.setFixedWidth(100)
    remote_url_row.addWidget(remote_url_label)
    win.remote_backend_url_input = QLineEdit(
        str(win._config.get(REMOTE_BACKEND_URL_KEY, "") or "")
    )
    win.remote_backend_url_input.setPlaceholderText(
        "https://helper.example.invalid/solve"
    )
    win.remote_backend_url_input.setToolTip(
        "The endpoint passed to the trusted youtube_backend plugin. Credentials "
        "and cookies are never sent by StreamKeep."
    )
    remote_url_row.addWidget(win.remote_backend_url_input, 1)
    yt_lay.addLayout(remote_url_row)

    win.remote_backend_status_label = QLabel(
        "Remote backend is disabled."
        if saved_backend_mode == "off"
        else "Not tested. Use Test capability to probe the configured backend."
    )
    win.remote_backend_status_label.setObjectName("subtleText")
    win.remote_backend_status_label.setWordWrap(True)
    yt_lay.addWidget(win.remote_backend_status_label)

    card_lay.addWidget(yt_block)

    # ── Filename templates ─────────────────────────────────────────
    tpl_block, tpl_lay = make_field_block(
        "Filename Templates",
        "Variables: {title} {channel} {platform} {date} {year} {month} {day}. "
        "Use / to create subfolders. Each segment is sanitized.",
    )
    folder_row = QHBoxLayout()
    folder_row.setSpacing(8)
    folder_label = QLabel("Folder:")
    folder_label.setFixedWidth(100)
    folder_row.addWidget(folder_label)
    win.folder_template_input = QLineEdit(win._folder_template)
    win.folder_template_input.setPlaceholderText(DEFAULT_FOLDER_TEMPLATE)
    folder_row.addWidget(win.folder_template_input, 1)
    tpl_lay.addLayout(folder_row)
    file_row = QHBoxLayout()
    file_row.setSpacing(8)
    file_label = QLabel("Filename:")
    file_label.setFixedWidth(100)
    file_row.addWidget(file_label)
    win.file_template_input = QLineEdit(win._file_template)
    win.file_template_input.setPlaceholderText(DEFAULT_FILE_TEMPLATE)
    file_row.addWidget(win.file_template_input, 1)
    tpl_lay.addLayout(file_row)

    # ── Live preview (F12) ────────────────────────────────────────
    _sample_ctx = {
        "title": "Just Chatting Marathon",
        "channel": "xQc",
        "platform": "twitch",
        "date": "2026-04-12",
        "year": "2026",
        "month": "04",
        "day": "12",
        "id": "v2098765432",
        "quality": "1080p60",
        "ext": "mp4",
    }
    preview_row = QHBoxLayout()
    preview_row.setSpacing(8)
    plabel = QLabel("Preview:")
    plabel.setFixedWidth(100)
    plabel.setObjectName("subtleText")
    preview_row.addWidget(plabel)
    win._template_preview = QLabel()
    win._template_preview.setObjectName("templatePreview")
    win._template_preview.setProperty("tone", "success")
    win._template_preview.setWordWrap(True)
    preview_row.addWidget(win._template_preview, 1)
    tpl_lay.addLayout(preview_row)

    def _update_template_preview():
        folder_tpl = win.folder_template_input.text().strip() or DEFAULT_FOLDER_TEMPLATE
        file_tpl = win.file_template_input.text().strip() or DEFAULT_FILE_TEMPLATE
        try:
            folder_parts = _render_template(folder_tpl, _sample_ctx)
            file_parts = _render_template(file_tpl, _sample_ctx)
            path = "/".join(folder_parts + file_parts) + ".mp4"
            win._template_preview.setText(path)
            win._template_preview.setProperty("tone", "success")
        except Exception:
            win._template_preview.setText("Invalid template")
            win._template_preview.setProperty("tone", "error")
        win._template_preview.style().unpolish(win._template_preview)
        win._template_preview.style().polish(win._template_preview)

    win.folder_template_input.textChanged.connect(lambda: _update_template_preview())
    win.file_template_input.textChanged.connect(lambda: _update_template_preview())
    _update_template_preview()

    card_lay.addWidget(tpl_block)

    # ── Webhook ────────────────────────────────────────────────────
    hook_block, hook_lay = make_field_block(
        "Webhook Notifications",
        "POST a JSON payload when downloads complete. Discord webhook URLs "
        "are auto-detected and formatted as embeds.",
    )
    win.webhook_input = QLineEdit(win._webhook_url)
    win.webhook_input.setPlaceholderText(
        "https://discord.com/api/webhooks/... or any POST endpoint"
    )
    hook_lay.addWidget(win.webhook_input)
    win._webhook_type_label = QLabel("")
    win._webhook_type_label.setObjectName("subtleText")
    hook_lay.addWidget(win._webhook_type_label)
    win.webhook_input.textChanged.connect(
        lambda text: _update_webhook_indicator(win, text))
    _update_webhook_indicator(win, win._webhook_url)
    card_lay.addWidget(hook_block)

    # ── Smart Mode URL profiles (V16) ─────────────────────────────
    smart_block, smart_lay = make_field_block(
        "Smart Mode URL Profiles",
        "Select the first matching URL profile automatically. Profiles only "
        "store URL patterns and safe download preferences; they never run a command.",
    )
    win.smart_mode_settings_check = QCheckBox(
        "Enable Smart Mode for new downloads and queue items"
    )
    win.smart_mode_settings_check.setChecked(
        bool(win._config.get("smart_mode", False))
    )
    win.smart_mode_settings_check.toggled.connect(
        lambda checked: win._on_smart_mode_toggled(checked, source="settings")
    )
    smart_lay.addWidget(win.smart_mode_settings_check)

    profile_pick_row = QHBoxLayout()
    profile_pick_row.addWidget(QLabel("Profile:"))
    win.smart_profile_combo = QComboBox()
    win.smart_profile_combo.currentIndexChanged.connect(
        lambda _idx: win._on_smart_profile_selected()
    )
    profile_pick_row.addWidget(win.smart_profile_combo, 1)
    win.smart_profile_enabled_check = QCheckBox("Enabled")
    win.smart_profile_enabled_check.setAccessibleName("Smart profile enabled")
    win.smart_profile_enabled_check.setChecked(True)
    profile_pick_row.addWidget(win.smart_profile_enabled_check)
    smart_lay.addLayout(profile_pick_row)

    profile_name_row = QHBoxLayout()
    profile_name_row.addWidget(QLabel("Name:"))
    win.smart_profile_name_input = QLineEdit()
    win.smart_profile_name_input.setPlaceholderText("e.g. YouTube archive")
    profile_name_row.addWidget(win.smart_profile_name_input, 1)
    smart_lay.addLayout(profile_name_row)

    smart_lay.addWidget(QLabel("URL patterns (one per line; * is a wildcard):"))
    win.smart_profile_patterns_edit = QPlainTextEdit()
    win.smart_profile_patterns_edit.setPlaceholderText(
        "https://www.youtube.com/*\nhttps://youtu.be/*"
    )
    win.smart_profile_patterns_edit.setFixedHeight(76)
    smart_lay.addWidget(win.smart_profile_patterns_edit)

    profile_grid = QGridLayout()
    profile_grid.setHorizontalSpacing(10)
    profile_grid.setVerticalSpacing(6)
    profile_grid.addWidget(QLabel("Output folder:"), 0, 0)
    win.smart_profile_output_input = QLineEdit()
    win.smart_profile_output_input.setPlaceholderText("Optional base output folder")
    profile_grid.addWidget(win.smart_profile_output_input, 0, 1)
    profile_grid.addWidget(QLabel("Quality:"), 0, 2)
    win.smart_profile_quality_input = QLineEdit()
    win.smart_profile_quality_input.setPlaceholderText("best, 1080p, audio")
    profile_grid.addWidget(win.smart_profile_quality_input, 0, 3)
    profile_grid.addWidget(QLabel("Folder template:"), 1, 0)
    win.smart_profile_folder_input = QLineEdit()
    win.smart_profile_folder_input.setPlaceholderText("{channel}/{date}")
    profile_grid.addWidget(win.smart_profile_folder_input, 1, 1)
    profile_grid.addWidget(QLabel("File template:"), 1, 2)
    win.smart_profile_file_input = QLineEdit()
    win.smart_profile_file_input.setPlaceholderText("{title}")
    profile_grid.addWidget(win.smart_profile_file_input, 1, 3)
    profile_grid.addWidget(QLabel("yt-dlp template:"), 2, 0)
    win.smart_profile_template_input = QLineEdit()
    win.smart_profile_template_input.setPlaceholderText("Named template (optional)")
    profile_grid.addWidget(win.smart_profile_template_input, 2, 1)
    profile_grid.addWidget(QLabel("Auth profile ID:"), 2, 2)
    win.smart_profile_auth_input = QLineEdit()
    win.smart_profile_auth_input.setPlaceholderText("Opaque site-bound ID")
    profile_grid.addWidget(win.smart_profile_auth_input, 2, 3)
    profile_grid.addWidget(QLabel("Proxy:"), 3, 0)
    win.smart_profile_proxy_input = QLineEdit()
    win.smart_profile_proxy_input.setPlaceholderText("Optional per-profile proxy URL")
    profile_grid.addWidget(win.smart_profile_proxy_input, 3, 1, 1, 3)
    smart_lay.addLayout(profile_grid)

    smart_action_row = QHBoxLayout()
    win.smart_profile_status = QLabel("")
    win.smart_profile_status.setObjectName("subtleText")
    win.smart_profile_status.setWordWrap(True)
    smart_action_row.addWidget(win.smart_profile_status, 1)
    win.smart_profile_delete_btn = QPushButton("Delete profile")
    win.smart_profile_delete_btn.setObjectName("secondary")
    win.smart_profile_delete_btn.clicked.connect(win._on_smart_profile_delete)
    smart_action_row.addWidget(win.smart_profile_delete_btn)
    win.smart_profile_save_btn = QPushButton("Save profile")
    win.smart_profile_save_btn.setObjectName("secondary")
    win.smart_profile_save_btn.clicked.connect(win._on_smart_profile_save)
    smart_action_row.addWidget(win.smart_profile_save_btn)
    smart_lay.addLayout(smart_action_row)
    card_lay.addWidget(smart_block)
    win._refresh_smart_profiles()

    # ── Event Hooks (F24) — structured, no-shell actions ──────────
    evt_block, evt_lay = make_field_block(
        "Event Hooks",
        "Run a program on lifecycle events. Each hook is an executable plus "
        "an explicit argument list — no shell. Context is passed only as "
        "environment variables ($SK_EVENT, $SK_TITLE, $SK_CHANNEL, "
        "$SK_PLATFORM, $SK_PATH, $SK_URL, $SK_QUALITY, $SK_ERROR). Legacy "
        "shell-command hooks are disabled until you re-create them here.",
    )
    hook_pick_row = QHBoxLayout()
    hook_pick_row.addWidget(QLabel("Event:"))
    win.hooks_event_combo = QComboBox()
    win.hooks_event_combo.currentIndexChanged.connect(
        lambda _idx: win._on_hook_event_selected()
    )
    hook_pick_row.addWidget(win.hooks_event_combo, 1)
    win.hook_enabled_check = QCheckBox("Enabled")
    win.hook_enabled_check.setChecked(True)
    hook_pick_row.addWidget(win.hook_enabled_check)
    evt_lay.addLayout(hook_pick_row)

    hook_exe_row = QHBoxLayout()
    hook_exe_row.addWidget(QLabel("Executable:"))
    win.hook_executable_input = QLineEdit()
    win.hook_executable_input.setMaxLength(4096)
    win.hook_executable_input.setPlaceholderText(
        "Full path to a program (e.g. C:/Tools/notify.exe)"
    )
    hook_exe_row.addWidget(win.hook_executable_input, 1)
    evt_lay.addLayout(hook_exe_row)

    win.hook_args_edit = QPlainTextEdit()
    win.hook_args_edit.setPlaceholderText(
        "One argument per line. Use $SK_* environment variables for context, "
        "e.g.\n--title\n%SK_TITLE%"
    )
    win.hook_args_edit.setFixedHeight(96)
    evt_lay.addWidget(win.hook_args_edit)

    hook_action_row = QHBoxLayout()
    win.hook_status_label = QLabel("")
    win.hook_status_label.setObjectName("subtleText")
    win.hook_status_label.setWordWrap(True)
    hook_action_row.addWidget(win.hook_status_label, 1)
    win.hook_save_btn = QPushButton("Save action")
    win.hook_save_btn.setObjectName("secondary")
    win.hook_save_btn.clicked.connect(win._on_hook_save)
    hook_action_row.addWidget(win.hook_save_btn)
    evt_lay.addLayout(hook_action_row)
    card_lay.addWidget(evt_block)
    win._refresh_hook_editor()

    # ── Duplicate detection ────────────────────────────────────────
    dup_block, dup_lay = make_field_block(
        "Duplicate Detection",
        "Warn before downloading something already in your history.",
    )
    win.dup_check = QCheckBox("Check history for URL and title matches before download")
    win.dup_check.setChecked(win._check_duplicates)
    dup_lay.addWidget(win.dup_check)
    card_lay.addWidget(dup_block)

    # ── Auto-Cleanup Lifecycle Policies (F32) ─────────────────────
    from ...lifecycle import DEFAULT_POLICY
    lc_block, lc_lay = make_field_block(
        "Auto-Cleanup Lifecycle",
        "Automatically recycle old or watched recordings to reclaim disk space. "
        "Always uses the recycle bin — never permanent delete.",
    )
    lc_cfg = win._config.get("lifecycle", dict(DEFAULT_POLICY))
    win.lc_enable_check = QCheckBox("Enable auto-cleanup after each download")
    win.lc_enable_check.setChecked(bool(lc_cfg.get("enabled")))
    lc_lay.addWidget(win.lc_enable_check)

    lc_days_row = QHBoxLayout()
    lc_days_row.setSpacing(8)
    lc_days_row.addWidget(QLabel("Delete recordings older than"))
    win.lc_max_days_spin = QSpinBox()
    win.lc_max_days_spin.setRange(0, 9999)
    win.lc_max_days_spin.setValue(int(lc_cfg.get("max_days", 0) or 0))
    win.lc_max_days_spin.setSpecialValueText("disabled")
    win.lc_max_days_spin.setFixedWidth(80)
    lc_days_row.addWidget(win.lc_max_days_spin)
    lc_days_row.addWidget(QLabel("days"))
    lc_days_row.addStretch(1)
    lc_lay.addLayout(lc_days_row)

    lc_gb_row = QHBoxLayout()
    lc_gb_row.setSpacing(8)
    lc_gb_row.addWidget(QLabel("Max total storage"))
    win.lc_max_gb_spin = QSpinBox()
    win.lc_max_gb_spin.setRange(0, 99999)
    win.lc_max_gb_spin.setValue(int(lc_cfg.get("max_total_gb", 0) or 0))
    win.lc_max_gb_spin.setSpecialValueText("unlimited")
    win.lc_max_gb_spin.setFixedWidth(80)
    lc_gb_row.addWidget(win.lc_max_gb_spin)
    lc_gb_row.addWidget(QLabel("GB (remove oldest first when exceeded)"))
    lc_gb_row.addStretch(1)
    lc_lay.addLayout(lc_gb_row)

    lc_keep_row = QHBoxLayout()
    lc_keep_row.setSpacing(8)
    lc_keep_row.addWidget(QLabel("Keep only the newest"))
    win.lc_keep_last_spin = QSpinBox()
    win.lc_keep_last_spin.setRange(0, 9999)
    win.lc_keep_last_spin.setValue(int(lc_cfg.get("keep_last_per_source", 0) or 0))
    win.lc_keep_last_spin.setSpecialValueText("all")
    win.lc_keep_last_spin.setFixedWidth(80)
    lc_keep_row.addWidget(win.lc_keep_last_spin)
    lc_keep_row.addWidget(QLabel("recordings per source channel"))
    lc_keep_row.addStretch(1)
    lc_lay.addLayout(lc_keep_row)

    win.lc_watched_check = QCheckBox("Delete watched recordings automatically")
    win.lc_watched_check.setChecked(bool(lc_cfg.get("delete_watched")))
    lc_lay.addWidget(win.lc_watched_check)
    win.lc_fav_exempt_check = QCheckBox("Favorited recordings are exempt from cleanup")
    win.lc_fav_exempt_check.setChecked(bool(lc_cfg.get("favorites_exempt", True)))
    lc_lay.addWidget(win.lc_fav_exempt_check)

    lc_btn_row = QHBoxLayout()
    lc_btn_row.setSpacing(8)
    win.lc_preview_btn = QPushButton("Preview cleanup…")
    win.lc_preview_btn.setObjectName("secondary")
    win.lc_preview_btn.setFixedWidth(150)
    win.lc_preview_btn.clicked.connect(win._on_lifecycle_preview)
    lc_btn_row.addWidget(win.lc_preview_btn)
    win.lc_tombstone_btn = QPushButton("Manage removed media…")
    win.lc_tombstone_btn.setObjectName("secondary")
    win.lc_tombstone_btn.setFixedWidth(180)
    win.lc_tombstone_btn.clicked.connect(win._on_tombstone_manager)
    lc_btn_row.addWidget(win.lc_tombstone_btn)
    lc_btn_row.addStretch(1)
    lc_lay.addLayout(lc_btn_row)
    card_lay.addWidget(lc_block)

    # ── Automatic Backups (V51) ───────────────────────────────────
    from ...backup import BACKUP_CADENCES, backup_settings

    backup_cfg = backup_settings(win._config)
    backup_block, backup_lay = make_field_block(
        "Automatic Backups",
        "Rotate validated .skbackup archives of your profile on a schedule. "
        "Only the app instance that owns queue execution runs the backup.",
    )
    win.auto_backup_check = QCheckBox("Back up this profile automatically")
    win.auto_backup_check.setChecked(backup_cfg["enabled"])
    win.auto_backup_check.setToolTip(
        "Archives config, library, tags, search index and notification history. "
        "Credentials and cookies are never included."
    )
    backup_lay.addWidget(win.auto_backup_check)

    backup_dir_row = QHBoxLayout()
    backup_dir_row.setSpacing(8)
    backup_dir_row.addWidget(QLabel("Destination:"))
    win.auto_backup_dir_input = QLineEdit(backup_cfg["dir"])
    win.auto_backup_dir_input.setPlaceholderText("Backup folder")
    backup_dir_row.addWidget(win.auto_backup_dir_input, 1)
    win.auto_backup_browse_btn = QPushButton("Browse…")
    win.auto_backup_browse_btn.setObjectName("secondary")
    win.auto_backup_browse_btn.setFixedWidth(96)
    win.auto_backup_browse_btn.clicked.connect(win._on_browse_auto_backup_dir)
    backup_dir_row.addWidget(win.auto_backup_browse_btn)
    backup_lay.addLayout(backup_dir_row)

    backup_opt_row = QHBoxLayout()
    backup_opt_row.setSpacing(8)
    backup_opt_row.addWidget(QLabel("Cadence:"))
    win.auto_backup_cadence_combo = QComboBox()
    for name in BACKUP_CADENCES:
        win.auto_backup_cadence_combo.addItem(name.capitalize(), name)
    index = win.auto_backup_cadence_combo.findData(backup_cfg["cadence"])
    win.auto_backup_cadence_combo.setCurrentIndex(max(0, index))
    backup_opt_row.addWidget(win.auto_backup_cadence_combo)
    backup_opt_row.addSpacing(12)
    backup_opt_row.addWidget(QLabel("Keep last:"))
    win.auto_backup_keep_spin = QSpinBox()
    win.auto_backup_keep_spin.setRange(1, 50)
    win.auto_backup_keep_spin.setValue(backup_cfg["keep_last"])
    win.auto_backup_keep_spin.setToolTip(
        "Older archives beyond this count are removed only after a new backup "
        "has been written and validated."
    )
    backup_opt_row.addWidget(win.auto_backup_keep_spin)
    backup_opt_row.addStretch(1)
    backup_lay.addLayout(backup_opt_row)

    win.auto_backup_status_label = QLabel("")
    win.auto_backup_status_label.setObjectName("hintLabel")
    win.auto_backup_status_label.setWordWrap(True)
    backup_lay.addWidget(win.auto_backup_status_label)

    backup_btn_row = QHBoxLayout()
    backup_btn_row.setSpacing(8)
    win.auto_backup_now_btn = QPushButton("Back up now")
    win.auto_backup_now_btn.setObjectName("secondary")
    win.auto_backup_now_btn.setFixedWidth(150)
    win.auto_backup_now_btn.clicked.connect(win._on_backup_now)
    backup_btn_row.addWidget(win.auto_backup_now_btn)
    win.auto_backup_refresh_btn = QPushButton("Refresh status")
    win.auto_backup_refresh_btn.setObjectName("secondary")
    win.auto_backup_refresh_btn.setFixedWidth(150)
    win.auto_backup_refresh_btn.clicked.connect(win._refresh_backup_status)
    backup_btn_row.addWidget(win.auto_backup_refresh_btn)
    backup_btn_row.addStretch(1)
    backup_lay.addLayout(backup_btn_row)
    card_lay.addWidget(backup_block)
    win._refresh_backup_status()

    # ── Media Library ──────────────────────────────────────────────
    lib_block, lib_lay = make_field_block(
        "Media Library",
        "Write Kodi/Jellyfin/Plex-compatible metadata files and chat replays "
        "for archival.",
    )
    win.nfo_check = QCheckBox("Write .nfo file (movie schema) alongside each download")
    win.nfo_check.setChecked(win._write_nfo)
    lib_lay.addWidget(win.nfo_check)
    win.chat_check = QCheckBox("Download Twitch VOD chat replay (JSON + plain text)")
    win.chat_check.setChecked(TwitchExtractor.download_chat_enabled)
    lib_lay.addWidget(win.chat_check)
    semantic_block, semantic_lay = make_field_block(
        "Local semantic moments",
        "Optional local-only search across timestamped transcript, scene, OCR, audio, "
        "and comment sidecars. The rebuildable index stays outside portable backups.",
    )
    win.semantic_search_check = QCheckBox(
        "Enable local semantic moment search (opt-in)"
    )
    win.semantic_search_check.setChecked(
        bool(win._config.get("semantic_search_enabled", False))
    )
    win.semantic_search_check.setToolTip(
        "No cloud model or network request is used. Exact transcript/comment FTS "
        "remains available when this is disabled."
    )
    semantic_lay.addWidget(win.semantic_search_check)
    semantic_limits = QHBoxLayout()
    semantic_limits.setSpacing(8)
    semantic_limits.addWidget(QLabel("Per-recording bound:"))
    from ...semantic import DEFAULT_MAX_MOMENTS, DEFAULT_MAX_INDEX_BYTES
    win.semantic_max_moments_spin = QSpinBox()
    win.semantic_max_moments_spin.setRange(1, 100000)
    win.semantic_max_moments_spin.setValue(int(win._config.get(
        "semantic_max_moments", DEFAULT_MAX_MOMENTS
    ) or DEFAULT_MAX_MOMENTS))
    win.semantic_max_moments_spin.setSuffix(" moments")
    win.semantic_max_moments_spin.setToolTip(
        "Maximum indexed moments per recording."
    )
    semantic_limits.addWidget(win.semantic_max_moments_spin)
    win.semantic_max_index_bytes_spin = QSpinBox()
    win.semantic_max_index_bytes_spin.setRange(1, 64 * 1024 * 1024)
    win.semantic_max_index_bytes_spin.setSingleStep(1024 * 1024)
    win.semantic_max_index_bytes_spin.setValue(int(win._config.get(
        "semantic_max_index_bytes", DEFAULT_MAX_INDEX_BYTES
    ) or DEFAULT_MAX_INDEX_BYTES))
    win.semantic_max_index_bytes_spin.setSuffix(" bytes")
    win.semantic_max_index_bytes_spin.setToolTip(
        "Maximum local feature payload per recording."
    )
    semantic_limits.addWidget(win.semantic_max_index_bytes_spin)
    semantic_limits.addStretch(1)
    semantic_lay.addLayout(semantic_limits)
    win.semantic_index_status_label = QLabel("Not indexed")
    win.semantic_index_status_label.setObjectName("hintLabel")
    win.semantic_index_status_label.setWordWrap(True)
    semantic_lay.addWidget(win.semantic_index_status_label)
    semantic_actions = QHBoxLayout()
    semantic_actions.setSpacing(8)
    win.semantic_rebuild_btn = QPushButton("Rebuild local moment index")
    win.semantic_rebuild_btn.setObjectName("secondary")
    win.semantic_rebuild_btn.clicked.connect(win._on_semantic_rebuild_clicked)
    semantic_actions.addWidget(win.semantic_rebuild_btn)
    win.semantic_cancel_btn = QPushButton("Cancel rebuild")
    win.semantic_cancel_btn.setObjectName("secondary")
    win.semantic_cancel_btn.setEnabled(False)
    win.semantic_cancel_btn.clicked.connect(win._on_semantic_cancel_clicked)
    semantic_actions.addWidget(win.semantic_cancel_btn)
    semantic_actions.addStretch(1)
    semantic_lay.addLayout(semantic_actions)
    card_lay.addWidget(semantic_block)
    translation_block, translation_lay = make_field_block(
        "Metadata translation",
        "Optional local-first translation of titles, descriptions, and chapter "
        "names into the current app language. Originals are always preserved.",
    )
    win.translate_metadata_check = QCheckBox(
        "Translate embedded metadata and chapters after download"
    )
    win.translate_metadata_check.setChecked(
        bool(win._config.get("translate_metadata_enabled", False))
    )
    win.translate_metadata_check.setToolTip(
        "Uses the local Ollama provider by default. Cloud providers are not "
        "reachable from this setting and require an explicit per-run consent "
        "through the translation API."
    )
    translation_lay.addWidget(win.translate_metadata_check)
    translation_model_row = QHBoxLayout()
    translation_model_row.setSpacing(8)
    translation_model_row.addWidget(QLabel("Local model:"))
    win.translation_model_input = QLineEdit(
        str(win._config.get("translation_model", "") or "")
    )
    win.translation_model_input.setPlaceholderText("Ollama model, e.g. llama3")
    translation_model_row.addWidget(win.translation_model_input, 1)
    translation_lay.addLayout(translation_model_row)
    translation_lay.addWidget(QLabel(
        "Target language follows Settings → Appearance. Translation failures "
        "never fail a download."
    ))
    card_lay.addWidget(translation_block)
    card_lay.addWidget(lib_block)

    # ── Media Server Auto-Import (F33) ────────────────────────────
    from ...integrations.media_server import SERVER_TYPES
    ms_block, ms_lay = make_field_block(
        "Media Server Auto-Import",
        "Copy recordings into a Plex/Jellyfin/Emby library folder and trigger "
        "a library scan after each download.",
    )
    ms_cfg = win._config.get("media_server", {})
    win.ms_enable_check = QCheckBox("Enable auto-import after download")
    win.ms_enable_check.setChecked(bool(ms_cfg.get("enabled")))
    ms_lay.addWidget(win.ms_enable_check)

    ms_type_row = QHBoxLayout()
    ms_type_row.setSpacing(8)
    ms_type_row.addWidget(QLabel("Server type:"))
    win.ms_type_combo = QComboBox()
    win.ms_type_combo.addItems([t.title() for t in SERVER_TYPES])
    cur_type = (ms_cfg.get("server_type") or "plex").lower()
    idx = SERVER_TYPES.index(cur_type) if cur_type in SERVER_TYPES else 0
    win.ms_type_combo.setCurrentIndex(idx)
    ms_type_row.addWidget(win.ms_type_combo)
    ms_type_row.addStretch(1)
    ms_lay.addLayout(ms_type_row)

    ms_url_row = QHBoxLayout()
    ms_url_row.setSpacing(8)
    ms_url_row.addWidget(QLabel("Server URL:"))
    win.ms_url_input = QLineEdit(ms_cfg.get("url", ""))
    win.ms_url_input.setPlaceholderText("http://localhost:32400")
    ms_url_row.addWidget(win.ms_url_input)
    ms_lay.addLayout(ms_url_row)

    ms_token_row = QHBoxLayout()
    ms_token_row.setSpacing(8)
    ms_token_row.addWidget(QLabel("API token:"))
    win.ms_token_input = QLineEdit(ms_cfg.get("token", ""))
    win.ms_token_input.setEchoMode(QLineEdit.EchoMode.Password)
    win.ms_token_input.setPlaceholderText("Plex token / Jellyfin API key")
    ms_token_row.addWidget(win.ms_token_input)
    ms_lay.addLayout(ms_token_row)

    ms_lib_row = QHBoxLayout()
    ms_lib_row.setSpacing(8)
    ms_lib_row.addWidget(QLabel("Library ID:"))
    win.ms_library_id_input = QLineEdit(ms_cfg.get("library_id", "1"))
    win.ms_library_id_input.setFixedWidth(60)
    win.ms_library_id_input.setToolTip("Plex library section ID (e.g. 1). Ignored for Jellyfin/Emby.")
    ms_lib_row.addWidget(win.ms_library_id_input)
    ms_lib_row.addStretch(1)
    ms_lay.addLayout(ms_lib_row)

    ms_path_row = QHBoxLayout()
    ms_path_row.setSpacing(8)
    ms_path_row.addWidget(QLabel("Library path:"))
    win.ms_path_input = QLineEdit(ms_cfg.get("library_path", ""))
    win.ms_path_input.setPlaceholderText("/path/to/media/library")
    ms_path_row.addWidget(win.ms_path_input)
    ms_lay.addLayout(ms_path_row)

    ms_layout_row = QHBoxLayout()
    ms_layout_row.setSpacing(8)
    ms_layout_row.addWidget(QLabel("Default layout:"))
    win.ms_layout_combo = QComboBox()
    win.ms_layout_combo.addItem("Season folders (Channel/Season YYYY)", userData="seasoned")
    win.ms_layout_combo.addItem("Flat channel folder (Channel)", userData="flat")
    layout_mode = str(ms_cfg.get("layout_mode", "seasoned") or "seasoned").lower()
    layout_index = max(0, win.ms_layout_combo.findData(layout_mode))
    win.ms_layout_combo.setCurrentIndex(layout_index)
    ms_layout_row.addWidget(win.ms_layout_combo)
    ms_layout_row.addStretch(1)
    ms_lay.addLayout(ms_layout_row)

    win.ms_portable_check = QCheckBox("Write a portable M3U playlist in the library root")
    win.ms_portable_check.setChecked(bool(ms_cfg.get("portable_m3u", False)))
    ms_lay.addWidget(win.ms_portable_check)
    win.ms_native_playlist_check = QCheckBox(
        "Maintain a native server playlist after each imported recording"
    )
    win.ms_native_playlist_check.setChecked(bool(ms_cfg.get("native_playlist", False)))
    ms_lay.addWidget(win.ms_native_playlist_check)

    ms_playlist_row = QHBoxLayout()
    ms_playlist_row.setSpacing(8)
    ms_playlist_row.addWidget(QLabel("Playlist name:"))
    win.ms_playlist_name_input = QLineEdit(
        str(ms_cfg.get("playlist_name", "StreamKeep") or "StreamKeep")
    )
    win.ms_playlist_name_input.setPlaceholderText("StreamKeep")
    ms_playlist_row.addWidget(win.ms_playlist_name_input)
    ms_lay.addLayout(ms_playlist_row)

    watched_hint = QLabel(
        "Watched-state import is manual and preview-first. Select exactly one server user; ambiguous matches are skipped and no files are deleted."
    )
    watched_hint.setObjectName("fieldHint")
    watched_hint.setWordWrap(True)
    ms_lay.addWidget(watched_hint)
    ms_user_row = QHBoxLayout()
    ms_user_row.setSpacing(8)
    ms_user_row.addWidget(QLabel("Selected user:"))
    win.ms_users_combo = QComboBox()
    win.ms_users_combo.addItem("Load users from server…", userData="")
    selected_user_id = str(ms_cfg.get("watched_user_id", "") or "")
    selected_user_name = str(ms_cfg.get("watched_user_name", "") or "")
    if selected_user_id:
        win.ms_users_combo.addItem(
            selected_user_name or selected_user_id, userData=selected_user_id
        )
        win.ms_users_combo.setCurrentIndex(1)
    ms_user_row.addWidget(win.ms_users_combo, 1)
    win.ms_load_users_btn = QPushButton("Load users")
    win.ms_load_users_btn.setObjectName("secondary")
    win.ms_load_users_btn.clicked.connect(win._on_media_server_load_users)
    ms_user_row.addWidget(win.ms_load_users_btn)
    ms_lay.addLayout(ms_user_row)
    ms_watch_btn_row = QHBoxLayout()
    ms_watch_btn_row.setSpacing(8)
    win.ms_preview_watched_btn = QPushButton("Preview watched state")
    win.ms_preview_watched_btn.setObjectName("secondary")
    win.ms_preview_watched_btn.clicked.connect(win._on_media_server_preview_watched)
    ms_watch_btn_row.addWidget(win.ms_preview_watched_btn)
    win.ms_apply_watched_btn = QPushButton("Apply preview")
    win.ms_apply_watched_btn.setObjectName("secondary")
    win.ms_apply_watched_btn.setEnabled(False)
    win.ms_apply_watched_btn.clicked.connect(win._on_media_server_apply_watched)
    ms_watch_btn_row.addWidget(win.ms_apply_watched_btn)
    win.ms_watched_status = QLabel("No watched-state preview loaded.")
    win.ms_watched_status.setObjectName("fieldHint")
    win.ms_watched_status.setWordWrap(True)
    ms_watch_btn_row.addWidget(win.ms_watched_status, 1)
    ms_lay.addLayout(ms_watch_btn_row)

    card_lay.addWidget(ms_block)

    # ── Post-Processing ────────────────────────────────────────────
    pp_block, pp_lay = make_field_block(
        "Post-Processing",
        "Automatic ffmpeg operations on each downloaded file. Originals are preserved.",
    )
    # Preset selector (v4.20.0 — F7)
    preset_row = QHBoxLayout()
    preset_row.setSpacing(8)
    preset_row.addWidget(QLabel("Preset:"))
    win.pp_preset_combo = QComboBox()
    win.pp_preset_combo.setMinimumWidth(180)
    win.pp_preset_combo.setToolTip("Load a saved post-processing profile")
    _populate_pp_presets(win)
    win.pp_preset_combo.currentIndexChanged.connect(
        lambda _idx, w=win: _on_pp_preset_selected(w)
    )
    preset_row.addWidget(win.pp_preset_combo)
    win.pp_preset_save_btn = QPushButton("Save As…")
    win.pp_preset_save_btn.setObjectName("ghost")
    win.pp_preset_save_btn.setFixedWidth(80)
    win.pp_preset_save_btn.clicked.connect(lambda _c=False, w=win: _on_pp_preset_save(w))
    preset_row.addWidget(win.pp_preset_save_btn)
    win.pp_preset_del_btn = QPushButton("Delete")
    win.pp_preset_del_btn.setObjectName("ghost")
    win.pp_preset_del_btn.setFixedWidth(60)
    win.pp_preset_del_btn.clicked.connect(lambda _c=False, w=win: _on_pp_preset_delete(w))
    preset_row.addWidget(win.pp_preset_del_btn)
    preset_row.addStretch(1)
    pp_lay.addLayout(preset_row)

    win.pp_audio_check = QCheckBox("Extract audio as MP3 (libmp3lame, VBR quality 2)")
    win.pp_audio_check.setChecked(PostProcessor.extract_audio)
    pp_lay.addWidget(win.pp_audio_check)
    win.pp_loud_check = QCheckBox("Normalize loudness (EBU R128: I=-16, TP=-1.5, LRA=11)")
    win.pp_loud_check.setChecked(PostProcessor.normalize_loudness)
    pp_lay.addWidget(win.pp_loud_check)
    win.pp_h265_check = QCheckBox("Re-encode video to H.265/HEVC (libx265, CRF 23 — slow)")
    win.pp_h265_check.setChecked(PostProcessor.reencode_h265)
    pp_lay.addWidget(win.pp_h265_check)
    win.pp_contact_check = QCheckBox("Generate contact sheet (3x3 thumbnail grid .jpg)")
    win.pp_contact_check.setChecked(PostProcessor.contact_sheet)
    pp_lay.addWidget(win.pp_contact_check)
    win.pp_split_check = QCheckBox(
        "Split by chapters into per-chapter files (for videos with chapters)"
    )
    win.pp_split_check.setChecked(PostProcessor.split_by_chapter)
    pp_lay.addWidget(win.pp_split_check)

    # Silence removal (v4.20.0 — F26)
    silence_row = QHBoxLayout()
    silence_row.setSpacing(8)
    win.pp_silence_check = QCheckBox("Remove silence / dead air")
    win.pp_silence_check.setChecked(PostProcessor.remove_silence)
    win.pp_silence_check.setToolTip(
        "Detect silent segments with ffmpeg silencedetect and cut them out.\n"
        "Produces a .nosilence copy — the original is preserved."
    )
    silence_row.addWidget(win.pp_silence_check)
    silence_row.addWidget(QLabel("Threshold:"))
    win.pp_silence_db_spin = QSpinBox()
    win.pp_silence_db_spin.setRange(-60, -10)
    win.pp_silence_db_spin.setSuffix(" dB")
    win.pp_silence_db_spin.setValue(int(PostProcessor.silence_noise_db or -30))
    win.pp_silence_db_spin.setToolTip("Noise floor — lower values are more aggressive")
    win.pp_silence_db_spin.setFixedWidth(90)
    silence_row.addWidget(win.pp_silence_db_spin)
    silence_row.addWidget(QLabel("Min:"))
    win.pp_silence_dur_spin = QSpinBox()
    win.pp_silence_dur_spin.setRange(1, 60)
    win.pp_silence_dur_spin.setSuffix("s")
    win.pp_silence_dur_spin.setValue(int(PostProcessor.silence_min_duration or 3))
    win.pp_silence_dur_spin.setToolTip("Minimum consecutive silence before cutting")
    win.pp_silence_dur_spin.setFixedWidth(80)
    silence_row.addWidget(win.pp_silence_dur_spin)
    silence_row.addStretch(1)
    pp_lay.addLayout(silence_row)

    # Video converter row
    win.pp_convert_video_check = QCheckBox("Convert video to:")
    win.pp_convert_video_check.setChecked(PostProcessor.convert_video)
    win.pp_convert_video_format = QComboBox()
    win.pp_convert_video_format.addItems(VIDEO_CONTAINERS)
    idx = (
        VIDEO_CONTAINERS.index(PostProcessor.convert_video_format)
        if PostProcessor.convert_video_format in VIDEO_CONTAINERS else 0
    )
    win.pp_convert_video_format.setCurrentIndex(idx)
    win.pp_convert_video_format.setFixedWidth(80)
    win.pp_convert_video_codec = QComboBox()
    vc_keys = available_video_codec_keys()
    win.pp_convert_video_codec.addItems(vc_keys)
    saved_vc = PostProcessor.convert_video_codec
    if saved_vc in vc_keys:
        win.pp_convert_video_codec.setCurrentIndex(vc_keys.index(saved_vc))
    elif "h264" in vc_keys:
        win.pp_convert_video_codec.setCurrentIndex(vc_keys.index("h264"))
    win.pp_convert_video_codec.setFixedWidth(140)
    hw_count = sum(1 for k in vc_keys if "(" in k)
    hw_note = (
        f" ({hw_count} GPU encoder{'s' if hw_count != 1 else ''} detected)"
        if hw_count else ""
    )
    win.pp_convert_video_codec.setToolTip(
        "copy = fast remux (no re-encode)\n"
        "h264/h265/vp9/av1/mpeg4 = software encoders\n"
        "(NVENC) = NVIDIA GPU (5-20x faster)\n"
        "(QSV) = Intel Quick Sync\n"
        "(AMF) = AMD GPU\n"
        "(VT) = Apple VideoToolbox\n"
        + hw_note
    )
    # Scale target
    scale_items = ["original", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
    win.pp_convert_video_scale = QComboBox()
    win.pp_convert_video_scale.addItems(scale_items)
    idx = (
        scale_items.index(PostProcessor.convert_video_scale)
        if PostProcessor.convert_video_scale in scale_items else 0
    )
    win.pp_convert_video_scale.setCurrentIndex(idx)
    win.pp_convert_video_scale.setFixedWidth(90)
    win.pp_convert_video_scale.setToolTip(
        "Downscale target height. Aspect ratio is preserved.\n"
        "Forces a re-encode when not 'original' (copy codec ignored)."
    )
    # FPS cap
    fps_items = ["original", "60", "30", "24"]
    win.pp_convert_video_fps = QComboBox()
    win.pp_convert_video_fps.addItems(fps_items)
    idx = (
        fps_items.index(PostProcessor.convert_video_fps)
        if PostProcessor.convert_video_fps in fps_items else 0
    )
    win.pp_convert_video_fps.setCurrentIndex(idx)
    win.pp_convert_video_fps.setFixedWidth(80)
    win.pp_convert_video_fps.setToolTip(
        "Frame rate cap. Forces a re-encode when not 'original'."
    )

    vconv_row = QHBoxLayout()
    vconv_row.setSpacing(6)
    vconv_row.addWidget(win.pp_convert_video_check)
    vconv_row.addSpacing(4)
    vconv_row.addWidget(QLabel("Container:"))
    vconv_row.addWidget(win.pp_convert_video_format)
    vconv_row.addSpacing(8)
    vconv_row.addWidget(QLabel("Codec:"))
    vconv_row.addWidget(win.pp_convert_video_codec)
    vconv_row.addSpacing(8)
    vconv_row.addWidget(QLabel("Scale:"))
    vconv_row.addWidget(win.pp_convert_video_scale)
    vconv_row.addSpacing(8)
    vconv_row.addWidget(QLabel("FPS:"))
    vconv_row.addWidget(win.pp_convert_video_fps)
    vconv_row.addStretch(1)
    pp_lay.addLayout(vconv_row)

    # Audio converter row
    win.pp_convert_audio_check = QCheckBox("Convert audio to:")
    win.pp_convert_audio_check.setChecked(PostProcessor.convert_audio)
    win.pp_convert_audio_format = QComboBox()
    win.pp_convert_audio_format.addItems(AUDIO_CONTAINERS)
    idx = (
        AUDIO_CONTAINERS.index(PostProcessor.convert_audio_format)
        if PostProcessor.convert_audio_format in AUDIO_CONTAINERS else 0
    )
    win.pp_convert_audio_format.setCurrentIndex(idx)
    win.pp_convert_audio_format.setFixedWidth(80)
    win.pp_convert_audio_codec = QComboBox()
    win.pp_convert_audio_codec.addItems(list(AUDIO_CODECS.keys()))
    ac_keys = list(AUDIO_CODECS.keys())
    idx = (
        ac_keys.index(PostProcessor.convert_audio_codec)
        if PostProcessor.convert_audio_codec in ac_keys else 1
    )
    win.pp_convert_audio_codec.setCurrentIndex(idx)
    win.pp_convert_audio_codec.setFixedWidth(90)
    win.pp_convert_audio_codec.setToolTip(
        "copy = remux only\n"
        "mp3 = libmp3lame (universal)\n"
        "aac = AAC-LC (Apple-friendly)\n"
        "opus = low-bitrate champion\n"
        "vorbis = open-source lossy\n"
        "flac/pcm = lossless"
    )
    win.pp_convert_audio_bitrate = QComboBox()
    win.pp_convert_audio_bitrate.addItems(["96k", "128k", "192k", "256k", "320k"])
    br_items = ["96k", "128k", "192k", "256k", "320k"]
    idx = (
        br_items.index(PostProcessor.convert_audio_bitrate)
        if PostProcessor.convert_audio_bitrate in br_items else 2
    )
    win.pp_convert_audio_bitrate.setCurrentIndex(idx)
    win.pp_convert_audio_bitrate.setFixedWidth(80)
    win.pp_convert_audio_bitrate.setToolTip("Bitrate (ignored for flac/pcm)")
    # Sample rate
    sr_items = ["original", "48000", "44100", "22050"]
    win.pp_convert_audio_samplerate = QComboBox()
    win.pp_convert_audio_samplerate.addItems(sr_items)
    idx = (
        sr_items.index(PostProcessor.convert_audio_samplerate)
        if PostProcessor.convert_audio_samplerate in sr_items else 0
    )
    win.pp_convert_audio_samplerate.setCurrentIndex(idx)
    win.pp_convert_audio_samplerate.setFixedWidth(90)
    win.pp_convert_audio_samplerate.setToolTip(
        "Sample rate (Hz). Forces a re-encode when not 'original'."
    )

    aconv_row = QHBoxLayout()
    aconv_row.setSpacing(6)
    aconv_row.addWidget(win.pp_convert_audio_check)
    aconv_row.addSpacing(4)
    aconv_row.addWidget(QLabel("Container:"))
    aconv_row.addWidget(win.pp_convert_audio_format)
    aconv_row.addSpacing(8)
    aconv_row.addWidget(QLabel("Codec:"))
    aconv_row.addWidget(win.pp_convert_audio_codec)
    aconv_row.addSpacing(8)
    aconv_row.addWidget(QLabel("Bitrate:"))
    aconv_row.addWidget(win.pp_convert_audio_bitrate)
    aconv_row.addSpacing(8)
    aconv_row.addWidget(QLabel("Rate:"))
    aconv_row.addWidget(win.pp_convert_audio_samplerate)
    aconv_row.addStretch(1)
    pp_lay.addLayout(aconv_row)

    win.pp_convert_delete_check = QCheckBox(
        "Delete original source file after successful conversion"
    )
    win.pp_convert_delete_check.setChecked(PostProcessor.convert_delete_source)
    pp_lay.addWidget(win.pp_convert_delete_check)

    # Subtitle post-processing: bilingual merge + LRC export (P3)
    win.pp_bilingual_check = QCheckBox(
        "Merge bilingual subtitles from downloaded sidecars"
    )
    win.pp_bilingual_check.setChecked(bool(PostProcessor.bilingual_subs))
    win.pp_bilingual_check.setToolTip(
        "After download, stack a primary and secondary subtitle language into "
        "one file (SRT or two-style ASS). Requires both language sidecars."
    )
    pp_lay.addWidget(win.pp_bilingual_check)

    bilingual_row = QHBoxLayout()
    bilingual_row.setSpacing(8)
    bilingual_row.addWidget(QLabel("Primary"))
    win.pp_bilingual_primary = QLineEdit(PostProcessor.bilingual_primary_lang or "en")
    win.pp_bilingual_primary.setPlaceholderText("en")
    win.pp_bilingual_primary.setFixedWidth(70)
    bilingual_row.addWidget(win.pp_bilingual_primary)
    bilingual_row.addWidget(QLabel("Secondary"))
    win.pp_bilingual_secondary = QLineEdit(PostProcessor.bilingual_secondary_lang or "")
    win.pp_bilingual_secondary.setPlaceholderText("es")
    win.pp_bilingual_secondary.setFixedWidth(70)
    bilingual_row.addWidget(win.pp_bilingual_secondary)
    bilingual_row.addWidget(QLabel("Format"))
    win.pp_bilingual_format = QComboBox()
    win.pp_bilingual_format.addItems(["srt", "ass"])
    _bfmt_idx = win.pp_bilingual_format.findText(
        (PostProcessor.bilingual_format or "srt").lower()
    )
    win.pp_bilingual_format.setCurrentIndex(max(0, _bfmt_idx))
    win.pp_bilingual_format.setFixedWidth(70)
    bilingual_row.addWidget(win.pp_bilingual_format)
    bilingual_row.addStretch(1)
    pp_lay.addLayout(bilingual_row)

    win.pp_lrc_check = QCheckBox("Export an LRC lyrics file from a subtitle track")
    win.pp_lrc_check.setChecked(bool(PostProcessor.lrc_export))
    win.pp_lrc_check.setToolTip(
        "Convert a subtitle sidecar of the chosen language into a synchronized "
        "[mm:ss.xx] .lrc file for music players."
    )
    pp_lay.addWidget(win.pp_lrc_check)

    lrc_row = QHBoxLayout()
    lrc_row.setSpacing(8)
    lrc_row.addWidget(QLabel("LRC language"))
    win.pp_lrc_lang = QLineEdit(PostProcessor.lrc_lang or "en")
    win.pp_lrc_lang.setPlaceholderText("en")
    win.pp_lrc_lang.setFixedWidth(70)
    lrc_row.addWidget(win.pp_lrc_lang)
    lrc_row.addStretch(1)
    pp_lay.addLayout(lrc_row)

    # Standalone manual converter
    manual_row = QHBoxLayout()
    manual_row.setSpacing(8)
    win.convert_files_btn = QPushButton("Convert Files...")
    win.convert_files_btn.setObjectName("secondary")
    win.convert_files_btn.setToolTip(
        "Pick individual media files and convert them with the current settings.\n"
        "Saves your settings first."
    )
    win.convert_files_btn.clicked.connect(win._on_convert_files_clicked)
    manual_row.addWidget(win.convert_files_btn)

    win.convert_folder_btn = QPushButton("Convert Folder...")
    win.convert_folder_btn.setObjectName("secondary")
    win.convert_folder_btn.setToolTip(
        "Pick a folder; every video/audio file in it gets converted."
    )
    win.convert_folder_btn.clicked.connect(win._on_convert_folder_clicked)
    manual_row.addWidget(win.convert_folder_btn)

    win.convert_cancel_btn = QPushButton("Cancel")
    win.convert_cancel_btn.setObjectName("secondary")
    win.convert_cancel_btn.setVisible(False)
    win.convert_cancel_btn.clicked.connect(win._on_convert_cancel)
    manual_row.addWidget(win.convert_cancel_btn)
    manual_row.addStretch(1)
    pp_lay.addLayout(manual_row)

    card_lay.addWidget(pp_block)

    # Save / Import / Export row
    save_row = QHBoxLayout()
    import_btn = QPushButton("Import config")
    import_btn.setObjectName("secondary")
    import_btn.setToolTip("Replace current settings with a backup file")
    import_btn.clicked.connect(win._on_import_config)
    save_row.addWidget(import_btn)
    export_btn = QPushButton("Export config")
    export_btn.setObjectName("secondary")
    export_btn.setToolTip("Write current settings to a backup file")
    export_btn.clicked.connect(win._on_export_config)
    save_row.addWidget(export_btn)
    save_row.addStretch()
    save_btn = QPushButton("Save settings")
    save_btn.setObjectName("primary")
    save_btn.clicked.connect(win._on_save_settings)
    save_row.addWidget(save_btn)
    card_lay.addLayout(save_row)

    def _ensure_visible(target):
        parent = page.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if isinstance(parent, QScrollArea):
            parent.ensureWidgetVisible(target, 12, 12)

    settings_nav_targets = (
        general_block,
        cookies_block,
        network_block,
        companion_panel,
        smart_block,
        lib_block,
        pp_block,
    )
    for button, target in zip(settings_nav_buttons, settings_nav_targets):
        button.clicked.connect(
            lambda _checked=False, target=target: QTimer.singleShot(
                0, lambda target=target: _ensure_visible(target)
            )
        )

    lay.addWidget(card, 1)
    return page
