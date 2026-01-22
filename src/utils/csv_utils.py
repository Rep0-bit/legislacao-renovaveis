# src/utils/csv_utils.py
from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO


def open_csv_reader(
    path: Path,
    delimiter: str = ";",
    encodings: Iterable[str] = ("utf-8-sig", "cp1252", "latin-1"),
) -> tuple[TextIO, csv.DictReader, str]:
    """
    Abre CSV tentando vários encodings comuns (Excel/Windows).
    Retorna (file_handle, DictReader, encoding_usado).

    Nota: o file_handle deve ser fechado por quem chama.
    """
    if not path.exists():
        raise FileNotFoundError(f"CSV não encontrado: {path}")

    last_err: Exception | None = None

    for enc in encodings:
        try:
            f = path.open("r", encoding=enc, newline="")
            reader = csv.DictReader(f, delimiter=delimiter)

            # força leitura de cabeçalho
            fieldnames = reader.fieldnames
            if not fieldnames or not any((n or "").strip() for n in fieldnames):
                f.close()
                raise ValueError("CSV sem cabeçalhos.")

            # normaliza nomes de colunas (strip)
            reader.fieldnames = [n.strip() if n else "" for n in fieldnames]

            return f, reader, enc

        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception as e:
            # erro não relacionado com encoding (ex.: CSV vazio/sem cabeçalho)
            last_err = e
            break

    raise last_err or UnicodeDecodeError(
        "utf-8",
        b"",
        0,
        1,
        "Não foi possível decodificar o CSV com encodings padrão.",
    )
