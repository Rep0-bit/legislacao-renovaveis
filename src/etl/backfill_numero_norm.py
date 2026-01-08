# src/etl/backfill_numero_norm.py
from __future__ import annotations

import argparse

from ..db.db import get_conn, init_db
from ..utils.normalize import (
    normalize_numero,
    normalize_numero_display,
    normalize_search_text,
    slugify_ascii_kebab,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dry-run", action="store_true", help="Mostra quantas linhas seriam atualizadas e termina."
    )
    args = ap.parse_args()

    init_db()

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, tipo, numero, ano, tipo_slug, numero_norm, numero_display, titulo, sumario, titulo_norm, sumario_norm
            FROM diplomas
            """
        ).fetchall()

        to_update: list[tuple[str, str, str, int]] = []
        for r in rows:
            _id = int(r["id"])
            tipo = str(r["tipo"] or "")
            numero = str(r["numero"] or "")
            ano = int(r["ano"] or 0)

            want_tipo_slug = slugify_ascii_kebab(str(r["tipo_slug"] or "")) or slugify_ascii_kebab(tipo)
            want_num_norm = normalize_numero(numero)
            want_num_display = normalize_numero_display(numero, ano=ano)
            want_titulo_norm = normalize_search_text(str(r["titulo"] or ""))
            want_sumario_norm = normalize_search_text(str(r["sumario"] or ""))

            cur_tipo_slug = str(r["tipo_slug"] or "")
            cur_num_norm = str(r["numero_norm"] or "")
            cur_num_display = str(r["numero_display"] or "")
            cur_titulo_norm = str(r["titulo_norm"] or "")
            cur_sumario_norm = str(r["sumario_norm"] or "")

            if (
                (cur_tipo_slug or "") != (want_tipo_slug or "")
                or (cur_num_norm or "") != (want_num_norm or "")
                or (cur_num_display or "") != (want_num_display or "")
                or (cur_titulo_norm or "") != (want_titulo_norm or "")
                or (cur_sumario_norm or "") != (want_sumario_norm or "")
            ):
                to_update.append(
                    (
                        want_tipo_slug,
                        want_num_norm,
                        want_num_display,
                        want_titulo_norm,
                        want_sumario_norm,
                        _id,
                    )
                )

        if args.dry_run:
            print(f"[dry-run] rows_to_update={len(to_update)}")
            return

        conn.executemany(
            "UPDATE diplomas SET tipo_slug=?, numero_norm=?, numero_display=?, titulo_norm=?, sumario_norm=? WHERE id=?",
            to_update,
        )
        conn.commit()
        print(f"✅ ok, rows_updated={len(to_update)}")


if __name__ == "__main__":
    main()
