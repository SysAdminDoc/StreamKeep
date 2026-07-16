"""YouTube live-chat replay normalization.

yt-dlp writes a YouTube VOD's live-chat replay as a ``*.live_chat.json`` file:
one JSON object per line, each a ``replayChatItemAction`` envelope. This module
flattens those envelopes into the same message dict shape the Twitch/Kick
readers produce (``ts``/``nick``/``message``/``color``/``badges``/``mod``/
``sub``, plus ``owner``/``member`` flags and a replay ``offset``) so the
existing spike-detection, highlight, ASS-render, and export tools consume it
unchanged. Partial/truncated files are non-fatal — malformed lines are skipped.
"""

import csv
import json
import os
import re

# Renderers that carry a user-authored chat line we want to keep.
_MESSAGE_RENDERERS = (
    "liveChatTextMessageRenderer",
    "liveChatPaidMessageRenderer",
    "liveChatPaidStickerRenderer",
    "liveChatMembershipItemRenderer",
)


def _runs_to_text(message):
    """Flatten a YouTube ``message.runs`` array into plain text.

    Text runs pass through; emoji runs render as their first shortcut (e.g.
    ``:smile:``) or accessibility label so custom/standard emotes survive.
    """
    if not isinstance(message, dict):
        return ""
    if "simpleText" in message:
        return str(message.get("simpleText") or "")
    parts = []
    for run in message.get("runs") or []:
        if not isinstance(run, dict):
            continue
        if "text" in run:
            parts.append(str(run.get("text") or ""))
        elif "emoji" in run:
            emoji = run.get("emoji") or {}
            shortcuts = emoji.get("shortcuts") or []
            if shortcuts:
                parts.append(str(shortcuts[0]))
            else:
                label = (
                    (emoji.get("image") or {})
                    .get("accessibility", {})
                    .get("accessibilityData", {})
                    .get("label", "")
                )
                parts.append(str(label or ""))
    return "".join(parts)


def _author_flags(renderer):
    owner = mod = member = False
    for badge in renderer.get("authorBadges") or []:
        info = badge.get("liveChatAuthorBadgeRenderer") or {}
        icon_type = str((info.get("icon") or {}).get("iconType", "")).upper()
        tooltip = str(info.get("tooltip", "")).lower()
        if icon_type == "OWNER" or "owner" in tooltip:
            owner = True
        elif icon_type == "MODERATOR" or "moderator" in tooltip:
            mod = True
        # A custom (image) badge with a "member" tooltip marks a channel member.
        if info.get("customThumbnail") or "member" in tooltip:
            member = True
    return owner, mod, member


def normalize_youtube_action(envelope):
    """Normalize one ``replayChatItemAction`` envelope to a message dict.

    Returns ``None`` for non-message events (deletions, banners, etc.).
    """
    if not isinstance(envelope, dict):
        return None
    replay = envelope.get("replayChatItemAction")
    if not isinstance(replay, dict):
        return None
    offset_ms = replay.get("videoOffsetTimeMsec")
    for action in replay.get("actions") or []:
        add = (action or {}).get("addChatItemAction")
        if not isinstance(add, dict):
            continue
        item = add.get("item") or {}
        renderer = None
        for key in _MESSAGE_RENDERERS:
            if isinstance(item.get(key), dict):
                renderer = item[key]
                break
        if renderer is None:
            continue
        nick = _runs_to_text(renderer.get("authorName"))
        text = _runs_to_text(renderer.get("message"))
        # Superchats/stickers carry the amount; memberships carry a header.
        amount = _runs_to_text(renderer.get("purchaseAmountText"))
        if amount:
            text = f"[{amount}] {text}".strip()
        if not text:
            text = _runs_to_text(renderer.get("headerSubtext"))
        if not nick and not text:
            continue
        try:
            ts = int(renderer.get("timestampUsec", 0)) / 1_000_000
        except (TypeError, ValueError):
            ts = 0.0
        try:
            offset = int(offset_ms) / 1000 if offset_ms is not None else 0.0
        except (TypeError, ValueError):
            offset = 0.0
        owner, mod, member = _author_flags(renderer)
        return {
            "ts": ts,
            "nick": nick,
            "message": text,
            "color": "",
            "badges": ",".join(
                b for b, on in (("owner", owner), ("moderator", mod),
                                ("member", member)) if on
            ),
            "mod": mod,
            "sub": member,
            "owner": owner,
            "member": member,
            "offset": offset,
        }
    return None


def parse_youtube_live_chat(text):
    """Parse a yt-dlp ``.live_chat.json`` document into normalized messages."""
    messages = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            envelope = json.loads(line)
        except ValueError:
            continue  # truncated/partial line — skip, non-fatal
        msg = normalize_youtube_action(envelope)
        if msg is not None:
            messages.append(msg)
    return messages


def filter_messages(messages, *, user=None, pattern=None):
    """Filter by exact nick (case-insensitive) and/or a message regex."""
    regex = None
    if pattern:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = None
    user_lc = user.lower() if user else None
    out = []
    for msg in messages:
        if user_lc and str(msg.get("nick", "")).lower() != user_lc:
            continue
        if regex is not None and not regex.search(str(msg.get("message", ""))):
            continue
        out.append(msg)
    return out


def export_chat_csv(messages, path):
    """Write normalized messages to a CSV (ts, offset, nick, badges, message)."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "offset", "nick", "badges", "message"])
        for msg in messages:
            writer.writerow([
                msg.get("ts", 0), msg.get("offset", 0),
                msg.get("nick", ""), msg.get("badges", ""),
                msg.get("message", ""),
            ])
    return path


def write_chat_jsonl(messages, path):
    """Append normalized messages to a ``chat.jsonl`` file."""
    with open(path, "a", encoding="utf-8") as handle:
        for msg in messages:
            handle.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return path


def ingest_replay_dir(out_dir, *, write_csv=True, log_fn=None):
    """Normalize any yt-dlp ``*.live_chat.json`` in *out_dir* into ``chat.jsonl``.

    Reachable finalize step: whatever yt-dlp already downloaded is flattened
    into the shared chat model (and an optional ``chat.csv``) so the existing
    spike/highlight/render tools work on YouTube replays. Returns the number of
    messages written. Absent/partial files are non-fatal.
    """
    if not out_dir or not os.path.isdir(out_dir):
        return 0
    try:
        candidates = [
            os.path.join(out_dir, name)
            for name in os.listdir(out_dir)
            if name.lower().endswith(".live_chat.json")
        ]
    except OSError:
        return 0
    if not candidates:
        return 0
    all_messages = []
    for path in sorted(candidates):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                all_messages.extend(parse_youtube_live_chat(handle.read()))
        except OSError:
            continue
    if not all_messages:
        return 0
    all_messages.sort(key=lambda m: m.get("ts", 0))
    write_chat_jsonl(all_messages, os.path.join(out_dir, "chat.jsonl"))
    if write_csv:
        try:
            export_chat_csv(all_messages, os.path.join(out_dir, "chat.csv"))
        except OSError:
            pass
    if log_fn:
        log_fn(
            f"[CHAT] Normalized {len(all_messages)} YouTube replay message(s) "
            "into chat.jsonl"
        )
    return len(all_messages)
