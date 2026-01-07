# src/leis/models.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.models import ConvertResult  # re-export


@dataclass(frozen=True)
class CollectResult:
    report_path: Path | None
    items_read: int
    items_processed: int
    links_found: int
    fail_fetch: int
    rejected_kw: int
    rejected_parse: int
    new_count: int
    updated_count: int
    unchanged_count: int
    manual_count: int


@dataclass(frozen=True)
class RunResult:
    collect: CollectResult
    convert: ConvertResult
