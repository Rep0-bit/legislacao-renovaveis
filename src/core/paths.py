from __future__ import annotations

import os
import sys
from pathlib import Path


def get_repo_root(start: str | Path | None = None) -> Path:
    """Resolve o root do repositório (onde vive o pyproject.toml ou .git)."""
    p = Path(start) if start is not None else Path(__file__).resolve()
    if p.is_file():
        p = p.parent
    for parent in [p, *p.parents]:
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    return p


def get_user_data_dir(app_name: str = "LegislacaoRenovaveis") -> Path:
    """Diretório de dados por utilizador (Windows-first, com fallback)."""
    local = os.getenv("LOCALAPPDATA")
    if local:
        return Path(local) / app_name

    home = Path.home()

    # macOS
    if os.name == "posix" and (home / "Library").exists():
        return home / "Library" / "Application Support" / app_name

    # Linux/posix
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / app_name

    return home / ".local" / "share" / app_name


# ---------------------------------------------------------------------------
# Default paths (compat layer)
# ---------------------------------------------------------------------------
#
# These constants are used across the project (DB, exports, conversions). They
# also provide backward compatibility for earlier phases of the project where
# modules imported `DEFAULT_DB_PATH`.


def get_data_dir() -> Path:
    """Resolve the project data directory.

    Priority:
    1) LEGREN_DATA_DIR env var
    2) frozen exe -> per-user data dir
    3) <repo_root>/data
    """
    env = (os.getenv("LEGREN_DATA_DIR", "") or "").strip()
    if env:
        return Path(env).expanduser().resolve()

    # PyInstaller / frozen executable: use per-user app data
    if getattr(sys, "frozen", False):
        return get_user_data_dir().resolve()

    return (get_repo_root() / "data").resolve()


DEFAULT_DATA_DIR: Path = get_data_dir()


def get_default_db_path() -> Path:
    """Default SQLite DB path (overridable via env)."""
    env = (os.getenv("LEGREN_DB_PATH", "") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (DEFAULT_DATA_DIR / "leis.sqlite3").resolve()


# Kept for compatibility with existing imports
DEFAULT_DB_PATH: Path = get_default_db_path()


# Diretório para dumps e artefactos de debug (HTML/PDF shells, relatórios, etc.)
DEBUG_DIR: Path = (DEFAULT_DATA_DIR / "debug").resolve()

# Aliases usados por módulos antigos/externos
DATA_DIR: Path = DEFAULT_DATA_DIR
INDEX_DIR: Path = (DATA_DIR / "index").resolve()
REPORTS_DIR: Path = (DATA_DIR / "reports").resolve()
LOGS_DIR: Path = (DATA_DIR / "logs").resolve()


def ensure_app_dirs() -> None:
    """Garante que os diretórios standard existem."""

    for p in (DATA_DIR, DEBUG_DIR, INDEX_DIR, REPORTS_DIR, LOGS_DIR):
        p.mkdir(parents=True, exist_ok=True)
