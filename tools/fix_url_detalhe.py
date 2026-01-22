# fix_url_detalhe.py
# Corrige url_detalhe na BD quando vem no formato antigo:
#   https://diariodarepublica.pt/dr/detalhe/<tipo>/<numero>-<id_dr>
# para o formato canónico:
#   https://diariodarepublica.pt/dr/detalhe/<tipo>/<numero>-<ano>-<id_dr>
#
# Uso (PowerShell):
#   python .\fix_url_detalhe.py
#
from __future__ import annotations

import re
import sqlite3

from src.db.db import DB_PATH

RE_OLD = re.compile(r"^(https?://diariodarepublica\.pt/dr/detalhe/[^/]+/)(\d+)-(\d+)$")


def compute_new_url(url: str, ano: int) -> str | None:
    m = RE_OLD.match(url.strip())
    if not m:
        return None
    prefix, numero, id_dr = m.group(1), m.group(2), m.group(3)
    return f"{prefix}{numero}-{int(ano)}-{id_dr}"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT id, ano, url_detalhe FROM diplomas WHERE url_detalhe IS NOT NULL AND url_detalhe <> ''"
    ).fetchall()

    to_update: list[tuple[str, int]] = []
    for _id, ano, url in rows:
        new = compute_new_url(str(url), int(ano))
        if new and new != url:
            to_update.append((new, int(_id)))

    print(f"Encontrados {len(to_update)} url_detalhe para corrigir.")

    if to_update:
        cur.executemany("UPDATE diplomas SET url_detalhe=? WHERE id=?", to_update)
        conn.commit()

    sample = cur.execute("SELECT id, url_detalhe FROM diplomas ORDER BY id").fetchall()
    print("Linhas:")
    for r in sample:
        print(r)

    conn.close()
    print("OK ✅")


if __name__ == "__main__":
    main()
