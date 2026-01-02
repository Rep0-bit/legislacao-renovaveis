# src/qa/validar_csv_vs_bd.py
from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..db.db import get_conn, init_db
from ..utils.csv_utils import open_csv_reader

Key = tuple[str, str, int]  # (tipo, numero, ano)


@dataclass
class ValidationReport:
    csv_count: int
    db_count: int
    common_count: int
    only_csv: list[Key]
    only_db: list[Key]
    diffs: list[str]


def norm(s: str | None) -> str:
    return (s or "").strip()


def to_int(s: str) -> int | None:
    s = norm(s)
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def load_csv_keys(path: Path, delimiter: str = ";", verbose: bool = True) -> dict[Key, dict[str, str]]:
    rows: dict[Key, dict[str, str]] = {}

    f, reader, enc = open_csv_reader(path, delimiter=delimiter)
    if verbose:
        print(f"📄 CSV lido com encoding: {enc}")

    try:
        for row in reader:
            tipo = norm(row.get("tipo"))
            numero = norm(row.get("numero"))
            ano = to_int(row.get("ano") or "")
            if not tipo or not numero or not ano:
                continue
            rows[(tipo, numero, int(ano))] = row
    finally:
        f.close()

    return rows


def load_db_keys(db_path: Path | None = None) -> dict[Key, dict[str, str | None]]:
    """
    Lê a BD e devolve:
      - dict: key -> campos principais
    """
    init_db(db_path=db_path)
    with get_conn(db_path) as conn:
        data = conn.execute(
            """
            SELECT
                tipo, numero, ano,
                id_dr,
                data_publicacao,
                titulo, sumario,
                url_detalhe, url_pdf, url_consolidado,
                resumo_1_frase, observacoes,
                estado,
                ultima_verificacao
            FROM diplomas
            """
        ).fetchall()

    keys: dict[Key, dict[str, str | None]] = {}
    for row in data:
        tipo = row["tipo"]
        numero = row["numero"]
        ano = int(row["ano"])

        k = (str(tipo), str(numero), ano)
        keys[k] = {
            "id_dr": row["id_dr"],
            "data_publicacao": row["data_publicacao"],
            "titulo": row["titulo"],
            "sumario": row["sumario"],
            "url_detalhe": row["url_detalhe"],
            "url_pdf": row["url_pdf"],
            "url_consolidado": row["url_consolidado"],
            "resumo_1_frase": row["resumo_1_frase"],
            "observacoes": row["observacoes"],
            "estado": row["estado"],
            "ultima_verificacao": row["ultima_verificacao"],
        }
    return keys


def csv_get(row: dict[str, str], *names: str) -> str:
    """Devolve o primeiro campo não-vazio entre names."""
    for n in names:
        v = norm(row.get(n))
        if v:
            return v
    return ""


def compare_common(
    csv_rows: dict[Key, dict[str, str]],
    db_rows: dict[Key, dict[str, str | None]],
    fields: list[str],
) -> list[str]:
    """
    Para chaves comuns, compara campos selecionados.
    Retorna lista de strings de diferenças (apenas quando ambos têm valor e são diferentes).
    """
    diffs: list[str] = []

    for k in sorted(set(csv_rows.keys()) & set(db_rows.keys())):
        csv_row = csv_rows[k]
        db_row = db_rows[k]
        tipo, numero, ano = k

        for f in fields:
            if f == "titulo":
                csv_val = csv_get(csv_row, "titulo", "titulo_curto")
                db_val = norm(db_row.get("titulo"))
            elif f == "url_detalhe":
                csv_val = csv_get(csv_row, "url_detalhe", "url_detalhe_dr")
                db_val = norm(db_row.get("url_detalhe"))
            elif f == "url_pdf":
                csv_val = csv_get(csv_row, "url_pdf", "url_pdf_direto")
                db_val = norm(db_row.get("url_pdf"))
            elif f == "data_publicacao":
                csv_val = csv_get(csv_row, "data_publicacao")
                db_val = norm(db_row.get("data_publicacao"))
            elif f == "estado":
                csv_val = csv_get(csv_row, "estado")
                db_val = norm(db_row.get("estado"))
            else:
                csv_val = csv_get(csv_row, f)
                db_val = norm(db_row.get(f))

            if csv_val and db_val and csv_val != db_val:
                diffs.append(f"{tipo} {numero}/{ano} | campo '{f}' difere | CSV='{csv_val}' | BD='{db_val}'")

    return diffs


