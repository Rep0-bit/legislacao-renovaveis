# src/collectors/coletor_dr_serie1_rss.py
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ..config.logging_setup import setup_logging
from ..core.pipeline import collect as pipeline_collect

logger = logging.getLogger(__name__)


def load_profiles(*, extra_path: Path | None = None) -> dict[str, dict]:
    """Load profiles.json.

    Search order:
      1) extra_path (if provided)
      2) ./config/profiles.json
      3) ./profiles.json
      4) ./data/profiles.json
      5) project root inferred from __file__ (two levels up) + same paths

    Returns {} if not found/invalid.
    """
    candidates: list[Path] = []
    if extra_path is not None:
        candidates.append(extra_path)

    # CWD candidates (includes user's original layout)
    candidates.append(Path("config") / "profiles.json")
    candidates.append(Path("profiles.json"))
    candidates.append(Path("data") / "profiles.json")

    # project root from this file (src/collectors/... -> parents[2] is repo root)
    try:
        root = Path(__file__).resolve().parents[2]
        candidates.append(root / "config" / "profiles.json")
        candidates.append(root / "profiles.json")
        candidates.append(root / "data" / "profiles.json")
    except Exception:
        pass

    for p in candidates:
        try:
            if p and p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def apply_profile_to_args(
    args: argparse.Namespace,
    *,
    extra_path: Path | None = None,
) -> None:
    """Aplica defaults de um --profile, sem sobrescrever flags explícitas.

    Precedência:
      flags explícitas (presentes em sys.argv) > profile > defaults do argparse
    """
    profile = (getattr(args, "profile", "") or "").strip()
    if not profile:
        return

    profiles = load_profiles(extra_path=extra_path)
    cfg = profiles.get(profile)
    if not cfg:
        raise SystemExit(f"Profile desconhecido: {profile}. Disponíveis: {', '.join(sorted(profiles))}")

    def has(flag: str) -> bool:
        return flag in sys.argv

    if (not has("--days")) and ("days" in cfg):
        args.days = int(cfg["days"])

    if (not has("--types")) and ("types" in cfg):
        v = cfg.get("types", "")
        if isinstance(v, list):
            args.types = ",".join(str(x).strip() for x in v if str(x).strip())
        else:
            args.types = str(v or "")

    if (not has("--exclude-types")) and ("exclude_types" in cfg):
        v = cfg.get("exclude_types", "")
        if isinstance(v, list):
            args.exclude_types = ",".join(str(x).strip() for x in v if str(x).strip())
        else:
            args.exclude_types = str(v or "")

    if (not has("--strict-types")) and ("strict_types" in cfg):
        args.strict_types = bool(cfg.get("strict_types"))

    if (not has("--pdf-fallback-pages")) and ("pdf_fallback_pages" in cfg):
        args.pdf_fallback_pages = int(cfg["pdf_fallback_pages"])

    # keywords vs no-keywords: se user passou flags explícitas, não mexemos
    if has("--no-keywords") or has("--keywords"):
        return

    if cfg.get("no_keywords") is True:
        args.no_keywords = True
    elif cfg.get("no_keywords") is False:
        args.no_keywords = False

    if ("keywords" in cfg) and (cfg.get("keywords") is not None):
        args.keywords = list(cfg.get("keywords") or [])

    logger.info(
        "📌 Profile aplicado: %s (days=%s, types=%s, exclude_types=%s, strict_types=%s, no_keywords=%s, pdf_fallback_pages=%s)",
        profile,
        getattr(args, "days", None),
        getattr(args, "types", "") or "—",
        getattr(args, "exclude_types", "") or "—",
        getattr(args, "strict_types", False),
        getattr(args, "no_keywords", False),
        getattr(args, "pdf_fallback_pages", None),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--profile",
        default="",
        help="Preset de parâmetros (ex.: renovaveis_portarias). Flags explícitas ganham ao profile.",
    )
    ap.add_argument("--days", type=int, default=90, help="Janela temporal em dias (ex.: 90)")
    ap.add_argument(
        "--force-full-window", action="store_true", help="Ignora checkpoint e reprocessa toda a janela"
    )
    ap.add_argument(
        "--reset-checkpoint", action="store_true", help="Apaga o checkpoint incremental e reprocessa"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Processa e gera relatório, mas não grava na DB nem atualiza checkpoint/cursor",
    )
    ap.add_argument(
        "--dump-html-shell", action="store_true", help="Guarda HTML quando o detalhe vem como shell"
    )
    ap.add_argument("--debug", action="store_true", help="Modo debug (mais logs)")
    ap.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    ap.add_argument(
        "--pdf-fallback-pages", type=int, default=8, help="N.º máximo de páginas para extrair texto do PDF"
    )

    ap.add_argument(
        "--no-keywords",
        action="store_true",
        help="Ignora qualquer filtro por keywords (incluindo keywords default). Aceita todos os itens válidos.",
    )
    ap.add_argument(
        "--keywords",
        nargs="*",
        default=[],
        help="Lista de palavras-chave",
    )
    ap.add_argument(
        "--types",
        default="",
        help="Filtra por tipo(s) de diploma (slug do DR). Ex: --types portaria,decreto-lei. Vazio = aceita todos.",
    )
    ap.add_argument(
        "--exclude-types",
        default="",
        help="Exclui tipo(s) de diploma (slug do DR). Ex: --exclude-types despacho,declaração. Vazio = não exclui.",
    )
    ap.add_argument(
        "--strict-types",
        action="store_true",
        help="Quando --types está definido, também rejeita itens com tipo desconhecido.",
    )

    ap.add_argument(
        "--list-profiles",
        action="store_true",
        help="Lista os perfis disponíveis (lidos de profiles.json ou fallback) e termina.",
    )
    ap.add_argument(
        "--profiles-path",
        default="",
        help="Caminho para profiles.json alternativo (override).",
    )

    args = ap.parse_args()

    # 1) Configurar logging antes de aplicar profile (para o log "📌 Profile aplicado" aparecer)
    setup_logging(args.log_level or ("DEBUG" if args.debug else None))

    profiles_path = Path(args.profiles_path) if (args.profiles_path or "").strip() else None

    if args.list_profiles:
        profiles = load_profiles(extra_path=profiles_path)
        print("Perfis disponíveis:")
        for name in sorted(name for name in profiles if str(name).strip()):
            print(f" - {name}")
        if not profiles:
            tried = []
            if profiles_path is not None:
                tried.append(str(profiles_path))
            tried += [
                "config/profiles.json",
                "profiles.json",
                "data/profiles.json",
                "<root>/config/profiles.json",
                "<root>/profiles.json",
                "<root>/data/profiles.json",
            ]
            print("⚠️ Nenhum profile encontrado. Tentados:", "; ".join(tried))
        return

    # 2) Aplicar defaults do profile (flags explícitas continuam a ganhar)
    apply_profile_to_args(args, extra_path=profiles_path)

    keywords_enabled = not args.no_keywords
    if keywords_enabled:
        logger.info("🔎 Filtro por keywords ATIVO")
    else:
        logger.info("🚫 Filtro por keywords DESATIVADO (--no-keywords ativo)")

    def _parse_types_csv(s: str) -> list[str]:
        parts = [p.strip().lower() for p in (s or "").split(",")]
        return [p for p in parts if p]

    include_types = _parse_types_csv(getattr(args, "types", ""))
    exclude_types = _parse_types_csv(getattr(args, "exclude_types", ""))

    if include_types:
        logger.info("🏷️  Filtro por tipo ATIVO (include): %s", ", ".join(include_types))
    if exclude_types:
        logger.info("🏷️  Filtro por tipo ATIVO (exclude): %s", ", ".join(exclude_types))
    if getattr(args, "strict_types", False) and include_types:
        logger.info("🏷️  strict-types=ON (tipo desconhecido será rejeitado)")

    # reduzir ruído de libs externas
    logging.getLogger("charset_normalizer").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    pipeline_collect(
        args.keywords,
        days=args.days,
        force_full_window=args.force_full_window,
        debug=args.debug,
        reset_checkpoint_flag=args.reset_checkpoint,
        dry_run=args.dry_run,
        dump_html_shell=args.dump_html_shell,
        pdf_fallback_pages=args.pdf_fallback_pages,
        keywords_enabled=keywords_enabled,
        include_types=include_types,
        exclude_types=exclude_types,
        strict_types=getattr(args, "strict_types", False),
        profile=None,
    )


if __name__ == "__main__":
    main()
