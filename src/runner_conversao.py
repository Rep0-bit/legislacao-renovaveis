# src/runner_conversao.py
from __future__ import annotations

import argparse
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db.db import get_conn, init_db
from .processing.conversao import converter
from .utils.csv_utils import open_csv_reader

REPORTS_DIR = Path("data/index/reports")


def _agora_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _migrar_schema_conversao(db_path: Path | None = None) -> None:
    """
    Migração leve (idempotente) para guardar o resultado da conversão.
    """
    with get_conn(db_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(diplomas)").fetchall()}

        def add_col(sql: str) -> None:
            with suppress(Exception):
                conn.execute(sql)

        if "conv_ok" not in cols:
            add_col("ALTER TABLE diplomas ADD COLUMN conv_ok INTEGER;")
        if "conv_at" not in cols:
            add_col("ALTER TABLE diplomas ADD COLUMN conv_at TEXT;")
        if "conv_doc_id" not in cols:
            add_col("ALTER TABLE diplomas ADD COLUMN conv_doc_id TEXT;")
        if "conv_meta_path" not in cols:
            add_col("ALTER TABLE diplomas ADD COLUMN conv_meta_path TEXT;")
        if "conv_error" not in cols:
            add_col("ALTER TABLE diplomas ADD COLUMN conv_error TEXT;")

        conn.commit()


def marcar_resultado(
    tipo: str,
    numero: str,
    ano: int,
    ok: bool,
    doc_id: str | None,
    meta_path: str | None,
    err: str | None,
    db_path: Path | None = None,
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE diplomas
            SET conv_ok=?, conv_at=?, conv_doc_id=?, conv_meta_path=?, conv_error=?
            WHERE tipo=? AND numero=? AND ano=?
            """,
            (
                1 if ok else 0,
                _agora_utc_iso(),
                doc_id,
                meta_path,
                err,
                tipo,
                numero,
                int(ano),
            ),
        )
        conn.commit()


def find_latest_report(reports_dir: Path = REPORTS_DIR) -> Path | None:
    if not reports_dir.exists():
        return None
    reports = sorted(reports_dir.glob("relatorio_coleta_*.csv"))
    return reports[-1] if reports else None


def read_report_targets(report_path: Path, delimiter: str = ";") -> list[tuple[str, str, int]]:
    """
    Lê relatório do coletor e devolve lista de chaves (tipo, numero, ano) onde status in (novo, atualizado).
    """
    targets: list[tuple[str, str, int]] = []

    f, reader, enc = open_csv_reader(report_path, delimiter=delimiter)
    print(f"📄 Relatório lido com encoding: {enc}")

    try:
        for row in reader:
            status = (row.get("status") or "").strip().lower()
            if status not in ("novo", "atualizado"):
                continue

            tipo = (row.get("tipo") or "").strip()
            numero = (row.get("numero") or "").strip()
            ano_s = (row.get("ano") or "").strip()
            if not (tipo and numero and ano_s.isdigit()):
                continue

            targets.append((tipo, numero, int(ano_s)))
    finally:
        f.close()

    return targets


def fetch_from_db(tipo: str, numero: str, ano: int, db_path: Path | None = None):
    with get_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT tipo, numero, ano, url_detalhe, url_pdf, conv_ok
            FROM diplomas
            WHERE tipo=? AND numero=? AND ano=?
            """,
            (tipo, numero, int(ano)),
        ).fetchone()


def listar_para_converter_modo_bd(
    only_missing: bool,
    limit: int | None,
    db_path: Path | None = None,
):
    where = "WHERE url_pdf IS NOT NULL AND url_pdf <> ''"
    if only_missing:
        where += " AND (conv_ok IS NULL OR conv_ok <> 1)"

    q = f"""
        SELECT tipo, numero, ano, url_detalhe, url_pdf, conv_ok
        FROM diplomas
        {where}
        ORDER BY ano DESC, tipo ASC, numero ASC
    """
    if limit:
        q += f" LIMIT {int(limit)}"

    with get_conn(db_path) as conn:
        return conn.execute(q).fetchall()


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Converte PDFs associados aos diplomas e guarda texto/meta em disco."
    )

    mode = ap.add_mutually_exclusive_group(required=False)
    mode.add_argument(
        "--from-latest-report",
        action="store_true",
        help="Converter apenas novos/atualizados do relatório mais recente",
    )
    mode.add_argument(
        "--from-report",
        default=None,
        help="Converter apenas novos/atualizados de um relatório específico (caminho)",
    )
    mode.add_argument("--from-db", action="store_true", help="Converter da BD (default)")

    ap.add_argument("--db", default=None, help="Caminho da BD SQLite (opcional)")
    ap.add_argument(
        "--out-dir",
        default="data",
        help="Diretório base de saída (raw_html/pdfs/text/meta). Default: data",
    )

    ap.add_argument(
        "--only-missing",
        action="store_true",
        help="Só converter o que ainda não foi convertido (default recomendado)",
    )
    ap.add_argument("--reprocess", action="store_true", help="Reprocessar mesmo que já tenha conv_ok=1")
    ap.add_argument("--limit", type=int, default=None, help="Limitar nº de conversões nesta execução")
    ap.add_argument("--report-delimiter", default=";", help="Separador do relatório do coletor (default ';')")

    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else None
    out_dir = Path(args.out_dir)

    init_db(db_path=db_path)
    _migrar_schema_conversao(db_path=db_path)

    only_missing = True
    if args.reprocess:
        only_missing = False
    if args.only_missing:
        only_missing = True

    rows_to_process: list[tuple[Any, ...]] = []

    # ----- Modo relatório -----
    want_report_mode = args.from_latest_report or bool(args.from_report)

    if want_report_mode:
        report_path = Path(args.from_report) if args.from_report else find_latest_report()

        # fallback: se não existir relatório, cai para BD
        if not report_path or not report_path.exists():
            print("⚠️ Não encontrei relatório (data/index/reports/relatorio_coleta_*.csv).")
            print("➡️ Vou usar fallback: modo BD (url_pdf preenchido).")
            rows_to_process = listar_para_converter_modo_bd(
                only_missing=only_missing,
                limit=args.limit,
                db_path=db_path,
            )
        else:
            targets = read_report_targets(report_path, delimiter=args.report_delimiter)
            print(f"🧾 Relatório: {report_path}")
            print(f"🎯 Alvos no relatório (novo/atualizado): {len(targets)}")

            for tipo, numero, ano in targets:
                row = fetch_from_db(tipo, numero, ano, db_path=db_path)
                if not row:
                    continue

                t, n, a, url_detalhe, url_pdf, conv_ok = row
                if only_missing and conv_ok == 1:
                    continue
                if not url_pdf:
                    continue

                rows_to_process.append((t, n, a, url_detalhe, url_pdf, conv_ok))

            if args.limit is not None:
                rows_to_process = rows_to_process[: int(args.limit)]

    else:
        # default: BD
        rows_to_process = listar_para_converter_modo_bd(
            only_missing=only_missing,
            limit=args.limit,
            db_path=db_path,
        )

    print(f"📥 Para converter: {len(rows_to_process)} (only_missing={only_missing})")

    ok_count = 0
    err_count = 0
    skipped_no_pdf = 0

    for tipo, numero, ano, url_detalhe, url_pdf, _conv_ok in rows_to_process:
        label = f"{tipo} {numero}/{ano}"

        if not url_pdf:
            skipped_no_pdf += 1
            continue

        try:
            meta = converter(url_detalhe=url_detalhe, url_pdf_direto=url_pdf, out_dir=out_dir)
            marcar_resultado(
                tipo,
                numero,
                ano,
                ok=True,
                doc_id=meta.get("doc_id"),
                meta_path=meta.get("ficheiro_meta"),
                err=None,
                db_path=db_path,
            )
            ok_count += 1
            print(f"✅ {label} -> {meta.get('doc_id')}")
        except Exception as e:
            marcar_resultado(
                tipo,
                numero,
                ano,
                ok=False,
                doc_id=None,
                meta_path=None,
                err=str(e)[:2000],
                db_path=db_path,
            )
            err_count += 1
            print(f"❌ {label} -> {e}")

    if skipped_no_pdf:
        print(f"⚠️ Ignorados por falta de url_pdf: {skipped_no_pdf}")

    print(f"\n🏁 Concluído: ok={ok_count} | erro={err_count}")


if __name__ == "__main__":
    main()
