# src/db/db.py
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..core.paths import DEFAULT_DB_PATH

DB_PATH = DEFAULT_DB_PATH


def get_conn(db_path: Any | None = None) -> sqlite3.Connection:
    """Abre ligação SQLite. Se `db_path` não for dado, usa `DB_PATH`."""
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    if _has_column(conn, table, col):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db(db_path: Any | None = None) -> None:
    """Cria/atualiza o schema mínimo da DB.

    - Tabela `diplomas`
    - Tabela `collector_state` (checkpoint/cursor do coletor)
    - Migrations leves (ADD COLUMN) para colunas novas
    """
    with get_conn(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS diplomas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,
                numero TEXT NOT NULL,
                ano INTEGER NOT NULL,
                data_publicacao TEXT,
                titulo TEXT,
                sumario TEXT,
                titulo_norm TEXT,
                sumario_norm TEXT,
                url_detalhe TEXT,
                url_pdf TEXT,
                url_consolidado TEXT,
                resumo_1_frase TEXT,
                observacoes TEXT,
                estado TEXT DEFAULT 'desconhecido',
                ultima_verificacao TEXT,
                hash_fonte TEXT,
                id_dr TEXT,
                conv_ok INTEGER,
                conv_at TEXT,
                conv_doc_id TEXT,
                conv_meta_path TEXT,
                conv_error TEXT,
                tipo_slug TEXT,
                numero_norm TEXT,
                numero_display TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collector_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )

        # Migrations leves para DBs antigas
        _ensure_column(conn, "diplomas", "tipo_slug", "tipo_slug TEXT")
        _ensure_column(conn, "diplomas", "numero_norm", "numero_norm TEXT")
        _ensure_column(conn, "diplomas", "numero_display", "numero_display TEXT")
        _ensure_column(conn, "diplomas", "titulo_norm", "titulo_norm TEXT")
        _ensure_column(conn, "diplomas", "sumario_norm", "sumario_norm TEXT")

        # Índices úteis
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_diplomas_tipo_slug_ano_numero_norm
            ON diplomas(tipo_slug, ano, numero_norm)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_diplomas_data_publicacao
            ON diplomas(data_publicacao)
            """
        )

        # Chave canónica do upsert (necessária para ON CONFLICT em upsert_diploma)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_diplomas_tipo_numero_ano
            ON diplomas(tipo, numero, ano)
            """
        )

        conn.commit()
