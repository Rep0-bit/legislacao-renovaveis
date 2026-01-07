from __future__ import annotations

import re
import unicodedata


def slugify_pt(text: str) -> str:
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text


def normalize_numero(numero: str) -> str:
    n = numero.strip().lower()
    n = n.replace("/", "-")
    n = re.sub(r"[^a-z0-9\-]+", "-", n)
    n = re.sub(r"-{2,}", "-", n).strip("-")
    return n


def make_document_id(tipo: str, numero: str, ano: int) -> str:
    return f"{slugify_pt(tipo)}-{normalize_numero(numero)}-{int(ano)}"


def safe_filename(name: str) -> str:
    # Conservador: remove chars típicos proibidos no Windows
    return re.sub(r'[<>:"/\\\\|?*]+', "_", name).strip()
