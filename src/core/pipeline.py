from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from ..config.logging_setup import setup_logging
from ..db.db import DB_PATH, init_db
from ..processing.indexador import upsert_diploma

logger = logging.getLogger(__name__)

RSS_SERIE1_HTML = "https://files.diariodarepublica.pt/rss/serie1-html.xml"

# -----------------------------
# Public model (used by tests)
# -----------------------------


@dataclass(frozen=True)
class CollectResult:
    """Resultado da recolha.

    Campos mínimos usados pelos testes:
      - success
      - error
      - rss_items_total
      - rss_items_window

    Campos extra ajudam no reporting/CLI.
    """

    success: bool
    error: str | None
    rss_items_total: int
    rss_items_window: int

    processed: int = 0
    novos: int = 0
    atualizados: int = 0
    inalterados: int = 0
    manuais: int = 0

    report_path: Path | None = None


# -----------------------------
# Helpers + monkeypatch hooks
# -----------------------------


def http_get(url: str, *, timeout: int = 25, headers: dict[str, str] | None = None) -> bytes:
    """GET básico (patchável nos testes)."""
    hdrs = {"User-Agent": "Mozilla/5.0"}
    if headers:
        hdrs.update(headers)
    r = requests.get(url, timeout=timeout, headers=hdrs)
    r.raise_for_status()
    return r.content


def _parse_pubdate_raw(raw: str | None) -> datetime | None:
    if not raw:
        return None
    # RSS do DR costuma trazer RFC822, mas nos testes vem ISO.
    raw = raw.strip()
    try:
        # ISO 8601
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except Exception:
        pass

    # RFC822 (email.utils.parsedate_to_datetime seria o ideal, mas evitamos import extra)
    # Ex.: Tue, 14 Jan 2026 10:00:00 GMT
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_rss_items(rss_xml: str) -> list[dict[str, Any]]:
    """Parseia RSS XML -> lista de itens (patchável nos testes)."""
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(rss_xml)
    except Exception:
        return items

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub_raw = (item.findtext("pubDate") or "").strip()
        pub_utc = _parse_pubdate_raw(pub_raw)

        # alguns feeds trazem PDF num <enclosure url="...">
        pdf_url = None
        enc = item.find("enclosure")
        if enc is not None:
            pdf_url = (enc.attrib.get("url") or "").strip() or None

        items.append(
            {
                "title": title,
                "link": link or None,
                "description": desc or None,
                "pubDate_raw": pub_raw or None,
                "pubDate_utc": pub_utc,
                "pdf_url": pdf_url,
            }
        )

    return items


