# src/core/keywords.py
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


# -----------------------------
# Keyword matching (accent-insensitive) + qualidade
# -----------------------------
def _norm_text(s: str) -> str:
    s = (s or "").strip().casefold()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s


def match_keywords_hit(text: str, keywords: Iterable[str]) -> str | None:
    s_norm = _norm_text(text or "")

    for k in keywords:
        k = (k or "").strip()
        if not k:
            continue

        kn = _norm_text(k)

        # Keywords curtas: palavra inteira (evita "sen" bater em "sendo")
        if len(kn) <= 3:
            if re.search(rf"\b{re.escape(kn)}\b", s_norm):
                return k
        else:
            if kn in s_norm:
                return k

    return None


GENERIC_KEYWORDS = {"sen", "armazenamento", "solar", "onshore", "offshore", "biomassa", "carbono"}
GENERIC_KEYWORDS_NORM = {_norm_text(k) for k in GENERIC_KEYWORDS}

ENERGY_CONTEXT_TERMS = {
    "energia",
    "energ",
    "elétric",
    "eletric",
    "rede eletrica",
    "rede elétrica",
    "rede de transporte",
    "rede de distribuicao",
    "rede de distribuição",
    "renov",
    "autoconsumo",
    "upac",
    "fotovolta",
    "eólic",
    "eolic",
    "hidrog",
    "bess",
    "produção",
    "produc",
    "sistema elétrico",
    "sistema eletrico",
    "garantias de origem",
    "título de reserva de capacidade",
    "trc",
}


def _has_energy_context(text: str) -> bool:
    s = _norm_text(text or "")
    for t in ENERGY_CONTEXT_TERMS:
        tt = _norm_text(t)
        if tt and tt in s:
            return True
    return False


def _has_energy_context_near_hit(text: str, hit: str, *, window: int = 500) -> bool:
    """
    Procura termos de contexto energético num raio (window) em torno do hit.
    Reduz falsos positivos (headers/footers, etc.).
    """
    s = _norm_text(text or "")
    h = _norm_text(hit or "")
    if not s or not h:
        return False

    start = 0
    while True:
        idx = s.find(h, start)
        if idx == -1:
            break

        lo = max(0, idx - window)
        hi = min(len(s), idx + len(h) + window)
        chunk = s[lo:hi]

        for t in ENERGY_CONTEXT_TERMS:
            tt = _norm_text(t)
            if tt and tt in chunk:
                return True

        start = idx + max(1, len(h))

    return False


def match_keywords_hit_quality(text: str, keywords: Iterable[str]) -> tuple[str | None, bool, bool]:
    """
    Retorna:
      (hit, is_generic_hit, has_context)

    - Se hit não é genérico: contexto pode ser global.
    - Se hit é genérico: exige contexto PERTO do hit (near-hit).
    """
    hit = match_keywords_hit(text, keywords)
    if not hit:
        return None, False, False

    is_generic = _norm_text(hit) in GENERIC_KEYWORDS_NORM

    if not is_generic:
        return hit, False, _has_energy_context(text)

    return hit, True, _has_energy_context_near_hit(text, hit, window=500)


__all__ = [
    "match_keywords_hit",
    "match_keywords_hit_quality",
    "GENERIC_KEYWORDS",
    "ENERGY_CONTEXT_TERMS",
]
