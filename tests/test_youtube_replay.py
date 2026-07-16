import json
import tempfile
from pathlib import Path

from streamkeep.chat.youtube_replay import (
    export_chat_csv,
    filter_messages,
    parse_youtube_live_chat,
)


def _text_action(nick, runs, ts_usec, offset_ms, badges=None):
    renderer = {
        "authorName": {"simpleText": nick},
        "message": {"runs": runs},
        "timestampUsec": str(ts_usec),
    }
    if badges:
        renderer["authorBadges"] = badges
    return {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": str(offset_ms),
            "actions": [
                {"addChatItemAction": {"item": {
                    "liveChatTextMessageRenderer": renderer
                }}}
            ],
        }
    }


def _owner_badge():
    return [{"liveChatAuthorBadgeRenderer": {"icon": {"iconType": "OWNER"}}}]


def _member_badge():
    return [{"liveChatAuthorBadgeRenderer": {
        "tooltip": "Member (3 months)", "customThumbnail": {"x": 1},
    }}]


def _fixture():
    lines = [
        _text_action("Alice", [{"text": "hello "}, {"text": "world"}],
                     1_600_000_000_000_000, 12000),
        _text_action("Streamer", [{"text": "thanks!"}],
                     1_600_000_001_000_000, 13000, badges=_owner_badge()),
        _text_action("Fan", [
            {"text": "nice "},
            {"emoji": {"shortcuts": [":heart:"]}},
        ], 1_600_000_002_000_000, 14000, badges=_member_badge()),
        # A non-message replay event must be ignored.
        {"replayChatItemAction": {"actions": [
            {"markChatItemAsDeletedAction": {"targetItemId": "x"}}
        ]}},
        "{ truncated json line",  # partial line — skipped, non-fatal
    ]
    return "\n".join(
        json.dumps(x) if isinstance(x, dict) else x for x in lines
    )


def test_parses_and_normalizes_youtube_replay():
    messages = parse_youtube_live_chat(_fixture())
    assert len(messages) == 3
    assert messages[0]["nick"] == "Alice"
    assert messages[0]["message"] == "hello world"
    assert messages[0]["ts"] == 1_600_000_000.0
    assert messages[0]["offset"] == 12.0


def test_owner_and_member_flags_and_emoji():
    messages = parse_youtube_live_chat(_fixture())
    streamer = next(m for m in messages if m["nick"] == "Streamer")
    assert streamer["owner"] is True
    assert "owner" in streamer["badges"]
    fan = next(m for m in messages if m["nick"] == "Fan")
    assert fan["member"] is True
    assert fan["sub"] is True
    # Emoji run rendered via its shortcut.
    assert fan["message"] == "nice :heart:"


def test_filter_by_user_and_regex():
    messages = parse_youtube_live_chat(_fixture())
    assert [m["nick"] for m in filter_messages(messages, user="alice")] == ["Alice"]
    hits = filter_messages(messages, pattern=r"thank")
    assert [m["nick"] for m in hits] == ["Streamer"]
    # An invalid regex degrades to no message filtering.
    assert len(filter_messages(messages, pattern="(")) == 3


def test_superchat_amount_prefixes_message():
    envelope = {
        "replayChatItemAction": {"videoOffsetTimeMsec": "5000", "actions": [
            {"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {
                "authorName": {"simpleText": "Whale"},
                "message": {"runs": [{"text": "great stream"}]},
                "purchaseAmountText": {"simpleText": "$5.00"},
                "timestampUsec": "1600000009000000",
            }}}}
        ]}
    }
    messages = parse_youtube_live_chat(json.dumps(envelope))
    assert messages[0]["message"] == "[$5.00] great stream"


def test_csv_export_round_trips():
    messages = parse_youtube_live_chat(_fixture())
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "chat.csv"
        export_chat_csv(messages, str(path))
        rows = path.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "ts,offset,nick,badges,message"
    assert "Alice" in rows[1]
    assert len(rows) == 4  # header + 3 messages


def test_spike_detector_consumes_normalized_jsonl():
    from streamkeep.chat.youtube_replay import write_chat_jsonl
    from streamkeep.chat import spike_detect
    messages = parse_youtube_live_chat(_fixture())
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "chat.jsonl"
        write_chat_jsonl(messages, str(path))
        # Existing spike tool must read the normalized file without error.
        spikes = spike_detect.detect_spikes(str(path))
    assert isinstance(spikes, list)


def test_ingest_replay_dir_writes_jsonl_and_csv():
    from streamkeep.chat.youtube_replay import ingest_replay_dir
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        (out / "video.live_chat.json").write_text(_fixture(), encoding="utf-8")
        count = ingest_replay_dir(str(out))
        assert count == 3
        assert (out / "chat.jsonl").is_file()
        assert (out / "chat.csv").is_file()
        lines = (out / "chat.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        # Absent replay file is a no-op.
        with tempfile.TemporaryDirectory() as empty:
            assert ingest_replay_dir(empty) == 0
