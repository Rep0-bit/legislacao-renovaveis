# src/export_llm.py
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from .core.export import diploma_row_to_llm_payload
from .db.db import DB_PATH, init_db


def _row_to_dict(cur: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    cols = [d[0] for d in cur.description or []]
    return {cols[i]: row[i] for i in range(len(cols))}


def export_llm(
    *,
    out_dir: Path,
    limit: int | None = None,
    where_sql: str | None = None,
    tipos: list[str] | None = None,
    write_text: bool = True,
) -> int:
    """Exporta diplomas da SQLite para artefactos LLM-ready (JSON + opcional TXT).

    - where_sql: trecho SQL após WHERE (uso avançado).
    - tipos: filtro por coluna `tipo` (case-insensitive). Ex.: ["portaria"].
    """
    init_db()

    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        sql = "SELECT * FROM diplomas"
        params: list[Any] = []
        where_parts: list[str] = []

        if where_sql:
            where_parts.append(f"({where_sql})")

        if tipos:
            # filtro case-insensitive na coluna `tipo`
            placeholders = ",".join(["?"] * len(tipos))
            where_parts.append(f"lower(tipo) IN ({placeholders})")
            params.extend([t.strip().lower() for t in tipos if t.strip()])

        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)

        sql += " ORDER BY rowid DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"

        cur.execute(sql, params)
        rows = cur.fetchall()

        count = 0
        for r in rows:
            d = _row_to_dict(cur, r)
            payload = diploma_row_to_llm_payload(d)

            base = str(payload.payload.get("id") or f"diploma_{count+1}")
            # sanitize filename
            base = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in base).strip("_")
            if not base:
                base = f"diploma_{count+1}"

            payload.write(out_dir, basename=base, write_text=write_text)
            count += 1

        summary = {"exported": count, "out_dir": str(out_dir)}
        (out_dir / "_export_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return count
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Exporta diplomas da DB (SQLite) em formato LLM-ready (JSON + TXT)."
    )
    ap.add_argument("--out", default="data/llm", help="Diretório de output (default: data/llm)")
    ap.add_argument(
        "--limit", type=int, default=100, help="Número máximo de diplomas a exportar (default: 100)"
    )

    # filtros
    ap.add_argument(
        "--tipo",
        action="append",
        default=[],
        help="Filtra por tipo (coluna `tipo`, case-insensitive). Pode repetir: --tipo portaria --tipo 'Resolução do Conselho de Ministros'",
    )
    ap.add_argument("--where", default="", help="Cláusula SQL após WHERE (uso avançado)")

    ap.add_argument("--no-text", action="store_true", help="Não gerar .txt (apenas JSON)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    where_sql = (args.where or "").strip() or None
    write_text = not args.no_text
    tipos = [t for t in (args.tipo or []) if str(t).strip()] or None

    n = export_llm(out_dir=out_dir, limit=args.limit, where_sql=where_sql, tipos=tipos, write_text=write_text)
    print(f"Exported {n} diplomas to {out_dir.resolve()}")

    if n == 0:
        # dica rápida (útil quando o tipo não bate)
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("SELECT tipo, COUNT(*) FROM diplomas GROUP BY tipo ORDER BY COUNT(*) DESC")
            tipos_db = cur.fetchall()
        finally:
            conn.close()
        if tipos_db:
            print("Tipos disponíveis na DB:")
            for t, c in tipos_db:
                print(f" - {t} ({c})")


if __name__ == "__main__":
    main()
