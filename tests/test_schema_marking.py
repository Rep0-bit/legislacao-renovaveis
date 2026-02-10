import sqlite3

from src.db.db import DB_PATH


def test_schema_has_marking_columns():
    c = sqlite3.connect(DB_PATH)
    cols = {r[1] for r in c.execute("pragma table_info(diplomas)")}
    c.close()

    required = {
        "tema",
        "candidate_renovaveis",
        "candidate_note",
    }

    missing = required - cols
    assert not missing, f"Colunas em falta na tabela diplomas: {missing}"
