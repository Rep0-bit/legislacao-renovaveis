# src/processing/conversao.py
# Conversão robusta:
# - Preferencialmente usa HTML do DR (server-rendered) via url_detalhe (/dr/detalhe/...)
# - Se não for uma página de detalhe do DR, tenta converter diretamente a partir de url_pdf (ex.: ERSE/DGEG)
#
# Nota importante:
# - Nunca aceitar “texto” que pareça binário PDF (ex.: começa por %PDF-). Isso indica PDF sem camada de texto
#   (ou fallback incorreto). Nesses casos, falha limpo e recomenda OCR.

from __future__ import annotations

import json
import re
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pypdf import PdfReader

from ..core.http import http_get, http_get_pdf
from ..core.paths import DATA_DIR, ensure_app_dirs

ensure_app_dirs()
DEFAULT_OUT_DIR = DATA_DIR


# -----------------------------
# Helpers
# -----------------------------
def _is_dr_detail_url(url: str) -> bool:
    u = (url or "").casefold()
    return ("diariodarepublica.pt" in u) and ("/dr/detalhe/" in u)


def _safe_doc_id_from_url(url: str) -> str:
    # Para PDFs externos: usa nome do ficheiro na URL
    p = urlparse(url).path
    base = (p.rsplit("/", 1)[-1] or "doc").strip()
    base = re.sub(r"\.pdf$", "", base, flags=re.I)
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", base)
    return (base[:120] if len(base) > 120 else base) or "doc"


def _looks_like_pdf_binary_text(text: str) -> bool:
    return bool(text) and text.lstrip().startswith("%PDF-")


def _make_soup(html: bytes) -> BeautifulSoup:
    s = html.decode("utf-8", errors="ignore")
    try:
        return BeautifulSoup(s, "lxml")
    except Exception:
        return BeautifulSoup(s, "html.parser")


def _extract_visible_text_from_html(html: bytes) -> str:
    soup = _make_soup(html)

    # Remove ruído óbvio
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # Remove zonas comuns de navegação/header/footer (melhor esforço)
    for sel in [
        "header",
        "footer",
        "nav",
        "[role='navigation']",
        "[aria-label*='Navega']",
        ".osui",
        ".header",
        ".footer",
    ]:
        for el in soup.select(sel):
            el.decompose()

    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    drop_prefixes = (
        "Ir para o conteúdo principal",
        "Pesquisa Avançada",
        "Ajuda à pesquisa",
        "Área Pessoal",
        "Página de entrada",
    )
    lines = [ln for ln in lines if not ln.startswith(drop_prefixes)]

    out = "\n".join(lines).strip()
    # Guard adicional: se por algum bug o HTML vier a conter binário PDF (muito improvável), não aceitar.
    if _looks_like_pdf_binary_text(out):
        return ""
    return out


def _pdf_bytes_to_text(pdf_bytes: bytes) -> tuple[str, str | None]:
    """Extrai texto de PDF (pypdf -> fallback PyMuPDF). Nunca levanta."""
    # 1) pypdf
    pypdf_err = None
    try:
        reader = PdfReader(BytesIO(pdf_bytes))

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

        if text and not _looks_like_pdf_binary_text(text):
            return text, None
    except Exception as e:
        pypdf_err = str(e)
    else:
        pypdf_err = "Sem texto extraível (pypdf)"

    # 2) fallback PyMuPDF (se existir)
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        parts2: list[str] = []
        for page in doc:
            t = page.get_text("text") or ""
            if t.strip():
                parts2.append(t)
        doc.close()

        text2 = "\n\n".join(parts2).strip()
        if text2 and not _looks_like_pdf_binary_text(text2):
            return text2, None

        return "", f"PDF sem texto extraível (requer OCR). pypdf_err={pypdf_err}"
    except Exception as e:
        return "", f"Falha a extrair texto do PDF (pypdf_err={pypdf_err}; pymupdf_err={e})"


