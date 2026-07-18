from unittest import mock

from streamkeep.extractors import ytdlp


def test_player_client_value_maps_presets():
    assert ytdlp.youtube_player_client_value("") == ""
    assert ytdlp.youtube_player_client_value("default") == ""
    assert ytdlp.youtube_player_client_value("web_safari") == "web_safari"
    assert ytdlp.youtube_player_client_value("resilient") == "web_safari,android_vr,tv"
    assert ytdlp.youtube_player_client_value("bogus") == ""


def test_player_client_args_only_for_youtube_and_real_preset():
    yt = "https://www.youtube.com/watch?v=abc"
    other = "https://example.com/video.mp4"
    assert ytdlp.youtube_player_client_args("web_safari", yt) == [
        "--extractor-args", "youtube:player_client=web_safari",
    ]
    # Non-YouTube URL -> no args even with a preset set.
    assert ytdlp.youtube_player_client_args("web_safari", other) == []
    # Empty/default preset -> no args.
    assert ytdlp.youtube_player_client_args("", yt) == []
    assert ytdlp.youtube_player_client_args("default", yt) == []
    # No URL supplied -> trust the preset (caller already scoped it).
    assert ytdlp.youtube_player_client_args("tv") == [
        "--extractor-args", "youtube:player_client=tv",
    ]


def test_resolve_command_includes_player_client_for_youtube():
    ext = ytdlp.YtDlpExtractor()
    try:
        ext.youtube_player_client = "android_vr"
        with mock.patch("streamkeep.extractors.ytdlp.ytdlp_command", return_value=["yt-dlp"]):
            cmd = ext._build_cmd("https://www.youtube.com/watch?v=abc")
        joined = " ".join(cmd)
        assert "youtube:player_client=android_vr" in joined
        # Non-YouTube: preset must not leak in.
        with mock.patch("streamkeep.extractors.ytdlp.ytdlp_command", return_value=["yt-dlp"]):
            cmd2 = ext._build_cmd("https://example.com/v.mp4")
        assert "player_client" not in " ".join(cmd2)
    finally:
        ext.youtube_player_client = ""


def test_pot_provider_detected_when_module_importable():
    with mock.patch("streamkeep.extractors.ytdlp.importlib.util.find_spec", return_value=object()):
        status = ytdlp.youtube_pot_provider_status()
    assert status["available"] is True
    assert status["provider"] in ytdlp._POT_PROVIDER_MODULES


def test_pot_provider_absent_gives_actionable_detail():
    with mock.patch("streamkeep.extractors.ytdlp.importlib.util.find_spec", return_value=None):
        status = ytdlp.youtube_pot_provider_status()
    assert status["available"] is False
    assert "bgutil" in status["detail"].lower()


def test_health_report_aggregates_runtime_pot_and_client():
    fake_runtime = {
        "state": "ready", "summary": "Ready", "yt_dlp_version": "2026.06.09",
        "js_runtime": {"name": "deno"}, "ejs_available": True, "problems": [],
    }
    with mock.patch("streamkeep.extractors.ytdlp.ytdlp_runtime_status", return_value=fake_runtime), \
         mock.patch("streamkeep.extractors.ytdlp.importlib.util.find_spec", return_value=None):
        report = ytdlp.youtube_health_report(player_client="web_safari")
    assert report["state"] == "ready"
    assert report["yt_dlp_version"] == "2026.06.09"
    assert report["player_client"] == "web_safari"
    # A ready runtime is still not "healthy" advisory-free when POT is missing?
    # healthy tracks runtime readiness; POT absence is a warning, not a hard fail.
    assert report["healthy"] is True
    assert any("PO-token" in w for w in report["warnings"])


def test_health_report_surfaces_runtime_problems():
    fake_runtime = {
        "state": "limited", "summary": "Limited", "yt_dlp_version": "2026.01.01",
        "js_runtime": {}, "ejs_available": False,
        "problems": ["JavaScript runtime not found"],
    }
    with mock.patch("streamkeep.extractors.ytdlp.ytdlp_runtime_status", return_value=fake_runtime), \
         mock.patch("streamkeep.extractors.ytdlp.importlib.util.find_spec", return_value=object()):
        report = ytdlp.youtube_health_report()
    assert report["healthy"] is False
    assert any("JavaScript runtime" in w for w in report["warnings"])
    assert report["pot_provider"]["available"] is True
