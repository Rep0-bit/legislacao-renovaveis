# src/etl/exportar_csv.py
from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
from pathlib import Path

from ..core.paths import INDEX_DIR, ensure_app_dirs
from ..db.db import get_conn, init_db

ensure_app_dirs()
DEFAULT_OUT = INDEX_DIR / "leis_renovaveis.csv"

SQL_EXPORT = """
SELECT
    tipo, numero, ano,
    id_dr,
    data_publicacao,
    titulo, sumario,
    resumo_1_frase, observacoes,
    estado,
    url_detalhe, url_pdf, url_consolidado,
    ultima_verificacao
FROM diplomas
ORDER BY ano DESC, tipo ASC, numero ASC
"""


HEADERS = [
    "tipo",
    "numero",
    "ano",
    "id_dr",
    "data_publicacao",
    "titulo",
    "sumario",
    "resumo_1_frase",
    "observacoes",
    "estado",
    "url_detalhe",
    "url_pdf",
    "url_consolidado",
    "ultima_verificacao",
]


def export_csv(out_path: Path, db_path: Path | None = None) -> int:
    """
    Exporta a tabela 'diplomas' para CSV.
    Retorna o número de linhas exportadas.
    """
    init_db(db_path=db_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with get_conn(db_path) as conn:
        rows = conn.execute(SQL_EXPORT).fetchall()

    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(HEADERS)
        # sqlite3.Row é iterável e já vem na ordem do SELECT
        w.writerows(rows)

    return len(rows)


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Exporta diplomas da BD para CSV")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Caminho do CSV de saída")
    ap.add_argument("--db", default=None, help="Caminho da BD SQLite (opcional)")
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    db_path = Path(args.db) if args.db else None

    n = export_csv(out_path, db_path=db_path)
    print(f"✅ CSV exportado ({n} linhas) em: {out_path}")


if __name__ == "__main__":
    main()
