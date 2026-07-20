"""Dedicated coverage for the Twitch IRC and Kick WebSocket chat readers.

The network transports are replaced with in-memory fakes so the parsing,
PING/PONG keepalive, and cancel-loop behaviour can be asserted without a live
connection.
"""

import json
import socket
import unittest

from streamkeep.chat import kick_ws
from streamkeep.chat.kick_ws import KickChatReader
from streamkeep.chat.twitch_irc import TwitchIRCReader, _parse_tags


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


def _chat_frame(username, content):
    return json.dumps({
        "event": "App\\Events\\ChatMessageEvent",
        "data": json.dumps({
            "sender": {"username": username, "identity": {"color": "#123456"}},
            "content": content,
        }),
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

    def test_is_available_reflects_optional_dep(self):
        # Just assert the probe returns a bool and does not raise.
        self.assertIsInstance(kick_ws.is_available(), bool)


if __name__ == "__main__":
    unittest.main()
