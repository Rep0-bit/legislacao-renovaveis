# src/processing/indexador.py
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from ..db.db import get_conn
from ..utils.normalize import (
    normalize_numero,
    normalize_numero_display,
    normalize_search_text,
    slugify_ascii_kebab,
)


def make_hash(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _row_get(row: Any, key: str, idx: int):
    """Compatível com sqlite3.Row e tuple."""
    if row is None:
        return None
    try:
        return row[key]  # sqlite3.Row
    except Exception:
        return row[idx]  # tuple


def _get_existing(tipo: str, numero: str, ano: int, db_path: Any | None = None) -> dict[str, Any] | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                id_dr,
                hash_fonte,
                resumo_1_frase,
                observacoes,
                estado
            FROM diplomas
            WHERE tipo=? AND numero=? AND ano=?
            """,
            (tipo, numero, ano),
        ).fetchone()

    if not row:
        return None

    return {
        "id_dr": _row_get(row, "id_dr", 0),
        "hash_fonte": _row_get(row, "hash_fonte", 1),
        "resumo_1_frase": (_row_get(row, "resumo_1_frase", 2) or ""),
        "observacoes": (_row_get(row, "observacoes", 3) or ""),
        "estado": (_row_get(row, "estado", 4) or "desconhecido"),
    }


def _get_key_by_id_dr(id_dr: str, db_path: Any | None = None) -> tuple[str, str, int] | None:
    """
    Se já existir um diploma com o mesmo id_dr, devolve a chave (tipo, numero, ano)
    para fazermos UPSERT por essa chave e evitarmos violar o UNIQUE(id_dr).
    """
    if not id_dr:
        return None

    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT tipo, numero, ano
            FROM diplomas
            WHERE id_dr=?
            """,
            (id_dr,),
        ).fetchone()

    if not row:
        return None

    tipo = _row_get(row, "tipo", 0)
    numero = _row_get(row, "numero", 1)
    ano = _row_get(row, "ano", 2)
    return (str(tipo), str(numero), int(ano))


def _is_manual(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> bool:
    def nz(v: Any) -> bool:
        return bool(str(v or "").strip())

    ex = existing or {}
    return (
        nz(incoming.get("resumo_1_frase"))
        or nz(incoming.get("observacoes"))
        or (incoming.get("estado") and incoming.get("estado") != "desconhecido")
        or nz(ex.get("resumo_1_frase"))
        or nz(ex.get("observacoes"))
        or (ex.get("estado") and ex.get("estado") != "desconhecido")
    )


def _compute_tipo_slug(reg: dict[str, Any], tipo_fallback: str) -> str:
    # prioridade: tipo_slug vindo do coletor (ex.: inferido do URL/título)
    ts = str(reg.get("tipo_slug") or "").strip()
    if ts:
        return slugify_ascii_kebab(ts)
    return slugify_ascii_kebab(tipo_fallback)


def upsert_diploma(
    reg: dict[str, Any], db_path: Any | None = None
) -> tuple[str, bool, str | None, str | None]:
    """
    Faz UPSERT e devolve:
      (status, is_manual, old_hash, new_hash)

    status ∈ {"novo", "atualizado", "inalterado"}
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    incoming_id_dr = (reg.get("id_dr") or "").strip()
    existing_key = _get_key_by_id_dr(incoming_id_dr, db_path=db_path)

    tipo = reg.get("tipo")
    numero = reg.get("numero")
    ano = reg.get("ano")
    if not (tipo and numero and ano):
        raise ValueError("upsert_diploma requer tipo/numero/ano")

    if existing_key is not None:
        tipo_s, numero_s, ano_i = existing_key
    else:
        tipo_s = str(tipo)
        numero_s = str(numero)
        ano_i = int(ano)

    new_hash = make_hash(reg)

    existing = _get_existing(tipo_s, numero_s, ano_i, db_path=db_path)
    old_hash = existing["hash_fonte"] if existing else None

    status = "novo" if existing is None else ("inalterado" if old_hash == new_hash else "atualizado")
    is_manual = _is_manual(existing, reg)

    tipo_slug = _compute_tipo_slug(reg, tipo_s)
    numero_norm = normalize_numero(numero_s)
    numero_display = normalize_numero_display(numero_s, ano=ano_i)
    titulo_norm = normalize_search_text(str(reg.get("titulo") or ""))
    sumario_norm = normalize_search_text(str(reg.get("sumario") or ""))

    payload: dict[str, Any] = {
        "id_dr": incoming_id_dr or (existing.get("id_dr") if existing else None),
        "tipo": tipo_s,
        "tipo_slug": tipo_slug or None,
        "numero": numero_s,
        "numero_norm": numero_norm or None,
        "numero_display": numero_display or None,
        "ano": ano_i,
        "data_publicacao": reg.get("data_publicacao"),
        "titulo": reg.get("titulo"),
        "sumario": reg.get("sumario"),
        "titulo_norm": titulo_norm or None,
        "sumario_norm": sumario_norm or None,
        "url_detalhe": reg.get("url_detalhe"),
        "url_pdf": reg.get("url_pdf"),
        "url_consolidado": reg.get("url_consolidado"),
        "resumo_1_frase": reg.get("resumo_1_frase", "") or "",
        "observacoes": reg.get("observacoes", "") or "",
        "estado": reg.get("estado", "desconhecido") or "desconhecido",
        "ultima_verificacao": now,
        "hash_fonte": new_hash,
    }

    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO diplomas (
                id_dr,
                tipo, tipo_slug, numero, numero_norm, numero_display, ano,
                data_publicacao, titulo, sumario, titulo_norm, sumario_norm,
                url_detalhe, url_pdf, url_consolidado,
                resumo_1_frase, observacoes,
                estado, ultima_verificacao, hash_fonte
            ) VALUES (
                :id_dr,
                :tipo, :tipo_slug, :numero, :numero_norm, :numero_display, :ano,
                :data_publicacao, :titulo, :sumario, :titulo_norm, :sumario_norm,
                :url_detalhe, :url_pdf, :url_consolidado,
                :resumo_1_frase, :observacoes,
                :estado, :ultima_verificacao, :hash_fonte
            )
            ON CONFLICT(tipo, numero, ano) DO UPDATE SET
                id_dr=COALESCE(NULLIF(excluded.id_dr,''), diplomas.id_dr),

                tipo_slug=COALESCE(NULLIF(excluded.tipo_slug,''), diplomas.tipo_slug),
                numero_norm=COALESCE(NULLIF(excluded.numero_norm,''), diplomas.numero_norm),
                numero_display=COALESCE(NULLIF(excluded.numero_display,''), diplomas.numero_display),

                data_publicacao=excluded.data_publicacao,
                titulo=excluded.titulo,
                sumario=excluded.sumario,
                titulo_norm=excluded.titulo_norm,
                sumario_norm=excluded.sumario_norm,

                url_detalhe=excluded.url_detalhe,
                url_pdf=excluded.url_pdf,
                url_consolidado=excluded.url_consolidado,

                resumo_1_frase=COALESCE(NULLIF(excluded.resumo_1_frase,''), diplomas.resumo_1_frase),
                observacoes=COALESCE(NULLIF(excluded.observacoes,''), diplomas.observacoes),

                estado=COALESCE(NULLIF(excluded.estado,''), diplomas.estado),

                ultima_verificacao=excluded.ultima_verificacao,
                hash_fonte=excluded.hash_fonte
            """,
            payload,
        )
        conn.commit()

    return status, is_manual, old_hash, new_hash
