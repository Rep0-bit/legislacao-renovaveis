# legislacao-renovaveis

Coletor + pipeline para recolher diplomas do **Diário da República (Série I)**, guardar numa base de dados SQLite,
descarregar PDFs, converter para texto e exportar conteúdo em formato **LLM‑ready** (texto + metadados), com
normalizações e filtros por tipo/keywords.

## Requisitos

- Python >= 3.10
- Windows (há launcher e build para executável), mas o core pode correr noutros SO.

## Instalação (venv)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Comandos principais

### 1) Coletar (RSS -> DB)

```powershell
python -m src.core.pipeline collect --days 7
```

Opções úteis:

- `--force-full-window` : ignora cursor/checkpoint e varre a janela toda
- `--no-keywords` : desliga filtros por keywords (se existirem no perfil)
- `--dry-run` : não grava na BD (útil para testar)
- `--log-level DEBUG` : mais detalhe

### 2) Exportar para LLM (JSON + TXT)

```powershell
python -m src.export_llm --tipo portaria --out data\llm_portarias
```

Notas:
- `--tipo` é repetível (`--tipo portaria --tipo decreto-lei`)
- Por omissão o output vai para `data/llm`
- O export separa texto **raw** vs **norm** (normalizado)

Exemplos (E4 — filtro renováveis):
```powershell
# preset renováveis (por defeito: match em titulo+sumario; usa data/keywords_renovaveis.txt)
python -m src.export_llm --preset renovaveis --out data\llm_renovaveis

# incluir também o texto completo (se existir)
python -m src.export_llm --preset renovaveis --match-fields all --out data\llm_renovaveis_all

# usar keywords custom
python -m src.export_llm --keywords-file data\keywords_renovaveis.txt --match-fields titulo+sumario --out data\llm_custom
```


### 3) Consultar a BD (CLI)

```powershell
python -m src.leis list --limit 20
python -m src.leis stats-tipo
```

### 4) Smoke test (rápido)

```powershell
.\smoke.ps1
```

## Paths e dados (DB / exports / reports)

Por omissão, o projeto usa o diretório `data/` dentro do repositório:

- **Data dir**: `data/`  
  Override via env var: `LEGREN_DATA_DIR`
- **DB**: `data/leis.sqlite3`  
  Override via env var: `LEGREN_DB_PATH`
- **Reports**: `data/reports/`
- **Índices**: `data/index/`
- **Debug**: `data/debug/`

## Executável (PyInstaller)

```powershell
.\build.ps1
```

Os artefactos ficam em `dist/`.

## Launcher (Windows)

Executa `run_coletor.bat` (menu/atalhos para correr o coletor).
