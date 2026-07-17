"""Advanced-option and media-track controls shared by Download surfaces."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QTableWidgetItem, QWidget


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
