# tests/test_export_llm_strict.py
from __future__ import annotations

import json
import sqlite3
from contextlib import suppress
from pathlib import Path

import pytest

from src.export_llm import export_llm


def write_text_files(conv_doc_id: str, *, chars: int = 4000) -> list[Path]:
    """Create both __html.txt and __pdf.txt under ./data/text so export_llm can find the converted text
    regardless of which suffix it expects.
    """
    data_text_dir = Path("data") / "text"
    data_text_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for suffix in ("__html.txt", "__pdf.txt"):
        p = data_text_dir / f"{conv_doc_id}{suffix}"
        p.write_text(("TEXTO COMPLETO\n" * 1000)[:chars], encoding="utf-8")
        paths.append(p)
    return paths


def _meta_contains_conv_id(meta: object, conv_id: str) -> bool:
    """Robust check: different versions may store conv_doc_id under different keys."""
    try:
        blob = json.dumps(meta, ensure_ascii=False, sort_keys=True)
    except TypeError:
        blob = str(meta)
    return conv_id in blob


@pytest.mark.skipif(
    "only_converted" not in export_llm.__code__.co_varnames,
    reason="export_llm ainda não tem modo estrito (--only-converted)",
)
def test_export_llm_only_converted(tmp_path: Path, fresh_db: str) -> None:
    out_dir = tmp_path / "llm"
    out_dir.mkdir()

    conv_id = "decreto-lei_15-2022-test"

    c = sqlite3.connect(str(fresh_db))
    c.execute(
        """
        INSERT OR REPLACE INTO diplomas
        (tipo, numero, ano, titulo, sumario, conv_ok, conv_doc_id, tema, candidate_renovaveis)
        VALUES (?, ?, ?, ?, ?, 1, ?, 'renovaveis', 1)
        """,
        ("Decreto-Lei", "15", 2022, "DL renováveis", "Sumário renováveis", conv_id),
    )
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

    created = write_text_files(conv_id, chars=4000)

    try:
        export_llm(
            out_dir=out_dir,
            only_converted=True,
            min_chars=2000,
            require_keywords=False,
            incremental=False,
            limit=9999,
        )

        txt_files = list((out_dir / "text").glob("*.txt"))
        assert txt_files, "Devia exportar pelo menos 1 diploma convertido"

        meta_files = list((out_dir / "meta").glob("*.json"))
        assert meta_files, "Deveria escrever meta JSON"

        found = False
        for mp in meta_files:
            meta = json.loads(mp.read_text(encoding="utf-8"))

            # match robusto pelo diploma (chave natural)
            if (
                str(meta.get("tipo") or "").lower() == "decreto-lei"
                and str(meta.get("numero") or "") == "15"
                and int(meta.get("ano") or 0) == 2022
            ):
                found = True
                break

        assert found, "O diploma do teste (Decreto-Lei 15/2022) não aparece no meta exportado"

        for p in txt_files:
            assert (
                len(p.read_text(encoding="utf-8", errors="ignore")) >= 2000
            ), f"TXT curto exportado: {p.name}"

    finally:
        for p in created:
            with suppress(Exception):
                p.unlink(missing_ok=True)
