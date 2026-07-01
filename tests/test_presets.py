import unittest

from streamkeep.postprocess.presets import (
    PRESETS,
    PRESET_NAMES,
    apply_preset,
    preset_for_config,
)


class PresetTests(unittest.TestCase):
    def test_all_presets_have_required_keys(self):
        for name, preset in PRESETS.items():
            self.assertIn("label", preset, f"{name} missing label")
            has_video = "convert_video" in preset
            has_audio = "convert_audio" in preset
            self.assertTrue(has_video or has_audio, f"{name} has no converter action")

    def test_apply_preset_sets_config_fields(self):
        cfg = {}
        ok = apply_preset("Discord", cfg)
        self.assertTrue(ok)
        self.assertEqual(cfg["pp_convert_video_format"], "mp4")
        self.assertEqual(cfg["pp_convert_video_codec"], "h264")
        self.assertEqual(cfg["pp_convert_video_scale"], "480p")
        self.assertEqual(cfg["pp_conversion_preset"], "Discord")

    def test_apply_unknown_preset_returns_false(self):
        cfg = {}
        ok = apply_preset("Nonexistent", cfg)
        self.assertFalse(ok)
        self.assertEqual(cfg, {})

    def test_preset_for_config_detects_matching_preset(self):
        cfg = {}
        apply_preset("Archive", cfg)
        detected = preset_for_config(cfg)
        self.assertEqual(detected, "Archive")

    def test_preset_for_config_returns_empty_on_mismatch(self):
        cfg = {"pp_convert_video_format": "avi", "pp_convert_video_codec": "mpeg4"}
        detected = preset_for_config(cfg)
        self.assertEqual(detected, "")

    def test_audio_only_preset_disables_video(self):
        cfg = {}
        apply_preset("Audio-only", cfg)
        self.assertFalse(cfg["pp_convert_video"])
        self.assertTrue(cfg["pp_convert_audio"])
        self.assertEqual(cfg["pp_convert_audio_format"], "mp3")

    def test_roundtrip_all_presets(self):
        for name in PRESET_NAMES:
            cfg = {}
            apply_preset(name, cfg)
            detected = preset_for_config(cfg)
            self.assertEqual(detected, name, f"Roundtrip failed for {name}")


if __name__ == "__main__":
    unittest.main()
