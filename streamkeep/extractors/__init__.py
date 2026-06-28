"""Extractor registry + concrete extractor classes.

Each concrete subclass auto-registers itself via `__init_subclass__`.
Importing this package imports every extractor module so the registry
is populated as a side effect — consumers only need
`from streamkeep.extractors import Extractor`.
"""

import importlib

from .base import Extractor

# Importing each submodule triggers its subclass registration.
# Order matters: `YtDlpExtractor` is the catch-all fallback and must
# register last so platform-specific matchers get first chance at a URL.
for _module in (
    "kick", "twitch", "rumble", "soundcloud",
    "reddit", "audius", "podcast", "ytdlp",
):
    importlib.import_module(f"{__name__}.{_module}")

from .kick import KickExtractor
from .twitch import TwitchExtractor
from .rumble import RumbleExtractor
from .soundcloud import SoundCloudExtractor
from .reddit import RedditExtractor
from .audius import AudiusExtractor
from .podcast import PodcastRSSExtractor
from .ytdlp import YtDlpExtractor

__all__ = [
    "Extractor",
    "KickExtractor",
    "TwitchExtractor",
    "RumbleExtractor",
    "SoundCloudExtractor",
    "RedditExtractor",
    "AudiusExtractor",
    "PodcastRSSExtractor",
    "YtDlpExtractor",
]
