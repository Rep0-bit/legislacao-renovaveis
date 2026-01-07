from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from src.db.db import DB_PATH

SQL_COUNT = """
SELECT COUNT(*)
FROM diplomas
WHERE tipo_slug IS NULL OR tipo_slug = ''
"""

SQL_UPDATE = """
UPDATE diplomas
SET tipo_slug = LOWER(REPLACE(tipo, '_', '-'))
WHERE tipo_slug IS NULL OR tipo_slug = ''
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill de tipo_slug a partir de tipo.")
    ap.add_argument("--dry-run", action="store_true", help="Mostra quantas linhas seriam atualizadas e sai.")
    ap.add_argument(
        "--db",
        default="",
        help="Caminho para a SQLite DB (override). Se vazio, usa DB_PATH do projeto.",
    )
    args = ap.parse_args()

    db_path = Path(args.db).resolve() if args.db else Path(DB_PATH).resolve()
    if not db_path.exists():
        raise SystemExit(f"❌ DB não encontrada: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(SQL_COUNT)
        to_update = int(cur.fetchone()[0])

        if args.dry_run:
            print(f"[dry-run] rows_to_update={to_update}")
            return

        cur.execute(SQL_UPDATE)
        conn.commit()

        # Nota: em sqlite3, rowcount é OK para UPDATEs deste tipo.
        print(f"✅ ok, rows_updated={cur.rowcount} (estimated_before={to_update})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
