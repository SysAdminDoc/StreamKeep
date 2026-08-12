"""Post-processing preset state and Settings-tab preset actions."""

from ...postprocess import PostProcessor
from ..widgets import (
    ask_premium_text_input,
    show_premium_message,
)


BUILTIN_PRESETS = {
    "Archive Quality": {
        "extract_audio": False, "normalize_loudness": True,
        "reencode_h265": True, "contact_sheet": True,
        "split_by_chapter": False, "remove_silence": False,
        "convert_video": False, "convert_audio": False,
    },
    "Quick Share": {
        "extract_audio": False, "normalize_loudness": False,
        "reencode_h265": False, "contact_sheet": False,
        "split_by_chapter": False, "remove_silence": False,
        "convert_video": True, "convert_video_format": "mp4",
        "convert_video_codec": "h264", "convert_video_scale": "720p",
        "convert_video_fps": "30", "convert_audio": False,
    },
    "Raw — No Processing": {
        "extract_audio": False, "normalize_loudness": False,
        "reencode_h265": False, "contact_sheet": False,
        "split_by_chapter": False, "remove_silence": False,
        "convert_video": False, "convert_audio": False,
    },
}


def _pp_snapshot():
    """Capture the current PostProcessor state as a dict."""
    return {
        "extract_audio": PostProcessor.extract_audio,
        "normalize_loudness": PostProcessor.normalize_loudness,
        "reencode_h265": PostProcessor.reencode_h265,
        "contact_sheet": PostProcessor.contact_sheet,
        "split_by_chapter": PostProcessor.split_by_chapter,
        "remove_silence": PostProcessor.remove_silence,
        "silence_noise_db": PostProcessor.silence_noise_db,
        "silence_min_duration": PostProcessor.silence_min_duration,
        "convert_video": PostProcessor.convert_video,
        "convert_video_format": PostProcessor.convert_video_format,
        "convert_video_codec": PostProcessor.convert_video_codec,
        "convert_video_scale": PostProcessor.convert_video_scale,
        "convert_video_fps": PostProcessor.convert_video_fps,
        "convert_audio": PostProcessor.convert_audio,
        "convert_audio_format": PostProcessor.convert_audio_format,
        "convert_audio_codec": PostProcessor.convert_audio_codec,
        "convert_audio_bitrate": PostProcessor.convert_audio_bitrate,
        "convert_audio_samplerate": PostProcessor.convert_audio_samplerate,
        "convert_delete_source": PostProcessor.convert_delete_source,
    }


def _pp_apply_snapshot(snap, win=None):
    """Apply a preset dict and optionally refresh Settings-tab widgets."""
    for key, val in snap.items():
        if hasattr(PostProcessor, key):
            setattr(PostProcessor, key, val)
    if win is None:
        return

    def _setc(w, v):
        w.blockSignals(True)
        w.setChecked(bool(v))
        w.blockSignals(False)

    if hasattr(win, "pp_audio_check"):
        _setc(win.pp_audio_check, PostProcessor.extract_audio)
    if hasattr(win, "pp_loud_check"):
        _setc(win.pp_loud_check, PostProcessor.normalize_loudness)
    if hasattr(win, "pp_h265_check"):
        _setc(win.pp_h265_check, PostProcessor.reencode_h265)
    if hasattr(win, "pp_contact_check"):
        _setc(win.pp_contact_check, PostProcessor.contact_sheet)
    if hasattr(win, "pp_split_check"):
        _setc(win.pp_split_check, PostProcessor.split_by_chapter)
    if hasattr(win, "pp_silence_check"):
        _setc(win.pp_silence_check, PostProcessor.remove_silence)
    if hasattr(win, "pp_silence_db_spin"):
        win.pp_silence_db_spin.setValue(int(PostProcessor.silence_noise_db or -30))
    if hasattr(win, "pp_silence_dur_spin"):
        win.pp_silence_dur_spin.setValue(int(PostProcessor.silence_min_duration or 3))
    if hasattr(win, "pp_convert_video_check"):
        _setc(win.pp_convert_video_check, PostProcessor.convert_video)
    if hasattr(win, "pp_convert_audio_check"):
        _setc(win.pp_convert_audio_check, PostProcessor.convert_audio)


