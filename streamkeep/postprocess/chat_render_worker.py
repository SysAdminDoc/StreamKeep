"""ChatRenderWorker — render a chat.jsonl file into an animated video overlay.

Reads .chat.jsonl (one JSON object per line: {ts, nick, message, color, ...}),
renders scrolling chat messages using Pillow frame-by-frame, and pipes raw
frames to ffmpeg for H.264 encoding.

Each message appears at the bottom and scrolls upward, staying visible for
`msg_duration` seconds. Username colors are preserved from IRC tags.

When emote support is enabled, BTTV/FFZ/7TV emotes are downloaded and cached
per channel, then rendered inline with the text at the configured font size.

Output: ``{recording_dir}/chat_render.mp4``
"""

import json
import logging
import os
import subprocess

try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 89_478_485
except ImportError:
    pass

from PyQt6.QtCore import QThread, pyqtSignal

from ..paths import _CREATE_NO_WINDOW, CONFIG_DIR, FFMPEG_SAFETY
from .emote_cache import load_channel_emotes, get_emote_image

logger = logging.getLogger(__name__)

# Emote cache directory
EMOTE_CACHE_DIR = CONFIG_DIR / "emote_cache"

# Default render settings
DEFAULT_WIDTH = 400
DEFAULT_HEIGHT = 600
DEFAULT_FONT_SIZE = 14
DEFAULT_MSG_DURATION = 8.0
DEFAULT_BG_OPACITY = 180       # 0-255
DEFAULT_FPS = 30


def _hex_to_rgb(color_hex):
    """Convert '#RRGGBB' or 'RRGGBB' to (R, G, B) tuple."""
    color_hex = (color_hex or "").lstrip("#").strip()
    if len(color_hex) != 6:
        return (200, 200, 200)
    try:
        return (int(color_hex[0:2], 16), int(color_hex[2:4], 16), int(color_hex[4:6], 16))
    except ValueError:
        return (200, 200, 200)


# Assign deterministic colors to nicks that don't carry an IRC color tag.
_NICK_PALETTE = [
    "#FF4500", "#1E90FF", "#00CED1", "#FF69B4", "#FFD700",
    "#9ACD32", "#BA55D3", "#FF6347", "#00FF7F", "#DC143C",
    "#7B68EE", "#FF8C00", "#20B2AA", "#FF1493", "#ADFF2F",
]


def _nick_color(nick, color_tag=""):
    """Return (R,G,B) for a nick — use the IRC color if available."""
    if color_tag and len(color_tag.lstrip("#")) == 6:
        return _hex_to_rgb(color_tag)
    idx = sum(ord(c) for c in nick) % len(_NICK_PALETTE)
    return _hex_to_rgb(_NICK_PALETTE[idx])


def _load_chat_jsonl(path, start_ts=None):
    """Load chat.jsonl and normalize timestamps relative to the first message."""
    messages = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = float(obj.get("ts", 0))
                nick = obj.get("nick", "") or "?"
                text = obj.get("message", "") or ""
                color = obj.get("color", "") or ""
                if text:
                    messages.append({
                        "ts": ts,
                        "nick": nick,
                        "text": text,
                        "color": color,
                    })
    except OSError:
        return []
    if not messages:
        return []
    # Normalize timestamps relative to start_ts or first message
    base_ts = start_ts if start_ts is not None else messages[0]["ts"]
    for m in messages:
        m["rel"] = max(0.0, m["ts"] - base_ts)
    return messages


def _fetch_emote_image(emote_id, source="twitch"):
    """Fetch an emote image to local cache. Returns path or None."""
    EMOTE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = EMOTE_CACHE_DIR / f"{source}_{emote_id}.png"
    if cache_file.exists():
        return str(cache_file)
    # CDN URLs for different providers
    urls = {
        "bttv": f"https://cdn.betterttv.net/emote/{emote_id}/2x.png",
        "ffz": f"https://cdn.frankerfacez.com/emote/{emote_id}/2",
        "7tv": f"https://cdn.7tv.app/emote/{emote_id}/2x.webp",
    }
    url = urls.get(source)
    if not url:
        return None
    try:
        import urllib.request
        urllib.request.urlretrieve(url, str(cache_file))
        return str(cache_file)
    except Exception:
        return None


