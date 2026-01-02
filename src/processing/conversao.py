# src/processing/conversao.py
from __future__ import annotations

import json
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

HEADERS = {"User-Agent": "Mozilla/5.0"}
DEFAULT_OUT_DIR = Path("data")
BASE_DR = "https://diariodarepublica.pt"


# -----------------------------
# HTTP
# -----------------------------
def download(url: str, timeout: int = 60) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.content


# -----------------------------
# IDs / parsing helpers
# -----------------------------
def make_doc_id_from_url(url: str) -> str:
    """
    Ex: https://diariodarepublica.pt/dr/detalhe/decreto-lei/15-2022-177634016
    -> decreto-lei_15-2022-177634016
    """
    p = urlparse(url).path.rstrip("/")
    m = re.search(r"/dr/detalhe/([^/]+)/([^/]+)$", p)
    if m:
        tipo = m.group(1)
        resto = m.group(2)
        return f"{tipo}_{resto}".replace("/", "_")
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", p.strip("/"))
    return safe[:120] if len(safe) > 120 else safe


def find_best_pdf_link(detail_html: bytes, detail_url: str) -> str | None:
    """
    Tenta encontrar o PDF do ato:
    1) <a href="...pdf"> (preferindo files.diariodarepublica / files.dre)
    2) regex fallback no HTML
    """
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
    return m2.group(0) if m2 else None


def _make_soup(html: bytes) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def extract_visible_text_from_html(html: bytes) -> str:
    soup = _make_soup(html)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def find_consolidated_link(detail_html: bytes, detail_url: str) -> str | None:
    """
    Procura link para legislação consolidada.
    Devolve URL absoluta.
    """
    soup = _make_soup(detail_html)
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if "/dr/legislacao-consolidada/" in href:
            return href if href.startswith("http") else urljoin(BASE_DR, href)

    # fallback: regex (às vezes o link não vem como <a> “normal”)
    s = detail_html.decode("utf-8", errors="ignore")
    m = re.search(r'(/dr/legislacao-consolidada/[^\s"\']+)', s)
    if m:
        return urljoin(BASE_DR, m.group(1))

    return None


# -----------------------------
# PDF -> text
# -----------------------------
def pdf_to_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))

    if getattr(reader, "is_encrypted", False):
        with suppress(Exception):
            reader.decrypt("")

    parts: list[str] = []
    for page in reader.pages:
        t = page.extract_text() or ""
        if t.strip():
            parts.append(t)
    return "\n\n".join(parts).strip()


# -----------------------------
# Main conversion
# -----------------------------
def converter(
    url_detalhe: str,
    url_pdf_direto: str | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> dict:
    """
    Converte um diploma:
      - guarda HTML detalhe
      - guarda PDF (direto ou encontrado)
      - extrai texto do PDF
      - tenta consolidado e extrai texto
      - guarda meta JSON

    Retorna meta dict (também escrito em disco).
    """
    doc_id = make_doc_id_from_url(url_detalhe)

    raw_dir = out_dir / "raw_html"
    pdf_dir = out_dir / "pdfs"
    text_dir = out_dir / "text"
    meta_dir = out_dir / "meta"
    for d in (raw_dir, pdf_dir, text_dir, meta_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1) detalhe
    detalhe_html = download(url_detalhe)
    detalhe_html_path = raw_dir / f"{doc_id}__detalhe.html"
    detalhe_html_path.write_bytes(detalhe_html)

    # 2) pdf
    pdf_url = url_pdf_direto or find_best_pdf_link(detalhe_html, detail_url=url_detalhe)
    pdf_path: Path | None = None
    pdf_text_path: Path | None = None
    pdf_text_ok = False

    if pdf_url:
        pdf_bytes = download(pdf_url)
        pdf_path = pdf_dir / f"{doc_id}.pdf"
        pdf_path.write_bytes(pdf_bytes)

        pdf_text = pdf_to_text(pdf_path)
        pdf_text_path = text_dir / f"{doc_id}__pdf.txt"
        pdf_text_path.write_text(pdf_text, encoding="utf-8")
        pdf_text_ok = bool(pdf_text.strip())

    # 3) consolidado
    cons_url = find_consolidated_link(detalhe_html, detail_url=url_detalhe)
    cons_text_ok = False
    cons_text_path: Path | None = None

    if cons_url:
        cons_html = download(cons_url)
        cons_html_path = raw_dir / f"{doc_id}__consolidado.html"
        cons_html_path.write_bytes(cons_html)

        cons_text = extract_visible_text_from_html(cons_html)
        cons_text_path = text_dir / f"{doc_id}__consolidado.txt"
        cons_text_path.write_text(cons_text, encoding="utf-8")
        cons_text_ok = bool(cons_text.strip())

    meta = {
        "doc_id": doc_id,
        "url_detalhe": url_detalhe,
        "url_pdf": pdf_url,
        "url_consolidado": cons_url,
        "ficheiro_html_detalhe": str(detalhe_html_path),
        "ficheiro_pdf": str(pdf_path) if pdf_path else None,
        "ficheiro_texto_pdf": str(pdf_text_path) if pdf_text_path else None,
        "ficheiro_texto_consolidado": str(cons_text_path) if cons_text_path else None,
        "tem_texto_pdf": pdf_text_ok,
        "tem_texto_consolidado": cons_text_ok,
    }

    meta_path = meta_dir / f"{doc_id}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    meta["ficheiro_meta"] = str(meta_path)

    return meta
