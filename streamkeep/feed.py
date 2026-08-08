"""RSS Feed Generator — podcast-compatible feeds for recordings (F70).

Serves RSS 2.0 feeds via the local web server with ``<enclosure>`` tags
pointing to ``/media/{id}`` URLs. Compatible with Pocket Casts, AntennaPod,
and other podcast apps.

Feeds:
  /feed/all.xml       — all shared recordings
  /feed/{channel}.xml — per-channel feed
"""

import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape
from urllib.parse import urlsplit

from .metadata import load_metadata_sidecar
from .utils import sanitize_xml_text


_MIME_OVERRIDES = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
}


def _escape_xml_attribute(value):
    """Escape a value used inside a double-quoted XML attribute."""
    return escape(
        sanitize_xml_text(value), {'"': "&quot;", "'": "&apos;"}
    )


def _escape_xml_text(value):
    """Escape sanitized XML element text."""
    return escape(sanitize_xml_text(value))


def _podcast_metadata_for_entry(entry):
    metadata = entry.get("podcast") or entry.get("podcast_metadata")
    if isinstance(metadata, dict):
        return metadata
    media_path = str(entry.get("media_path", "") or "")
    if not media_path:
        return {}
    return load_metadata_sidecar(Path(media_path).parent).get("podcast", {})


