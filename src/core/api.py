from __future__ import annotations

from .models import PipelineConfig, PipelineResult


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    # aqui vais chamar o teu coletor atual (refactor gradual)
    raise NotImplementedError
