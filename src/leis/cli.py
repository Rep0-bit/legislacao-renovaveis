from __future__ import annotations

import argparse

from . import api


def _parse_csv_list(s: str) -> list[str]:
    return [p.strip() for p in (s or "").split(",") if p.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m src.leis")
    ap.add_argument(
        "--db",
        default="",
        help="Caminho para a SQLite DB (override). Se vazio, usa DB_PATH do projeto.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="Lista diplomas (com filtros).")
    p_list.add_argument("--tipo", default="", help="tipo_slug exato (ex.: portaria)")
    p_list.add_argument(
        "--tipo-in", default="", help="CSV de tipo_slug a incluir (ex.: portaria,decreto-lei)"
    )
    p_list.add_argument("--tipo-not-in", default="", help="CSV de tipo_slug a excluir")
    p_list.add_argument("--ano", type=int, default=0, help="Ano (ex.: 2026)")
    p_list.add_argument("--from", dest="date_from", default="", help="Data publicação >= YYYY-MM-DD")
    p_list.add_argument("--to", dest="date_to", default="", help="Data publicação <= YYYY-MM-DD")
    p_list.add_argument("--q", default="", help="Pesquisa em titulo/sumario/numero/numero_display (LIKE)")
    p_list.add_argument("--limit", type=int, default=20, help="Máximo de linhas (1..500)")
    p_list.add_argument("--offset", type=int, default=0, help="Offset")
    p_list.add_argument(
        "--order-by",
        default="data_publicacao_desc",
        choices=[
            "data_publicacao_desc",
            "data_publicacao_asc",
            "ano_desc",
            "ano_asc",
            "id_desc",
            "id_asc",
        ],
        help="Ordenação",
    )

    p_stats = sub.add_parser("stats-tipo", help="Contagens por tipo_slug.")
    p_stats.add_argument("--from", dest="date_from", default="", help="Data publicação >= YYYY-MM-DD")
    p_stats.add_argument("--to", dest="date_to", default="", help="Data publicação <= YYYY-MM-DD")
    p_stats.add_argument("--q", default="", help="Pesquisa em titulo/sumario/numero/numero_display (LIKE)")
    p_stats.add_argument("--top", type=int, default=50, help="Top N (1..500)")

    p_export = sub.add_parser("export", help="Exporta diplomas para CSV.")
    p_export.add_argument("--out", required=True, help="Caminho do CSV de saída")
    p_export.add_argument("--tipo", default="", help="tipo_slug exato (ex.: portaria)")
    p_export.add_argument("--tipo-in", default="", help="CSV de tipo_slug a incluir")
    p_export.add_argument("--tipo-not-in", default="", help="CSV de tipo_slug a excluir")
    p_export.add_argument("--ano", type=int, default=0, help="Ano (ex.: 2026)")
    p_export.add_argument("--from", dest="date_from", default="", help="Data publicação >= YYYY-MM-DD")
    p_export.add_argument("--to", dest="date_to", default="", help="Data publicação <= YYYY-MM-DD")
    p_export.add_argument("--q", default="", help="Pesquisa em titulo/sumario/numero/numero_display (LIKE)")
    p_export.add_argument("--limit", type=int, default=500, help="Máximo de linhas (1..500)")
    p_export.add_argument("--offset", type=int, default=0, help="Offset")
    p_export.add_argument(
        "--order-by",
        default="data_publicacao_desc",
        choices=[
            "data_publicacao_desc",
            "data_publicacao_asc",
            "ano_desc",
            "ano_asc",
            "id_desc",
            "id_asc",
        ],
        help="Ordenação",
    )
    p_export.add_argument("--delimiter", default=";", help="Separador CSV (default ; )")

    args = ap.parse_args()
    db_path = args.db or None

    def _default_order(for_tipo: bool, user_value: str | None) -> str:
        if user_value:
            return user_value
        return "ano_desc" if for_tipo else "data_publicacao_desc"

    if args.cmd == "list":
        order_by = _default_order(bool(args.tipo), args.order_by)

        rows = api.list_diplomas(
            db_path=db_path,
            tipo=args.tipo or None,
            tipo_in=_parse_csv_list(args.tipo_in),
            tipo_not_in=_parse_csv_list(args.tipo_not_in),
            ano=args.ano or None,
            date_from=args.date_from or None,
            date_to=args.date_to or None,
            q=args.q or None,
            limit=args.limit,
            offset=args.offset,
            order_by=order_by,
        )

        if not rows:
            print("ℹ️ Sem resultados.")
            return

        for r in rows:
            print(
                f"{r.get('data_publicacao', '')}\t{r.get('tipo_slug', '')}\t{r.get('ano', '')}/{r.get('numero', '')}\t{r.get('titulo', '')}"
            )

    elif args.cmd == "stats-tipo":
        rows = api.stats_by_tipo(
            db_path=db_path,
            date_from=args.date_from or None,
            date_to=args.date_to or None,
            q=args.q or None,
            top=args.top,
        )
        for r in rows:
            print(f"{r.get('tipo_slug', '') or '—'}\t{r.get('n', 0)}")

    elif args.cmd == "export":
        order_by = _default_order(bool(args.tipo), args.order_by)

        outp = api.export_csv(
            args.out,
            db_path=db_path,
            delimiter=args.delimiter,
            tipo=args.tipo or None,
            tipo_in=_parse_csv_list(args.tipo_in),
            tipo_not_in=_parse_csv_list(args.tipo_not_in),
            ano=args.ano or None,
            date_from=args.date_from or None,
            date_to=args.date_to or None,
            q=args.q or None,
            limit=args.limit,
            offset=args.offset,
            order_by=order_by,
        )
        print(f"✅ Exportado: {outp}")
    else:
        raise SystemExit(f"Comando desconhecido: {args.cmd}")


if __name__ == "__main__":
    main()