def try_keyword_match_via_pdf(*_a: Any, **_k: Any) -> tuple[str | None, str]:
    """Hook para futura validação via PDF.

    No projeto atual, devolve sempre (None, ""). Nos testes é monkeypatched.
    """

    return None, ""


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    """Escreve relatório CSV (patchável nos testes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "pubDate_utc",
        "tipo",
        "numero",
        "ano",
        "titulo",
        "link",
        "pdf_url",
        "status",
        "manual",
        "note",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})


def _ensure_reports_dir(report_dir: Path | None) -> Path:
    base = report_dir or (Path(os.getenv("DATA_DIR", "data")) / "reports")
    base.mkdir(parents=True, exist_ok=True)
    return base


_RE_TITLE = re.compile(
    r"^\s*(?P<tipo>.+?)\s+n\s*[\.ºo]?\s*(?P<num>\d+)\s*/\s*(?P<ano>\d{4})(?:\s*/\s*(?P<sub>\d+))?",
    re.IGNORECASE,
)


def _infer_from_title(title: str) -> tuple[str | None, str | None, int | None]:
    if not title:
        return None, None, None
    # Alguns títulos chegam com mojibake; mesmo assim o "n" e números aparecem.
    m = _RE_TITLE.match(title)
    if not m:
        return None, None, None

    tipo_raw = (m.group("tipo") or "").strip()
    numero = (m.group("num") or "").strip()
    ano_s = (m.group("ano") or "").strip()
    sub = (m.group("sub") or "").strip()

    # tipo = slug simples para compatibilidade com DB
    tipo_slug = _slugify_ascii_kebab(tipo_raw)

    # se houver sub-número (ex. "2/2026/1"), guarda como "2/1" (ano é campo próprio)
    if sub:
        numero = f"{numero}/{sub}"

    try:
        ano = int(ano_s)
    except ValueError:
        ano = None

    return tipo_slug or None, numero or None, ano


def _infer_from_link(link: str | None) -> tuple[str | None, str | None, int | None, str | None]:
    """Tenta inferir (tipo_slug, numero, ano, id_dr) a partir do URL detalhe."""
    if not link:
        return None, None, None, None

    # Ex.: https://diariodarepublica.pt/dr/detalhe/decreto-lei/9-2026-1006464123
    m = re.search(r"/detalhe/(?P<tipo>[^/]+)/(?P<num>\d+)-(?:\d{4})-(?P<id>\d+)", link)
    if m:
        tipo = m.group("tipo")
        numero = m.group("num")
        # tenta extrair ano do segmento (num-ano-id)
        m2 = re.search(r"/detalhe/[^/]+/(?P<num>\d+)-(?P<ano>\d{4})-(?P<id>\d+)", link)
        ano = int(m2.group("ano")) if m2 else None
        return tipo, numero, ano, m.group("id")

    # Ex.: .../detalhe/declaracao-retificacao/2-1006464124 (sem ano explícito)
    m = re.search(r"/detalhe/(?P<tipo>[^/]+)/(?P<num>\d+)-(?P<id>\d+)", link)
    if m:
        return m.group("tipo"), m.group("num"), None, m.group("id")

    return None, None, None, None


def _slugify_ascii_kebab(s: str) -> str:
    # versão leve (evita import circular). Para consistência final, indexador normaliza.
    s = (s or "").strip().lower()
    s = s.replace("ª", "a").replace("º", "o")
    # remove diacríticos
    try:
        import unicodedata

        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
    except Exception:
        pass

    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _normalize_upsert_result(res: Any) -> tuple[str, bool, str | None, str | None]:
    """Normaliza retorno do upsert.

    upsert_diploma real -> (status, is_manual, old_hash, new_hash)
    testes podem devolver dict com chaves 'status'/'manual'.
    """

    if isinstance(res, dict):
        status = str(res.get("status") or "")
        manual = bool(res.get("manual") or False)
        tipo_out = res.get("tipo")
        numero_out = res.get("numero")
        return (
            status,
            manual,
            (str(tipo_out) if tipo_out else None),
            (str(numero_out) if numero_out else None),
        )

    if isinstance(res, tuple | list) and len(res) >= 2:
        status = str(res[0])
        manual = bool(res[1])
        # os 2 últimos campos do upsert real são hashes; para reporting
        return status, manual, None, None

    return str(res), False, None, None


def collect(
    profile: str | None = None,
    *,
    days: int = 7,
    dry_run: bool = False,
    force_full_window: bool = False,
    keywords_enabled: bool = True,
    report_dir: Path | None = None,
    rss_url: str = RSS_SERIE1_HTML,
) -> CollectResult:
    """Executa recolha RSS -> DB.

    O parâmetro `profile` existe por compatibilidade com chamadas antigas.
    """

    _ = profile

    try:
        init_db()

        rss_bytes = http_get(rss_url)
        # o feed do DR pode vir em bytes; assume UTF-8 mas tolera erros
        xml_text = rss_bytes.decode("utf-8", errors="replace")

        items = parse_rss_items(xml_text)
        total = len(items)

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        window = [
            it
            for it in items
            if force_full_window
            or (isinstance(it.get("pubDate_utc"), datetime) and it["pubDate_utc"] >= cutoff)
        ]

        logger.info("📡 RSS lido: %s itens", total)
        logger.info("🗓️ Janela: últimos %s dias (cutoff UTC: %s)", days, cutoff.isoformat())
        if force_full_window:
            logger.info("♻️ Force full window: ON (ignorar checkpoint)")
        logger.info("➡️ Itens a processar nesta execução: %s", len(window))

        links_found = sum(1 for it in window if it.get("link"))
        pdfs_found = sum(1 for it in window if it.get("pdf_url"))
        logger.info("🔗 Links de detalhe/ELI encontrados (bruto): %s", links_found)
        logger.info("📄 Itens com PDF direto usados: %s", pdfs_found)

        novos = atualizados = inalterados = manuais = processed = 0
        report_rows: list[dict[str, Any]] = []

        for it in window:
            title = (it.get("title") or "").strip()
            link = (it.get("link") or "").strip() or None
            pdf_url = (it.get("pdf_url") or "").strip() or None
            pub_utc = it.get("pubDate_utc")

            tipo, numero, ano = _infer_from_title(title)
            tipo2, numero2, ano2, id_dr = _infer_from_link(link)

            tipo = tipo or tipo2
            numero = numero or numero2
            ano = ano or ano2 or (pub_utc.year if isinstance(pub_utc, datetime) else None)

            if not (tipo and numero and ano):
                report_rows.append(
                    {
                        "pubDate_utc": pub_utc.isoformat() if isinstance(pub_utc, datetime) else None,
                        "tipo": tipo,
                        "numero": numero,
                        "ano": ano,
                        "titulo": title,
                        "link": link,
                        "pdf_url": pdf_url,
                        "status": "skipped",
                        "manual": False,
                        "note": "missing tipo/numero/ano",
                    }
                )
                continue

            # keywords (opcional)
            if keywords_enabled:
                _kw_match, _kw_note = try_keyword_match_via_pdf(pdf_url=pdf_url, link=link, title=title)
                # Por agora não rejeitamos com base no PDF aqui; o hook existe para evoluir.

            reg: dict[str, Any] = {
                "tipo": tipo,
                "numero": str(numero),
                "ano": int(ano),
                "data_publicacao": None,
                "titulo": title or None,
                "sumario": (it.get("description") or None),
                "url_detalhe": link,
                "url_pdf": pdf_url,
                "tipo_slug": tipo2 or tipo,
                "id_dr": id_dr,
            }

            if dry_run:
                processed += 1
                report_rows.append(
                    {
                        "pubDate_utc": pub_utc.isoformat() if isinstance(pub_utc, datetime) else None,
                        "tipo": tipo,
                        "numero": str(numero),
                        "ano": int(ano),
                        "titulo": title,
                        "link": link,
                        "pdf_url": pdf_url,
                        "status": "dry_run",
                        "manual": False,
                        "note": None,
                    }
                )
                continue

            try:
                up_res = upsert_diploma(reg, db_path=DB_PATH)
            except Exception as e:
                report_rows.append(
                    {
                        "pubDate_utc": pub_utc.isoformat() if isinstance(pub_utc, datetime) else None,
                        "tipo": tipo,
                        "numero": str(numero),
                        "ano": int(ano),
                        "titulo": title,
                        "link": link,
                        "pdf_url": pdf_url,
                        "status": "error",
                        "manual": False,
                        "note": f"{type(e).__name__}: {e}",
                    }
                )
                continue

            status, is_manual, _tipo_out, _numero_out = _normalize_upsert_result(up_res)
            processed += 1
            if is_manual:
                manuais += 1

            # compat: status pode vir em PT (indexador) ou EN (teste)
            if status in {"novo", "new"}:
                novos += 1
            elif status in {"atualizado", "updated"}:
                atualizados += 1
            else:
                inalterados += 1

            report_rows.append(
                {
                    "pubDate_utc": pub_utc.isoformat() if isinstance(pub_utc, datetime) else None,
                    "tipo": tipo,
                    "numero": str(numero),
                    "ano": int(ano),
                    "titulo": title,
                    "link": link,
                    "pdf_url": pdf_url,
                    "status": status,
                    "manual": bool(is_manual),
                    "note": None,
                }
            )

        rep_dir = _ensure_reports_dir(report_dir)
        report_path = rep_dir / f"relatorio_coleta_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        write_report(report_path, report_rows)

        logger.info("📊 Resultado (filtrados + processados):")
        logger.info("   - novos: %s", novos)
        logger.info("   - atualizados: %s", atualizados)
        logger.info("   - inalterados: %s", inalterados)
        logger.info("   - manuais (entre os processados): %s", manuais)
        logger.info("🧾 Relatório guardado: %s", report_path)
        logger.info(
            "✅ Fim: novos=%s atualizados=%s inalterados=%s processados=%s (lidos=%s)",
            novos,
            atualizados,
            inalterados,
            processed,
            total,
        )

        return CollectResult(
            success=True,
            error=None,
            rss_items_total=total,
            rss_items_window=len(window),
            processed=processed,
            novos=novos,
            atualizados=atualizados,
            inalterados=inalterados,
            manuais=manuais,
            report_path=report_path,
        )

    except Exception as e:
        return CollectResult(
            success=False,
            error=f"{type(e).__name__}: {e}",
            rss_items_total=0,
            rss_items_window=0,
        )


# -----------------------------
# CLI
# -----------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.core.pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="Executa a recolha (RSS -> DB)")
    c.add_argument("--days", type=int, default=7)
    c.add_argument("--force-full-window", action="store_true")
    c.add_argument("--no-keywords", action="store_true")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--log-level", default="INFO")

    args = p.parse_args(argv)

    setup_logging(args.log_level)

    if args.cmd == "collect":
        res = collect(
            None,
            days=args.days,
            dry_run=bool(args.dry_run),
            force_full_window=bool(args.force_full_window),
            keywords_enabled=not bool(args.no_keywords),
        )
        return 0 if res.success else 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
