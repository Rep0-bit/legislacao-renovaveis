"""
Entrypoint para executável (PyInstaller).

Uso (exe):
  coletor_dr.exe --profile renovaveis_todos --days 1 --dry-run --force-full-window
"""

from __future__ import annotations

from src.collectors.coletor_dr_serie1_rss import main

if __name__ == "__main__":
    # Passa argumentos CLI diretamente para o main()
    # (PyInstaller preserva sys.argv)
    main()
