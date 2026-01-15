# src/config/logging_setup.py
from __future__ import annotations

import atexit
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_LOG_FILE: Path | None = None
_REGISTERED_SUMMARY = False


def _resolve_level(level: str | int | None) -> str:
    if isinstance(level, int):
        level = logging.getLevelName(level)
    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    if resolved not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        resolved = "INFO"
    return resolved


def _make_log_path() -> Path:
    # Import lazy to avoid circular imports on module load
    from ..core.paths import LOGS_DIR, ensure_app_dirs

    ensure_app_dirs()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return LOGS_DIR / f"run_{ts}.log"


def _register_summary() -> None:
    global _REGISTERED_SUMMARY
    if _REGISTERED_SUMMARY:
        return
    _REGISTERED_SUMMARY = True

    def _summary() -> None:
        # Import lazy: only when the program exits
        try:
            from ..core.paths import DATA_DIR, LOGS_DIR, REPORTS_DIR
            from ..db.db import DB_PATH

            logging.getLogger(__name__).info("📁 Data dir: %s", DATA_DIR)
            logging.getLogger(__name__).info("🗄️ DB: %s", DB_PATH)
            logging.getLogger(__name__).info("🧾 Reports: %s", REPORTS_DIR)
            logging.getLogger(__name__).info("🧰 Logs dir: %s", LOGS_DIR)
            if _LOG_FILE:
                logging.getLogger(__name__).info("🧷 Log file: %s", _LOG_FILE)
        except Exception:
            # Never fail on shutdown
            pass

    atexit.register(_summary)


def setup_logging(level: str | int | None = None) -> Path:
    """Configura logging previsível.

    - Consola: nível pedido (default INFO)
    - Ficheiro: DEBUG (sempre)
    - Um ficheiro por execução (UTC timestamp) em DATA_DIR/logs/
    - No fim da execução imprime paths úteis (DB/reports/logs)
    """
    global _LOG_FILE

    resolved = _resolve_level(level)

    root = logging.getLogger()
    root.setLevel("DEBUG")  # root em DEBUG para não cortar logs do file handler

    # Remove handlers existentes para evitar duplicados
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1) Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(resolved)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # 2) File handler (sempre DEBUG)
    _LOG_FILE = _make_log_path()
    fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    fh.setLevel("DEBUG")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Reduz ruído típico
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    _register_summary()

    # Mostra logo onde o log está a ser escrito
    logging.getLogger(__name__).info("🧷 Log file: %s", _LOG_FILE)

    return _LOG_FILE
