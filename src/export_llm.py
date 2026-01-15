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
    last_id: int | None = None  # E2: highest id processed in this run (for --continue state)


# -----------------------------
# Helpers
# -----------------------------
def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def _norm_tipo(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


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


def _fix_mojibake(s: str | None) -> str | None:
    """Best-effort fix for common UTF-8 text wrongly decoded as Latin-1/CP1252 (mojibake)."""
    if s is None:
        return None
    s0 = str(s)
    if not s0:
        return s0
    if ("Ã" not in s0) and ("Â" not in s0):
        return s0

    def score(txt: str) -> tuple[int, int]:
        return (txt.count("Ã") + txt.count("Â") + txt.count("�"), len(txt))

    best = s0
    best_score = score(s0)

    candidates: list[str] = []
    for enc in ("latin-1", "cp1252"):
        try:
            b = s0.encode(enc, errors="replace")
            candidates.append(b.decode("utf-8", errors="replace"))
        except Exception:
            pass

    for c in list(candidates):
        if ("Ã" in c) or ("Â" in c):
            for enc in ("latin-1", "cp1252"):
                try:
                    b = c.encode(enc, errors="replace")
                    candidates.append(b.decode("utf-8", errors="replace"))
                except Exception:
                    pass

    for c in candidates:
        sc = score(c)
        if sc < best_score:
            best, best_score = c, sc

    return best


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
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _row_get(row, key: str, default=None):
    try:
        return row[key]
    except Exception:
        try:
            return row.get(key, default)  # type: ignore[attr-defined]
        except Exception:
            return default


# -----------------------------
# E2: state helpers (collector_state)
# -----------------------------
def _make_state_key(state: str, tipos: list[str]) -> str:
    st = (state or "default").strip() or "default"
    if tipos:
        norm = sorted({_norm_tipo(t) for t in tipos if str(t).strip()})
        tipos_key = ",".join(norm) if norm else "all"
    else:
        tipos_key = "all"
    return f"export_llm:{st}:{tipos_key}"


def _state_get(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute("SELECT value FROM collector_state WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    try:
        return int(row[0])
    except Exception:
        return None


def _state_set(conn: sqlite3.Connection, key: str, value: int) -> None:
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO collector_state(key, value, updated_at) VALUES(?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, str(int(value)), ts),
    )


def _state_delete(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM collector_state WHERE key = ?", (key,))


# -----------------------------
# Core export
# -----------------------------
def export_llm(
    *,
    out_dir: Path,
    limit: int = 100,
    where_sql: str = "",
    tipos: list[str] | None = None,
    write_text: bool = True,
    incremental: bool = True,
    # E1 filters
    since: str | None = None,
    since_id: int | None = None,
    # E2 ordering: for --continue we must process ascending to avoid skipping by LIMIT
    order: str = "desc",
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
    for extra_norm in ("titulo_norm", "sumario_norm"):
        if extra_norm in cols:
            select_cols.append(extra_norm)
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
        clauses.append("lower(tipo) IN (" + ", ".join(["?"] * len(wanted)) + ")")
        params.extend(wanted)

    if where_sql.strip():
        clauses.append(f"({where_sql.strip()})")

    # E1: incremental filters
    if since_id is not None:
        clauses.append("id >= ?")
        params.append(int(since_id))
    elif since:
        since_s = str(since).strip()
        row = conn.execute(
            "SELECT MIN(id) AS min_id FROM diplomas WHERE data_publicacao IS NOT NULL AND data_publicacao >= ?",
            (since_s,),
        ).fetchone()
        min_id = 0
        if row is not None:
            try:
                min_id = int(row["min_id"] or 0)
            except Exception:
                try:
                    min_id = int(row[0] or 0)
                except Exception:
                    min_id = 0
        clauses.append(
            "((data_publicacao IS NOT NULL AND data_publicacao >= ?) OR (data_publicacao IS NULL AND id >= ?))"
        )
        params.extend([since_s, min_id])

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    ord_sql = "ASC" if str(order).lower().startswith("a") else "DESC"
    sql += f" ORDER BY id {ord_sql}"
    sql += " LIMIT ?"
    params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    exported = 0
    skipped = 0
    last_id: int | None = None

    for r in rows:
        rid = int(r["id"])
        last_id = rid if (last_id is None or rid > last_id) else last_id

        tipo = (_row_get(r, "tipo") or "").strip()
        numero = (_row_get(r, "numero") or "").strip()
        ano = _row_get(r, "ano")
        titulo_raw = (_row_get(r, "titulo") or "").strip()
        sumario_raw = (_row_get(r, "sumario") or "").strip()
        titulo_norm = (_row_get(r, "titulo_norm") or "").strip()
        sumario_norm = (_row_get(r, "sumario_norm") or "").strip()

        titulo_raw_fixed = _fix_mojibake(titulo_raw)
        sumario_raw_fixed = _fix_mojibake(sumario_raw)

        titulo = (titulo_raw_fixed or "").strip()
        sumario = (sumario_raw_fixed or "").strip()
        if (not titulo) or ("Ã" in titulo or "Â" in titulo):
            titulo = (_fix_mojibake(titulo_norm) or titulo_norm or titulo_raw or "").strip()
        if (not sumario) or ("Ã" in sumario or "Â" in sumario):
            sumario = (_fix_mojibake(sumario_norm) or sumario_norm or sumario_raw or "").strip()

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
        exported_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        meta: dict[str, Any] = {
            "id": rid,
            "tipo": tipo,
            "numero": numero,
            "ano": ano,
            "data_publicacao": _row_get(r, "data_publicacao"),
            "url_detalhe": _row_get(r, "url_detalhe"),
            "url_pdf": _row_get(r, "url_pdf"),
            "hash_fonte": _row_get(r, "hash_fonte"),
            "titulo_raw": titulo_raw,
            "titulo_raw_fixed": titulo_raw_fixed or None,
            "sumario_raw": sumario_raw,
            "sumario_raw_fixed": sumario_raw_fixed or None,
            "titulo_norm": titulo_norm or None,
            "sumario_norm": sumario_norm or None,
            "titulo": titulo,
            "sumario": sumario,
            "exported_at": exported_at,
            "text_sha256": _sha256_bytes(text_clean.encode("utf-8")),
            "text_len": len(text_clean),
        }

        meta_path = out_dir / "meta" / f"{rid}.json"
        if incremental and meta_path.exists():
            try:
                prev = json.loads(meta_path.read_text(encoding="utf-8"))
                if prev.get("text_sha256") == meta["text_sha256"]:
                    skipped += 1
                    continue
            except Exception:
                pass

        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        if write_text:
            txt_path = out_dir / "text" / f"{rid}.txt"
            txt_path.write_text(text_clean + "\n", encoding="utf-8")

        exported += 1

    return ExportSummary(exported=exported, skipped=skipped, out_dir=out_dir, last_id=last_id)


# -----------------------------
# CLI
# -----------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Export diplomas to LLM-ready JSON + TXT")
    ap.add_argument("--out", default="", help="Output dir. Default: <data_dir>/llm")
    ap.add_argument(
        "--preset",
        choices=["all", "decretos", "portarias", "renovaveis"],
        default="",
        help="Preset de export (atalho para filtros e state).",
    )
    ap.add_argument("--limit", type=int, default=100, help="Max diplomas to export (default: 100)")
    ap.add_argument("--since", default="", help="Export diplomas desde YYYY-MM-DD")
    ap.add_argument("--since-id", type=int, default=0, help="Export diplomas com id >= N (prioritário)")
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

    # E2
    ap.add_argument(
        "--continue", dest="continue_export", action="store_true", help="Usa cursor guardado na BD."
    )
    ap.add_argument("--state", default="default", help="Nome do estado (default: default)")
    ap.add_argument("--reset-state", action="store_true", help="Apaga o cursor desse estado e termina.")

    args = ap.parse_args()

    # E3: presets (aplicam defaults apenas se o utilizador não especificou filtros)
    preset = str(getattr(args, "preset", "") or "").strip().casefold()
    if preset:
        # tipo(s) só são aplicados se o utilizador não passou --tipo
        if not list(args.tipo or []):
            if preset == "decretos":
                args.tipo = ["decreto-lei"]
            elif preset == "portarias":
                args.tipo = ["portaria"]
            # preset=all/renovaveis não força tipos

        # state só é aplicado se o utilizador não passou --state (mantém default)
        if str(args.state) == "default":
            if preset == "decretos":
                args.state = "decretos"
            elif preset == "portarias":
                args.state = "portarias"
            elif preset == "renovaveis":
                args.state = "renovaveis"

        # Nota: preset renovaveis prepara o state; o filtro por keywords/where entra no E4.
        if preset == "renovaveis" and not str(args.where or "").strip():
            print("ℹ️ preset=renovaveis: state preparado; filtro temático será adicionado no E4")

    tipos = list(args.tipo or [])
    state_key = _make_state_key(str(args.state), tipos)

    if bool(args.reset_state):
        conn = sqlite3.connect(DB_PATH)
        try:
            _state_delete(conn, state_key)
            conn.commit()
        finally:
            conn.close()
        print(f"🧹 Estado limpo: {state_key}")
        return

    out_dir = Path(args.out) if (args.out or "").strip() else (get_data_dir() / "llm")

    since: str | None = str(args.since).strip() or None
    since_id: int | None = args.since_id if int(args.since_id or 0) > 0 else None
    order = "desc"
    save_state = False

    if bool(getattr(args, "continue_export", False)):
        conn = sqlite3.connect(DB_PATH)
        try:
            last = _state_get(conn, state_key)
        finally:
            conn.close()
        since = None
        since_id = (int(last) + 1) if last is not None else 1
        order = "asc"
        save_state = True
        print(f"📤 Continue state={args.state} key={state_key} since-id={since_id}")

    summary = export_llm(
        out_dir=out_dir,
        limit=int(args.limit),
        where_sql=str(args.where or ""),
        tipos=tipos,
        write_text=not bool(args.no_text),
        incremental=not bool(args.no_incremental),
        since=since,
        since_id=since_id,
        order=order,
    )

    if save_state and summary.last_id is not None:
        conn = sqlite3.connect(DB_PATH)
        try:
            _state_set(conn, state_key, int(summary.last_id))
            conn.commit()
        finally:
            conn.close()

    print(f"Exported {summary.exported} diplomas to {summary.out_dir} (skipped={summary.skipped})")


if __name__ == "__main__":
    main()
