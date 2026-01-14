# src/core/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

from .paths import get_repo_root, get_user_data_dir


def get_data_dir() -> Path:
    """Diretório base de dados do projeto.

    Mantido por compatibilidade com scripts que importam `get_data_dir` a partir
    de `src.core.config`.
    """

    # Preferimos o diretório do projeto por defeito (repo_root/data), mas
    # permitimos override por env (ver `src.core.paths.get_data_dir`).
    from .paths import DEFAULT_DATA_DIR

    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_DATA_DIR


@dataclass(frozen=True)
class AppConfig:
    """Config de execução do coletor (pensado para uso por API / tarefas agendadas)."""

    profile: str | None = None
    days: int = 90
    keywords_enabled: bool = True
    keywords: list[str] | None = None
    include_types: list[str] | None = None
    exclude_types: list[str] | None = None
    strict_types: bool = False
    pdf_fallback_pages: int = 2
    dump_html_shell: bool = False
    log_level: str | None = None


def _csv_list(v: str | None) -> list[str] | None:
    if not v:
        return None
    parts = [p.strip().lower() for p in v.split(",")]
    out = [p for p in parts if p]
    return out or None


def _env_bool(name: str, default: bool) -> bool:
    v = (os.getenv(name, "") or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    v = (os.getenv(name, "") or "").strip()
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _env_list(name: str) -> list[str] | None:
    v = (os.getenv(name, "") or "").strip()
    if not v:
        return None
    # aceita CSV ou lista separada por ';'
    v = v.replace(";", ",")
    parts = [p.strip() for p in v.split(",")]
    out = [p for p in parts if p]
    return out or None


def find_config_file(explicit_path: str | Path | None = None) -> Path | None:
    """Procura config.toml (ordem):
    1) caminho explícito
    2) LEGREN_CONFIG
    3) repo_root/config.toml
    4) repo_root/config/config.toml
    5) user_data/config.toml
    """
    if explicit_path:
        p = Path(explicit_path).expanduser().resolve()
        return p if p.exists() else None

    env = (os.getenv("LEGREN_CONFIG", "") or "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        return p if p.exists() else None

    repo = get_repo_root()
    for cand in [repo / "config.toml", repo / "config" / "config.toml"]:
        if cand.exists():
            return cand

    user = get_user_data_dir()
    cand = user / "config.toml"
    if cand.exists():
        return cand

    return None


def load_config(explicit_path: str | Path | None = None) -> AppConfig:
    """Carrega config de (a) TOML e (b) variáveis de ambiente (que ganham ao TOML).

    Variáveis suportadas (prefixo LEGREN_):
    - PROFILE
    - DAYS
    - NO_KEYWORDS (boolean)
    - KEYWORDS (CSV)
    - TYPES (CSV)
    - EXCLUDE_TYPES (CSV)
    - STRICT_TYPES (boolean)
    - PDF_FALLBACK_PAGES (int)
    - DUMP_HTML_SHELL (boolean)
    - LOG_LEVEL (string)
    """
    file_cfg: dict = {}
    p = find_config_file(explicit_path)
    if p and tomllib is not None:
        try:
            file_cfg = tomllib.loads(p.read_text(encoding="utf-8"))
        except Exception:
            file_cfg = {}

    c = (file_cfg.get("coletor") or {}) if isinstance(file_cfg, dict) else {}

    profile = c.get("profile") or None
    days = int(c.get("days") or 90)
    keywords_enabled = not bool(c.get("no_keywords") is True)
    keywords = c.get("keywords") if isinstance(c.get("keywords"), list) else None
    include_types = c.get("types") if isinstance(c.get("types"), list) else None
    exclude_types = c.get("exclude_types") if isinstance(c.get("exclude_types"), list) else None
    strict_types = bool(c.get("strict_types") is True)
    pdf_fallback_pages = int(c.get("pdf_fallback_pages") or 2)
    dump_html_shell = bool(c.get("dump_html_shell") is True)
    log_level = c.get("log_level") if isinstance(c.get("log_level"), str) else None

    # ENV overrides
    env_profile = (os.getenv("LEGREN_PROFILE", "") or "").strip() or None
    if env_profile:
        profile = env_profile

    days = _env_int("LEGREN_DAYS", days)
    pdf_fallback_pages = _env_int("LEGREN_PDF_FALLBACK_PAGES", pdf_fallback_pages)
    dump_html_shell = _env_bool("LEGREN_DUMP_HTML_SHELL", dump_html_shell)
    strict_types = _env_bool("LEGREN_STRICT_TYPES", strict_types)

    no_kw = _env_bool("LEGREN_NO_KEYWORDS", not keywords_enabled)
    keywords_enabled = not no_kw

    kw_env = _env_list("LEGREN_KEYWORDS")
    if kw_env is not None:
        keywords = kw_env

    types_env = _csv_list(os.getenv("LEGREN_TYPES"))
    if types_env is not None:
        include_types = types_env

    ex_types_env = _csv_list(os.getenv("LEGREN_EXCLUDE_TYPES"))
    if ex_types_env is not None:
        exclude_types = ex_types_env

    ll = (os.getenv("LEGREN_LOG_LEVEL", "") or "").strip()
    if ll:
        log_level = ll

    return AppConfig(
        profile=profile,
        days=days,
        keywords_enabled=keywords_enabled,
        keywords=keywords,
        include_types=include_types,
        exclude_types=exclude_types,
        strict_types=strict_types,
        pdf_fallback_pages=pdf_fallback_pages,
        dump_html_shell=dump_html_shell,
        log_level=log_level,
    )
