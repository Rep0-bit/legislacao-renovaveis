# GUI + EXE (PyInstaller) — Instruções de integração

Este pacote adiciona uma interface gráfica (Tkinter) e ajusta os paths para funcionar corretamente em modo **.exe** (PyInstaller),
guardando dados em `%LOCALAPPDATA%\LegislacaoRenovaveis` quando a app está “frozen”.

## 1) Ficheiros incluídos / o que substituir

Substituir:
- `src/core/paths.py`  *(mudança: `get_data_dir()` passa a usar `get_user_data_dir()` quando `sys.frozen == True`)*

Adicionar:
- `src/gui/__init__.py`
- `src/gui/__main__.py`
- `src/gui/app.py`
- `assets/megajoule.ico`  *(ícone do executável, gerado a partir do logo fornecido)*
- `legislacao-renovaveis-gui.spec`
- `build_gui.ps1`

## 2) Como aplicar as alterações

1. Copiar os ficheiros para o repositório, respeitando os caminhos acima.
2. Confirmar que a app abre em modo desenvolvimento:

```powershell
. .\.venv\Scripts\Activate.ps1
python -m src.gui
```

3. Testar o pipeline normal (recomendado):

```powershell
pytest
powershell -ExecutionPolicy Bypass -File .\smoke.ps1
```

## 3) Gerar o EXE (GUI)

```powershell
. .\.venv\Scripts\Activate.ps1
.\build_gui.ps1
```

Saída típica:
- `dist\legislacao-renovaveis-gui\legislacao-renovaveis-gui.exe`

Ao executar o EXE, os dados (DB, logs, reports) devem ir para:
- `%LOCALAPPDATA%\LegislacaoRenovaveis\...`

## 4) Quando fazer commit

Faz commit **depois** de:
- `python -m src.gui` abrir e os botões **Coletar** e **Exportar** funcionarem (mesmo que não haja dados suficientes ainda),
- `pytest` passar,
- `build_gui.ps1` gerar o executável com ícone.

Sugestão de mensagem de commit (1 único commit):
- `GUI: adiciona app Tkinter + build PyInstaller com icon + paths frozen`

## 5) Notas

- A GUI chama **funções internas** (não usa subprocess), o que é mais robusto no `.exe`.
- O preset “renovaveis” prepara o **state** de export; filtros adicionais (keywords/where) podem ser adicionados numa iteração seguinte.
