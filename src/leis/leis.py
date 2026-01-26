from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..db.db import DB_PATH


@dataclass
class ImportSummary:
    rows_total: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    report_path: str | None = None


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _open_csv(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")

    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except Exception:
        dialect = csv.get_dialect("excel")

    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    rows: list[dict[str, str]] = []
    for r in reader:
        rows.append({(k or "").strip(): (v or "").strip() for k, v in (r or {}).items()})
    return rows


def _db_columns(conn: sqlite3.Connection) -> set[str]:
    cur = conn.cursor()
    cols = cur.execute("PRAGMA table_info(diplomas)").fetchall()
    return {c[1] for c in cols}  # name


def _colmap() -> dict[str, str]:
    # aliases CSV -> colunas BD
    return {
        "tipo": "tipo",
        "tipo_slug": "tipo_slug",
        "numero": "numero",
        "ano": "ano",
        "data_publicacao": "data_publicacao",
        "titulo": "titulo",
        "sumario": "sumario",
        "url_detalhe": "url_detalhe",
        "link": "url_detalhe",
        "url_pdf": "url_pdf",
        "pdf_url": "url_pdf",
        "url_consolidado": "url_consolidado",
        "id_dr": "id_dr",
        "numero_display": "numero_display",
    }


def _ensure_identity(row_db: dict[str, Any]) -> bool:
    # identidade mínima: um destes, ou tipo+numero+ano
    if row_db.get("id_dr") or row_db.get("url_detalhe") or row_db.get("url_pdf"):
        return True
    return bool(row_db.get("tipo") and row_db.get("numero") and row_db.get("ano"))


def _existing_id(conn: sqlite3.Connection, row_db: dict[str, Any]) -> int | None:
    cur = conn.cursor()

    if row_db.get("id_dr"):
        r = cur.execute("SELECT id FROM diplomas WHERE id_dr = ?", (row_db["id_dr"],)).fetchone()
        if r:
            return int(r[0])

    if row_db.get("url_detalhe"):
        r = cur.execute("SELECT id FROM diplomas WHERE url_detalhe = ?", (row_db["url_detalhe"],)).fetchone()
        if r:
            return int(r[0])

    if row_db.get("url_pdf"):
        r = cur.execute("SELECT id FROM diplomas WHERE url_pdf = ?", (row_db["url_pdf"],)).fetchone()
        if r:
            return int(r[0])

    if row_db.get("tipo") and row_db.get("numero") and row_db.get("ano"):
        r = cur.execute(
            "SELECT id FROM diplomas WHERE tipo = ? AND numero = ? AND ano = ?",
            (row_db["tipo"], row_db["numero"], row_db["ano"]),
        ).fetchone()
        if r:
            return int(r[0])

    return None


def import_csv_to_db(
    *,
    csv_path: Path,
    db_path: Path | None = None,
    mode: str = "upsert",  # upsert|insert
    reset_conversion: bool = False,
    report_dir: Path | None = None,
) -> ImportSummary:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise SystemExit(f"CSV não encontrado: {csv_path}")

    rows = _open_csv(csv_path)
    if not rows:
        raise SystemExit("CSV vazio (0 linhas).")

    mode = (mode or "upsert").strip().casefold()
    if mode not in {"upsert", "insert"}:
        raise SystemExit("--mode tem de ser upsert ou insert")

    dbp = Path(db_path) if db_path else DB_PATH
    report_dir = report_dir or (Path("data") / "reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"import_manual_{_stamp()}.csv"

    summary = ImportSummary(report_path=str(report_path))

    conn = sqlite3.connect(str(dbp))
    conn.execute("PRAGMA foreign_keys = ON;")
    cols_db = _db_columns(conn)

    # colunas que podemos escrever (só se existirem na tabela)
    writable = cols_db.intersection(
        {
            "tipo",
            "tipo_slug",
            "numero",
            "ano",
            "data_publicacao",
            "titulo",
            "sumario",
            "url_detalhe",
            "url_pdf",
            "url_consolidado",
            "id_dr",
            "numero_display",
        }
    )
    conv_cols = [
        c for c in ["conv_ok", "conv_at", "conv_error", "conv_doc_id", "conv_meta_path"] if c in cols_db
    ]
    colmap = _colmap()

    with report_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "row",
                "action",
                "id",
                "msg",
                "tipo",
                "numero",
                "ano",
                "url_detalhe",
                "url_pdf",
                "id_dr",
            ],
        )
        w.writeheader()

        try:
            for i, r in enumerate(rows, start=1):
                summary.rows_total += 1
                try:
                    # normalizar keys
                    row_db: dict[str, Any] = {}
                    for k_csv, v in r.items():
                        k = (k_csv or "").strip()
                        if not k:
                            continue
                        k_norm = k.casefold()
                        if k_norm in colmap:
                            k_db = colmap[k_norm]
                            if k_db in writable and (v or "").strip():
                                row_db[k_db] = (v or "").strip()
                        # aceita também nomes já “certos” (igual à BD)
                        elif k in writable and (v or "").strip():
                            row_db[k] = (v or "").strip()

                    if not _ensure_identity(row_db):
                        summary.skipped += 1
                        w.writerow(
                            {
                                "row": i,
                                "action": "skipped",
                                "id": "",
                                "msg": "Sem identificador (precisa url_detalhe/url_pdf/id_dr ou tipo+numero+ano)",
                                "tipo": row_db.get("tipo", ""),
                                "numero": row_db.get("numero", ""),
                                "ano": row_db.get("ano", ""),
                                "url_detalhe": row_db.get("url_detalhe", ""),
                                "url_pdf": row_db.get("url_pdf", ""),
                                "id_dr": row_db.get("id_dr", ""),
                            }
                        )
                        continue

                    existing = _existing_id(conn, row_db)

                    if existing is None:
                        cols = list(row_db.keys())
                        vals = [row_db[c] for c in cols]
                        placeholders = ",".join(["?"] * len(cols))

                        conn.execute(
                            f"INSERT INTO diplomas ({','.join(cols)}) VALUES ({placeholders})",
                            vals,
                        )
                        new_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

                        if reset_conversion and conv_cols:
                            sets = ",".join([f"{c}=NULL" for c in conv_cols])
                            conn.execute(f"UPDATE diplomas SET {sets} WHERE id = ?", (new_id,))

                        summary.inserted += 1
                        w.writerow(
                            {
                                "row": i,
                                "action": "inserted",
                                "id": new_id,
                                "msg": "",
                                "tipo": row_db.get("tipo", ""),
                                "numero": row_db.get("numero", ""),
                                "ano": row_db.get("ano", ""),
                                "url_detalhe": row_db.get("url_detalhe", ""),
                                "url_pdf": row_db.get("url_pdf", ""),
                                "id_dr": row_db.get("id_dr", ""),
                            }
                        )
                    else:
                        if mode == "insert":
                            summary.skipped += 1
                            w.writerow(
                                {
                                    "row": i,
                                    "action": "skipped",
                                    "id": existing,
                                    "msg": "Já existe (modo insert não atualiza)",
                                    "tipo": row_db.get("tipo", ""),
                                    "numero": row_db.get("numero", ""),
                                    "ano": row_db.get("ano", ""),
                                    "url_detalhe": row_db.get("url_detalhe", ""),
                                    "url_pdf": row_db.get("url_pdf", ""),
                                    "id_dr": row_db.get("id_dr", ""),
                                }
                            )
                            continue

                        # upsert -> UPDATE só dos campos presentes
                        cols = list(row_db.keys())
                        if cols:
                            set_sql = ",".join([f"{c}=?" for c in cols])
                            vals = [row_db[c] for c in cols]
                            conn.execute(f"UPDATE diplomas SET {set_sql} WHERE id = ?", (*vals, existing))

                        if reset_conversion and conv_cols:
                            sets = ",".join([f"{c}=NULL" for c in conv_cols])
                            conn.execute(f"UPDATE diplomas SET {sets} WHERE id = ?", (existing,))

                        summary.updated += 1
                        w.writerow(
                            {
                                "row": i,
                                "action": "updated",
                                "id": existing,
                                "msg": "",
                                "tipo": row_db.get("tipo", ""),
                                "numero": row_db.get("numero", ""),
                                "ano": row_db.get("ano", ""),
                                "url_detalhe": row_db.get("url_detalhe", ""),
                                "url_pdf": row_db.get("url_pdf", ""),
                                "id_dr": row_db.get("id_dr", ""),
                            }
                        )

                except Exception as e:
                    summary.errors += 1
                    w.writerow({"row": i, "action": "error", "id": "", "msg": str(e)})

            conn.commit()
        finally:
            conn.close()

    return summary
