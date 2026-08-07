"""V166: an archive must not quietly store an AI-synthesised rendition.

Platforms publish AI super-resolution video alongside the real renditions and
mark it in ``format_note``. It is taller than the original, so a plain
"best video" pick takes it. There is no ``--format-sort`` field for it, so the
preference has to be expressed as a format-selection filter.

The end-to-end tests below drive yt-dlp's own format selector rather than
asserting on our generated string, because the failure that matters is a
selection outcome, not a spelling.
"""

import pytest

from streamkeep.download_options import (
    SYNTHESISED_VIDEO_FILTER,
    SYNTHESISED_VIDEO_MARKER,
    apply_synthesised_track_policy,
    validate_download_options,
)


def _formats():
    """Worst-to-best, as yt-dlp's selector expects. The upscaled rendition is
    last among the video formats, so an unfiltered pick lands on it."""
    return [
        {"format_id": "248", "url": "u", "ext": "webm", "vcodec": "vp9",
         "acodec": "none", "height": 1080, "format_note": "1080p"},
        {"format_id": "137sr", "url": "u", "ext": "mp4", "vcodec": "avc1",
         "acodec": "none", "height": 2160,
         "format_note": SYNTHESISED_VIDEO_MARKER},
        {"format_id": "140", "url": "u", "ext": "m4a", "vcodec": "none",
         "acodec": "mp4a", "format_note": "medium"},
    ]


def _select(spec, formats):
    from yt_dlp import YoutubeDL

    ydl = YoutubeDL({"quiet": True, "simulate": True})
    selector = ydl.build_format_selector(spec)
    return [
        f["format_id"]
        for f in selector({"formats": formats, "incomplete_formats": False})
    ]


def test_an_unfiltered_selection_really_does_take_the_upscaled_rendition():
    """The premise of the whole item — without this the rest proves nothing."""
    assert _select("bv*+ba/b", _formats()) == ["137sr+140"]


def test_the_policy_selects_the_real_rendition_instead():
    assert _select(apply_synthesised_track_policy("bv*+ba/b"), _formats()) == [
        "248+140"
    ]


def test_opting_in_restores_the_synthesised_rendition():
    spec = apply_synthesised_track_policy("bv*+ba/b", allow=True)
    assert _select(spec, _formats()) == ["137sr+140"]


def test_a_site_whose_formats_carry_no_note_is_unaffected():
    """The regression this guards is severe and silent.

    yt-dlp drops any format whose filtered field is absent unless the
    comparison is marked optional with ``?``. ``format_note`` is absent on most
    non-YouTube extractors, so the unmarked spelling of this filter selects
    nothing at all on those sites — every download fails with "requested format
    is not available" and nothing points at this policy.
    """
    plain = [{"format_id": "0", "url": "u", "ext": "mp4", "vcodec": "avc1",
              "acodec": "mp4a", "height": 720}]
    assert _select(apply_synthesised_track_policy("bv*+ba/b"), plain) == ["0"]
    assert "!*=?" in SYNTHESISED_VIDEO_FILTER


@pytest.mark.parametrize("spec, expected_filtered", [
    ("bv*+ba/b", ["bv*", "b"]),
    ("bestvideo+bestaudio/best", ["bestvideo", "best"]),
    ("bv*[height<=1080]+ba/b", ["bv*[height<=1080]", "b"]),
])
def test_the_filter_lands_on_video_branches_only(spec, expected_filtered):
    result = apply_synthesised_track_policy(spec)
    for branch in expected_filtered:
        assert branch + SYNTHESISED_VIDEO_FILTER in result
    # The audio branches keep their own shape.
    assert "ba" + SYNTHESISED_VIDEO_FILTER not in result
    assert "bestaudio" + SYNTHESISED_VIDEO_FILTER not in result


def test_an_explicit_format_id_is_left_alone():
    """Naming a rendition by id is an explicit choice; do not second-guess it."""
    assert apply_synthesised_track_policy("137+251") == "137+251"


def test_an_existing_language_filter_survives():
    result = apply_synthesised_track_policy("bv*+ba[language^=en]/b")
    assert "ba[language^=en]" in result
    assert result.startswith("bv*" + SYNTHESISED_VIDEO_FILTER)


def test_the_policy_is_idempotent():
    once = apply_synthesised_track_policy("bv*+ba/b")
    assert apply_synthesised_track_policy(once) == once


def test_validation_defaults_to_excluding_synthesised_tracks():
    assert validate_download_options()["allow_synthesised_tracks"] is False
    assert validate_download_options(
        allow_synthesised_tracks=True
    )["allow_synthesised_tracks"] is True
