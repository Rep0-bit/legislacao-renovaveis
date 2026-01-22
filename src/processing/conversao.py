# src/processing/conversao.py
# Conversão por HTML (server-rendered) com fallback de UA crawler no http_get.
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..core.http import http_get
from ..core.paths import DATA_DIR, ensure_app_dirs

ensure_app_dirs()
DEFAULT_OUT_DIR = DATA_DIR


def _make_soup(html: bytes) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _extract_visible_text_from_html(html: bytes) -> str:
    soup = _make_soup(html)

    # Remove ruído óbvio
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # Tenta remover zonas comuns de navegação / header / footer
    for sel in [
        "header",
        "footer",
        "nav",
        "[role='navigation']",
        "[aria-label*='Navegação']",
        ".osui",
        ".header",
        ".footer",
    ]:
        for el in soup.select(sel):
            el.decompose()

    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Remove linhas muito “UI”
    drop_prefixes = (
        "Ir para o conteúdo principal",
        "Pesquisa Avançada",
        "Ajuda à pesquisa",
        "Área Pessoal",
        "Página de entrada",
    )
    lines = [ln for ln in lines if not ln.startswith(drop_prefixes)]

    return "\n".join(lines).strip()


def converter(url_detalhe: str, url_pdf_direto: str | None = None, out_dir: Path = DEFAULT_OUT_DIR) -> dict:
    out_dir = Path(out_dir)
    raw_dir = out_dir / "raw_html"
    text_dir = out_dir / "text"
    meta_dir = out_dir / "meta"
    for d in (raw_dir, text_dir, meta_dir):
        d.mkdir(parents=True, exist_ok=True)

    path = urlparse(url_detalhe).path.rstrip("/")
    m = re.search(r"/dr/detalhe/([^/]+)/([^/]+)$", path)
    doc_id = f"{m.group(1)}_{m.group(2)}" if m else path.strip("/").replace("/", "_")

    detalhe_html = http_get(url_detalhe, timeout=30, force_browser_headers=True)
    detalhe_path = raw_dir / f"{doc_id}__detalhe.html"
    detalhe_path.write_bytes(detalhe_html)

    text = _extract_visible_text_from_html(detalhe_html).strip()
    conv_error = None if text else "HTML sem texto extraível (texto final vazio)"

    text_path = text_dir / f"{doc_id}__html.txt"
    text_path.write_text(text, encoding="utf-8")

    meta = {
        "doc_id": doc_id,
        "url_detalhe": url_detalhe,
        "url_pdf": url_pdf_direto,
        "ficheiro_html_detalhe": str(detalhe_path),
        "ficheiro_texto_html": str(text_path),
        "conv_error": conv_error,
        "text": text,
        "text_len": len(text),
    }

    meta_path = meta_dir / f"{doc_id}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    meta["ficheiro_meta"] = str(meta_path)
    return meta
