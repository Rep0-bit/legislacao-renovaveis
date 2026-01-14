from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.pipeline import CollectResult, collect


@pytest.fixture()
def fake_rss_items() -> list[dict]:
    """Item RSS fake com pubDate sempre 'agora' para o teste ser estável."""
    now_utc = datetime.now(UTC).replace(microsecond=0)
    return [
        {
            "title": "Portaria n.º 1/2026/1  -  Diário da República n.º 1/2026, Série I de 2026-01-01",
            "link": "https://diariodarepublica.pt/dr/detalhe/portaria/1-000000000",
            "pubDate_raw": now_utc.isoformat().replace("+00:00", "Z"),
            "pubDate_utc": now_utc,
            "pdf_url": "https://dre.tretas.example/1s/2026/01/01/0000.pdf",
            "description": "Sumário de teste",
        }
    ]


def test_collect_returns_collectresult_offline(
    monkeypatch: pytest.MonkeyPatch, fake_rss_items: list[dict]
) -> None:
    monkeypatch.setattr("src.core.pipeline.http_get", lambda *a, **k: b"<rss/>")
    monkeypatch.setattr("src.core.pipeline.parse_rss_items", lambda *_a, **_k: fake_rss_items)
    monkeypatch.setattr("src.core.pipeline.try_keyword_match_via_pdf", lambda *_a, **_k: (None, ""))
    monkeypatch.setattr(
        "src.core.pipeline.upsert_diploma",
        lambda *_a, **_k: {"status": "inalterado", "manual": False, "old_hash": "", "new_hash": ""},
    )
    monkeypatch.setattr("src.core.pipeline.write_report", lambda *_a, **_k: None)

    result = collect(
        None,
        days=1,
        dry_run=True,
        force_full_window=True,
        keywords_enabled=False,
    )

    assert isinstance(result, CollectResult)
    assert result.success is True
    assert result.error is None
    assert result.rss_items_total == 1
    assert result.rss_items_window == 1
