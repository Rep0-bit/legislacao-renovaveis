# tests/test_import_csv_marks_tema.py
import sqlite3

from src.etl.import_csv import import_csv


def test_import_csv_sets_tema_and_candidate(tmp_path, fresh_db):
    csv_path = tmp_path / "manual.csv"
    csv_path.write_text(
        "tipo,numero,ano,titulo\nDecreto-Lei,555,1999,RJUE\n",
        encoding="utf-8",
    )

    # O teu import CLI assume autodetetor; aqui força delimiter para o teste
    import_csv(csv_path, delimiter=",")

    c = sqlite3.connect(str(fresh_db))
    row = c.execute(
        "select tema, candidate_renovaveis from diplomas "
        "where tipo='Decreto-Lei' and numero='555' and ano=1999"
    ).fetchone()
    c.close()

    assert row is not None, "Diploma não foi importado"
    tema, candidate = row
    assert tema == "renovaveis"
    assert candidate == 1
