"""Third-party emote cache — BTTV, FFZ, 7TV.

Downloads and caches emote images per channel so the chat renderer can
embed them inline with text. Each emote is stored as a PNG in
``%APPDATA%/StreamKeep/emote_cache/<provider>_<id>.png``.

The three APIs:
  - BTTV: https://api.betterttv.net/3/cached/users/twitch/{twitch_id}
          https://api.betterttv.net/3/cached/emotes/global
  - FFZ:  https://api.frankerfacez.com/v1/room/id/{twitch_id}
          https://api.frankerfacez.com/v1/set/global
  - 7TV:  https://7tv.io/v3/users/twitch/{twitch_id}
          https://7tv.io/v3/emote-sets/global
"""

import json
import logging
import urllib.error
import urllib.request

from ..paths import CONFIG_DIR

logger = logging.getLogger(__name__)

EMOTE_CACHE_DIR = CONFIG_DIR / "emote_cache"

_CDN = {
    "bttv": "https://cdn.betterttv.net/emote/{id}/2x.png",
    "ffz": "https://cdn.frankerfacez.com/emote/{id}/2",
    "7tv": "https://cdn.7tv.app/emote/{id}/2x.webp",
}

_UA = "StreamKeep/1.0 (emote-cache)"
_TIMEOUT = 10


def _ensure_dir():
    EMOTE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cached_path(provider, emote_id):
    return EMOTE_CACHE_DIR / f"{provider}_{emote_id}.png"


def _download(url, dest):
    """Download a URL to dest. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = resp.read(2 * 1024 * 1024)
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _fetch_json(url):
    """GET JSON from a URL. Returns parsed dict/list or None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read(1 * 1024 * 1024))
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        return None


# ── Per-provider fetchers ──────────────────────────────────────────

def fetch_bttv_emotes(twitch_user_id):
    """Return a dict of {emote_name: (provider, emote_id)} for BTTV."""
    result = {}
    global_data = _fetch_json("https://api.betterttv.net/3/cached/emotes/global")
    if isinstance(global_data, list):
        for e in global_data:
            if isinstance(e, dict) and e.get("code") and e.get("id"):
                result[e["code"]] = ("bttv", e["id"])

    if twitch_user_id:
        user_data = _fetch_json(
            f"https://api.betterttv.net/3/cached/users/twitch/{twitch_user_id}"
        )
        if isinstance(user_data, dict):
            for e in user_data.get("channelEmotes", []):
                if isinstance(e, dict) and e.get("code") and e.get("id"):
                    result[e["code"]] = ("bttv", e["id"])
            for e in user_data.get("sharedEmotes", []):
                if isinstance(e, dict) and e.get("code") and e.get("id"):
                    result[e["code"]] = ("bttv", e["id"])
    return result


def fetch_ffz_emotes(twitch_user_id):
    """Return a dict of {emote_name: (provider, emote_id)} for FFZ."""
    result = {}
    global_data = _fetch_json("https://api.frankerfacez.com/v1/set/global")
    if isinstance(global_data, dict):
        for s in (global_data.get("sets") or {}).values():
            for e in s.get("emoticons", []):
                if isinstance(e, dict) and e.get("name") and e.get("id"):
                    result[e["name"]] = ("ffz", str(e["id"]))

    if twitch_user_id:
        user_data = _fetch_json(
            f"https://api.frankerfacez.com/v1/room/id/{twitch_user_id}"
        )
        if isinstance(user_data, dict):
            for s in (user_data.get("sets") or {}).values():
                for e in s.get("emoticons", []):
                    if isinstance(e, dict) and e.get("name") and e.get("id"):
                        result[e["name"]] = ("ffz", str(e["id"]))
    return result


def fetch_7tv_emotes(twitch_user_id):
    """Return a dict of {emote_name: (provider, emote_id)} for 7TV."""
    result = {}
    global_data = _fetch_json("https://7tv.io/v3/emote-sets/global")
    if isinstance(global_data, dict):
        for e in global_data.get("emotes", []):
            if isinstance(e, dict) and e.get("name") and e.get("id"):
                result[e["name"]] = ("7tv", e["id"])

    if twitch_user_id:
        user_data = _fetch_json(
            f"https://7tv.io/v3/users/twitch/{twitch_user_id}"
        )
        if isinstance(user_data, dict):
            emote_set = user_data.get("emote_set") or {}
            for e in emote_set.get("emotes", []):
                if isinstance(e, dict) and e.get("name") and e.get("id"):
                    result[e["name"]] = ("7tv", e["id"])
    return result


# ── Public API ─────────────────────────────────────────────────────

def load_channel_emotes(twitch_user_id=None, log_fn=None):
    """Load all BTTV + FFZ + 7TV emotes for a channel.

    Returns ``{emote_name: (provider, emote_id)}``.
    """
    emotes = {}
    for name, fetcher in [
        ("BTTV", fetch_bttv_emotes),
        ("FFZ", fetch_ffz_emotes),
        ("7TV", fetch_7tv_emotes),
    ]:
        try:
            found = fetcher(twitch_user_id)
            emotes.update(found)
            if log_fn:
                log_fn(f"[EMOTE] {name}: {len(found)} emotes")
        except Exception as e:
            logger.debug("Failed to fetch %s emotes: %s", name, e)
            if log_fn:
                log_fn(f"[EMOTE] {name} fetch failed: {e}")
    return emotes


def get_emote_image(provider, emote_id):
    """Return the local path to a cached emote image, downloading if needed.

    Returns the path string or None if download fails.
    """
    _ensure_dir()
    path = _cached_path(provider, emote_id)
    if path.exists() and path.stat().st_size > 0:
        return str(path)
    cdn_template = _CDN.get(provider)
    if not cdn_template:
        return None
    url = cdn_template.replace("{id}", str(emote_id))
    if _download(url, str(path)):
        return str(path)
    return None
