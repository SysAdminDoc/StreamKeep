"""Sample StreamKeep extractor plugin.

This demonstrates the plugin SDK contract. To install: copy this folder
into ``%APPDATA%/StreamKeep/plugins/`` and mark it trusted in Settings.
"""

import re

from streamkeep.extractors.base import Extractor
from streamkeep.models import QualityInfo, StreamInfo


class SampleExtractor(Extractor):
    NAME = "Sample"
    ICON = "S"
    COLOR = "green"
    URL_PATTERNS = [
        re.compile(r"(?:https?://)?(?:www\.)?sample-streaming\.example\.com/"),
    ]

    def resolve(self, url, log_fn=None):
        self._log(log_fn, f"Resolving sample URL: {url}")
        return StreamInfo(
            title="Sample Stream",
            url=url,
            platform="Sample",
            qualities=[
                QualityInfo(name="720p", url=url, format_type="mp4"),
            ],
        )

    def extract_channel_id(self, url):
        return "sample-channel"
