# src/etl/import_csv.py
from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db.db import get_conn, init_db
from ..processing.indexador import upsert_diploma
from ..utils.csv_utils import open_csv_reader


@dataclass
class ImportStats:
    ok: int = 0
    skipped: int = 0
    errors: int = 0


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


def get_existing(tipo: str, numero: str, ano: int, db_path: Path | None = None) -> dict[str, Any]:
    """Lê campos existentes para evitar sobrescrever com vazio."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                id_dr, data_publicacao, titulo, sumario,
                url_detalhe, url_pdf, url_consolidado,
                resumo_1_frase, observacoes, estado
            FROM diplomas
            WHERE tipo=? AND numero=? AND ano=?
            """,
            (tipo, numero, ano),
        ).fetchone()

    return dict(row) if row else {}


def pick(incoming: str | None, existing: str | None) -> str | None:
    """Se incoming vazio, mantém existing."""
    inc = norm(incoming)
    if inc:
        return inc
    return existing


def map_row(row: dict[str, str], db_path: Path | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """
    Mapeia linhas de CSV para o esquema do upsert_diploma.
    Retorna (reg, erro) — reg None quando há erro.
    """
    tipo = norm(row.get("tipo"))
    numero = norm(row.get("numero"))
    ano = to_int(row.get("ano") or "")

    if not tipo or not numero or not ano:
        return None, "Campos obrigatórios em falta (tipo/numero/ano)."

    existing = get_existing(tipo, numero, int(ano), db_path=db_path)

    # título: export ou seed
    titulo_in = row.get("titulo")
    if not norm(titulo_in):
        titulo_in = row.get("titulo_curto")

    # url detalhe: export ou seed
    url_det_in = row.get("url_detalhe")
    if not norm(url_det_in):
        url_det_in = row.get("url_detalhe_dr")

    # url pdf: export ou seed
    url_pdf_in = row.get("url_pdf")
    if not norm(url_pdf_in):
        url_pdf_in = row.get("url_pdf_direto")

    # sumario (só mexe se existir coluna no CSV)
    sumario_in = row.get("sumario")

    resumo_1_frase_in = row.get("resumo_1_frase", "")
    observacoes_in = row.get("observacoes", "")

    # guarda "categoria" nas observações (sem mudar schema)
    categoria = norm(row.get("categoria"))
    if categoria:
        obs = norm(observacoes_in)
        tag = f"[categoria: {categoria}]"
        observacoes_in = f"{tag} {obs}".strip() if obs else tag

    data_pub_in = row.get("data_publicacao")
    estado_in = row.get("estado")

    reg: dict[str, Any] = {
        "id_dr": pick(row.get("id_dr"), existing.get("id_dr")),
        "tipo": tipo,
        "numero": numero,
        "ano": int(ano),
        "data_publicacao": pick(data_pub_in, existing.get("data_publicacao")),
        "titulo": pick(titulo_in, existing.get("titulo")),
        "sumario": existing.get("sumario")
        if sumario_in is None
        else pick(sumario_in, existing.get("sumario")),
        "url_detalhe": pick(url_det_in, existing.get("url_detalhe")),
        "url_pdf": pick(url_pdf_in, existing.get("url_pdf")),
        "url_consolidado": pick(row.get("url_consolidado"), existing.get("url_consolidado")),
        "resumo_1_frase": pick(resumo_1_frase_in, existing.get("resumo_1_frase")) or "",
        "observacoes": pick(observacoes_in, existing.get("observacoes")) or "",
        "estado": pick(estado_in, existing.get("estado")) or "desconhecido",
    }

    return reg, None


def import_csv(
    path: Path,
    delimiter: str = ";",
    db_path: Path | None = None,
    verbose: bool = True,
) -> ImportStats:
    """
    Importa diplomas a partir de CSV e faz upsert na BD.
    Retorna estatísticas (ok/skipped/errors).
    """
    init_db(db_path=db_path)

    if not path.exists():
        raise FileNotFoundError(f"CSV não encontrado: {path}")

    stats = ImportStats()

    f, reader, enc = open_csv_reader(path, delimiter=delimiter)
    if verbose:
        print(f"📄 CSV lido com encoding: {enc}")

    try:
        for i, row in enumerate(reader, start=2):
            reg, err = map_row(row, db_path=db_path)
            if err:
                stats.errors += 1
                if verbose:
                    print(f"❌ Linha {i}: {err} | row={row}")
                continue

            # evita upserts vazios (sem qualquer informação útil além da chave)
            if not (
                norm(reg.get("titulo", ""))
                or norm(reg.get("url_detalhe", ""))
                or norm(reg.get("url_pdf", ""))
                or norm(reg.get("sumario", ""))
            ):
                stats.skipped += 1
                continue

            upsert_diploma(reg)
            stats.ok += 1
    finally:
        f.close()

    if verbose:
        print(f"✅ Import concluído: {stats.ok} upserts | {stats.skipped} ignoradas | {stats.errors} erros")

    return stats


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Importa diplomas de um CSV para a BD (upsert)")
    ap.add_argument("--csv", default="data/index/leis_renovaveis.csv", help="Caminho do CSV")
    ap.add_argument("--delimiter", default=";", help="Separador (por defeito ';')")
    ap.add_argument("--db", default=None, help="Caminho da BD SQLite (opcional)")
    ap.add_argument("--quiet", action="store_true", help="Silencia logs (exceto erros fatais)")
    args = ap.parse_args(argv)

    csv_path = Path(args.csv)
    db_path = Path(args.db) if args.db else None
    import_csv(csv_path, delimiter=args.delimiter, db_path=db_path, verbose=not args.quiet)


if __name__ == "__main__":
    main()
