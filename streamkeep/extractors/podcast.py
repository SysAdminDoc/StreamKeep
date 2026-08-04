"""Podcast RSS extraction with Podcasting 2.0 metadata preservation."""

from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import re
import urllib.parse

from .. import CURL_UA
from ..http import curl
from ..models import QualityInfo, StreamInfo, VODInfo
from .base import Extractor


_ATTR_RE = re.compile(
    r'([\w:.-]+)\s*=\s*"([^"]*)"|([\w:.-]+)\s*=\s*\'([^\']*)\''
)
_ITEM_RE = re.compile(r"<item\b[^>]*>.*?</item\s*>", re.IGNORECASE | re.DOTALL)
_CHANNEL_RE = re.compile(
    r"<channel\b[^>]*>(?P<body>.*?)</channel\s*>",
    re.IGNORECASE | re.DOTALL,
)
_PAGED_FEED_LIMIT = 32
_MAX_PODCAST_LIST_ROWS = 20_000


def _parse_attrs(attr_text):
    attrs = {}
    for match in _ATTR_RE.finditer(attr_text or ""):
        key = (match.group(1) or match.group(3) or "").casefold()
        value = match.group(2) if match.group(2) is not None else match.group(4)
        if key:
            attrs[key] = html.unescape((value or "").strip())
    return attrs


def _strip_cdata(value):
    text = str(value or "").strip()
    if text.startswith("<![CDATA[") and text.endswith("]]>"):
        text = text[9:-3]
    return html.unescape(text).strip()


def _tag_records(xml_text, local_name):
    """Return lightweight namespace-agnostic records for one local tag.

    A few otherwise valid podcast feeds omit namespace declarations in item
    fragments, so this deliberately avoids requiring a full XML document. The
    parser still bounds every list and only treats public HTTP(S) URLs as
    fetchable later in the sidecar layer.
    """
    if not isinstance(xml_text, str):
        return []
    escaped = re.escape(local_name)
    opening = re.compile(
        rf"<(?P<prefix>[A-Za-z_][\w.-]*:)?{escaped}\b"
        rf"(?P<attrs>[^>]*?)(?P<self>/?)>",
        re.IGNORECASE | re.DOTALL,
    )
    records = []
    for match in opening.finditer(xml_text):
        prefix = (match.group("prefix") or "").rstrip(":") or None
        attrs = _parse_attrs(match.group("attrs"))
        if match.group("self").strip() == "/":
            records.append({
                "prefix": prefix,
                "attrs": attrs,
                "body": "",
                "raw": match.group(0),
            })
            continue
        close = re.search(
            rf"</{re.escape(prefix + ':' if prefix else '')}{escaped}\s*>",
            xml_text[match.end():],
            re.IGNORECASE | re.DOTALL,
        )
        if close is None:
            records.append({
                "prefix": prefix,
                "attrs": attrs,
                "body": "",
                "raw": match.group(0),
            })
            continue
        end = match.end() + close.end()
        records.append({
            "prefix": prefix,
            "attrs": attrs,
            "body": xml_text[match.end():match.end() + close.start()],
            "raw": xml_text[match.start():end],
        })
    return records


def _record_text(record):
    body = str(record.get("body", "") or "")
    body = _strip_cdata(body)
    body = re.sub(r"<[^>]+>", "", body)
    return _strip_cdata(body)


def _first_text(xml_text, local_name, *, prefix=None):
    for record in _tag_records(xml_text, local_name):
        if prefix is not None and (record.get("prefix") or "").casefold() != prefix.casefold():
            continue
        text = _record_text(record)
        if text:
            return text
    return ""


def _first_prefixed_text(xml_text, local_name):
    for record in _tag_records(xml_text, local_name):
        if record.get("prefix") and _record_text(record):
            return _record_text(record)
    return ""


