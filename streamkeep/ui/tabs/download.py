"""Download tab — URL input, VOD picker, segments table, queue, log."""

from PyQt6.QtCore import Qt, QUrl, QStringListModel
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMenu, QPushButton, QSpinBox, QSplitter, QTableWidget, QTextEdit,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...theme import CAT
from ...utils import default_output_dir as _default_output_dir
from ..widgets import make_field_block, path_label, style_table
from .download_queue import DownloadQueueMixin
from .download_vod import DownloadVodMixin
from .download_finalize import DownloadFinalizeMixin


def _populate_adv_pp(win):
    """Populate the per-download PP preset combo from settings presets."""
    from .settings import BUILTIN_PRESETS, _get_user_presets
    combo = win.adv_pp_combo
    combo.blockSignals(True)
    current = combo.currentData() or ""
    combo.clear()
    combo.addItem("(use global setting)", userData="")
    for name in BUILTIN_PRESETS:
        combo.addItem(f"★ {name}", userData=name)
    for name in _get_user_presets(win):
        combo.addItem(name, userData=name)
    # Restore selection
    for i in range(combo.count()):
        if combo.itemData(i) == current:
            combo.setCurrentIndex(i)
            break
    combo.blockSignals(False)


def _populate_adv_ytdlp_templates(win):
    """Refresh named structured yt-dlp templates in Download Advanced."""
    from ...download_options import normalize_ytdlp_arg_templates
    combo = win.adv_ytdlp_template_combo
    current = combo.currentData() or ""
    try:
        templates = normalize_ytdlp_arg_templates(
            getattr(win, "_config", {}).get("ytdlp_arg_templates", {})
        )
    except ValueError:
        templates = {}
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("No argument template", userData="")
    for name in sorted(templates, key=str.casefold):
        combo.addItem(name, userData=name)
    index = combo.findData(current)
    combo.setCurrentIndex(max(0, index))
    combo.blockSignals(False)


def _reset_adv_overrides(win):
    """Clear all per-download override fields."""
    win.adv_pp_combo.setCurrentIndex(0)
    win.adv_rate_input.clear()
    win.adv_parallel_spin.setValue(0)
    win.adv_folder_tpl_input.clear()
    win.adv_file_tpl_input.clear()
    win.adv_format_input.clear()
    win.adv_format_sort_combo.setCurrentIndex(0)
    win.adv_container_combo.setCurrentIndex(0)
    win.adv_audio_combo.setCurrentIndex(0)
    win.adv_audio_quality_input.clear()
    win.adv_subtitle_mode_combo.setCurrentIndex(0)
    win.adv_subtitle_list.clearSelection()
    win.adv_subtitle_auto_check.setChecked(True)
    win.adv_subtitle_convert_combo.setCurrentIndex(0)
    win.adv_subtitle_delivery_combo.setCurrentIndex(0)
    win.adv_sponsorblock_mode_combo.setCurrentIndex(0)
    for combo in win.adv_sponsorblock_action_combos.values():
        combo.setCurrentIndex(0)
    win.adv_sponsorblock_api_input.clear()
    win.adv_playlist_items_input.clear()
    win.adv_playlist_after_input.clear()
    win.adv_playlist_before_input.clear()
    win.adv_playlist_filter_input.clear()
    win.adv_playlist_max_spin.setValue(0)
    win.adv_playlist_archive_check.setChecked(False)
    win.adv_ytdlp_fragments_spin.setValue(0)
    win.adv_ytdlp_retries_input.clear()
    win.adv_ytdlp_fragment_retries_input.clear()
    win.adv_ytdlp_retry_sleep_input.clear()
    win.adv_ytdlp_unavailable_combo.setCurrentIndex(0)
    win.adv_ytdlp_throttled_input.clear()
    win.adv_ytdlp_wait_input.clear()
    win.adv_ytdlp_live_combo.setCurrentIndex(0)
    win.adv_ytdlp_embed_chapters_combo.setCurrentIndex(0)
    win.adv_ytdlp_embed_metadata_combo.setCurrentIndex(0)
    win.adv_ytdlp_embed_thumbnail_combo.setCurrentIndex(0)
    win.adv_ytdlp_template_combo.setCurrentIndex(0)
    win.adv_hls_key_input.clear()
    win.adv_hls_iv_input.clear()
    win.adv_override_badge.setVisible(False)


def get_adv_overrides(win):
    """Return a dict of active per-download overrides (empty keys omitted).

    Called from main_window._on_download() to merge into worker context.
    """
    overrides = {}
    preset_name = win.adv_pp_combo.currentData() or ""
    if preset_name:
        overrides["pp_preset"] = preset_name
    rate = win.adv_rate_input.text().strip()
    if rate:
        overrides["rate_limit"] = rate
    par = win.adv_parallel_spin.value()
    if par > 0:
        overrides["parallel_connections"] = par
    ftpl = win.adv_folder_tpl_input.text().strip()
    if ftpl:
        overrides["folder_template"] = ftpl
    fitpl = win.adv_file_tpl_input.text().strip()
    if fitpl:
        overrides["file_template"] = fitpl
    format_spec = win.adv_format_input.text()
    if format_spec.strip():
        overrides["format_spec"] = format_spec
    format_sort_preset = win.adv_format_sort_combo.currentData() or ""
    if format_sort_preset:
        overrides["format_sort_preset"] = format_sort_preset
    container = win.adv_container_combo.currentData() or ""
    if container:
        overrides["container"] = container
    audio_format = win.adv_audio_combo.currentData() or ""
    if audio_format:
        overrides["audio_format"] = audio_format
    audio_quality = win.adv_audio_quality_input.text().strip()
    if audio_quality:
        overrides["audio_quality"] = audio_quality
    subtitle_mode = win.adv_subtitle_mode_combo.currentData() or ""
    if subtitle_mode:
        overrides["subtitle_mode"] = subtitle_mode
        if subtitle_mode == "custom":
            languages = [
                str(win.adv_subtitle_list.item(index).data(
                    Qt.ItemDataRole.UserRole
                ) or "")
                for index in range(win.adv_subtitle_list.count())
                if win.adv_subtitle_list.item(index).isSelected()
            ]
            overrides["subtitle_languages"] = ",".join(
                language for language in languages if language
            )
            overrides["subtitle_auto"] = win.adv_subtitle_auto_check.isChecked()
            overrides["subtitle_convert"] = (
                win.adv_subtitle_convert_combo.currentData() or ""
            )
            overrides["subtitle_embed"] = (
                win.adv_subtitle_delivery_combo.currentData() == "embed"
            )
    sponsorblock_mode = win.adv_sponsorblock_mode_combo.currentData() or ""
    if sponsorblock_mode:
        overrides["sponsorblock_mode"] = sponsorblock_mode
        if sponsorblock_mode == "custom":
            overrides["sponsorblock_mark"] = ",".join(
                category for category, combo
                in win.adv_sponsorblock_action_combos.items()
                if combo.currentData() == "mark"
            )
            overrides["sponsorblock_remove"] = ",".join(
                category for category, combo
                in win.adv_sponsorblock_action_combos.items()
                if combo.currentData() == "remove"
            )
            overrides["sponsorblock_api"] = (
                win.adv_sponsorblock_api_input.text().strip()
            )
    playlist_items = win.adv_playlist_items_input.text().strip()
    if playlist_items:
        overrides["playlist_items"] = playlist_items
    playlist_after = win.adv_playlist_after_input.text().strip()
    if playlist_after:
        overrides["playlist_date_after"] = playlist_after
    playlist_before = win.adv_playlist_before_input.text().strip()
    if playlist_before:
        overrides["playlist_date_before"] = playlist_before
    playlist_filter = win.adv_playlist_filter_input.text()
    if playlist_filter.strip():
        overrides["playlist_match_filter"] = playlist_filter
    playlist_max = win.adv_playlist_max_spin.value()
    if playlist_max:
        overrides["playlist_max_downloads"] = playlist_max
    if win.adv_playlist_archive_check.isChecked():
        overrides["playlist_archive_sync"] = True
    fragments = win.adv_ytdlp_fragments_spin.value()
    if fragments:
        overrides["ytdlp_concurrent_fragments"] = fragments
    for name, widget in (
        ("retries", win.adv_ytdlp_retries_input),
        ("fragment_retries", win.adv_ytdlp_fragment_retries_input),
        ("retry_sleep", win.adv_ytdlp_retry_sleep_input),
        ("throttled_rate", win.adv_ytdlp_throttled_input),
        ("wait_for_video", win.adv_ytdlp_wait_input),
    ):
        value = widget.text().strip()
        if value:
            overrides[f"ytdlp_{name}"] = value
    unavailable = win.adv_ytdlp_unavailable_combo.currentData()
    if unavailable:
        overrides["ytdlp_unavailable_fragments"] = unavailable
    for name, combo in (
        ("live_from_start", win.adv_ytdlp_live_combo),
        ("embed_chapters", win.adv_ytdlp_embed_chapters_combo),
        ("embed_metadata", win.adv_ytdlp_embed_metadata_combo),
        ("embed_thumbnail", win.adv_ytdlp_embed_thumbnail_combo),
    ):
        value = combo.currentData()
        if value is not None:
            overrides[f"ytdlp_{name}"] = value
    template_combo = getattr(win, "adv_ytdlp_template_combo", None)
    template_name = template_combo.currentData() or "" if template_combo else ""
    if template_name:
        overrides["ytdlp_template_name"] = template_name
    hls_key = win.adv_hls_key_input.text().strip()
    hls_iv = win.adv_hls_iv_input.text().strip()
    if hls_key:
        overrides["hls_key_override"] = hls_key
    if hls_iv:
        overrides["hls_key_iv"] = hls_iv
    return overrides


def _refresh_adv_subtitle_controls(win):
    custom = (win.adv_subtitle_mode_combo.currentData() == "custom")
    auto_enabled = custom and win.adv_subtitle_auto_check.isChecked()
    win.adv_subtitle_list.setEnabled(custom)
    win.adv_subtitle_auto_check.setEnabled(custom)
    win.adv_subtitle_convert_combo.setEnabled(custom)
    win.adv_subtitle_delivery_combo.setEnabled(custom)
    manual_role = Qt.ItemDataRole.UserRole.value + 1
    for index in range(win.adv_subtitle_list.count()):
        item = win.adv_subtitle_list.item(index)
        automatic_only = not bool(item.data(manual_role))
        item.setHidden(custom and automatic_only and not auto_enabled)
        if automatic_only and not auto_enabled:
            item.setSelected(False)


def _refresh_adv_sponsorblock_controls(win):
    custom = (win.adv_sponsorblock_mode_combo.currentData() == "custom")
    win.adv_sponsorblock_table.setEnabled(custom)
    win.adv_sponsorblock_api_input.setEnabled(custom)


def _populate_adv_subtitles(win, info=None):
    """Populate source-reported subtitle languages after a resolve."""
    previous = {
        str(item.data(Qt.ItemDataRole.UserRole) or "")
        for item in win.adv_subtitle_list.selectedItems()
    }
    win.adv_subtitle_list.clear()
    manual_role = Qt.ItemDataRole.UserRole.value + 1
    tracks = list(getattr(info, "subtitles", []) or [])
    for track in tracks:
        language = str(getattr(track, "language", "") or "")
        if not language:
            continue
        kinds = []
        if getattr(track, "manual", False):
            kinds.append("manual")
        if getattr(track, "automatic", False):
            kinds.append("auto")
        formats = "/".join(getattr(track, "formats", []) or [])
        detail = ", ".join(kinds)
        if formats:
            detail += ("; " if detail else "") + formats
        name = str(getattr(track, "name", "") or "")
        label = language + (f" — {name}" if name and name != language else "")
        if detail:
            label += f" ({detail})"
        win.adv_subtitle_list.addItem(label)
        item = win.adv_subtitle_list.item(win.adv_subtitle_list.count() - 1)
        item.setData(Qt.ItemDataRole.UserRole, language)
        item.setData(manual_role, bool(getattr(track, "manual", False)))
        if language in previous:
            item.setSelected(True)
    custom_item = win.adv_subtitle_mode_combo.model().item(2)
    if custom_item is not None:
        custom_item.setEnabled(bool(tracks))
    if not tracks and win.adv_subtitle_mode_combo.currentData() == "custom":
        win.adv_subtitle_mode_combo.setCurrentIndex(0)
    win.adv_subtitle_list.setToolTip(
        f"{len(tracks)} language(s) reported by the current yt-dlp source"
        if tracks else "Fetch a yt-dlp source to list its subtitle languages"
    )
    _refresh_adv_subtitle_controls(win)


def _on_track_toggled(win, checkbox, track, checked):
    if not checked or getattr(track, "kind", "") != "video":
        return
    for other_checkbox, other_track in win._track_checks:
        if (other_checkbox is not checkbox
                and getattr(other_track, "kind", "") == "video"
                and other_checkbox.isChecked()):
            other_checkbox.setChecked(False)


def _populate_track_table(win):
    """Show the selected quality's HLS/DASH representation matrix."""
    if not hasattr(win, "track_table"):
        return
    quality = win.quality_combo.currentData() if hasattr(win, "quality_combo") else None
    tracks = list(getattr(quality, "tracks", []) or [])
    win.track_table.setRowCount(0)
    win._track_checks = []
    win.track_section.setVisible(bool(tracks))
    if not tracks:
        return
    from ...models import default_media_tracks
    selected_ids = {track.id for track in default_media_tracks(quality)}
    win.track_table.setRowCount(len(tracks))
    for row, track in enumerate(tracks):
        checkbox = QCheckBox()
        checkbox.setChecked(track.id in selected_ids)
        checkbox.toggled.connect(
            lambda checked, cb=checkbox, item=track:
            _on_track_toggled(win, cb, item, checked)
        )
        holder = QWidget()
        holder_lay = QHBoxLayout(holder)
        holder_lay.setContentsMargins(0, 0, 0, 0)
        holder_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        holder_lay.addWidget(checkbox)
        win.track_table.setCellWidget(row, 0, holder)
        win.track_table.setItem(row, 1, QTableWidgetItem(track.kind.title()))
        win.track_table.setItem(
            row, 2, QTableWidgetItem(track.language or "—")
        )
        flags = []
        if track.default:
            flags.append("default")
        if track.forced:
            flags.append("forced")
        label = track.label or track.id
        if flags:
            label += f" ({', '.join(flags)})"
        win.track_table.setItem(row, 3, QTableWidgetItem(label))
        detail = track.codec or ""
        if track.resolution:
            detail = f"{track.resolution}  {detail}".strip()
        if track.bandwidth:
            detail += f"  {track.bandwidth // 1000} kbps"
        win.track_table.setItem(row, 4, QTableWidgetItem(detail.strip() or "—"))
        win._track_checks.append((checkbox, track))
    win.track_summary_label.setText(
        f"{len(tracks)} representation(s); select one video and any audio/subtitle tracks."
    )


def get_selected_media_tracks(win):
    return [
        track for checkbox, track in getattr(win, "_track_checks", [])
        if checkbox.isChecked()
    ]


