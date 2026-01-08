# src/utils/normalize.py
from __future__ import annotations

import re
import unicodedata


def slugify_ascii_kebab(text: str) -> str:
    """
    Converte texto para ASCII kebab-case (a-z0-9-), removendo acentos.
    Ex.: "Decreto-Lei" -> "decreto-lei"; "Acórdão do Tribunal Constitucional" -> "acordao-do-tribunal-constitucional"
    """
    s = (text or "").strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


_NUM_SEG_RE = re.compile(r"^\s*(?P<num>\d+)\s*(?P<suf>[a-zA-Z]+)?\s*$")


def normalize_numero(numero: str, *, pad: int = 6) -> str:
    """
    Normaliza 'numero' para ordenação consistente.

    Regras:
    - Aceita "151-B", "30-A", "4", "14/1"
    - Separa por '/' em segmentos.
    - Cada segmento vira: <num_padded><-suffix_lower?>
      Ex.: "151-B" -> "000151-b"
           "14/1"  -> "000014-000001"
           "4"     -> "000004"
    """
    raw = (numero or "").strip()
    if not raw:
        return ""

    parts = [p.strip() for p in raw.split("/") if p.strip()]
    out_parts: list[str] = []

    for p in parts:
        # permitir hífen no meio (ex.: 151-B)
        p2 = p.replace("_", "-").replace(" ", "")
        # separar num + sufixo (opcional) ignorando hífens intermédios
        m = _NUM_SEG_RE.match(p2.replace("-", ""))
        if not m:
            # fallback: limpa e devolve algo estável
            cleaned = re.sub(r"[^0-9a-zA-Z]+", "", p2).lower()
            out_parts.append(cleaned)
            continue

        n = int(m.group("num"))
        suf = (m.group("suf") or "").lower()
        seg = f"{n:0{pad}d}"
        if suf:
            seg = f"{seg}-{suf}"
        out_parts.append(seg)

    return "-".join(out_parts)


def normalize_numero_display(numero: str, *, ano: int | None = None) -> str:
    """
    Normaliza 'numero' para apresentação/pesquisa (sem padding).

    Objetivo:
    - Ter um formato estável e amigável para humanos (e para LIKE), sem perder a ordenação
      (a ordenação continua a usar `numero_norm`).

    Exemplos (assumindo ano=2026):
      - numero="14/1"        -> "14-2026-1"
      - numero="151-B"       -> "151-b-2026"   (se não existir ano embutido)
      - numero="151-B/2013"  -> "151-b-2013"
      - numero="2"           -> "2-2026"

    Regras:
    - separadores ('/', '_', espaços) → '-' (na prática devolve sempre com '-')
    - sufixos (A, B, etc.) → minúsculas
    - se `ano` for fornecido e não estiver presente nos segmentos, injeta-o:
      - 1 segmento: <seg>-<ano>
      - >=2 segmentos: <seg0>-<ano>-<seg1>-...
    """
    s = (numero or "").strip()
    if not s:
        return ""

    raw_parts = [p.strip() for p in s.split("/") if p.strip()]

    out_parts: list[str] = []
    for p in raw_parts:
        p2 = p.replace("_", "-").strip()
        # remove espaços internos, mas preserva '-' (para casos tipo "151-B")
        p2 = p2.replace(" ", "")

        # tenta reconhecer <num><suffix?>
        m = _NUM_SEG_RE.match(p2.replace("-", ""))
        if not m:
            cleaned = re.sub(r"[^0-9a-zA-Z]+", "", p2).lower()
            if cleaned:
                out_parts.append(cleaned)
            continue

        n = int(m.group("num"))
        suf = (m.group("suf") or "").lower()
        seg = str(n)
        if suf:
            seg = f"{seg}-{suf}"
        out_parts.append(seg)

    if ano is not None:
        ano_s = str(int(ano))
        if ano_s not in out_parts:
            if len(out_parts) <= 1:
                out_parts.append(ano_s)
            else:
                out_parts.insert(1, ano_s)

    return "-".join(out_parts)


def normalize_search_text(text: str | None) -> str:
    """
    Normaliza texto para pesquisa (compatível com LIKE no SQLite):

    - lowercase
    - remove acentos
    - converte tudo o que não é alfanumérico em espaços
    - colapsa espaços

    Ex.: "Resolução n.º 3/2026" -> "resolucao n o 3 2026"
    """
    s = (text or "").strip()
    if not s:
        return ""

    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()

    out: list[str] = []
    for ch in s:
        out.append(ch if ch.isalnum() else " ")

    s = "".join(out)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s
