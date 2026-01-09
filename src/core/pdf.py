# src/core/pdf.py
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO

from .http import FetchError, http_get
from .keywords import match_keywords_hit_quality

try:
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

logger = logging.getLogger(__name__)


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


__all__ = ["extract_text_from_pdf_bytes", "try_keyword_match_via_pdf"]
