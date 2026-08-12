"""Dedicated coverage for the Twitch IRC and Kick WebSocket chat readers.

The network transports are replaced with in-memory fakes so the parsing,
PING/PONG keepalive, and cancel-loop behaviour can be asserted without a live
connection.
"""

import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from streamkeep.chat import kick_ws
from streamkeep.chat.chat_worker import ChatWorker
from streamkeep.chat.kick_ws import KickChatReader
from streamkeep.chat.limits import (
    ChatPayloadTooLarge,
    IRC_BUFFER_LIMIT,
    KICK_PAYLOAD_LIMIT,
)
from streamkeep.chat.twitch_irc import TwitchIRCReader, _parse_tags
from streamkeep.chat.spike_detect import detect_spikes
from streamkeep.postprocess.chat_render_worker import _load_chat_jsonl


class _FakeSocket:
    """Feeds pre-canned byte chunks through recv(); records sendall bytes."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.sent = []

    def recv(self, _n):
        if not self._chunks:
            return b""  # EOF -> iterator stops
        item = self._chunks.pop(0)
        if item is TimeoutError:
            raise socket.timeout()
        return item

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        pass


class TwitchIRCReaderTests(unittest.TestCase):
    def test_parse_tags_splits_pairs(self):
        tags = _parse_tags("color=#FF0000;display-name=Alice;mod=1;bad")
        self.assertEqual(tags["color"], "#FF0000")
        self.assertEqual(tags["display-name"], "Alice")
        self.assertEqual(tags["mod"], "1")
        self.assertNotIn("bad", tags)

    def test_privmsg_is_parsed_into_message_dict(self):
        line = (
            "@color=#00FF00;display-name=Bob;mod=1;subscriber=1 "
            ":bob!bob@bob.tmi.twitch.tv PRIVMSG #chan :hello world\r\n"
        )
        reader = TwitchIRCReader("chan")
        reader._sock = _FakeSocket([line.encode()])
        messages = list(reader.iter_messages())
        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertEqual(msg["nick"], "Bob")
        self.assertEqual(msg["message"], "hello world")
        self.assertEqual(msg["color"], "#00FF00")
        self.assertTrue(msg["mod"])
        self.assertTrue(msg["sub"])

    def test_ping_triggers_pong_and_continues(self):
        chunks = [
            b"PING :tmi.twitch.tv\r\n",
            b":x!x@x PRIVMSG #chan :hi\r\n",
        ]
        reader = TwitchIRCReader("chan")
        sock = _FakeSocket(chunks)
        reader._sock = sock
        messages = list(reader.iter_messages())
        self.assertEqual(len(messages), 1)
        self.assertIn(b"PONG :tmi.twitch.tv\r\n", sock.sent)

    def test_cancel_hook_stops_iteration(self):
        reader = TwitchIRCReader("chan", should_cancel=lambda: True)
        reader._sock = _FakeSocket([b":x!x@x PRIVMSG #chan :hi\r\n"])
        self.assertEqual(list(reader.iter_messages()), [])

    def test_channel_is_normalized(self):
        self.assertEqual(TwitchIRCReader("#MixedCase").channel, "mixedcase")

    def test_partial_lines_buffer_until_complete(self):
        reader = TwitchIRCReader("chan")
        reader._sock = _FakeSocket([
            b":x!x@x PRIVMSG #chan :split ",
            b"message\r\n",
        ])
        messages = list(reader.iter_messages())
        self.assertEqual(messages[0]["message"], "split message")

    def test_unterminated_input_is_bounded_and_reported(self):
        reader = TwitchIRCReader("chan")
        sock = _FakeSocket([b"x" * (1024 * 1024)])
        reader._sock = sock

        with self.assertRaisesRegex(
            ChatPayloadTooLarge,
            f"exceeded {IRC_BUFFER_LIMIT} bytes",
        ):
            list(reader.iter_messages())

        self.assertIsNone(reader._sock)

    def test_event_commands_round_trip_as_typed_rows(self):
        lines = [
            (
                "@display-name=Alice;msg-id=sub;subscriber=1 "
                ":tmi.twitch.tv USERNOTICE #chan :Alice subscribed\r\n"
            ),
            (
                "@display-name=Alice;msg-id=resub;subscriber=1 "
                ":tmi.twitch.tv USERNOTICE #chan :Alice resubscribed\r\n"
            ),
            (
                "@display-name=Alice;msg-id=subgift;target-user-name=Bob "
                ":tmi.twitch.tv USERNOTICE #chan :gifted\r\n"
            ),
            (
                "@msg-id=raid;msg-param-viewerCount=42 "
                ":tmi.twitch.tv USERNOTICE #chan :raid incoming\r\n"
            ),
            (
                "@msg-id=announcement;system-msg=Stream\\supdate "
                ":tmi.twitch.tv USERNOTICE #chan :announcement\r\n"
            ),
            (
                "@ban-duration=600;target-user-id=42;target-user-name=Bob "
                ":tmi.twitch.tv CLEARCHAT #chan :Bob\r\n"
            ),
            (
                "@login=Bob;target-msg-id=deleted "
                ":tmi.twitch.tv CLEARMSG #chan :removed text\r\n"
            ),
            (
                "@msg-id=vendor-future;foo=bar "
                ":tmi.twitch.tv USERNOTICE #chan :future\r\n"
            ),
        ]
        reader = TwitchIRCReader("chan")
        reader._sock = _FakeSocket(["".join(lines).encode()])
        rows = list(reader.iter_messages())
        self.assertEqual(
            [row["event_kind"] for row in rows],
            [
                "subscription", "resubscription", "gift_subscription", "raid",
                "announcement", "timeout", "message_delete",
                "usernotice:vendor-future",
            ],
        )
        self.assertEqual(rows[5]["duration"], 600)
        self.assertEqual(rows[5]["target"], "Bob")
        self.assertEqual(rows[-1]["event_data"]["foo"], "bar")
        self.assertIn("USERNOTICE", rows[-1]["raw_event"])

    def test_privmsg_shape_remains_unchanged_alongside_events(self):
        line = (
            "@display-name=Bob :bob!bob@host PRIVMSG #chan :hello\r\n"
            ":tmi.twitch.tv USERNOTICE #chan :notice\r\n"
        )
        reader = TwitchIRCReader("chan")
        reader._sock = _FakeSocket([line.encode()])
        rows = list(reader.iter_messages())
        self.assertEqual(
            set(rows[0]),
            {"ts", "nick", "message", "color", "badges", "mod", "sub"},
        )
        self.assertEqual(rows[1]["event_kind"], "usernotice")


class _FakeWS:
    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []
        self.connected = True

    def settimeout(self, _t):
        pass

    def recv(self):
        if not self._frames:
            self.connected = False
            raise OSError("closed")
        return self._frames.pop(0)

    def send(self, data):
        self.sent.append(data)

    def close(self):
        self.connected = False


class _FakeFrameWS(_FakeWS):
    def recv_frame(self):
        if not self._frames:
            self.connected = False
            raise OSError("closed")
        return self._frames.pop(0)

    def pong(self, data):
        self.sent.append(data)


def _chat_frame(username, content):
    return json.dumps({
        "event": "App\\Events\\ChatMessageEvent",
        "data": json.dumps({
            "sender": {"username": username, "identity": {"color": "#123456"}},
            "content": content,
        }),
    })


def _kick_event_frame(event, payload):
    return json.dumps({
        "event": f"App\\Events\\{event}",
        "data": json.dumps(payload),
    })


class KickChatReaderTests(unittest.TestCase):
    def test_chat_message_event_is_parsed(self):
        reader = KickChatReader("someslug")
        reader._ws = _FakeWS([_chat_frame("Carol", "kick chat!")])
        messages = list(reader.iter_messages())
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["nick"], "Carol")
        self.assertEqual(messages[0]["message"], "kick chat!")
        self.assertEqual(messages[0]["color"], "#123456")

    def test_pusher_ping_is_answered_with_pong(self):
        reader = KickChatReader("someslug")
        ws = _FakeWS([
            json.dumps({"event": "pusher:ping"}),
            _chat_frame("Dan", "hi"),
        ])
        reader._ws = ws
        messages = list(reader.iter_messages())
        self.assertEqual(len(messages), 1)
        self.assertIn(json.dumps({"event": "pusher:pong"}), ws.sent)

    def test_non_chat_events_are_ignored(self):
        reader = KickChatReader("someslug")
        reader._ws = _FakeWS([
            json.dumps({"event": "pusher_internal:subscription_succeeded"}),
            _chat_frame("Eve", "real"),
        ])
        messages = list(reader.iter_messages())
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["nick"], "Eve")

    def test_empty_username_or_content_is_dropped(self):
        reader = KickChatReader("someslug")
        reader._ws = _FakeWS([_chat_frame("", "no user")])
        self.assertEqual(list(reader.iter_messages()), [])

    def test_cancel_hook_stops_before_recv(self):
        reader = KickChatReader("someslug", should_cancel=lambda: True)
        reader._ws = _FakeWS([_chat_frame("Frank", "never seen")])
        self.assertEqual(list(reader.iter_messages()), [])

    def test_slug_is_stripped(self):
        self.assertEqual(KickChatReader("/some-slug/").channel_slug, "some-slug")

    def test_connect_requests_a_bounded_websocket(self):
        ws = _FakeWS([])
        create_connection = mock.Mock(return_value=ws)
        websocket_module = SimpleNamespace(create_connection=create_connection)
        with (
            mock.patch.dict(sys.modules, {"websocket": websocket_module}),
            mock.patch.object(kick_ws, "_channel_meta", return_value=(42, "channel")),
            mock.patch.object(
                kick_ws,
                "_probe_pusher_constants",
                return_value=("key", "cluster"),
            ),
        ):
            KickChatReader("channel").connect()

        self.assertEqual(
            create_connection.call_args.kwargs["max_size"],
            KICK_PAYLOAD_LIMIT,
        )

    def test_oversized_websocket_payload_is_rejected_before_parsing(self):
        reader = KickChatReader("someslug")
        ws = _FakeWS(["x" * (KICK_PAYLOAD_LIMIT + 1)])
        reader._ws = ws

        with (
            mock.patch.object(kick_ws.json, "loads") as loads,
            self.assertRaisesRegex(ChatPayloadTooLarge, "payload exceeded"),
        ):
            list(reader.iter_messages())

        loads.assert_not_called()
        self.assertFalse(ws.connected)

    def test_fragmented_websocket_payload_is_bounded(self):
        reader = KickChatReader("someslug")
        reader._ws = _FakeFrameWS([
            SimpleNamespace(
                opcode=1,
                data=b"x" * (KICK_PAYLOAD_LIMIT // 2 + 1),
                fin=False,
            ),
            SimpleNamespace(
                opcode=0,
                data=b"x" * (KICK_PAYLOAD_LIMIT // 2),
                fin=True,
            ),
        ])

        with self.assertRaisesRegex(ChatPayloadTooLarge, "payload exceeded"):
            list(reader.iter_messages())

        self.assertFalse(reader._ws)

    def test_event_envelopes_and_unknown_payloads_are_archived(self):
        frames = [
            _kick_event_frame("SubscriptionEvent", {"username": "Alice"}),
            _kick_event_frame("GiftedSubscriptionsEvent", {"username": "Alice"}),
            _kick_event_frame("RaidEvent", {"sender": {"username": "Alice"}}),
            _kick_event_frame("AnnouncementEvent", {"content": "Heads up"}),
            _kick_event_frame("MessageDeletedEvent", {"username": "Bob"}),
            _kick_event_frame("UserBannedEvent", {"username": "Bob", "duration": 30}),
            _kick_event_frame("FutureEnvelopeEvent", {"opaque": {"value": 7}}),
        ]
        reader = KickChatReader("someslug")
        reader._ws = _FakeWS(frames)
        rows = list(reader.iter_messages())
        self.assertEqual(
            [row["event_kind"] for row in rows],
            [
                "subscription", "gift_subscription", "raid", "announcement",
                "message_delete", "timeout", "kick:futureenvelope",
            ],
        )
        self.assertEqual(rows[5]["duration"], 30)
        self.assertEqual(rows[-1]["event_data"]["opaque"]["value"], 7)
        self.assertIn("FutureEnvelopeEvent", rows[-1]["raw_event"])

    def test_is_available_reflects_optional_dep(self):
        # Just assert the probe returns a bool and does not raise.
        self.assertIsInstance(kick_ws.is_available(), bool)


class ChatEventArchiveTests(unittest.TestCase):
    def test_chat_worker_surfaces_transport_limit_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "streamkeep.chat.chat_worker.TwitchIRCReader"
        ) as reader_cls:
            reader_cls.return_value.iter_messages.side_effect = (
                ChatPayloadTooLarge("Twitch IRC buffer exceeded its limit")
            )
            logs = []
            worker = ChatWorker("chan", tmpdir, render_ass=False)
            worker.log.connect(logs.append)
            worker.run()

        self.assertIn(
            "[CHAT] Reader error: Twitch IRC buffer exceeded its limit",
            logs,
        )

    def test_chat_worker_writes_events_and_distinct_ass_rows(self):
        rows = [
            {
                "ts": 1.0, "nick": "Alice", "message": "hello",
                "color": "", "badges": "", "mod": False, "sub": False,
            },
            {
                "ts": 2.0, "nick": "system", "message": "Alice raided",
                "color": "", "badges": "", "mod": False, "sub": False,
                "event_kind": "raid", "event_type": "raid",
                "raw_event": "fixture-raid",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "streamkeep.chat.chat_worker.TwitchIRCReader"
        ) as reader_cls:
            reader = reader_cls.return_value
            reader.iter_messages.return_value = rows
            worker = ChatWorker(
                "chan", tmpdir, render_ass=True, start_ts=0,
            )
            worker.run()
            saved = [json.loads(line) for line in
                     (Path(tmpdir) / "chat.jsonl").read_text(encoding="utf-8").splitlines()]
            ass = (Path(tmpdir) / "chat.ass").read_text(encoding="utf-8")
        self.assertEqual(saved, rows)
        self.assertIn("ChatEvent", ass)
        self.assertIn("[RAID] system: Alice raided", ass)

    def test_render_loader_keeps_unknown_event_without_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chat.jsonl"
            path.write_text(
                json.dumps({
                    "ts": 10, "nick": "system", "event_kind": "kick:future",
                }) + "\n",
                encoding="utf-8",
            )
            rows = _load_chat_jsonl(str(path))
        self.assertEqual(rows[0]["event_kind"], "kick:future")
        self.assertEqual(rows[0]["text"], "KICK:FUTURE")

    def test_event_rows_receive_extra_highlight_weight(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chat.jsonl"
            path.write_text(
                "\n".join([
                    json.dumps({"ts": 0, "message": "one"}),
                    json.dumps({"ts": 10, "message": "two"}),
                    json.dumps({"ts": 20, "message": "raid", "event_kind": "raid"}),
                ]) + "\n",
                encoding="utf-8",
            )
            plain = detect_spikes(
                str(path), bucket_secs=10, min_std_dev=1.0, start_ts=0,
            )
            weighted = detect_spikes(
                str(path), bucket_secs=10, min_std_dev=1.0, start_ts=0,
                event_weights={"*": 3.0},
            )
        self.assertEqual(plain, [])
        self.assertEqual(weighted[0]["time"], 20)


if __name__ == "__main__":
    unittest.main()
