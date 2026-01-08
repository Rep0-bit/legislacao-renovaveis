# src/reports/gerar_relatorio_da_bd.py
from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from ..core.paths import REPORTS_DIR, ensure_app_dirs
from ..db.db import get_conn, init_db

ensure_app_dirs()
DEFAULT_OUT_DIR = REPORTS_DIR


SQL = """
SELECT
    tipo, numero, ano,
    titulo,
    url_detalhe, url_pdf,
    id_dr,
    hash_fonte
FROM diplomas
ORDER BY ano DESC, tipo ASC, numero ASC
"""


HEADERS = [
    "status",
    "manual",
    "tipo",
    "numero",
    "ano",
    "titulo",
    "url_detalhe",
    "url_pdf",
    "id_dr",
    "old_hash",
    "new_hash",
]


def gerar_relatorio_seed(out_dir: Path = DEFAULT_OUT_DIR, db_path: Path | None = None) -> Path:
    """
    Gera um relatório CSV a partir do estado atual da BD, no formato de 'relatório de coleta',
    marcando tudo como 'novo' e 'manual=sim' (útil para seeds ou auditoria do estado atual).

    Retorna o caminho do relatório gerado.
    """
    init_db(db_path=db_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"relatorio_bd_seed_{ts}.csv"

    with get_conn(db_path) as conn:
        rows = conn.execute(SQL).fetchall()

    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS, delimiter=";")
        w.writeheader()
        for row in rows:
            w.writerow(
                {
                    "status": "novo",
                    "manual": "sim",  # seed é curado/manual por definição
                    "tipo": row["tipo"],
                    "numero": row["numero"],
                    "ano": row["ano"],
                    "titulo": row["titulo"] or "",
                    "url_detalhe": row["url_detalhe"] or "",
                    "url_pdf": row["url_pdf"] or "",
                    "id_dr": row["id_dr"] or "",
                    "old_hash": "",
                    "new_hash": row["hash_fonte"] or "",
                }
            )

    return out_path


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Gera relatório (seed/auditoria) a partir da BD")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Diretório de saída do relatório")
    ap.add_argument("--db", default=None, help="Caminho da BD SQLite (opcional)")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    db_path = Path(args.db) if args.db else None

    out_path = gerar_relatorio_seed(out_dir=out_dir, db_path=db_path)

    # conta linhas de forma simples (pelo query já feito seria len(rows), mas aqui não reconsulta)
    print(f"🧾 Relatório gerado: {out_path}")


if __name__ == "__main__":
    main()
