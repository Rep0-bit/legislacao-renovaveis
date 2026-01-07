# src/collectors/coletor_dr_serie1_rss.py
from __future__ import annotations

import argparse
import csv
import email.utils
import json
import logging
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from contextlib import suppress

# -----------------------------
# Reporting helpers
# -----------------------------
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..config.logging_setup import setup_logging
from ..db.db import get_conn, init_db
from ..processing.indexador import _get_existing, _is_manual, make_hash, upsert_diploma

try:
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore


logger = logging.getLogger(__name__)

RSS_SERIE1_PDF = "https://files.diariodarepublica.pt/rss/serie1.xml"

# Mantemos o checkpoint antigo por compatibilidade (opcional),
# mas vamos usar o cursor v2 para incremental robusto.
STATE_KEY_LAST_PUBDATE = "serie1_pdf_last_pubdate_utc"
STATE_KEY_CURSOR = "serie1_pdf_cursor_v2"


# -----------------------------
# Headers / Session (cookies + warm-up)
# -----------------------------
HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0",
}

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS_BROWSER)

_WARMED_UP = False


def _warmup() -> None:
    """Pedido inicial para obter cookies/sessão (evita shell OutSystems em alguns casos)."""
    global _WARMED_UP
    if _WARMED_UP:
        return
    with suppress(Exception):
        SESSION.get("https://diariodarepublica.pt/dr/home", timeout=30, allow_redirects=True)
    _WARMED_UP = True


# -----------------------------
# Exceptions
# -----------------------------
class FetchError(RuntimeError):
    def __init__(self, url: str, msg: str, status_code: int | None = None):
        super().__init__(msg)
        self.url = url
        self.status_code = status_code


# -----------------------------
# Helpers HTTP
# -----------------------------
def _looks_like_outsystems_shell(html: bytes) -> bool:
    """
    Deteta a shell mínima do OutSystems (reactContainer + scripts OutSystems),
    que NÃO contém o conteúdo do diploma nem links PDF.
    """
    s = html.decode("utf-8", errors="ignore")

    has_react = (
        ('id="reactContainer"' in s) or ("OutSystemsReactView.js" in s) or ("window.OutSystemsApp" in s)
    )
    if not has_react:
        return False

    # Se já há sinais de conteúdo, não é shell
    return not (("SUMÁRIO" in s) or ("TEXTO" in s) or ("Data de Publicação" in s) or ("<h1" in s.lower()))


def http_get(url: str, timeout: int = 60, *, force_browser_headers: bool = False) -> bytes:
    """
    GET robusto:
    - Usa requests.Session (cookies + keep-alive)
    - Faz warm-up antes de pedir páginas do diariodarepublica.pt
    - Se vier "HTML shell OutSystems" num /dr/detalhe/, tenta 2ª vez com headers browser + Referer
    """
    if "diariodarepublica.pt" in (url or ""):
        _warmup()

    headers = HEADERS_BROWSER if force_browser_headers else HEADERS_BASE

    try:
        r = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            raise FetchError(url, f"HTTP {r.status_code}", status_code=r.status_code)

        content = r.content

        # Retry quando apanha shell (especialmente em /dr/detalhe/)
        if ("/dr/detalhe/" in url) and _looks_like_outsystems_shell(content):
            headers2 = dict(HEADERS_BROWSER)
            headers2["Referer"] = "https://diariodarepublica.pt/dr/home"
            _warmup()

            r2 = SESSION.get(url, headers=headers2, timeout=timeout, allow_redirects=True)
            if r2.status_code >= 400:
                raise FetchError(url, f"HTTP {r2.status_code}", status_code=r2.status_code)
            return r2.content

        return content

    except requests.exceptions.Timeout as e:
        logger.debug("⏱️ Timeout a pedir %s (timeout=%ss)", url, timeout)
        raise FetchError(url, f"Timeout ({timeout}s)") from e
    except requests.exceptions.RequestException as e:
        logger.debug("🌐 RequestException a pedir %s: %s", url, e)
        raise FetchError(url, f"RequestException: {e}") from e


# -----------------------------
# PDF keyword fallback
# -----------------------------
def extract_text_from_pdf_bytes(pdf_bytes: bytes, *, max_pages: int = 2, max_chars: int = 20000) -> str:
    """Extrai texto de até `max_pages` páginas para matching de keywords."""
    if PdfReader is None:
        return ""

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        texts: list[str] = []
        for page in reader.pages[:max_pages]:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t:
                texts.append(t)

        joined = "\n".join(texts).strip()
        if len(joined) > max_chars:
            joined = joined[:max_chars]
        return joined
    except Exception as e:
        logger.debug("📄 Falha a extrair texto do PDF: %s", e)
        return ""


@dataclass
class DecisionInfo:
    decision: str = ""  # accepted | rejected_keywords | rejected_no_text | error
    reason: str = ""  # short reason string
    match_keyword: str = ""  # which keyword matched
    match_where: str = ""  # title | summary | fulltext
    text_source: str = ""  # rss | detail_html | pdf
    text_len: int = 0  # length of the analyzed text


def _contains_keyword(text: str, keywords: list[str]) -> str | None:
    """
    Returns the first keyword matched (case-insensitive substring match),
    or None if none match.
    If you already do accent normalization elsewhere, apply it before calling.
    """
    if not text:
        return None
    t = text.lower()
    for kw in keywords:
        if kw and kw.lower() in t:
            return kw
    return None


def keyword_match_fields(
    title: str, summary: str, fulltext: str, keywords: list[str]
) -> tuple[str | None, str | None]:
    """
    Returns (keyword, where) where in {title, summary, fulltext}.
    """
    for where, value in (("title", title), ("summary", summary), ("fulltext", fulltext)):
        hit = _contains_keyword(value or "", keywords)
        if hit:
            return hit, where
    return None, None


def analyzed_text_len(title: str, summary: str, fulltext: str) -> int:
    """
    A single scalar that helps debugging: how much text existed to match against.
    Prefer fulltext, else summary, else title.
    """
    base = fulltext or summary or title or ""
    return len(base)


def try_keyword_match_via_pdf(
    pdf_url: str | None,
    keywords: Iterable[str],
    *,
    max_pages: int = 2,
    keywords_enabled: bool = True,
) -> tuple[str | None, str]:
    """Tenta match de keywords via PDF. Retorna (hit, excerpt)."""
    if not pdf_url:
        return None, ""

    try:
        pdf_bytes = http_get(pdf_url, timeout=90, force_browser_headers=True)
    except FetchError as e:
        logger.debug("📄 Falha a baixar PDF (%s): %s", e.url, e)
        return None, ""

    txt = extract_text_from_pdf_bytes(pdf_bytes, max_pages=max_pages)
    if not txt:
        return None, ""

    hit: str | None = None
    is_generic = False
    has_ctx = False

    if keywords_enabled:
        hit, is_generic, has_ctx = match_keywords_hit_quality(txt, keywords)

    # Se o hit for genérico e não houver contexto (near-hit), ignora
    if hit and is_generic and not has_ctx:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("🧹 PDF hit genérico sem contexto: hit=%r (ignorado)", hit)
        hit = None

    excerpt = " ".join(txt.split())[:260]
    return hit, excerpt


