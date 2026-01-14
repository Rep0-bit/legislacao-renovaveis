# src/config/logging_setup.py
from __future__ import annotations

import logging
import os
import sys


def setup_logging(level=None):
    if isinstance(level, int):
        # converte 20 -> "INFO", 10 -> "DEBUG", etc.
        level = logging.getLevelName(level)
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
