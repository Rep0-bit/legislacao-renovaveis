import sqlite3
from pathlib import Path

import pytest

from src.db.db import DB_PATH
from src.export_llm import export_llm


@pytest.mark.skipif(
    "only_converted" not in export_llm.__code__.co_varnames,
    reason="export_llm ainda não tem modo estrito (--only-converted)",
)
def test_export_llm_only_converted(tmp_path):
    out_dir = tmp_path / "llm"
    out_dir.mkdir()

    c = sqlite3.connect(DB_PATH)

    # Diploma convertido
    c.execute(
        """
        insert into diplomas
        (tipo, numero, ano, titulo, sumario, conv_ok, conv_doc_id, tema, candidate_renovaveis)
        values (?, ?, ?, ?, ?, 1, ?, 'renovaveis', 1)
        """,
        (
            "Decreto-Lei",
            "15",
            2022,
            "DL renováveis",
            "Sumário renováveis",
            "decreto-lei_15-2022-test",
        ),
    )

    # Diploma não convertido
    c.execute(
        """
        insert into diplomas
        (tipo, numero, ano, titulo, sumario, conv_ok, tema, candidate_renovaveis)
        values (?, ?, ?, ?, ?, 0, 'renovaveis', 1)
        """,
        (
            "Decreto-Lei",
            "555",
            1999,
            "RJUE",
            "Regime Jurídico da Urbanização",
        ),
    )
    c.commit()
    c.close()

    # Ficheiro convertido “a sério”
    text_dir = Path("data/text")
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "decreto-lei_15-2022-test__html.txt").write_text(
        "TEXTO COMPLETO\n" * 300,
        encoding="utf-8",
    )

    export_llm(
        out_dir=out_dir,
        preset="renovaveis",
        only_converted=True,
        min_chars=2000,
        require_keywords=False,
        incremental=False,
    )

    txt_files = list((out_dir / "text").glob("*.txt"))
    assert len(txt_files) == 1, "Só devia exportar o diploma convertido"

    incompletos = out_dir / "incompletos.csv"
    assert incompletos.exists(), "Deveria gerar incompletos.csv"
