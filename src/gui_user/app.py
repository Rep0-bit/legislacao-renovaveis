from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from src.core.paths import get_data_dir
from src.core.pipeline import collect as pipeline_collect
from src.export_llm import export_llm as export_llm_fn

DEFAULT_DAYS = 7
DEFAULT_INCREMENTAL = True


class TextHandler(logging.Handler):
    def __init__(self, text: tk.Text) -> None:
        super().__init__()
        self.text = text

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record) + "\n"
        self.text.after(0, self._append, msg)

    def _append(self, msg: str) -> None:
        self.text.insert("end", msg)
        self.text.see("end")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Legislação Renováveis — Megajoule")
        self.geometry("920x560")

        self.status = tk.StringVar(value="Pronto.")

        header = ttk.Frame(self, padding=12)
        header.pack(fill="x")

        ttk.Label(
            header,
            text="Legislação Renováveis",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left")

        ttk.Label(header, textvariable=self.status).pack(side="right")

        actions = ttk.Frame(self, padding=(12, 0, 12, 12))
        actions.pack(fill="x")

        self.btn_collect = ttk.Button(actions, text="1) Atualizar base (coletar)", command=self.on_collect)
        self.btn_collect.pack(side="left", padx=6)

        self.btn_export = ttk.Button(actions, text="2) Gerar pacote para LLM", command=self.on_export)
        self.btn_export.pack(side="left", padx=6)

        ttk.Button(actions, text="Abrir resultados", command=self.on_open_results).pack(side="left", padx=6)
        ttk.Button(actions, text="Limpar log", command=self.on_clear).pack(side="right", padx=6)

        self.text = tk.Text(self, wrap="word")
        self.text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._setup_logging()
        logging.getLogger(__name__).info("Data dir: %s", get_data_dir())

    def _setup_logging(self) -> None:
        root = logging.getLogger()
        root.setLevel(logging.INFO)

        handler = TextHandler(self.text)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        root.handlers.clear()
        root.addHandler(handler)

    def _set_busy(self, busy: bool, status: str) -> None:
        self.status.set(status)
        state = "disabled" if busy else "normal"
        self.btn_collect.config(state=state)
        self.btn_export.config(state=state)

    def on_collect(self) -> None:
        def worker() -> None:
            self._set_busy(True, "A atualizar base…")
            try:
                res = pipeline_collect(days=DEFAULT_DAYS, force_full_window=False, debug=False)

                novos = getattr(res, "novos", None)
                atualizados = getattr(res, "atualizados", None)
                inalterados = getattr(res, "inalterados", None)
                processados = getattr(res, "processados", None)
                lidos = getattr(res, "lidos", None)

                logging.getLogger(__name__).info(
                    "✅ Coleta concluída: novos=%s atualizados=%s inalterados=%s processados=%s lidos=%s",
                    novos,
                    atualizados,
                    inalterados,
                    processados,
                    lidos,
                )
                self._set_busy(False, "Pronto. Base atualizada.")
            except Exception as e:
                logging.getLogger(__name__).exception("❌ Falha na coleta: %s", e)
                self._set_busy(False, "Erro na coleta.")

        threading.Thread(target=worker, daemon=True).start()

    def on_export(self) -> None:
        def worker() -> None:
            self._set_busy(True, "A gerar pacote para LLM…")
            try:
                out_dir = get_data_dir() / "llm_renovaveis"
                summary = export_llm_fn(
                    out_dir=out_dir,
                    limit=500,
                    tipos=None,
                    keywords=None,
                    match_fields="titulo+sumario",
                    match_mode="any",
                    write_text=True,
                    incremental=DEFAULT_INCREMENTAL,
                )
                logging.getLogger(__name__).info(
                    "✅ Export concluído: exported=%s skipped=%s out=%s",
                    summary.exported,
                    summary.skipped,
                    summary.out_dir,
                )
                self._set_busy(False, "Pronto. Pacote gerado.")
            except Exception as e:
                logging.getLogger(__name__).exception("❌ Falha no export: %s", e)
                self._set_busy(False, "Erro no export.")

        threading.Thread(target=worker, daemon=True).start()

    def on_open_results(self) -> None:
        p = get_data_dir()
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(p))
        except Exception:
            messagebox.showinfo("Resultados", str(p))

    def on_clear(self) -> None:
        self.text.delete("1.0", "end")


def run() -> None:
    App().mainloop()
