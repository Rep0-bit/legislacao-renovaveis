# src/leis/api.py
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..collectors.coletor_dr_serie1_rss import collect as _collect_rss
from ..db.db import init_db as _init_db
from ..runner_conversao import run_convert as _run_convert
from .models import ConvertResult


def init_db(db_path: Path | None = None) -> None:
    """Inicializa a BD (idempotente)."""
    _init_db(db_path=db_path)


def collect(
    keywords: Iterable[str] | None = None,
    profile: str | None = None,
    *,
    days: int = 90,
    force_full_window: bool = False,
    debug: bool = False,
    reset_checkpoint: bool = False,
    dump_html_shell: bool = False,
) -> None:
    """
    Recolhe diplomas do DR (RSS Série I), filtra por keywords e faz upsert na BD.
    """
    kw_list = list(keywords) if keywords is not None else []
    _collect_rss(
        kw_list if keywords is not None else None,
        days=days,
        force_full_window=force_full_window,
        debug=debug,
        reset_checkpoint_flag=reset_checkpoint,
        dump_html_shell=dump_html_shell,
        profile=profile,
    )


def convert(
    *,
    db: Path | None = None,
    out_dir: Path = Path("data"),
    only_missing: bool = True,
    reprocess: bool = False,
    limit: int | None = None,
    from_latest_report: bool = False,
    from_report: Path | None = None,
    report_delimiter: str = ";",
) -> ConvertResult:
    """
    Descarrega PDFs + converte para TXT, atualizando colunas conv_* na BD.
    """
    only_missing_eff = False if reprocess else only_missing

    return _run_convert(
        db_path=db,
        out_dir=out_dir,
        only_missing=only_missing_eff,
        limit=limit,
        from_latest_report=from_latest_report,
        from_report=from_report,
        report_delimiter=report_delimiter,
    )
