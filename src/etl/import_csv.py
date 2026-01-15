from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db.db import init_db
from ..processing import indexador
from ..utils.csv_utils import open_csv_reader


@dataclass
class ImportStats:
    novo: int = 0
    atualizado: int = 0
    inalterado: int = 0
    errors: int = 0


REQUIRED_COLS = {"tipo", "numero", "ano"}


def _norm(s: str | None) -> str:
    return (s or "").strip()


def validate_row(row: dict[str, str]) -> tuple[bool, str | None]:
    missing = [c for c in REQUIRED_COLS if not _norm(row.get(c))]
    if missing:
        return False, f"Campos obrigatórios em falta: {', '.join(missing)}"
    return True, None


def write_report(rows: list[dict[str, Any]]) -> Path:
    from ..core.paths import REPORTS_DIR, ensure_app_dirs

    ensure_app_dirs()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"import_csv_{ts}.csv"

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    return path


def import_csv(
    path: Path,
    *,
    delimiter: str = ";",
    dry_run: bool = False,
    verbose: bool = True,
) -> ImportStats:
    init_db()

    if not path.exists():
        raise FileNotFoundError(f"CSV não encontrado: {path}")

    stats = ImportStats()
    report_rows: list[dict[str, Any]] = []

    f, reader, enc = open_csv_reader(path, delimiter=delimiter)
    if verbose:
        print(f"📄 CSV lido com encoding: {enc}")

    try:
        for i, row in enumerate(reader, start=2):
            ok, err = validate_row(row)
            if not ok:
                stats.errors += 1
                report_rows.append(
                    {
                        "row": i,
                        "status": "error",
                        "tipo": row.get("tipo"),
                        "numero": row.get("numero"),
                        "ano": row.get("ano"),
                        "manual": "",
                        "note": err,
                    }
                )
                continue

            reg = {
                "tipo": _norm(row.get("tipo")),
                "numero": _norm(row.get("numero")),
                "ano": int(_norm(row.get("ano"))),
            }

            for k in ("titulo", "sumario", "url_detalhe", "url_pdf", "data_publicacao"):
                if _norm(row.get(k)):
                    reg[k] = _norm(row.get(k))

            if dry_run:
                tipo_s = str(reg["tipo"])
                numero_s = str(reg["numero"])
                ano_i = int(reg["ano"])

                existing = indexador._get_existing(tipo_s, numero_s, ano_i)
                new_hash = indexador.make_hash(reg)
                old_hash = existing["hash_fonte"] if existing else None

                if existing is None:
                    status = "novo"
                elif old_hash == new_hash:
                    status = "inalterado"
                else:
                    status = "atualizado"

                is_manual = indexador._is_manual(existing, reg)
                note = "dry-run"
            else:
                status, is_manual, _, _ = indexador.upsert_diploma(reg)
                note = ""

            if status == "novo":
                stats.novo += 1
            elif status == "atualizado":
                stats.atualizado += 1
            else:
                stats.inalterado += 1

            report_rows.append(
                {
                    "row": i,
                    "status": status,
                    "tipo": reg["tipo"],
                    "numero": reg["numero"],
                    "ano": reg["ano"],
                    "manual": is_manual,
                    "note": note,
                }
            )

    finally:
        f.close()

    report_path = write_report(report_rows)

    if verbose:
        print(
            f"🧾 Relatório: {report_path} | "
            f"novo={stats.novo} atualizado={stats.atualizado} "
            f"inalterado={stats.inalterado} erros={stats.errors}"
        )

    return stats


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Importa diplomas de um CSV (upsert)")
    ap.add_argument("--csv", help="Caminho do CSV")
    ap.add_argument("--in", dest="csv_in", help="Alias de --csv")
    ap.add_argument("--delimiter", default=";", help="Separador (por defeito ';')")
    ap.add_argument("--dry-run", action="store_true", help="Simula o upsert sem escrever na BD")
    ap.add_argument("--quiet", action="store_true", help="Silencia logs (exceto erros fatais)")
    args = ap.parse_args(argv)

    csv_path = args.csv_in or args.csv
    if not csv_path:
        ap.error("É obrigatório indicar --csv ou --in")

    import_csv(
        Path(csv_path),
        delimiter=args.delimiter,
        dry_run=args.dry_run,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
