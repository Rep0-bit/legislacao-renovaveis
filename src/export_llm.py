from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core.config import get_data_dir
from .db.db import DB_PATH


@dataclass(frozen=True)
class ExportSummary:
    exported: int
    skipped: int
    out_dir: Path


# -----------------------------
# Helpers
# -----------------------------
def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def _norm_tipo(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def _safe_filename(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", (s or "").strip(), flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "item"


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _find_text_column(cols: set[str]) -> str | None:
    """Pick the best available text column name, if any."""
    candidates = [
        "texto_limpo",
        "texto",
        "text",
        "conteudo",
        "conteudo_txt",
        "conteudo_texto",
    ]
    for c in candidates:
        if c in cols:
            return c
    return None


def _extract_text_from_conv_meta(meta_path: str | None) -> str | None:
    if not meta_path:
        return None
    try:
        p = Path(meta_path)
        if not p.exists():
            return None
        meta = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

    for key in (
        "txt_path",
        "text_path",
        "text_file",
        "output_txt",
        "out_txt",
        "path_txt",
        "file_txt",
    ):
        v = meta.get(key)
        if isinstance(v, str) and v.strip():
            tp = Path(v)
            if not tp.is_absolute():
                tp = (p.parent / tp).resolve()
            if tp.exists():
                try:
                    return tp.read_text(encoding="utf-8")
                except Exception:
                    try:
                        return tp.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        return None

    # Sometimes the text is inlined
    for key in ("texto_limpo", "texto", "text", "conteudo"):
        v = meta.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _extract_text_from_doc_id(doc_id: str | None, data_dir: Path) -> str | None:
    if not doc_id:
        return None

    doc_id = str(doc_id).strip()
    if not doc_id:
        return None

    # Common layouts: data/convert/<id>.txt, data/convert/txt/<id>.txt, data/convert/<id>/text.txt
    candidates = [
        data_dir / "convert" / f"{doc_id}.txt",
        data_dir / "convert" / f"{doc_id}.text",
        data_dir / "convert" / "txt" / f"{doc_id}.txt",
        data_dir / "convert" / doc_id / "text.txt",
        data_dir / "convert" / doc_id / "output.txt",
        data_dir / "convert" / doc_id / "clean.txt",
    ]
    for p in candidates:
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                try:
                    return p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
    return None


def _clean_text_for_llm(text: str) -> str:
    # light normalization: collapse excessive whitespace but keep paragraph breaks
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Normalize line endings and trim trailing spaces
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # Collapse 3+ blank lines -> 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# -----------------------------
# Core export
# -----------------------------


def _row_get(row, key: str, default=None):
    """Compat helper for sqlite3.Row / dict."""
    try:
        return row[key]  # sqlite3.Row supports mapping access
    except Exception:
        try:
            return row.get(key, default)  # type: ignore[attr-defined]
        except Exception:
            return default


def export_llm(
    *,
    out_dir: Path,
    limit: int = 100,
    where_sql: str = "",
    tipos: list[str] | None = None,
    write_text: bool = True,
    incremental: bool = True,
) -> ExportSummary:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta").mkdir(parents=True, exist_ok=True)
    if write_text:
        (out_dir / "text").mkdir(parents=True, exist_ok=True)

    data_dir = get_data_dir()
    tipos = tipos or []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = _table_columns(conn, "diplomas")
    text_col = _find_text_column(cols)

    # Base select: keep robust to schema
    select_cols = [
        "id",
        "tipo",
        "numero",
        "ano",
        "data_publicacao",
        "titulo",
        "sumario",
        "url_detalhe",
        "url_pdf",
        "hash_fonte",
    ]
    for extra in ("conv_ok", "conv_at", "conv_doc_id", "conv_meta_path", "conv_error"):
        if extra in cols:
            select_cols.append(extra)
    if text_col:
        select_cols.append(text_col)

    sql = f"SELECT {', '.join(select_cols)} FROM diplomas"
    clauses: list[str] = []
    params: list[Any] = []

    if tipos:
        wanted = [_norm_tipo(t) for t in tipos]
        # case-insensitive match in SQLite: use lower() and bind params
        clauses.append("lower(tipo) IN (" + ", ".join(["?"] * len(wanted)) + ")")
        params.extend(wanted)

    if where_sql.strip():
        clauses.append(f"({where_sql.strip()})")

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    sql += " ORDER BY id DESC"
    sql += " LIMIT ?"
    params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    exported = 0
    skipped = 0

    for r in rows:
        rid = int(r["id"])
        tipo = (_row_get(r, "tipo") or "").strip()
        numero = (_row_get(r, "numero") or "").strip()
        ano = _row_get(r, "ano")
        titulo = (_row_get(r, "titulo") or "").strip()
        sumario = (_row_get(r, "sumario") or "").strip()

        # Text resolution priority:
        # 1) text column (if exists)
        # 2) conv_meta_path -> referenced txt
        # 3) conv_doc_id -> known txt paths under data dir
        # 4) fallback: titulo + sumario
        text: str | None = None
        if text_col:
            v = _row_get(r, text_col)
            if isinstance(v, str) and v.strip():
                text = v

        if not text:
            text = _extract_text_from_conv_meta(_row_get(r, "conv_meta_path"))

        if not text:
            text = _extract_text_from_doc_id(_row_get(r, "conv_doc_id"), data_dir)

        if not text:
            # Minimal fallback so the export never hard-fails on schema
            parts = []
            if titulo:
                parts.append(titulo)
            if sumario:
                parts.append(sumario)
            text = "\n\n".join(parts).strip() or None

        if text is None:
            skipped += 1
            continue

        text_clean = _clean_text_for_llm(text)

        meta: dict[str, Any] = {
            "id": rid,
            "tipo": tipo,
            "numero": numero,
            "ano": ano,
            "data_publicacao": _row_get(r, "data_publicacao"),
            "titulo": titulo,
            "sumario": sumario,
            "url_detalhe": _row_get(r, "url_detalhe"),
            "url_pdf": _row_get(r, "url_pdf"),
            "hash_fonte": _row_get(r, "hash_fonte"),
            "exported_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "text_sha256": _sha256_bytes(text_clean.encode("utf-8")),
            "text_len": len(text_clean),
        }

        # Incremental: if meta exists with same sha, skip
        meta_path = out_dir / "meta" / f"{rid}.json"
        if incremental and meta_path.exists():
            try:
                prev = json.loads(meta_path.read_text(encoding="utf-8"))
                if prev.get("text_sha256") == meta["text_sha256"]:
                    skipped += 1
                    continue
            except Exception:
                # if corrupted, re-write
                pass

        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        if write_text:
            txt_path = out_dir / "text" / f"{rid}.txt"
            txt_path.write_text(text_clean + "\n", encoding="utf-8")

        exported += 1

    return ExportSummary(exported=exported, skipped=skipped, out_dir=out_dir)


# -----------------------------
# CLI
# -----------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Export diplomas to LLM-ready JSON + TXT")
    ap.add_argument("--out", default="", help="Output dir. Default: <data_dir>/llm")
    ap.add_argument("--limit", type=int, default=100, help="Max diplomas to export (default: 100)")
    ap.add_argument("--where", default="", help="Optional SQL WHERE snippet (advanced).")
    ap.add_argument(
        "--tipo",
        action="append",
        default=[],
        help="Filter by tipo (case-insensitive). Repeatable: --tipo portaria --tipo 'Resolução ...'",
    )
    ap.add_argument("--no-text", action="store_true", help="Write only JSON meta (no TXT files).")
    ap.add_argument(
        "--no-incremental",
        action="store_true",
        help="Re-export even if existing meta has same text hash.",
    )

    args = ap.parse_args()

    out_dir = Path(args.out) if (args.out or "").strip() else (get_data_dir() / "llm")
    summary = export_llm(
        out_dir=out_dir,
        limit=int(args.limit),
        where_sql=str(args.where or ""),
        tipos=list(args.tipo or []),
        write_text=not bool(args.no_text),
        incremental=not bool(args.no_incremental),
    )
    print(f"Exported {summary.exported} diplomas to {summary.out_dir} (skipped={summary.skipped})")


if __name__ == "__main__":
    main()
