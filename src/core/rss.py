# src/core/rss.py
from __future__ import annotations

import email.utils
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .html import _extract_detail_links_from_text
from .links import _is_detail_link

# -----------------------------
# RSS parsing
# -----------------------------
logger = logging.getLogger(__name__)


def parse_pubdate_to_utc(pubdate: str) -> datetime | None:
    if not pubdate:
        logger.debug("🕒 pubDate vazio no RSS (vai tentar data do título)")
        return None

    try:
        dt = email.utils.parsedate_to_datetime(pubdate)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception as e:
        logger.debug("🕒 Falha parsedate_to_datetime('%s'): %s", pubdate, e)

    try:
        s = pubdate.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as e:
        logger.debug("🕒 Falha fromisoformat('%s'): %s", pubdate, e)
        return None


def parse_date_from_title_to_utc(title: str) -> datetime | None:
    t = (title or "").strip()
    m = re.search(r"\bde\s+(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if not m:
        logger.debug("🗓️ Não encontrei data no título: %s", (t[:120] + "…") if len(t) > 120 else t)
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime(y, mo, d, 0, 0, 0, tzinfo=timezone.utc)
    except Exception as e:
        logger.debug("🗓️ Falha a construir datetime do título '%s': %s", t, e)
        return None


def _find_text(el: ET.Element, local_name: str) -> str:
    if el is None:
        return ""
    for c in list(el):
        if not isinstance(c.tag, str):
            continue
        if c.tag == local_name or c.tag.endswith("}" + local_name):
            return (c.text or "").strip()
    return ""


def _find_link(el: ET.Element) -> str:
    # Atom style: <link href="...">
    for c in list(el):
        if not isinstance(c.tag, str):
            continue
        if c.tag == "link" or c.tag.endswith("}link"):
            href = (c.attrib or {}).get("href", "").strip()
            if href:
                return href

    # RSS style: <link>https://...</link>
    link = _find_text(el, "link")
    if link:
        return link

    return _find_text(el, "guid")


def _find_enclosure_url(el: ET.Element) -> str:
    if el is None:
        return ""

    # <enclosure url="...pdf" .../>
    for c in list(el):
        if not isinstance(c.tag, str):
            continue
        if c.tag == "enclosure" or c.tag.endswith("}enclosure"):
            url = (c.attrib or {}).get("url", "").strip()
            if url:
                return url

    # fallback: se <link> já for um url (pode ser pdf)
    link = _find_link(el)
    return link or ""


def _find_date_any(el: ET.Element) -> str:
    for name in ("pubDate", "published", "updated"):
        v = _find_text(el, name)
        if v:
            return v
    for c in list(el):
        if not isinstance(c.tag, str):
            continue
        if c.tag.endswith("}date") or c.tag == "date":
            txt = (c.text or "").strip()
            if txt:
                return txt
    return ""


def parse_rss_items(rss_xml: bytes) -> list[dict]:
    root = ET.fromstring(rss_xml)

    channel = root.find("channel")
    if channel is None:
        channel = root.find(".//channel")

    if channel is not None:
        items_xml = channel.findall("item") or channel.findall(".//item")
    else:
        items_xml = root.findall(".//item")

    if not items_xml:
        items_xml = root.findall(".//{*}item")

    items: list[dict] = []
    for item in items_xml:
        title = _find_text(item, "title")
        pub_raw = _find_date_any(item)
        pub_dt = parse_pubdate_to_utc(pub_raw) or parse_date_from_title_to_utc(title)
        pdf_or_link = _find_enclosure_url(item)

        link = _find_link(item)
        desc = _find_text(item, "description")
        guid = _find_text(item, "guid")

        # B7: tentar extrair links de detalhe logo do RSS (description/guid) antes de fazer fetch ao índice
        detail_links: set[str] = set()
        if link and _is_detail_link(link):
            detail_links.add(link)

        base_for_join = link or "https://diariodarepublica.pt/"
        for u in _extract_detail_links_from_text(desc or "", base_for_join):
            detail_links.add(u)
        for u in _extract_detail_links_from_text(guid or "", base_for_join):
            detail_links.add(u)

        items.append(
            {
                "title": title,
                "link": link,
                "guid": guid,
                "pdf_url": pdf_or_link,
                "pubDate": pub_raw,
                "pubDate_utc": pub_dt,
                "description": desc,
                "detail_links": sorted(detail_links),
            }
        )

    return items


__all__ = ["parse_pubdate_to_utc", "parse_date_from_title_to_utc", "parse_rss_items"]
