# src/core/report.py
from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import REPORTS_DIR, ensure_app_dirs

logger = logging.getLogger(__name__)

# Colunas estáveis (compatível com relatórios antigos)
REPORT_FIELDS: list[str] = [
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
    "match_keyword",
    "match_where",
    "text_source",
    "text_len",
    "reason",
]


def _infer_match_where_and_source(
    *,
    hit: str,
    rss_title: str,
    rss_desc: str,
    meta_titulo: str,
    meta_sumario: str,
    used_pdf: bool,
) -> tuple[str, str]:
    """Heurística simples para o relatório: onde ocorreu o match e qual a fonte do texto."""
    if used_pdf:
        return ("pdf", "pdf")

    hit_l = (hit or "").lower()
    if hit_l and hit_l in (rss_title or "").lower():
        return ("rss_title", "rss")
    if hit_l and hit_l in (rss_desc or "").lower():
        return ("rss_desc", "rss")
    if hit_l and hit_l in (meta_titulo or "").lower():
        return ("meta_titulo", "detail")
    if hit_l and hit_l in (meta_sumario or "").lower():
        return ("meta_sumario", "detail")

    # fallback (sem match ou hit vazio)
    return ("", "rss")


def _text_len_for_report(
    *,
    rss_title: str,
    rss_desc: str,
    pdf_excerpt: str,
    used_pdf: bool,
) -> int:
    if used_pdf:
        return len((pdf_excerpt or "").strip())
    return len(f"{rss_title} {rss_desc}".strip())


def write_report(rows: list[dict[str, Any]]) -> Path:
    """Escreve CSV do relatório na pasta padrão e devolve o path."""
    ensure_app_dirs()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"relatorio_coleta_{ts}.csv"

    # Normalizar keys: garantir todas as colunas
    normalized: list[dict[str, Any]] = []
    for r in rows:
        out = {k: r.get(k, "") for k in REPORT_FIELDS}
        # manter colunas extra se existirem (sem perder info)
        for k, v in r.items():
            if k not in out:
                out[k] = v
        normalized.append(out)

    # Se aparecerem colunas extra, acrescentar no fim
    fieldnames = list(REPORT_FIELDS)
    for r in normalized:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(normalized)

    logger.debug("🧾 CSV report escrito: %s", path)
    return path


__all__ = ["write_report", "_infer_match_where_and_source", "_text_len_for_report", "REPORT_FIELDS"]
