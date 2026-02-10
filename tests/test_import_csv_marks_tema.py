import sqlite3

from src.db.db import DB_PATH
from src.etl.import_csv import import_csv


def test_import_csv_sets_tema_and_candidate(tmp_path):
    csv_path = tmp_path / "manual.csv"
    csv_path.write_text(
        "tipo,numero,ano,titulo\n" "Decreto-Lei,555,1999,RJUE\n",
        encoding="utf-8",
    )

    import_csv(csv_path)

    c = sqlite3.connect(DB_PATH)
    row = c.execute(
        "select tema, candidate_renovaveis from diplomas "
        "where tipo='Decreto-Lei' and numero='555' and ano=1999"
    ).fetchone()
    c.close()

    assert row is not None, "Diploma não foi importado"
    tema, candidate = row
    assert tema == "renovaveis"
    assert candidate == 1
