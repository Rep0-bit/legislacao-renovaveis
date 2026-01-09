# src/core/diploma_parse.py
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


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


__all__ = [
    "parse_tipo_numero_ano",
    "extract_id_dr",
    "infer_tipo_from_title",
    "extract_tipo_from_detail_url",
    "should_filter_by_tipo",
    "_norm_tipo_slug",
    "_filter_items",
]