def _podcast_item_xml(metadata):
    """Render safe, non-payment Podcasting 2.0 item declarations."""
    if not isinstance(metadata, dict):
        return ""
    lines = []
    season = metadata.get("season")
    if isinstance(season, dict) and season.get("number") not in (None, ""):
        name = _escape_xml_attribute(season.get("name", ""))
        attr = f' name="{name}"' if name else ""
        lines.append(
            f"      <podcast:season{attr}>{_escape_xml_text(season['number'])}</podcast:season>"
        )
    episode = metadata.get("episode")
    if isinstance(episode, dict) and episode.get("number") not in (None, ""):
        display = _escape_xml_attribute(episode.get("display", ""))
        attr = f' display="{display}"' if display else ""
        lines.append(
            f"      <podcast:episode{attr}>{_escape_xml_text(episode['number'])}</podcast:episode>"
        )
    medium = str(metadata.get("medium", "") or "")
    if medium:
        lines.append(f"      <podcast:medium>{_escape_xml_text(medium)}</podcast:medium>")
    for person in metadata.get("person", []) if isinstance(metadata.get("person", []), list) else []:
        if not isinstance(person, dict) or not person.get("name"):
            continue
        attrs = []
        for key in ("role", "group", "img", "href"):
            if person.get(key):
                attrs.append(f'{key}="{_escape_xml_attribute(person[key])}"')
        attr_text = f" {' '.join(attrs)}" if attrs else ""
        lines.append(
            f"      <podcast:person{attr_text}>{_escape_xml_text(person['name'])}</podcast:person>"
        )
    for soundbite in metadata.get("soundbite", []) if isinstance(metadata.get("soundbite", []), list) else []:
        if not isinstance(soundbite, dict):
            continue
        try:
            start = float(soundbite.get("start_time", 0) or 0)
            duration = float(soundbite.get("duration", 0) or 0)
        except (TypeError, ValueError):
            continue
        lines.append(
            f'      <podcast:soundbite startTime="{start:g}" duration="{duration:g}">'
            f"{_escape_xml_text(soundbite.get('title', '') or '')}</podcast:soundbite>"
        )
    for funding in metadata.get("funding", []) if isinstance(metadata.get("funding", []), list) else []:
        if not isinstance(funding, dict) or not funding.get("url"):
            continue
        lines.append(
            f'      <podcast:funding url="{_escape_xml_attribute(funding["url"])}">'
            f"{_escape_xml_text(funding.get('text', '') or '')}</podcast:funding>"
        )
    license_row = metadata.get("license")
    if isinstance(license_row, dict) and license_row.get("name"):
        url = _escape_xml_attribute(license_row.get("url", ""))
        attr = f' url="{url}"' if url else ""
        lines.append(
            f"      <podcast:license{attr}>{_escape_xml_text(license_row['name'])}</podcast:license>"
        )
    for location in metadata.get("location", []) if isinstance(metadata.get("location", []), list) else []:
        if not isinstance(location, dict) or not location.get("name"):
            continue
        attrs = []
        for key in ("rel", "geo", "osm", "country"):
            if location.get(key):
                attrs.append(f'{key}="{_escape_xml_attribute(location[key])}"')
        attr_text = f" {' '.join(attrs)}" if attrs else ""
        lines.append(
            f"      <podcast:location{attr_text}>{_escape_xml_text(location['name'])}</podcast:location>"
        )
    for txt in metadata.get("txt", []) if isinstance(metadata.get("txt", []), list) else []:
        if not isinstance(txt, dict) or not txt.get("value"):
            continue
        purpose = _escape_xml_attribute(txt.get("purpose", ""))
        attr = f' purpose="{purpose}"' if purpose else ""
        lines.append(
            f"      <podcast:txt{attr}>{_escape_xml_text(txt['value'])}</podcast:txt>"
        )
    for alternate in (
        metadata.get("alternate_enclosures", [])
        if isinstance(metadata.get("alternate_enclosures", []), list) else []
    ):
        if not isinstance(alternate, dict):
            continue
        attrs = []
        for key, xml_key in (
            ("type", "type"), ("length", "length"), ("bitrate", "bitrate"),
            ("height", "height"), ("lang", "lang"), ("title", "title"),
            ("rel", "rel"), ("codecs", "codecs"), ("default", "default"),
        ):
            value = alternate.get(key)
            if value not in (None, "", 0, False):
                attrs.append(f'{xml_key}="{_escape_xml_attribute(value)}"')
        source_lines = []
        for source in alternate.get("sources", []) if isinstance(
            alternate.get("sources", []), list
        ) else []:
            if not isinstance(source, dict) or not source.get("uri"):
                continue
            source_attrs = [
                f'uri="{_escape_xml_attribute(source["uri"])}"',
            ]
            if source.get("content_type"):
                source_attrs.append(
                    "contentType=\""
                    + _escape_xml_attribute(source["content_type"])
                    + "\""
                )
            source_lines.append(
                f"        <podcast:source {' '.join(source_attrs)}/>"
            )
        if not source_lines:
            continue
        integrity = alternate.get("integrity")
        integrity_line = ""
        if isinstance(integrity, dict) and integrity.get("value"):
            integrity_line = (
                "\n        <podcast:integrity"
                f' type="{_escape_xml_attribute(integrity.get("type", ""))}"'
                f' value="{_escape_xml_attribute(integrity["value"])}"/>'
            )
        attr_text = f" {' '.join(attrs)}" if attrs else ""
        lines.append(
            f"      <podcast:alternateEnclosure{attr_text}>\n"
            + "\n".join(source_lines)
            + integrity_line
            + "\n      </podcast:alternateEnclosure>"
        )
    for image in metadata.get("artwork", []) if isinstance(metadata.get("artwork", []), list) else []:
        if not isinstance(image, dict) or not image.get("href"):
            continue
        attrs = [f'href="{_escape_xml_attribute(image["href"])}"']
        for key, xml_key in (("alt", "alt"), ("aspect_ratio", "aspect-ratio"), ("width", "width"), ("height", "height"), ("type", "type"), ("purpose", "purpose")):
            if image.get(key) not in (None, "", 0):
                attrs.append(f'{xml_key}="{_escape_xml_attribute(image[key])}"')
        lines.append(f"      <podcast:image {' '.join(attrs)}/>")
    # podcast:value is intentionally not rendered into the local publication:
    # StreamKeep stores it as declaration data and never activates payments.
    return "\n" + "\n".join(lines) if lines else ""


