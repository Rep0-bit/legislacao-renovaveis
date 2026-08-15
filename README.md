# legislacao-renovaveis

[![CI](https://github.com/Rep0-bit/legislacao-renovaveis/actions/workflows/ci.yml/badge.svg)](https://github.com/Rep0-bit/legislacao-renovaveis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python >= 3.10](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](pyproject.toml)

Coletor + pipeline para recolher diplomas do **Diário da República (Série I)**, guardar numa base de dados SQLite,
descarregar PDFs, converter para texto e exportar conteúdo em formato **LLM‑ready** (texto + metadados), com
normalizações e filtros por tipo/keywords.

## Índice

- [Requisitos](#requisitos)
- [Instalação (venv)](#instalação-venv)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Comandos principais](#comandos-principais)
- [Paths e dados (DB / exports / reports)](#paths-e-dados-db--exports--reports)
- [Executável (PyInstaller)](#executável-pyinstaller)
- [Launcher (Windows)](#launcher-windows)
- [Documentação adicional](#documentação-adicional)
- [Nota sobre encoding (Windows PowerShell)](#nota-sobre-encoding-windows-powershell)
- [Licença](#licença)

## Requisitos

- Python >= 3.10
- Windows (há launcher e build para executável), mas o core pode correr noutros SO.

## Instalação (venv)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Estrutura do projeto

```
src/
├── collectors/   # recolha de diplomas (RSS, DR)
├── config/       # perfis e configuração
├── core/         # pipeline principal
├── db/           # acesso à base de dados SQLite
├── download/     # download de PDFs (rede resiliente)
├── etl/          # conversão e normalização de texto
├── gui/ gui_user/# interfaces desktop (Tkinter)
├── leis/         # CLI de consulta à BD
├── processing/   # marcação temática, filtros
├── qa/           # verificações de qualidade
├── reports/      # geração de relatórios
├── utils/        # utilitários partilhados
└── export_llm.py # exportação para formato LLM-ready
tests/            # testes de topo de repositório
tools/            # scripts auxiliares (build, fixes)
docs/             # manual do utilizador e instruções de build
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

## Documentação adicional

- [Manual do utilizador (PDF)](docs/Megajoule_Legislacao_Manual_Utilizador_v1.0.2.pdf)
- [Instruções — executável GUI](docs/INSTRUCOES_GUI_EXE.md)
- [Instruções — patch onefile GUI (utilizador)](docs/INSTRUCOES_PATCH_ONEFILE_GUI_USER.md)
- [CHANGELOG](CHANGELOG.md)

## Nota sobre encoding (Windows PowerShell)

Os ficheiros de texto gerados pela aplicação são gravados em **UTF-8 (sem BOM)**.

No **Windows PowerShell 5.1**, o comando `Get-Content` pode interpretar UTF-8
como Windows-1252, apresentando caracteres como `Ã§`, `Ã£`, `Âº`, etc.
Isto é apenas um problema de visualização — os ficheiros estão corretos.

Para visualizar corretamente, usar:

```powershell
Get-Content ficheiro.txt -Encoding utf8
```

## Licença

Distribuído sob a licença [MIT](LICENSE).
