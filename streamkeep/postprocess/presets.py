"""Named conversion presets — HandBrake-style profiles for common targets.

Each preset maps to a dict of PostProcessor converter fields.
Selecting a preset overwrites the converter settings; the user can
still tweak individual fields afterward.
"""

PRESETS = {
    "Archive": {
        "label": "Archive (lossless copy)",
        "convert_video": True,
        "convert_video_format": "mkv",
        "convert_video_codec": "copy",
        "convert_video_scale": "original",
        "convert_video_fps": "original",
        "convert_audio": False,
        "convert_delete_source": False,
    },
    "Discord": {
        "label": "Discord (10 MB / 50 MB Nitro)",
        "convert_video": True,
        "convert_video_format": "mp4",
        "convert_video_codec": "h264",
        "convert_video_scale": "480p",
        "convert_video_fps": "30",
        "convert_audio": False,
        "convert_delete_source": False,
    },
    "YouTube": {
        "label": "YouTube upload",
        "convert_video": True,
        "convert_video_format": "mp4",
        "convert_video_codec": "h264",
        "convert_video_scale": "original",
        "convert_video_fps": "original",
        "convert_audio": False,
        "convert_delete_source": False,
    },
    "Audio-only": {
        "label": "Audio extract (MP3 192k)",
        "convert_video": False,
        "convert_audio": True,
        "convert_audio_format": "mp3",
        "convert_audio_codec": "mp3",
        "convert_audio_bitrate": "192k",
        "convert_audio_samplerate": "original",
        "convert_delete_source": False,
    },
    "Device-safe": {
        "label": "Device-safe (720p H.264 AAC)",
        "convert_video": True,
        "convert_video_format": "mp4",
        "convert_video_codec": "h264",
        "convert_video_scale": "720p",
        "convert_video_fps": "30",
        "convert_audio": False,
        "convert_delete_source": False,
    },
}

PRESET_NAMES = list(PRESETS.keys())

_CONVERTER_FIELDS = (
    "convert_video", "convert_video_format", "convert_video_codec",
    "convert_video_scale", "convert_video_fps",
    "convert_audio", "convert_audio_format", "convert_audio_codec",
    "convert_audio_bitrate", "convert_audio_samplerate",
    "convert_delete_source",
)


def apply_preset(name, config):
    """Apply a named preset to a config dict. Returns True if applied."""
    preset = PRESETS.get(name)
    if not preset:
        return False
    for field in _CONVERTER_FIELDS:
        if field in preset:
            config[f"pp_{field}"] = preset[field]
    config["pp_conversion_preset"] = name
    return True


def preset_for_config(config):
    """Return the preset name that matches the current config, or ''."""
    for name, preset in PRESETS.items():
        match = True
        for field in _CONVERTER_FIELDS:
            if field not in preset:
                continue
            if config.get(f"pp_{field}") != preset[field]:
                match = False
                break
        if match:
            return name
    return ""