def generate_rss(entries, base_url, *, title="StreamKeep", channel=None,
                 limit=100):
    """Build RSS 2.0 XML from history entries.

    *entries* is a list of dicts with keys: share_id, title, channel,
    date, path, media_path, duration_secs.

    *base_url* is e.g. ``http://192.168.1.100:8080``.

    Returns XML string.
    """
    base_url = str(base_url or "").strip().rstrip("/")
    try:
        parsed_base = urlsplit(base_url)
    except ValueError:
        parsed_base = None
    if (
        parsed_base is None
        or parsed_base.scheme not in {"http", "https"}
        or not parsed_base.netloc
        or parsed_base.username
        or parsed_base.password
        or parsed_base.query
        or parsed_base.fragment
    ):
        raise ValueError("RSS base_url must use HTTP or HTTPS")
    if any(char.isspace() or ord(char) < 32 for char in base_url):
        raise ValueError("RSS base_url contains invalid characters")
    feed_title = f"{title} - {channel}" if channel else title
    feed_desc = f"Recordings from {channel}" if channel else "All StreamKeep recordings"
    try:
        limit = max(1, min(1000, int(limit or 100)))
    except (TypeError, ValueError):
        limit = 100

    # Filter by channel if specified
    if channel:
        entries = [e for e in entries if (e.get("channel", "") or "").lower() == channel.lower()]

    # Limit to most recent
    entries = list(entries or [])[-limit:]

    entry_metadata = [_podcast_metadata_for_entry(e) for e in entries]
    podcast_guids = {
        str(meta.get("podcast_guid", "") or "")
        for meta in entry_metadata if meta.get("podcast_guid")
    }
    podcast_mediums = {
        str(meta.get("medium", "") or "")
        for meta in entry_metadata if meta.get("medium")
    }

    items_xml = ""
    for e in reversed(entries):
        podcast_metadata = _podcast_metadata_for_entry(e)
        sid = str(e.get("share_id", "") or "").strip()
        if not sid:
            continue
        etitle = _escape_xml_text(e.get("title", "Untitled") or "Untitled")
        echannel = _escape_xml_text(e.get("channel", "") or "")
        edate = str(e.get("date", "") or "")
        media_url = f"{base_url}/media/{sid}" if sid else ""
        try:
            duration = max(0, int(e.get("duration_secs", 0) or 0))
        except (TypeError, ValueError):
            duration = 0

        # RFC 822 date
        pub_date = ""
        try:
            dt = datetime.strptime(edate[:16], "%Y-%m-%d %H:%M")
            pub_date = dt.replace(tzinfo=timezone.utc).strftime(
                "%a, %d %b %Y %H:%M:%S +0000"
            )
        except (ValueError, TypeError):
            pass

        # File size estimate
        media_path = e.get("media_path", "")
        file_size = 0
        if media_path and os.path.isfile(media_path):
            try:
                file_size = os.path.getsize(media_path)
            except OSError:
                pass
        extension = os.path.splitext(str(media_path or ""))[1].lower()
        media_type = (
            _MIME_OVERRIDES.get(extension)
            or mimetypes.guess_type(str(media_path or ""))[0]
            or "application/octet-stream"
        )

        # Duration in HH:MM:SS for itunes:duration
        dur_str = ""
        if duration > 0:
            h = duration // 3600
            m = (duration % 3600) // 60
            s = duration % 60
            dur_str = f"{h}:{m:02d}:{s:02d}"

        items_xml += f"""    <item>
      <title>{etitle}</title>
      <description>{echannel}</description>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">{_escape_xml_text(sid)}</guid>"""

        items_xml += _podcast_item_xml(podcast_metadata)

        if media_url:
            items_xml += f"""
      <enclosure url="{_escape_xml_attribute(media_url)}" length="{file_size}" type="{_escape_xml_attribute(media_type)}"/>"""

        if dur_str:
            items_xml += f"""
      <itunes:duration>{dur_str}</itunes:duration>"""

        items_xml += """
    </item>
"""

    channel_podcast = ""
    if len(podcast_guids) == 1:
        channel_podcast += (
            f"\n    <podcast:guid>{_escape_xml_text(next(iter(podcast_guids)))}</podcast:guid>"
        )
    if len(podcast_mediums) == 1:
        channel_podcast += (
            f"\n    <podcast:medium>{_escape_xml_text(next(iter(podcast_mediums)))}</podcast:medium>"
        )
    podcast_locked = {
        str(meta.get("locked", "") or "").casefold()
        for meta in entry_metadata if meta.get("locked")
    }
    if len(podcast_locked) == 1 and next(iter(podcast_locked)) in {"yes", "no"}:
        owners = {
            str(meta.get("locked_owner", "") or "")
            for meta in entry_metadata if meta.get("locked_owner")
        }
        owner_attr = (
            f' owner="{_escape_xml_attribute(next(iter(owners)))}"'
            if len(owners) == 1 else ""
        )
        channel_podcast += (
            f"\n    <podcast:locked{owner_attr}>"
            f"{next(iter(podcast_locked))}</podcast:locked>"
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>{_escape_xml_text(feed_title)}</title>
    <description>{_escape_xml_text(feed_desc)}</description>
    <link>{_escape_xml_text(base_url)}</link>
    <generator>StreamKeep</generator>
    <lastBuildDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>
{channel_podcast}
{items_xml}  </channel>
</rss>"""


def channel_list(entries):
    """Return sorted list of unique channel names from entries."""
    channels = set()
    for e in entries:
        ch = e.get("channel", "")
        if ch:
            channels.add(ch)
    return sorted(channels)
