from __future__ import annotations

import csv
import json
import logging
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.core.paths import get_data_dir
from src.core.pipeline import collect as pipeline_collect
from src.etl.import_csv import import_csv as etl_import_csv
from src.export_llm import export_llm as export_llm_fn

DEFAULT_INCREMENTAL = True
WINDOW_OPTIONS = [7, 30, 90]
DEFAULT_WINDOW = 30


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


def write_llm_manifest(out_dir: Path) -> Path:
    """
    Cria/atualiza um CSV de monitorização do que foi efetivamente exportado para o LLM,
    lendo os JSON em <out_dir>/meta/*.json.

    Output:
      <out_dir>/index_llm.csv  (delimiter=';')
    """
    meta_dir = out_dir / "meta"
    metas = sorted(meta_dir.glob("*.json")) if meta_dir.exists() else []

    manifest_path = out_dir / "index_llm.csv"
    fieldnames = [
        "id",
        "tipo",
        "numero",
        "ano",
        "data_publicacao",
        "titulo",
        "sumario",
        "url_detalhe",
        "url_pdf",
        "exported_at",
        "text_len",
        "text_sha256",
        "matched_keywords",
        "matched_in",
    ]

    rows: list[dict[str, object]] = []
    for mp in metas:
        meta = json.loads(mp.read_text(encoding="utf-8"))
        rows.append(
            {
                "id": meta.get("id"),
                "tipo": meta.get("tipo"),
                "numero": meta.get("numero"),
                "ano": meta.get("ano"),
                "data_publicacao": meta.get("data_publicacao"),
                "titulo": meta.get("titulo"),
                "sumario": meta.get("sumario"),
                "url_detalhe": meta.get("url_detalhe"),
                "url_pdf": meta.get("url_pdf"),
                "exported_at": meta.get("exported_at"),
                "text_len": meta.get("text_len"),
                "text_sha256": meta.get("text_sha256"),
                "matched_keywords": ",".join(meta.get("matched_keywords") or []),
                "matched_in": ",".join(meta.get("matched_in") or []),
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        w.writeheader()
        w.writerows(rows)

    return manifest_path


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title("Legislação Renováveis — Megajoule")
        self.geometry("980x600")

        self.status = tk.StringVar(value="Pronto.")
        self.window_days = tk.IntVar(value=DEFAULT_WINDOW)
        self.only_converted = tk.BooleanVar(value=True)
        self.min_chars = tk.StringVar(value="2000")

        # ── Header ─────────────────────────────────────────────
        header = ttk.Frame(self, padding=12)
        header.pack(fill="x")

        ttk.Label(
            header,
            text="Legislação Renováveis",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left")

        ttk.Label(header, textvariable=self.status).pack(side="right")

        # ── Controlos ─────────────────────────────────────────
        controls = ttk.Frame(self, padding=(12, 0, 12, 8))
        controls.pack(fill="x")

        ttk.Label(controls, text="Janela temporal:").pack(side="left")
        self.cmb_days = ttk.Combobox(
            controls,
            values=WINDOW_OPTIONS,
            width=5,
            textvariable=self.window_days,
            state="readonly",
        )
        self.cmb_days.pack(side="left", padx=6)
        ttk.Label(controls, text="dias").pack(side="left")

        # ── Botões de ação ─────────────────────────────────────
        actions = ttk.Frame(self, padding=(12, 0, 12, 12))
        actions.pack(fill="x")

        self.btn_collect = ttk.Button(actions, text="Atualizar base", command=self.on_collect)
        self.btn_collect.pack(side="left", padx=6)

        self.btn_reprocess = ttk.Button(actions, text="Reprocessar janela", command=self.on_reprocess)
        self.btn_reprocess.pack(side="left", padx=6)

        self.btn_export = ttk.Button(actions, text="Gerar pacote para LLM", command=self.on_export)
        self.btn_export.pack(side="left", padx=6)

        self.btn_import_csv = ttk.Button(actions, text="Importar CSV", command=self.on_import_csv)
        self.btn_import_csv.pack(side="left", padx=6)

        ttk.Button(actions, text="Abrir resultados", command=self.on_open_results).pack(side="left", padx=6)

        ttk.Checkbutton(actions, text="Só exportar convertidos", variable=self.only_converted).pack(
            side="left", padx=(12, 6)
        )
        ttk.Label(actions, text="mín chars:").pack(side="left", padx=(6, 2))
        ttk.Entry(actions, textvariable=self.min_chars, width=6).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Limpar log", command=self.on_clear).pack(side="right", padx=6)

        # ── Área de log ────────────────────────────────────────
        self.text = tk.Text(self, wrap="word")
        self.text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._setup_logging()
        logging.getLogger(__name__).info("Data dir: %s", get_data_dir())

    # ── Helpers ───────────────────────────────────────────────
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
        for btn in (self.btn_collect, self.btn_reprocess, self.btn_export, self.btn_import_csv):
            btn.config(state=state)

    def _log_collect_result(self, res, days: int) -> None:
        novos = getattr(res, "novos", 0)
        atualizados = getattr(res, "atualizados", 0)
        inalterados = getattr(res, "inalterados", 0)
        processados = getattr(res, "processados", 0)
        lidos = getattr(res, "lidos", None)

        if novos == 0 and atualizados == 0:
            logging.getLogger(__name__).info("ℹ️ Não foram encontradas novidades nos últimos %s dias.", days)

        logging.getLogger(__name__).info(
            "Resultado: novos=%s atualizados=%s inalterados=%s processados=%s lidos=%s",
            novos,
            atualizados,
            inalterados,
            processados,
            lidos,
        )

    # ── Ações ────────────────────────────────────────────────
    def on_collect(self) -> None:
        days = int(self.window_days.get())

        def worker() -> None:
            self._set_busy(True, "A atualizar base…")
            try:
                res = pipeline_collect(days=days, force_full_window=False, debug=False)
                self._log_collect_result(res, days)
                self._set_busy(False, "Base atualizada.")
            except Exception as e:
                logging.getLogger(__name__).exception("Falha na coleta: %s", e)
                self._set_busy(False, "Erro na atualização.")

        threading.Thread(target=worker, daemon=True).start()

    def on_reprocess(self) -> None:
        days = int(self.window_days.get())

        if not messagebox.askyesno(
            "Reprocessar",
            f"Isto irá reprocessar toda a janela dos últimos {days} dias.\nContinuar?",
        ):
            return

        def worker() -> None:
            self._set_busy(True, "A reprocessar janela completa…")
            try:
                res = pipeline_collect(days=days, force_full_window=True, debug=False)
                self._log_collect_result(res, days)
                self._set_busy(False, "Reprocessamento concluído.")
            except Exception as e:
                logging.getLogger(__name__).exception("Falha no reprocessamento: %s", e)
                self._set_busy(False, "Erro no reprocessamento.")

        threading.Thread(target=worker, daemon=True).start()

    def on_import_csv(self) -> None:
        """
        Importação segura:
        - compara com BD (upsert por tipo/numero/ano)
        - não duplica
        - marca novo/atualizado/inalterado
        - gera relatório automático em data/reports/import_csv_*.csv (lógica do ETL)
        """
        csv_path = filedialog.askopenfilename(
            title="Selecionar CSV para importação",
            filetypes=[("CSV", "*.csv"), ("Todos os ficheiros", "*.*")],
        )
        if not csv_path:
            return

        p = Path(csv_path)

        if not messagebox.askyesno(
            "Importar CSV",
            "O CSV será importado para a base de dados.\n"
            "Registos existentes serão atualizados (não duplica).\n\n"
            f"Ficheiro:\n{p}\n\nContinuar?",
        ):
            return

        def worker() -> None:
            self._set_busy(True, "A importar CSV…")
            try:
                # Delimiter ';' é o default do ETL e é o mais compatível com Excel PT.
                stats = etl_import_csv(p, delimiter=";", dry_run=False, verbose=True)
                logging.getLogger(__name__).info(
                    "Import CSV concluído: novo=%s atualizado=%s inalterado=%s errors=%s",
                    stats.novo,
                    stats.atualizado,
                    stats.inalterado,
                    stats.errors,
                )
                self._set_busy(False, "Importação concluída.")
            except Exception as e:
                logging.getLogger(__name__).exception("Falha na importação CSV: %s", e)
                self._set_busy(False, "Erro na importação CSV.")

        threading.Thread(target=worker, daemon=True).start()

    def on_export(self) -> None:
        def worker() -> None:
            self._set_busy(True, "A gerar pacote para LLM…")
            try:
                out_dir = get_data_dir() / "llm_renovaveis"
                preset = "renovaveis"
                summary = export_llm_fn(
                    out_dir=out_dir,
                    limit=500,
                    tipos=None,
                    keywords=None,
                    match_fields="titulo+sumario",
                    match_mode="any",
                    write_text=True,
                    incremental=DEFAULT_INCREMENTAL,
                    only_converted=bool(self.only_converted.get()),
                    min_chars=int(self.min_chars.get() or 2000),
                    require_keywords=(preset == "renovaveis"),
                )
                logging.getLogger(__name__).info(
                    "Export concluído: exported=%s skipped=%s out=%s",
                    summary.exported,
                    summary.skipped,
                    summary.out_dir,
                )

                # Atualiza automaticamente o CSV de monitorização do LLM
                manifest_path = write_llm_manifest(out_dir)
                logging.getLogger(__name__).info("CSV de monitorização atualizado: %s", manifest_path)

                self._set_busy(False, "Pacote gerado.")
            except Exception as e:
                logging.getLogger(__name__).exception("Falha no export: %s", e)
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
