# src/core/html.py
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .http import _looks_like_outsystems_shell
from .links import _is_detail_link

logger = logging.getLogger(__name__)


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
    dump_dir: Path | None = None,
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


__all__ = ["extract_detail_links_from_index_html", "extract_meta_from_detail_html", "find_best_pdf_link"]
