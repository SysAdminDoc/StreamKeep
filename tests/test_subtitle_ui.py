from types import SimpleNamespace

from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QLabel, QLineEdit, QListWidget,
    QSpinBox, QTableWidget,
)

from streamkeep.models import StreamInfo, SubtitleInfo
from streamkeep.ui.tabs.download import (
    _populate_adv_subtitles, _refresh_adv_subtitle_controls,
    get_adv_overrides,
)


def _window_with_advanced_controls():
    win = SimpleNamespace()
    win.adv_pp_combo = QComboBox()
    win.adv_pp_combo.addItem("global", userData="")
    win.adv_rate_input = QLineEdit()
    win.adv_parallel_spin = QSpinBox()
    win.adv_folder_tpl_input = QLineEdit()
    win.adv_file_tpl_input = QLineEdit()
    win.adv_format_input = QLineEdit()
    win.adv_format_sort_combo = QComboBox()
    win.adv_format_sort_combo.addItem("default", userData="")
    win.adv_container_combo = QComboBox()
    win.adv_container_combo.addItem("mp4", userData="")
    win.adv_audio_combo = QComboBox()
    win.adv_audio_combo.addItem("video", userData="")
    win.adv_audio_quality_input = QLineEdit()
    win.adv_subtitle_mode_combo = QComboBox()
    win.adv_subtitle_mode_combo.addItem("global", userData="")
    win.adv_subtitle_mode_combo.addItem("disabled", userData="disabled")
    win.adv_subtitle_mode_combo.addItem("custom", userData="custom")
    win.adv_subtitle_list = QListWidget()
    win.adv_subtitle_list.setSelectionMode(
        QAbstractItemView.SelectionMode.MultiSelection
    )
    win.adv_subtitle_auto_check = QCheckBox()
    win.adv_subtitle_auto_check.setChecked(True)
    win.adv_subtitle_convert_combo = QComboBox()
    win.adv_subtitle_convert_combo.addItem("keep", userData="")
    win.adv_subtitle_convert_combo.addItem("srt", userData="srt")
    win.adv_subtitle_delivery_combo = QComboBox()
    win.adv_subtitle_delivery_combo.addItem("embed", userData="embed")
    win.adv_subtitle_delivery_combo.addItem("sidecar", userData="sidecar")
    win.adv_sponsorblock_mode_combo = QComboBox()
    win.adv_sponsorblock_mode_combo.addItem("global", userData="")
    win.adv_sponsorblock_mode_combo.addItem("disabled", userData="disabled")
    win.adv_sponsorblock_mode_combo.addItem("custom", userData="custom")
    win.adv_sponsorblock_table = QTableWidget()
    win.adv_sponsorblock_action_combos = {}
    for category in ("sponsor", "intro", "chapter"):
        combo = QComboBox()
        combo.addItem("ignore", userData="")
        combo.addItem("mark", userData="mark")
        if category != "chapter":
            combo.addItem("remove", userData="remove")
        win.adv_sponsorblock_action_combos[category] = combo
    win.adv_sponsorblock_api_input = QLineEdit()
    win.adv_playlist_items_input = QLineEdit()
    win.adv_playlist_after_input = QLineEdit()
    win.adv_playlist_before_input = QLineEdit()
    win.adv_playlist_filter_input = QLineEdit()
    win.adv_playlist_max_spin = QSpinBox()
    win.adv_playlist_max_spin.setRange(0, 10000)
    win.adv_playlist_archive_check = QCheckBox()
    win.adv_ytdlp_fragments_spin = QSpinBox()
    win.adv_ytdlp_fragments_spin.setRange(0, 32)
    win.adv_ytdlp_retries_input = QLineEdit()
    win.adv_ytdlp_fragment_retries_input = QLineEdit()
    win.adv_ytdlp_retry_sleep_input = QLineEdit()
    win.adv_ytdlp_unavailable_combo = QComboBox()
    win.adv_ytdlp_unavailable_combo.addItem("global", userData="")
    win.adv_ytdlp_unavailable_combo.addItem("abort", userData="abort")
    win.adv_ytdlp_throttled_input = QLineEdit()
    win.adv_ytdlp_wait_input = QLineEdit()
    win.adv_ytdlp_live_combo = QComboBox()
    win.adv_ytdlp_live_combo.addItem("global", userData=None)
    win.adv_ytdlp_live_combo.addItem("on", userData=True)
    for name in ("chapters", "metadata", "thumbnail"):
        combo = QComboBox()
        combo.addItem("global", userData=None)
        combo.addItem("on", userData=True)
        combo.addItem("off", userData=False)
        setattr(win, f"adv_ytdlp_embed_{name}_combo", combo)
    win.adv_ytdlp_template_combo = QComboBox()
    win.adv_ytdlp_template_combo.addItem("none", userData="")
    win.adv_ytdlp_template_combo.addItem(
        "Authenticated archive", userData="Authenticated archive"
    )
    win.adv_override_badge = QLabel()
    return win


