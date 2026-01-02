# src/collectors/coletor_dr_serie1_rss.py
from __future__ import annotations

import argparse
import csv
import email.utils
import json
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..config.logging_setup import setup_logging
from ..db.db import get_conn, init_db
from ..processing.indexador import upsert_diploma

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0"}
RSS_SERIE1_HTML = "https://files.diariodarepublica.pt/rss/serie1-html.xml"
STATE_KEY_LAST_PUBDATE = "serie1_html_last_pubdate_utc"


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
def http_get(url: str, timeout: int = 60) -> bytes:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            raise FetchError(url, f"HTTP {r.status_code}", status_code=r.status_code)
        return r.content
    except requests.exceptions.Timeout as e:
        logger.debug("⏱️ Timeout a pedir %s (timeout=%ss)", url, timeout)
        raise FetchError(url, f"Timeout ({timeout}s)") from e
    except requests.exceptions.RequestException as e:
        logger.debug("🌐 RequestException a pedir %s: %s", url, e)
        raise FetchError(url, f"RequestException: {e}") from e


# -----------------------------
# State (checkpoint)
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


def clear_state(key: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM collector_state WHERE key=?", (key,))
        conn.commit()


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
    m = re.search(r"\bde\s+(\d{4})-(\d{2})-(\d{2})\b", t)
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
    child = el.find(local_name)
    if child is not None and child.text:
        return child.text.strip()
    for c in list(el):
        if isinstance(c.tag, str) and (c.tag == local_name or c.tag.endswith("}" + local_name)):
            return (c.text or "").strip()
    return ""


def _find_link(el: ET.Element) -> str:
    link = _find_text(el, "link")
    if link:
        return link
    for c in list(el):
        if not isinstance(c.tag, str):
            continue
        if c.tag == "link" or c.tag.endswith("}link"):
            href = (c.attrib or {}).get("href", "").strip()
            if href:
                return href
    return _find_text(el, "guid")


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

        items.append(
            {
                "title": title,
                "link": _find_link(item),
                "pubDate": pub_raw,
                "pubDate_utc": pub_dt,
                "description": _find_text(item, "description"),
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


# -----------------------------
# Extract act links from daily index HTML
# -----------------------------
def extract_detail_links_from_index_html(index_html: bytes, base_url: str) -> list[str]:
    soup = _make_soup(index_html)
    links = set()

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        full = urljoin(base_url, href)
        if "/dr/detalhe/" in full or "/eli/" in full and "diario" not in full:
            links.add(full)

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
    t = (title or "").strip()
    if not t:
        return None, None, None

    t_norm = re.sub(r"\bn[.\s]*[ºo]\b", "n.º", t, flags=re.IGNORECASE)
    t_norm = re.sub(r"\s+", " ", t_norm).strip()

    m = re.search(
        r"(?P<tipo>.+?)\s+n\.º\s+(?P<num>\d+(?:-[A-Z])?)\s*/\s*(?P<ano>\d{4})(?:\s*/\s*(?P<extra>[0-9A-Z]+(?:-[0-9A-Z]+)?))?",
        t_norm,
        flags=re.IGNORECASE,
    )
    if m:
        tipo = _clean_tipo(m.group("tipo"))
        ano = int(m.group("ano"))
        numero = _canon_numero(m.group("num"), m.group("extra"))
        return tipo, numero, ano

    m2 = re.search(
        r"(?P<tipo>.+?)\s+n\.º\s+(?P<all>\d+(?:-[A-Z])?/\d{4}(?:/[0-9A-Z]+(?:-[0-9A-Z]+)?)?)",
        t_norm,
        flags=re.IGNORECASE,
    )
    if m2:
        tipo = _clean_tipo(m2.group("tipo"))
        all_ = m2.group("all").upper()
        parts = all_.split("/")
        if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 4:
            ano = int(parts[1])
            num = parts[0]
            extra = parts[2] if len(parts) >= 3 else None
            numero = _canon_numero(num, extra)
            return tipo, numero, ano

    return None, None, None


# -----------------------------
# Extract id_dr and pdf URL
# -----------------------------
def extract_id_dr(url: str) -> str | None:
    m = re.search(r"-(\d+)$", (url or "").rstrip("/"))
    return m.group(1) if m else None


def find_best_pdf_link(detail_html: bytes, detail_url: str) -> str | None:
    soup = _make_soup(detail_html)

    pdf_candidates: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if ".pdf" in href.lower():
            pdf_candidates.append(urljoin(detail_url, href))

    for u in pdf_candidates:
        if "files.diariodarepublica.pt" in u or "files.dre.pt" in u:
            return u
    if pdf_candidates:
        return pdf_candidates[0]

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
# Robust title extraction helpers
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


# -----------------------------
# Extract minimal metadata from "detalhe"
# -----------------------------
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

    # 1) H1 (ideal)
    h1 = soup.find("h1")
    titulo = h1.get_text(" ", strip=True) if h1 else ""

    # 2) Meta tags comuns
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
            sumario = md["content"].strip()

    if not sumario:
        og = soup.find("meta", attrs={"property": "og:description"})
        if og and og.get("content"):
            sumario = og["content"].strip()

    # ⚠️ HTML shell: fallback explícito do RSS (mas mantém log + dump)
    html_shell = False
    if not titulo:
        html_shell = True
        if rss_title:
            titulo = rss_title

    if not sumario and rss_desc:
        # limita o tamanho para manter consistência
        s = " ".join(rss_desc.split())
        sumario = (s[:1200].rstrip() + "…") if len(s) > 1200 else s

    if html_shell:
        logger.debug("🧾 HTML shell (sem título no HTML); a usar título do RSS: %s", detail_url)
        if dump_html_shell:
            dump_dir.mkdir(parents=True, exist_ok=True)
            name = _safe_dump_name(detail_url)
            out_path = dump_dir / f"{name}.html"
            try:
                out_path.write_bytes(detail_html)
                logger.debug("🧪 HTML shell guardado em: %s", out_path)
            except Exception as e:
                logger.debug("🧪 Falha a guardar HTML shell (%s): %s", out_path, e)

    return {"titulo": titulo, "sumario": sumario, "url_detalhe": detail_url}


# -----------------------------
# Keyword matching (accent-insensitive) + debug-friendly hit
# -----------------------------
def _norm_text(s: str) -> str:
    s = (s or "").strip().casefold()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s


def match_keywords_hit(text: str, keywords: Iterable[str]) -> str | None:
    s = _norm_text(text or "")
    for k in keywords:
        kn = _norm_text(k)
        if kn and kn in s:
            return k
    return None


# -----------------------------
# Report
# -----------------------------
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
    ]

    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        w.writeheader()
        for r in report_rows:
            w.writerow({h: r.get(h, "") for h in headers})

    return out_path


# -----------------------------
# Orchestration helpers
# -----------------------------
def _parse_last_pub_dt() -> datetime | None:
    last_pub_str = get_state(STATE_KEY_LAST_PUBDATE)
    if not last_pub_str:
        return None
    try:
        return datetime.fromisoformat(last_pub_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception as e:
        logger.debug("⏱️ Checkpoint inválido '%s': %s", last_pub_str, e)
        return None


def _filter_items(
    items: list[dict],
    cutoff: datetime,
    last_pub_dt: datetime | None,
    force_full_window: bool,
) -> list[dict]:
    filtered: list[dict] = []
    for it in items:
        dt = it.get("pubDate_utc")

        if dt is None:
            if force_full_window:
                filtered.append(it)
            continue

        if dt < cutoff:
            continue
        if (not force_full_window) and last_pub_dt and dt <= last_pub_dt:
            continue

        filtered.append(it)

    filtered.sort(key=lambda x: x.get("pubDate_utc") or datetime(1970, 1, 1, tzinfo=timezone.utc))
    return filtered


def _is_detail_link(url: str) -> bool:
    u = (url or "").strip()
    return ("/dr/detalhe/" in u) or ("/eli/" in u and "diario" not in u)


# -----------------------------
# Main collector
# -----------------------------
def collect(
    keywords: list[str],
    days: int = 90,
    force_full_window: bool = False,
    debug: bool = False,
    reset_checkpoint: bool = False,
    dump_html_shell: bool = False,
) -> None:
    init_db()

    if reset_checkpoint:
        clear_state(STATE_KEY_LAST_PUBDATE)
        logger.warning("🧹 Checkpoint removido: %s", STATE_KEY_LAST_PUBDATE)

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=days)
    last_pub_dt = _parse_last_pub_dt()

    if debug:
        logger.debug(
            "🔎 Checkpoint atual (last_pub_dt): %s", last_pub_dt.isoformat() if last_pub_dt else "None"
        )

    try:
        rss = http_get(RSS_SERIE1_HTML)
    except FetchError as e:
        logger.error("❌ Falha a obter RSS: %s (%s)", e.url, e)
        return

    items = parse_rss_items(rss)
    filtered = _filter_items(
        items, cutoff=cutoff, last_pub_dt=last_pub_dt, force_full_window=force_full_window
    )

    logger.info("📡 RSS lido: %d itens", len(items))
    logger.info("🗓️ Janela: últimos %d dias (cutoff UTC: %s)", days, cutoff.isoformat())
    if last_pub_dt and not force_full_window:
        logger.info("⏱️ Incremental desde: %s", last_pub_dt.isoformat())
    elif force_full_window:
        logger.info("♻️ Force full window: ON (ignorar checkpoint)")
    logger.info("➡️ Itens a processar nesta execução: %d", len(filtered))

    report_rows: list[dict] = []
    total_links = 0
    newest_pub_seen: datetime | None = last_pub_dt
    fail_fetch = 0
    rejected_kw = 0
    rejected_parse = 0

    for it in filtered:
        link = (it.get("link") or "").strip()
        title = it.get("title") or ""
        desc = it.get("description") or ""
        pub_dt = it.get("pubDate_utc")

        if pub_dt and (newest_pub_seen is None or pub_dt > newest_pub_seen):
            newest_pub_seen = pub_dt

        if not link:
            continue

        detail_urls: list[str] = [link] if _is_detail_link(link) else []
        if not detail_urls:
            try:
                index_html = http_get(link)
                detail_urls = extract_detail_links_from_index_html(index_html, base_url=link)
            except FetchError as e:
                fail_fetch += 1
                logger.warning("⚠️ Falha a abrir link do item: %s (%s)", e.url, e)
                continue

        total_links += len(detail_urls)

        for detail_url in detail_urls:
            try:
                detail_html = http_get(detail_url)
            except FetchError as e:
                fail_fetch += 1
                logger.warning("⚠️ Falha detalhe: %s (%s)", e.url, e)
                continue

            meta = extract_meta_from_detail_html(
                detail_html,
                detail_url=detail_url,
                rss_title=title,
                rss_desc=desc,
                dump_html_shell=dump_html_shell,
            )

            filtro_texto = f"{meta.get('titulo', '')} {meta.get('sumario', '')} {title} {desc}"
            hit = match_keywords_hit(filtro_texto, keywords)
            if not hit:
                rejected_kw += 1
                if debug:
                    prev = (filtro_texto or "").replace("\n", " ")
                    logger.debug("❌ Rejeitado por keywords: %s", detail_url)
                    logger.debug("   preview: %s", prev[:220])
                continue
            else:
                if debug:
                    logger.debug("✅ Keyword match (%s): %s", hit, detail_url)

            pdf_url = find_best_pdf_link(detail_html, detail_url=detail_url)
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
                continue

            reg = {
                "id_dr": id_dr,
                "tipo": tipo,
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
                }
            )

    if newest_pub_seen:
        set_state(STATE_KEY_LAST_PUBDATE, newest_pub_seen.isoformat().replace("+00:00", "Z"))
        if debug:
            logger.debug("✅ Checkpoint atualizado para: %s", newest_pub_seen.isoformat())

    counts = {"novo": 0, "atualizado": 0, "inalterado": 0}
    manual_count = 0
    for r in report_rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        manual_count += 1 if r["manual"] == "sim" else 0

    logger.info("🔗 Links de atos encontrados (bruto): %d", total_links)
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90, help="Janela temporal em dias (ex.: 90)")
    ap.add_argument(
        "--force-full-window", action="store_true", help="Ignora checkpoint e reprocessa toda a janela"
    )
    ap.add_argument(
        "--reset-checkpoint", action="store_true", help="Apaga o checkpoint incremental e reprocessa"
    )
    ap.add_argument(
        "--dump-html-shell", action="store_true", help="Guarda HTML quando o detalhe vem como shell"
    )
    ap.add_argument("--debug", action="store_true", help="Modo debug (mais logs)")
    ap.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    ap.add_argument(
        "--keywords",
        nargs="*",
        default=[
            "energia renovável",
            "energias renováveis",
            "autoconsumo",
            "UPAC",
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
            "TRC",
            "Título de Reserva de Capacidade",
            "SEN",
            "Sistema Elétrico Nacional",
            "rede elétrica",
            "produção renovável",
            "carbono",
        ],
        help="Lista de palavras-chave",
    )
    args = ap.parse_args()

    setup_logging(args.log_level)

    collect(
        args.keywords,
        days=args.days,
        force_full_window=args.force_full_window,
        debug=args.debug,
        reset_checkpoint=args.reset_checkpoint,
        dump_html_shell=args.dump_html_shell,
    )


if __name__ == "__main__":
    main()
