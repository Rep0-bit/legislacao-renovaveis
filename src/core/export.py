# src/core/export.py
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _iso_date(v: Any) -> str | None:
    """Best-effort conversion to ISO date string (YYYY-MM-DD)."""
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = str(v).strip()
    if not s:
        return None
    # Accept ISO, or 'YYYY-MM-DD ...'
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    return None


def clean_text(text: str | None) -> str:
    """Normalize whitespace & remove obvious garbage."""
    if not text:
        return ""
    t = text.replace("\x00", " ").replace("\ufeff", " ")
    # collapse whitespace
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()
    return t


def _pick_first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LlmPayload:
    """Canonical representation of a diploma for LLM indexing."""

    payload: dict[str, Any]
    llm_text: str

    def to_json(self, *, indent: int | None = 2) -> str:
        obj = dict(self.payload)
        obj["llm_text"] = self.llm_text
        return json.dumps(obj, ensure_ascii=False, indent=indent)

    def write(self, out_dir: Path, *, basename: str, write_text: bool = True) -> tuple[Path, Path | None]:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"{basename}.json"
        json_path.write_text(self.to_json(indent=2), encoding="utf-8")

        txt_path: Path | None = None
        if write_text:
            txt_path = out_dir / f"{basename}.txt"
            txt_path.write_text(self.llm_text, encoding="utf-8")

        return json_path, txt_path


def build_llm_text(meta: dict[str, Any], body_text: str) -> str:
    """Render the final text that will be indexed."""
    parts: list[str] = []
    titulo = (meta.get("titulo") or "").strip()
    sumario = (meta.get("sumario") or "").strip()

    if titulo:
        parts.append(titulo)
    if sumario:
        parts.append(sumario)

    body = clean_text(body_text)
    if body:
        parts.append(body)

    return "\n\n".join(p for p in parts if p).strip()


def diploma_row_to_llm_payload(row: dict[str, Any], *, pipeline_version: str = "A7.1") -> LlmPayload:
    """Convert a DB row (dict) into canonical LLM payload.

    This is best-effort: it tries multiple common column names so it works across schema variants.
    """
    # identifiers / metadata
    diploma_id = _pick_first(row, "id", "diploma_id", "id_dr", "dr_id")
    tipo = _pick_first(row, "tipo", "tipo_slug", "tipo_norm")
    numero = _pick_first(row, "numero", "num", "n")
    ano = _pick_first(row, "ano", "year")

    data_pub = _pick_first(row, "data_publicacao", "data", "pub_date", "pubdate", "data_pub")
    data_pub_iso = _iso_date(data_pub)

    titulo = _pick_first(row, "titulo", "title")
    sumario = _pick_first(row, "sumario", "summary", "sumario_rss")

    url_detalhe = _pick_first(row, "url_detalhe", "detail_url", "url", "link")
    url_pdf = _pick_first(row, "url_pdf", "pdf_url", "pdf")

    # text fields (schema-agnostic)
    text_raw = _pick_first(
        row,
        "texto",
        "texto_limpo",
        "texto_pdf",
        "texto_html",
        "full_text",
        "text",
    )
    text_clean = clean_text(str(text_raw) if text_raw is not None else "")

    meta: dict[str, Any] = {
        "id": diploma_id,
        "tipo": tipo,
        "numero": numero,
        "ano": ano,
        "data_publicacao": data_pub_iso,
        "titulo": titulo,
        "sumario": sumario,
        "fonte": {"url_detalhe": url_detalhe, "url_pdf": url_pdf},
        "pipeline_version": pipeline_version,
    }

    llm_text = build_llm_text({"titulo": titulo or "", "sumario": sumario or ""}, text_clean)

    meta["texto"] = {
        "n_caracteres": len(llm_text),
        "sha256": _sha256(llm_text) if llm_text else None,
    }

    # remove empty keys (keep stable shape but avoid noise)
    if meta["fonte"]["url_detalhe"] in (None, ""):
        meta["fonte"]["url_detalhe"] = None
    if meta["fonte"]["url_pdf"] in (None, ""):
        meta["fonte"]["url_pdf"] = None

    return LlmPayload(payload=meta, llm_text=llm_text)
