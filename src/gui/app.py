from __future__ import annotations

import logging
import os
import sqlite3
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ..core.paths import get_data_dir
from ..core.pipeline import CollectResult, collect as pipeline_collect
from ..db.db import DB_PATH
from ..export_llm import ExportSummary, _make_state_key, _state_get, _state_set, export_llm

logger = logging.getLogger(__name__)


class _TextHandler(logging.Handler):
    def __init__(self, text: tk.Text) -> None:
        super().__init__()
        self._text = text

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record) + "\n"
        self._text.after(0, self._append, msg)

    def _append(self, msg: str) -> None:
        self._text.insert("end", msg)
        self._text.see("end")


def _setup_logging(text: tk.Text, *, debug: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    handler = _TextHandler(text)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root.handlers.clear()
    root.addHandler(handler)


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows
    except Exception:
        messagebox.showinfo("Pasta", str(path))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Legislação Renováveis")
        self.geometry("980x640")

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Profile:").grid(row=0, column=0, sticky="w")
        self.profile = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.profile, width=22).grid(row=0, column=1, padx=6)

        ttk.Label(top, text="Days:").grid(row=0, column=2, sticky="w")
        self.days = tk.IntVar(value=7)
        ttk.Entry(top, textvariable=self.days, width=8).grid(row=0, column=3, padx=6)

        self.force_full = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="force full window", variable=self.force_full).grid(
            row=0, column=4, padx=10
        )

        self.debug = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="DEBUG", variable=self.debug, command=self._on_toggle_debug).grid(
            row=0, column=5, padx=10
        )

        ttk.Separator(self).pack(fill="x", padx=10, pady=(0, 10))

        btns = ttk.Frame(self, padding=(10, 0, 10, 10))
        btns.pack(fill="x")

        ttk.Button(btns, text="Coletar (RSS → DB)", command=self.on_collect).pack(side="left", padx=6)
        ttk.Button(btns, text="Exportar LLM (preset)", command=self.on_export).pack(side="left", padx=6)
        ttk.Button(btns, text="Abrir pasta de dados", command=self.on_open_data).pack(side="left", padx=6)
        ttk.Button(btns, text="Limpar log", command=self.on_clear).pack(side="right", padx=6)

        export_bar = ttk.Frame(self, padding=(10, 0, 10, 10))
        export_bar.pack(fill="x")

        ttk.Label(export_bar, text="Preset:").pack(side="left")
        self.preset = tk.StringVar(value="renovaveis")
        ttk.Combobox(
            export_bar,
            textvariable=self.preset,
            values=["all", "decretos", "portarias", "renovaveis"],
            width=16,
            state="readonly",
        ).pack(side="left", padx=6)

        self.continue_state = tk.BooleanVar(value=True)
        ttk.Checkbutton(export_bar, text="continuar (state)", variable=self.continue_state).pack(
            side="left", padx=10
        )

        ttk.Label(export_bar, text="Limit:").pack(side="left")
        self.limit = tk.IntVar(value=200)
        ttk.Entry(export_bar, textvariable=self.limit, width=8).pack(side="left", padx=6)

        self.text = tk.Text(self, wrap="word")
        self.text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        _setup_logging(self.text, debug=self.debug.get())
        logger.info("Pronto. Data dir: %s", get_data_dir())

    def _on_toggle_debug(self) -> None:
        _setup_logging(self.text, debug=self.debug.get())
        logger.info("Logging atualizado: %s", "DEBUG" if self.debug.get() else "INFO")

    def on_collect(self) -> None:
        def worker() -> None:
            try:
                _setup_logging(self.text, debug=self.debug.get())
                profile = (self.profile.get() or "").strip() or None
                res: CollectResult = pipeline_collect(
                    profile=profile,
                    days=int(self.days.get()),
                    force_full_window=bool(self.force_full.get()),
                    debug=bool(self.debug.get()),
                )
                if res.success:
                    logger.info(
                        "✅ Collect OK | processed=%s novos=%s atualizados=%s inalterados=%s report=%s",
                        res.processed,
                        res.novos,
                        res.atualizados,
                        res.inalterados,
                        res.report_path,
                    )
                else:
                    logger.error("❌ Collect falhou: %s", res.error)
            except Exception:
                logger.exception("❌ Exceção durante collect")

        threading.Thread(target=worker, daemon=True).start()

    def on_export(self) -> None:
        def worker() -> None:
            try:
                _setup_logging(self.text, debug=self.debug.get())

                preset = (self.preset.get() or "").strip().casefold()
                tipos: list[str] | None = None
                state = "default"

                if preset == "decretos":
                    tipos = ["decreto-lei"]
                    state = "decretos"
                elif preset == "portarias":
                    tipos = ["portaria"]
                    state = "portarias"
                elif preset == "renovaveis":
                    tipos = None  # mantém todos; filtros por keywords/where podem ser acrescentados depois
                    state = "renovaveis"
                else:
                    tipos = None
                    state = "default"

                out_dir = (get_data_dir() / "llm").resolve()
                out_dir.mkdir(parents=True, exist_ok=True)

                since_id: int | None = None
                state_key = _make_state_key(state, tipos or [])
                if self.continue_state.get():
                    conn = sqlite3.connect(DB_PATH)
                    try:
                        since_id = _state_get(conn, state_key)
                    finally:
                        conn.close()

                logger.info("Export preset=%s tipos=%s since_id=%s out=%s", preset, tipos, since_id, out_dir)

                summary: ExportSummary = export_llm(
                    out_dir=out_dir,
                    limit=int(self.limit.get()),
                    tipos=tipos,
                    write_text=True,
                    incremental=True,
                    since_id=since_id,
                )
                logger.info(
                    "✅ Export OK | exported=%s skipped=%s last_id=%s out=%s",
                    summary.exported,
                    summary.skipped,
                    summary.last_id,
                    summary.out_dir,
                )

                if self.continue_state.get() and summary.last_id is not None:
                    conn = sqlite3.connect(DB_PATH)
                    try:
                        _state_set(conn, state_key, int(summary.last_id))
                        conn.commit()
                    finally:
                        conn.close()
                    logger.info("State atualizado: %s = %s", state_key, summary.last_id)

            except Exception:
                logger.exception("❌ Exceção durante export")

        threading.Thread(target=worker, daemon=True).start()

    def on_open_data(self) -> None:
        _open_folder(get_data_dir())

    def on_clear(self) -> None:
        self.text.delete("1.0", "end")


def run() -> None:
    App().mainloop()
