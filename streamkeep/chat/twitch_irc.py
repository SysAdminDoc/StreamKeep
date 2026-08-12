"""Minimal anonymous Twitch IRC reader.

Connects to `irc.chat.twitch.tv:6667` as an anonymous (justinfanNNNNN)
user, requests the tags+commands capabilities, joins one channel, and
yields parsed messages to the caller. No external deps — just stdlib
`socket`.

This is scoped to public chat read-only. Authenticated (member-only)
chat would need an OAuth flow we don't ship yet.
"""

import random
import re
import socket
import ssl
import time

from .limits import ChatPayloadTooLarge, IRC_BUFFER_LIMIT

SERVER = "irc.chat.twitch.tv"
PORT = 6697


# IRCv3 tag-bearing line pattern:
#   @tag1=val;tag2=val :nick!user@host PRIVMSG #chan :message text
_MSG_RE = re.compile(
    r"^(?:@(?P<tags>[^ ]+)\s)?"
    r":(?P<nick>[^!]+)!(?:[^ ]+)\s"
    r"PRIVMSG\s#(?P<channel>\S+)\s"
    r":(?P<message>.+)$"
)

_EVENT_COMMANDS = frozenset({"USERNOTICE", "CLEARCHAT", "CLEARMSG"})
_USERNOTICE_KINDS = {
    "sub": "subscription",
    "resub": "resubscription",
    "subgift": "gift_subscription",
    "anonsubgift": "gift_subscription",
    "submysterygift": "gift_subscription",
    "communitysubgift": "gift_subscription",
    "giftpaidupgrade": "gift_subscription_upgrade",
    "anongiftpaidupgrade": "gift_subscription_upgrade",
    "raid": "raid",
    "unraid": "raid_cancelled",
    "announcement": "announcement",
}


def _parse_tags(tag_str):
    if not tag_str:
        return {}
    out = {}
    for pair in tag_str.split(";"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        out[k] = v
    return out


def _unescape_tag_value(value):
    return (
        str(value or "")
        .replace(r"\s", " ")
        .replace(r"\:", ";")
        .replace(r"\r", "\r")
        .replace(r"\n", "\n")
        .replace(r"\\", "\\")
    )


def _parse_irc_line(line):
    """Return ``(tags, prefix, command, params, trailing)`` for one IRC line."""
    text = str(line or "")
    tags = {}
    if text.startswith("@"):
        tag_text, separator, text = text.partition(" ")
        if not separator:
            return {}, "", "", [], ""
        tags = _parse_tags(tag_text[1:])
    prefix = ""
    if text.startswith(":"):
        prefix, separator, text = text[1:].partition(" ")
        if not separator:
            return tags, prefix, "", [], ""
    command, separator, params_text = text.partition(" ")
    if not command:
        return tags, prefix, "", [], ""
    trailing = ""
    if " :" in params_text:
        params_text, trailing = params_text.split(" :", 1)
    params = params_text.split() if params_text else []
    return tags, prefix, command.upper(), params, trailing


def _event_kind(command, tags):
    command = str(command or "").upper()
    if command == "USERNOTICE":
        msg_id = str(tags.get("msg-id", "") or "").casefold()
        return _USERNOTICE_KINDS.get(msg_id, f"usernotice:{msg_id}" if msg_id else "usernotice")
    if command == "CLEARCHAT":
        return "timeout" if tags.get("target-user-id") or tags.get("ban-duration") else "chat_clear"
    if command == "CLEARMSG":
        return "message_delete"
    return command.casefold()


def _event_row(command, tags, prefix, params, trailing, raw_line):
    """Normalize Twitch moderation/system commands without losing raw IRC."""
    kind = _event_kind(command, tags)
    msg_id = str(tags.get("msg-id", "") or "")
    prefix_nick = str(prefix or "").split("!", 1)[0]
    nick = (
        _unescape_tag_value(tags.get("display-name"))
        or _unescape_tag_value(tags.get("login"))
        or prefix_nick
        or "system"
    )
    target = (
        _unescape_tag_value(tags.get("target-user-name"))
        or _unescape_tag_value(tags.get("login"))
        or str(trailing or "")
    )
    message = (
        _unescape_tag_value(trailing)
        or _unescape_tag_value(tags.get("system-msg"))
        or target
        or kind
    )
    row = {
        "ts": time.time(),
        "nick": nick,
        "message": message,
        "color": tags.get("color", "") or "",
        "badges": tags.get("badges", "") or "",
        "mod": tags.get("mod", "0") == "1",
        "sub": tags.get("subscriber", "0") == "1",
        "event_kind": kind,
        "event_type": msg_id or str(command or "").upper(),
        "raw_event": str(raw_line or ""),
        "event_data": dict(tags),
    }
    if target:
        row["target"] = target
    if tags.get("ban-duration"):
        try:
            row["duration"] = int(tags["ban-duration"])
        except (TypeError, ValueError):
            row["duration"] = str(tags["ban-duration"])
    if params:
        row["channel"] = str(params[0]).lstrip("#")
    return row


class TwitchIRCReader:
    """Open a blocking-but-interruptible Twitch IRC connection and
    iterate chat messages. Intended to be driven by a QThread so the
    `should_cancel` callback can stop the read loop."""

    def __init__(self, channel, should_cancel=None, timeout=15):
        # Twitch IRC expects lower-case channels without the # prefix.
        self.channel = (channel or "").lstrip("#").lower()
        self.should_cancel = should_cancel or (lambda: False)
        self.timeout = timeout
        self._sock = None

    def connect(self):
        nick = f"justinfan{random.randint(10000, 99999)}"
        raw = socket.create_connection((SERVER, PORT), timeout=self.timeout)
        try:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(raw, server_hostname=SERVER)
            sock.settimeout(1.0)
            sock.sendall(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
            sock.sendall(b"PASS SCHMOOPIIE\r\n")
            sock.sendall(f"NICK {nick}\r\n".encode())
            sock.sendall(f"JOIN #{self.channel}\r\n".encode())
            self._sock = sock
        except Exception:
            try:
                raw.close()
            except OSError:
                pass
            raise

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def iter_messages(self):
        """Yield dicts {ts, nick, message, color, badges, mod, sub} as
        they arrive. Returns when the cancel hook fires or the socket
        dies — safe for a QThread run-loop."""
        if self._sock is None:
            self.connect()
        buf = ""
        while not self.should_cancel():
            try:
                data = self._sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            try:
                buf += data.decode("utf-8", errors="replace")
            except Exception:
                continue
            while "\r\n" in buf:
                line, buf = buf.split("\r\n", 1)
                if not line:
                    continue
                if line.startswith("PING"):
                    try:
                        self._sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                    except OSError:
                        return
                    continue
                m = _MSG_RE.match(line)
                if m:
                    tags = _parse_tags(m.group("tags") or "")
                    yield {
                        "ts": time.time(),
                        "nick": tags.get("display-name") or m.group("nick"),
                        "message": m.group("message"),
                        "color": tags.get("color", "") or "",
                        "badges": tags.get("badges", "") or "",
                        "mod": tags.get("mod", "0") == "1",
                        "sub": tags.get("subscriber", "0") == "1",
                    }
                    continue
                tags, prefix, command, params, trailing = _parse_irc_line(line)
                if command in _EVENT_COMMANDS:
                    yield _event_row(
                        command, tags, prefix, params, trailing, line,
                    )
            if len(buf) > IRC_BUFFER_LIMIT:
                self.close()
                raise ChatPayloadTooLarge(
                    "Twitch IRC buffer exceeded "
                    f"{IRC_BUFFER_LIMIT} bytes without a line terminator"
                )
