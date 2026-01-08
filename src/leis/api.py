from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

OrderBy = Literal[
    "data_publicacao_desc",
    "data_publicacao_asc",
    "ano_desc",
    "ano_asc",
    "id_desc",
    "id_asc",
]


def _resolve_db_path(db_path: str | Path | None) -> str:
    if db_path:
        return str(Path(db_path).resolve())

    # default do projeto
    from ..db.db import DB_PATH  # type: ignore

    return str(Path(DB_PATH).resolve())


def _get_db_conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    return sqlite3.connect(_resolve_db_path(db_path))


def _row_to_dict(cur: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    cols = [d[0] for d in (cur.description or [])]
    return {cols[i]: row[i] for i in range(len(cols))}


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
        tipo_in_list = [str(t).strip().lower() for t in tipo_in if str(t).strip()]
        if tipo_in_list:
            where.append(f"tipo_slug IN ({','.join(['?'] * len(tipo_in_list))})")
            params.extend(tipo_in_list)

    if tipo_not_in:
        tipo_not_in_list = [str(t).strip().lower() for t in tipo_not_in if str(t).strip()]
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
        from ..utils.normalize import normalize_search_text

        q_raw = q.strip()
        qv = f"%{q_raw}%"
        qv2 = f"%{q_raw.replace('/', '-')}%" if "/" in q_raw else qv

        q_norm = normalize_search_text(q_raw)
        qvn = f"%{q_norm}%" if q_norm else qv

        where.append(
            "("
            "titulo LIKE ? OR sumario LIKE ? "
            "OR numero LIKE ? OR numero_norm LIKE ? "
            "OR numero_display LIKE ? OR numero_display LIKE ? "
            "OR titulo_norm LIKE ? OR sumario_norm LIKE ?"
            ")"
        )
        params.extend([qv, qv, qv, qv, qv, qv2, qvn, qvn])


def _order_by_sql(order_by: OrderBy) -> str:
    mapping: dict[str, str] = {
        "data_publicacao_desc": "data_publicacao DESC, id DESC",
        "data_publicacao_asc": "data_publicacao ASC, id ASC",
        "ano_desc": "ano DESC, COALESCE(numero_norm, numero) DESC, id DESC",
        "ano_asc": "ano ASC, COALESCE(numero_norm, numero) ASC, id ASC",
        "id_desc": "id DESC",
        "id_asc": "id ASC",
    }
    return mapping.get(order_by, mapping["data_publicacao_desc"])


def list_diplomas(
    *,
    db_path: str | Path | None = None,
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
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    # garante schema (ADD COLUMN leves) antes de usar colunas recentes
    from ..db.db import init_db

    init_db(db_path=db_path)

    sql = """
    SELECT
      id,
      tipo_slug,
      tipo,
      numero,
      numero_norm,
      numero_display,
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

    conn = _get_db_conn(db_path)
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
    db_path: str | Path | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    top: int = 50,
) -> list[dict[str, Any]]:
    top = max(1, min(int(top), 500))

    from ..db.db import init_db

    init_db(db_path=db_path)
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

    conn = _get_db_conn(db_path)
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
    db_path: str | Path | None = None,
    delimiter: str = ";",
    **kwargs: Any,
) -> Path:
    outp = Path(out_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    rows = list_diplomas(db_path=db_path, **kwargs)

    fieldnames = [
        "id",
        "tipo_slug",
        "tipo",
        "numero",
        "numero_norm",
        "numero_display",
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