def test_resolved_subtitles_feed_multiselect_and_override_payload():
    win = _window_with_advanced_controls()
    info = StreamInfo(subtitles=[
        SubtitleInfo("en", "English", manual=True, automatic=True, formats=["vtt"]),
        SubtitleInfo("es", "Spanish", manual=True, formats=["vtt"]),
        SubtitleInfo("fr", "French", automatic=True, formats=["json3"]),
    ])

    _populate_adv_subtitles(win, info)
    win.adv_subtitle_mode_combo.setCurrentIndex(2)
    _refresh_adv_subtitle_controls(win)
    win.adv_subtitle_list.item(0).setSelected(True)
    win.adv_subtitle_list.item(1).setSelected(True)
    win.adv_subtitle_convert_combo.setCurrentIndex(1)
    win.adv_subtitle_delivery_combo.setCurrentIndex(1)

    overrides = get_adv_overrides(win)

    assert overrides["subtitle_mode"] == "custom"
    assert overrides["subtitle_languages"] == "en,es"
    assert overrides["subtitle_auto"] is True
    assert overrides["subtitle_convert"] == "srt"
    assert overrides["subtitle_embed"] is False

    win.adv_subtitle_auto_check.setChecked(False)
    _refresh_adv_subtitle_controls(win)
    assert win.adv_subtitle_list.item(2).isHidden()


def test_no_reported_subtitles_disables_custom_mode():
    win = _window_with_advanced_controls()
    _populate_adv_subtitles(win, StreamInfo())
    assert win.adv_subtitle_mode_combo.model().item(2).isEnabled() is False


def test_sponsorblock_matrix_builds_custom_override_payload():
    win = _window_with_advanced_controls()
    win.adv_sponsorblock_mode_combo.setCurrentIndex(2)
    win.adv_sponsorblock_action_combos["sponsor"].setCurrentIndex(2)
    win.adv_sponsorblock_action_combos["intro"].setCurrentIndex(1)
    win.adv_sponsorblock_action_combos["chapter"].setCurrentIndex(1)
    win.adv_sponsorblock_api_input.setText("https://sponsor.example/api")

    overrides = get_adv_overrides(win)

    assert overrides["sponsorblock_mode"] == "custom"
    assert overrides["sponsorblock_mark"] == "intro,chapter"
    assert overrides["sponsorblock_remove"] == "sponsor"
    assert overrides["sponsorblock_api"] == "https://sponsor.example/api"


def test_playlist_expansion_controls_build_override_payload():
    win = _window_with_advanced_controls()
    win.adv_playlist_items_input.setText("2:5")
    win.adv_playlist_after_input.setText("20260101")
    win.adv_playlist_before_input.setText("20261231")
    win.adv_playlist_filter_input.setText("duration > 60")
    win.adv_playlist_max_spin.setValue(3)
    win.adv_playlist_archive_check.setChecked(True)

    overrides = get_adv_overrides(win)

    assert overrides["playlist_items"] == "2:5"
    assert overrides["playlist_date_after"] == "20260101"
    assert overrides["playlist_date_before"] == "20261231"
    assert overrides["playlist_match_filter"] == "duration > 60"
    assert overrides["playlist_max_downloads"] == 3
    assert overrides["playlist_archive_sync"] is True


def test_ytdlp_transfer_controls_build_override_payload():
    win = _window_with_advanced_controls()
    win.adv_ytdlp_fragments_spin.setValue(4)
    win.adv_ytdlp_retries_input.setText("8")
    win.adv_ytdlp_fragment_retries_input.setText("infinite")
    win.adv_ytdlp_retry_sleep_input.setText("fragment:exp=1:20")
    win.adv_ytdlp_unavailable_combo.setCurrentIndex(1)
    win.adv_ytdlp_throttled_input.setText("250K")
    win.adv_ytdlp_wait_input.setText("30-120")
    win.adv_ytdlp_live_combo.setCurrentIndex(1)
    win.adv_ytdlp_embed_chapters_combo.setCurrentIndex(1)
    win.adv_ytdlp_embed_metadata_combo.setCurrentIndex(2)
    win.adv_ytdlp_embed_thumbnail_combo.setCurrentIndex(1)

    overrides = get_adv_overrides(win)

    assert overrides["ytdlp_concurrent_fragments"] == 4
    assert overrides["ytdlp_fragment_retries"] == "infinite"
    assert overrides["ytdlp_unavailable_fragments"] == "abort"
    assert overrides["ytdlp_live_from_start"] is True
    assert overrides["ytdlp_embed_chapters"] is True
    assert overrides["ytdlp_embed_metadata"] is False


def test_named_ytdlp_template_builds_override_payload():
    win = _window_with_advanced_controls()
    win.adv_ytdlp_template_combo.setCurrentIndex(1)
    assert get_adv_overrides(win)["ytdlp_template_name"] == (
        "Authenticated archive"
    )