# -----------------------------
# State (checkpoint/cursor)
# -----------------------------
def get_state(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM collector_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_state(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO collector_state(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )
        conn.commit()


def delete_state(key: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM collector_state WHERE key=?", (key,))
        conn.commit()


def reset_checkpoint() -> None:
    delete_state(STATE_KEY_LAST_PUBDATE)


def _parse_last_pub_dt() -> datetime | None:
    last_pub_str = get_state(STATE_KEY_LAST_PUBDATE)
    if not last_pub_str:
        return None
    try:
        return datetime.fromisoformat(last_pub_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception as e:
        logger.debug("⏱️ Checkpoint inválido '%s': %s", last_pub_str, e)
        return None


def _parse_cursor() -> tuple[datetime | None, set[str]]:
    """
    Cursor v2:
      {"last_dt":"2026-01-06T00:00:00Z","last_dt_links":[...]}
    """
    raw = get_state(STATE_KEY_CURSOR)
    if not raw:
        return None, set()

    try:
        obj = json.loads(raw)
        last_dt_s = (obj.get("last_dt") or "").strip()
        links = obj.get("last_dt_links") or []
        if not last_dt_s:
            return None, set()

        last_dt = datetime.fromisoformat(last_dt_s.replace("Z", "+00:00")).astimezone(timezone.utc)
        return last_dt, set(str(x).strip() for x in links if str(x).strip())
    except Exception as e:
        logger.debug("⏱️ Cursor inválido (%s): %s", raw[:120], e)
        return None, set()


def _save_cursor(last_dt: datetime, last_dt_links: set[str]) -> None:
    capped = list(sorted(last_dt_links))[:500]  # evita crescimento infinito
    obj = {
        "last_dt": last_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "last_dt_links": capped,
    }
    set_state(STATE_KEY_CURSOR, json.dumps(obj, ensure_ascii=False))


# -----------------------------
# RSS parsing
# -----------------------------
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


# -----------------------------
# HTML parsing helpers
# -----------------------------
def _make_soup(html: bytes) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.debug("🍲 Falha parser lxml; a usar html.parser (%s)", e)
        return BeautifulSoup(html, "html.parser")


def _extract_detail_links_from_text(text: str, base_url: str) -> list[str]:
    r"""Extrai links /dr/detalhe/ e /eli/ de texto/HTML (inclui JSON com \\/)."""
    links: set[str] = set()
    if not text:
        return []

    # 1) Atributos HTML (href, data-href, data-url)
    try:
        soup = BeautifulSoup(text, "lxml")
    except Exception:
        soup = BeautifulSoup(text, "html.parser")

    for tag in soup.find_all(["a", "div", "span", "li", "button"], href=True):
        href = (tag.get("href") or "").strip()
        if href:
            full = urljoin(base_url, href)
            if _is_detail_link(full):
                links.add(full)

    for tag in soup.find_all(True):
        for attr in ("data-href", "data-url", "data-link"):
            v = (tag.get(attr) or "").strip()
            if v:
                full = urljoin(base_url, v)
                if _is_detail_link(full):
                    links.add(full)

    # 2) Regex em texto cru (inclui URLs escapadas)
    raw = text.replace("\\/", "/")
    # Absolutas
    for m in re.finditer(r"https?://[^\s'\"<>]+/(?:dr/detalhe|eli)/[^\s'\"<>]+", raw):
        links.add(m.group(0))
    # Relativas
    for m in re.finditer(r"/(?:dr/detalhe|eli)/[^\s'\"<>]+", raw):
        links.add(urljoin(base_url, m.group(0)))

    return sorted(links)


def extract_detail_links_from_index_html(index_html: bytes, base_url: str) -> list[str]:
    """Extrai links de detalhe a partir de HTML de índice.

    B7: Mais robusto do que apenas <a href>, porque o DR pode embutir URLs em JSON/scripts.
    """
    # primeiro tenta soup sobre bytes
    soup = _make_soup(index_html)
    links: set[str] = set()

    # 1) <a href>
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        full = urljoin(base_url, href)
        if _is_detail_link(full):
            links.add(full)

    # 2) data-*
    for tag in soup.find_all(True):
        for attr in ("data-href", "data-url", "data-link"):
            v = (tag.get(attr) or "").strip()
            if v:
                full = urljoin(base_url, v)
                if _is_detail_link(full):
                    links.add(full)

    # 3) Regex em texto cru (inclui \//JSON)
    try:
        decoded = index_html.decode("utf-8", errors="ignore")
    except Exception:
        decoded = str(index_html)

    for u in _extract_detail_links_from_text(decoded, base_url):
        links.add(u)

    return sorted(links)


# -----------------------------
# Parse tipo/numero/ano
# -----------------------------
def _clean_tipo(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _canon_numero(num: str, extra: str | None) -> str:
    n = (num or "").strip().upper()
    e = (extra or "").strip().upper()
    return f"{n}/{e}" if e else n


def parse_tipo_numero_ano(title: str) -> tuple[str | None, str | None, int | None]:
    """
    Extrai (tipo, numero, ano) de títulos do DR, tolerante a variações.
    """
    t = (title or "").strip()
    if not t:
        return None, None, None

    t_norm = re.sub(r"\s+", " ", t).strip()

    # Normalizar variantes de "nº", "n.o", "n °" -> "n.º"
    t_norm = re.sub(r"\bN\s*[.\s]*[ºo°]\b\.?", "n.º", t_norm, flags=re.IGNORECASE)
    t_norm = re.sub(r"\bn\s*[.\s]*[ºo°]\b\.?", "n.º", t_norm, flags=re.IGNORECASE)
    t_norm = re.sub(r"\s+", " ", t_norm).strip()

    def _finalize(tipo_raw: str, num_raw: str, ano_raw: str, extra_raw: str | None):
        try:
            ano = int(ano_raw)
        except Exception:
            return None, None, None
        tipo = _clean_tipo(tipo_raw)
        numero = _canon_numero(num_raw, extra_raw)
        return tipo, numero, ano

    num_pat = r"\d+(?:-[A-Z0-9]+)?"
    extra_pat = r"[A-Z0-9]+(?:-[A-Z0-9]+)?"

    pat1 = re.compile(
        rf"""
        ^\s*
        (?P<tipo>.+?)
        \s+
        n\.º
        \s+
        (?P<num>{num_pat})
        \s*/\s*
        (?P<ano>\d{{4}})
        (?:\s*/\s*(?P<extra>{extra_pat}))?
        \s*$
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    m = pat1.search(t_norm)
    if m:
        return _finalize(m.group("tipo"), m.group("num"), m.group("ano"), m.group("extra"))

    pat2 = re.compile(
        rf"""
        (?P<tipo>.+?)
        \s+
        n\.º
        \s+
        (?P<num>{num_pat})
        \s*/\s*
        (?P<ano>\d{{4}})
        (?:\s*/\s*(?P<extra>{extra_pat}))?
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    m = pat2.search(t_norm)
    if m:
        return _finalize(m.group("tipo"), m.group("num"), m.group("ano"), m.group("extra"))

    pat3 = re.compile(
        rf"""
        ^\s*
        (?P<tipo>.+?)
        \s+
        (?P<num>{num_pat})
        \s*/\s*
        (?P<ano>\d{{4}})
        (?:\s*/\s*(?P<extra>{extra_pat}))?
        \s*$
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    m = pat3.search(t_norm)
    if m:
        return _finalize(m.group("tipo"), m.group("num"), m.group("ano"), m.group("extra"))

    pat4 = re.compile(
        rf"""
        (?P<num>{num_pat})
        \s*/\s*
        (?P<ano>\d{{4}})
        (?:\s*/\s*(?P<extra>{extra_pat}))?
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    m = pat4.search(t_norm)
    if m:
        tipo_raw = t_norm[: m.start()].strip(" -–—:;")
        if tipo_raw:
            return _finalize(tipo_raw, m.group("num"), m.group("ano"), m.group("extra"))

    return None, None, None


# -----------------------------
# Extract id_dr and pdf URL
# -----------------------------
def extract_id_dr(url: str) -> str | None:
    u = (url or "").strip().split("?", 1)[0].rstrip("/")
    m = re.search(r"-(\d+)$", u)
    return m.group(1) if m else None


def find_best_pdf_link(detail_html: bytes, detail_url: str) -> str | None:
    soup = _make_soup(detail_html)

    pdf_candidates: list[str] = []

    # 1) Links diretos com .pdf
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if ".pdf" in href.lower():
            pdf_candidates.append(urljoin(detail_url, href))

    # 2) Às vezes vem em atributos data-*
    for tag in soup.find_all(attrs=True):
        for _, v in (tag.attrs or {}).items():
            if isinstance(v, str) and ".pdf" in v.lower():
                pdf_candidates.append(urljoin(detail_url, v.strip()))

    # Preferir domínios do DR
    for u in pdf_candidates:
        if "files.diariodarepublica.pt" in u or "files.dre.pt" in u:
            return u
    if pdf_candidates:
        return pdf_candidates[0]

    # 3) Regex fallback no HTML
    s = detail_html.decode("utf-8", errors="ignore")
    m = re.search(r"https?://files\.(?:diariodarepublica|dre)\.pt/[^\s\"']+\.pdf", s)
    if m:
        return m.group(0)
    m2 = re.search(r"https?://[^\s\"']+\.pdf", s)
    if m2:
        return m2.group(0)

    logger.debug("📄 Nenhum link PDF encontrado no detalhe: %s", detail_url)
    return None


# -----------------------------
# Robust title extraction helpers (detalhe)
# -----------------------------
def _first_meta_content(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> str:
    if prop:
        m = soup.find("meta", attrs={"property": prop})
        if m and m.get("content"):
            return str(m["content"]).strip()
    if name:
        m = soup.find("meta", attrs={"name": name})
        if m and m.get("content"):
            return str(m["content"]).strip()
    return ""


def _try_jsonld_title(soup: BeautifulSoup) -> str:
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (tag.string or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            for key in ("headline", "name", "titulo", "title"):
                v = obj.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return ""


def _safe_dump_name(detail_url: str) -> str:
    id_dr = extract_id_dr(detail_url) or "sem_id"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", detail_url.strip().rstrip("/").split("/")[-1])[:40]
    return f"{id_dr}_{slug}".strip("_-")


def extract_meta_from_detail_html(
    detail_html: bytes,
    detail_url: str,
    *,
    rss_title: str = "",
    rss_desc: str = "",
    dump_html_shell: bool = False,
    dump_dir: Path = Path("data/debug/html_shell"),
) -> dict:
    soup = _make_soup(detail_html)

    # 1) H1
    h1 = soup.find("h1")
    titulo = h1.get_text(" ", strip=True) if h1 else ""

    # 2) Meta tags
    if not titulo:
        titulo = _first_meta_content(soup, prop="og:title") or _first_meta_content(soup, name="twitter:title")

    # 3) <title>
    if not titulo:
        t = soup.find("title")
        if t:
            titulo = t.get_text(" ", strip=True)

    # 4) JSON-LD
    if not titulo:
        titulo = _try_jsonld_title(soup)

    # Sumário
    sumario = ""
    raw_text = soup.get_text("\n", strip=True)
    m = re.search(
        r"\bSUM[ÁA]RIO\b\s*[:\-]?\s*(.+?)(?:\n+TEXTO\b|\n+Texto\b|$)",
        raw_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m:
        sumario = " ".join(m.group(1).split())
        if len(sumario) > 1200:
            sumario = sumario[:1200].rstrip() + "…"

    if not sumario:
        md = soup.find("meta", attrs={"name": "description"})
        if md and md.get("content"):
            sumario = str(md["content"]).strip()

    if not sumario:
        og = soup.find("meta", attrs={"property": "og:description"})
        if og and og.get("content"):
            sumario = str(og["content"]).strip()

    html_shell = _looks_like_outsystems_shell(detail_html)

    # fallback RSS
    if not titulo and rss_title:
        titulo = rss_title
    if not sumario and rss_desc:
        s = " ".join(rss_desc.split())
        sumario = (s[:1200].rstrip() + "…") if len(s) > 1200 else s

    if html_shell:
        logger.debug("🧾 HTML shell (sem conteúdo server-side): %s", detail_url)
        if dump_html_shell:
            dump_dir.mkdir(parents=True, exist_ok=True)
            name = _safe_dump_name(detail_url)
            out_path = dump_dir / f"{name}.html"
            try:
                out_path.write_bytes(detail_html)
                logger.debug("🧪 HTML shell guardado em: %s", out_path)
            except Exception as e:
                logger.debug("🧪 Falha a guardar HTML shell (%s): %s", out_path, e)

    return {"titulo": titulo, "sumario": sumario, "url_detalhe": detail_url, "html_shell": html_shell}


# -----------------------------
# Keyword matching (accent-insensitive) + qualidade
# -----------------------------
def _norm_text(s: str) -> str:
    s = (s or "").strip().casefold()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s


def match_keywords_hit(text: str, keywords: Iterable[str]) -> str | None:
    s_norm = _norm_text(text or "")

    for k in keywords:
        k = (k or "").strip()
        if not k:
            continue

        kn = _norm_text(k)

        # Keywords curtas: palavra inteira (evita "sen" bater em "sendo")
        if len(kn) <= 3:
            if re.search(rf"\b{re.escape(kn)}\b", s_norm):
                return k
        else:
            if kn in s_norm:
                return k

    return None


GENERIC_KEYWORDS = {"sen", "armazenamento", "solar", "onshore", "offshore", "biomassa", "carbono"}
GENERIC_KEYWORDS_NORM = {_norm_text(k) for k in GENERIC_KEYWORDS}

ENERGY_CONTEXT_TERMS = {
    "energia",
    "energ",
    "elétric",
    "eletric",
    "rede eletrica",
    "rede elétrica",
    "rede de transporte",
    "rede de distribuicao",
    "rede de distribuição",
    "renov",
    "autoconsumo",
    "upac",
    "fotovolta",
    "eólic",
    "eolic",
    "hidrog",
    "bess",
    "produção",
    "produc",
    "sistema elétrico",
    "sistema eletrico",
    "garantias de origem",
    "título de reserva de capacidade",
    "trc",
}


def _has_energy_context(text: str) -> bool:
    s = _norm_text(text or "")
    for t in ENERGY_CONTEXT_TERMS:
        tt = _norm_text(t)
        if tt and tt in s:
            return True
    return False


def _has_energy_context_near_hit(text: str, hit: str, *, window: int = 500) -> bool:
    """
    Procura termos de contexto energético num raio (window) em torno do hit.
    Reduz falsos positivos (headers/footers, etc.).
    """
    s = _norm_text(text or "")
    h = _norm_text(hit or "")
    if not s or not h:
        return False

    start = 0
    while True:
        idx = s.find(h, start)
        if idx == -1:
            break

        lo = max(0, idx - window)
        hi = min(len(s), idx + len(h) + window)
        chunk = s[lo:hi]

        for t in ENERGY_CONTEXT_TERMS:
            tt = _norm_text(t)
            if tt and tt in chunk:
                return True

        start = idx + max(1, len(h))

    return False


def match_keywords_hit_quality(text: str, keywords: Iterable[str]) -> tuple[str | None, bool, bool]:
    """
    Retorna:
      (hit, is_generic_hit, has_context)

    - Se hit não é genérico: contexto pode ser global.
    - Se hit é genérico: exige contexto PERTO do hit (near-hit).
    """
    hit = match_keywords_hit(text, keywords)
    if not hit:
        return None, False, False

    is_generic = _norm_text(hit) in GENERIC_KEYWORDS_NORM

    if not is_generic:
        return hit, False, _has_energy_context(text)

    return hit, True, _has_energy_context_near_hit(text, hit, window=500)


# -----------------------------
# Report
# -----------------------------
def _infer_match_where_and_source(
    hit: str | None,
    rss_title: str,
    rss_desc: str,
    meta_titulo: str = "",
    meta_sumario: str = "",
    used_pdf: bool = False,
) -> tuple[str, str]:
    """
    Returns (match_where, text_source).
    match_where: title | summary | meta_title | meta_summary | fulltext | unknown
    text_source: rss | detail_html | pdf
    """
    if not hit:
        return "", ""
    h = hit.lower()
    if used_pdf:
        return "fulltext", "pdf"
    if rss_title and h in rss_title.lower():
        return "title", "rss"
    if rss_desc and h in rss_desc.lower():
        return "summary", "rss"
    if meta_titulo and h in meta_titulo.lower():
        return "meta_title", "detail_html"
    if meta_sumario and h in meta_sumario.lower():
        return "meta_summary", "detail_html"
    return "unknown", "rss"


def _text_len_for_report(
    rss_title: str,
    rss_desc: str,
    meta_titulo: str = "",
    meta_sumario: str = "",
    pdf_excerpt: str = "",
    used_pdf: bool = False,
) -> int:
    base = pdf_excerpt if used_pdf else f"{rss_title} {rss_desc} {meta_titulo} {meta_sumario}"
    return len((base or "").strip())


def write_report(report_rows: list[dict], out_dir: Path = Path("data/index/reports")) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"relatorio_coleta_{ts}.csv"

    headers = [
        "status",
        "manual",
        "tipo",
        "numero",
        "ano",
        "titulo",
        "url_detalhe",
        "url_pdf",
        "id_dr",
        "old_hash",
        "new_hash",
        # --- B-3: relatório mais informativo ---
        "match_keyword",
        "match_where",
        "text_source",
        "text_len",
        "reason",
    ]

    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=headers, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in report_rows:
            w.writerow({h: r.get(h, "") for h in headers})

    return out_path


# -----------------------------
# Orchestration helpers
# -----------------------------
def _is_detail_link(url: str) -> bool:
    u = (url or "").strip()
    return ("/dr/detalhe/" in u) or ("/eli/" in u)


# -----------------------------
# B6: filtro por tipo (slug do DR)
# -----------------------------
_TIPO_ALIASES: dict[str, str] = {
    "dl": "decreto-lei",
    "decretolei": "decreto-lei",
    "decreto_lei": "decreto-lei",
    "decreto lei": "decreto-lei",
}


def _norm_tipo_slug(t: str) -> str:
    t = (t or "").strip().lower()
    t = t.replace("_", "-")
    return _TIPO_ALIASES.get(t, t)


def extract_tipo_from_detail_url(url: str) -> str | None:
    """Extrai o slug do tipo a partir de /dr/detalhe/<tipo>/..."""
    if not url:
        return None
    m = re.search(r"/dr/detalhe/([^/]+)/", url)
    if not m:
        return None
    return _norm_tipo_slug(m.group(1))


def infer_tipo_from_title(title: str) -> str | None:
    """Fallback leve baseado no prefixo do título."""
    t = (title or "").strip().lower()
    if not t:
        return None
    if t.startswith("portaria"):
        return "portaria"
    if t.startswith("decreto-lei") or t.startswith("decreto lei"):
        return "decreto-lei"
    if t.startswith("lei"):
        return "lei"
    if t.startswith("despacho"):
        return "despacho"
    if t.startswith("resolução") or t.startswith("resolucao"):
        return "resolucao"
    return None


def should_filter_by_tipo(
    tipo: str | None,
    include_set: set[str],
    exclude_set: set[str],
    strict_types: bool,
) -> tuple[bool, str]:
    """Retorna (filtrar?, motivo)."""
    tipo_norm = _norm_tipo_slug(tipo or "") if tipo else ""
    if exclude_set and tipo_norm and (tipo_norm in exclude_set):
        return True, f"excluded type: {tipo_norm}"

    if include_set:
        if not tipo_norm:
            return (True, "unknown type (strict)") if strict_types else (False, "unknown type (allowed)")
        if tipo_norm not in include_set:
            return True, f"type not in include set: {tipo_norm}"

    return False, "ok"


def _filter_items(
    items: list[dict],
    cutoff: datetime,
    last_cursor_dt: datetime | None,
    last_cursor_links: set[str],
    force_full_window: bool,
) -> list[dict]:
    filtered: list[dict] = []

    for it in items:
        dt = it.get("pubDate_utc")
        link = (it.get("link") or "").strip()

        if dt is None:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("⛔ item sem data: title=%r link=%s", (it.get("title", "") or "")[:80], link)
            if force_full_window:
                filtered.append(it)
            continue

        if dt < cutoff:
            continue

        if not force_full_window and last_cursor_dt:
            if dt < last_cursor_dt:
                continue

            if dt == last_cursor_dt and link and link in last_cursor_links:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("⛔ excluído por cursor (mesmo dt + link já visto): %s", link)
                continue

        filtered.append(it)

    filtered.sort(key=lambda x: x.get("pubDate_utc") or datetime(1970, 1, 1, tzinfo=timezone.utc))
    return filtered


# -----------------------------
# Main collector
# -----------------------------
def collect(
    keywords: list[str] | None,
    *,
    days: int = 90,
    force_full_window: bool = False,
    debug: bool = False,
    reset_checkpoint_flag: bool = False,
    dry_run: bool = False,
    dump_html_shell: bool = False,
    pdf_fallback_pages: int = 2,
    keywords_enabled: bool = True,
    include_types: list[str] | None = None,
    exclude_types: list[str] | None = None,
    strict_types: bool = False,
    profile: str | None = None,
) -> None:
    init_db()

    # B8: profiles (presets) — útil quando o coletor é chamado via API/testes.
    # Aqui não temos sys.argv, por isso a precedência é:
    #   parâmetros explícitos da chamada > profile > defaults.
    if profile:
        profiles = load_profiles()
        cfg = profiles.get(profile)
        if not cfg:
            raise ValueError(f"Profile desconhecido: {profile}. Disponíveis: {', '.join(sorted(profiles))}")
        if (days == 90) and ("days" in cfg):
            days = int(cfg["days"])
        if (include_types is None) and ("types" in cfg):
            v = cfg.get("types")
            if isinstance(v, list):
                include_types = [str(x).strip() for x in v if str(x).strip()]
            else:
                include_types = [p for p in str(v or "").split(",") if p.strip()]
        if (exclude_types is None) and ("exclude_types" in cfg):
            v = cfg.get("exclude_types")
            if isinstance(v, list):
                exclude_types = [str(x).strip() for x in v if str(x).strip()]
            else:
                exclude_types = [p for p in str(v or "").split(",") if p.strip()]
        if (not strict_types) and ("strict_types" in cfg):
            strict_types = bool(cfg.get("strict_types"))
        if (pdf_fallback_pages == 2) and ("pdf_fallback_pages" in cfg):
            pdf_fallback_pages = int(cfg["pdf_fallback_pages"])
        if keywords is None:
            if cfg.get("no_keywords") is True:
                keywords_enabled = False
                keywords = []
            elif ("keywords" in cfg) and (cfg.get("keywords") is not None):
                keywords = list(cfg.get("keywords") or [])
            else:
                keywords = list(DEFAULT_KEYWORDS)

    if keywords is None:
        keywords = list(DEFAULT_KEYWORDS)

    include_types = include_types or []
    exclude_types = exclude_types or []
    include_set = {_norm_tipo_slug(t) for t in include_types if _norm_tipo_slug(t)}
    exclude_set = {_norm_tipo_slug(t) for t in exclude_types if _norm_tipo_slug(t)}

    if reset_checkpoint_flag:
        reset_checkpoint()
        delete_state(STATE_KEY_CURSOR)
        if debug:
            print(f"🧹 Checkpoint removido: {STATE_KEY_LAST_PUBDATE}")
            print(f"🧹 Cursor removido: {STATE_KEY_CURSOR}")

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=days)

    # compat (antigo) + cursor v2 (novo)
    _ = _parse_last_pub_dt()
    cursor_dt, cursor_links = _parse_cursor()

    if debug:
        logger.debug(
            "🔎 Cursor v2 atual: dt=%s | links=%d",
            cursor_dt.isoformat() if cursor_dt else "None",
            len(cursor_links),
        )

    try:
        rss = http_get(RSS_SERIE1_PDF)
    except FetchError as e:
        logger.error("❌ Falha a obter RSS: %s (%s)", e.url, e)
        return

    # DEBUG: guardar RSS bruto em disco
    if dump_html_shell:
        debug_dir = Path("data/debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        rss_path = debug_dir / "rss_serie1.xml"
        rss_path.write_bytes(rss)
        logger.info("🧪 RSS guardado em: %s", rss_path)

    items = parse_rss_items(rss)

    if debug:
        for it in items[:5]:
            logger.debug(
                "🧾 RSS item: title=%r | link=%r | pdf_url=%r | pub_raw=%r",
                (it.get("title", "") or "")[:80],
                it.get("link", ""),
                it.get("pdf_url", ""),
                it.get("pubDate", ""),
            )

    filtered = _filter_items(
        items,
        cutoff=cutoff,
        last_cursor_dt=cursor_dt,
        last_cursor_links=cursor_links,
        force_full_window=force_full_window,
    )

    logger.info("📡 RSS lido: %d itens", len(items))
    logger.info("🗓️ Janela: últimos %d dias (cutoff UTC: %s)", days, cutoff.isoformat())
    if cursor_dt and not force_full_window:
        logger.info("⏱️ Incremental desde: %s (cursor v2)", cursor_dt.isoformat())
    elif force_full_window:
        logger.info("♻️ Force full window: ON (ignorar checkpoint)")
    logger.info("➡️ Itens a processar nesta execução: %d", len(filtered))

    report_rows: list[dict] = []
    total_links = 0
    total_pdf_direct = 0
    fail_fetch = 0
    rejected_kw = 0
    rejected_parse = 0

    # Cursor de trabalho (só atualiza com itens aceites)
    cursor_dt_work = cursor_dt
    cursor_links_work = set(cursor_links)

    # Para o checkpoint antigo (compat) guardamos a dt mais recente aceite
    newest_pub_seen_accepted: datetime | None = None

    for it in filtered:
        link = (it.get("link") or "").strip()
        title = it.get("title") or ""
        desc = it.get("description") or ""
        pub_dt = it.get("pubDate_utc")
        rss_pdf_url = (it.get("pdf_url") or "").strip() or None

        # ------------------------------------------------------------
        # Caminho A: RSS já traz PDF
        # ------------------------------------------------------------
        if rss_pdf_url:
            pdf_url = rss_pdf_url

            # B6: filtro por tipo (o mais cedo possível)
            tipo_slug = extract_tipo_from_detail_url(link) or infer_tipo_from_title(title)
            filtrar_tipo, motivo_tipo = should_filter_by_tipo(
                tipo_slug, include_set, exclude_set, strict_types
            )
            if filtrar_tipo:
                if debug:
                    logger.debug(
                        "🚫 Filtrado por tipo (RSS+PDF): %s | motivo=%s | url=%s",
                        tipo_slug or "?",
                        motivo_tipo,
                        (link or pdf_url),
                    )
                report_rows.append(
                    {
                        "status": "rejeitado",
                        "manual": "nao",
                        "tipo": tipo_slug or "",
                        "numero": "",
                        "ano": "",
                        "titulo": title or "",
                        "url_detalhe": link or "",
                        "url_pdf": pdf_url or "",
                        "id_dr": "",
                        "old_hash": "",
                        "new_hash": "",
                        "match_keyword": "",
                        "match_where": "",
                        "text_source": "rss",
                        "text_len": len((f"{title} {desc}" or "").strip()),
                        "reason": f"type filter: {motivo_tipo}",
                    }
                )
                continue

            filtro_texto = f"{title} {desc}"
            hit, is_generic, has_ctx = match_keywords_hit_quality(filtro_texto, keywords)

            # Genérica sem contexto -> não aceita ainda, valida no PDF
            if hit and is_generic and not has_ctx:
                if debug:
                    logger.debug("🧹 Hit genérico sem contexto no RSS: hit=%r | vai validar no PDF", hit)
                hit = None

            pdf_hit: str | None = None
            pdf_excerpt = ""

            if not hit:
                pdf_hit, pdf_excerpt = try_keyword_match_via_pdf(
                    pdf_url, keywords, max_pages=pdf_fallback_pages, keywords_enabled=keywords_enabled
                )

                if debug:
                    logger.debug(
                        "🧪 PDF fallback: pdf_url=%s | hit=%r | excerpt_len=%d",
                        pdf_url,
                        pdf_hit,
                        len(pdf_excerpt or ""),
                    )

                if pdf_hit:
                    hit = pdf_hit
                    if debug:
                        logger.debug("✅ Keyword match via PDF (%s): %s", hit, pdf_url)
                        logger.debug("   pdf_excerpt: %s", pdf_excerpt)

            # --- B-3: relatório mais informativo ---
            match_keyword = pdf_hit or hit or ""
            used_pdf = bool(pdf_hit)
            match_where, text_source = _infer_match_where_and_source(
                hit=match_keyword,
                rss_title=title,
                rss_desc=desc,
                meta_titulo="",
                meta_sumario="",
                used_pdf=used_pdf,
            )
            text_len = _text_len_for_report(
                rss_title=title,
                rss_desc=desc,
                pdf_excerpt=pdf_excerpt,
                used_pdf=used_pdf,
            )
            # -------------------------------------------

            if keywords_enabled and not hit:
                rejected_kw += 1
                if debug:
                    prev = (filtro_texto or "").replace("\n", " ")
                    logger.debug("❌ Rejeitado por keywords (RSS+PDF): %s", (link or pdf_url))
                    logger.debug("   preview: %s", prev[:220])
                report_rows.append(
                    {
                        "status": "rejeitado",
                        "manual": "nao",
                        "tipo": "",
                        "numero": "",
                        "ano": "",
                        "titulo": title or "",
                        "url_detalhe": link or "",
                        "url_pdf": pdf_url or "",
                        "id_dr": "",
                        "old_hash": "",
                        "new_hash": "",
                        "match_keyword": "",
                        "match_where": "",
                        "text_source": "pdf" if pdf_url else "rss",
                        "text_len": text_len,
                        "reason": "no keyword hit",
                    }
                )
                continue

            # Parse tipo/numero/ano preferindo o title do RSS
            tipo, numero, ano = parse_tipo_numero_ano(title)
            if not (tipo and numero and ano):
                tipo, numero, ano = parse_tipo_numero_ano(desc)

            if not (tipo and numero and ano):
                rejected_parse += 1
                if debug:
                    logger.debug("❌ Rejeitado por parse tipo/numero/ano (RSS): %s", (link or pdf_url))
                    logger.debug("   title: %s", title[:140])
                report_rows.append(
                    {
                        "status": "rejeitado",
                        "manual": "nao",
                        "tipo": "",
                        "numero": "",
                        "ano": "",
                        "titulo": title or "",
                        "url_detalhe": link or "",
                        "url_pdf": pdf_url or "",
                        "id_dr": "",
                        "old_hash": "",
                        "new_hash": "",
                        "match_keyword": match_keyword,
                        "match_where": match_where,
                        "text_source": text_source,
                        "text_len": text_len,
                        "reason": "parse tipo/numero/ano failed",
                    }
                )
                continue

            id_dr = extract_id_dr(link) if link else None

            tipo_slug = (
                extract_tipo_from_detail_url(link or "")
                or infer_tipo_from_title(title or "")
                or _norm_tipo_slug(str(tipo))
            )
            reg = {
                "id_dr": id_dr,
                "tipo": tipo,
                "tipo_slug": tipo_slug,
                "numero": str(numero),
                "ano": int(ano),
                "data_publicacao": pub_dt.date().isoformat() if pub_dt else None,
                "titulo": title or "",
                "sumario": desc or "",
                "url_detalhe": link or "",
                "url_pdf": pdf_url,
                "url_consolidado": None,
                "resumo_1_frase": "",
                "observacoes": "",
                "estado": "desconhecido",
            }

            if pdf_url:
                total_pdf_direct += 1

            # --- B-4: dry-run (não escrever DB) ---
            if dry_run:
                existing = _get_existing(tipo, str(numero), int(ano))
                old_hash = (existing or {}).get("hash_fonte")
                new_hash = make_hash(reg)
                if not existing:
                    status = "novo"
                elif old_hash != new_hash:
                    status = "atualizado"
                else:
                    status = "inalterado"
                is_manual = _is_manual(existing, reg)
            else:
                status, is_manual, old_hash, new_hash = upsert_diploma(reg)

            report_rows.append(
                {
                    "status": status,
                    "manual": "sim" if is_manual else "nao",
                    "tipo": tipo,
                    "numero": str(numero),
                    "ano": int(ano),
                    "titulo": reg.get("titulo", ""),
                    "url_detalhe": reg.get("url_detalhe", ""),
                    "url_pdf": reg.get("url_pdf", "") or "",
                    "id_dr": id_dr or "",
                    "old_hash": old_hash or "",
                    "new_hash": new_hash or "",
                    "match_keyword": match_keyword,
                    "match_where": match_where,
                    "text_source": text_source,
                    "text_len": text_len,
                    "reason": f"matched in {match_where}" if match_keyword else "",
                }
            )

            # ---- cursor v2: atualizar apenas com itens aceites ----
            if pub_dt:
                if (cursor_dt_work is None) or (pub_dt > cursor_dt_work):
                    cursor_dt_work = pub_dt
                    cursor_links_work = set()
                if pub_dt == cursor_dt_work and link:
                    cursor_links_work.add(link)

                if (newest_pub_seen_accepted is None) or (pub_dt > newest_pub_seen_accepted):
                    newest_pub_seen_accepted = pub_dt
            # ------------------------------------------------------

            continue

        # ------------------------------------------------------------
        # Caminho B: abrir link -> detalhe(s) -> extrair meta + pdf
        # ------------------------------------------------------------
        # B7: usar links já extraídos do RSS (description/guid) antes de fazer fetch ao índice
        detail_urls: list[str] = [link] if _is_detail_link(link) else list(it.get("detail_links") or [])
        # garantir únicos e ordem estável
        detail_urls = sorted({u for u in detail_urls if u})

        if not detail_urls:
            try:
                index_html = http_get(link)
                detail_urls = extract_detail_links_from_index_html(index_html, base_url=link)
            except FetchError as e:
                fail_fetch += 1
                logger.warning("⚠️ Falha a abrir link do item: %s (%s)", e.url, e)
                report_rows.append(
                    {
                        "status": "rejeitado",
                        "manual": "nao",
                        "tipo": "",
                        "numero": "",
                        "ano": "",
                        "titulo": title or "",
                        "url_detalhe": link or "",
                        "url_pdf": rss_pdf_url or "",
                        "id_dr": "",
                        "old_hash": "",
                        "new_hash": "",
                        "match_keyword": "",
                        "match_where": "",
                        "text_source": "rss" if not link else "detail_html",
                        "text_len": len((f"{title} {desc}" or "").strip()),
                        "reason": "fetch index failed",
                    }
                )
                continue

        total_links += len(detail_urls)

        if not detail_urls:
            # Nada para processar a partir deste item (sem links de detalhe).
            report_rows.append(
                {
                    "status": "rejeitado",
                    "manual": "nao",
                    "tipo": "",
                    "numero": "",
                    "ano": "",
                    "titulo": title or "",
                    "url_detalhe": link or "",
                    "url_pdf": rss_pdf_url or "",
                    "id_dr": "",
                    "old_hash": "",
                    "new_hash": "",
                    "match_keyword": "",
                    "match_where": "",
                    "text_source": "rss",
                    "text_len": len((f"{title} {desc}" or "").strip()),
                    "reason": "no detail urls found",
                }
            )
            continue

        for detail_url in detail_urls:
            if debug:
                logger.debug("🔗 detalhe_url: %s", detail_url)

            # B6: filtro por tipo (antes de abrir o detalhe)
            tipo_slug = extract_tipo_from_detail_url(detail_url) or infer_tipo_from_title(title)
            filtrar_tipo, motivo_tipo = should_filter_by_tipo(
                tipo_slug, include_set, exclude_set, strict_types
            )
            if filtrar_tipo:
                if debug:
                    logger.debug(
                        "🚫 Filtrado por tipo: %s | motivo=%s | url=%s",
                        tipo_slug or "?",
                        motivo_tipo,
                        detail_url,
                    )
                report_rows.append(
                    {
                        "status": "rejeitado",
                        "manual": "nao",
                        "tipo": tipo_slug or "",
                        "numero": "",
                        "ano": "",
                        "titulo": title or "",
                        "url_detalhe": detail_url or "",
                        "url_pdf": "",
                        "id_dr": "",
                        "old_hash": "",
                        "new_hash": "",
                        "match_keyword": "",
                        "match_where": "",
                        "text_source": "rss",
                        "text_len": len((f"{title} {desc}" or "").strip()),
                        "reason": f"type filter: {motivo_tipo}",
                    }
                )
                continue

            try:
                detail_html = http_get(detail_url)
            except FetchError as e:
                fail_fetch += 1
                logger.warning("⚠️ Falha detalhe: %s (%s)", e.url, e)
                report_rows.append(
                    {
                        "status": "rejeitado",
                        "manual": "nao",
                        "tipo": "",
                        "numero": "",
                        "ano": "",
                        "titulo": title or "",
                        "url_detalhe": detail_url or "",
                        "url_pdf": "",
                        "id_dr": "",
                        "old_hash": "",
                        "new_hash": "",
                        "match_keyword": "",
                        "match_where": "",
                        "text_source": "rss" if not detail_url else "detail_html",
                        "text_len": len((f"{title} {desc}" or "").strip()),
                        "reason": "fetch detail failed",
                    }
                )
                continue

            meta = extract_meta_from_detail_html(
                detail_html,
                detail_url=detail_url,
                rss_title=title,
                rss_desc=desc,
                dump_html_shell=dump_html_shell,
            )

            pdf_url: str | None = find_best_pdf_link(detail_html, detail_url=detail_url)

            # se continuarmos em shell e sem pdf_url, tentar refetch com headers browser
            if meta.get("html_shell") and not pdf_url:
                try:
                    detail_html2 = http_get(detail_url, force_browser_headers=True)
                    meta2 = extract_meta_from_detail_html(
                        detail_html2,
                        detail_url=detail_url,
                        rss_title=title,
                        rss_desc=desc,
                        dump_html_shell=dump_html_shell,
                    )
                    detail_html = detail_html2
                    meta = meta2
                    pdf_url = find_best_pdf_link(detail_html, detail_url=detail_url)
                except FetchError:
                    pass

            filtro_texto = f"{title} {desc} {meta.get('titulo', '')} {meta.get('sumario', '')}"
            hit, is_generic, has_ctx = match_keywords_hit_quality(filtro_texto, keywords)

            # Genérica sem contexto -> valida no PDF antes de aceitar
            if hit and is_generic and not has_ctx and pdf_url:
                if debug:
                    logger.debug("🧹 Hit genérico sem contexto no texto: hit=%r | vai validar no PDF", hit)
                hit = None

            pdf_hit: str | None = None
            pdf_excerpt = ""

            if not hit and pdf_url:
                pdf_hit, pdf_excerpt = try_keyword_match_via_pdf(
                    pdf_url, keywords, max_pages=pdf_fallback_pages, keywords_enabled=keywords_enabled
                )
                if pdf_hit:
                    hit = pdf_hit
                    if debug:
                        logger.debug("✅ Keyword match via PDF (%s): %s", hit, detail_url)
                        logger.debug("   pdf_excerpt: %s", pdf_excerpt)

            # --- B-3: relatório mais informativo ---
            meta_titulo = meta.get("titulo") or ""
            meta_sumario = meta.get("sumario") or ""
            match_keyword = hit or ""
            used_pdf = bool(pdf_hit)
            match_where, text_source = _infer_match_where_and_source(
                hit=match_keyword,
                rss_title=title,
                rss_desc=desc,
                meta_titulo=meta_titulo,
                meta_sumario=meta_sumario,
                used_pdf=used_pdf,
            )
            text_len = _text_len_for_report(
                rss_title=title,
                rss_desc=desc,
                meta_titulo=meta_titulo,
                meta_sumario=meta_sumario,
                pdf_excerpt=pdf_excerpt,
                used_pdf=used_pdf,
            )
            # -------------------------------------------

            if keywords_enabled and not hit:
                rejected_kw += 1
                if debug:
                    prev = (filtro_texto or "").replace("\n", " ")
                    logger.debug("❌ Rejeitado por keywords: %s", detail_url)
                    logger.debug("   preview: %s", prev[:220])
                    logger.debug("   html_shell=%s | pdf_url=%s", bool(meta.get("html_shell")), pdf_url or "")
                report_rows.append(
                    {
                        "status": "rejeitado",
                        "manual": "nao",
                        "tipo": "",
                        "numero": "",
                        "ano": "",
                        "titulo": (meta.get("titulo") or title or ""),
                        "url_detalhe": detail_url or "",
                        "url_pdf": pdf_url or "",
                        "id_dr": "",
                        "old_hash": "",
                        "new_hash": "",
                        "match_keyword": "",
                        "match_where": "",
                        "text_source": "pdf" if pdf_url else "detail_html",
                        "text_len": text_len,
                        "reason": "no keyword hit",
                    }
                )
                continue

            if debug:
                logger.debug("✅ Keyword match (%s): %s", hit, detail_url)

            id_dr = extract_id_dr(detail_url)

            tipo, numero, ano = parse_tipo_numero_ano(meta.get("titulo") or "")
            if not (tipo and numero and ano):
                tipo, numero, ano = parse_tipo_numero_ano(title)
            if not (tipo and numero and ano):
                tipo, numero, ano = parse_tipo_numero_ano(meta.get("sumario") or "")

            if not (tipo and numero and ano):
                rejected_parse += 1
                if debug:
                    logger.debug("❌ Rejeitado por parse tipo/numero/ano: %s", detail_url)
                    logger.debug("   titulo: %s", (meta.get("titulo", "") or "")[:140])
                report_rows.append(
                    {
                        "status": "rejeitado",
                        "manual": "nao",
                        "tipo": "",
                        "numero": "",
                        "ano": "",
                        "titulo": (meta.get("titulo") or title or ""),
                        "url_detalhe": detail_url or "",
                        "url_pdf": pdf_url or "",
                        "id_dr": "",
                        "old_hash": "",
                        "new_hash": "",
                        "match_keyword": match_keyword,
                        "match_where": match_where,
                        "text_source": text_source,
                        "text_len": text_len,
                        "reason": "parse tipo/numero/ano failed",
                    }
                )
                continue

            tipo_slug = (
                extract_tipo_from_detail_url(meta.get("url_detalhe") or "")
                or infer_tipo_from_title(meta.get("titulo") or title or "")
                or _norm_tipo_slug(str(tipo))
            )
            reg = {
                "id_dr": id_dr,
                "tipo": tipo,
                "tipo_slug": tipo_slug,
                "numero": str(numero),
                "ano": int(ano),
                "data_publicacao": pub_dt.date().isoformat() if pub_dt else None,
                "titulo": meta.get("titulo") or title or "",
                "sumario": meta.get("sumario") or desc or "",
                "url_detalhe": meta.get("url_detalhe"),
                "url_pdf": pdf_url,
                "url_consolidado": None,
                "resumo_1_frase": "",
                "observacoes": "",
                "estado": "desconhecido",
            }

            # --- B-4: dry-run (não escrever DB) ---
            if dry_run:
                existing = _get_existing(tipo, str(numero), int(ano))
                old_hash = (existing or {}).get("hash_fonte")
                new_hash = make_hash(reg)
                if not existing:
                    status = "novo"
                elif old_hash != new_hash:
                    status = "atualizado"
                else:
                    status = "inalterado"
                is_manual = _is_manual(existing, reg)
            else:
                status, is_manual, old_hash, new_hash = upsert_diploma(reg)

            report_rows.append(
                {
                    "status": status,
                    "manual": "sim" if is_manual else "nao",
                    "tipo": tipo,
                    "numero": str(numero),
                    "ano": int(ano),
                    "titulo": reg.get("titulo", ""),
                    "url_detalhe": reg.get("url_detalhe", ""),
                    "url_pdf": reg.get("url_pdf", "") or "",
                    "id_dr": id_dr or "",
                    "old_hash": old_hash or "",
                    "new_hash": new_hash or "",
                    "match_keyword": match_keyword,
                    "match_where": match_where,
                    "text_source": text_source,
                    "text_len": text_len,
                    "reason": f"matched in {match_where}" if match_keyword else "",
                }
            )

            # ---- cursor v2: atualizar apenas com itens aceites ----
            if pub_dt:
                if (cursor_dt_work is None) or (pub_dt > cursor_dt_work):
                    cursor_dt_work = pub_dt
                    cursor_links_work = set()
                if pub_dt == cursor_dt_work and link:
                    cursor_links_work.add(link)

                if (newest_pub_seen_accepted is None) or (pub_dt > newest_pub_seen_accepted):
                    newest_pub_seen_accepted = pub_dt
            # ------------------------------------------------------

    # Guardar cursor/checkpoint apenas se houve itens aceites (report_rows)
    if (not dry_run) and cursor_dt_work and report_rows:
        _save_cursor(cursor_dt_work, cursor_links_work)
        if debug:
            logger.debug(
                "✅ Cursor v2 atualizado: dt=%s | links=%d",
                cursor_dt_work.isoformat(),
                len(cursor_links_work),
            )
    elif debug and dry_run and report_rows:
        logger.debug("🧪 Dry-run: Cursor v2 NÃO atualizado (dry-run).")
    elif debug:
        logger.debug("ℹ️ Cursor v2 NÃO atualizado (nenhum item aceite nesta execução).")

    # Checkpoint antigo (compat) — opcional, mas mantemos
    if (not dry_run) and newest_pub_seen_accepted and report_rows:
        set_state(STATE_KEY_LAST_PUBDATE, newest_pub_seen_accepted.isoformat().replace("+00:00", "Z"))
        if debug:
            logger.debug("✅ Checkpoint atualizado para: %s", newest_pub_seen_accepted.isoformat())
    elif debug and dry_run and report_rows:
        logger.debug("🧪 Dry-run: Checkpoint NÃO atualizado (dry-run).")
    elif debug:
        logger.debug("ℹ️ Checkpoint NÃO atualizado (nenhum item aceite nesta execução).")

    counts = {"novo": 0, "atualizado": 0, "inalterado": 0}
    manual_count = 0
    for r in report_rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        manual_count += 1 if r["manual"] == "sim" else 0

    logger.info("🔗 Links de detalhe/ELI encontrados (bruto): %d", total_links)
    logger.info("📄 Itens com PDF direto usados: %d", total_pdf_direct)
    if fail_fetch:
        logger.warning("⚠️ Falhas de fetch: %d", fail_fetch)
    if debug:
        logger.debug("🧪 Rejeitados: keywords=%d | parse=%d", rejected_kw, rejected_parse)

    logger.info("📊 Resultado (filtrados + processados):")
    logger.info("   - novos: %d", counts["novo"])
    logger.info("   - atualizados: %d", counts["atualizado"])
    logger.info("   - inalterados: %d", counts["inalterado"])
    logger.info("   - manuais (entre os processados): %d", manual_count)

    if report_rows:
        report_path = write_report(report_rows)
        logger.info("🧾 Relatório guardado: %s", report_path)
    else:
        logger.info("🧾 Relatório não gerado (nada processado nesta execução).")


DEFAULT_KEYWORDS = [
    "energia renovável",
    "energias renováveis",
    "autoconsumo",
    "UPAC",
    "UPP/UPAC",
    "comunidades de energia",
    "hidrogénio",
    "solar",
    "fotovolta",
    "eólico",
    "armazenamento",
    "BESS",
    "onshore",
    "offshore",
    "biomassa",
    "garantias de origem",
    "TRC ",
    "Título de Reserva de Capacidade",
    "Sistema Elétrico Nacional",
    "sistema elétrico nacional",
    "rede elétrica",
    "produção renovável",
    "carbono",
]

DEFAULT_PROFILES: dict[str, dict] = {
    "renovaveis_portarias": {
        "days": 14,
        "types": ["portaria"],
        "pdf_fallback_pages": 8,
        "no_keywords": False,
    },
    "renovaveis_todos": {
        "days": 14,
        "types": [],
        "pdf_fallback_pages": 8,
        "no_keywords": False,
    },
    "sem_keywords_portarias": {
        "days": 14,
        "types": ["portaria"],
        "pdf_fallback_pages": 8,
        "no_keywords": True,
    },
}


def load_profiles(*, extra_path: Path | None = None) -> dict[str, dict]:
    """Carrega perfis de `src/config/profiles.json` (ou override via extra_path).

    Se o ficheiro não existir, usa DEFAULT_PROFILES como fallback.
    """
    # src/collectors/ -> src/
    base = Path(__file__).resolve().parents[1]
    default_path = base / "config" / "profiles.json"
    path = extra_path or default_path

    if not path.exists():
        return dict(DEFAULT_PROFILES)

    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("profiles.json tem de ser um objeto JSON (dict) no topo")
        # normalizar para dict[str, dict]
        out: dict[str, dict] = {}
        for k, v in obj.items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            out[k.strip()] = v
        return out or dict(DEFAULT_PROFILES)
    except Exception as e:
        logger.warning("⚠️ Falha a ler profiles (%s): %s", path, e)
        return dict(DEFAULT_PROFILES)


# Profiles efetivos (externos, com fallback) são carregados em runtime via load_profiles().


def apply_profile_to_args(
    args: argparse.Namespace,
    *,
    extra_path: Path | None = None,
) -> None:
    """Aplica defaults de um --profile, sem sobrescrever flags explícitas.

    Precedência:
      flags explícitas (presentes em sys.argv) > profile > defaults do argparse
    """
    profile = (getattr(args, "profile", "") or "").strip()
    if not profile:
        return

    profiles = load_profiles(extra_path=extra_path)
    cfg = profiles.get(profile)
    if not cfg:
        raise SystemExit(f"Profile desconhecido: {profile}. Disponíveis: {', '.join(sorted(profiles))}")

    def has(flag: str) -> bool:
        return flag in sys.argv

    if (not has("--days")) and ("days" in cfg):
        args.days = int(cfg["days"])

    if (not has("--types")) and ("types" in cfg):
        v = cfg.get("types", "")
        if isinstance(v, list):
            args.types = ",".join(str(x).strip() for x in v if str(x).strip())
        else:
            args.types = str(v or "")

    if (not has("--exclude-types")) and ("exclude_types" in cfg):
        v = cfg.get("exclude_types", "")
        if isinstance(v, list):
            args.exclude_types = ",".join(str(x).strip() for x in v if str(x).strip())
        else:
            args.exclude_types = str(v or "")

    if (not has("--strict-types")) and ("strict_types" in cfg):
        args.strict_types = bool(cfg.get("strict_types"))

    if (not has("--pdf-fallback-pages")) and ("pdf_fallback_pages" in cfg):
        args.pdf_fallback_pages = int(cfg["pdf_fallback_pages"])

    # keywords vs no-keywords: se user passou flags explícitas, não mexemos
    if has("--no-keywords") or has("--keywords"):
        return

    if cfg.get("no_keywords") is True:
        args.no_keywords = True
    elif cfg.get("no_keywords") is False:
        args.no_keywords = False

    if ("keywords" in cfg) and (cfg.get("keywords") is not None):
        args.keywords = list(cfg.get("keywords") or [])

    logger.info(
        "📌 Profile aplicado: %s (days=%s, types=%s, exclude_types=%s, strict_types=%s, no_keywords=%s, pdf_fallback_pages=%s)",
        profile,
        getattr(args, "days", None),
        getattr(args, "types", "") or "—",
        getattr(args, "exclude_types", "") or "—",
        getattr(args, "strict_types", False),
        getattr(args, "no_keywords", False),
        getattr(args, "pdf_fallback_pages", None),
    )

    def has(flag: str) -> bool:
        return flag in sys.argv

    if (not has("--days")) and ("days" in cfg):
        args.days = int(cfg["days"])

    if (not has("--types")) and ("types" in cfg):
        v = cfg.get("types", "")
        if isinstance(v, list):
            args.types = ",".join(str(x).strip() for x in v if str(x).strip())
        else:
            args.types = str(v or "")
    if (not has("--exclude-types")) and ("exclude_types" in cfg):
        v = cfg.get("exclude_types", "")
        if isinstance(v, list):
            args.exclude_types = ",".join(str(x).strip() for x in v if str(x).strip())
        else:
            args.exclude_types = str(v or "")

    if (not has("--strict-types")) and ("strict_types" in cfg):
        args.strict_types = bool(cfg.get("strict_types"))

    if (not has("--pdf-fallback-pages")) and ("pdf_fallback_pages" in cfg):
        args.pdf_fallback_pages = int(cfg["pdf_fallback_pages"])

    # keywords vs no-keywords: se user passou flags explícitas, não mexemos
    if has("--no-keywords") or has("--keywords"):
        return
    if cfg.get("no_keywords") is True:
        args.no_keywords = True
    elif cfg.get("no_keywords") is False:
        args.no_keywords = False
    if ("keywords" in cfg) and (cfg.get("keywords") is not None):
        args.keywords = list(cfg.get("keywords") or [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--profile",
        default="",
        help="Preset de parâmetros (ex.: renovaveis_portarias). Flags explícitas ganham ao profile.",
    )
    ap.add_argument("--days", type=int, default=90, help="Janela temporal em dias (ex.: 90)")
    ap.add_argument(
        "--force-full-window", action="store_true", help="Ignora checkpoint e reprocessa toda a janela"
    )
    ap.add_argument(
        "--reset-checkpoint", action="store_true", help="Apaga o checkpoint incremental e reprocessa"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Processa e gera relatório, mas não grava na DB nem atualiza checkpoint/cursor",
    )
    ap.add_argument(
        "--dump-html-shell", action="store_true", help="Guarda HTML quando o detalhe vem como shell"
    )
    ap.add_argument("--debug", action="store_true", help="Modo debug (mais logs)")
    ap.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    ap.add_argument(
        "--pdf-fallback-pages", type=int, default=8, help="N.º máximo de páginas para extrair texto do PDF"
    )

    ap.add_argument(
        "--no-keywords",
        action="store_true",
        help="Ignora qualquer filtro por keywords (incluindo keywords default). Aceita todos os itens válidos.",
    )
    ap.add_argument(
        "--keywords",
        nargs="*",
        default=DEFAULT_KEYWORDS,
        help="Lista de palavras-chave",
    )
    ap.add_argument(
        "--types",
        default="",
        help="Filtra por tipo(s) de diploma (slug do DR). Ex: --types portaria,decreto-lei. Vazio = aceita todos.",
    )
    ap.add_argument(
        "--exclude-types",
        default="",
        help="Exclui tipo(s) de diploma (slug do DR). Ex: --exclude-types despacho,declaração. Vazio = não exclui.",
    )
    ap.add_argument(
        "--strict-types",
        action="store_true",
        help="Quando --types está definido, também rejeita itens com tipo desconhecido.",
    )

    ap.add_argument(
        "--list-profiles",
        action="store_true",
        help="Lista os perfis disponíveis (lidos de profiles.json ou fallback) e termina.",
    )
    ap.add_argument(
        "--profiles-path",
        default="",
        help="Caminho para profiles.json alternativo (override).",
    )

    args = ap.parse_args()

    # 1) Configurar logging antes de aplicar profile (para o log "📌 Profile aplicado" aparecer)
    setup_logging(args.log_level or ("DEBUG" if args.debug else None))

    profiles_path = Path(args.profiles_path) if (args.profiles_path or "").strip() else None

    if args.list_profiles:
        profiles = load_profiles(extra_path=profiles_path)
        print("Perfis disponíveis:")
        for name in sorted(name for name in profiles if name.strip()):
            print(f" - {name}")
        return

    # 2) Aplicar defaults do profile (flags explícitas continuam a ganhar)
    apply_profile_to_args(args, extra_path=profiles_path)

    keywords_enabled = not args.no_keywords
    if keywords_enabled:
        logger.info("🔎 Filtro por keywords ATIVO")
    else:
        logger.info("🚫 Filtro por keywords DESATIVADO (--no-keywords ativo)")

    def _parse_types_csv(s: str) -> list[str]:
        parts = [p.strip().lower() for p in (s or "").split(",")]
        return [p for p in parts if p]

    include_types = _parse_types_csv(getattr(args, "types", ""))
    exclude_types = _parse_types_csv(getattr(args, "exclude_types", ""))

    if include_types:
        logger.info("🏷️  Filtro por tipo ATIVO (include): %s", ", ".join(include_types))
    if exclude_types:
        logger.info("🏷️  Filtro por tipo ATIVO (exclude): %s", ", ".join(exclude_types))
    if getattr(args, "strict_types", False) and include_types:
        logger.info("🏷️  strict-types=ON (tipo desconhecido será rejeitado)")

    # reduzir ruído de libs externas
    logging.getLogger("charset_normalizer").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    collect(
        args.keywords,
        days=args.days,
        force_full_window=args.force_full_window,
        debug=args.debug,
        reset_checkpoint_flag=args.reset_checkpoint,
        dry_run=args.dry_run,
        dump_html_shell=args.dump_html_shell,
        pdf_fallback_pages=args.pdf_fallback_pages,
        keywords_enabled=keywords_enabled,
        include_types=include_types,
        exclude_types=exclude_types,
        strict_types=getattr(args, "strict_types", False),
        profile=None,
    )


if __name__ == "__main__":
    main()