def validate(
    csv_path: Path,
    delimiter: str = ";",
    db_path: Path | None = None,
    show_diffs: bool = False,
    verbose: bool = True,
) -> ValidationReport:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV não encontrado: {csv_path}")

    csv_rows = load_csv_keys(csv_path, delimiter=delimiter, verbose=verbose)
    db_rows = load_db_keys(db_path=db_path)

    csv_keys = set(csv_rows.keys())
    db_keys = set(db_rows.keys())

    only_csv = sorted(csv_keys - db_keys)
    only_db = sorted(db_keys - csv_keys)
    common = sorted(csv_keys & db_keys)

    diffs: list[str] = []
    if show_diffs:
        diffs = compare_common(
            csv_rows,
            db_rows,
            fields=["titulo", "data_publicacao", "url_detalhe", "url_pdf", "estado"],
        )

    return ValidationReport(
        csv_count=len(csv_keys),
        db_count=len(db_keys),
        common_count=len(common),
        only_csv=only_csv,
        only_db=only_db,
        diffs=diffs,
    )


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Valida consistência entre CSV e BD")
    ap.add_argument("--csv", default="data/index/leis_renovaveis.csv", help="Caminho do CSV")
    ap.add_argument("--delimiter", default=";", help="Separador do CSV (por defeito ';')")
    ap.add_argument("--db", default=None, help="Caminho da BD SQLite (opcional)")
    ap.add_argument("--show-diffs", action="store_true", help="Mostrar diferenças em campos comuns")
    ap.add_argument("--fail-on-diff", action="store_true", help="Sai com código != 0 se houver diferenças")
    ap.add_argument("--quiet", action="store_true", help="Silencia logs")
    args = ap.parse_args(argv)

    csv_path = Path(args.csv)
    db_path = Path(args.db) if args.db else None

    report = validate(
        csv_path,
        delimiter=args.delimiter,
        db_path=db_path,
        show_diffs=args.show_diffs,
        verbose=not args.quiet,
    )

    if not args.quiet:
        print(f"📄 CSV: {report.csv_count} chaves válidas")
        print(f"🗄️  BD:  {report.db_count} chaves")
        print(f"🤝 Em comum: {report.common_count}")
        print(f"➕ Só no CSV (faltam na BD): {len(report.only_csv)}")
        print(f"➖ Só na BD (não estão no CSV): {len(report.only_db)}")

        if report.only_csv:
            print("\n--- Só no CSV (faltam na BD) ---")
            for tipo, numero, ano in report.only_csv[:200]:
                print(f"- {tipo} {numero}/{ano}")
            if len(report.only_csv) > 200:
                print(f"... ({len(report.only_csv) - 200} mais)")

        if report.only_db:
            print("\n--- Só na BD (não estão no CSV) ---")
            for tipo, numero, ano in report.only_db[:200]:
                print(f"- {tipo} {numero}/{ano}")
            if len(report.only_db) > 200:
                print(f"... ({len(report.only_db) - 200} mais)")

        if args.show_diffs:
            print(f"\n🔍 Diferenças (campos comuns, ambos com valor): {len(report.diffs)}")
            for d in report.diffs[:200]:
                print("- " + d)
            if len(report.diffs) > 200:
                print(f"... ({len(report.diffs) - 200} mais)")

    has_diff = bool(report.only_csv or report.only_db or report.diffs)
    if args.fail_on_diff and has_diff:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
