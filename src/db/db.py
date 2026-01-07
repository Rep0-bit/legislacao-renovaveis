# src/db.py
from __future__ import annotations

import os
import sqlite3
from contextlib import suppress
from pathlib import Path

# Permite override por variável de ambiente (útil para testes/produção)
DEFAULT_DB_PATH = Path("data/index/leis_renovaveis.db")
DB_PATH = Path(os.getenv("LEIS_DB_PATH", str(DEFAULT_DB_PATH)))


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """
    Abre ligação SQLite com defaults seguros.
    """
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # PRAGMAs úteis (não são obrigatórios, mas melhoram robustez/concorrência)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")

    return conn


def init_db(db_path: Path | None = None) -> None:
    """
    Cria tabelas e aplica migrações leves (idempotente).
    """
    path = db_path or DB_PATH

    with get_conn(path) as conn:
        # Tabela principal (já inclui id_dr de raiz)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS diplomas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- chave única principal (fallback)
                tipo TEXT NOT NULL,
                numero TEXT NOT NULL,
                ano INTEGER NOT NULL,

                -- id do Diário da República (quando disponível)
                id_dr TEXT,

                data_publicacao TEXT,
                titulo TEXT,
                sumario TEXT,

                url_detalhe TEXT,
                url_pdf TEXT,
                url_consolidado TEXT,

                resumo_1_frase TEXT,
                observacoes TEXT,

                estado TEXT DEFAULT 'desconhecido',  -- em_vigor / revogado / desconhecido
                ultima_verificacao TEXT,
                hash_fonte TEXT,

                UNIQUE(tipo, numero, ano)
            );
            """
        )

        # --- MIGRAÇÕES LEVES (compatibilidade com BD antigas) ---
        # Se a tabela foi criada sem id_dr numa versão anterior
        with suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE diplomas ADD COLUMN id_dr TEXT;")

        # Se a tabela foi criada sem tipo_slug numa versão anterior
        with suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE diplomas ADD COLUMN tipo_slug TEXT;")

        # índice único para id_dr (quando existir)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_diplomas_id_dr
            ON diplomas(id_dr)
            WHERE id_dr IS NOT NULL AND id_dr <> '';
            """
        )

        # tabela para guardar checkpoints/estado de coletores
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collector_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        conn.commit()


if __name__ == "__main__":
    init_db()
    print("✅ BD inicializada em:", DB_PATH)
