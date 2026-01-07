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


def download(url: str, timeout: int = 60) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.content


def make_doc_id_from_url(url: str | bytes | bytearray | memoryview) -> str:
    # ✅ normaliza para str (aceita bytes/bytearray/memoryview)
    if isinstance(url, memoryview):
        url = url.tobytes()
    url = url.decode("utf-8", errors="ignore") if isinstance(url, bytes | bytearray) else str(url)

    p = urlparse(url).path.rstrip("/")
    m = re.search(r"/dr/detalhe/([^/]+)/([^/]+)$", p)
    if m:
        tipo = m.group(1)
        resto = m.group(2)
        return f"{tipo}_{resto}".replace("/", "_")
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", p.strip("/"))
    return safe[:120] if len(safe) > 120 else safe


def _make_soup(html: bytes) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


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
    return m2.group(0) if m2 else None


def extract_visible_text_from_html(html: bytes) -> str:
    soup = _make_soup(html)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def find_consolidated_link(detail_html: bytes, detail_url: str) -> str | None:
    soup = _make_soup(detail_html)
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if "/dr/legislacao-consolidada/" in href:
            return href if href.startswith("http") else urljoin(BASE_DR, href)

    s = detail_html.decode("utf-8", errors="ignore")
    m = re.search(r'(/dr/legislacao-consolidada/[^\s"\']+)', s)
    if m:
        return urljoin(BASE_DR, m.group(1))

    return None


def pdf_to_text(pdf_path: Path) -> tuple[str, str | None]:
    """
    Extrai texto do PDF de forma resiliente.
    Estratégia:
      1) pypdf
      2) fallback PyMuPDF (fitz)
    Nunca lança exceções: devolve (texto, erro_ou_None).
    """
    # ---------------------------
    # 1) Tentativa com pypdf
    # ---------------------------
    try:
        reader = PdfReader(str(pdf_path))

        if getattr(reader, "is_encrypted", False):
            with suppress(Exception):
                reader.decrypt("")
            with suppress(Exception):
                reader.decrypt(b"")
            with suppress(Exception):
                reader.decrypt(None)

        if getattr(reader, "is_encrypted", False):
            raise RuntimeError("PDF encriptado (pypdf não conseguiu abrir)")

        parts: list[str] = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)

        text = "\n\n".join(parts).strip()
        if text:
            return text, None

        raise RuntimeError("pypdf devolveu texto vazio")

    except Exception as e_pypdf:
        pypdf_err = f"pypdf falhou: {e_pypdf}"

    # ---------------------------
    # 2) Fallback: PyMuPDF (fitz)
    # ---------------------------
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf_path))
        parts: list[str] = []

        for page in doc:
            t = page.get_text()
            if t.strip():
                parts.append(t)

        doc.close()

        text = "\n\n".join(parts).strip()
        if text:
            return text, None

        return "", "PyMuPDF devolveu texto vazio"

    except Exception as e_fitz:
        return "", f"{pypdf_err} | PyMuPDF falhou: {e_fitz}"


def converter(
    url_detalhe: str | bytes | bytearray | memoryview,
    url_pdf_direto: str | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> dict:
    """
    Converter resiliente:
    - tenta guardar HTML/PDF/TXT
    - nunca lança exceções
    - escreve sempre um meta JSON (com erro, se existir)
    """
    if isinstance(url_detalhe, memoryview):
        url_detalhe = url_detalhe.tobytes()
    url_detalhe = (
        url_detalhe.decode("utf-8", errors="ignore")
        if isinstance(url_detalhe, bytes | bytearray)
        else str(url_detalhe)
    )

    doc_id = make_doc_id_from_url(url_detalhe)

    raw_dir = out_dir / "raw_html"
    pdf_dir = out_dir / "pdfs"
    text_dir = out_dir / "text"
    meta_dir = out_dir / "meta"
    for d in (raw_dir, pdf_dir, text_dir, meta_dir):
        d.mkdir(parents=True, exist_ok=True)

    detalhe_html_path = raw_dir / f"{doc_id}__detalhe.html"
    pdf_path: Path | None = None
    pdf_text_path: Path | None = None
    cons_text_path: Path | None = None

    pdf_url: str | None = None
    cons_url: str | None = None

    pdf_text_ok = False
    cons_text_ok = False

    pdf_extract_error: str | None = None
    conv_error: str | None = None

    detalhe_html: bytes | None = None

    try:
        # 1) detalhe
        detalhe_html = download(url_detalhe)
        detalhe_html_path.write_bytes(detalhe_html)

        # 2) pdf
        pdf_url = url_pdf_direto or find_best_pdf_link(detalhe_html, detail_url=url_detalhe)
        if pdf_url:
            try:
                pdf_bytes = download(pdf_url)
                pdf_path = pdf_dir / f"{doc_id}.pdf"
                pdf_path.write_bytes(pdf_bytes)
            except Exception as e:
                conv_error = (
                    conv_error + " | " if conv_error else ""
                ) + f"Falha a descarregar/guardar PDF: {e}"

            if pdf_path and pdf_path.exists():
                pdf_text, err = pdf_to_text(pdf_path)
                pdf_extract_error = err[:2000] if err else None

                pdf_text_path = text_dir / f"{doc_id}__pdf.txt"
                pdf_text_path.write_text(pdf_text or "", encoding="utf-8")
                pdf_text_ok = bool((pdf_text or "").strip())

        # 3) consolidado
        if detalhe_html is not None:
            cons_url = find_consolidated_link(detalhe_html, detail_url=url_detalhe)
            if cons_url:
                try:
                    cons_html = download(cons_url)
                    cons_html_path = raw_dir / f"{doc_id}__consolidado.html"
                    cons_html_path.write_bytes(cons_html)

                    cons_text = extract_visible_text_from_html(cons_html)
                    cons_text_path = text_dir / f"{doc_id}__consolidado.txt"
                    cons_text_path.write_text(cons_text, encoding="utf-8")
                    cons_text_ok = bool(cons_text.strip())
                except Exception as e:
                    conv_error = (conv_error + " | " if conv_error else "") + f"Falha consolidado: {e}"

    except Exception as e:
        conv_error = (conv_error + " | " if conv_error else "") + str(e)[:2000]

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
        "pdf_extract_error": pdf_extract_error,
        "conv_error": conv_error,
    }

    meta_path = meta_dir / f"{doc_id}.json"
    meta_json = json.dumps(meta, ensure_ascii=False, indent=2)
    meta_path.write_text(meta_json, encoding="utf-8")
    meta["ficheiro_meta"] = str(meta_path)

    return meta