def _public_url(value, base_url=""):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        text = urllib.parse.urljoin(base_url or "", text)
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def _finite_float(value, default=0.0):
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _positive_int(value, default=0):
    try:
        parsed = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, parsed)


def _number_value(value):
    text = _strip_cdata(value)
    if not text:
        return ""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    if not math.isfinite(number):
        return ""
    return int(number) if number.is_integer() else number


def _duration_seconds(value):
    text = _strip_cdata(value)
    if not text:
        return 0.0
    parts = text.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return max(0.0, int(hours) * 3600 + int(minutes) * 60 + float(seconds))
        if len(parts) == 2:
            minutes, seconds = parts
            return max(0.0, int(minutes) * 60 + float(seconds))
        return max(0.0, float(text))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _duration_label(seconds):
    total = max(0, int(float(seconds or 0)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s" if total else ""


def _parse_sidecar_links(xml_text, feed_url=""):
    refs = []
    for local_name, kind in (("transcript", "transcript"), ("chapters", "chapters")):
        for record in _tag_records(xml_text, local_name):
            attrs = record["attrs"]
            url = _public_url(attrs.get("url"), feed_url)
            if not url:
                continue
            refs.append({
                "kind": kind,
                "url": url,
                "type": attrs.get("type", ""),
                "language": attrs.get("language", attrs.get("lang", "")),
                "rel": attrs.get("rel", ""),
            })
    seen = set()
    unique = []
    for ref in refs:
        key = (ref["kind"], ref["url"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


def _parse_people(xml_text, feed_url=""):
    people = []
    for record in _tag_records(xml_text, "person")[:256]:
        name = _record_text(record)
        if not name:
            continue
        attrs = record["attrs"]
        people.append({
            "name": name,
            "role": attrs.get("role", ""),
            "group": attrs.get("group", ""),
            "img": _public_url(attrs.get("img"), feed_url),
            "href": _public_url(attrs.get("href"), feed_url),
        })
    return people


def _parse_value_tags(xml_text, feed_url=""):
    values = []
    for record in _tag_records(xml_text, "value")[:64]:
        attrs = record["attrs"]
        recipients = []
        for child in _tag_records(record.get("body", ""), "valueRecipient")[:256]:
            child_attrs = child["attrs"]
            recipients.append({
                key: value for key, value in {
                    "name": _record_text(child) or child_attrs.get("name", ""),
                    "address": child_attrs.get("address", ""),
                    "type": child_attrs.get("type", ""),
                    "split": child_attrs.get("split", ""),
                    "fee": child_attrs.get("fee", ""),
                    "custom_key": child_attrs.get("customkey", ""),
                    "custom_value": child_attrs.get("customvalue", ""),
                }.items() if value != ""
            })
        # raw_xml is intentionally retained. StreamKeep records this public
        # declaration but never evaluates, pays, or follows it.
        values.append({
            "type": attrs.get("type", ""),
            "method": attrs.get("method", ""),
            "suggested": attrs.get("suggested", ""),
            "recipients": recipients,
            "raw_xml": str(record.get("raw", "")),
        })
    return values


def _parse_alternate_enclosures(xml_text, feed_url=""):
    rows = []
    for record in _tag_records(xml_text, "alternateEnclosure")[:256]:
        attrs = record["attrs"]
        sources = []
        for source in _tag_records(record.get("body", ""), "source")[:64]:
            source_attrs = source["attrs"]
            uri = _public_url(source_attrs.get("uri"), feed_url)
            if uri:
                sources.append({
                    "uri": uri,
                    "content_type": source_attrs.get("contenttype", ""),
                })
        integrity = None
        integrity_rows = _tag_records(record.get("body", ""), "integrity")
        if integrity_rows:
            integrity_record = integrity_rows[0]
            integrity_attrs = integrity_record["attrs"]
            integrity = {
                "type": integrity_attrs.get("type", ""),
                "value": integrity_attrs.get("value", "") or _record_text(integrity_record),
            }
        if not sources:
            continue
        row = {
            "type": attrs.get("type", ""),
            "length": _positive_int(attrs.get("length")),
            "bitrate": _positive_int(attrs.get("bitrate")),
            "height": _positive_int(attrs.get("height")),
            "lang": attrs.get("lang", ""),
            "title": _strip_cdata(attrs.get("title", "")),
            "rel": attrs.get("rel", ""),
            "codecs": attrs.get("codecs", ""),
            "default": attrs.get("default", "").casefold() in {"1", "true", "yes"},
            "sources": sources,
        }
        if integrity is not None:
            row["integrity"] = integrity
        rows.append(row)
    return rows


def _parse_images(xml_text, feed_url=""):
    images = []
    for record in _tag_records(xml_text, "image"):
        attrs = record["attrs"]
        href = attrs.get("href") or attrs.get("url")
        # The unprefixed RSS <image> container has a nested <url>, while
        # podcast:image and itunes:image carry href/url attributes.
        if not href and record.get("prefix"):
            href = _record_text(record)
        href = _public_url(href, feed_url)
        if not href:
            continue
        images.append({
            "href": href,
            "alt": attrs.get("alt", ""),
            "aspect_ratio": attrs.get("aspect-ratio", ""),
            "width": _positive_int(attrs.get("width")),
            "height": _positive_int(attrs.get("height")),
            "type": attrs.get("type", ""),
            "purpose": attrs.get("purpose", ""),
        })
    return images


def _parse_scope_metadata(xml_text, feed_url=""):
    metadata = {}
    podcast_guid = _first_prefixed_text(xml_text, "guid")
    if podcast_guid:
        metadata["podcast_guid"] = podcast_guid
    episode_guid = ""
    for record in _tag_records(xml_text, "guid"):
        if not record.get("prefix"):
            episode_guid = _record_text(record)
            if episode_guid:
                break
    if episode_guid:
        metadata["guid"] = episode_guid

    season_rows = _tag_records(xml_text, "season")
    if season_rows:
        season = season_rows[0]
        number = _number_value(_record_text(season))
        if number != "":
            metadata["season"] = {
                "number": number,
                "name": _strip_cdata(season["attrs"].get("name", "")),
            }
    episode_rows = _tag_records(xml_text, "episode")
    if episode_rows:
        episode = episode_rows[0]
        number = _number_value(_record_text(episode))
        if number != "":
            metadata["episode"] = {
                "number": number,
                "display": _strip_cdata(episode["attrs"].get("display", "")),
            }

    medium = _first_text(xml_text, "medium")
    if medium:
        metadata["medium"] = medium
    people = _parse_people(xml_text, feed_url)
    if people:
        metadata["person"] = people

    soundbites = []
    for record in _tag_records(xml_text, "soundbite")[:256]:
        attrs = record["attrs"]
        if "starttime" not in attrs or "duration" not in attrs:
            continue
        soundbites.append({
            "title": _record_text(record),
            "start_time": _finite_float(attrs.get("starttime")),
            "duration": _finite_float(attrs.get("duration")),
        })
    if soundbites:
        metadata["soundbite"] = soundbites

    funding = []
    for record in _tag_records(xml_text, "funding")[:256]:
        url = _public_url(record["attrs"].get("url"), feed_url)
        if url:
            funding.append({"text": _record_text(record), "url": url})
    if funding:
        metadata["funding"] = funding

    license_rows = _tag_records(xml_text, "license")
    if license_rows:
        license_record = license_rows[0]
        metadata["license"] = {
            "name": _record_text(license_record),
            "url": _public_url(license_record["attrs"].get("url"), feed_url),
        }

    locations = []
    for record in _tag_records(xml_text, "location")[:256]:
        name = _record_text(record)
        if not name:
            continue
        attrs = record["attrs"]
        locations.append({
            "name": name,
            "rel": attrs.get("rel", ""),
            "geo": attrs.get("geo", ""),
            "osm": attrs.get("osm", ""),
            "country": attrs.get("country", ""),
        })
    if locations:
        metadata["location"] = locations

    txt_rows = []
    for record in _tag_records(xml_text, "txt")[:256]:
        value = _record_text(record)
        if value:
            txt_rows.append({
                "value": value,
                "purpose": record["attrs"].get("purpose", ""),
            })
    if txt_rows:
        metadata["txt"] = txt_rows

    values = _parse_value_tags(xml_text, feed_url)
    if values:
        metadata["value"] = values
    alternates = _parse_alternate_enclosures(xml_text, feed_url)
    if alternates:
        metadata["alternate_enclosures"] = alternates
    images = _parse_images(xml_text, feed_url)
    if images:
        metadata["artwork"] = images
    sidecars = _parse_sidecar_links(xml_text, feed_url)
    if sidecars:
        metadata["sidecars"] = sidecars
    return metadata


def _merge_metadata(channel_metadata, item_metadata):
    merged = copy.deepcopy(channel_metadata or {})
    for key, value in (item_metadata or {}).items():
        if value not in (None, "", [], {}):
            merged[key] = copy.deepcopy(value)
    return merged


def _feed_parts(feed_body):
    if not isinstance(feed_body, str):
        return "", "", []
    channel_match = _CHANNEL_RE.search(feed_body)
    if channel_match:
        channel_inner = channel_match.group("body")
    else:
        channel_inner = feed_body
    items = [match.group(0) for match in _ITEM_RE.finditer(channel_inner)]
    channel_scope = _ITEM_RE.sub("", channel_inner)
    return channel_inner, channel_scope, items


def _next_page_url(channel_scope, current_url):
    for record in _tag_records(channel_scope, "link"):
        attrs = record["attrs"]
        if attrs.get("rel", "").casefold() != "next":
            continue
        candidate = _public_url(attrs.get("href") or attrs.get("url"), current_url)
        if candidate and candidate != current_url:
            return candidate
    return ""


def _enclosure(item_xml, feed_url=""):
    rows = _tag_records(item_xml, "enclosure")
    if not rows:
        return None
    attrs = rows[0]["attrs"]
    url = _public_url(attrs.get("url"), feed_url)
    if not url:
        return None
    return {
        "url": url,
        "type": attrs.get("type", ""),
        "length": _positive_int(attrs.get("length")),
        "bitrate": _positive_int(attrs.get("bitrate")),
    }


def parse_podcast_episode_metadata(item_xml, channel_xml="", feed_url=""):
    """Parse one RSS item into bounded Podcasting 2.0 metadata."""
    channel_metadata = _parse_scope_metadata(channel_xml, feed_url)
    item_metadata = _parse_scope_metadata(item_xml, feed_url)
    if item_metadata.get("podcast_guid") and not item_metadata.get("guid"):
        item_metadata["guid"] = item_metadata["podcast_guid"]
    return _merge_metadata(channel_metadata, item_metadata)


def parse_podcast_feed(feed_body, feed_url=""):
    """Parse one feed page without fetching it.

    The result deliberately keeps the next-page URL separate from episode
    rows, allowing callers such as the monitor to follow RFC 5005 ``rel=next``
    links without treating a page URL as an episode identity.
    """
    _channel_inner, channel_scope, item_xmls = _feed_parts(feed_body)
    channel_metadata = _parse_scope_metadata(channel_scope, feed_url)
    channel_title = _first_text(channel_scope, "title") or ""
    items = []
    for item_xml in item_xmls:
        enclosure = _enclosure(item_xml, feed_url)
        if enclosure is None:
            continue
        item_metadata = _parse_scope_metadata(item_xml, feed_url)
        if item_metadata.get("podcast_guid") and not item_metadata.get("guid"):
            item_metadata["guid"] = item_metadata["podcast_guid"]
        metadata = _merge_metadata(channel_metadata, item_metadata)
        title = _first_text(item_xml, "title") or "Untitled"
        date = _first_text(item_xml, "pubDate") or _first_text(item_xml, "date")
        duration_seconds = _duration_seconds(_first_text(item_xml, "duration"))
        if not duration_seconds:
            duration_seconds = _duration_seconds(_first_text(item_xml, "duration", prefix="itunes"))
        if not duration_seconds:
            duration_seconds = _duration_seconds(_first_text(item_xml, "duration"))
        # itunes:duration is not a Podcasting 2.0 tag and can be found with a
        # local-name lookup regardless of whether the namespace is declared.
        if not duration_seconds:
            for duration_record in _tag_records(item_xml, "duration"):
                duration_seconds = _duration_seconds(_record_text(duration_record))
                if duration_seconds:
                    break
        duration_text = ""
        for duration_record in _tag_records(item_xml, "duration"):
            duration_text = _record_text(duration_record)
            if duration_text:
                break
        duration_label = _duration_label(duration_seconds)
        if re.fullmatch(r"\d+(?:\.\d+)?", duration_text.strip()):
            duration_label = f"{int(duration_seconds)}s"
        rss_guid = metadata.get("guid", "")
        items.append({
            "title": title,
            "date": date,
            "duration_seconds": duration_seconds,
            "duration": duration_label,
            "enclosure": enclosure,
            "guid": rss_guid,
            "metadata": metadata,
            "artwork_url": (
                (metadata.get("artwork") or [{}])[0].get("href", "")
                if isinstance(metadata.get("artwork"), list) else ""
            ),
            "raw_xml": item_xml,
        })
    return {
        "channel_title": channel_title,
        "channel_metadata": channel_metadata,
        "items": items[:_MAX_PODCAST_LIST_ROWS],
        "next_url": _next_page_url(channel_scope, feed_url),
    }


def find_podcast_episode(feed_body, enclosure_url, feed_url=""):
    """Return parsed metadata for the item matching an enclosure URL."""
    parsed = parse_podcast_feed(feed_body, feed_url=feed_url)
    target = str(enclosure_url or "").strip()
    target_path = urllib.parse.urlsplit(target).path
    fallback = None
    for item in parsed["items"]:
        current = item["enclosure"]["url"]
        if current == target:
            return item
        if fallback is None and urllib.parse.urlsplit(current).path == target_path:
            fallback = item
    return fallback


def parse_podcast_chapters_json(text):
    """Parse a Podcast Namespace ``application/json+chapters`` document."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    raw = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    parsed = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        start = _finite_float(entry.get("startTime"), -1.0)
        if start < 0:
            continue
        end = None
        if entry.get("endTime") is not None:
            candidate = _finite_float(entry.get("endTime"), -1.0)
            end = candidate if candidate >= start else None
        parsed.append({
            "title": _strip_cdata(entry.get("title", "")),
            "start": start,
            "end": end,
            "img": _strip_cdata(entry.get("img", "")),
            "url": _strip_cdata(entry.get("url", "")),
            "toc": entry.get("toc", True),
        })
    parsed.sort(key=lambda chapter: chapter["start"])
    filtered = [chapter for chapter in parsed if chapter.get("toc", True) is not False]
    for chapter in filtered:
        chapter.pop("toc", None)
    for index, chapter in enumerate(filtered):
        if chapter["end"] is None:
            chapter["end"] = (
                filtered[index + 1]["start"] if index + 1 < len(filtered) else 0.0
            )
    return filtered


class PodcastRSSExtractor(Extractor):
    NAME = "Podcast"
    ICON = "P"
    COLOR = "yellow"
    URL_PATTERNS = [
        re.compile(r"(?:https?://).+\.(rss|xml)(\?.*)?$"),
        re.compile(r"(?:https?://).+/feed/?(\?.*)?$"),
        re.compile(r"(?:https?://).+/rss/?(\?.*)?$"),
    ]

    def extract_channel_id(self, url):
        try:
            parsed = urllib.parse.urlparse(url.strip())
            return parsed.netloc.replace(".", "_")
        except Exception:
            return "podcast"

    def supports_vod_listing(self):
        return True

    def list_vods(self, url, log_fn=None, cursor=None):
        origin_url = str(url or "").strip()
        current_url = str(cursor or origin_url).strip()
        if not current_url:
            return [], None
        self._log(log_fn, f"Fetching podcast RSS: {current_url}")
        vods = []
        seen_pages = set()
        seen_identities = set()
        inherited_metadata = {}
        inherited_channel = ""
        for _page in range(_PAGED_FEED_LIMIT):
            if not current_url or current_url in seen_pages:
                break
            seen_pages.add(current_url)
            body = curl(
                current_url,
                headers={
                    "User-Agent": CURL_UA,
                    "Accept": "application/rss+xml, application/xml, text/xml",
                },
            )
            if not body:
                self._log(log_fn, f"Failed to fetch RSS feed page: {current_url}")
                break
            parsed = parse_podcast_feed(body, feed_url=current_url)
            if parsed.get("channel_metadata"):
                inherited_metadata = _merge_metadata(
                    inherited_metadata, parsed["channel_metadata"]
                )
            inherited_channel = inherited_channel or parsed.get("channel_title", "")
            for row in parsed["items"]:
                enclosure = row["enclosure"]
                metadata = _merge_metadata(inherited_metadata, row["metadata"])
                identity_seed = str(
                    row.get("guid") or enclosure.get("url") or ""
                ).strip()
                if not identity_seed:
                    continue
                identity_key = identity_seed.casefold()
                if identity_key in seen_identities:
                    continue
                seen_identities.add(identity_key)
                media_type = str(metadata.get("medium", "") or "").casefold()
                media_type = "video" if media_type in {"video", "film"} else "audio"
                vod = VODInfo(
                    title=row["title"],
                    date=row["date"],
                    source=enclosure["url"],
                    is_live=False,
                    viewers=0,
                    duration=row["duration"],
                    duration_ms=int(row["duration_seconds"] * 1000),
                    platform="Podcast",
                    channel=inherited_channel,
                    feed_url=origin_url,
                    source_id="episode:" + hashlib.sha256(
                        identity_seed.encode("utf-8", errors="replace")
                    ).hexdigest(),
                    webpage_url=origin_url,
                    media_type=media_type,
                    thumbnail_url=row.get("artwork_url", "") or "",
                    podcast_metadata=metadata,
                )
                vods.append(self._canonicalize_vod_info(vod))
                if len(vods) >= _MAX_PODCAST_LIST_ROWS:
                    break
            if len(vods) >= _MAX_PODCAST_LIST_ROWS:
                break
            next_url = parsed.get("next_url", "")
            if not next_url or next_url in seen_pages:
                break
            current_url = next_url
            self._log(log_fn, f"Following podcast RSS next page: {current_url}")
        else:
            self._log(log_fn, f"Stopped after {_PAGED_FEED_LIMIT} podcast RSS pages")

        self._log(log_fn, f"Found {len(vods)} episode(s)")
        return vods, None

    def resolve(self, url, log_fn=None):
        if any(url.endswith(ext) for ext in (".mp3", ".m4a", ".ogg", ".wav", ".aac")):
            info = StreamInfo(
                platform="Podcast",
                url=url,
                title=url.split("/")[-1],
                channel=self.extract_channel_id(url) or "",
                source_id="episode:" + hashlib.sha256(
                    url.encode("utf-8", errors="replace")
                ).hexdigest(),
                webpage_url=url,
            )
            info.qualities.append(QualityInfo(
                name="audio", url=url, resolution="audio", format_type="mp4",
            ))
            return self._canonicalize_stream_info(info, source_url=url)
        return None
