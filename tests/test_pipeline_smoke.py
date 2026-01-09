# tests/test_pipeline_smoke.py
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.pipeline import CollectResult, collect


@pytest.fixture()
def fake_rss_items() -> list[dict]:
    # O pipeline espera, no mínimo, estas chaves vindas do parse_rss_items().
    return [
        {
            "link": "https://diariodarepublica.pt/dr/detalhe/portaria/1-000000000",
            "title": "Portaria n.º 1/2026/1  -  Diário da República n.º 1/2026, Série I de 2026-01-02",
            "description": "Sumário de teste",
            "pubDate_utc": datetime(2026, 1, 9, 0, 0, tzinfo=timezone.utc),
            # Forçamos o caminho A (RSS já traz PDF)
            "pdf_url": "https://files.diariodarepublica.pt/1s/2026/01/01/0000.pdf",
        }
    ]


def test_collect_returns_collectresult_offline(
    monkeypatch: pytest.MonkeyPatch, fake_rss_items: list[dict]
) -> None:
    # 1) Evitar rede: o collect() tenta obter o RSS via http_get()
    monkeypatch.setattr("src.core.pipeline.http_get", lambda *a, **k: b"<rss/>")

    # 2) Evitar parser RSS real: devolvemos itens já normalizados
    monkeypatch.setattr("src.core.pipeline.parse_rss_items", lambda *_a, **_k: fake_rss_items)

    # 3) Evitar qualquer fetch de PDF: o pipeline chama try_keyword_match_via_pdf em fallback
    monkeypatch.setattr("src.core.pipeline.try_keyword_match_via_pdf", lambda *_a, **_k: (None, ""))

    # 4) Evitar tocar na DB (mesmo que alguma branch chame): status "inalterado"
    monkeypatch.setattr(
        "src.core.pipeline.upsert_diploma",
        lambda *_a, **_k: {"status": "inalterado", "manual": False, "old_hash": "", "new_hash": ""},
    )

    # 5) Evitar escrita real de report: devolve path fictício
    monkeypatch.setattr("src.core.pipeline.write_report", lambda *_a, **_k: None)

    result = collect(
        None,
        days=1,
        dry_run=True,
        force_full_window=True,
        keywords_enabled=False,  # garante que não tentamos validar keywords/PDF
    )

    assert isinstance(result, CollectResult)
    assert result.success is True
    assert result.error is None

    # Métricas estáveis
    assert result.rss_items_total == 1
    assert result.rss_items_window == 1
    assert result.pdf_direct_used == 1


def test_collectresult_has_stable_fields() -> None:
    # Se alguém refatorar no futuro, este teste ajuda a manter o "contrato" do resultado.
    r = CollectResult(success=True)
    assert hasattr(r, "success")
    assert hasattr(r, "error")
    assert hasattr(r, "rss_items_total")
    assert hasattr(r, "novos")
    assert hasattr(r, "report_path")
