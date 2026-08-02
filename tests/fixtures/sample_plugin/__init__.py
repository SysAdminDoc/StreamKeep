"""Sample StreamKeep adapter plugin.

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


class SamplePostProcessor:
    """Minimal post-process adapter used by the contract test."""

    def process(self, file_path, context=None):
        if context is not None:
            context.require("filesystem_read")
        return {"processed": True, "file_path": str(file_path)}


class SampleUploader:
    """Minimal upload adapter used by the contract test."""

    def upload(self, file_path, metadata=None, progress_cb=None, context=None):
        if context is not None:
            context.require("network")
        if progress_cb is not None:
            progress_cb(1.0)
        return {"uploaded": True, "file_path": str(file_path), "metadata": metadata or {}}


class SampleYoutubeBackend:
    """Deterministic remote-cipher/token backend used by the contract tests."""

    def health(self, request, context=None):
        if context is not None:
            context.require("network")
        return {
            "reachable": bool(request.get("backend_url")),
            "provider": "sample",
            "capabilities": ["cipher", "po-token"],
            "detail": "Sample backend is reachable.",
        }

    def solve(self, request, context=None):
        if context is not None:
            context.require("network")
        if "youtube.com" not in str(request.get("url", "")):
            return {"extractor_args": []}
        return {
            "extractor_args": [
                "--extractor-args",
                "youtube:po_token=sample-contract-token",
            ],
            "provider": "sample",
        }
