"""OPML import/export for podcast and monitor subscriptions.

Exports monitor entries and podcast feeds to OPML 2.0 outlines.
Imports nested OPML outlines into monitor entries, with duplicate
and invalid-feed reporting.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from xml.sax.saxutils import escape as _xml_escape


def export_opml(entries, *, title="StreamKeep Subscriptions"):
    """Export a list of monitor-entry dicts to an OPML 2.0 XML string.

    Each entry should have at least ``url`` and optionally ``platform``,
    ``channel_id``, ``auto_record``, ``subscribe_vods``.

    Only entries whose URL starts with ``http`` are included.
    RSS-capable entries (podcast, feed, rss, xml URLs) get
    ``type="rss"``; others get ``type="link"``.
    """
    now_rfc822 = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "<head>",
        f"  <title>{_xml_escape(title)}</title>",
        f"  <dateCreated>{now_rfc822}</dateCreated>",
        "</head>",
        "<body>",
    ]

    by_platform = {}
    for e in entries:
        url = str(e.get("url", "") or "").strip()
        if not url.startswith("http"):
            continue
        platform = str(e.get("platform", "") or "").strip() or "Other"
        by_platform.setdefault(platform, []).append(e)

    for platform in sorted(by_platform):
        lines.append(f'  <outline text="{_xml_escape(platform)}">')
        for e in by_platform[platform]:
            url = str(e.get("url", "")).strip()
            channel = str(e.get("channel_id", "") or "").strip()
            text = channel or url
            outline_type = "rss" if _is_rss_capable(url) else "link"
            attrs = (
                f'text="{_xml_escape(text)}" '
                f'type="{outline_type}" '
                f'xmlUrl="{_xml_escape(url)}" '
                f'htmlUrl="{_xml_escape(url)}"'
            )
            lines.append(f"    <outline {attrs}/>")
        lines.append("  </outline>")

    lines.append("</body>")
    lines.append("</opml>")
    return "\n".join(lines)


_RSS_URL_RE = re.compile(r"\.(rss|xml|atom)(\?|$)|/feed/?(\?|$)|/rss/?(\?|$)", re.I)


def _is_rss_capable(url):
    return bool(_RSS_URL_RE.search(url))


def import_opml(xml_text, *, existing_urls=None):
    """Parse an OPML XML string and return importable entries.

    Returns ``(entries, report)`` where:
    - ``entries`` is a list of dicts with ``url``, ``platform`` (from
      the parent outline text, or "Imported"), and ``channel_id``.
    - ``report`` is a dict with ``total``, ``imported``, ``duplicates``,
      ``invalid``, and ``errors`` (list of strings).
    """
    existing = set(existing_urls or ())
    entries = []
    report = {"total": 0, "imported": 0, "duplicates": 0, "invalid": 0, "errors": []}

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        report["errors"].append(f"XML parse error: {e}")
        return entries, report

    body = root.find("body")
    if body is None:
        report["errors"].append("No <body> element found in OPML")
        return entries, report

    _walk_outlines(body, entries, report, existing, parent_text="")
    return entries, report


def _walk_outlines(parent_el, entries, report, existing, parent_text):
    for outline in parent_el.findall("outline"):
        text = (outline.get("text") or "").strip()
        url = (
            outline.get("xmlUrl")
            or outline.get("htmlUrl")
            or outline.get("url")
            or ""
        ).strip()

        children = outline.findall("outline")
        if children and not url:
            _walk_outlines(outline, entries, report, existing, parent_text=text)
            continue

        report["total"] += 1
        if not url or not url.startswith("http"):
            report["invalid"] += 1
            report["errors"].append(f"Skipped outline with no valid URL: {text!r}")
            continue
        if url in existing:
            report["duplicates"] += 1
            continue

        existing.add(url)
        platform = parent_text or "Imported"
        entries.append({
            "url": url,
            "platform": platform,
            "channel_id": text or "",
        })
        report["imported"] += 1
