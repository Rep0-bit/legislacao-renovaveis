# tests/conftest.py
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    (data_dir / "text").mkdir(parents=True, exist_ok=True)
    (data_dir / "reports").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    return data_dir


@pytest.fixture()
def fresh_db(isolated_data_dir: Path) -> Path:
    """
    Inicializa uma BD limpa no DATA_DIR temporário e devolve o caminho efetivo.
    Não tenta forçar DB_PATH; confia no init_db() do projeto.
    """
    import src.db.db as dbmod

    importlib.reload(dbmod)

    dbmod.init_db()

    # DB_PATH efetivo após init
    db_path = Path(dbmod.DB_PATH)
    assert db_path.exists(), f"DB de teste não existe: {db_path}"
    return db_path


def write_text_file(data_dir: Path, conv_doc_id: str, *, chars: int = 4000) -> Path:
    p = data_dir / "text" / f"{conv_doc_id}__html.txt"
    p.write_text(("TEXTO COMPLETO\n" * 1000)[:chars], encoding="utf-8")
    return p
