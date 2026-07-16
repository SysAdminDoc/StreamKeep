import tempfile
from pathlib import Path

from streamkeep import subtitles
from streamkeep.subtitles import (
    SubtitleCue,
    export_lrc,
    merge_bilingual_cues,
    parse_srt,
    parse_vtt,
    render_bilingual_ass,
    render_srt,
)


def test_parse_srt_and_vtt_produce_cues():
    srt = parse_srt(
        "1\n00:00:01,000 --> 00:00:02,500\nHello <i>world</i>\n"
    )
    assert srt == [SubtitleCue(1.0, 2.5, "Hello world")]
    # WebVTT minute-only timestamps are accepted.
    vtt = parse_vtt("WEBVTT\n\n00:01.000 --> 00:02.000\n<v A>Hi there\n")
    assert vtt == [SubtitleCue(1.0, 2.0, "Hi there")]


def test_merge_bilingual_anchors_on_primary_by_overlap():
    primary = [SubtitleCue(0.0, 2.0, "Hello"), SubtitleCue(2.0, 4.0, "World")]
    secondary = [
        SubtitleCue(0.1, 1.9, "Hola"),
        SubtitleCue(2.1, 3.9, "Mundo"),
        SubtitleCue(10.0, 11.0, "no overlap"),
    ]
    merged = merge_bilingual_cues(primary, secondary)
    assert [c.text for c in merged] == ["Hello\nHola", "World\nMundo"]
    # Cue order and count follow the primary track.
    assert len(merged) == 2


def test_render_srt_round_trips():
    cues = [SubtitleCue(1.0, 2.5, "One"), SubtitleCue(3.0, 4.0, "Two")]
    text = render_srt(cues)
    assert "00:00:01,000 --> 00:00:02,500" in text
    assert parse_srt(text) == cues


def test_bilingual_ass_has_two_styles_and_preserves_order():
    primary = [SubtitleCue(0.0, 1.0, "A"), SubtitleCue(1.0, 2.0, "B")]
    secondary = [SubtitleCue(0.0, 1.0, "X")]
    ass = render_bilingual_ass(primary, secondary)
    assert "Style: Primary" in ass and "Style: Secondary" in ass
    prim = [ln for ln in ass.splitlines() if ln.startswith("Dialogue") and "Primary" in ln]
    assert len(prim) == 2
    assert any("Secondary,,0,0,0,,X" in ln for ln in ass.splitlines())


def test_export_lrc_is_monotonic_and_formatted():
    cues = [
        SubtitleCue(65.30, 67.0, "second line"),
        SubtitleCue(3.5, 5.0, "first line"),
    ]
    lrc = export_lrc(cues, metadata={"ti": "Song", "ar": "Artist"})
    lines = lrc.strip().splitlines()
    assert lines[0] == "[ti:Song]"
    assert lines[1] == "[ar:Artist]"
    # Sorted by time -> monotonic; centisecond formatting.
    assert lines[2] == "[00:03.50]first line"
    assert lines[3] == "[01:05.30]second line"


def test_postprocessor_writes_bilingual_and_lrc_preserving_originals():
    from streamkeep.postprocess.processor import PostProcessor

    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        (out / "video.en.srt").write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nHello\n", encoding="utf-8",
        )
        (out / "video.es.srt").write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nHola\n", encoding="utf-8",
        )
        saved = {
            k: getattr(PostProcessor, k) for k in (
                "bilingual_subs", "bilingual_primary_lang",
                "bilingual_secondary_lang", "bilingual_format",
                "lrc_export", "lrc_lang",
            )
        }
        logs = []
        try:
            PostProcessor.bilingual_subs = True
            PostProcessor.bilingual_primary_lang = "en"
            PostProcessor.bilingual_secondary_lang = "es"
            PostProcessor.bilingual_format = "srt"
            PostProcessor.lrc_export = True
            PostProcessor.lrc_lang = "en"
            PostProcessor._run_subtitle_processing(str(out), logs.append)
        finally:
            for key, value in saved.items():
                setattr(PostProcessor, key, value)

        bilingual = out / "subtitles.en-es.bilingual.srt"
        lrc = out / "lyrics.en.lrc"
        assert bilingual.is_file()
        assert "Hello\nHola" in bilingual.read_text(encoding="utf-8")
        assert lrc.is_file()
        assert "[00:00.00]Hello" in lrc.read_text(encoding="utf-8")
        # Originals are preserved.
        assert (out / "video.en.srt").is_file()
        assert (out / "video.es.srt").is_file()


def test_find_subtitles_language_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        (out / "clip.en.srt").write_text("x", encoding="utf-8")
        (out / "clip.vtt").write_text("x", encoding="utf-8")
        found = {lang: fmt for _p, lang, fmt in subtitles.find_subtitles(str(out))}
        assert found.get("en") == "srt"