class ChatRenderWorker(QThread):
    """Render chat.jsonl into an MP4 video overlay."""

    progress = pyqtSignal(int, str)
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, chat_jsonl_path, output_path=None, *,
                 width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
                 font_size=DEFAULT_FONT_SIZE,
                 msg_duration=DEFAULT_MSG_DURATION,
                 bg_opacity=DEFAULT_BG_OPACITY,
                 fps=DEFAULT_FPS, preview_secs=0,
                 twitch_user_id="", enable_emotes=True):
        super().__init__()
        self.chat_jsonl_path = chat_jsonl_path
        self.output_path = output_path or os.path.join(
            os.path.dirname(chat_jsonl_path), "chat_render.mp4"
        )
        self.width = max(200, int(width))
        self.height = max(200, int(height))
        self.font_size = max(8, int(font_size))
        self.msg_duration = max(1.0, float(msg_duration))
        self.bg_opacity = max(0, min(255, int(bg_opacity)))
        self.fps = max(1, min(60, int(fps)))
        self.preview_secs = float(preview_secs)
        self.twitch_user_id = str(twitch_user_id or "")
        self.enable_emotes = bool(enable_emotes)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            self.done.emit(False, "Pillow not installed. Run: pip install Pillow")
            return

        messages = _load_chat_jsonl(self.chat_jsonl_path)
        if not messages:
            self.done.emit(False, "No chat messages found.")
            return

        # Load third-party emotes (BTTV/FFZ/7TV)
        emote_map = {}
        emote_images = {}
        if self.enable_emotes:
            self.progress.emit(0, "Loading emotes...")
            emote_map = load_channel_emotes(
                self.twitch_user_id or None,
                log_fn=lambda msg: self.log.emit(msg),
            )
            if emote_map:
                emote_size = self.font_size + 2
                for name, (provider, eid) in emote_map.items():
                    path = get_emote_image(provider, eid)
                    if path:
                        try:
                            eimg = Image.open(path).convert("RGBA")
                            eimg = eimg.resize(
                                (emote_size, emote_size),
                                Image.LANCZOS,
                            )
                            emote_images[name] = eimg
                        except Exception:
                            pass
                self.log.emit(
                    f"[CHAT RENDER] {len(emote_images)}/{len(emote_map)} "
                    f"emote images loaded"
                )

        total_duration = messages[-1]["rel"] + self.msg_duration
        if self.preview_secs > 0:
            total_duration = min(total_duration, self.preview_secs)

        total_frames = max(1, int(total_duration * self.fps))
        self.progress.emit(0, f"Rendering {len(messages)} messages, {total_duration:.0f}s...")
        self.log.emit(
            f"[CHAT RENDER] {len(messages)} messages, "
            f"{total_duration:.0f}s @ {self.fps}fps = {total_frames} frames"
        )

        # Try to load a monospace font
        try:
            font = ImageFont.truetype("consola.ttf", self.font_size)
        except (OSError, IOError):
            try:
                font = ImageFont.truetype("DejaVuSansMono.ttf", self.font_size)
            except (OSError, IOError):
                font = ImageFont.load_default()

        line_height = self.font_size + 4
        max_visible = self.height // line_height

        # Start ffmpeg pipe
        cmd = [
            "ffmpeg", *FFMPEG_SAFETY, "-hide_banner", "-loglevel", "error",
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{self.width}x{self.height}",
            "-r", str(self.fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "23",
            self.output_path,
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            self.done.emit(False, "ffmpeg not found in PATH.")
            return

        bg_color = (24, 24, 37, self.bg_opacity)  # Catppuccin Mocha crust

        try:
            msg_idx = 0
            active_msgs = []  # list of (expire_time, nick, text, color_rgb)

            for frame_num in range(total_frames):
                if self._cancel:
                    proc.stdin.close()
                    proc.wait()
                    self.done.emit(False, "Cancelled.")
                    return

                t = frame_num / self.fps

                # Add messages that should appear at this time
                while msg_idx < len(messages) and messages[msg_idx]["rel"] <= t:
                    m = messages[msg_idx]
                    rgb = _nick_color(m["nick"], m["color"])
                    expire = m["rel"] + self.msg_duration
                    active_msgs.append((expire, m["nick"], m["text"], rgb))
                    msg_idx += 1

                # Remove expired messages
                active_msgs = [a for a in active_msgs if a[0] > t]

                # Render frame
                img = Image.new("RGBA", (self.width, self.height), bg_color)
                draw = ImageDraw.Draw(img)

                # Draw visible messages (most recent at bottom)
                visible = active_msgs[-max_visible:]
                y = self.height - len(visible) * line_height - 4
                text_color = (205, 214, 244)
                emote_h = self.font_size + 2
                for _expire, nick, text, nick_rgb in visible:
                    nick_display = f"{nick}: "
                    draw.text((6, y), nick_display, fill=nick_rgb, font=font)
                    try:
                        nick_w = draw.textlength(nick_display, font=font)
                    except (TypeError, AttributeError):
                        nick_w = len(nick_display) * (self.font_size * 0.6)
                    cursor_x = 6 + int(nick_w)
                    max_x = self.width - 6
                    if emote_images:
                        for word in text.split():
                            if cursor_x >= max_x:
                                break
                            eimg = emote_images.get(word)
                            if eimg is not None:
                                if cursor_x + emote_h <= max_x:
                                    img.paste(eimg, (int(cursor_x), y), eimg)
                                    cursor_x += emote_h + 2
                            else:
                                frag = word + " "
                                try:
                                    fw = draw.textlength(frag, font=font)
                                except (TypeError, AttributeError):
                                    fw = len(frag) * (self.font_size * 0.6)
                                if cursor_x + fw > max_x:
                                    avail = max_x - cursor_x
                                    try:
                                        chars = int(avail / max(fw / len(frag), 1))
                                    except ZeroDivisionError:
                                        chars = 0
                                    frag = word[:max(chars - 1, 0)] + "…"
                                    draw.text((int(cursor_x), y), frag, fill=text_color, font=font)
                                    cursor_x = max_x
                                else:
                                    draw.text((int(cursor_x), y), frag, fill=text_color, font=font)
                                    cursor_x += fw
                    else:
                        remaining_text = text
                        max_text_w = max_x - cursor_x
                        try:
                            if draw.textlength(text, font=font) > max_text_w:
                                lo, hi = 0, len(text)
                                while lo < hi:
                                    mid = (lo + hi + 1) // 2
                                    if draw.textlength(text[:mid], font=font) <= max_text_w:
                                        lo = mid
                                    else:
                                        hi = mid - 1
                                remaining_text = text[:max(lo - 1, 0)] + "…" if lo < len(text) else text
                        except (TypeError, AttributeError):
                            remaining_text = text[:int(max_text_w / (self.font_size * 0.6))]
                        draw.text((int(cursor_x), y), remaining_text, fill=text_color, font=font)
                    y += line_height

                # Write raw RGBA frame to ffmpeg
                proc.stdin.write(img.tobytes())

                # Progress update every 30 frames
                if frame_num % 30 == 0:
                    pct = min(99, int(frame_num / total_frames * 100))
                    self.progress.emit(pct, f"Frame {frame_num}/{total_frames}")

        except (BrokenPipeError, OSError) as e:
            self.done.emit(False, f"ffmpeg pipe error: {e}")
            return
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.wait()
        if proc.returncode != 0:
            stderr = ""
            try:
                stderr = proc.stderr.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            self.done.emit(False, f"ffmpeg failed (exit {proc.returncode}): {stderr}")
            return

        self.progress.emit(100, "Complete")
        self.log.emit(f"[CHAT RENDER] Wrote {self.output_path}")
        self.done.emit(True, self.output_path)
