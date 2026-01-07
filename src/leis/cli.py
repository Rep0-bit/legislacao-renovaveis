# src/leis/cli.py
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Módulos reais no teu projeto (confirmados pela árvore)
MODULE_COLLECT = "src.collectors.coletor_dr_serie1_rss"
MODULE_CONVERT = "src.processing.conversao"


def _configure_logging(debug: bool) -> None:
    """
    Configura logging via setup_logging() do projeto.
    O teu setup_logging espera level como string (ex: "DEBUG"/"INFO").
    """
    level_str = "DEBUG" if debug else "INFO"

    try:
        from src.config.logging_setup import setup_logging  # type: ignore
    except Exception:
        logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)
        return

    # 1) Se suportar debug=...
    try:
        setup_logging(debug=debug)  # type: ignore[arg-type]
        return
    except TypeError:
        pass

    # 2) Caso padrão do teu projeto: level como STRING
    try:
        setup_logging(level=level_str)  # type: ignore[arg-type]
        return
    except TypeError:
        pass

    # 3) Sem args
    try:
        setup_logging()  # type: ignore[misc]
        return
    except Exception:
        logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)


def _run_module(module: str, argv: Sequence[str], *, debug: bool = False) -> int:
    """
    Executa `python -m <module> ...` no mesmo venv.
    Se debug=True, propaga LOG_LEVEL=DEBUG para o processo filho.
    """
    cmd = [sys.executable, "-m", module, *argv]
    logger.debug("A executar: %s", " ".join(cmd))

    import os

    env = os.environ.copy()
    if debug:
        env["LOG_LEVEL"] = "DEBUG"

    return subprocess.call(cmd, env=env)


def cmd_init_db(_args: argparse.Namespace) -> int:
    from src.db.db import init_db  # type: ignore

    init_db()
    print("✅ BD inicializada.")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    argv: list[str] = ["--days", str(args.days)]

    if args.reset_checkpoint:
        argv.append("--reset-checkpoint")

    if args.force_full_window:
        argv.append("--force-full-window")

    # Propagar debug do CLI para o coletor
    if args.debug:
        argv += ["--debug", "--log-level", "DEBUG"]

    return _run_module(MODULE_COLLECT, argv, debug=args.debug)


def cmd_convert(args: argparse.Namespace) -> int:
    argv: list[str] = []
    if args.limit is not None:
        argv += ["--limit", str(args.limit)]

    if args.debug:
        argv += ["--log-level", "DEBUG"]  # usa só se o conversor suportar

    return _run_module(MODULE_CONVERT, argv, debug=args.debug)


def cmd_run(args: argparse.Namespace) -> int:
    rc = cmd_init_db(args)
    if rc != 0:
        return rc

    rc = cmd_collect(args)
    if rc != 0:
        return rc

    return cmd_convert(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leis",
        description="Ferramentas para recolha/conversão de legislação (DR).",
    )

    # ✅ Opção A: DEBUG GLOBAL (vale para todos os subcomandos)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Ativa logging em modo DEBUG (global).",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init-db", help="Inicializa a base de dados (idempotente).")
    p_init.set_defaults(func=cmd_init_db)

    p_collect = sub.add_parser(
        "collect",
        help="Recolhe diplomas do DR (RSS Série I) e grava na BD.",
    )
    p_collect.add_argument("--days", type=int, default=3, help="Janela temporal em dias.")
    p_collect.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Remove o checkpoint e força reprocessamento.",
    )
    p_collect.add_argument(
        "--force-full-window",
        action="store_true",
        help="Ignora checkpoint e processa a janela inteira.",
    )
    p_collect.set_defaults(func=cmd_collect)

    p_convert = sub.add_parser(
        "convert",
        help="Descarrega PDFs e converte para TXT, atualizando conv_ok na BD.",
    )
    p_convert.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limita nº de documentos a converter (se suportado).",
    )
    p_convert.set_defaults(func=cmd_convert)

    p_run = sub.add_parser(
        "run",
        help="Pipeline: init-db + collect + convert (modo '1 clique').",
    )
    p_run.add_argument("--days", type=int, default=1, help="Janela temporal em dias.")
    p_run.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Remove o checkpoint (aplica ao collect).",
    )
    p_run.add_argument(
        "--force-full-window",
        action="store_true",
        help="Ignora checkpoint (aplica ao collect).",
    )
    p_run.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limita nº de documentos a converter (se suportado).",
    )
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # ✅ logging configurado ANTES de executar subcomandos
    _configure_logging(debug=args.debug)
    logger.debug("Args: %s", args)

    return int(args.func(args))
