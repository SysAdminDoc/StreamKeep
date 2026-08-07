"""Extractor base class + auto-registering subclass hook."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class Extractor:
    """Abstract base. Subclasses auto-register via __init_subclass__."""

    NAME = ""
    ICON = ""
    COLOR = ""
    URL_PATTERNS = []
    _registry = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.NAME:
            Extractor._registry.append(cls)

    @classmethod
    def detect(cls, url: str) -> Extractor | None:
        """Return an instance of the matching extractor, or None."""
        if not url or not isinstance(url, str):
            return None
        url = url.strip()
        if not url:
            return None
        fallback_classes = []
        for ext_cls in cls._registry:
            # Declarative definitions are intended to cover new sites while
            # keeping native extractors authoritative. The yt-dlp catch-all
            # therefore runs only after the data-only registry has had a turn.
            if getattr(ext_cls, "NAME", "") == "yt-dlp":
                fallback_classes.append(ext_cls)
                continue
            for pattern in ext_cls.URL_PATTERNS:
                try:
                    if pattern.match(url):
                        return ext_cls()
                except Exception:
                    continue
        try:
            from ..declarative import (
                detect_declarative_extractor,
                report_adapter_load_errors,
            )
            # A definition that fails to load used to fall through here in
            # silence, so a typo looked identical to "this site has no
            # adapter". Reported once per registry change, not per keystroke.
            report_adapter_load_errors()
            declarative = detect_declarative_extractor(url)
            if declarative is not None:
                return declarative
        except Exception as error:
            # A malformed optional YAML definition must not disable the
            # built-in extractor registry or turn URL detection into a crash.
            logger.warning(
                "[ADAPTERS] Declarative detection failed for this URL: %s", error,
            )
        for ext_cls in fallback_classes:
            for pattern in ext_cls.URL_PATTERNS:
                try:
                    if pattern.match(url):
                        return ext_cls()
                except Exception:
                    continue
        return None

    @classmethod
    def all_names(cls) -> list[str]:
        names = [e.NAME for e in cls._registry]
        try:
            from ..declarative import declarative_adapter_names
            names.extend(declarative_adapter_names())
        except Exception:
            # Adapter diagnostics are optional; preserve the native names if
            # a malformed or unavailable YAML registry cannot be inspected.
            pass
        return names

    def resolve(self, url: str, log_fn: Callable[[str], Any] | None = None) -> Any:
        """Resolve a URL to a StreamInfo with qualities.
        Returns StreamInfo or None."""
        raise NotImplementedError

    def list_vods(self, url: str, log_fn: Callable[[str], Any] | None = None, cursor: str | None = None) -> tuple[list[Any], str | None]:
        """List available VODs for a channel.

        Returns ``(list[VODInfo], next_cursor)`` where *next_cursor* is
        an opaque value to pass back for the next page, or ``None`` when
        there are no more results.  Legacy callers that only check for a
        list still work because the tuple is truthy when non-empty.
        """
        return [], None

    def is_direct_url(self, url: str) -> bool:
        """True when *url* points at a single resolvable item (e.g. a VOD
        permalink) that should be resolved directly rather than treated as
        a channel for live-check / VOD-listing."""
        return False

    def supports_vod_listing(self) -> bool:
        return False

    def supports_live_check(self) -> bool:
        return False

    def check_live(self, url: str) -> bool | None:
        """Check if channel is live. Returns bool or None."""
        return None

    def extract_channel_id(self, url: str) -> str | None:
        """Extract channel name/slug for folder naming."""
        return None

    def _log(self, log_fn: Callable[[str], Any] | None, msg: str) -> None:
        if log_fn:
            log_fn(msg)
        logger.debug(msg)

    @staticmethod
    def _canonicalize_stream_info(info, *, source_url=""):
        """Attach the shared, credential-free identity to a resolved stream."""
        if info is None:
            return info
        from ..metadata import build_archival_provenance

        provenance = build_archival_provenance(info, source_url=source_url)
        if provenance.platform:
            info.platform = provenance.platform
        info.source_id = provenance.source_id
        info.webpage_url = provenance.webpage_url
        return info

    @staticmethod
    def _canonicalize_vod_info(info, *, source_url=""):
        """Attach the shared, credential-free identity to a listed VOD."""
        if info is None:
            return info
        from ..metadata import build_archival_provenance

        provenance = build_archival_provenance(vod_info=info, source_url=source_url)
        if provenance.platform:
            info.platform = provenance.platform
        info.source_id = provenance.source_id
        info.webpage_url = provenance.webpage_url
        return info
