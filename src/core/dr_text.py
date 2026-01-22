# src/core/dr_text.py
from __future__ import annotations

import re
import urllib.request
from html import unescape

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def fetch_html(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    # O DR é UTF-8; se falhar, tenta fallback
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return raw.decode(errors="replace")


def extract_texto_from_dr_html(html: str) -> str | None:
    """
    Extrai o bloco de TEXTO completo a partir do HTML de /dr/detalhe/...
    Heurística simples e robusta:
      - começa em 'TEXTO'
      - pára antes do rodapé ('Mapa do Site', 'INCM', etc.)
      - remove lixo e normaliza whitespace
    """
    if not html:
        return None

    # Normaliza para facilitar procura
    h = html

    # Remove scripts/styles para reduzir ruído
    h = re.sub(r"<script\b[^>]*>.*?</script>", " ", h, flags=re.I | re.S)
    h = re.sub(r"<style\b[^>]*>.*?</style>", " ", h, flags=re.I | re.S)

    # Se a palavra TEXTO não existir, não há muito a fazer
    idx = h.upper().find("TEXTO")
    if idx == -1:
        return None

    h = h[idx:]

    # corta rodapés comuns
    stop_markers = [
        "Mapa do Site",
        "Avisos Legais",
        "Acessibilidade do site",
        "JavaScript is required",
        "INCM",
    ]
    stop_idx = None
    for m in stop_markers:
        j = h.find(m)
        if j != -1:
            stop_idx = j if stop_idx is None else min(stop_idx, j)
    if stop_idx:
        h = h[:stop_idx]

    # Remove tags HTML "na unha" (suficiente porque o DR já vem bem linearizado)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</p\s*>", "\n", h, flags=re.I)
    h = re.sub(r"</li\s*>", "\n", h, flags=re.I)
    h = re.sub(r"</h\d\s*>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)

    # Decode entidades HTML e normaliza espaços
    text = unescape(h)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s+\n", "\n\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # “TEXTO” aparece como label — remove a primeira linha se for só isso
    lines = [ln.strip() for ln in text.split("\n")]
    if lines and lines[0].upper() == "TEXTO":
        lines = lines[1:]
    text = "\n".join([ln for ln in lines if ln != ""]).strip()

    return text or None


def fetch_texto_completo(url_detalhe: str, timeout: int = 30) -> str | None:
    html = fetch_html(url_detalhe, timeout=timeout)
    return extract_texto_from_dr_html(html)
