# tools/migrar_numero_formato_oficial.py
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def get_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def row_to_dict(cols: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    return {cols[i]: row[i] for i in range(len(cols))}


def nz(v: Any) -> bool:
    return bool(str(v or "").strip())


def normalize_slashes(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\s+", " ", s)
    return s


def is_numero_oficial(numero: str, ano: int) -> bool:
    """
    Considera "oficial" se contiver /ANO como segmento (ex.: 80/2023, 1/2026/1, 12-A/2024/1).
    """
    n = normalize_slashes(numero).upper()
    a = str(int(ano))
    return f"/{a}" in n  # simples e eficaz para os formatos DR


def parse_numero_oficial_from_title(title: str) -> tuple[str, int] | None:
    """
    Extrai "numero/oficial" e ano do título se existir:
      - "n.º 80/2023"
      - "n.º 1/2026/1"
      - "n.º 1/2026/M"
      - "n.º 12-A/2024/1"
    Devolve (numero_oficial, ano).
    """
    t = (title or "").strip()
    if not t:
        return None

    # normaliza variantes de n.º
    t = re.sub(r"\bn[.\s]*[ºo]\b", "n.º", t, flags=re.IGNORECASE)
    t = normalize_slashes(t)

    m = re.search(
        r"n\.º\s+(?P<num>\d+(?:-[A-Z])?)\s*/\s*(?P<ano>\d{4})(?:\s*/\s*(?P<extra>[0-9A-Z]+(?:-[0-9A-Z]+)?))?",
        t,
        flags=re.IGNORECASE,
    )
    if not m:
        return None

    num = m.group("num").upper()
    ano = int(m.group("ano"))
    extra = m.group("extra")
    if extra:
        return f"{num}/{ano}/{extra.upper()}", ano
    return f"{num}/{ano}", ano


def parse_numero_oficial_from_url(url_detalhe: str, ano: int) -> str | None:
    """
    Best-effort: tenta inferir o numero oficial a partir do URL.
    Exemplos de URLs (observados):
      - .../dr/detalhe/decreto-lei/80-2023-<id>
      - .../dr/detalhe/decreto-presidente-republica/1-2026-<id>
      - .../dr/detalhe/acordao-tribunal-constitucional/1134-2025-<id>
      - .../dr/detalhe/portaria/1-<id>   (aqui pode não trazer ano/extra)
    Regra:
      - se o slug tiver "NUM-ANO" (ex.: 80-2023, 1134-2025) => NUM/ANO
      - se tiver "NUM-ANO-..." => NUM/ANO
      - se não tiver ano no slug, não inventa
    """
    u = (url_detalhe or "").strip()
    if not u:
        return None

    # tenta apanhar ".../<slug>/<num>-<ano>-<id>" ou ".../<slug>/<num>-<ano>"
    m = re.search(
        r"/dr/detalhe/[^/]+/(?P<num>\d+(?:-[A-Z])?)-(?P<ano>\d{4})(?:-\d+)?$", u, flags=re.IGNORECASE
    )
    if not m:
        return None

    num = m.group("num").upper()
    ano_url = int(m.group("ano"))
    # se o ano divergir do ano da linha, não mexer (conservador)
    if ano_url != int(ano):
        return None
    return f"{num}/{ano_url}"


def main():
    ap = argparse.ArgumentParser(
        description="Migra diplomas.numero para o formato OFICIAL (mantém ano embutido), sem o remover."
    )
    ap.add_argument("--db", default="data/index/leis_renovaveis.db", help="Caminho para a BD SQLite")
    ap.add_argument("--backup", action="store_true", help="Criar backup .bak antes de aplicar alterações")
    ap.add_argument("--dry-run", action="store_true", help="Não aplica alterações, só imprime o plano")
    ap.add_argument("--limit", type=int, default=None, help="Limitar nº de alterações (debug)")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(f"BD não encontrada: {db_path}")

    if args.backup and not args.dry_run:
        bak = db_path.with_suffix(db_path.suffix + ".bak")
        shutil.copy2(db_path, bak)
        print(f"🧷 Backup criado: {bak}")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON;")
    cols = get_cols(conn, "diplomas")

    needed = {"id", "tipo", "numero", "ano"}
    if not needed.issubset(set(cols)):
        raise RuntimeError(f"Tabela diplomas não tem colunas mínimas esperadas: {sorted(needed)}")

    # Ler colunas úteis
    want = ["id", "tipo", "numero", "ano", "id_dr", "titulo", "url_detalhe"]
    select_cols = [c for c in want if c in cols]
    q = f"SELECT {', '.join(select_cols)} FROM diplomas ORDER BY ano DESC, tipo ASC, numero ASC;"
    rows = conn.execute(q).fetchall()

    updates = 0
    skips = 0
    collisions = 0

    print(f"🗄️ BD: {db_path}")
    print(f"📌 Registos lidos: {len(rows)}")
    print("🔧 A procurar numeros NÃO-oficiais (sem /ANO) para promover para formato oficial...")

    for row in rows:
        d = row_to_dict(select_cols, row)
        rid = int(d["id"])
        tipo = str(d["tipo"])
        numero = str(d["numero"] or "")
        ano = int(d["ano"])
        titulo = str(d.get("titulo") or "")
        url_detalhe = str(d.get("url_detalhe") or "")
        id_dr = str(d.get("id_dr") or "")

        if not numero.strip():
            skips += 1
            continue

        # Já é oficial? então não mexe
        if is_numero_oficial(numero, ano):
            skips += 1
            continue

        # Tentativa 1: inferir pelo título (mais fiável)
        infer = parse_numero_oficial_from_title(titulo)
        numero_new = None
        if infer is not None:
            numero_new, ano_from_title = infer
            if ano_from_title != ano:
                numero_new = None

        # Tentativa 2: inferir pelo URL
        if numero_new is None:
            numero_new = parse_numero_oficial_from_url(url_detalhe, ano)

        # Se não foi possível inferir, não inventar
        if not numero_new:
            skips += 1
            continue

        numero_new = normalize_slashes(numero_new)

        # Se for igual após normalização, não mexe
        if normalize_slashes(numero) == numero_new:
            skips += 1
            continue

        # Ver se já existe a chave alvo (tipo, numero_new, ano)
        target = conn.execute(
            "SELECT id FROM diplomas WHERE tipo=? AND ano=? AND numero=? LIMIT 1;",
            (tipo, ano, numero_new),
        ).fetchone()

        if target is not None:
            # Não fazemos merge automático aqui (pode ser sensível).
            # Deixa o indexador resolver via id_dr quando aparecer, ou trata manualmente.
            collisions += 1
            print(
                f"⚠️ COLISÃO: id={rid} ({tipo} numero='{numero}' ano={ano}) -> numero='{numero_new}' "
                f"já existe (id={target[0]}). (id_dr={id_dr or '-'})"
            )
            continue

        updates += 1
        print(
            f"✏️ UPDATE id={rid} | {tipo} numero='{numero}' ano={ano} -> numero='{numero_new}' "
            f"(id_dr={id_dr or '-'})"
        )

        if not args.dry_run:
            conn.execute("UPDATE diplomas SET numero=? WHERE id=?;", (numero_new, rid))

        if args.limit is not None and updates >= int(args.limit):
            print(f"⏹️ Limit atingido: {args.limit}")
            break

    if args.dry_run:
        print("\n🧪 DRY-RUN: nenhuma alteração foi aplicada.")
        print(f"Resumo: updates={updates} | colisoes={collisions} | ignorados={skips}")
        conn.close()
        return

    conn.commit()
    conn.close()

    print("\n✅ Migração concluída e gravada na BD.")
    print(f"Resumo: updates={updates} | colisoes={collisions} | ignorados={skips}")
    if collisions:
        print(
            "📌 Há colisões: nesses casos não atualizei para evitar merge indevido. Podemos tratar caso-a-caso."
        )


if __name__ == "__main__":
    main()
