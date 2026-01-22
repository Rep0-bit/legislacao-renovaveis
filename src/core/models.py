from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineConfig:
    days: int
    keywords: list[str] | None
    force_full_window: bool
    reset_checkpoint: bool
    out_dir: Path
    debug: bool = False


@dataclass(frozen=True)
class ParseResult:
    ok: bool
    tipo: str | None = None
    numero: str | None = None
    ano: int | None = None
    confidence: float = 0.0
    raw_match: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    processed: int
    novos: int
    atualizados: int
    inalterados: int
    manuais: int
    report_path: Path | None


@dataclass(frozen=True)
class ConvertResult:
    """Métricas de conversão (PDF -> texto/meta)."""

    processed: int
    ok: int
    error: int
    skipped_no_pdf: int = 0
