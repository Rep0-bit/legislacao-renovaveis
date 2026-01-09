# src/core/links.py
from __future__ import annotations

import re

_DETAIL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/dr/detalhe/"),
    re.compile(r"/eli/"),
)


def _is_detail_link(url: str) -> bool:
    """Heurística: links de detalhe/ELI do DR."""
    if not url:
        return False
    u = url.lower()
    return any(p.search(u) for p in _DETAIL_PATTERNS)


__all__ = ["_is_detail_link"]