def _get_user_presets(win):
    """Return the user-defined presets dict from config."""
    cfg = getattr(win, "_config", {})
    return dict(cfg.get("pp_presets", {}))


def _save_user_presets(win, presets):
    cfg = getattr(win, "_config", {})
    cfg["pp_presets"] = dict(presets)


def _populate_pp_presets(win):
    """Refresh the preset combo box."""
    combo = win.pp_preset_combo
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("(custom)", userData="")
    for name in BUILTIN_PRESETS:
        combo.addItem(f"★ {name}", userData=name)
    for name in _get_user_presets(win):
        combo.addItem(name, userData=name)
    combo.setCurrentIndex(0)
    combo.blockSignals(False)


def _on_pp_preset_selected(win):
    """User picked a preset from the combo — apply it."""
    name = win.pp_preset_combo.currentData()
    if not name:
        return
    snap = BUILTIN_PRESETS.get(name) or _get_user_presets(win).get(name)
    if snap:
        _pp_apply_snapshot(snap, win)


def _on_pp_preset_save(win):
    """Save current PP state as a named preset."""
    name, ok = ask_premium_text_input(
        win,
        title="Save post-processing preset",
        body=(
            "Capture the current conversion, cleanup, and archive settings so "
            "you can reuse them later without rebuilding the whole stack."
        ),
        eyebrow="POST-PROCESSING",
        badge_text="Preset",
        tone="info",
        summary_title="Built-in presets stay read-only",
        summary_body="Saved presets capture the current post-processing toggles exactly as shown below.",
        field_label="Preset name",
        field_hint="Use a short label that will still make sense when it appears in the preset picker.",
        placeholder="Weekend archive",
        primary_label="Save preset",
        secondary_label="Cancel",
        validator=lambda value: (bool((value or "").strip()), "Enter a preset name."),
    )
    if not ok:
        return
    if name in BUILTIN_PRESETS:
        show_premium_message(
            win,
            title="Built-in presets are locked",
            body="Pick a different name if you want to save your current adjustments as a reusable custom preset.",
            eyebrow="POST-PROCESSING",
            badge_text="Preset",
            tone="warning",
            summary_title="Archive Quality, Quick Share, and Raw — No Processing stay unchanged.",
            primary_label="Close",
        )
        return
    presets = _get_user_presets(win)
    replaced = presets.get(name)
    if replaced is not None:
        win._preset_change_for_undo = (name, dict(replaced))
    presets[name] = _pp_snapshot()
    _save_user_presets(win, presets)
    _populate_pp_presets(win)
    idx = win.pp_preset_combo.findData(name)
    if idx >= 0:
        win.pp_preset_combo.setCurrentIndex(idx)
    action = "Replaced" if replaced is not None else "Saved"
    win._set_status(f'{action} preset "{name}".', "success")
    if replaced is not None:
        win._notify_center(
            f'Replaced preset "{name}". Use Undo preset change in Notifications '
            "to restore the previous settings.",
            "success",
        )


def _on_pp_preset_delete(win):
    """Delete the currently selected user preset."""
    name = win.pp_preset_combo.currentData()
    if not name or name in BUILTIN_PRESETS:
        return
    presets = _get_user_presets(win)
    snapshot = presets.pop(name, None)
    if snapshot is None:
        return
    win._preset_change_for_undo = (name, dict(snapshot))
    _save_user_presets(win, presets)
    _populate_pp_presets(win)
    win._set_status(f'Deleted preset "{name}".', "success")
    win._notify_center(
        f'Deleted preset "{name}". Use Undo preset change in Notifications '
        "to restore it.",
        "success",
    )


__all__ = [
    "BUILTIN_PRESETS",
    "_get_user_presets",
    "_on_pp_preset_delete",
    "_on_pp_preset_save",
    "_on_pp_preset_selected",
    "_populate_pp_presets",
    "_pp_apply_snapshot",
    "_pp_snapshot",
    "_save_user_presets",
]
