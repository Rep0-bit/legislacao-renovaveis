from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

# NOTE: B9 should be read-only queries + exports. Avoid importing conversion/LLM pipeline here.


OrderBy = Literal[
    "data_publicacao_desc",
    "data_publicacao_asc",
    "ano_desc",
    "ano_asc",
    "id_desc",
    "id_asc",
]


def _get_db_conn() -> sqlite3.Connection:
    """Return a SQLite connection using project's db module when available.

    Falls back to connecting directly via DB_PATH.
    """
    try:
        # Preferred: project helper
        from ..db.db import get_conn  # type: ignore

        return get_conn()
    except Exception:
        from ..db.db import DB_PATH  # type: ignore

        return sqlite3.connect(DB_PATH)


def _row_to_dict(cur: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    cols = [d[0] for d in cur.description or []]
    return {cols[i]: row[i] for i in range(len(cols))}


def _parse_csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip().lower() for p in value.split(",") if p.strip()]


def _apply_filters(
    where: list[str],
    params: list[Any],
    *,
    tipo: str | None = None,
    tipo_in: Iterable[str] | None = None,
    tipo_not_in: Iterable[str] | None = None,
    ano: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
) -> None:
    if tipo:
        where.append("tipo_slug = ?")
        params.append(tipo.strip().lower())

    if tipo_in:
        tipo_in_list = [t.strip().lower() for t in tipo_in if str(t).strip()]
        if tipo_in_list:
            where.append(f"tipo_slug IN ({','.join(['?'] * len(tipo_in_list))})")
            params.extend(tipo_in_list)

    if tipo_not_in:
        tipo_not_in_list = [t.strip().lower() for t in tipo_not_in if str(t).strip()]
        if tipo_not_in_list:
            where.append(f"tipo_slug NOT IN ({','.join(['?'] * len(tipo_not_in_list))})")
            params.extend(tipo_not_in_list)

    if ano is not None:
        where.append("ano = ?")
        params.append(int(ano))

    if date_from:
        where.append("data_publicacao >= ?")
        params.append(date_from)

    if date_to:
        where.append("data_publicacao <= ?")
        params.append(date_to)

    if q:
        qv = f"%{q.strip()}%"
        where.append("(titulo LIKE ? OR sumario LIKE ?)")
        params.extend([qv, qv])


def _order_by_sql(order_by: OrderBy) -> str:
    mapping = {
        "data_publicacao_desc": "data_publicacao DESC, id DESC",
        "data_publicacao_asc": "data_publicacao ASC, id ASC",
        "ano_desc": "ano DESC, numero DESC, id DESC",
        "ano_asc": "ano ASC, numero ASC, id ASC",
        "id_desc": "id DESC",
        "id_asc": "id ASC",
    }
    return mapping.get(order_by, mapping["data_publicacao_desc"])


def list_diplomas(
    *,
    tipo: str | None = None,
    tipo_in: Iterable[str] | None = None,
    tipo_not_in: Iterable[str] | None = None,
    ano: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    order_by: OrderBy = "data_publicacao_desc",
) -> list[dict[str, Any]]:
    """List diplomas with optional filters. Dates should be YYYY-MM-DD."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    sql = """
    SELECT
      id,
      tipo_slug,
      tipo,
      numero,
      ano,
      data_publicacao,
      titulo,
      sumario,
      url_detalhe,
      url_pdf,
      estado
    FROM diplomas
    """
    where: list[str] = []
    params: list[Any] = []

    _apply_filters(
        where,
        params,
        tipo=tipo,
        tipo_in=tipo_in,
        tipo_not_in=tipo_not_in,
        ano=ano,
        date_from=date_from,
        date_to=date_to,
        q=q,
    )

    if where:
        sql += " WHERE " + " AND ".join(where)

    sql += f" ORDER BY {_order_by_sql(order_by)} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = _get_db_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]
    finally:
        conn.close()


def stats_by_tipo(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    top: int = 50,
) -> list[dict[str, Any]]:
    top = max(1, min(int(top), 500))
    sql = """
    SELECT
      COALESCE(tipo_slug, '') AS tipo_slug,
      COUNT(*) AS n
    FROM diplomas
    """
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, date_from=date_from, date_to=date_to, q=q)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY COALESCE(tipo_slug, '') ORDER BY n DESC, tipo_slug ASC LIMIT ?"
    params.append(top)

    conn = _get_db_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]
    finally:
        conn.close()


def export_csv(
    out_path: str | Path,
    *,
    delimiter: str = ";",
    **kwargs: Any,
) -> Path:
    """Export filtered diplomas to CSV. kwargs forwarded to list_diplomas()."""
    outp = Path(out_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    rows = list_diplomas(**kwargs)

    fieldnames = [
        "id",
        "tipo_slug",
        "tipo",
        "numero",
        "ano",
        "data_publicacao",
        "titulo",
        "sumario",
        "url_detalhe",
        "url_pdf",
        "estado",
    ]
    with outp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    return outp