# -----------------------------
# API
# -----------------------------
def converter(
    url_detalhe: str | None,
    url_pdf_direto: str | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> dict:
    """Converte um diploma e devolve um dicionário de meta (que também é gravado em JSON)."""
    out_dir = Path(out_dir)
    raw_dir = out_dir / "raw_html"
    text_dir = out_dir / "text"
    meta_dir = out_dir / "meta"
    pdf_dir = out_dir / "pdf"
    for d in (raw_dir, text_dir, meta_dir, pdf_dir):
        d.mkdir(parents=True, exist_ok=True)

    url_detalhe = (url_detalhe or "").strip() or None
    url_pdf_direto = (url_pdf_direto or "").strip() or None

    # -----------------------------
    # Caso 1) DR detalhe: HTML server-rendered
    # -----------------------------
    if url_detalhe and _is_dr_detail_url(url_detalhe):
        path = urlparse(url_detalhe).path.rstrip("/")
        m = re.search(r"/dr/detalhe/([^/]+)/([^/]+)$", path)
        doc_id = f"{m.group(1)}_{m.group(2)}" if m else path.strip("/").replace("/", "_")

        detalhe_html = http_get(url_detalhe, timeout=30, force_browser_headers=True)
        detalhe_path = raw_dir / f"{doc_id}__detalhe.html"
        detalhe_path.write_bytes(detalhe_html)

        text = _extract_visible_text_from_html(detalhe_html).strip()
        conv_error = None if text else "HTML sem texto extraível (texto final vazio)"

        text_path = text_dir / f"{doc_id}__html.txt"
        # Mesmo que esteja vazio, gravar para diagnóstico.
        text_path.write_text(text, encoding="utf-8")

        meta = {
            "doc_id": doc_id,
            "source": "html",
            "url_detalhe": url_detalhe,
            "url_pdf_original": url_pdf_direto,
            "url_pdf": url_pdf_direto,
            "ficheiro_html": str(detalhe_path),
            "ficheiro_texto": str(text_path),
            "text_len": len(text),
            "conv_error": conv_error,
            "pdf_extract_error": None,
        }

        meta_path = meta_dir / f"{doc_id}.json"
        meta["ficheiro_meta"] = str(meta_path)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    # -----------------------------
    # Caso 2) PDF direto (externo ou sem detalhe DR)
    # -----------------------------
    pdf_url = url_pdf_direto or url_detalhe
    if not pdf_url:
        return {
            "doc_id": None,
            "source": "none",
            "url_detalhe": url_detalhe,
            "url_pdf_original": url_pdf_direto,
            "url_pdf": None,
            "ficheiro_meta": None,
            "ficheiro_texto": None,
            "text_len": 0,
            "conv_error": "Sem url_detalhe e sem url_pdf (nada para converter)",
            "pdf_extract_error": None,
        }

    doc_id = _safe_doc_id_from_url(pdf_url)

    # Download PDF robusto (seguindo redirects / páginas intermédias)
    try:
        pdf_bytes = http_get_pdf(pdf_url, timeout=45)
    except Exception as e:
        meta = {
            "doc_id": doc_id,
            "source": "pdf",
            "url_detalhe": url_detalhe,
            "url_pdf_original": url_pdf_direto,
            "url_pdf": pdf_url,
            "ficheiro_meta": None,
            "ficheiro_texto": None,
            "text_len": 0,
            "conv_error": f"Falha a descarregar PDF: {e}",
            "pdf_extract_error": None,
        }
        meta_path = meta_dir / f"{doc_id}.json"
        meta["ficheiro_meta"] = str(meta_path)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    prefix = pdf_bytes.lstrip()[:8]
    if not prefix.startswith(b"%PDF-"):
        # Guardar um "raw" para diagnóstico (pode ser HTML)
        raw_path = raw_dir / f"{doc_id}__pdf_download.bin"
        raw_path.write_bytes(pdf_bytes[:200_000])  # cap para não explodir disco
        meta = {
            "doc_id": doc_id,
            "source": "pdf",
            "url_detalhe": url_detalhe,
            "url_pdf_original": url_pdf_direto,
            "url_pdf": pdf_url,
            "ficheiro_raw": str(raw_path),
            "ficheiro_meta": None,
            "ficheiro_texto": None,
            "text_len": 0,
            "conv_error": f"Conteúdo não-PDF (prefix={pdf_bytes[:30]!r})",
            "pdf_extract_error": None,
        }
        meta_path = meta_dir / f"{doc_id}.json"
        meta["ficheiro_meta"] = str(meta_path)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    pdf_path = pdf_dir / f"{doc_id}.pdf"
    pdf_path.write_bytes(pdf_bytes)

    text, pdf_err = _pdf_bytes_to_text(pdf_bytes)
    # Guard definitivo: nunca aceitar “texto” que seja binário PDF
    if _looks_like_pdf_binary_text(text):
        text = ""
        pdf_err = "Texto inválido (parece binário PDF); PDF provavelmente sem camada de texto (requer OCR)."

    conv_error = None
    if not text:
        conv_error = pdf_err or "PDF sem texto extraível (requer OCR)"

    text_path = text_dir / f"{doc_id}__pdf.txt"
    text_path.write_text(text, encoding="utf-8")

    meta = {
        "doc_id": doc_id,
        "source": "pdf",
        "url_detalhe": url_detalhe,
        "url_pdf_original": url_pdf_direto,
        "url_pdf": pdf_url,
        "ficheiro_pdf": str(pdf_path),
        "ficheiro_texto": str(text_path),
        "text_len": len(text),
        "conv_error": conv_error,
        "pdf_extract_error": pdf_err if conv_error else None,
    }

    meta_path = meta_dir / f"{doc_id}.json"
    meta["ficheiro_meta"] = str(meta_path)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


__all__ = ["converter"]
