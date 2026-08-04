"""Coverage for the V16 URL-pattern Smart Mode resolver."""

from types import SimpleNamespace

import pytest

from streamkeep import config, smart_mode


def _profile(name, patterns, **overrides):
    return {
        "name": name,
        "patterns": patterns,
        "enabled": True,
        "overrides": overrides,
    }


def test_first_matching_profile_wins_and_www_is_normalized():
    config = {
        "smart_mode": True,
        "smart_profiles": [
            _profile("generic", ["youtube.com/*"], quality="720p"),
            _profile("specific", ["https://www.youtube.com/watch?v=*"] , quality="1080p"),
        ],
    }

    selected = smart_mode.resolve_profile(
        "https://www.youtube.com/watch?v=abc", config
    )

    assert selected["name"] == "generic"
    assert selected["overrides"]["quality"] == "720p"


def test_profile_glob_and_regex_fail_closed():
    profile = _profile("v", ["https://example.com/video/*"])
    assert smart_mode.profile_matches(profile, "https://example.com/video/7")
    assert not smart_mode.profile_matches(profile, "https://evil-example.com/video/7")
    assert smart_mode.profile_matches(
        _profile("regex", [r"re:^https://example\.org/items/\d+$"]),
        "https://example.org/items/12",
    )
    assert not smart_mode.profile_matches(
        _profile("bad", ["re:("]), "https://example.org/items/12"
    )


@pytest.mark.parametrize(
    ("expression", "url"),
    [
        (r"^https://cdn\.Example\.com/\S+\Z", "https://cdn.example.com/video"),
        (r"^https://cdn\.Example\.com/\D+\Z", "https://cdn.example.com/video"),
        (r"^https://cdn\.Example\.com/\W+\Z", "https://cdn.example.com/!!!"),
        (r"^https://cdn\.Example\.com/ab\Bcd\Z", "https://cdn.example.com/abcd"),
        (r"^https://cdn\.Example\.com/video\Z", "https://cdn.example.com/video"),
    ],
)
def test_regex_escapes_round_trip_and_match_case_insensitively(expression, url):
    pattern = "re:" + expression

    assert smart_mode.normalize_pattern(pattern) == pattern
    assert smart_mode.profile_matches(_profile("regex", [pattern]), url)


def test_regex_patterns_use_all_url_candidates_like_globs():
    profile = _profile("regex", [r"re:^example\.org/items/\d+\Z"])

    assert smart_mode.profile_matches(profile, "https://example.org/items/12")


def test_apply_preserves_explicit_values_and_records_safe_name():
    config = {
        "smart_mode": True,
        "smart_profiles": [_profile(
            "Archive",
            ["twitch.tv/*"],
            output_dir="D:/Archive",
            quality="1080p",
            ytdlp_template_name="Twitch",
            auth_profile_id="opaque-id",
        )],
    }

    result = smart_mode.apply_smart_profile_to_job({
        "url": "https://twitch.tv/channel",
        "quality": "720p",
        "auth_profile_id": "caller-id",
    }, config)

    assert result["quality"] == "720p"
    assert result["auth_profile_id"] == "caller-id"
    assert result["output_dir"] == "D:/Archive"
    assert result["arg_template"] == "Twitch"
    assert result["_smart_profile"] == "Archive"


def test_disabled_mode_and_disabled_profile_do_nothing():
    profile = _profile("off", ["example.com/*"], quality="720p")
    profile["enabled"] = False
    config = {"smart_mode": True, "smart_profiles": [profile]}
    assert smart_mode.resolve_profile("https://example.com/a", config) is None
    assert smart_mode.apply_smart_profile_to_job(
        {"url": "https://example.com/a"}, {**config, "smart_mode": False}
    ) == {"url": "https://example.com/a"}


def test_quality_index_supports_named_numeric_and_audio_choices():
    qualities = [
        SimpleNamespace(name="1080p", resolution="1080p"),
        SimpleNamespace(name="720p", resolution="720p"),
        SimpleNamespace(name="Audio", resolution="audio"),
    ]
    assert smart_mode.quality_index(qualities, "720p") == 1
    assert smart_mode.quality_index(qualities, "900p") == 1
    assert smart_mode.quality_index(qualities, "audio") == 2
    assert smart_mode.quality_index(qualities, "best") == 0


def test_validation_rejects_unknown_fields_and_invalid_patterns():
    with pytest.raises(ValueError, match="unsupported fields"):
        smart_mode.validate_profiles([{
            "name": "x",
            "patterns": ["example.com/*"],
            "exec": "never",
        }])
    with pytest.raises(ValueError, match="is invalid"):
        smart_mode.validate_profiles([{
            "name": "x",
            "patterns": ["re:("],
        }])


def test_config_import_quarantines_profiles_until_approved():
    imported = {
        "smart_mode": True,
        "smart_profiles": [_profile("x", ["example.com/*"], proxy="http://proxy")],
    }
    config._validate_config_schema(imported)
    quarantined, held = config._quarantine_import_capabilities(imported)

    assert quarantined["smart_mode"] is False
    assert quarantined["smart_profiles"] == []
    assert "smart_profiles" in held
