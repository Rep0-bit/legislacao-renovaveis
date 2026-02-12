# tests/test_export_llm_strict.py

import sqlite3
from pathlib import Path

import pytest

from src.export_llm import export_llm


def write_text_file(data_dir: Path, conv_doc_id: str, *, chars: int = 4000) -> Path:
    p = data_dir / "text" / f"{conv_doc_id}__html.txt"
    p.write_text(("TEXTO COMPLETO\n" * 1000)[:chars], encoding="utf-8")
    return p


@pytest.mark.skipif(
    "only_converted" not in export_llm.__code__.co_varnames,
    reason="export_llm ainda não tem modo estrito (--only-converted)",
)
def test_export_llm_only_converted(tmp_path: Path, isolated_data_dir: Path, fresh_db: str) -> None:
    out_dir = tmp_path / "llm"
    out_dir.mkdir()

    conv_id = "decreto-lei_15-2022-test"

    c = sqlite3.connect(str(fresh_db))

    # Diploma convertido (deve exportar)
    c.execute(
        """
        INSERT OR REPLACE INTO diplomas
        (tipo, numero, ano, titulo, sumario, conv_ok, conv_doc_id, tema, candidate_renovaveis)
        VALUES (?, ?, ?, ?, ?, 1, ?, 'renovaveis', 1)
        """,
        ("Decreto-Lei", "15", 2022, "DL renováveis", "Sumário renováveis", conv_id),
    )

    # Diploma não convertido (não deve exportar)
    c.execute(
        """
        INSERT OR REPLACE INTO diplomas
        (tipo, numero, ano, titulo, sumario, conv_ok, tema, candidate_renovaveis)
        VALUES (?, ?, ?, ?, ?, 0, 'renovaveis', 1)
        """,
        ("Decreto-Lei", "555", 1999, "RJUE", "Regime Jurídico da Urbanização"),
    )
    c.commit()
    c.close()

    # cria o txt convertido no DATA_DIR isolado
    write_text_file(isolated_data_dir, conv_id, chars=4000)

    export_llm(
        out_dir=out_dir,
        only_converted=True,
        min_chars=2000,
        require_keywords=False,
        incremental=False,
        limit=9999,
    )

    txt_files = list((out_dir / "text").glob("*.txt"))
    assert len(txt_files) == 1, "Só devia exportar o diploma convertido"

    assert (out_dir / "incompletos.csv").exists(), "Deveria gerar incompletos.csv"
