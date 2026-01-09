# src/core/pipeline.py
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..db.db import init_db
from ..processing.indexador import _get_existing, _is_manual, make_hash, upsert_diploma
from .diploma_parse import (
    _filter_items,
    _norm_tipo_slug,
    extract_id_dr,
    extract_tipo_from_detail_url,
    infer_tipo_from_title,
    parse_tipo_numero_ano,
    should_filter_by_tipo,
)
from .html import extract_detail_links_from_index_html, extract_meta_from_detail_html, find_best_pdf_link
from .http import FetchError, http_get
from .keywords import match_keywords_hit_quality
from .links import _is_detail_link
from .paths import DEBUG_DIR
from .pdf import try_keyword_match_via_pdf
from .report import _infer_match_where_and_source, _text_len_for_report, write_report
from .rss import parse_rss_items
from .state import (
    STATE_KEY_CURSOR,
    STATE_KEY_LAST_PUBDATE,
    _parse_cursor,
    _parse_last_pub_dt,
    _save_cursor,
    delete_state,
    reset_checkpoint,
    set_state,
)

logger = logging.getLogger(__name__)

RSS_SERIE1_PDF = "https://files.diariodarepublica.pt/rss/serie1.xml"

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
        debug_dir = DEBUG_DIR
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


__all__ = ["collect", "DEFAULT_KEYWORDS", "DEFAULT_PROFILES", "load_profiles"]