def build_download_tab(win):
    """Build the Download tab page. Stashes widget refs on `win.*`."""
    page = QWidget()
    root = QVBoxLayout(page)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(12)

    hero = QFrame()
    hero.setObjectName("pageHeader")
    hero_lay = QVBoxLayout(hero)
    hero_lay.setContentsMargins(4, 12, 4, 4)
    hero_lay.setSpacing(3)

    win.download_hero_title = QLabel("New download")
    win.download_hero_title.setObjectName("heroTitle")
    win.download_hero_title.setWordWrap(True)
    win.download_hero_body = QLabel("Paste a stream, VOD, podcast, or media URL.")
    win.download_hero_body.setObjectName("heroBody")
    win.download_hero_body.setWordWrap(True)
    hero_lay.addWidget(win.download_hero_title)
    hero_lay.addWidget(win.download_hero_body)

    # Existing workers update these labels; they remain state holders instead
    # of becoming six competing dashboard cards above the primary action.
    # Keep them under an invisible parent: a parentless QLabel becomes a
    # top-level window when set_metric() later makes its detail text visible.
    win._download_metric_state = QWidget(page)
    win._download_metric_state.setObjectName("downloadMetricState")
    win._download_metric_state.setVisible(False)
    for key, value, detail in (
        ("platform", "Auto detect", "Waiting for a URL"),
        ("duration", "Waiting", "Metadata not loaded"),
        ("selection", "Not ready", "Segments appear after fetch"),
        ("output", path_label(str(_default_output_dir())), ""),
        ("finalize", "Idle", "No background tasks"),
        ("speed", "—", ""),
        ("eta", "—", ""),
    ):
        value_label = QLabel(value, win._download_metric_state)
        detail_label = QLabel(detail, win._download_metric_state)
        value_label.setVisible(False)
        detail_label.setVisible(False)
        setattr(win, f"download_{key}_value", value_label)
        setattr(win, f"download_{key}_sub", detail_label)
    root.addWidget(hero)

    # Update banner — shown only after a successful release-check when a
    # newer version is available. Styled like the resume banner so users
    # recognize it as a one-click actionable notice.
    win.update_banner = QFrame()
    win.update_banner.setObjectName("updateBanner")
    win.update_banner.setVisible(False)
    ub_lay = QHBoxLayout(win.update_banner)
    ub_lay.setContentsMargins(16, 12, 16, 12)
    ub_lay.setSpacing(12)
    win.update_banner_label = QLabel("A newer release is available.")
    win.update_banner_label.setWordWrap(True)
    win.update_banner_label.setObjectName("updateBannerLabel")
    ub_lay.addWidget(win.update_banner_label, 1)
    win.update_banner_install_btn = QPushButton("Download & install")
    win.update_banner_install_btn.setObjectName("primary")
    win.update_banner_install_btn.clicked.connect(win._on_update_install)
    ub_lay.addWidget(win.update_banner_install_btn)
    win.update_banner_dismiss_btn = QPushButton("Dismiss")
    win.update_banner_dismiss_btn.setObjectName("secondary")
    win.update_banner_dismiss_btn.clicked.connect(win._on_update_dismiss)
    ub_lay.addWidget(win.update_banner_dismiss_btn)
    root.addWidget(win.update_banner)

    # Resume banner — shown only when startup scan finds orphan sidecars.
    win.resume_banner = QFrame()
    win.resume_banner.setObjectName("resumeBanner")
    win.resume_banner.setVisible(False)
    rb_lay = QHBoxLayout(win.resume_banner)
    rb_lay.setContentsMargins(16, 12, 16, 12)
    rb_lay.setSpacing(12)
    win.resume_banner_label = QLabel("Interrupted download ready to resume.")
    win.resume_banner_label.setWordWrap(True)
    win.resume_banner_label.setObjectName("resumeBannerLabel")
    rb_lay.addWidget(win.resume_banner_label, 1)
    win.resume_banner_resume_btn = QPushButton("Resume")
    win.resume_banner_resume_btn.setObjectName("primary")
    win.resume_banner_resume_btn.clicked.connect(win._on_resume_all)
    rb_lay.addWidget(win.resume_banner_resume_btn)
    win.resume_banner_discard_btn = QPushButton("Discard")
    win.resume_banner_discard_btn.setObjectName("secondary")
    win.resume_banner_discard_btn.clicked.connect(win._on_resume_discard)
    rb_lay.addWidget(win.resume_banner_discard_btn)
    root.addWidget(win.resume_banner)

    # Live-chat dock — hidden until the user enables live chat capture
    # in Settings AND at least one Twitch auto-record is running.
    win.chat_dock = QFrame()
    win.chat_dock.setObjectName("card")
    win.chat_dock.setVisible(False)
    cd_lay = QVBoxLayout(win.chat_dock)
    cd_lay.setContentsMargins(16, 12, 16, 12)
    cd_lay.setSpacing(6)
    chat_hdr = QLabel("Live chat")
    chat_hdr.setObjectName("sectionTitle")
    cd_lay.addWidget(chat_hdr)
    win.chat_log_view = QTextEdit()
    win.chat_log_view.setReadOnly(True)
    win.chat_log_view.setFixedHeight(180)
    cd_lay.addWidget(win.chat_log_view)
    root.addWidget(win.chat_dock)

    url_card = QFrame()
    url_card.setObjectName("composerCard")
    url_lay = QVBoxLayout(url_card)
    url_lay.setContentsMargins(16, 14, 16, 14)
    url_lay.setSpacing(10)

    url_header = QVBoxLayout()
    url_header.setSpacing(4)
    sec1 = QLabel("Source URL")
    sec1.setObjectName("sectionTitle")
    url_header.addWidget(sec1)
    url_lay.addLayout(url_header)

    url_row = QHBoxLayout()
    url_row.setSpacing(10)
    win.url_input = QLineEdit()
    win.url_input.setPlaceholderText(
        "Paste a stream, channel, VOD, or direct media URL…"
    )
    win.url_input.setClearButtonEnabled(True)
    win.url_input.setMinimumHeight(44)
    win.url_input.returnPressed.connect(lambda: win._on_fetch())
    win.url_input.textChanged.connect(win._on_url_changed)
    # Recent URLs autocomplete dropdown
    win._recent_url_model = QStringListModel(win._recent_urls)
    win._recent_url_completer = QCompleter(win._recent_url_model, win)
    win._recent_url_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    win._recent_url_completer.setFilterMode(Qt.MatchFlag.MatchContains)
    win._recent_url_completer.setMaxVisibleItems(10)
    win.url_input.setCompleter(win._recent_url_completer)
    url_row.addWidget(win.url_input, 1)

    win.platform_badge = QLabel("")
    win.platform_badge.setFixedHeight(40)
    win.platform_badge.setMinimumWidth(96)
    win.platform_badge.setVisible(False)
    win.platform_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    url_row.addWidget(win.platform_badge)

    win.fetch_btn = QPushButton("Fetch")
    win.fetch_btn.setObjectName("primary")
    win.fetch_btn.setMinimumWidth(116)
    win.fetch_btn.setMinimumHeight(44)
    win.fetch_btn.clicked.connect(win._on_fetch)
    url_row.addWidget(win.fetch_btn)
    url_lay.addLayout(url_row)

    utility_bar = QFrame()
    utility_bar.setObjectName("optionsRow")
    utility_lay = QHBoxLayout(utility_bar)
    utility_lay.setContentsMargins(0, 0, 0, 0)
    utility_lay.setSpacing(8)

    win.batch_import_btn = QPushButton("Import URLs")
    win.batch_import_btn.setObjectName("secondary")
    win.batch_import_btn.setToolTip("Import URLs from a text file (one per line) and queue them all (F44)")
    win.batch_import_btn.clicked.connect(win._on_batch_url_import)
    utility_lay.addWidget(win.batch_import_btn)

    paste_btn = QPushButton("Paste")
    paste_btn.setObjectName("secondary")
    paste_btn.setToolTip("Paste a URL from the clipboard")
    paste_btn.clicked.connect(win.url_input.paste)
    utility_lay.addWidget(paste_btn)

    win.scan_btn = QPushButton("Scan page")
    win.scan_btn.setObjectName("secondary")
    win.scan_btn.setToolTip("Fetch the URL as HTML and extract all video/media links it references")
    win.scan_btn.clicked.connect(win._on_scan_page)
    utility_lay.addWidget(win.scan_btn)

    win.scan_lan_check = QCheckBox("Allow LAN for this scan")
    win.scan_lan_check.setToolTip(
        "One scan only: allow RFC1918/ULA page targets. Loopback, link-local, "
        "cloud metadata, and other special addresses remain blocked."
    )
    utility_lay.addWidget(win.scan_lan_check)

    win.queue_btn = QPushButton("Queue")
    win.queue_btn.setObjectName("secondary")
    win.queue_btn.setToolTip("Add the current URL to the download queue")
    win.queue_btn.clicked.connect(win._on_queue_url)
    utility_lay.addWidget(win.queue_btn)

    utility_lay.addStretch(1)

    more_btn = QPushButton("More")
    more_btn.setObjectName("ghost")
    more_menu = QMenu(more_btn)
    win.expand_btn = more_menu.addAction("Expand playlist")
    win.expand_btn.setToolTip("Queue every item from a playlist or channel")
    win.expand_btn.triggered.connect(win._on_expand_playlist)
    win.recover_btn = more_menu.addAction("Recover Twitch VOD")
    win.recover_btn.triggered.connect(win._on_recover_vod)
    more_menu.addSeparator()
    win.clip_btn = more_menu.addAction("Clipboard watch")
    win.clip_btn.setCheckable(True)
    win.clip_btn.triggered.connect(win._on_toggle_clipboard)
    more_btn.setMenu(more_menu)
    utility_lay.addWidget(more_btn)
    url_lay.addWidget(utility_bar)

    win.info_label = QLabel("")
    win.info_label.setObjectName("streamInfo")
    win.info_label.setWordWrap(True)
    win.info_label.setVisible(False)
    url_lay.addWidget(win.info_label)

    # VOD picker subwidget
    win.vod_widget = QFrame()
    win.vod_widget.setObjectName("subtleCard")
    vod_main_lay = QVBoxLayout(win.vod_widget)
    vod_main_lay.setContentsMargins(14, 14, 14, 14)
    vod_main_lay.setSpacing(10)

    vod_header = QHBoxLayout()
    vod_header_copy = QVBoxLayout()
    vod_header_copy.setSpacing(2)
    vod_title = QLabel("Available VODs")
    vod_title.setObjectName("sectionTitle")
    vod_hint = QLabel("Select one or more VODs to load for inspection or download in a batch.")
    vod_hint.setObjectName("sectionBody")
    vod_hint.setWordWrap(True)
    vod_header_copy.addWidget(vod_title)
    vod_header_copy.addWidget(vod_hint)
    vod_header.addLayout(vod_header_copy, 1)

    win.vod_summary_label = QLabel("Inspect a channel to browse available VODs.")
    win.vod_summary_label.setObjectName("tableHint")
    vod_header.addWidget(win.vod_summary_label)

    win.vod_select_all_cb = QCheckBox("Select All")
    win.vod_select_all_cb.setChecked(False)
    win.vod_select_all_cb.stateChanged.connect(win._on_vod_select_all)
    vod_header.addWidget(win.vod_select_all_cb)
    vod_main_lay.addLayout(vod_header)

    win.vod_table = QTableWidget()
    win.vod_table.setColumnCount(6)
    win.vod_table.setHorizontalHeaderLabels(["", "Platform", "Title", "Date", "Duration", "Views"])
    vh = win.vod_table.horizontalHeader()
    vh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    vh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    vh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    vh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
    vh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    vh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
    win.vod_table.setColumnWidth(0, 36)
    win.vod_table.setColumnWidth(1, 84)
    win.vod_table.setColumnWidth(3, 160)
    win.vod_table.setColumnWidth(4, 96)
    win.vod_table.setColumnWidth(5, 72)
    win.vod_table.verticalHeader().setVisible(False)
    win.vod_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.vod_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    win.vod_table.setMaximumHeight(300)
    style_table(win.vod_table, 42)
    vod_main_lay.addWidget(win.vod_table)

    vod_btn_row = QHBoxLayout()
    win.vod_load_more_btn = QPushButton("Load More VODs")
    win.vod_load_more_btn.setObjectName("ghost")
    win.vod_load_more_btn.setToolTip("Fetch the next page of VODs from the platform API")
    win.vod_load_more_btn.clicked.connect(win._on_vod_load_more)
    win.vod_load_more_btn.setVisible(False)
    vod_btn_row.addWidget(win.vod_load_more_btn)
    vod_btn_row.addStretch(1)
    win.vod_load_btn = QPushButton("Load Selected")
    win.vod_load_btn.setObjectName("secondary")
    win.vod_load_btn.clicked.connect(win._on_vod_load_single)
    vod_btn_row.addWidget(win.vod_load_btn)
    win.vod_queue_btn = QPushButton("Queue Selected")
    win.vod_queue_btn.setObjectName("secondary")
    win.vod_queue_btn.setToolTip("Add checked VODs to the download queue for concurrent downloading")
    win.vod_queue_btn.clicked.connect(win._on_vod_queue_selected)
    vod_btn_row.addWidget(win.vod_queue_btn)
    win.vod_dl_all_btn = QPushButton("Download All Checked")
    win.vod_dl_all_btn.setObjectName("primary")
    win.vod_dl_all_btn.clicked.connect(win._on_vod_download_all)
    vod_btn_row.addWidget(win.vod_dl_all_btn)
    vod_main_lay.addLayout(vod_btn_row)

    win.vod_widget.setVisible(False)
    url_lay.addWidget(win.vod_widget)
    root.addWidget(url_card)

    # Controls card — quality / segment / output folder
    controls_card = QFrame()
    controls_card.setObjectName("optionsRow")
    controls_lay = QGridLayout(controls_card)
    controls_lay.setContentsMargins(4, 4, 4, 6)
    controls_lay.setHorizontalSpacing(18)
    controls_lay.setVerticalSpacing(8)

    quality_block, quality_lay = make_field_block("Quality")
    win.quality_combo = QComboBox()
    win.quality_combo.setEnabled(False)
    win.quality_combo.currentIndexChanged.connect(win._on_quality_changed)
    quality_lay.addWidget(win.quality_combo)
    controls_lay.addWidget(quality_block, 0, 2)

    segment_block, segment_lay = make_field_block("Segments")
    win.segment_combo = QComboBox()
    win._segment_options = [
        ("15 minutes", 900), ("30 minutes", 1800), ("1 hour", 3600),
        ("2 hours", 7200), ("4 hours", 14400), ("Full stream", 0),
    ]
    for label, _ in win._segment_options:
        win.segment_combo.addItem(label)
    win.segment_combo.setCurrentIndex(2)
    win.segment_combo.currentIndexChanged.connect(win._on_segment_length_changed)
    segment_lay.addWidget(win.segment_combo)
    controls_lay.addWidget(segment_block, 0, 3)

    # Time-range crop (F21) — optional start/end for partial downloads
    crop_block, crop_lay = make_field_block("Time range (optional)")
    crop_row = QHBoxLayout()
    crop_row.setSpacing(8)
    crop_start_label = QLabel("Start:")
    crop_start_label.setFixedWidth(36)
    crop_row.addWidget(crop_start_label)
    win.crop_start_input = QLineEdit()
    win.crop_start_input.setPlaceholderText("HH:MM:SS")
    win.crop_start_input.setClearButtonEnabled(True)
    win.crop_start_input.setFixedWidth(100)
    crop_row.addWidget(win.crop_start_input)
    crop_end_label = QLabel("End:")
    crop_end_label.setFixedWidth(28)
    crop_row.addWidget(crop_end_label)
    win.crop_end_input = QLineEdit()
    win.crop_end_input.setPlaceholderText("HH:MM:SS")
    win.crop_end_input.setClearButtonEnabled(True)
    win.crop_end_input.setFixedWidth(100)
    crop_row.addWidget(win.crop_end_input)
    crop_row.addStretch(1)
    crop_lay.addLayout(crop_row)
    controls_lay.addWidget(crop_block, 1, 0, 1, 4)

    output_block, output_lay = make_field_block("Output folder")
    output_row = QHBoxLayout()
    output_row.setSpacing(8)
    win.output_input = QLineEdit(str(_default_output_dir()))
    win.output_input.setClearButtonEnabled(True)
    win.output_input.textChanged.connect(win._refresh_download_summary)
    output_row.addWidget(win.output_input, 1)
    browse_btn = QPushButton("…")
    browse_btn.setObjectName("secondary")
    browse_btn.setFixedWidth(42)
    browse_btn.setToolTip("Choose output folder")
    browse_btn.clicked.connect(win._on_browse)
    output_row.addWidget(browse_btn)
    output_lay.addLayout(output_row)
    controls_lay.addWidget(output_block, 0, 0, 1, 2)
    controls_lay.setColumnStretch(0, 2)
    controls_lay.setColumnStretch(1, 2)
    controls_lay.setColumnStretch(2, 2)
    controls_lay.setColumnStretch(3, 2)
    crop_block.setVisible(False)
    root.addWidget(controls_card)

    win.track_section = QFrame()
    win.track_section.setObjectName("workSection")
    track_lay = QVBoxLayout(win.track_section)
    track_lay.setContentsMargins(14, 12, 14, 12)
    track_lay.setSpacing(8)
    track_header = QHBoxLayout()
    track_title = QLabel("Media tracks")
    track_title.setObjectName("sectionTitle")
    track_header.addWidget(track_title)
    track_header.addStretch(1)
    win.track_summary_label = QLabel("")
    win.track_summary_label.setObjectName("tableHint")
    track_header.addWidget(win.track_summary_label)
    track_lay.addLayout(track_header)
    win.track_table = QTableWidget()
    win.track_table.setColumnCount(5)
    win.track_table.setHorizontalHeaderLabels(
        ["Use", "Type", "Language", "Track", "Codec / rate"]
    )
    track_header_view = win.track_table.horizontalHeader()
    track_header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    track_header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    track_header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    track_header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    track_header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
    win.track_table.setColumnWidth(0, 48)
    win.track_table.setColumnWidth(1, 90)
    win.track_table.setColumnWidth(2, 100)
    win.track_table.verticalHeader().setVisible(False)
    win.track_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    win.track_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.track_table.setMaximumHeight(240)
    style_table(win.track_table, 36)
    track_lay.addWidget(win.track_table)
    win.track_section.setVisible(False)
    win._track_checks = []
    root.addWidget(win.track_section)

    # ── Per-Download Settings Override (F18) ──────────────────────
    adv_toggle_row = QHBoxLayout()
    adv_toggle_row.setSpacing(6)
    range_toggle_btn = QPushButton("Time range")
    range_toggle_btn.setObjectName("ghost")
    range_toggle_btn.setCheckable(True)
    range_toggle_btn.toggled.connect(crop_block.setVisible)
    adv_toggle_row.addWidget(range_toggle_btn)
    win.adv_toggle_btn = QPushButton("Advanced")
    win.adv_toggle_btn.setObjectName("ghost")
    win.adv_toggle_btn.setCheckable(True)
    win.adv_override_badge = QLabel("")
    win.adv_override_badge.setStyleSheet(
        f"background:transparent; color:{CAT['peach']}; border:none; "
        f"font-size:12px; font-weight:700; padding:0 4px;"
    )
    win.adv_override_badge.setVisible(False)
    adv_toggle_row.addWidget(win.adv_toggle_btn)
    adv_toggle_row.addWidget(win.adv_override_badge)
    adv_toggle_row.addStretch(1)
    root.addLayout(adv_toggle_row)

    win.adv_frame = QFrame()
    win.adv_frame.setObjectName("optionsRow")
    win.adv_frame.setVisible(False)
    adv_lay = QGridLayout(win.adv_frame)
    adv_lay.setContentsMargins(14, 14, 14, 14)
    adv_lay.setHorizontalSpacing(12)
    adv_lay.setVerticalSpacing(10)

    # Override: post-processing preset
    adv_lay.addWidget(QLabel("Post-process preset:"), 0, 0)
    win.adv_pp_combo = QComboBox()
    win.adv_pp_combo.addItem("(use global setting)", userData="")
    adv_lay.addWidget(win.adv_pp_combo, 0, 1)

    # Override: rate limit
    adv_lay.addWidget(QLabel("Rate limit:"), 1, 0)
    win.adv_rate_input = QLineEdit()
    win.adv_rate_input.setPlaceholderText("e.g. 5M (blank = global)")
    win.adv_rate_input.setFixedWidth(140)
    adv_lay.addWidget(win.adv_rate_input, 1, 1)

    # Override: parallel connections
    adv_lay.addWidget(QLabel("Parallel connections:"), 2, 0)
    win.adv_parallel_spin = QSpinBox()
    win.adv_parallel_spin.setRange(0, 16)
    win.adv_parallel_spin.setSpecialValueText("(global)")
    win.adv_parallel_spin.setFixedWidth(80)
    adv_lay.addWidget(win.adv_parallel_spin, 2, 1)

    # Override: output folder template
    adv_lay.addWidget(QLabel("Folder template:"), 3, 0)
    win.adv_folder_tpl_input = QLineEdit()
    win.adv_folder_tpl_input.setPlaceholderText("(blank = global template)")
    adv_lay.addWidget(win.adv_folder_tpl_input, 3, 1)

    # Override: file template
    adv_lay.addWidget(QLabel("File template:"), 4, 0)
    win.adv_file_tpl_input = QLineEdit()
    win.adv_file_tpl_input.setPlaceholderText("(blank = global template)")
    adv_lay.addWidget(win.adv_file_tpl_input, 4, 1)

    # yt-dlp direct output controls
    adv_lay.addWidget(QLabel("Raw format spec:"), 5, 0)
    win.adv_format_input = QLineEdit()
    win.adv_format_input.setPlaceholderText("e.g. 137+251 or bv*+ba/b")
    win.adv_format_input.setToolTip(
        "Passed verbatim to yt-dlp -f for yt-dlp direct sources"
    )
    adv_lay.addWidget(win.adv_format_input, 5, 1)

    adv_lay.addWidget(QLabel("Format sort:"), 6, 0)
    win.adv_format_sort_combo = QComboBox()
    win.adv_format_sort_combo.addItem("(source default)", userData="")
    win.adv_format_sort_combo.addItem("Prefer AV1", userData="prefer-av1")
    win.adv_format_sort_combo.addItem("Cap at 2160p", userData="cap-2160p")
    win.adv_format_sort_combo.addItem("Cap at 1080p", userData="cap-1080p")
    win.adv_format_sort_combo.addItem("Cap at 720p", userData="cap-720p")
    win.adv_format_sort_combo.addItem("Smallest file", userData="smallest")
    win.adv_format_sort_combo.setToolTip(
        "Safe yt-dlp -S presets; a resolution cap prefers the best format at or below it"
    )
    adv_lay.addWidget(win.adv_format_sort_combo, 6, 1)

    adv_lay.addWidget(QLabel("Video container:"), 7, 0)
    win.adv_container_combo = QComboBox()
    win.adv_container_combo.addItem("MP4 (default)", userData="")
    win.adv_container_combo.addItem("MKV", userData="mkv")
    win.adv_container_combo.addItem("WebM", userData="webm")
    win.adv_container_combo.addItem("Original", userData="original")
    win.adv_container_combo.setToolTip(
        "Merge/remux video without re-encoding; Original keeps the source container"
    )
    adv_lay.addWidget(win.adv_container_combo, 7, 1)

    adv_lay.addWidget(QLabel("Audio extraction:"), 8, 0)
    audio_row = QHBoxLayout()
    win.adv_audio_combo = QComboBox()
    win.adv_audio_combo.addItem("Video download", userData="")
    for audio_format in ("best", "mp3", "m4a", "opus", "flac", "wav"):
        win.adv_audio_combo.addItem(audio_format.upper(), userData=audio_format)
    audio_row.addWidget(win.adv_audio_combo, 1)
    win.adv_audio_quality_input = QLineEdit()
    win.adv_audio_quality_input.setPlaceholderText("quality: 0-10 or 128K")
    win.adv_audio_quality_input.setToolTip(
        "Optional encoder quality; 0 is best, 10 is worst, or use a bitrate such as 128K"
    )
    win.adv_audio_quality_input.setEnabled(False)
    audio_row.addWidget(win.adv_audio_quality_input, 1)
    adv_lay.addLayout(audio_row, 8, 1)

    adv_lay.addWidget(QLabel("Subtitles:"), 9, 0)
    win.adv_subtitle_mode_combo = QComboBox()
    win.adv_subtitle_mode_combo.addItem("Use global setting", userData="")
    win.adv_subtitle_mode_combo.addItem("No subtitles", userData="disabled")
    win.adv_subtitle_mode_combo.addItem(
        "Choose source languages", userData="custom"
    )
    win.adv_subtitle_mode_combo.model().item(2).setEnabled(False)
    adv_lay.addWidget(win.adv_subtitle_mode_combo, 9, 1)

    adv_lay.addWidget(QLabel("Subtitle languages:"), 10, 0)
    win.adv_subtitle_list = QListWidget()
    win.adv_subtitle_list.setSelectionMode(
        QAbstractItemView.SelectionMode.MultiSelection
    )
    win.adv_subtitle_list.setMaximumHeight(105)
    win.adv_subtitle_list.setEnabled(False)
    win.adv_subtitle_list.setToolTip(
        "Fetch a yt-dlp source to list its subtitle languages"
    )
    adv_lay.addWidget(win.adv_subtitle_list, 10, 1)

    adv_lay.addWidget(QLabel("Subtitle output:"), 11, 0)
    subtitle_output_row = QHBoxLayout()
    win.adv_subtitle_auto_check = QCheckBox("Include automatic captions")
    win.adv_subtitle_auto_check.setChecked(True)
    subtitle_output_row.addWidget(win.adv_subtitle_auto_check)
    win.adv_subtitle_convert_combo = QComboBox()
    win.adv_subtitle_convert_combo.addItem("Keep format", userData="")
    for sub_format in ("srt", "vtt", "ass"):
        win.adv_subtitle_convert_combo.addItem(
            f"Convert {sub_format.upper()}", userData=sub_format
        )
    subtitle_output_row.addWidget(win.adv_subtitle_convert_combo, 1)
    win.adv_subtitle_delivery_combo = QComboBox()
    win.adv_subtitle_delivery_combo.addItem("Embed", userData="embed")
    win.adv_subtitle_delivery_combo.addItem("Sidecar", userData="sidecar")
    subtitle_output_row.addWidget(win.adv_subtitle_delivery_combo, 1)
    adv_lay.addLayout(subtitle_output_row, 11, 1)
    _refresh_adv_subtitle_controls(win)

    from ...download_options import (
        SPONSORBLOCK_CATEGORIES, SPONSORBLOCK_NON_REMOVABLE,
    )
    adv_lay.addWidget(QLabel("SponsorBlock:"), 12, 0)
    win.adv_sponsorblock_mode_combo = QComboBox()
    win.adv_sponsorblock_mode_combo.addItem(
        "Use global setting", userData=""
    )
    win.adv_sponsorblock_mode_combo.addItem("Disabled", userData="disabled")
    win.adv_sponsorblock_mode_combo.addItem(
        "Custom category actions", userData="custom"
    )
    adv_lay.addWidget(win.adv_sponsorblock_mode_combo, 12, 1)

    adv_lay.addWidget(QLabel("Category actions:"), 13, 0)
    win.adv_sponsorblock_table = QTableWidget(
        len(SPONSORBLOCK_CATEGORIES), 2
    )
    win.adv_sponsorblock_table.setHorizontalHeaderLabels(
        ["Category", "Action"]
    )
    sponsor_header = win.adv_sponsorblock_table.horizontalHeader()
    sponsor_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    sponsor_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    win.adv_sponsorblock_table.verticalHeader().setVisible(False)
    win.adv_sponsorblock_table.setEditTriggers(
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    win.adv_sponsorblock_table.setMaximumHeight(270)
    style_table(win.adv_sponsorblock_table, 34)
    win.adv_sponsorblock_action_combos = {}
    for row, (category, label) in enumerate(SPONSORBLOCK_CATEGORIES.items()):
        item = QTableWidgetItem(label)
        item.setToolTip(category)
        win.adv_sponsorblock_table.setItem(row, 0, item)
        combo = QComboBox()
        combo.addItem("Ignore", userData="")
        combo.addItem("Mark chapter", userData="mark")
        if category not in SPONSORBLOCK_NON_REMOVABLE:
            combo.addItem("Remove segment", userData="remove")
        win.adv_sponsorblock_table.setCellWidget(row, 1, combo)
        win.adv_sponsorblock_action_combos[category] = combo
    adv_lay.addWidget(win.adv_sponsorblock_table, 13, 1)

    adv_lay.addWidget(QLabel("SponsorBlock API:"), 14, 0)
    win.adv_sponsorblock_api_input = QLineEdit()
    win.adv_sponsorblock_api_input.setPlaceholderText(
        "Default API, or a custom HTTPS base URL"
    )
    adv_lay.addWidget(win.adv_sponsorblock_api_input, 14, 1)
    _refresh_adv_sponsorblock_controls(win)

    adv_lay.addWidget(QLabel("Playlist items:"), 15, 0)
    win.adv_playlist_items_input = QLineEdit()
    win.adv_playlist_items_input.setPlaceholderText(
        "e.g. 1:10,15,20:30:2 (blank = all)"
    )
    adv_lay.addWidget(win.adv_playlist_items_input, 15, 1)

    adv_lay.addWidget(QLabel("Playlist dates:"), 16, 0)
    playlist_date_row = QHBoxLayout()
    win.adv_playlist_after_input = QLineEdit()
    win.adv_playlist_after_input.setPlaceholderText("after YYYYMMDD")
    win.adv_playlist_before_input = QLineEdit()
    win.adv_playlist_before_input.setPlaceholderText("before YYYYMMDD")
    playlist_date_row.addWidget(win.adv_playlist_after_input)
    playlist_date_row.addWidget(win.adv_playlist_before_input)
    adv_lay.addLayout(playlist_date_row, 16, 1)

    adv_lay.addWidget(QLabel("Playlist filter:"), 17, 0)
    win.adv_playlist_filter_input = QLineEdit()
    win.adv_playlist_filter_input.setPlaceholderText(
        "yt-dlp match filter, e.g. duration > 60 & !is_live"
    )
    adv_lay.addWidget(win.adv_playlist_filter_input, 17, 1)

    adv_lay.addWidget(QLabel("Playlist sync:"), 18, 0)
    playlist_sync_row = QHBoxLayout()
    win.adv_playlist_max_spin = QSpinBox()
    win.adv_playlist_max_spin.setRange(0, 10000)
    win.adv_playlist_max_spin.setSpecialValueText("No maximum")
    playlist_sync_row.addWidget(win.adv_playlist_max_spin)
    win.adv_playlist_archive_check = QCheckBox(
        "Incremental archive (stop at existing)"
    )
    playlist_sync_row.addWidget(win.adv_playlist_archive_check)
    adv_lay.addLayout(playlist_sync_row, 18, 1)

    adv_lay.addWidget(QLabel("yt-dlp fragments:"), 19, 0)
    transfer_fragment_row = QHBoxLayout()
    win.adv_ytdlp_fragments_spin = QSpinBox()
    win.adv_ytdlp_fragments_spin.setRange(0, 32)
    win.adv_ytdlp_fragments_spin.setSpecialValueText("Global")
    transfer_fragment_row.addWidget(win.adv_ytdlp_fragments_spin)
    win.adv_ytdlp_retries_input = QLineEdit()
    win.adv_ytdlp_retries_input.setPlaceholderText("retries: global/infinite")
    transfer_fragment_row.addWidget(win.adv_ytdlp_retries_input)
    win.adv_ytdlp_fragment_retries_input = QLineEdit()
    win.adv_ytdlp_fragment_retries_input.setPlaceholderText(
        "fragment retries: global/infinite"
    )
    transfer_fragment_row.addWidget(win.adv_ytdlp_fragment_retries_input)
    adv_lay.addLayout(transfer_fragment_row, 19, 1)

    adv_lay.addWidget(QLabel("yt-dlp retry policy:"), 20, 0)
    transfer_retry_row = QHBoxLayout()
    win.adv_ytdlp_retry_sleep_input = QLineEdit()
    win.adv_ytdlp_retry_sleep_input.setPlaceholderText(
        "sleep, e.g. fragment:exp=1:20"
    )
    transfer_retry_row.addWidget(win.adv_ytdlp_retry_sleep_input)
    win.adv_ytdlp_unavailable_combo = QComboBox()
    win.adv_ytdlp_unavailable_combo.addItem("Unavailable: global", userData="")
    win.adv_ytdlp_unavailable_combo.addItem("Unavailable: skip", userData="skip")
    win.adv_ytdlp_unavailable_combo.addItem("Unavailable: abort", userData="abort")
    transfer_retry_row.addWidget(win.adv_ytdlp_unavailable_combo)
    adv_lay.addLayout(transfer_retry_row, 20, 1)

    adv_lay.addWidget(QLabel("yt-dlp live depth:"), 21, 0)
    transfer_live_row = QHBoxLayout()
    win.adv_ytdlp_throttled_input = QLineEdit()
    win.adv_ytdlp_throttled_input.setPlaceholderText("throttled rate: global")
    transfer_live_row.addWidget(win.adv_ytdlp_throttled_input)
    win.adv_ytdlp_wait_input = QLineEdit()
    win.adv_ytdlp_wait_input.setPlaceholderText("wait seconds or MIN-MAX")
    transfer_live_row.addWidget(win.adv_ytdlp_wait_input)
    win.adv_ytdlp_live_combo = QComboBox()
    win.adv_ytdlp_live_combo.addItem("Live start: global", userData=None)
    win.adv_ytdlp_live_combo.addItem("Live from start", userData=True)
    win.adv_ytdlp_live_combo.addItem("Live current edge", userData=False)
    transfer_live_row.addWidget(win.adv_ytdlp_live_combo)
    adv_lay.addLayout(transfer_live_row, 21, 1)

    adv_lay.addWidget(QLabel("yt-dlp embedding:"), 22, 0)
    transfer_embed_row = QHBoxLayout()
    for name, label in (
        ("chapters", "Chapters"),
        ("metadata", "Metadata"),
        ("thumbnail", "Thumbnail"),
    ):
        combo = QComboBox()
        combo.addItem(f"{label}: global", userData=None)
        combo.addItem(f"{label}: on", userData=True)
        combo.addItem(f"{label}: off", userData=False)
        setattr(win, f"adv_ytdlp_embed_{name}_combo", combo)
        transfer_embed_row.addWidget(combo)
    adv_lay.addLayout(transfer_embed_row, 22, 1)

    adv_lay.addWidget(QLabel("yt-dlp arguments:"), 23, 0)
    win.adv_ytdlp_template_combo = QComboBox()
    win.adv_ytdlp_template_combo.setToolTip(
        "Attach a named structured argv template managed in Settings"
    )
    adv_lay.addWidget(win.adv_ytdlp_template_combo, 23, 1)
    _populate_adv_ytdlp_templates(win)

    adv_lay.addWidget(QLabel("HLS clear key:"), 24, 0)
    hls_key_row = QHBoxLayout()
    win.adv_hls_key_input = QLineEdit()
    win.adv_hls_key_input.setMaxLength(4096)
    win.adv_hls_key_input.setEchoMode(QLineEdit.EchoMode.Password)
    win.adv_hls_key_input.setPlaceholderText(
        "Authorized key URI or 32-digit AES-128 key"
    )
    win.adv_hls_key_input.setToolTip(
        "Expert non-DRM recovery only. Overrides a wrong EXT-X-KEY URI/value "
        "through yt-dlp's native HLS downloader and is not persisted."
    )
    hls_key_row.addWidget(win.adv_hls_key_input, 2)
    win.adv_hls_iv_input = QLineEdit()
    win.adv_hls_iv_input.setMaxLength(66)
    win.adv_hls_iv_input.setEchoMode(QLineEdit.EchoMode.Password)
    win.adv_hls_iv_input.setPlaceholderText("Optional IV (hex)")
    win.adv_hls_iv_input.setToolTip(
        "Optional 1-32 digit hexadecimal initialization vector"
    )
    hls_key_row.addWidget(win.adv_hls_iv_input, 1)
    adv_lay.addLayout(hls_key_row, 24, 1)

    # Reset button
    adv_reset_btn = QPushButton("Reset overrides")
    adv_reset_btn.setObjectName("ghost")
    adv_reset_btn.setFixedWidth(130)
    adv_reset_btn.clicked.connect(lambda: _reset_adv_overrides(win))
    adv_lay.addWidget(adv_reset_btn, 25, 1)

    root.addWidget(win.adv_frame)

    def _on_adv_toggle(checked):
        win.adv_frame.setVisible(checked)
        win.adv_toggle_btn.setText("Hide advanced" if checked else "Advanced")
    win.adv_toggle_btn.toggled.connect(_on_adv_toggle)

    # Populate PP preset choices and wire badge updates
    _populate_adv_pp(win)

    def _update_adv_badge():
        active = bool(get_adv_overrides(win))
        win.adv_override_badge.setVisible(active)
        win.adv_override_badge.setText("Modified" if active else "")

    win.adv_pp_combo.currentIndexChanged.connect(lambda _: _update_adv_badge())
    win.adv_rate_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_parallel_spin.valueChanged.connect(lambda _: _update_adv_badge())
    win.adv_folder_tpl_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_file_tpl_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_format_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_format_sort_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_container_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_audio_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_audio_combo.currentIndexChanged.connect(
        lambda _: win.adv_audio_quality_input.setEnabled(
            bool(win.adv_audio_combo.currentData())
        )
    )
    win.adv_audio_quality_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_hls_key_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_hls_iv_input.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_subtitle_mode_combo.currentIndexChanged.connect(
        lambda _: _refresh_adv_subtitle_controls(win)
    )
    win.adv_subtitle_mode_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_subtitle_list.itemSelectionChanged.connect(_update_adv_badge)
    win.adv_subtitle_auto_check.toggled.connect(
        lambda _: _refresh_adv_subtitle_controls(win)
    )
    win.adv_subtitle_auto_check.toggled.connect(lambda _: _update_adv_badge())
    win.adv_subtitle_convert_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_subtitle_delivery_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_sponsorblock_mode_combo.currentIndexChanged.connect(
        lambda _: _refresh_adv_sponsorblock_controls(win)
    )
    win.adv_sponsorblock_mode_combo.currentIndexChanged.connect(
        lambda _: _update_adv_badge()
    )
    for combo in win.adv_sponsorblock_action_combos.values():
        combo.currentIndexChanged.connect(lambda _: _update_adv_badge())
    win.adv_sponsorblock_api_input.textChanged.connect(
        lambda _: _update_adv_badge()
    )
    for field in (
        win.adv_playlist_items_input, win.adv_playlist_after_input,
        win.adv_playlist_before_input, win.adv_playlist_filter_input,
    ):
        field.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_playlist_max_spin.valueChanged.connect(
        lambda _: _update_adv_badge()
    )
    win.adv_playlist_archive_check.toggled.connect(
        lambda _: _update_adv_badge()
    )
    for widget in (
        win.adv_ytdlp_retries_input,
        win.adv_ytdlp_fragment_retries_input,
        win.adv_ytdlp_retry_sleep_input,
        win.adv_ytdlp_throttled_input,
        win.adv_ytdlp_wait_input,
    ):
        widget.textChanged.connect(lambda _: _update_adv_badge())
    win.adv_ytdlp_fragments_spin.valueChanged.connect(
        lambda _: _update_adv_badge()
    )
    for combo in (
        win.adv_ytdlp_unavailable_combo,
        win.adv_ytdlp_live_combo,
        win.adv_ytdlp_embed_chapters_combo,
        win.adv_ytdlp_embed_metadata_combo,
        win.adv_ytdlp_embed_thumbnail_combo,
        win.adv_ytdlp_template_combo,
    ):
        combo.currentIndexChanged.connect(lambda _: _update_adv_badge())

    # Splitter: segments table + runtime log
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.setChildrenCollapsible(False)

    table_frame = QFrame()
    table_frame.setObjectName("workSection")
    table_lay = QVBoxLayout(table_frame)
    table_lay.setContentsMargins(4, 10, 4, 4)
    table_lay.setSpacing(8)

    table_header = QHBoxLayout()
    table_copy = QVBoxLayout()
    table_copy.setSpacing(3)
    sec2 = QLabel("Segments")
    sec2.setObjectName("sectionTitle")
    table_copy.addWidget(sec2)
    table_header.addLayout(table_copy, 1)

    win.segment_summary_label = QLabel("Segments will appear after metadata is loaded.")
    win.segment_summary_label.setObjectName("tableHint")
    table_header.addWidget(win.segment_summary_label)

    win.select_all_cb = QCheckBox("Select All")
    win.select_all_cb.setChecked(True)
    win.select_all_cb.stateChanged.connect(win._on_select_all)
    table_header.addWidget(win.select_all_cb)
    table_lay.addLayout(table_header)

    win.table = QTableWidget()
    win.table.setColumnCount(5)
    win.table.setHorizontalHeaderLabels(
        ["", "Segment", "Time Range", "Progress", "Size"]
    )
    th = win.table.horizontalHeader()
    th.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    th.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    th.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    th.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    th.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    win.table.setColumnWidth(0, 36)
    win.table.setColumnWidth(1, 140)
    win.table.setColumnWidth(4, 96)
    win.table.verticalHeader().setVisible(False)
    win.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    style_table(win.table, 46)
    table_lay.addWidget(win.table)
    splitter.addWidget(table_frame)

    log_frame = QFrame()
    log_frame.setObjectName("workSection")
    log_lay = QVBoxLayout(log_frame)
    log_lay.setContentsMargins(18, 10, 4, 4)
    log_lay.setSpacing(8)

    log_header = QHBoxLayout()
    log_copy = QVBoxLayout()
    log_copy.setSpacing(3)
    sec3 = QLabel("Activity")
    sec3.setObjectName("sectionTitle")
    log_copy.addWidget(sec3)
    log_header.addLayout(log_copy, 1)
    clear_log_btn = QPushButton("Clear Log")
    clear_log_btn.setObjectName("ghost")
    clear_log_btn.clicked.connect(lambda: win.log_text.clear())
    log_header.addWidget(clear_log_btn)
    log_lay.addLayout(log_header)

    win.log_text = QTextEdit()
    win.log_text.setObjectName("log")
    win.log_text.setReadOnly(True)
    win.log_text.setPlainText("Ready")
    log_lay.addWidget(win.log_text)
    splitter.addWidget(log_frame)
    splitter.setSizes([450, 220])
    root.addWidget(splitter, 1)

    # Download action row
    dl_row = QHBoxLayout()
    dl_row.addStretch(1)
    win.schedule_btn = QPushButton("Schedule...")
    win.schedule_btn.setObjectName("secondary")
    win.schedule_btn.setToolTip("Queue the URL and start it at a future time")
    win.schedule_btn.clicked.connect(win._on_schedule_url)
    dl_row.addWidget(win.schedule_btn)
    win.copy_command_btn = QPushButton("Copy command")
    win.copy_command_btn.setObjectName("secondary")
    win.copy_command_btn.setEnabled(False)
    win.copy_command_btn.setToolTip(
        "Copy the exact standalone yt-dlp or FFmpeg command for the latest job"
    )
    win.copy_command_btn.clicked.connect(win._on_copy_download_command)
    dl_row.addWidget(win.copy_command_btn)
    win.download_btn = QPushButton("Download Selected")
    win.download_btn.setObjectName("primary")
    win.download_btn.setEnabled(False)
    win.download_btn.clicked.connect(win._on_download)
    dl_row.addWidget(win.download_btn)
    root.addLayout(dl_row)

    # Queue panel — shows pending items
    queue_card = QFrame()
    queue_card.setObjectName("workSection")
    qcard_lay = QVBoxLayout(queue_card)
    qcard_lay.setContentsMargins(4, 10, 18, 4)
    qcard_lay.setSpacing(8)
    queue_header = QHBoxLayout()
    qt = QLabel("Queue")
    qt.setObjectName("sectionTitle")
    queue_header.addWidget(qt)
    queue_header.addStretch()
    clear_queue_btn = QPushButton("Clear Queue")
    clear_queue_btn.setObjectName("ghost")
    clear_queue_btn.clicked.connect(win._on_clear_queue)
    queue_header.addWidget(clear_queue_btn)
    qcard_lay.addLayout(queue_header)
    win.queue_table = QTableWidget()
    win.queue_table.setColumnCount(6)
    win.queue_table.setHorizontalHeaderLabels(
        ["Status", "Platform", "Title", "Added / Scheduled", "", ""]
    )
    qh = win.queue_table.horizontalHeader()
    qh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    qh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    qh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    qh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    qh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    qh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
    win.queue_table.setColumnWidth(0, 96)
    win.queue_table.setColumnWidth(1, 90)
    win.queue_table.setColumnWidth(3, 160)
    win.queue_table.setColumnWidth(4, 66)   # move up/down
    win.queue_table.setColumnWidth(5, 84)   # remove
    win.queue_table.verticalHeader().setVisible(False)
    win.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    style_table(win.queue_table, 36)
    qcard_lay.addWidget(win.queue_table)
    win.queue_empty_state = QFrame()
    empty_lay = QVBoxLayout(win.queue_empty_state)
    empty_lay.setContentsMargins(12, 28, 12, 12)
    empty_lay.setSpacing(5)
    empty_lay.addStretch(1)
    empty_title = QLabel("No downloads in the queue")
    empty_title.setObjectName("emptyStateTitle")
    empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_lay.addWidget(empty_title)
    empty_body = QLabel("Add a URL above to get started.")
    empty_body.setObjectName("emptyStateBody")
    empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_lay.addWidget(empty_body)
    empty_lay.addStretch(2)
    qcard_lay.addWidget(win.queue_empty_state, 1)
    root.addWidget(queue_card)

    # Main working surface: queue and activity share the viewport. Segment
    # details remain available below once metadata has been fetched.
    work_index = root.indexOf(splitter)
    root.removeWidget(splitter)
    root.removeWidget(queue_card)
    table_frame.setParent(None)
    log_frame.setParent(None)
    splitter.deleteLater()

    work_surface = QSplitter(Qt.Orientation.Horizontal)
    work_surface.setObjectName("workSurface")
    work_surface.setChildrenCollapsible(False)
    queue_card.setMinimumWidth(560)
    log_frame.setMinimumWidth(320)
    log_frame.setMinimumHeight(240)
    work_surface.addWidget(queue_card)
    work_surface.addWidget(log_frame)
    work_surface.setStretchFactor(0, 2)
    work_surface.setStretchFactor(1, 1)
    work_surface.setSizes([860, 420])
    root.insertWidget(work_index, work_surface, 1)
    win.segments_section = table_frame
    win.segments_section.setVisible(False)
    root.addWidget(table_frame)

    win._refresh_download_summary()
    win._refresh_queue_table()

    return page

import os
import re
import time
from collections import deque
from datetime import datetime

from PyQt6.QtWidgets import (
    QFileDialog, QProgressBar,
)
from PyQt6.QtGui import QColor, QDesktopServices

from ...extractors import (
    Extractor,
    YtDlpExtractor,
)
from ...workers import (
    FetchWorker,
    DownloadWorker,
    PlaylistExpandWorker as _PlaylistExpandWorker,
    PageScrapeWorker as _PageScrapeWorker,
)
from ...utils import (
    fmt_size as _fmt_size,
    fmt_duration as _fmt_duration,
    safe_filename as _safe_filename,
    render_template as _render_template,
    build_template_context as _build_template_context,
    free_space_bytes as _free_space_bytes,
)
from ..widgets import (
    PLATFORM_BADGES,
    ask_premium_confirmation,
    path_label as _path_label,
)
from ... import db as _db


class DownloadTabMixin(
    DownloadFinalizeMixin, DownloadVodMixin, DownloadQueueMixin
):
    """Download-tab handler methods, mixed into ``StreamKeep``."""

    # ── Summary / metrics ───────────────────────────────────────

    def _refresh_download_summary(self):
        if not hasattr(self, "download_hero_title"):
            return

        url = self.url_input.text().strip() if hasattr(self, "url_input") else ""
        if self.stream_info:
            title = self.stream_info.title or "Ready to download"
            summary_parts = []
            if self.stream_info.platform:
                summary_parts.append(self.stream_info.platform)
            if self.stream_info.channel:
                summary_parts.append(self.stream_info.channel)
            if self.stream_info.duration_str:
                summary_parts.append(self.stream_info.duration_str)
            if self.stream_info.is_live:
                summary_parts.append("Live capture")
            body = "  •  ".join(summary_parts) if summary_parts else "Metadata loaded."
        elif url:
            ext = Extractor.detect(url)
            title = "Source detected" if ext else "New download"
            if ext:
                body = f"{ext.NAME} link recognized. Fetch when ready."
            else:
                body = "Paste a supported stream, VOD, podcast, or media URL."
        else:
            title = "New download"
            body = "Paste a stream, VOD, podcast, or media URL."

        self.download_hero_title.setText(title)
        self.download_hero_body.setText(body)

        platform_value = self.stream_info.platform if self.stream_info else "Auto detect"
        platform_sub = "Detected after fetch" if self.stream_info else "Waiting for a supported URL"
        duration_value = self.stream_info.duration_str if self.stream_info and self.stream_info.duration_str else "Waiting"
        duration_sub = "Stream length" if self.stream_info else "Metadata not loaded yet"

        total_segments = len(self._segment_checks)
        checked_segments = sum(1 for cb in self._segment_checks if cb.isChecked())
        if total_segments:
            selection_value = f"{checked_segments}/{total_segments}"
            selection_sub = "segments selected"
        elif self.stream_info and self.stream_info.total_secs <= 0:
            selection_value = "Live"
            selection_sub = "capture runs until you stop it"
        else:
            selection_value = "Not ready"
            selection_sub = "segments appear after fetch"

        output_path = self.output_input.text().strip() if hasattr(self, "output_input") else ""
        output_sub = output_path if len(output_path) <= 50 else f"...{output_path[-47:]}"
        finalize_active = bool(self._finalize_worker is not None and self._finalize_worker.isRunning())
        finalize_queued = len(self._finalize_tasks)
        if finalize_active:
            if self._finalize_active_total:
                finalize_value = f"{self._finalize_active_step}/{self._finalize_active_total}"
            else:
                finalize_value = "Starting"
            finalize_parts = []
            if self._finalize_active_title:
                finalize_parts.append(self._finalize_active_title[:42])
            if self._finalize_active_label:
                finalize_parts.append(self._finalize_active_label)
            finalize_sub = " | ".join(finalize_parts) or "Preparing background cleanup"
            if finalize_queued:
                finalize_sub = f"{finalize_sub} | {finalize_queued} queued"
        elif finalize_queued:
            finalize_value = f"{finalize_queued} queued"
            finalize_sub = "Waiting for the current background cleanup"
        else:
            finalize_value = "Idle"
            finalize_sub = "Metadata and post-processing will queue here"

        self._set_metric(self.download_platform_value, self.download_platform_sub, platform_value, platform_sub)
        self._set_metric(self.download_duration_value, self.download_duration_sub, duration_value, duration_sub)
        self._set_metric(self.download_selection_value, self.download_selection_sub, selection_value, selection_sub)
        # Append free-disk hint to the output card subline so users see
        # what they have to work with before starting a long download.
        free_bytes = _free_space_bytes(output_path) if output_path else None
        free_label = f"{_fmt_size(free_bytes)} free" if free_bytes else ""
        output_sub_with_free = output_sub or "Choose a destination folder"
        if free_label:
            output_sub_with_free = f"{free_label} \u2022 {output_sub_with_free}"
        self._set_metric(
            self.download_output_value,
            self.download_output_sub,
            _path_label(output_path),
            output_sub_with_free,
        )
        if hasattr(self, "download_finalize_value"):
            self._set_metric(
                self.download_finalize_value,
                self.download_finalize_sub,
                finalize_value,
                finalize_sub,
            )
        self.download_output_value.setToolTip(output_path)
        self.download_output_sub.setToolTip(output_path)
        if hasattr(self, "download_finalize_sub"):
            self.download_finalize_sub.setToolTip(finalize_sub)

        if hasattr(self, "segment_summary_label"):
            if total_segments:
                self.segment_summary_label.setText(f"{checked_segments} of {total_segments} segment(s) selected")
            else:
                self.segment_summary_label.setText("Segments will appear after metadata is loaded.")


    # Monitor tab handlers → streamkeep.ui.tabs.monitor.MonitorTabMixin

    # History tab handlers → streamkeep.ui.tabs.history.HistoryTabMixin

    def _update_badge(self, platform_name=None):
        if platform_name and platform_name in PLATFORM_BADGES:
            badge = PLATFORM_BADGES[platform_name]
            self.platform_badge.setText(f" {badge['text']} ")
            self.platform_badge.setStyleSheet(
                f"background-color: {badge['color']}; color: {CAT['crust']}; "
                f"border-radius: 999px; font-weight: bold; font-size: 11px; padding: 4px 12px;"
            )
            self.platform_badge.setVisible(True)
        else:
            self.platform_badge.setVisible(False)


    # ── URL input / clipboard ───────────────────────────────────

    def _on_url_changed(self, text):
        ext = Extractor.detect(text.strip())
        if ext:
            self._update_badge(ext.NAME)
            ch = ext.extract_channel_id(text.strip())
            if ch and self._can_autofill_output():
                self._apply_auto_output(str(_default_output_dir() / _safe_filename(ch)))
        else:
            self._update_badge(None)
        self._refresh_download_summary()

    def _on_toggle_clipboard(self, checked):
        if checked:
            self.clipboard_monitor.start()
            self._log("[CLIPBOARD] Monitoring started - copy a URL to auto-load")
            self._set_status("Clipboard monitoring active. Copy a supported URL to load it automatically.", "working")
        else:
            self.clipboard_monitor.stop()
            self._log("[CLIPBOARD] Monitoring stopped")
            self._set_status("Clipboard monitoring stopped.", "idle")

    def _on_clipboard_url(self, url):
        # Don't interrupt an active download
        existing = getattr(self, "download_worker", None)
        if existing is not None and existing.isRunning():
            self._log(f"[CLIPBOARD] Ignored {url[:60]}... (download in progress)")
            return
        # Basic URL sanity — reject newlines/control chars
        if "\n" in url or "\r" in url or len(url) > 2048:
            self._log("[CLIPBOARD] Rejected malformed URL")
            return
        # Dedup: ignore if already in the input box (avoids re-fetching on focus switches)
        if url == self.url_input.text().strip():
            return
        # Dedup: ignore if it's the same as the last clipboard URL we accepted
        if url == getattr(self, "_last_clipboard_url", ""):
            return
        self._last_clipboard_url = url
        self._log(f"[CLIPBOARD] Detected: {url}")
        self.url_input.setText(url)
        self._switch_tab(0)  # Switch to Download tab
        self._on_fetch()

    def _remember_url(self, url):
        """Add URL to the top of the recent URLs list (most-recent-first)."""
        if not url:
            return
        previous = list(self._recent_urls)
        # Dedup: move to front if already present
        if url in self._recent_urls:
            self._recent_urls.remove(url)
        self._recent_urls.insert(0, url)
        # Keep the last 30
        self._recent_urls = self._recent_urls[:30]
        if hasattr(self, "_recent_url_model"):
            self._recent_url_model.setStringList(self._recent_urls)
        if self._recent_urls != previous:
            self._schedule_persist_config()


    # ── Fetch / resolve ─────────────────────────────────────────

    def _on_fetch(self, vod_source=None, vod_platform=None, vod_title=None, vod_channel=None):
        url = self.url_input.text().strip()
        if not url:
            return
        self._last_fetch_request = {
            "url": url,
            "vod_source": vod_source or "",
            "vod_platform": vod_platform or "",
            "vod_title": vod_title or "",
            "vod_channel": vod_channel or "",
        }
        # Track recent URLs for the autocomplete dropdown
        if not vod_source:
            self._remember_url(url)
        # Check for URL-based duplicate before hitting the network
        if not vod_source:
            dup = self._find_duplicate(url)
            if dup:
                self._log(f"[DUPLICATE] Already downloaded on {dup.date} to {dup.path}")
                self._set_status(
                    f"Already downloaded {dup.date} to {dup.path}. Fetching anyway.",
                    "warning",
                )
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Fetching")
        self.download_btn.setEnabled(False)
        self.open_folder_btn.setVisible(False)
        if hasattr(self, "trim_btn"):
            self.trim_btn.setVisible(False)
        self.overall_progress.setVisible(False)
        self.quality_combo.clear()
        self.quality_combo.setEnabled(False)
        _populate_adv_subtitles(self, None)
        self.table.setRowCount(0)
        if hasattr(self, "segments_section"):
            self.segments_section.setVisible(False)
        self._segment_checks = []
        self._segment_progress = []
        self.info_label.setVisible(False)
        self.stream_info = None
        if not vod_source:
            self.vod_widget.setVisible(False)
            self._vod_checks = []
            self._refresh_vod_summary()
        self._refresh_download_summary()
        self._set_status("Fetching stream info and available playback options...", "working")

        # Disconnect any existing fetch worker to prevent stale signals
        prev_worker = getattr(self, "_fetch_worker", None)
        if prev_worker is not None:
            try:
                prev_worker.log.disconnect()
                prev_worker.finished.disconnect()
                prev_worker.vods_found.disconnect()
                prev_worker.error.disconnect()
            except (TypeError, RuntimeError):
                pass
            if prev_worker.isRunning():
                prev_worker.requestInterruption()

        self._fetch_worker = FetchWorker(
            url,
            vod_source=vod_source,
            vod_platform=vod_platform,
            vod_title=vod_title,
            vod_channel=vod_channel,
        )
        self._fetch_worker.log.connect(self._log)
        self._fetch_worker.finished.connect(self._on_fetch_done)
        self._fetch_worker.vods_found.connect(self._on_vods_found)
        self._fetch_worker.error.connect(self._on_fetch_error)
        self._fetch_worker.start()

    def _on_fetch_done(self, info):
        if info is None:
            self._on_fetch_error("Extractor returned no stream info")
            return
        self.stream_info = info
        _populate_adv_subtitles(self, info)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self._update_badge(info.platform)

        # Populate qualities
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        qualities = info.qualities or []
        for q in qualities:
            bw_mbps = q.bandwidth / 1_000_000 if q.bandwidth else 0
            ft_tag = f" [{q.format_type.upper()}]" if q.format_type != "hls" else ""
            fps = getattr(q, "frame_rate", 0.0) or 0.0
            fps_tag = f" {fps:.0f}fps" if fps else ""
            vr = str(getattr(q, "video_range", "") or "").upper()
            hdr_tag = " HDR" if vr in ("PQ", "HLG") else ""
            label = (
                f"{q.name} ({q.resolution}{fps_tag}{hdr_tag}, "
                f"{bw_mbps:.1f} Mbps){ft_tag}"
            )
            self.quality_combo.addItem(label, q)
        if qualities:
            selected_idx = self._choose_default_quality_index(
                qualities, info.platform or ""
            )
            self.quality_combo.setCurrentIndex(selected_idx)
        self.quality_combo.setEnabled(len(qualities) > 0)
        self.quality_combo.blockSignals(False)
        _populate_track_table(self)
        if not qualities:
            self._log("[WARN] No playable qualities found for this URL.")

        # Stream info
        parts = [f"Platform: {info.platform}", f"Duration: {info.duration_str}"]
        if info.title:
            parts.insert(1, f"Title: {info.title[:60]}")
        if info.start_time:
            try:
                dt = datetime.fromisoformat(info.start_time.replace("Z", "+00:00"))
                parts.append(f"Started: {dt.strftime('%Y-%m-%d %I:%M %p UTC')}")
            except Exception:
                pass
        if info.segment_count:
            parts.append(f"Segments: {info.segment_count}")
        self.info_label.setText("  |  ".join(parts))
        self.info_label.setVisible(True)

        # Update output folder to use the title for non-channel content (yt-dlp, Direct, etc.)
        if info.title and info.platform in ("yt-dlp", "Direct", "Rumble", "SoundCloud",
                                            "Reddit", "Audius", "Podcast"):
            current_out = self.output_input.text().strip()
            parent = os.path.dirname(current_out)
            if parent and self._can_autofill_output():
                new_out = os.path.join(parent, _safe_filename(info.title))
                self._apply_auto_output(new_out)

        self._build_segments(info.total_secs)
        self.download_btn.setEnabled(True)
        self._refresh_download_summary()

        # Metadata-based duplicate check after resolve (F40 — fuzzy matching)
        dup = self._find_duplicate(
            "", info.title, platform=info.platform,
            duration_secs=info.total_secs,
        )
        if dup:
            self._log(f"[DUPLICATE] Match: already downloaded {dup.date} to {dup.path}")
            self._set_status(
                f"Possible duplicate of \"{dup.title}\" ({dup.date}). Download anyway if intentional.",
                "warning",
            )
            # Advisory dialog — non-blocking for queue/batch, shown for manual fetches
            if not self._queue_autostart:
                details = (
                    f"Title: {dup.title}\n"
                    f"Downloaded: {dup.date}\n"
                    f"Quality: {dup.quality}\n"
                    f"Size: {dup.size}\n"
                    f"Location: {dup.path}"
                )
                if not ask_premium_confirmation(
                    self,
                    title="Possible duplicate found",
                    body="StreamKeep found a recording in your library that closely matches what you are about to download.",
                    eyebrow="DOWNLOAD",
                    badge_text="Potential match",
                    tone="warning",
                    summary_title="Downloading again may waste storage and clutter history.",
                    summary_body="Continue only if you intentionally want another copy or a better variant.",
                    details_title="Existing recording",
                    details_body=details,
                    primary_label="Download anyway",
                    secondary_label="Skip download",
                    default_action="secondary",
                    min_width=640,
                ):
                    self._set_status("Download skipped — duplicate detected.", "idle")
                    return
        elif info.is_live or info.total_secs <= 0:
            self._set_status("Live source ready. Start recording and stop it when you have enough footage.", "success")
        else:
            self._set_status("Source ready. Review the segments and start the download when you are happy.", "success")

        if self._queue_autostart and self._queue_active_item is not None:
            self._set_queue_item_status(self._queue_active_item, "downloading")
            if not self._on_download():
                self._release_queue_item("failed", "Could not start the queued download")
                self._start_next_background_job()

    def _on_fetch_error(self, err):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")
        self._log(f"[ERROR] {err}")
        self._record_failed_job(
            stage="fetch",
            error=err,
            item=self._queue_active_item,
            out_dir=self.output_input.text().strip() if hasattr(self, "output_input") else "",
        )
        self._refresh_download_summary()
        self._set_status(f"Fetch failed: {err}", "error")
        if self._queue_active_item is not None:
            self._release_queue_item("failed", err[:120])
            self._start_next_background_job()


    # ── VOD listing ─────────────────────────────────────────────



    # ── Segment management ──────────────────────────────────────

    def _get_segment_secs(self):
        idx = self.segment_combo.currentIndex()
        return self._segment_options[idx][1]

    @staticmethod
    def _parse_crop_secs(text):
        """Parse a HH:MM:SS, MM:SS, or plain seconds string into total
        seconds. Returns 0 if the text is empty or invalid."""
        text = (text or "").strip()
        if not text:
            return 0
        parts = text.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(float(text))
        except (ValueError, IndexError):
            return 0

    @staticmethod
    def _fmt_crop_time(secs):
        """Format seconds as HH:MM:SS for log output."""
        s = int(secs)
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    def _is_audio_only(self):
        """Detect if the current stream is audio-only based on its qualities."""
        if not self.stream_info:
            return False
        if not self.stream_info.qualities:
            return False
        return all(
            (q.resolution or "").lower() == "audio" or "audio" in (q.name or "").lower()
            for q in self.stream_info.qualities
        )

    def _content_label(self, idx, total_segments, seg_secs, total_secs):
        """Generate a content-aware segment label."""
        is_audio = self._is_audio_only()
        kind = "Audio" if is_audio else "Video"
        if total_secs <= 0:
            return "Live Capture" if not is_audio else "Live Audio"

        if total_segments == 1:
            # Single segment — use the content type
            if total_secs < 60:
                return f"{kind} ({int(total_secs)}s)"
            elif total_secs < 3600:
                return f"{kind} ({int(total_secs // 60)}m)"
            else:
                return f"{kind} ({_fmt_duration(total_secs)})"

        # Multi-segment naming based on segment length
        if seg_secs >= 3600:
            return f"Hour {idx + 1}"
        elif seg_secs >= 60:
            mins = seg_secs // 60
            return f"Part {idx + 1} ({mins}m)"
        else:
            return f"Part {idx + 1}"

    def _build_segments(self, total_secs):
        if total_secs <= 0:
            segments = [(0, 0)]
            seg_secs = 0
        else:
            seg_secs = self._get_segment_secs()

            # Auto-collapse: if content is shorter than segment length, use one segment
            if seg_secs == 0 or total_secs <= seg_secs:
                segments = [(0, total_secs)]
            else:
                segments = []
                pos = 0
                while pos < total_secs:
                    end = min(pos + seg_secs, total_secs)
                    segments.append((pos, end))
                    pos = end

        self.table.setRowCount(len(segments))
        if hasattr(self, "segments_section"):
            self.segments_section.setVisible(bool(segments))
        self._segment_checks = []
        self._segment_progress = []

        for i, (start, end) in enumerate(segments):
            duration = end - start
            cb = QCheckBox()
            cb.setChecked(True)
            cb.stateChanged.connect(lambda _state, self=self: self._refresh_download_summary())
            cb_w = QWidget()
            cb_l = QHBoxLayout(cb_w)
            cb_l.addWidget(cb)
            cb_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_l.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(i, 0, cb_w)
            self._segment_checks.append(cb)

            label = self._content_label(i, len(segments), seg_secs, total_secs)
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 1, item)

            if total_secs <= 0:
                range_text = "Starts now - runs until stopped"
            else:
                s_str = f"{int(start//3600):02d}:{int((start%3600)//60):02d}:{int(start%60):02d}"
                e_str = f"{int(end//3600):02d}:{int((end%3600)//60):02d}:{int(end%60):02d}"
                range_text = f"{s_str} - {e_str}  ({int(duration//60)}m {int(duration%60)}s)"
            t_item = QTableWidgetItem(range_text)
            t_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 2, t_item)

            pbar = QProgressBar()
            if total_secs <= 0:
                pbar.setMaximum(0)
            else:
                pbar.setValue(0)
            self.table.setCellWidget(i, 3, pbar)
            self._segment_progress.append(pbar)

            # Estimated size: bandwidth (bits/sec) × duration / 8 = bytes
            est = self._estimate_size_bytes(duration)
            sz_text = f"~{_fmt_size(est)}" if est > 0 else "\u2014"
            sz = QTableWidgetItem(sz_text)
            sz.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sz.setForeground(QColor(CAT["muted"]))
            self.table.setItem(i, 4, sz)
        self._refresh_download_summary()

    def _estimate_size_bytes(self, duration_secs):
        """Return estimated file size in bytes using the selected quality's bandwidth."""
        if duration_secs <= 0:
            return 0
        q = self.quality_combo.currentData() if hasattr(self, "quality_combo") else None
        if not q or not getattr(q, "bandwidth", 0):
            return 0
        # bandwidth is bits/sec; convert to bytes
        return int(q.bandwidth * duration_secs / 8)

    def _on_select_all(self, state):
        checked = state == Qt.CheckState.Checked.value
        for cb in self._segment_checks:
            cb.setChecked(checked)
        self._refresh_download_summary()

    def _on_segment_length_changed(self, idx):
        if self.stream_info and self.stream_info.total_secs > 0:
            self._build_segments(self.stream_info.total_secs)
        else:
            self._refresh_download_summary()

    def _on_quality_changed(self, idx):
        """Rebuild size estimates in the segment table when quality changes."""
        _populate_track_table(self)
        if not self.stream_info or not hasattr(self, "_segment_progress"):
            return
        total_secs = self.stream_info.total_secs
        if total_secs <= 0:
            return
        seg_secs = self._get_segment_secs()
        if seg_secs == 0 or total_secs <= seg_secs:
            durations = [total_secs]
        else:
            durations = []
            pos = 0
            while pos < total_secs:
                end = min(pos + seg_secs, total_secs)
                durations.append(end - pos)
                pos = end
        for i, d in enumerate(durations):
            if i >= self.table.rowCount():
                break
            est = self._estimate_size_bytes(d)
            sz_text = f"~{_fmt_size(est)}" if est > 0 else "\u2014"
            sz = QTableWidgetItem(sz_text)
            sz.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sz.setForeground(QColor(CAT["muted"]))
            self.table.setItem(i, 4, sz)

    def _on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_input.text())
        if d:
            self.output_input.setText(d)

    def _on_copy_download_command(self):
        command = str(getattr(self, "_export_command_text", "") or "")
        if not command:
            self._set_status(
                "Start a prepared download before copying its command.", "info"
            )
            return
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(command)
        self._set_status(
            "Standalone command copied. It may include cookie paths or headers.",
            "success",
        )


    # ── Download core ───────────────────────────────────────────

    def _on_download(self):
        if not self.stream_info:
            return False
        src_url = self.url_input.text().strip() if hasattr(self, "url_input") else ""
        if src_url and not self.stream_info.is_live:
            prev = _db.find_history_by_url(src_url)
            if prev:
                from PyQt6.QtWidgets import QMessageBox
                ans = QMessageBox.question(
                    self, "Already Downloaded",
                    f"This URL was downloaded on {prev.get('date', '?')}\n"
                    f"to: {prev.get('path', '?')[:80]}\n\n"
                    "Download again?",
                )
                if ans != QMessageBox.StandardButton.Yes:
                    return False
        # Disk-space preflight — catches "no more room on device" before
        # ffmpeg runs for three hours and exits with a muxing error. Only
        # warns when we have a meaningful estimate; lives / unknown-duration
        # streams skip the check.
        if not self._preflight_disk_space():
            return False
        total_secs = self.stream_info.total_secs
        is_live_capture = bool(self.stream_info.is_live or total_secs <= 0)

        q_data = self.quality_combo.currentData()
        selected_tracks = get_selected_media_tracks(self)
        audio_url = ""
        ytdlp_source = ""
        ytdlp_format = ""
        if q_data:
            playlist_url = q_data.url
            fmt_type = q_data.format_type
            audio_url = q_data.audio_url
            ytdlp_source = q_data.ytdlp_source
            ytdlp_format = q_data.ytdlp_format
            if q_data.tracks and not any(
                track.kind in {"video", "audio"} for track in selected_tracks
            ):
                self._log("[ERROR] No playable media tracks selected")
                self._set_status(
                    "Select at least one video or audio track.", "warning"
                )
                return False
        elif self.stream_info.url:
            playlist_url = self.stream_info.url
            fmt_type = "hls"
        else:
            self._log("[ERROR] No quality selected")
            self._set_status("Pick a quality before starting the download.", "warning")
            return False

        # Per-download overrides (F18)
        _dl_overrides = get_adv_overrides(self)
        ytdlp_override_keys = {
            "format_spec", "format_sort_preset", "container",
            "audio_format", "audio_quality", "subtitle_mode",
            "sponsorblock_mode",
        }
        ytdlp_override_keys.update(
            key for key in _dl_overrides if key.startswith("ytdlp_")
        )
        active_ytdlp_overrides = ytdlp_override_keys.intersection(_dl_overrides)
        if active_ytdlp_overrides and fmt_type != "ytdlp_direct":
            self._log(
                "[OUTPUT] Format/container/audio controls require a yt-dlp direct quality."
            )
            self._set_status(
                "These output controls apply only to yt-dlp direct sources; "
                "choose a yt-dlp quality or reset them.",
                "warning",
            )
            return False
        if _dl_overrides.get("audio_format") and _dl_overrides.get("container"):
            self._set_status(
                "Choose either a video container or audio extraction, not both.",
                "warning",
            )
            return False
        try:
            from ...download_options import (
                validate_hls_key_override,
                resolve_ytdlp_arg_template,
                resolve_ytdlp_transfer_options,
                validate_download_options, validate_sponsorblock_options,
                validate_subtitle_options,
            )
            ytdlp_options = validate_download_options(
                format_spec=_dl_overrides.get("format_spec", ""),
                format_sort_preset=_dl_overrides.get("format_sort_preset", ""),
                container=_dl_overrides.get("container", ""),
                audio_format=_dl_overrides.get("audio_format", ""),
                audio_quality=_dl_overrides.get("audio_quality", ""),
            )
            subtitle_mode = _dl_overrides.get("subtitle_mode", "")
            if subtitle_mode == "disabled":
                subtitle_options = validate_subtitle_options(enabled=False)
            elif subtitle_mode == "custom":
                subtitle_options = validate_subtitle_options(
                    enabled=True,
                    languages=_dl_overrides.get("subtitle_languages", ""),
                    automatic=_dl_overrides.get("subtitle_auto", True),
                    convert=_dl_overrides.get("subtitle_convert", ""),
                    embed=_dl_overrides.get("subtitle_embed", True),
                )
            else:
                subtitle_options = validate_subtitle_options(
                    enabled=YtDlpExtractor.download_subs,
                    languages=YtDlpExtractor.subtitle_languages,
                    automatic=YtDlpExtractor.subtitle_auto,
                    convert=YtDlpExtractor.subtitle_convert,
                    embed=YtDlpExtractor.subtitle_embed,
                )
            sponsorblock_mode = _dl_overrides.get("sponsorblock_mode", "")
            if sponsorblock_mode == "disabled":
                sponsorblock_options = validate_sponsorblock_options(
                    enabled=False
                )
            elif sponsorblock_mode == "custom":
                sponsorblock_options = validate_sponsorblock_options(
                    enabled=True,
                    mark=_dl_overrides.get("sponsorblock_mark", ""),
                    remove=_dl_overrides.get("sponsorblock_remove", ""),
                    api_url=_dl_overrides.get("sponsorblock_api", ""),
                )
            else:
                sponsorblock_options = validate_sponsorblock_options(
                    enabled=YtDlpExtractor.sponsorblock,
                    mark=YtDlpExtractor.sponsorblock_mark,
                    remove=YtDlpExtractor.sponsorblock_remove,
                    api_url=YtDlpExtractor.sponsorblock_api,
                )
            transfer_options = resolve_ytdlp_transfer_options(
                YtDlpExtractor, overrides=_dl_overrides,
            )
            ytdlp_template_name = _dl_overrides.get(
                "ytdlp_template_name", ""
            )
            ytdlp_template_args = resolve_ytdlp_arg_template(
                self._config.get("ytdlp_arg_templates", {}),
                ytdlp_template_name,
            )
            hls_key_options = validate_hls_key_override(
                _dl_overrides.get("hls_key_override", ""),
                _dl_overrides.get("hls_key_iv", ""),
            )
        except ValueError as error:
            self._log(f"[OUTPUT] Invalid per-download settings: {error}")
            self._set_status(str(error), "warning")
            return False

        if hls_key_options["value"]:
            if fmt_type not in {"hls", "ytdlp_direct"}:
                self._set_status(
                    "The clear-key override applies only to non-DRM HLS sources.",
                    "warning",
                )
                return False
            selected_urls = {
                track.url for track in selected_tracks if track.url
            }
            from ...models import default_media_tracks
            default_ids = {
                track.id for track in default_media_tracks(q_data)
            } if q_data else set()
            selected_ids = {track.id for track in selected_tracks}
            if (len(selected_urls) > 1
                    or (default_ids and selected_ids != default_ids)):
                self._set_status(
                    "Clear-key recovery supports the default tracks from one HLS "
                    "media playlist; load that playlist directly for custom tracks.",
                    "warning",
                )
                return False

        if (subtitle_mode == "custom" and ytdlp_options["audio_format"]
                and subtitle_options["embed"]):
            self._set_status(
                "Audio extraction cannot embed subtitles; choose Sidecar.",
                "warning",
            )
            return False

        if ytdlp_options["format_spec"]:
            ytdlp_format = ytdlp_options["format_spec"]
        elif ytdlp_options["audio_format"]:
            ytdlp_format = "bestaudio/best"

        # Render filename + folder from templates (templates can produce
        # nested paths like "{channel}/{date} - {title}")
        ctx = _build_template_context(self.stream_info)
        _folder_tpl = _dl_overrides.get("folder_template") or self._folder_template
        _file_tpl = _dl_overrides.get("file_template") or self._file_template
        folder_parts = _render_template(_folder_tpl, ctx)
        file_parts = _render_template(_file_tpl, ctx)
        title_safe = file_parts[-1] if file_parts else (
            _safe_filename(self.stream_info.title)
            or f"{self.stream_info.platform}_download"
        )

        # Time-range crop (F21) — parse optional start/end bounds
        crop_start = self._parse_crop_secs(
            self.crop_start_input.text() if hasattr(self, "crop_start_input") else ""
        )
        crop_end = self._parse_crop_secs(
            self.crop_end_input.text() if hasattr(self, "crop_end_input") else ""
        )
        if crop_end and crop_start and crop_end <= crop_start:
            self._set_status("Time range end must be after start.", "warning")
            return False

        seg_secs = self._get_segment_secs()
        single_segment = (is_live_capture or hls_key_options["value"]
                          or fmt_type in ("mp4", "ytdlp_direct")
                          or seg_secs == 0 or total_secs <= seg_secs)
        segments = []
        for i, cb in enumerate(self._segment_checks):
            if cb.isChecked():
                if single_segment:
                    seg_start = crop_start or 0
                    seg_dur = (crop_end or (0 if is_live_capture else int(total_secs))) - seg_start
                    segments.append((0, title_safe, seg_start, max(0, seg_dur)))
                    break
                else:
                    start = i * seg_secs
                    end = min((i + 1) * seg_secs, total_secs)
                    # Skip segments entirely outside the crop window
                    if crop_end and start >= crop_end:
                        continue
                    if crop_start and end <= crop_start:
                        continue
                    # Clamp segment bounds to the crop window
                    if crop_start and start < crop_start:
                        start = crop_start
                    if crop_end and end > crop_end:
                        end = crop_end
                    label = f"{title_safe}_part{i + 1:02d}"
                    segments.append((i, label, start, int(end - start)))

        if not segments:
            self._log("No segments selected.")
            self._set_status("Select at least one segment before downloading.", "warning")
            return False

        if crop_start or crop_end:
            self._log(f"[CROP] Time range: {self._fmt_crop_time(crop_start)} → {self._fmt_crop_time(crop_end or total_secs)}")

        # For non-channel content, user's output box is the base; folder template
        # adds a subfolder. For channel content the template already has
        # {channel} in it, so joining still works.
        base_out = self.output_input.text().strip()
        if folder_parts:
            out_dir = os.path.join(base_out, *folder_parts)
        else:
            out_dir = base_out
        os.makedirs(out_dir, exist_ok=True)

        self._log(f"\n{'=' * 50}")
        self._log(f"Downloading {len(segments)} segments to {out_dir}")
        self._log(f"Quality: {self.quality_combo.currentText()}")
        self._log(f"{'=' * 50}")

        self._total_segments = len(segments)
        self._completed_segments = 0
        self._download_had_errors = False
        self._init_speed_tracking()
        self.download_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.stop_btn.setVisible(True)
        self.open_folder_btn.setVisible(False)
        self.overall_progress.setVisible(True)
        self.overall_progress.setValue(0)
        self.overall_progress.setMaximum(len(segments))
        if is_live_capture:
            self._set_status(
                f"Live capture started. Recording to {_path_label(out_dir)} until you stop it.",
                "working",
            )
        else:
            self._set_status(
                f"Downloading 0 of {len(segments)} segment(s) to {_path_label(out_dir)}.",
                "working",
            )

        self._set_download_context(
            out_dir=out_dir,
            quality_name=self.quality_combo.currentText(),
            history_url=self._resolve_history_url(),
            info=self.stream_info,
        )
        self.download_worker = DownloadWorker(playlist_url or "", segments, out_dir, format_type=fmt_type)
        self.download_worker.audio_url = audio_url
        self.download_worker.selected_tracks = selected_tracks
        self.download_worker.ytdlp_source = ytdlp_source
        self.download_worker.ytdlp_format = ytdlp_format
        self.download_worker.ytdlp_format_sort = ytdlp_options["format_sort"]
        self.download_worker.ytdlp_container = ytdlp_options["container"]
        self.download_worker.ytdlp_audio_format = ytdlp_options["audio_format"]
        self.download_worker.ytdlp_audio_quality = ytdlp_options["audio_quality"]
        self.download_worker.cookies_browser = YtDlpExtractor.cookies_browser
        self.download_worker.rate_limit = _dl_overrides.get("rate_limit") or YtDlpExtractor.rate_limit
        self.download_worker.proxy = YtDlpExtractor.proxy
        self.download_worker.download_subs = subtitle_options["enabled"]
        self.download_worker.capture_youtube_chat = YtDlpExtractor.capture_youtube_chat
        self.download_worker.subtitle_languages = subtitle_options["languages"]
        self.download_worker.subtitle_auto = subtitle_options["automatic"]
        self.download_worker.subtitle_convert = subtitle_options["convert"]
        self.download_worker.subtitle_embed = subtitle_options["embed"]
        self.download_worker.sponsorblock = sponsorblock_options["enabled"]
        self.download_worker.sponsorblock_mark = sponsorblock_options["mark"]
        self.download_worker.sponsorblock_remove = sponsorblock_options["remove"]
        self.download_worker.sponsorblock_api = sponsorblock_options["api_url"]
        from ...download_options import (
            apply_external_downloader_options, apply_ytdlp_transfer_options,
        )
        apply_ytdlp_transfer_options(
            self.download_worker,
            transfer_options,
        )
        apply_external_downloader_options(self.download_worker, YtDlpExtractor)
        self.download_worker.ytdlp_template_name = ytdlp_template_name
        self.download_worker.ytdlp_template_args = ytdlp_template_args
        self.download_worker.hls_key_override = hls_key_options["value"]
        self.download_worker.hls_key_iv = hls_key_options["iv"]
        self.download_worker.parallel_connections = _dl_overrides.get("parallel_connections") or self._parallel_connections
        # Pass time-range crop to yt-dlp via --download-sections (F21)
        if ((fmt_type == "ytdlp_direct" or hls_key_options["value"])
                and (crop_start or crop_end)):
            cs = self._fmt_crop_time(crop_start) if crop_start else "0:00:00"
            ce = self._fmt_crop_time(crop_end) if crop_end else ""
            self.download_worker.download_sections = f"*{cs}-{ce}" if ce else f"*{cs}-"
        if hls_key_options["value"]:
            self._log(
                "[HLS] Authorized clear-key override enabled for this job; "
                "the value will not be persisted."
            )
        if audio_url:
            self._log("Audio merge: enabled (video-only format detected)")
        if fmt_type == "ytdlp_direct":
            self._log("Download mode: yt-dlp direct (handles URL refresh + format merge)")
            if ytdlp_options["audio_format"]:
                detail = ytdlp_options["audio_format"]
                if ytdlp_options["audio_quality"]:
                    detail += f" @ {ytdlp_options['audio_quality']}"
                self._log(f"[OUTPUT] Audio extraction: {detail}")
            else:
                self._log(
                    f"[OUTPUT] Video container: {ytdlp_options['container']}"
                )
            if ytdlp_options["format_sort"]:
                self._log(f"[OUTPUT] Format sort: {ytdlp_options['format_sort']}")
            if subtitle_options["enabled"]:
                delivery = "embedded" if subtitle_options["embed"] else "sidecar"
                conversion = subtitle_options["convert"] or "source format"
                auto = "+ auto" if subtitle_options["automatic"] else "manual only"
                self._log(
                    f"[SUBS] {subtitle_options['languages']} | {auto} | "
                    f"{conversion} | {delivery}"
                )
            if sponsorblock_options["enabled"]:
                self._log(
                    "[SPONSORBLOCK] Mark: "
                    f"{sponsorblock_options['mark'] or 'none'} | Remove: "
                    f"{sponsorblock_options['remove'] or 'none'}"
                )
            if ytdlp_template_name:
                self._log(
                    f"[ARGS] Named yt-dlp template: {ytdlp_template_name}"
                )
        try:
            self._export_command_text = self.download_worker.export_command()
            self.copy_command_btn.setEnabled(True)
        except (TypeError, ValueError) as error:
            self._export_command_text = ""
            self.copy_command_btn.setEnabled(False)
            self._log(f"[EXPORT] Could not build standalone command: {error}")
        self.download_worker.progress.connect(self._on_dl_progress)
        self.download_worker.segment_done.connect(self._on_segment_done)
        self.download_worker.error.connect(self._on_dl_error)
        self.download_worker.log.connect(self._log)
        self.download_worker.all_done.connect(self._on_all_done)
        self.download_worker.finished.connect(self._on_download_worker_finished)
        self._attach_resume_to_worker(self.download_worker)
        # Store overrides for postprocess snapshot merge (F18)
        self._dl_overrides = _dl_overrides
        if _dl_overrides:
            self._log(f"[OVERRIDE] Per-download overrides active: {', '.join(_dl_overrides.keys())}")
        _reset_adv_overrides(self)
        self.download_worker.start()
        return True

    def _on_download_worker_finished(self):
        if not getattr(self, "_download_had_errors", False):
            return
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(self._output_contains_media(self._active_output_dir))
        if self._queue_active_item is not None:
            note = f"{getattr(self, '_completed_segments', 0)}/{getattr(self, '_total_segments', 0)} segments completed"
            self._release_queue_item("failed", note)
        self._set_status(
            "Download stopped after failed segment(s). Resume sidecar was kept for retry.",
            "warning",
        )
        self._persist_config()
        self._update_tray_badge()
        self._reset_speed_dashboard()
        self._start_next_background_job()

    def _on_dl_progress(self, idx, pct, status):
        if idx < len(self._segment_progress):
            if self.stream_info and (self.stream_info.is_live or self.stream_info.total_secs <= 0):
                self._segment_progress[idx].setMaximum(0)
            else:
                self._segment_progress[idx].setMaximum(100)
                self._segment_progress[idx].setValue(pct)
        if hasattr(self, "_total_segments") and self._total_segments:
            self._set_status(
                f"Downloading {self._completed_segments}/{self._total_segments}. Segment {idx + 1}: {status}",
                "working",
            )
        # Parse speed from the status text (F16 speed dashboard)
        self._update_speed_from_status(status)

    def _on_segment_done(self, idx, size_str):
        if idx < len(self._segment_progress):
            self._segment_progress[idx].setMaximum(100)
            self._segment_progress[idx].setValue(100)
            self._segment_progress[idx].setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {CAT['green']}; border-radius: 6px; }}"
            )
        size_item = QTableWidgetItem(size_str)
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(idx, 4, size_item)
        self._completed_segments += 1
        self.overall_progress.setValue(self._completed_segments)
        self._set_status(
            f"Downloaded {self._completed_segments} of {self._total_segments} segment(s).",
            "working",
        )

    def _on_dl_error(self, idx, err):
        self._download_had_errors = True
        self._record_failed_job(
            stage="download",
            error=err,
            item=self._queue_active_item,
            info=self._active_stream_info or self.stream_info,
            out_dir=self._active_output_dir,
        )
        if idx < len(self._segment_progress):
            self._segment_progress[idx].setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {CAT['red']}; border-radius: 6px; }}"
            )
        fail_item = QTableWidgetItem("FAILED")
        fail_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(idx, 4, fail_item)
        self._set_status(f"Segment {idx + 1} failed: {err}", "error")

    def _on_all_done(self):
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(True)
        if hasattr(self, "trim_btn"):
            self.trim_btn.setVisible(True)
        active_info_n = self._active_stream_info or self.stream_info
        title_n = (active_info_n.title if active_info_n and active_info_n.title else "Download")[:80]
        self._notify_center(
            f"Download complete: {title_n}",
            "success" if not self._download_had_errors else "warning",
        )
        active_info = self._active_stream_info or self.stream_info
        out_dir = self._active_output_dir or self.output_input.text().strip()
        q_name = self._active_quality_name or (
            self.quality_combo.currentText() if self.quality_combo.count() else ""
        )
        title = active_info.title if active_info and active_info.title else "Download"

        self._log(f"\n{'=' * 50}")
        if self._download_had_errors:
            self._log("Download finished with one or more failed segments.")
            self._log(f"{'=' * 50}")
            self._record_failed_job(
                stage="download",
                error=f"{self._completed_segments}/{self._total_segments} segments completed",
                item=self._queue_active_item,
                info=active_info,
                out_dir=out_dir,
            )
            self._set_status(
                "Download finished with one or more failed segments. Review the log before retrying.",
                "warning",
            )
            if self._queue_active_item is not None:
                note = f"{self._completed_segments}/{self._total_segments} segments completed"
                self._release_queue_item("failed", note)
        else:
            self._log("All downloads complete!")
            self._log(f"{'=' * 50}")
            if active_info and (active_info.is_live or active_info.total_secs <= 0):
                self._set_status("Live capture finished and was saved to the selected folder.", "success")
                self._notify("StreamKeep — Capture finished", title[:80])
                self._send_webhook("capture finished", title,
                                   f"Segments: {self._completed_segments}")
            else:
                self._set_status(
                    f"Download complete. Saved {self._completed_segments} segment(s) to the selected folder.",
                    "success",
                )
                self._notify("StreamKeep — Download complete", title[:80])
                self._send_webhook("download complete", title,
                                   f"Segments: {self._completed_segments}")
                self._fire_hook(
                    "download_complete", title=title,
                    path=out_dir,
                    platform=active_info.platform if active_info else "")
                _db.mark_failed_jobs_resolved_for_url(self._active_history_url)
            self._save_metadata(
                out_dir,
                q_name,
                history_url=self._active_history_url,
                info=active_info,
            )
            self._media_server_import(out_dir, active_info)
            if self._queue_active_item is not None:
                self._release_queue_item("done")
        self._persist_config()
        self._run_lifecycle_cleanup()
        self._update_tray_badge()
        self._reset_speed_dashboard()
        self._start_next_background_job()

    def _on_stop(self):
        worker = self.download_worker
        resume_background_jobs = bool(
            self._queue_active_item is not None
            or self._autorecord_workers
            or self._autorecord_resolvers
            or self._pending_auto_records
        )
        live_capture = bool(
            worker and any(len(seg) >= 4 and seg[3] <= 0 for seg in getattr(worker, "segments", []))
        )
        # Halt any in-progress batch by marking it done
        if hasattr(self, '_batch_vods') and hasattr(self, '_batch_total'):
            self._batch_active = False
            self._batch_idx = self._batch_total
            self._cancel_batch_fetch_worker()
        if self.download_worker is not None:
            try:
                self.download_worker.cancel()
                if not self.download_worker.wait(5000):
                    self.download_worker.terminate()
                    self.download_worker.wait(1000)
            except Exception:
                pass
            self.download_worker = None
        # Also stop any parallel auto-records. The stop button is a global
        # "halt everything the user is actively watching" — parallel lives
        # included. (Use the Monitor tab's per-row Stop+Remove for selective
        # stops.)
        for ch_id in list(self._autorecord_workers.keys()):
            w = self._autorecord_workers.get(ch_id)
            if w is not None and w.isRunning():
                try:
                    w.cancel()
                    if not w.wait(3000):
                        w.terminate()
                        w.wait(500)
                except Exception:
                    pass
        self._autorecord_workers.clear()
        self._autorecord_contexts.clear()
        for ch_id in list(self._autorecord_resolvers.keys()):
            w = self._autorecord_resolvers.get(ch_id)
            if w is not None and w.isRunning():
                try:
                    w.requestInterruption()
                    w.wait(1500)
                except Exception:
                    pass
        self._autorecord_resolvers.clear()
        # Stop any paired live-chat captures.
        for ch_id in list(self._chat_workers.keys()):
            w = self._chat_workers.get(ch_id)
            if w is not None and w.isRunning():
                try:
                    w.cancel()
                    w.wait(2000)
                except Exception:
                    pass
        self._chat_workers.clear()
        # Clear any green/red chunk overrides left on segment bars so the
        # next download starts from a neutral style instead of inheriting
        # the previous run's success/fail colors.
        for pbar in getattr(self, "_segment_progress", []):
            try:
                pbar.setStyleSheet("")
                pbar.setValue(0)
            except Exception:
                pass
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.overall_progress.setVisible(False)
        if hasattr(self, 'vod_dl_all_btn'):
            self.vod_dl_all_btn.setEnabled(True)
            self.vod_load_btn.setEnabled(True)
        for entry in self.monitor.entries:
            entry.is_recording = False
        self._active_auto_record_channel = ""
        self._refresh_monitor_summary()
        self._log("[CANCELLED] Download stopped by user.")
        if self._queue_active_item is not None:
            self._release_queue_item("cancelled", "Stopped by user")
        if live_capture:
            has_media = self._output_contains_media(self._active_output_dir)
            self.open_folder_btn.setVisible(has_media)
            if has_media and self._active_output_dir and self._active_stream_info:
                self._save_metadata(
                    self._active_output_dir,
                    self._active_quality_name,
                    history_url=self._active_history_url,
                    info=self._active_stream_info,
                )
                self._set_status("Recording stopped. Any captured portion was kept on disk.", "warning")
            else:
                self._set_status("Recording stopped before any media was saved.", "warning")
        else:
            self._set_status("Download cancelled. You can adjust the selection and try again.", "warning")
        if resume_background_jobs:
            self._start_next_background_job()

    def _on_open_folder(self):
        out_dir = self._active_output_dir or self.output_input.text().strip()
        if os.path.isdir(out_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(out_dir))


    # ── Speed / ETA tracking ────────────────────────────────────

    def _init_speed_tracking(self):
        """Reset speed tracking state at the start of a download."""
        self._speed_samples = deque(maxlen=60)  # (timestamp, speed_bytes_per_sec)
        self._dl_start_time = time.monotonic()
        if hasattr(self, "download_speed_value"):
            self.download_speed_value.setText("—")
            self.download_speed_sub.setText("Waiting for data")
        if hasattr(self, "download_eta_value"):
            self.download_eta_value.setText("—")
            self.download_eta_sub.setText("Estimating...")

    def _update_speed_from_status(self, status):
        """Parse speed info from the progress status string and update the
        speed/ETA dashboard cards."""
        if not hasattr(self, "download_speed_value"):
            return
        # Try to extract a speed like "12.4MB/s" or "3.2MiB/s" from the status
        m = re.search(r'([\d.]+)\s*(B|KB|KiB|MB|MiB|GB|GiB)/s', status, re.IGNORECASE)
        if not m:
            return
        val = float(m.group(1))
        unit = m.group(2).upper().replace("I", "")
        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
        bps = val * multipliers.get(unit, 1)
        now = time.monotonic()
        self._speed_samples.append((now, bps))
        # Compute 5-second smoothed average
        cutoff = now - 5.0
        recent = [(t, s) for t, s in self._speed_samples if t >= cutoff]
        if recent:
            avg_speed = sum(s for _, s in recent) / len(recent)
        else:
            avg_speed = bps
        # Display speed
        self.download_speed_value.setText(_fmt_size(int(avg_speed)) + "/s")
        self.download_speed_sub.setText(f"5-sec avg ({len(recent)} samples)")
        # Calculate ETA from remaining segments
        total = getattr(self, "_total_segments", 0)
        done = getattr(self, "_completed_segments", 0)
        if total > 0 and done < total and avg_speed > 0:
            # Estimate bytes remaining from elapsed speed and segment ratio
            elapsed = now - getattr(self, "_dl_start_time", now)
            if elapsed > 0 and done > 0:
                est_total_time = elapsed * total / done
                remaining = est_total_time - elapsed
                if remaining > 0:
                    self.download_eta_value.setText(_fmt_duration(remaining))
                    self.download_eta_sub.setText(
                        f"{done}/{total} segments done"
                    )
                    return
            self.download_eta_value.setText("Estimating...")
        elif total > 0 and done >= total:
            self.download_eta_value.setText("Done")
            self.download_eta_sub.setText("Finalizing...")

    def _reset_speed_dashboard(self):
        """Clear speed/ETA cards after download completes."""
        if hasattr(self, "download_speed_value"):
            self.download_speed_value.setText("—")
            self.download_speed_sub.setText("Starts during download")
        if hasattr(self, "download_eta_value"):
            self.download_eta_value.setText("—")
            self.download_eta_sub.setText("Estimated time remaining")


    # ── Playlist / page scrape ──────────────────────────────────

    def _on_expand_playlist(self):
        """Probe the URL for playlist/channel entries and queue them all."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a URL first.", "warning")
            return
        overrides = get_adv_overrides(self)
        archive_path = ""
        if overrides.get("playlist_archive_sync"):
            from ...paths import source_archive_path
            archive_path = source_archive_path(url)
        try:
            from ...download_options import validate_playlist_options
            options = validate_playlist_options(
                items=overrides.get("playlist_items", ""),
                date_after=overrides.get("playlist_date_after", ""),
                date_before=overrides.get("playlist_date_before", ""),
                match_filter=overrides.get("playlist_match_filter", ""),
                max_downloads=overrides.get("playlist_max_downloads", 0),
                archive_path=archive_path,
                break_on_existing=bool(archive_path),
            )
        except ValueError as error:
            self._set_status(str(error), "warning")
            return
        self.expand_btn.setEnabled(False)
        self._set_status("Probing for playlist/channel entries...", "working")
        self._log(f"[PLAYLIST] Probing: {url}")
        # Run in a throwaway thread to avoid blocking the UI
        worker = _PlaylistExpandWorker(
            url,
            playlist_items=options["items"],
            date_after=options["date_after"],
            date_before=options["date_before"],
            match_filter=options["match_filter"],
            max_downloads=options["max_downloads"],
            archive_path=options["archive_path"],
            break_on_existing=options["break_on_existing"],
        )
        worker.finished.connect(
            lambda entries, u=url, o=options: self._on_expand_done(u, entries, o)
        )
        worker.error.connect(self._on_expand_error)
        worker.log.connect(self._log)
        self._expand_worker = worker
        worker.start()

    def _on_expand_done(self, source_url, entries, options=None):
        self.expand_btn.setEnabled(True)
        if not entries:
            if options and options.get("archive_path"):
                self._log("[PLAYLIST] Incremental archive is already current")
                self._set_status(
                    "Archive sync is current; no new playlist entries were queued.",
                    "success",
                )
                return
            self._set_status(
                "No playlist entries found. This URL may be a single video — use Fetch instead.",
                "warning",
            )
            return
        added = 0
        options = options or {}
        for e in entries:
            if self._queue_add(
                e.get("url", ""), title=e.get("title", ""),
                platform="yt-dlp",
                download_archive=options.get("archive_path", ""),
                break_on_existing=options.get("break_on_existing", False),
            ):
                added += 1
        self._log(f"[PLAYLIST] Queued {added} new of {len(entries)} total entries")
        self._set_status(
            f"Playlist expanded. Queued {added} new entries "
            f"({len(entries) - added} already in the queue).",
            "success",
        )
        # Kick off the queue if nothing's downloading
        worker = getattr(self, "download_worker", None)
        if worker is None or not worker.isRunning():
            self._advance_queue()

    def _on_expand_error(self, err):
        self.expand_btn.setEnabled(True)
        self._log(f"[PLAYLIST] {err}")
        self._set_status(f"Playlist probe failed: {err}", "error")

    def _on_scan_page(self):
        """Scrape a webpage for video/media links and queue them."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a webpage URL first.", "warning")
            return
        if not url.startswith("http"):
            self._set_status("Scan Page expects a full http(s) URL.", "warning")
            return
        self.scan_btn.setEnabled(False)
        allow_lan = self.scan_lan_check.isChecked()
        self.scan_lan_check.setChecked(False)
        self._set_status("Scanning page for media links...", "working")
        self._log(
            f"[SCRAPE] Scanning {url} "
            f"(LAN override {'enabled for this scan' if allow_lan else 'off'})"
        )
        worker = _PageScrapeWorker(
            url,
            allow_private_network=allow_lan,
        )
        worker.finished.connect(self._on_scan_done)
        worker.error.connect(self._on_scan_error)
        worker.log.connect(self._log)
        self._scan_worker = worker
        worker.start()

    def _on_scan_done(self, links):
        self.scan_btn.setEnabled(True)
        if not links:
            self._set_status(
                "No media links found. Try Fetch or Expand Playlist instead.",
                "warning",
            )
            return
        added = 0
        for url, hint in links:
            if self._queue_add(url, title=url[:80], platform=hint):
                added += 1
        self._log(f"[SCRAPE] Queued {added} new link(s) of {len(links)} found")
        self._set_status(
            f"Found {len(links)} link(s). Queued {added} new ({len(links) - added} already in queue).",
            "success",
        )
        worker = getattr(self, "download_worker", None)
        if worker is None or not worker.isRunning():
            self._advance_queue()

    def _on_scan_error(self, err):
        self.scan_btn.setEnabled(True)
        self._log(f"[SCRAPE] {err}")
        self._set_status(f"Scan failed: {err}", "error")


    # ── Recover VOD ─────────────────────────────────────────────

    def _on_recover_vod(self):
        """Open the Deleted VOD Recovery Wizard dialog (F23)."""
        from ..recover_dialog import RecoverDialog
        dlg = RecoverDialog(self, log_fn=self._log)
        dlg.download_requested.connect(self._on_recover_download)
        dlg.exec()

    def _on_recover_download(self, url):
        """Handle a recovered VOD URL — paste into input and trigger fetch."""
        self.url_input.setText(url)
        self._on_fetch()


    # ── Batch URL import ────────────────────────────────────────



    # ── Finalize pipeline ───────────────────────────────────────


    def _on_trim_last(self):
        """Open the trim dialog for the most-recently-finished download."""
        out_dir = self._active_output_dir or self.output_input.text().strip()
        if not out_dir or not os.path.isdir(out_dir):
            self._set_status("No recent download folder to trim.", "warning")
            return
        self._open_clip_dialog_for_dir(out_dir)

    # Monitor actions → streamkeep.ui.tabs.monitor.MonitorTabMixin


    # ── Browser companion ───────────────────────────────────────

    def _on_companion_url(self, url, action):
        """The extension just POSTed a URL. Route it through the Fetch
        path or queue it immediately depending on action."""
        self._log(f"[COMPANION] Received {action.upper()} for {url[:80]}")
        self._present_main_window(0)
        try:
            self.url_input.setText(url)
        except Exception:
            pass
        if action == "queue":
            try:
                added = self._queue_add(url, title="", platform="")
                if added:
                    self._set_status(f"Queued via browser extension: {url[:80]}", "success")
                else:
                    self._set_status("That browser handoff is already in the queue.", "warning")
            except Exception as e:
                self._log(f"[COMPANION] Queue failed: {e}")
        else:
            self._on_fetch()

    def _on_companion_clip(self, url, start_secs, end_secs):
        """The extension sent validated clip bounds alongside a URL. Prefill the
        crop range and present the main window so the fetch that immediately
        follows (via ``url_received``) opens a ready-to-clip workflow once.

        The local server emits ``clip_received`` before ``url_received``, so the
        crop fields are populated before the fetch reads them at download time.
        """
        try:
            start = max(0.0, float(start_secs or 0.0))
            end = max(0.0, float(end_secs or 0.0))
        except (TypeError, ValueError):
            return
        self._present_main_window(0)
        try:
            if url:
                self.url_input.setText(url)
        except Exception:
            pass
        try:
            self.crop_start_input.setText(
                self._fmt_crop_time(start) if start > 0 else ""
            )
            self.crop_end_input.setText(
                self._fmt_crop_time(end) if end > 0 else ""
            )
        except Exception:
            pass
        span = ""
        if end > start:
            span = f" ({self._fmt_crop_time(start)}-{self._fmt_crop_time(end)})"
        self._log(f"[COMPANION] Clip range received{span}")
        self._set_status(
            "Browser clip range prefilled; fetching the source.", "info"
        )
