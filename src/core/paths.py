# src/core/paths.py
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "LegislacaoRenovaveis"
ENV_BASE_DIR = "LEGRE_BASEDIR"


def _default_base_dir() -> Path:
    r"""Base dir para dados/runtime (não depende do CWD).

    Windows: %LOCALAPPDATA%\LegislacaoRenovaveis
    Linux:   ~/.local/share/legislacao-renovaveis
    macOS:   ~/Library/Application Support/LegislacaoRenovaveis
    """
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / APP_DIR_NAME

    # macOS
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME

    # Linux/Unix
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "legislacao-renovaveis"
    return Path.home() / ".local" / "share" / "legislacao-renovaveis"


def get_base_dir() -> Path:
    """Devolve base dir configurável (override via env LEGRE_BASEDIR)."""
    override = (os.environ.get(ENV_BASE_DIR) or "").strip()
    if override:
        return Path(override)
    return _default_base_dir()


BASE_DIR: Path = get_base_dir()
DATA_DIR: Path = BASE_DIR / "data"
INDEX_DIR: Path = DATA_DIR / "index"
LOG_DIR: Path = BASE_DIR / "logs"

# Mantém compatibilidade com a estrutura existente "data/index/reports"
REPORTS_DIR: Path = INDEX_DIR / "reports"

DEBUG_DIR: Path = DATA_DIR / "debug"
HTML_SHELL_DIR: Path = DEBUG_DIR / "html_shell"

DEFAULT_DB_PATH: Path = DATA_DIR / "diplomas.sqlite3"


def ensure_app_dirs() -> None:
    """Garante que as pastas base existem."""
    for d in (DATA_DIR, INDEX_DIR, LOG_DIR, REPORTS_DIR, DEBUG_DIR, HTML_SHELL_DIR):
        d.mkdir(parents=True, exist_ok=True)


def get_resource_root() -> Path:
    """Root para recursos empacotados (PyInstaller).

    - Em dev: .../src
    - Em PyInstaller: sys._MEIPASS
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    # src/core/paths.py -> src/
    return Path(__file__).resolve().parents[1]
