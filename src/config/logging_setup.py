# src/config/logging_setup.py
from __future__ import annotations

import logging
import os
import sys


def setup_logging(level: str | None = None) -> None:
    """
    Logging consistente para CLI/app.

    - Nível por defeito: INFO
    - Pode ser definido por argumento ou env var LOG_LEVEL
    - Logs para stdout (bom para PowerShell e redirecionamentos)
    """
    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").upper()

    if resolved not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        resolved = "INFO"

    root = logging.getLogger()
    root.setLevel(resolved)

    # Evita handlers duplicados
    if root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(resolved)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Reduz ruído típico
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
